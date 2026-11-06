"""One workflow: fetch listings, read descriptions, transform, and persist."""
import hashlib
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import db
from .enrich import enrich_job
from .fetch import FETCH_DEADLINE, fetch_text
from .sources import CANADIAN
from .models import JobKeywords
from .normalize import normalize

logger = logging.getLogger(__name__)
SYNC_LOCK = 78112028


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


COUNTERS = ("scraped", "parsed", "processed", "inserted", "updated", "skipped_duplicates", "failed",
            "enrichment_attempts", "enrichment_successes", "enrichment_failed",
            "enrichment_cached", "enrichment_skipped", "closed", "dead_links", "unchanged", "removed")


def new_run():
    return {"id": str(uuid.uuid4()), "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None, "source": "all", "status": "running", "stage": "fetching",
            "sources": [], "errors": [], "unchanged_sources": 0, "new_canonical_job_ids": [], **dict.fromkeys(COUNTERS, 0)}


def recover_interrupted(conn):
    # Caller owns the session lock, so any persisted running row is orphaned.
    rows = conn.execute("SELECT report FROM ingestion_batches WHERE status = 'running'", ()).fetchall()
    for row in rows:
        report = row["report"]
        report.update(status="failed", stage="finished", finished_at=datetime.now(timezone.utc).isoformat(),
                      error="The previous sync was interrupted. Start a new sync to resume.")
        accounted = sum(report.get(key, 0) for key in ("inserted", "updated", "skipped_duplicates", "failed"))
        report["failed"] = report.get("failed", 0) + max(0, report.get("scraped", 0) - accounted)
        db.record_batch(conn, report)
        conn.execute("""UPDATE ingestion_runs SET status='failed', finished_at=clock_timestamp(),
            error_summary='Sync interrupted; resume from retained snapshots and postings.',
            records_failed=GREATEST(records_failed, records_fetched-records_inserted-records_updated-records_skipped)
            WHERE batch_id=%s AND status='running'""", (report['id'],))


def retain_description(job, old):
    if old and old.description:
        for key in ("description", "keywords", "extraction_method", "resolved_url", "enriched_at"):
            setattr(job, key, getattr(old, key))


def process_description(job, old, run, cache, enrich, limit, force, delay):
    if job.status == "closed":
        run["closed"] += 1
        run["enrichment_skipped"] += 1
        return
    if old and old.status == "dead" and old.url == job.url and old.link_checked_at and old.link_checked_at > datetime.now(timezone.utc) - timedelta(days=1) and not force:
        job.status = "dead"
        run["dead_links"] += 1
        run["enrichment_skipped"] += 1
        return
    fresh = old and old.status == "open" and old.description and old.enriched_at and old.url == job.url and old.enriched_at > datetime.now(timezone.utc) - timedelta(days=1)
    if enrich and job.status == "open" and job.url:
        if fresh and not force:
            retain_description(job, old)
            run["enrichment_cached"] += 1
        elif limit is None or run["enrichment_attempts"] < limit:
            if run["enrichment_attempts"]:
                time.sleep(delay)
            enrichment = enrich_job(job, cache)
            run["enrichment_attempts"] += 1
            job.link_checked_at = datetime.now(timezone.utc)
            job.enrichment_status = enrichment["status"]
            job.enrichment_error = enrichment.get("error")
            if enrichment.get("dead"):
                job.status = "dead"
                run["dead_links"] += 1
                run["enrichment_skipped"] += 1
                return
            if enrichment["status"] == "success":
                job.description = enrichment["description"]
                job.keywords = JobKeywords.model_validate(enrichment["keywords"])
                job.extraction_method = enrichment["extraction_method"]
                job.resolved_url = enrichment.get("resolved_url")
                job.enriched_at = datetime.now(timezone.utc)
                run["enrichment_successes"] += 1
            else:
                retain_description(job, old)
                key = "enrichment_failed" if enrichment["status"] in {"failed", "unavailable"} else "enrichment_skipped"
                run[key] += 1
        else:
            job.enrichment_status = "not_attempted"
            run['enrichment_skipped'] += 1
            retain_description(job, old)
    else:
        retain_description(job, old)
        run["enrichment_skipped"] += 1
    # An explicitly metadata-only run must not resurrect a confirmed dead link.
    if old and old.status == "dead" and old.url == job.url and job.enrichment_status != "success":
        job.status = "dead"
        return

def _metadata_changed(job, old):
    fields = ('company', 'title', 'location', 'url', 'posted_at', 'term', 'status')
    return old is None or any(getattr(job, key) != getattr(old, key) for key in fields)


def _check_budget(deadline):
    if deadline is not None and time.monotonic() >= deadline - 10:
        raise TimeoutError('Ingestion time budget reached; committed records are retained for the next run')


