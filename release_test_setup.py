""" Set up raw data for the release test.

Copies a set of run folders and selected FASTQ files from the main raw data
folder to your developer workstation. Configure the set of samples to copy
in test_samples.csv.
You'll need to upload the QC data from the InterOp folders before you can
start MISEQ_MONITOR.py.
"""

from argparse import ArgumentParser
from csv import DictReader
from datetime import datetime
import errno
from glob import glob
from itertools import groupby
from operator import attrgetter
import os
from shutil import copytree, copy, rmtree

from micall.settings import rawdata_mount, pipeline_version, ERROR_PROCESSING, \
    NEEDS_PROCESSING


def parse_args():
    parser = ArgumentParser(description='Prepare sample runs for testing a new release.')
    parser.add_argument(
        'source_folder',
        help='Main RAWDATA folder that will be copied to local RAWDATA.')
    parser.add_argument(
        '-s',
        '--samples_csv',
        default='test_samples.csv',
        help='CSV file with sample and run names.')
    parser.add_argument(
        '-m',
        '--min_run_name',
        help='Select all later runs, (e.g., "161201"). Overrides samples_csv.')
    # parser.add_argument('-r', '--run_name',
    #                     help='Run name to set up. Overrides samples_csv.')
    # parser.add_argument('-e', '--enum',
    #                     help='Extraction number to set up. Requires run_name.')
    return parser.parse_args()


class Sample(object):
    def __init__(self, run_name, extract_num):
        self.run_name = run_name
        self.extract_num = extract_num
        self.fastq_paths = None

    def find(self, source_folder):
        run_path = os.path.join(source_folder, 'MiSeq', 'runs', self.run_name)
        if os.path.exists(run_path):
            self.run_name = run_path
        else:
            run_parts = self.run_name.split('.')
            if len(run_parts) < 2:
                run_parts.append('M01841')
            format_string = '%d-%b-%y' if len(run_parts[0]) == 9 else '%d-%b-%Y'
            run_date = datetime.strptime(run_parts[0], format_string)
            base_run_name = run_date.strftime('%y%m%d') + '_' + run_parts[1]
            pattern = os.path.join(source_folder, 'MiSeq', 'runs', base_run_name+'*')
            matches = glob(pattern)
            if len(matches) != 1:
                raise RuntimeError('Expected one match for {}, but found: {}'.format(
                    pattern,
                    matches))
            self.run_name = matches[0]

        pattern = os.path.join(self.run_name,
                               'Data',
                               'Intensities',
                               'BaseCalls',
                               self.extract_num + '*_R1_*')
        matches = glob(pattern)
        if len(matches) == 0:
            raise RuntimeError('No matches found for ' + pattern)
        matches.sort()
        self.fastq_paths = matches
        run_parts = os.path.basename(self.run_name).split('_')
        run_date = datetime.strptime(run_parts[0], '%y%m%d')
        qai_run_name = run_date.strftime('%d-%b-%Y') + '.' + str(run_parts[1])
        print('LabMiseqRun.import("{}")'.format(qai_run_name))

    def setup_samples(self):
        base_run_name = os.path.basename(self.run_name)
        target_data_path = os.path.join(rawdata_mount,
                                        'MiSeq',
                                        'runs',
                                        base_run_name,
                                        'Data',
                                        'Intensities',
                                        'BaseCalls')
        suspended_path = os.path.join(target_data_path, 'suspended')
        sample_paths = []
        for fastq1_path in self.fastq_paths:
            fastq2_path = fastq1_path.replace('_R1_', '_R2_')
            for fastq_path in (fastq1_path, fastq2_path):
                base_name = os.path.basename(fastq_path)
                target_path = os.path.join(target_data_path, base_name)
                sample_paths.append(target_path)
                if not os.path.exists(target_path):
                    suspended_fastq_path = os.path.join(suspended_path,
                                                        base_name)
                    if os.path.exists(suspended_fastq_path):
                        os.rename(suspended_fastq_path,
                                  target_path)
                    else:
                        os.symlink(fastq_path, target_path)
        return sample_paths

    def setup_run(self):
        base_run_name = os.path.basename(self.run_name)
        target_run_path = os.path.join(rawdata_mount, 'MiSeq', 'runs', base_run_name)
        suspended_run_path = os.path.join(rawdata_mount,
                                          'MiSeq',
                                          'runs',
                                          'suspended',
                                          base_run_name)
        if os.path.exists(target_run_path):
            pass
        elif os.path.exists(suspended_run_path):
            os.rename(suspended_run_path, target_run_path)
        else:
            base_calls_path = os.path.join(target_run_path,
                                           'Data',
                                           'Intensities',
                                           'BaseCalls')
            os.makedirs(base_calls_path)
            os.symlink(os.path.join(self.run_name, 'InterOp'),
                       os.path.join(target_run_path, 'InterOp'))
            for filename in ('RunInfo.xml',
                             'runParameters.xml',
                             'SampleSheet.csv',
                             'needsprocessing'):
                copy(os.path.join(self.run_name, filename),
                     os.path.join(target_run_path, filename))
        results_path = os.path.join(target_run_path,
                                    'Results',
                                    'version_' + pipeline_version)
        try:
            rmtree(results_path)
        except OSError as ex:
            if ex.errno != errno.ENOENT:
                raise
        try:
            os.remove(os.path.join(target_run_path,
                                   ERROR_PROCESSING))
        except OSError as ex:
            if ex.errno != errno.ENOENT:
                raise
        return target_run_path


