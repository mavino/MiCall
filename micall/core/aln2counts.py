#! /usr/bin/env python3.4

"""
Shipyard-style MiSeq pipeline, post-processing 1
Takes aligned CSV as input (produced by sam2aln).
Re-aligns sequence variants to lab standard references (e.g., HXB2).
Reports nucleotide and amino acid frequencies by reference coordinates.
Outputs consensus sequences in same coordinate system.
This assumes a consistent reading frame across the entire region.
"""

import argparse
import re
from collections import Counter, defaultdict, OrderedDict
import csv
from itertools import groupby
import logging
from operator import itemgetter
import os

import gotoh

from micall.core import miseq_logging
from micall.core.project_config import ProjectConfig, G2P_SEED_NAME
from micall.utils.big_counter import BigCounter
from micall.utils.translation import translate, ambig_dict

AMINO_ALPHABET = 'ACDEFGHIKLMNPQRSTVWY*'
CONSEQ_MIXTURE_CUTOFFS = [0.01, 0.02, 0.05, 0.1, 0.2, 0.25]
GAP_OPEN_COORD = 40
GAP_EXTEND_COORD = 10
CONSENSUS_MIN_COVERAGE = 100


def parse_args():
    parser = argparse.ArgumentParser(
        description='Post-processing of short-read alignments.')

    parser.add_argument('aligned_csv',
                        type=argparse.FileType('r'),
                        help='input CSV with aligned reads')
    parser.add_argument('g2p_aligned_csv',
                        type=argparse.FileType('r'),
                        help='input CSV with aligned reads from G2P step')
    parser.add_argument('clipping_csv',
                        type=argparse.FileType('r'),
                        help='input CSV with count of soft-clipped reads at each position')
    parser.add_argument('remap_conseq_csv',
                        type=argparse.FileType('r'),
                        help='input CSV with consensus sequences from remap step')
    parser.add_argument('conseq_ins_csv',
                        type=argparse.FileType('r'),
                        help='input CSV with insertions relative to consensus sequence')
    parser.add_argument('nuc_csv',
                        type=argparse.FileType('w'),
                        help='CSV containing nucleotide frequencies')
    parser.add_argument('amino_csv',
                        type=argparse.FileType('w'),
                        help='CSV containing amino frequencies')
    parser.add_argument('coord_ins_csv',
                        type=argparse.FileType('w'),
                        help='CSV containing insertions relative to coordinate reference')
    parser.add_argument('conseq_csv',
                        type=argparse.FileType('w'),
                        help='CSV containing consensus sequences')
    parser.add_argument('failed_align_csv',
                        type=argparse.FileType('w'),
                        help='CSV containing any consensus that failed to align')

    return parser.parse_args()


logger = miseq_logging.init_logging_console_only(logging.DEBUG)

MAX_CUTOFF = 'MAX'


