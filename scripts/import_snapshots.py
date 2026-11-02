"""Replay one snapshot per registered source into the common repository."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from internshipfeed.ingestion import run_ingestion

parser = argparse.ArgumentParser()
parser.add_argument('--canadian', required=True)
parser.add_argument('--simplify', required=True)
args = parser.parse_args()
run = run_ingestion(input_paths={'canadian-tech': args.canadian, 'simplify': args.simplify}, enrich=False)
print(json.dumps({key: run[key] for key in ('status', 'scraped', 'inserted', 'updated', 'skipped_duplicates', 'failed', 'source_failures')}))
raise SystemExit(0 if run['status'] == 'success' else 1)
