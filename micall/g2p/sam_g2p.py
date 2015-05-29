import pssm_lib
import argparse
from csv import DictReader
from micall.core.sam2aln import apply_cigar, merge_pairs
from micall.utils.translation import translate

QMIN = 20   # minimum base quality within insertions
QCUT = 10   # minimum base quality to not be censored
QDELTA = 5

def parse_args():
    parser = argparse.ArgumentParser(description='Calculate g2p scores from amino acid sequences.')

    parser.add_argument('remap_csv', type=argparse.FileType('rU'),
                        help='<input> CSV containing remap output (modified SAM)')
    parser.add_argument('nuc_csv', type=argparse.FileType('rU'),
                        help='<input> CSV containing nucleotide frequency output from aln2counts.py')
    parser.add_argument('g2p_csv', type=argparse.FileType('w'),
                        help='<output> CSV containing g2p predictions.')
    return parser.parse_args()


class RegionTracker:
    def __init__(self, tracked_region):
        self.tracked_region = tracked_region
        self.ranges = {}

    def add_nuc(self, seed, region, query_pos):
        """
         # Add a nucleotide position to the tracker.
        :param seed: name of the seed region
        :param region: name of the coordinate region
        :param query_pos: query position in the consensus coordinates
        :return: unused
        """
        if region != self.tracked_region:
            return

        if seed in self.ranges:
            range = self.ranges[seed]
            if range[1] < query_pos:
                range[1] = query_pos
            elif query_pos < range[0]:
                range[0] = query_pos
        else:
            self.ranges.update({seed: [query_pos, query_pos]})

    def get_range(self, seed):
        """
        Get the minimum and maximum query positions that were seen for a seed.
        :param seed: name of the seed region
        :return: array of two integers
        """
        return self.ranges[seed]


def main():
    args = parse_args()

    pairs = {}  # cache read for pairing
    merged = {}  # tabular merged sequence variants
    tracker = RegionTracker('V3LOOP')

    # look up clipping region for each read
    with DictReader(args.nuc_csv) as reader:
        for row in reader:
            tracker.add_nuc(row['seed'], row['region'], int(row['query_pos'])-1)

    with DictReader(args.remap_csv) as reader:
        for row in reader:
            clip_from, clip_to = tracker.get_range(row['rname'])
            if clip_from is None:
                # uninteresting region
                continue

            seq, qual = apply_cigar(row['cigar'], row['seq'], row['qual'])
            offset = int(row['pos'])-1
            seq2 = ('-' * offset + seq)[clip_from:clip_to]
            qual2 = ('!' * offset + qual)[clip_from:clip_to]

            mate = pairs.get(row['qname'], None)
            if mate:
                # merge reads
                seq1 = mate['seq']
                qual1 = mate['qual']
                mseq = merge_pairs(seq1, seq2, qual1, qual2)
                if mseq not in merged:
                    merged.update({mseq: 0})
                merged[mseq] += 1

            else:
                pairs.update({row['qname']: {'seq': seq2, 'qual': qual2}})

    sorted = [(v,k) for k, v in merged.iteritems()]
    sorted.sort(reverse=True)

    # apply g2p algorithm to merged reads
    with args.g2p_csv as f:
        f.write('rank,count,g2p,fpr,aligned,error\n')  # CSV header
        rank = 0
        for count, s in sorted:
            rank += 1
            prefix = '%d,%d' % (rank, count)
            seq = s.replace('-', '')
            seqlen = len(seq)
            if seq.upper().count('N') > (0.5*seqlen):
                # if more than 50% of the sequence is garbage
                f.write('%s,,,,low quality\n' % prefix)
                continue

            if seqlen == 0:
                f.write('%s,,,,zerolength\n' % prefix)
                continue

            prot = translate(seq, 0)

            # sanity check 1 - bounded by cysteines
            if not prot.startswith('C') or not prot.endswith('C'):
                f.write('%s,,,%s,cysteines\n' % (prefix, prot))
                continue

            # sanity check 2 - no ambiguous codons
            if prot.count('X') > 0:
                f.write('%s,,,%s,ambiguous\n' % (prefix, prot))
                continue

            # sanity check 3 - no stop codons
            if prot.count('*') > 0:
                f.write('%s,,,%s,stop codons\n' % (prefix, prot))
                continue

            # sanity check 4 - V3 length in range 32-40 inclusive
            if len(prot) < 32 or len(prot) > 40:
                f.write('%s,,,%s,length\n' % (prefix, prot))
                continue

            score, aligned = pssm_lib.run_g2p(seq)
            try:
                aligned2 = ''.join(aligned)
            except:
                # sequence failed to align
                f.write('%s,%s,,,failed to align\n' % (prefix, score))
                continue

            fpr = pssm_lib.g2p_to_fpr(score)
            f.write('%s,%s,%s,%s,\n' % (prefix, score, fpr, aligned2))

        f.close()


if __name__ == '__main__':
    main()