class SequenceReport(object):
    """ Hold the data for several reports related to a sample's genetic sequence.

    To use a report object, read a group of aligned reads that mapped to a
    single region, and then write out all the reports for that region.
    """
    def __init__(self,
                 insert_writer,
                 projects,
                 conseq_mixture_cutoffs,
                 clipping_counts=None,
                 conseq_insertion_counts=None):
        """ Create an object instance.

        @param insert_writer: InsertionWriter object that will track reads and
            write out any insertions relative to the coordinate reference.
        @param projects: ProjectConfig object
        @param conseq_mixture_cutoffs: a list of cutoff fractions used to
            determine what portion a variant must exceed before it will be
            included as a mixture in the consensus.
        """
        self.consensus_min_coverage = 0
        self.callback_progress = 0
        self.callback_next = self.callback_chunk_size = self.callback_max = None
        self.insert_writer = insert_writer
        self.projects = projects
        self.conseq_mixture_cutoffs = list(conseq_mixture_cutoffs)
        self.conseq_mixture_cutoffs.insert(0, MAX_CUTOFF)
        self.callback = None
        self.v3_overlap_aminos = None
        self.v3_overlap_region_name = None
        self.seed_aminos = self.reports = self.reading_frames = None
        self.inserts = self.consensus = None
        self.coordinate_refs = self.remap_conseqs = None

        # {seed_name: {pos: count}}
        self.clipping_counts = clipping_counts or defaultdict(Counter)

        # {seed_name: {pos: count}
        self.conseq_insertion_counts = (conseq_insertion_counts or
                                        defaultdict(Counter))
        self.nuc_writer = self.amino_writer = self.conseq_writer = None
        self.fail_writer = None

    def enable_callback(self, callback, file_size):
        """ Enable callbacks to update progress while counting reads.

        @param callback: a function to report progress with three optional
            parameters - callback(message, progress, max_progress)
        @param file_size: the size of the aligned reads file
        """

        self.callback = callback
        self.callback_max = file_size
        self.callback_chunk_size = file_size // 100
        self.callback_next = self.callback_chunk_size
        self.callback(message='... extracting statistics from alignments',
                      progress=0,
                      max_progress=self.callback_max)

    def _count_reads(self, aligned_reads):
        """
        Parses contents of aligned CSV.

        :param aligned_reads: a sequence of Dicts from csv.DictReader
        """

        for i, row in enumerate(aligned_reads):
            if i == 0:
                # these will be the same for all rows, so just assign from the first
                self.seed = row['refname']
                self.qcut = row['qcut']
            nuc_seq = row['seq']
            offset = int(row['offset'])
            count = int(row['count'])
            if self.callback:
                row_size = sum(map(len, row.values()))
                self.callback_progress += row_size
                if self.callback_progress >= self.callback_next:
                    self.callback(progress=self.callback_progress)
                    self.callback_next += self.callback_chunk_size

            # first run, prepare containers
            if not self.seed_aminos:
                self.insert_writer.start_group(self.seed,
                                               self.qcut)
                for reading_frame in range(3):
                    self.seed_aminos[reading_frame] = []

            # record this read to calculate insertions later
            self.insert_writer.add_nuc_read('-'*offset + nuc_seq, count)

            # cycle through reading frames
            for reading_frame, frame_seed_aminos in self.seed_aminos.items():
                offset_nuc_seq = ' ' * (reading_frame + offset) + nuc_seq
                # pad to a codon boundary
                offset_nuc_seq += ' ' * ((3 - (len(offset_nuc_seq) % 3)) % 3)
                start = offset - (offset % 3)
                for nuc_pos in range(start, len(offset_nuc_seq), 3):
                    codon_index = nuc_pos // 3
                    # append SeedAmino objects to this list if necessary
                    while len(frame_seed_aminos) <= codon_index:
                        frame_seed_aminos.append(SeedAmino(
                            len(frame_seed_aminos)*3-reading_frame))

                    # update amino acid counts
                    codon = offset_nuc_seq[nuc_pos:nuc_pos + 3]
                    seed_amino = frame_seed_aminos[codon_index]
                    seed_amino.count_aminos(codon, count)
        if self.callback:
            self.callback(progress=self.callback_max)

    def _pair_align(self, reference, query, gap_open=15, gap_extend=5, use_terminal_gap_penalty=1):
        """ Align a query sequence of amino acids to a reference sequence.

        @return: (aligned_ref, aligned_query, score)
        """
        # noinspection PyUnresolvedReferences
        aligned_ref, aligned_query, score = gotoh.align_it_aa(
            reference,
            query,
            gap_open,
            gap_extend,
            use_terminal_gap_penalty)
        return aligned_ref, aligned_query, score

    def _map_to_coordinate_ref(self, coordinate_name, coordinate_ref):
        """
        Align consensus of aligned reads in each reading frame to the
        coordinate reference to determine best frame.
        Generate a map of consensus indices to the coordinate reference.

        :param coordinate_name: Name of coordinate reference.
        :param coordinate_ref: Amino acid sequence of coordinate reference.
        :return: Dict object containing coordinate map.
        """

        # Start max_score with the minimum score we can consider a valid
        # alignment. Anything worse, we won't bother
        consensus_length = len([amino for amino in self.seed_aminos[0] if amino.counts])
        max_score = min(consensus_length, len(coordinate_ref))

        best_alignment = None
        for reading_frame, frame_seed_aminos in self.seed_aminos.items():
            consensus = ''.join([seed_amino1.get_consensus()
                                for seed_amino1 in frame_seed_aminos])
            if reading_frame == 0:
                # best guess before aligning - if alignments fail, this will be non-empty
                self.consensus[coordinate_name] = consensus

            # map to reference coordinates by aligning consensus
            a_coord, a_consensus, score = self._pair_align(
                coordinate_ref,
                consensus,
                gap_open=GAP_OPEN_COORD,
                gap_extend=GAP_EXTEND_COORD)
            if score < max_score:
                continue
            max_score = score
            best_alignment = (reading_frame, consensus, a_coord, a_consensus)

        report_aminos = []
        if best_alignment is not None:
            reading_frame, consensus, a_coord, a_consensus = best_alignment

            coord2conseq = self.map_sequences(coordinate_ref,
                                              consensus,
                                              a_coord,
                                              a_consensus)
            frame_seed_aminos = self.seed_aminos[reading_frame]
            self.reading_frames[coordinate_name] = reading_frame
            self.consensus[coordinate_name] = consensus
            coordinate_inserts = {i*3 - reading_frame for i in range(len(consensus))}
            prev_conseq_index = None
            prev_consensus_nuc_index = None
            for coord_index in range(len(coordinate_ref)):
                conseq_index = coord2conseq.get(coord_index)
                if conseq_index is None:
                    seed_amino = SeedAmino(None)
                    if (prev_conseq_index is not None and
                            prev_conseq_index+1 < len(frame_seed_aminos)):
                        prev_seed_amino = frame_seed_aminos[prev_conseq_index]
                        prev_count = sum(prev_seed_amino.counts.values())
                        prev_count += prev_seed_amino.deletions
                        next_seed_amino = frame_seed_aminos[prev_conseq_index+1]
                        next_count = sum(next_seed_amino.counts.values())
                        next_count += next_seed_amino.deletions
                        min_count = min(prev_count, next_count)
                        seed_amino.deletions = min_count
                        for nuc in seed_amino.nucleotides:
                            nuc.count_nucleotides('-', min_count)
                else:
                    seed_amino = frame_seed_aminos[conseq_index]
                    if prev_conseq_index is None:
                        coordinate_inserts = {i
                                              for i in coordinate_inserts
                                              if i >= seed_amino.consensus_nuc_index}
                    prev_conseq_index = conseq_index
                if (coordinate_name == self.v3_overlap_region_name and
                        self.v3_overlap_aminos):
                    seed_amino.count_overlap(
                        self.v3_overlap_aminos[coord_index].seed_amino)

                report_aminos.append(ReportAmino(seed_amino, coord_index + 1))
                if seed_amino.consensus_nuc_index is not None:
                    coordinate_inserts.remove(seed_amino.consensus_nuc_index)
                    prev_consensus_nuc_index = seed_amino.consensus_nuc_index
            if prev_consensus_nuc_index is None:
                coordinate_inserts.clear()
            else:
                coordinate_inserts = {i
                                      for i in coordinate_inserts
                                      if i <= prev_consensus_nuc_index}
            self.inserts[coordinate_name] = coordinate_inserts

        self.reports[coordinate_name] = report_aminos

    def map_sequences(self, from_seq, to_seq, from_aligned=None, to_aligned=None):
        if from_aligned is None or to_aligned is None:
            from_aligned, to_aligned, _score = self._pair_align(
                from_seq,
                to_seq,
                gap_open=GAP_OPEN_COORD,
                gap_extend=GAP_EXTEND_COORD)
        seq_map = {}
        from_index = to_index = 0
        for from_aa, to_aa in zip(from_aligned, to_aligned):
            if (to_index < len(to_seq) and
                    to_aa == to_seq[to_index]):
                seq_map[from_index] = to_index
                to_index += 1
            if (from_index < len(from_seq) and
                    from_aa == from_seq[from_index]):
                from_index += 1
        return seq_map

    def process_reads(self,
                      g2p_aligned_csv,
                      bowtie2_aligned_csv,
                      coverage_summary=None,
                      g2p_region_name='V3LOOP'):
        # parse CSV file containing aligned reads, grouped by reference and quality cutoff
        for aligned_csv in (bowtie2_aligned_csv, g2p_aligned_csv):
            aligned_reader = csv.DictReader(aligned_csv)
            if aligned_csv == bowtie2_aligned_csv:
                v3_overlap_region_name = g2p_region_name
            else:
                v3_overlap_region_name = None
                if self.remap_conseqs is not None:
                    self.remap_conseqs[G2P_SEED_NAME] = (
                        self.projects.getReference(G2P_SEED_NAME))
            for _, aligned_reads in groupby(aligned_reader,
                                            lambda row: (row['refname'], row['qcut'])):
                self.read(aligned_reads, v3_overlap_region_name)

                if self.insert_writer is not None:
                    self.write_insertions(self.insert_writer)
                if self.nuc_writer is not None:
                    self.write_nuc_counts(self.nuc_writer)
                if self.amino_writer is not None:
                    self.write_amino_counts(self.amino_writer,
                                            coverage_summary=coverage_summary)
                if self.conseq_writer is not None:
                    self.write_consensus(self.conseq_writer)
                if self.fail_writer is not None:
                    self.write_failure(self.fail_writer)

    def read(self, aligned_reads, v3_overlap_region_name=None):
        """
        Reset all the counters, and read a new section of aligned reads.
        A section must have the same region, and qcut on all lines.

        @param aligned_reads: an iterator of dicts generated by csv.DictReader
            Each dict corresponds to a row from an aligned.CSV file and
            corresponds to a single aligned read.
        @param v3_overlap_region_name: coordinate region name to record as G2P
            overlap instead of the usual reporting.
        """
        aligned_reads = self.align_deletions(aligned_reads)

        self.seed_aminos = {}  # {reading_frame: [SeedAmino(consensus_nuc_index)]}
        self.reports = {}  # {coord_name: [ReportAmino()]}
        self.reading_frames = {}  # {coord_name: reading_frame}
        self.inserts = {}  # {coord_name: set([consensus_index])}
        self.consensus = {}  # {coord_name: consensus_amino_seq}

        # populates these dictionaries, generates amino acid counts
        self._count_reads(aligned_reads)

        if not self.seed_aminos:
            # no valid reads were aligned to this region and counted, skip next step
            self.coordinate_refs = {}
        else:
            self.coordinate_refs = self.projects.getCoordinateReferences(self.seed)
            if not self.coordinate_refs:
                # No coordinate references defined for this region.
                # Pad the amino acid count list until it has the same length
                # as the seed reference, then skip next step.
                seed_ref = self.projects.getReference(self.seed)
                while len(self.seed_aminos[0])*3 < len(seed_ref):
                    self.seed_aminos[0].append(SeedAmino(3*len(self.seed_aminos[0])))

        # iterate over coordinate references defined for this region
        for coordinate_name, coordinate_ref in self.coordinate_refs.items():
            self._map_to_coordinate_ref(coordinate_name, coordinate_ref)
            report_aminos = self.reports[coordinate_name]
            if coordinate_name == v3_overlap_region_name:
                self.v3_overlap_region_name = v3_overlap_region_name
                self.v3_overlap_aminos = report_aminos
                self.reports[coordinate_name] = []
                self.inserts[coordinate_name] = []

    def read_clipping(self, clipping_csv):
        for row in csv.DictReader(clipping_csv):
            pos = int(row['pos'])
            count = int(row['count'])
            self.clipping_counts[row['refname']][pos] += count

    def read_insertions(self, conseq_ins_csv):
        reader = csv.DictReader(conseq_ins_csv)
        for refname, rows in groupby(reader, itemgetter('refname')):
            insertion_names = defaultdict(set)  # {pos: set([qname])}
            for row in rows:
                pos = int(row['pos'])
                pos_names = insertion_names[pos]
                pos_names.add(row['qname'])
            self.conseq_insertion_counts[refname] = Counter(
                {pos: len(names) for pos, names in insertion_names.items()})

    @staticmethod
    def _create_amino_writer(amino_file):
        columns = ['seed',
                   'region',
                   'q-cutoff',
                   'query.nuc.pos',
                   'refseq.aa.pos']
        columns.extend(AMINO_ALPHABET)
        columns.extend(
            ('X', 'partial', 'del', 'ins', 'clip', 'v3_overlap', 'coverage'))
        return csv.DictWriter(amino_file,
                              columns,
                              lineterminator=os.linesep)

    def write_amino_header(self, amino_file):
        self.amino_writer = self._create_amino_writer(amino_file)
        self.amino_writer.writeheader()

    def write_amino_counts(self, amino_writer=None, coverage_summary=None):
        """ Write amino counts file.

        Must have already called write_nuc_counts() to calculate max_clip_count
        for each ReportAmino.
        """
        amino_writer = amino_writer or self.amino_writer
        regions = sorted(self.reports.keys())
        for region in regions:
            coverage_sum = 0.0
            pos_count = 0
            for report_amino in self.reports[region]:
                seed_amino = report_amino.seed_amino
                query_pos = (str(seed_amino.consensus_nuc_index + 1)
                             if seed_amino.consensus_nuc_index is not None
                             else '')
                row = {'seed': self.seed,
                       'region': region,
                       'q-cutoff': self.qcut,
                       'query.nuc.pos': query_pos,
                       'refseq.aa.pos': report_amino.position,
                       'X': seed_amino.low_quality,
                       'partial': seed_amino.partial,
                       'del': seed_amino.deletions,
                       'ins': report_amino.insertion_count,
                       'clip': report_amino.max_clip_count,
                       'v3_overlap': seed_amino.v3_overlap,
                       'coverage': seed_amino.deletions}
                for letter in AMINO_ALPHABET:
                    letter_count = seed_amino.counts[letter]
                    row[letter] = letter_count
                    coverage_sum += letter_count
                    row['coverage'] += letter_count
                amino_writer.writerow(row)
                pos_count += 1
            if coverage_summary is not None and pos_count > 0:
                region_coverage = coverage_sum / pos_count
                old_coverage = coverage_summary.get('avg_coverage', -1)
                if region_coverage > old_coverage:
                    coverage_summary['avg_coverage'] = region_coverage
                    coverage_summary['coverage_region'] = region
                    coverage_summary['region_width'] = pos_count

    @staticmethod
    def _create_nuc_writer(nuc_file):
        return csv.DictWriter(nuc_file,
                              ['seed',
                               'region',
                               'q-cutoff',
                               'query.nuc.pos',
                               'refseq.nuc.pos',
                               'A',
                               'C',
                               'G',
                               'T',
                               'N',
                               'del',
                               'ins',
                               'clip',
                               'v3_overlap',
                               'coverage'],
                              lineterminator=os.linesep)

    def write_nuc_header(self, nuc_file):
        self.nuc_writer = self._create_nuc_writer(nuc_file)
        self.nuc_writer.writeheader()

    def write_counts(self, region, seed_amino, report_amino, nuc_writer):
        """ Write a row of nucleotide counts for a single position.

        :param region: the coordinate reference name
        :param seed_amino: the SeedAmino object for this position
        :param ReportAmino? report_amino: the ReportAmino object for this position
        :param nuc_writer: the CSV writer to write the row into
        """
        max_clip_count = 0
        total_insertion_count = 0
        seed_insertion_counts = self.conseq_insertion_counts[self.seed]
        for i, seed_nuc in enumerate(seed_amino.nucleotides):
            if seed_amino.consensus_nuc_index is None:
                query_pos_txt = ''
                insertion_count = clip_count = 0
            else:
                query_pos = i + seed_amino.consensus_nuc_index + 1
                query_pos_txt = str(query_pos)
                clip_count = self.clipping_counts[self.seed][query_pos]
                max_clip_count = max(clip_count, max_clip_count)
                insertion_count = seed_insertion_counts[query_pos]
            ref_pos = (str(i + 3*report_amino.position - 2)
                       if report_amino is not None
                       else '')
            if i == 2 and report_amino is not None:
                insertion_counts = self.insert_writer.insert_pos_counts[
                    (self.seed, region)]
                insertion_count += insertion_counts[report_amino.position]
            total_insertion_count += insertion_count
            row = {'seed': self.seed,
                   'region': region,
                   'q-cutoff': self.qcut,
                   'query.nuc.pos': query_pos_txt,
                   'refseq.nuc.pos': ref_pos,
                   'del': seed_nuc.counts['-'],
                   'ins': insertion_count,
                   'clip': clip_count,
                   'v3_overlap': seed_nuc.v3_overlap,
                   'coverage': seed_nuc.counts['-']}
            for base in 'ACTGN':
                nuc_count = seed_nuc.counts[base]
                row[base] = nuc_count
                if base != 'N':
                    row['coverage'] += nuc_count
            nuc_writer.writerow(row)
        if report_amino is not None:
            report_amino.max_clip_count = max_clip_count
            report_amino.insertion_count = total_insertion_count

    def write_nuc_counts(self, nuc_writer=None):
        nuc_writer = nuc_writer or self.nuc_writer

        if not self.coordinate_refs:
            for seed_amino in self.seed_aminos[0]:
                self.write_counts(self.seed, seed_amino, None, nuc_writer)
        else:
            for region, report_aminos in sorted(self.reports.items()):
                for report_amino in report_aminos:
                    self.write_counts(region, report_amino.seed_amino, report_amino, nuc_writer)

    @staticmethod
    def _create_consensus_writer(conseq_file):
        return csv.DictWriter(conseq_file,
                              ['region',
                               'q-cutoff',
                               'consensus-percent-cutoff',
                               'offset',
                               'sequence'],
                              lineterminator=os.linesep)

    def write_consensus_header(self, conseq_file):
        self.conseq_writer = self._create_consensus_writer(conseq_file)
        self.conseq_writer.writeheader()

    def write_consensus(self, conseq_writer=None):
        conseq_writer = conseq_writer or self.conseq_writer
        for mixture_cutoff in self.conseq_mixture_cutoffs:
            consensus = ''
            offset = None
            for seed_amino in self.seed_aminos[0]:
                if offset is None:
                    amino_coverage = sum(seed_amino.counts.values())
                    if (not amino_coverage or
                            amino_coverage < self.consensus_min_coverage):
                        continue
                    offset = seed_amino.consensus_nuc_index
                for seed_nuc in seed_amino.nucleotides:
                    nuc_coverage = sum(seed_nuc.counts.values())
                    if nuc_coverage < self.consensus_min_coverage:
                        nuc_consensus = '-'
                    else:
                        nuc_consensus = seed_nuc.get_consensus(mixture_cutoff)
                    consensus += nuc_consensus
            if offset is not None:
                conseq_writer.writerow(
                    {'region': self.seed,
                     'q-cutoff': self.qcut,
                     'consensus-percent-cutoff': format_cutoff(mixture_cutoff),
                     'offset': offset,
                     'sequence': consensus})

    @staticmethod
    def _create_failure_writer(fail_file):
        return csv.DictWriter(fail_file,
                              ['seed',
                               'region',
                               'qcut',
                               'queryseq',
                               'refseq'],
                              lineterminator=os.linesep)

    def write_failure_header(self, fail_file):
        self.fail_writer = self._create_failure_writer(fail_file)
        self.fail_writer.writeheader()

    def write_failure(self, fail_writer=None):
        fail_writer = fail_writer or self.fail_writer
        if any(self.reports.values()):
            return  # Successfully aligned at least one coordinate reference.
        for region, report_aminos in sorted(self.reports.items()):
            coordinate_ref = self.projects.getReference(region)
            fail_writer.writerow(dict(seed=self.seed,
                                      region=region,
                                      qcut=self.qcut,
                                      queryseq=self.consensus[region],
                                      refseq=coordinate_ref))

    def write_insertions(self, insert_writer=None):
        insert_writer = insert_writer or self.insert_writer
        for coordinate_name, coordinate_inserts in self.inserts.items():
            insert_writer.write(coordinate_inserts,
                                coordinate_name,
                                self.reports[coordinate_name])

    def read_remap_conseqs(self, remap_conseq_csv):
        self.remap_conseqs = dict(map(itemgetter('region', 'sequence'),
                                      csv.DictReader(remap_conseq_csv)))

    @staticmethod
    def group_deletions(positions):
        """ Group deletion positions into groups that are within 13 bases.

        They also have to have at least 13 bases without deletions in between
        the groups.
        """
        if not positions:
            return
        gap_size = 13
        working_positions = positions[:]
        # Avoid special check for end condition by adding an extra group.
        working_positions.append(positions[-1] + gap_size)
        next_yield = 0
        for i, pos in enumerate(working_positions):
            if i == 0:
                continue
            start = positions[next_yield]
            end = positions[i-1]
            if pos - end >= gap_size:
                if end - start < gap_size and (i - next_yield) % 3 == 0:
                    yield positions[next_yield:i]
                next_yield = i

    def align_deletions(self, aligned_reads):
        """ Align codon deletions to the codon boundaries.

        :param aligned_reads: group of rows from the CSV reader of the aligned
            reads.
        :return: a generator of rows from the CSV reader with codon deletions
            aligned to the codon boundaries.
        """
        if self.remap_conseqs is None:
            yield from aligned_reads
        reading_frames = None
        for row in aligned_reads:
            if reading_frames is None:
                reading_frames = self.load_reading_frames(row['refname'])
            seq = row['seq']
            seq_offset = int(row['offset'])
            for old_positions in self.group_deletions(
                    [match.start() for match in re.finditer('-', seq)]):
                chars = list(seq)
                anchor_index = len(old_positions)//2
                anchor_pos = old_positions[anchor_index]
                start_pos = anchor_pos-len(old_positions)//2
                end_pos = start_pos + len(old_positions)
                nuc_pos = seq_offset + start_pos
                reading_frame = reading_frames[nuc_pos]
                offset = (nuc_pos + reading_frame) % 3
                if offset == 1:
                    start_pos -= 1
                    end_pos -= 1
                elif offset == 2:
                    start_pos += 1
                    end_pos += 1
                new_positions = list(range(start_pos, end_pos))
                for pos in reversed(old_positions):
                    del chars[pos]
                for pos in new_positions:
                    chars.insert(pos, '-')
                seq = ''.join(chars)
                row['seq'] = seq
            yield row

    def load_reading_frames(self, seed_name):
        """ Calculate reading frames along a consensus sequence.

        :param seed_name: the name of the seed to look up
        :return: {pos: frame} zero-based position and reading frame for each
            position. Frame 1 needs one nucleotide inserted at start.
        """
        result = Counter()
        conseq = self.remap_conseqs[seed_name]
        coord_refs = self.projects.getCoordinateReferences(seed_name)
        for coord_ref in coord_refs.values():
            best_alignment = (-1000000, '', '', 0)
            for frame_index in range(3):
                conseq_aminos = translate('-'*frame_index + conseq)
                aconseq, acoord, score = self._pair_align(conseq_aminos,
                                                          coord_ref,
                                                          GAP_OPEN_COORD,
                                                          GAP_EXTEND_COORD)
                best_alignment = max(best_alignment, (score, aconseq, acoord, frame_index))
            score, aconseq, acoord, frame_index = best_alignment
            if frame_index == 0:
                continue  # defaults to 0, no need to record
            conseq_codon_index = -1
            coord_codon_index = -1
            for conseq_amino, coord_amino in zip(aconseq, acoord):
                if conseq_amino != '-':
                    conseq_codon_index += 1
                if coord_amino == '-':
                    continue
                coord_codon_index += 1
                
                nuc_pos = conseq_codon_index * 3 - frame_index
                for i in range(3):
                    result[nuc_pos+i] = frame_index
        return result