def run_ingestion(input_path=None, output=None, enrich=True, enrichment_limit=None,
                  force=False, delay=1.0, conn=None, run=None, owns_lock=False,
                  source_keys=None, input_paths=None, write_files=True, deadline=None):
    output = Path(output or db.ROOT / "data")
    run = run or new_run()
    lock_acquired = owns_lock
    token = FETCH_DEADLINE.set(deadline)
    cache, raw_records, enrichment_queue = {}, [], []
    try:
        conn = conn or db.connect()
        db.initialize(conn)
        if not lock_acquired:
            lock_acquired = conn.execute("SELECT pg_try_advisory_lock(%s) AS acquired", (SYNC_LOCK,)).fetchone()["acquired"]
            if not lock_acquired:
                run.update(status='busy', stage='finished')
                return run
            recover_interrupted(conn)
        if write_files:
            (output / 'snapshots').mkdir(parents=True, exist_ok=True)
        inputs = dict(input_paths or {})
        keys = source_keys
        if input_path:
            if source_keys and len(source_keys) != 1:
                raise ValueError("--input requires exactly one --source")
            keys = source_keys or [CANADIAN.key]
            inputs[keys[0]] = input_path
        selected = db.configured_sources(conn, keys)
        existing_ids = {row['id'] for row in conn.execute('SELECT id FROM canonical_job_identities')}
        db.record_batch(conn, run)
        source_failures = 0
        for configured, source in selected:
            report = {"key": source.key, "name": source.name, "source": source.url,
                      "source_id": configured['id'], "readme_url": source.readme_url,
                      "incremental_version": 1, "parser_version": source.parser_version,
                      "status": "running", **dict.fromkeys(COUNTERS, 0)}
            before = {key: run[key] for key in COUNTERS}
            report['id'] = str(db.start_source_run(conn, configured['id'], run['id']))
            run['sources'].append(report)
            run.update(stage='fetching', current_source=source.name)
            db.record_batch(conn, run)
            try:
                _check_budget(deadline)
                if source.key in inputs:
                    raw = Path(inputs[source.key]).read_text(encoding='utf-8')
                else:
                    raw, report['resolved_url'] = fetch_text(source.readme_url)
                content_hash = hashlib.sha256(raw.encode('utf-8')).hexdigest()
                previous = db.applied_source_snapshot(conn, configured['id'])
                snapshot = db.save_snapshot(conn, configured['id'], raw)
                report.update(snapshot_id=snapshot['id'], content_hash=content_hash)
                db.record_source_run(conn, report['id'], report, snapshot['id'])
                if write_files:
                    (output / 'snapshots' / (content_hash + '.md')).write_text(raw, encoding='utf-8')
                if (previous and previous['content_hash'] == content_hash and not force
                        and previous['report'].get('parser_version', 1) == source.parser_version):
                    report.update(source_unchanged=True, status='success', scraped=previous['report']['scraped'],
                                  unchanged=previous['report'].get('unique_postings', previous['report']['scraped']),
                                  unique_postings=previous['report'].get('unique_postings', previous['report']['scraped']))
                    report['skipped_duplicates'] = report['scraped']
                    run['scraped'] += report['scraped']
                    run['unchanged'] += report['unchanged']
                    run['skipped_duplicates'] += report['skipped_duplicates']
                    run['unchanged_sources'] += 1
                    db.touch_source_postings(conn, configured['id'], snapshot['id'])
                else:
                    records = source.parse(raw)
                    if not records:
                        raise ValueError('Empty source parse; missing postings will not be closed')
                    report['scraped'] = len(records)
                    run['scraped'] += len(records)
                    old_jobs = db.source_observations(conn, configured['id'])
                    seen, unchanged, normalized = set(), [], []
                    # Validate every row before deciding whether disappearance means closure.
                    for record in records:
                        try:
                            job = normalize(record, source.url, str(snapshot['id']))
                            job.source_id = configured['id']
                            normalized.append(job)
                        except Exception as exc:
                            run['failed'] += 1
                            run['errors'].append({'source': source.key, 'error': type(exc).__name__})
                    run['parsed'] += len(normalized)
                    for job in normalized:
                        _check_budget(deadline)
                        run.update(stage='persisting', current_job=job.company + ' — ' + job.title)
                        if job.external_id in seen:
                            run['skipped_duplicates'] += 1
                            run['processed'] += 1
                            continue
                        seen.add(job.external_id)
                        old = old_jobs.get(job.external_id)
                        # A previous confirmed dead link stays dead until a material change or forced check.
                        if old and old.status == 'dead' and job.status == 'open' and not force and not _metadata_changed(job, old.model_copy(update={'status': 'open'})):
                            job.status = 'dead'
                        if not force and not _metadata_changed(job, old):
                            unchanged.append(job)
                            run['unchanged'] += 1
                            run['skipped_duplicates'] += 1
                        else:
                            try:
                                outcome = db.save_job(conn, job)
                                run[outcome] += 1
                                if job.status == 'closed':
                                    run['closed'] += 1
                                    run['enrichment_skipped'] += 1
                                else:
                                    enrichment_queue.append((job, old, report))
                            except Exception as exc:
                                run['failed'] += 1
                                run['errors'].append({'source': source.key, 'source_line': job.source_line, 'error': type(exc).__name__})
                        run['processed'] += 1
                        if run['processed'] % 25 == 0:
                            db.record_batch(conn, run)
                    db.touch_source_postings(conn, configured['id'], snapshot['id'], unchanged)
                    for key in COUNTERS:
                        if key != 'scraped':
                            report[key] += run[key] - before[key]
                    report['unique_postings'] = len(seen)
                    if not report['failed']:
                        _check_budget(deadline)
                        removed = db.close_missing_postings(conn, configured['id'], seen)
                        report['removed'] = removed
                        report['closed'] += removed
                        run['removed'] += removed
                        run['closed'] += removed
                    report['status'] = 'partial_failure' if report['failed'] else 'success'
                    if report['failed']:
                        report['error'] = 'Some records failed; missing postings were not closed'
                    if write_files:
                        raw_records.extend({'source': source.url, **record.model_dump()} for record in records)
                        write_json(output / 'source_records.json', raw_records)
            except Exception as exc:
                source_failures += 1
                report.update(status='failed', error=type(exc).__name__ + ': ' + str(exc))
                run['errors'].append({'source': source.key, 'error': type(exc).__name__})
                run['failed'] += 1
                report['failed'] += 1
                logger.error('Source %s failed (%s); existing committed data retained', source.key, type(exc).__name__)
            for key in COUNTERS:
                if key != 'scraped':
                    report[key] = run[key] - before[key]
            db.record_source_run(conn, report['id'], report, report.get('snapshot_id'))
            db.record_batch(conn, run)
        # Every source's metadata/lifecycle commits before optional application-page work.
        run['enrichment_deferred'] = 0
        run['stage'] = 'enriching'
        for index, (job, old, report) in enumerate(enrichment_queue):
            if deadline is not None and time.monotonic() >= deadline - 15:
                run['enrichment_deferred'] += len(enrichment_queue) - index
                break
            before = {key: run[key] for key in COUNTERS}
            try:
                relevant = ('company', 'title', 'location', 'url', 'term', 'status')
                needs_description = old is None or force or any(getattr(job, key) != getattr(old, key) for key in relevant)
                process_description(job, old, run, cache, enrich and needs_description, enrichment_limit,
                                    force or (old is not None and needs_description), delay)
                if enrich and needs_description and job.enrichment_status == 'not_attempted':
                    run['enrichment_deferred'] += 1
                if job.enrichment_status not in {'not_requested', 'not_attempted'} or job.status == 'dead':
                    db.save_job(conn, job)
            except Exception as exc:
                run['enrichment_failed'] += 1
                logger.warning('Description persistence failed (%s); metadata remains committed', type(exc).__name__)
            for key in COUNTERS:
                report[key] += run[key] - before[key]
        for report in run['sources']:
            db.record_source_run(conn, report['id'], report, report.get('snapshot_id'))
        run['new_canonical_job_ids'] = sorted(row['id'] for row in conn.execute(
            'SELECT c.id FROM canonical_jobs c JOIN canonical_job_identities i ON i.id=c.id WHERE i.merged_into_id IS NULL')
            if row['id'] not in existing_ids)
        run['reconciliation'] = db.reconciliation_stats(conn)
        run['source_failures'] = source_failures
        run['status'] = 'failed' if selected and source_failures == len(selected) else 'partial_failure' if source_failures or run['failed'] else 'success'
    except Exception as exc:
        run.update(status='failed', error=type(exc).__name__ + ': ' + str(exc))
        logger.error('Ingestion failed (%s)', type(exc).__name__)
    finally:
        FETCH_DEADLINE.reset(token)
        run.update(finished_at=datetime.now(timezone.utc).isoformat(), stage='finished')
        run.pop('current_job', None)
        run.pop('current_source', None)
        if conn is not None:
            try:
                if lock_acquired:
                    db.record_batch(conn, run)
            except Exception as exc:
                run.update(status='failed', report_persistence_error=type(exc).__name__)
            finally:
                conn.close()
        if write_files and lock_acquired:
            try:
                output.mkdir(parents=True, exist_ok=True)
                write_json(output / 'run.json', run)
                (output / 'runs').mkdir(exist_ok=True)
                write_json(output / 'runs' / (run['id'] + '.json'), run)
            except OSError:
                logger.warning('Optional diagnostic files could not be written; PostgreSQL retains the run')
    return run
