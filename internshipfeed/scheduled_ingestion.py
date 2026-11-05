"""Run bounded ingestion inside the active cron invocation without email or local files."""
import time

from .ingestion import run_ingestion


def run_scheduled_ingestion():
    run = run_ingestion(write_files=False, enrichment_limit=5, delay=0,
                        deadline=time.monotonic() + 240)
    return {
        'id': run['id'], 'status': run['status'], 'sources': len(run['sources']),
        **{key: run.get(key, 0) for key in ('scraped', 'parsed', 'inserted', 'updated', 'unchanged',
                                         'unchanged_sources', 'closed', 'removed', 'failed',
                                         'enrichment_attempts', 'enrichment_failed', 'enrichment_deferred')},
        'new_canonical_jobs': len(run.get('new_canonical_job_ids', [])),
    }