class SeedAmino(object):
    """
    Records the frequencies of amino acids at a given position of the
    aligned reads as determined by the consensus sequence.
    """
    def __init__(self, consensus_nuc_index, counts=None):
        self.v3_overlap = 0
        self.consensus_nuc_index = consensus_nuc_index
        self.counts = counts or Counter()
        self.nucleotides = [SeedNucleotide() for _ in range(3)]
        self.low_quality = 0
        self.partial = 0
        self.deletions = 0

    def __repr__(self):
        if self.counts:
            return 'SeedAmino({!r}, {!r})'.format(self.consensus_nuc_index,
                                                  dict(self.counts))
        return 'SeedAmino({})'.format(self.consensus_nuc_index)

    def count_aminos(self, codon_seq, count):
        """ Record a set of reads at this position in the seed reference.
        @param codon_seq: a string of three nucleotides that were read at this
                          position, may be padded with spaces at the start
                          or end of a sequence, or dashes for deletions
        @param count: the number of times they were read
        """
        if 'N' in codon_seq:
            self.low_quality += count
        elif '---' == codon_seq:
            self.deletions += count
        elif '-' in codon_seq:
            self.partial += count
        elif ' ' not in codon_seq and 'n' not in codon_seq:
            amino = translate(codon_seq.upper())
            self.counts[amino] += count
        for i, nuc in enumerate(codon_seq):
            if nuc != ' ':
                seed_nucleotide = self.nucleotides[i]
                seed_nucleotide.count_nucleotides(nuc, count)

    def get_report(self):
        """ Build a report string with the counts of each amino acid.

        Report how many times each amino acid was seen in count_aminos().
        @return: comma-separated list of counts in the same order as the
        AMINO_ALPHABET list
        """
        return ','.join([str(self.counts[amino])
                         for amino in AMINO_ALPHABET])

    def get_consensus(self):
        """ Find the amino acid that was seen most often in count_aminos().

        If there is a tie, just pick one of the tied amino acids.
        @return: the letter of the most common amino acid
        """
        consensus = self.counts.most_common(1)
        return '-' if not consensus else consensus[0][0]

    def count_overlap(self, other):
        for nuc1, nuc2 in zip(self.nucleotides, other.nucleotides):
            nuc1.count_overlap(nuc2)
            self.v3_overlap = max(self.v3_overlap, nuc1.v3_overlap)


