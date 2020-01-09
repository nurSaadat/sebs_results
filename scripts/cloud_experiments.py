#!python3

import argparse
import importlib
import sys

from experiments_utils import *

parser = argparse.ArgumentParser(description='Run cloud experiments.')
parser.add_argument('cloud', choices=['azure','aws'], help='Cloud to use')
parser.add_argument('benchmark', type=str, help='Benchmark name')
parser.add_argument('output_dir', type=str, help='Output dir')
parser.add_argument('language', choices=['python', 'nodejs', 'cpp'],
                    help='Benchmark language')
parser.add_argument('size', choices=['test', 'small', 'large'],
                    help='Benchmark input test size')
parser.add_argument('--repetitions', action='store', default=5, type=int,
                    help='Number of experimental repetitions')
parser.add_argument('--verbose', action='store', default=False, type=bool,
                    help='Verbose output')
args = parser.parse_args()

# create cloud object
if args.cloud == 'aws':
    from cloud_providers import aws
    client = aws()
else:
    # TODO:
    pass

def prepare_input(client, benchmark, benchmark_path, size):
    # Look for input generator file in the directory containing benchmark
    sys.path.append(benchmark_path)
    mod = importlib.import_module('input')
    buckets = mod.buckets_count()
    storage = client.get_storage(benchmark, buckets, False)
    # Get JSON and upload data as required by benchmark
    input_config = mod.generate_input(size, storage.input_buckets, storage.output_buckets, storage.uploader_func)
    return input_config

benchmark_summary = {}

# 0. Input args
args = parser.parse_args()
verbose = args.verbose

# 1. Create output dir
output_dir = create_output(args.output_dir, args.verbose)
logging.info('# Created experiment output at {}'.format(args.output_dir))

# 2. Locate benchmark
benchmark_path = find_benchmark(args.benchmark)
logging.info('# Located benchmark {} at {}'.format(args.benchmark, benchmark_path))

# 3. Build code package
code_package, code_size = create_code_package(args.benchmark, benchmark_path, args.language, args.verbose)
logging.info('# Created code_package {} of size {}'.format(code_package, code_size))

# 5. Prepare benchmark input
input_config = prepare_input(client, args.benchmark, benchmark_path, args.size)

# create bucket if it does not exist
# upload data if does not exist

# pack function and deploy

# get experiment and run

# get metrics