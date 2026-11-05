"""One-shot external-scheduler command: ingest, rank new jobs, notify once."""
import argparse
import json
import logging
import os
from dataclasses import dataclass
from uuid import UUID

import psycopg
from dotenv import load_dotenv

from . import db
from .behavioral import build_behavior_profile
from .ingestion import run_ingestion
from .models import CandidateProfile
from .notifications import EmailError, EmailConfig, format_email, send_email, validate_channel
from .recommendations import rank_recommendations

logger = logging.getLogger(__name__)
DAILY_REFRESH_LOCK = 78112030


@dataclass(frozen=True)
class DailySettings:
    min_score: int = 60
    top_n: int = 5

    def __post_init__(self):
        if not 0 <= self.min_score <= 100:
            raise ValueError('DAILY_EMAIL_MIN_SCORE must be between 0 and 100')
        if not 1 <= self.top_n <= 5:
            raise ValueError('DAILY_EMAIL_TOP_N must be between 1 and 5')

    @classmethod
    def from_env(cls):
        validate_channel()
        try:
            return cls(int(os.environ.get('DAILY_EMAIL_MIN_SCORE', str(cls.min_score))),
                       int(os.environ.get('DAILY_EMAIL_TOP_N', str(cls.top_n))))
        except ValueError:
            raise ValueError('DAILY_EMAIL_MIN_SCORE must be an integer 0..100 and DAILY_EMAIL_TOP_N an integer 1..5') from None


def run_daily_refresh(settings=None, dry_run=False, batch_id=None, ingestion_options=None, email_config=None):
    result = {'status': 'failed', 'exit_code': 1, 'batch_id': None, 'new_jobs': 0,
              'ingestion_status': 'failed', 'notification_status': 'not_attempted', 'eligible_jobs': 0, 'notified_jobs': 0, 'subject': '', 'html': '', 'message': ''}
    logger.info('Daily refresh started')
    with db.connect() as conn:
        db.initialize(conn)
        if not conn.execute('SELECT pg_try_advisory_lock(%s) AS acquired', (DAILY_REFRESH_LOCK,)).fetchone()['acquired']:
            logger.error('Another daily refresh is running')
            return dict(result, status='busy')
        try:
            run = db.get_batch(conn, batch_id) if batch_id else run_ingestion(**(ingestion_options or {}))
            if run:
                result['ingestion_status'] = run['status']
                result['batch_id'] = str(run['id'])
                for source in run.get('sources', []):
                    logger.info('Source %s: %s (%s fetched, %s failed)', source['key'], source['status'], source.get('scraped', 0), source.get('failed', 0))
            if not run or run.get('status') not in {'success', 'partial_failure'} or not run.get('sources'):
                logger.error('Ingestion batch failed, is incomplete, or has no enabled sources; no email sent')
                return result
            if 'new_canonical_job_ids' not in run:
                logger.error('Batch predates exact new-job tracking; no email sent')
                return result
            partial = run['status'] == 'partial_failure'
            result['status'], result['exit_code'] = ('partial_failure', 1) if partial else ('success', 0)
            result['new_jobs'] = len(run['new_canonical_job_ids'])
            try:
                settings = settings or DailySettings.from_env()
                if not dry_run and email_config is None:
                    email_config = EmailConfig.from_env()
            except ValueError:
                logger.info('Ingestion committed; optional email is not configured')
                result['notification_status'] = 'skipped_unconfigured'
                return result
            candidate = db.development_profile(conn)
            if candidate is None:
                logger.error('Development candidate profile is missing; no email sent')
                return dict(result, notification_status='failed')
            ids = db.unnotified_job_ids(conn, candidate['id'], run['new_canonical_job_ids'])
            entries = db.candidate_job_details(conn, candidate['id'], job_ids=ids)
            behavior = build_behavior_profile(db.behavior_history(conn, candidate['id']))
            ranked = rank_recommendations(CandidateProfile.model_validate(candidate), [details for details, _ in entries], behavior)
            eligible = [match for match in ranked if match.final_score >= settings.min_score]
            result['eligible_jobs'] = len(eligible)
            digest = format_email(eligible, settings.top_n, {details.job.id: details.term for details, _ in entries}, os.environ.get('APP_BASE_URL'))
            result.update(subject=digest.subject, html=digest.html, message=digest.text)
            logger.info('%s new canonical jobs; %s eligible; %s selected for email', result['new_jobs'], len(eligible), len(digest.job_ids))
            if not digest.job_ids:
                result['notification_status'] = 'no_matches'
                logger.info('No new sendable matches; no email sent')
                return result
            if dry_run:
                result['status'] = 'dry_run_partial_failure' if partial else 'dry_run'
                result['notification_status'] = 'dry_run'
                logger.info('Dry run; email not sent and notification history unchanged')
                return result
            try:
                receipt = send_email(digest, email_config)
            except EmailError as error:
                logger.error('email not confirmed: %s', error)
                return dict(result, notification_status='failed')
            logger.info('email accepted by Resend: %s', receipt.message_id)
            try:
                db.record_job_notifications(conn, candidate['id'], list(digest.job_ids), receipt.message_id)
            except psycopg.Error:
                logger.error('email accepted but notification history could not be saved; check Resend before retrying batch %s', run['id'])
                return dict(result, notification_status='record_failed')
            result['notification_status'] = 'success'
            result['notified_jobs'] = len(digest.job_ids)
            return result
        finally:
            try:
                conn.execute('SELECT pg_advisory_unlock(%s)', (DAILY_REFRESH_LOCK,))
            except psycopg.Error:
                pass  # Closing the session releases any remaining advisory lock.


def main(argv=None):
    parser = argparse.ArgumentParser(description='Refresh enabled internship sources and email the best newly discovered matches')
    parser.add_argument('--dry-run', action='store_true', help='Ingest/rank and print the digest without sending or recording notifications')
    parser.add_argument('--no-ingest', action='store_true', help='Retry notification selection for a retained --batch-id without scraping')
    parser.add_argument('--batch-id', type=UUID)
    parser.add_argument('--no-enrich', action='store_true', help='Skip job-page fetches; the normal run enriches automatically')
    parser.add_argument('--enrich-limit', type=int, default=None)
    args = parser.parse_args(argv)
    if args.no_ingest != (args.batch_id is not None):
        parser.error('--no-ingest and --batch-id must be supplied together')
    if args.enrich_limit is not None and args.enrich_limit < 0:
        parser.error('--enrich-limit must be nonnegative')
    load_dotenv(db.ROOT / '.env', override=False)
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    try:
        result = run_daily_refresh(dry_run=args.dry_run, batch_id=args.batch_id,
                                   ingestion_options={'enrich': not args.no_enrich, 'enrichment_limit': args.enrich_limit})
    except Exception as error:
        logger.error('Daily refresh failed (%s); no automatic retry', type(error).__name__)
        return 1
    if args.dry_run and result['message']:
        print('Subject: ' + result['subject'])
        print(result['message'])
        print(result['html'])
    print(json.dumps({key: value for key, value in result.items() if key not in {'message', 'html'}}))
    return result['exit_code']


if __name__ == '__main__':
    raise SystemExit(main())