class SeedNucleotide(object):
    """
    Records the frequencies of nucleotides at a given position of the
    aligned reads as determined by the consensus sequence.
    """
    def __init__(self, counts=None):
        self.v3_overlap = 0
        self.counts = counts or Counter()

    def __repr__(self):
        return 'SeedNucleotide({!r})'.format(dict(self.counts))

    def count_nucleotides(self, nuc_seq, count):
        """ Record a set of reads at this position in the seed reference.
        @param nuc_seq: a single nucleotide letter that was read at this
        position
        @param count: the number of times it was read
        """
        if nuc_seq == 'n':
            "Represents gap between forward and reverse read, ignore."
        else:
            self.counts[nuc_seq] += count

    def get_report(self):
        """ Build a report string with the counts of each nucleotide.

        Report how many times each nucleotide was seen in count_nucleotides().
        @return: comma-separated list of counts for A, C, G, and T.
        """
        return ','.join(map(str, [self.counts[nuc] for nuc in 'ACGT']))

    def get_consensus(self, mixture_cutoff):
        """ Choose consensus nucleotide or mixture from the counts.

        @param mixture_cutoff: the minimum fraction of reads
            that a nucleotide must be found in for it to be considered,
            or MAX_CUTOFF to consider only the most common nucleotide.
        @return: The letter for the consensus nucleotide or mixture.
            Nucleotide mixtures are encoded by IUPAC symbols, and the most common
            nucleotide can be a mixture if there is a tie.
        """
        if not self.counts:
            return ''

        intermed = self.counts.most_common()

        # Remove gaps and low quality reads if there is anything else.
        for i in reversed(range(len(intermed))):
            nuc, _count = intermed[i]
            if nuc in ('N', '-') and len(intermed) > 1:
                intermed.pop(i)

        total_count = sum(self.counts.values())
        mixture = []
        min_count = (intermed[0][1]
                     if mixture_cutoff == MAX_CUTOFF
                     else total_count * mixture_cutoff)
        # filter for nucleotides that pass frequency cutoff
        for nuc, count in intermed:
            if count >= min_count:
                mixture.append(nuc)

        if len(mixture) > 1:
            mixture.sort()
            consensus = ambig_dict[''.join(mixture)]
        elif len(mixture) == 1:
            # no ambiguity
            consensus = mixture[0]
        else:
            # all reads were below the cutoff
            consensus = 'N'
        return consensus

    def count_overlap(self, other):
        for nuc in 'ACGT':
            self.v3_overlap += other.counts[nuc]