def suspend_inactive_runs(active_runs):
    active_run_names = set(map(os.path.basename, active_runs))
    local_runs = glob(os.path.join(rawdata_mount, 'MiSeq', 'runs', '*'))
    for run_path in local_runs:
        base_run_name = os.path.basename(run_path)
        if base_run_name not in active_run_names and base_run_name != 'suspended':
            suspended_path = os.path.join(rawdata_mount,
                                          'MiSeq',
                                          'runs',
                                          'suspended',
                                          base_run_name)
            os.rename(run_path, suspended_path)


def suspend_inactive_samples(run_path, active_sample_paths):
    base_calls_path = os.path.join(run_path, 'Data', 'Intensities', 'BaseCalls')
    local_samples = glob(os.path.join(base_calls_path, '*'))
    excluded = list(active_sample_paths)
    suspended_path = os.path.join(base_calls_path, 'suspended')
    excluded.append(suspended_path)
    for sample_path in local_samples:
        if sample_path not in excluded:
            if not os.path.exists(suspended_path):
                os.makedirs(suspended_path)
            os.rename(sample_path,
                      os.path.join(suspended_path, os.path.basename(sample_path)))
    try:
        os.rmdir(suspended_path)
    except OSError as ex:
        if ex.errno not in (errno.ENOTEMPTY, errno.ENOENT):
            raise


def find_run_folders(source_folder, min_run_name):
    pattern = os.path.join(source_folder, 'MiSeq', 'runs', '*')
    matches = glob(pattern)
    folder_names = [os.path.basename(match) for match in matches]
    recent_folders = [folder
                      for folder in folder_names
                      if folder >= min_run_name and folder[0].isnumeric()]
    folders_with_data = [folder
                         for folder in recent_folders
                         if os.path.exists(os.path.join(source_folder,
                                                        'MiSeq',
                                                        'runs',
                                                        folder,
                                                        NEEDS_PROCESSING))]
    return folders_with_data


def main():
    args = parse_args()
    if args.min_run_name is not None:
        run_folders = find_run_folders(args.source_folder, args.min_run_name)
        samples = [Sample(folder, '*') for folder in run_folders]
    else:
        with open(args.samples_csv, 'rU') as samples_csv:
            samples = [Sample(row['run'], row['enum'])
                       for row in DictReader(samples_csv)]
    for sample in samples:
        sample.find(args.source_folder)
    samples.sort(key=lambda s: (s.run_name, s.extract_num))
    runs = []
    for run, run_samples in groupby(samples, key=attrgetter('run_name')):
        runs.append(run)
        run_name = os.path.basename(run)
        print('--' + run_name)
        sample_paths = []
        run_path = None
        for sample in run_samples:
            run_path = sample.setup_run()
            sample_paths.extend(sample.setup_samples())
            print('  ' + ', '.join(map(os.path.basename, sample.fastq_paths)))
        suspend_inactive_samples(run_path, active_sample_paths=sample_paths)
    suspend_inactive_runs(active_runs=runs)

if __name__ == '__main__':
    main()
