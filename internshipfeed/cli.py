"""Optional CLI for the same scrape-and-enrich workflow used by the UI."""
import argparse
import json
import logging
from pathlib import Path

from . import db
from .ingestion import run_ingestion
from .sources import SOURCES


def main(argv=None):
    parser = argparse.ArgumentParser(description="Scrape and normalize internship sources into PostgreSQL")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path, default=db.ROOT / "data")
    parser.add_argument("--enrich", action="store_true", help="Compatibility flag: enrichment is now automatic")
    parser.add_argument("--no-enrich", action="store_true", help="Skip job-page fetching explicitly")
    parser.add_argument("--source", action="append", choices=[source.key for source in SOURCES], help="Select sources; defaults to both")
    parser.add_argument("--enrich-limit", type=int, default=None, help="Optional limit; default processes all open jobs")
    parser.add_argument("--refresh-descriptions", action="store_true")
    parser.add_argument("--delay", type=float, default=1.0)
    args = parser.parse_args(argv)
    if (args.enrich_limit is not None and args.enrich_limit < 0) or args.delay < 0:
        parser.error("Enrichment limit and delay must be nonnegative")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run = run_ingestion(input_path=args.input, output=args.output, enrich=not args.no_enrich,
                        enrichment_limit=args.enrich_limit, force=args.refresh_descriptions,
                        delay=args.delay, source_keys=args.source)
    print(json.dumps({key: run[key] for key in ("status", "scraped", "inserted", "updated", "skipped_duplicates", "failed", "enrichment_failed", "source_failures")}))
    return 0 if run["status"] == "success" else 1