class ReportAmino(object):
    def __init__(self, seed_amino, position):
        """ Create a new instance.

        @param seed_amino: Counts for the
        """
        self.seed_amino = seed_amino
        self.position = position
        self.max_clip_count = 0
        self.insertion_count = 0

    def __repr__(self):
        return 'ReportAmino({!r}, {})'.format(self.seed_amino, self.position)


class InsertionWriter(object):
    def __init__(self, insert_file):
        """ Initialize a writer object.

        @param insert_file: an open file that the data will be written to
        """
        self.insert_writer = csv.DictWriter(insert_file,
                                            ['seed',
                                             'region',
                                             'qcut',
                                             'left',
                                             'insert',
                                             'count',
                                             'before'],
                                            lineterminator=os.linesep)
        self.insert_writer.writeheader()

        # {(seed, region): {pos: insert_count}}
        self.insert_pos_counts = defaultdict(Counter)
        self.seed = self.qcut = None
        self.insert_file_name = getattr(insert_file, 'name', None)
        self.nuc_seqs = self.create_counter('nuc_read_counts')  # {nuc_seq: count}

    def create_counter(self, base_name):
        if self.insert_file_name is None:
            counter = Counter()
        else:
            dirname = os.path.dirname(self.insert_file_name)
            file_prefix = os.path.join(os.path.abspath(dirname),
                                       base_name)
            counter = BigCounter(file_prefix=file_prefix)
        return counter

    def start_group(self, seed, qcut):
        """ Start a new group of reads.

        @param seed: the name of the region these reads mapped to
        @param qcut: the quality cut off used for these reads
        """
        self.seed = seed
        self.qcut = qcut
        self.nuc_seqs.clear()

    def add_nuc_read(self, offset_sequence, count):
        """ Add a read to the group.

        @param offset_sequence: the nucleotide sequence of the read that has
            had dashes added to offset it into the consensus sequence
            coordinates
        @param count: the number of times this sequence was read
        """
        self.nuc_seqs[offset_sequence] += count

    def write(self, inserts, region, report_aminos=None):
        """ Write any insert ranges to the file.

        Sequence data comes from the reads that were added to the current group.
        @param inserts: indexes of positions in the reads that should be
            reported as insertions.
        @param region: the name of the coordinate region the current group was
            mapped to
        @param report_aminos: a list of ReportAmino objects that represent the
            sequence that successfully mapped to the coordinate reference.
        """
        if len(inserts) == 0:
            return

        report_aminos = report_aminos or []

        region_insert_pos_counts = self.insert_pos_counts[(self.seed, region)]
        inserts = list(inserts)
        inserts.sort()

        # convert insertion coordinates into contiguous ranges
        insert_ranges = []
        for insert in inserts:
            if not insert_ranges or insert != insert_ranges[-1][1]:
                # just starting or we hit a gap
                insert_ranges.append([insert, insert + 3])
            else:
                insert_ranges[-1][1] += 3

        # enumerate insertions by popping out all AA sub-string variants
        insert_counts = OrderedDict()  # {left: {insert_seq: count}}
        insert_targets = {}  # {left: inserted_before_pos}
        for left, right in insert_ranges:
            for report_amino in report_aminos:
                seed_amino = report_amino.seed_amino
                if seed_amino.consensus_nuc_index == right:
                    insert_targets[left] = report_amino.position
                    break
            current_counts = Counter()
            insert_counts[left] = current_counts
            for nuc_seq, count in self.nuc_seqs.items():
                insert_nuc_seq = nuc_seq[left:right]
                is_valid = (insert_nuc_seq and
                            'n' not in insert_nuc_seq and
                            '-' not in insert_nuc_seq)
                if is_valid:
                    insert_amino_seq = translate(insert_nuc_seq)
                    if insert_amino_seq:
                        current_counts[insert_amino_seq] += count

        # record insertions to CSV
        for left, counts in insert_counts.items():
            for insert_seq, count in counts.most_common():
                insert_before = insert_targets.get(left)
                # Only care about insertions in the middle of the sequence,
                # so ignore any that come before or after the reference.
                # Also report if we're in test mode (no report_aminos).
                if not report_aminos or insert_before not in (1, None):
                    row = dict(seed=self.seed,
                               region=region,
                               qcut=self.qcut,
                               left=left + 1,
                               insert=insert_seq,
                               count=count,
                               before=insert_before)
                    self.insert_writer.writerow(row)
                    if insert_before is not None:
                        region_insert_pos_counts[insert_before-1] += count


def format_cutoff(cutoff):
    """ Format the cutoff fraction as a string to use as a name. """

    if cutoff == MAX_CUTOFF:
        return cutoff
    return '{:0.3f}'.format(cutoff)


def aln2counts(aligned_csv,
               nuc_csv,
               amino_csv,
               coord_ins_csv,
               conseq_csv,
               failed_align_csv,
               callback=None,
               coverage_summary_csv=None,
               clipping_csv=None,
               conseq_ins_csv=None,
               g2p_aligned_csv=None,
               remap_conseq_csv=None):
    """
    Analyze aligned reads for nucleotide and amino acid frequencies.
    Generate consensus sequences.
    @param aligned_csv:         Open file handle containing aligned reads (from sam2aln)
    @param nuc_csv:             Open file handle to write nucleotide frequencies.
    @param amino_csv:           Open file handle to write amino acid frequencies.
    @param coord_ins_csv:       Open file handle to write insertions relative to coordinate reference.
    @param conseq_csv:          Open file handle to write consensus sequences.
    @param failed_align_csv:    Open file handle to write sample consensus sequences that failed to
                                align to the coordinate reference.
    @param callback: a function to report progress with three optional
        parameters - callback(message, progress, max_progress)
    @param coverage_summary_csv: Open file handle to write coverage depth.
    @param clipping_csv: Open file handle containing soft clipping counts
    @param conseq_ins_csv: Open file handle containing insertions relative to consensus sequence
    @param g2p_aligned_csv: Open file handle containing aligned reads (from fastq_g2p)
    @param remap_conseq_csv: Open file handle containing consensus sequences
        from the remap step.
    """
    # load project information
    projects = ProjectConfig.loadDefault()

    # initialize reporter classes
    insert_writer = InsertionWriter(coord_ins_csv)
    report = SequenceReport(insert_writer,
                            projects,
                            CONSEQ_MIXTURE_CUTOFFS)
    report.consensus_min_coverage = CONSENSUS_MIN_COVERAGE
    report.write_amino_header(amino_csv)
    report.write_consensus_header(conseq_csv)
    report.write_failure_header(failed_align_csv)
    report.write_nuc_header(nuc_csv)
    if coverage_summary_csv is None:
        coverage_summary = coverage_writer = None
    else:
        coverage_writer = csv.DictWriter(coverage_summary_csv,
                                         ['avg_coverage',
                                          'coverage_region',
                                          'region_width'],
                                         lineterminator=os.linesep)
        coverage_writer.writeheader()
        coverage_summary = {}

    if callback:
        aligned_filename = getattr(aligned_csv, 'name', None)
        if aligned_filename:
            file_size = os.stat(aligned_filename).st_size
            report.enable_callback(callback, file_size)

    if clipping_csv is not None:
        report.read_clipping(clipping_csv)
    if conseq_ins_csv is not None:
        report.read_insertions(conseq_ins_csv)
    if remap_conseq_csv is not None:
        report.read_remap_conseqs(remap_conseq_csv)

    report.process_reads(g2p_aligned_csv, aligned_csv, coverage_summary)

    if coverage_summary_csv is not None:
        if coverage_summary:
            coverage_writer.writerow(coverage_summary)


def main():
    args = parse_args()
    aln2counts(args.aligned_csv,
               args.nuc_csv,
               args.amino_csv,
               args.coord_ins_csv,
               args.conseq_csv,
               args.failed_align_csv,
               clipping_csv=args.clipping_csv,
               conseq_ins_csv=args.conseq_ins_csv,
               g2p_aligned_csv=args.g2p_aligned_csv,
               remap_conseq_csv=args.remap_conseq_csv)


if __name__ == '__main__':
    main()
