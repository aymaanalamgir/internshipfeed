"""Exercise bounded catalog reads, filters, ranking, and candidate isolation on PostgreSQL."""
import os
import unittest
import uuid
from datetime import date

from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo

from internshipfeed import api, db
from internshipfeed.behavioral import build_behavior_profile
from internshipfeed.models import CandidateProfile, JobKeywords, JobListing
from internshipfeed.pagination import encode_cursor
from internshipfeed.recommendations import rank_recommendations


@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'), 'Run scripts/test_with_postgres.py')
class PaginationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = 'pagination_' + uuid.uuid4().hex
        with db.connect(os.environ['TEST_DATABASE_URL']) as conn:
            conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(cls.schema)))
        cls.dsn = make_conninfo(os.environ['TEST_DATABASE_URL'], options='-csearch_path=' + cls.schema)
        with db.connect(cls.dsn) as conn:
            db.initialize(conn)
            cls.user, cls.profile = db.create_anonymous_candidate(conn)
            cls.other_user, cls.other_profile = db.create_anonymous_candidate(conn)
            source = conn.execute("SELECT id,repository FROM job_sources WHERE key='canadian-tech'").fetchone()
            cls.source = source
            snapshot = db.save_snapshot(conn, source['id'], 'pagination fixtures')
            cls.snapshot = snapshot
            for index in range(123):
                db.save_job(conn, JobListing(company='Acme' if index % 2 else 'Other', title='Backend Intern ' + str(index),
                    location='Toronto' if index % 2 else 'Vancouver', url='https://example.test/jobs/' + str(index),
                    source=source['repository'], source_id=source['id'], external_id='page-' + str(index),
                    snapshot_id=str(snapshot['id']), source_line=index + 1, status='unknown' if index % 3 == 0 else 'open',
                    posted_at=date(2026, 11, index % 3 + 1) if index < 90 else None,
                    term='Summer 2027' if index % 2 else 'Fall 2027', description='Full description. ' * 100,
                    keywords=JobKeywords(languages=['Python'], tools_and_platforms=['PostgreSQL'], requirements=['Detailed requirement. ' * 100])))

    @classmethod
    def tearDownClass(cls):
        with db.connect(os.environ['TEST_DATABASE_URL']) as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(cls.schema)))

    def setUp(self):
        self.conn = db.connect(self.dsn)
        self.rollback = self.conn.transaction(force_rollback=True)
        self.rollback.__enter__()
        api.app.dependency_overrides[api.connection] = lambda: self.conn
        self.client = TestClient(api.app)
        self.client.cookies.set('internshipfeed_session', str(self.user))

    def tearDown(self):
        self.client.close()
        api.app.dependency_overrides.clear()
        self.rollback.__exit__(None, None, None)
        self.conn.close()

    def page(self, **params):
        response = self.client.get('/api/feed', params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_default_custom_maximum_and_rejected_limits(self):
        for limit in (None, 1, 7, 100):
            page = self.page(**({} if limit is None else {'limit': limit}))
            self.assertEqual(page['limit'], limit or 50)
            self.assertEqual(len(page['items']), limit or 50)
            self.assertEqual(page['total'], 123)
            self.assertTrue(page['has_more'])
            self.assertIsNotNone(page['next_cursor'])
        for limit in (0, -1, 101, 10000, 'bad'):
            self.assertEqual(self.client.get('/api/feed', params={'limit': limit}).status_code, 422)

    def test_all_pages_are_stable_with_tied_and_null_dates(self):
        expected = [row['id'] for row in self.conn.execute('SELECT id FROM canonical_jobs ORDER BY posted_at DESC NULLS LAST,id DESC')]
        actual, cursor, sizes = [], None, []
        while True:
            page = self.page(limit=20, **({'cursor': cursor} if cursor else {}))
            sizes.append(len(page['items']))
            actual.extend(item['job']['id'] for item in page['items'])
            self.assertEqual(page['total'], 123)
            if not page['has_more']:
                self.assertIsNone(page['next_cursor'])
                break
            cursor = page['next_cursor']
        self.assertEqual(actual, expected)
        self.assertEqual(sizes, [20] * 6 + [3])
        self.assertEqual(len(set(actual)), 123)
        self.assertIsNone(page['items'][-1]['job']['posted_at'])

    def test_empty_results_and_cursor_beyond_end(self):
        for params in ({'q': 'no such company'}, {'cursor': encode_cursor(None, 1)}):
            page = self.page(**params)
            self.assertEqual(page['items'], [])
            self.assertFalse(page['has_more'])
            self.assertIsNone(page['next_cursor'])

    def test_invalid_cursors_are_422(self):
        for cursor in ('bad', 'e30', 'W251bGwsZmFsc2Vd', 'WyIyMDI2LTk5LTk5IiwxXQ', 'x' * 257):
            self.assertEqual(self.client.get('/api/feed', params={'cursor': cursor}).status_code, 422)

    def test_filters_search_totals_and_pagination_compose(self):
        first = self.page(q='  PYTHON  ', source_id=self.source['id'], location='Toronto', term='Summer 2027', status='open', limit=4)
        self.assertEqual(first['total'], 41)
        self.assertEqual(len(first['items']), 4)
        second = self.page(q='python', source=self.source['id'], location='Toronto', term='Summer 2027', status='open', limit=4, cursor=first['next_cursor'])
        self.assertEqual(second['total'], 41)
        self.assertFalse({item['job']['id'] for item in first['items']} & {item['job']['id'] for item in second['items']})
        for item in first['items'] + second['items']:
            self.assertEqual(item['job']['location'], 'Toronto')
            self.assertEqual(item['term'], 'Summer 2027')
            self.assertEqual(item['job']['status'], 'open')
        self.assertEqual(self.page(source_id=9999)['total'], 0)
        self.assertEqual(self.page(q='Acme')['total'], 61)
        self.assertEqual(self.page(q='Backend Intern')['total'], 123)
        self.assertEqual(self.page(q='Vancouver')['total'], 62)

    def test_search_treats_percent_and_underscore_as_literal_text(self):
        job_id = self.page()['items'][0]['job']['id']
        self.conn.execute('UPDATE canonical_jobs SET company=%s WHERE id=%s', ('100%_company', job_id))
        self.assertEqual(self.page(q='%_')['total'], 1)
        self.assertEqual(self.page(q="' OR true --")['total'], 0)

    def test_summary_excludes_details_and_limits_skill_tags(self):
        page = self.page(limit=1)
        item = page['items'][0]
        self.assertEqual(set(item), {'job', 'term', 'skills', 'interaction_state'})
        self.assertEqual(set(item['job']), set(db.CORE_FIELDS))
        self.assertEqual(item['skills'], ['Python', 'PostgreSQL'])
        details = self.client.get('/api/jobs/%s/details' % item['job']['id']).json()
        self.assertIn('Full description.', details['description'])
        self.assertTrue(details['sources'])
        self.assertTrue(details['keywords']['requirements'])
        self.assertLess(len(self.client.get('/api/feed?limit=50').content), 50000)
        from psycopg.types.json import Jsonb
        self.conn.execute("UPDATE canonical_jobs SET keywords=jsonb_set(keywords,'{languages}',%s) WHERE id=%s",
                          (Jsonb(['Python', 'Python', 'Java', 'Rust', 'C', 'C++', 'Go', 'Swift']), item['job']['id']))
        self.assertEqual(self.page(limit=1)['items'][0]['skills'], ['Python', 'Java', 'Rust', 'C', 'C++', 'Go'])

    def test_closed_source_filter_does_not_hide_another_active_source(self):
        item = self.page(limit=1)['items'][0]
        source = self.conn.execute("SELECT id,repository FROM job_sources WHERE key='simplify'").fetchone()
        snapshot = db.save_snapshot(self.conn, source['id'], 'closed source fixture')
        db.save_job(self.conn, JobListing(company=item['job']['company'], title='Archived source title', location=item['job']['location'],
            url=item['job']['application_url'], source=source['repository'], source_id=source['id'], external_id='closed-alias',
            snapshot_id=str(snapshot['id']), source_line=1, status='closed'))
        self.assertEqual(self.page()['total'], 123)
        self.assertEqual(self.page(source_id=source['id'])['total'], 0)
        self.assertEqual(self.page(q='Archived source title')['total'], 1)

    def test_jobs_endpoint_is_bounded_and_preserves_core_contract(self):
        page = self.client.get('/api/jobs?limit=3').json()
        self.assertEqual(len(page['items']), 3)
        self.assertEqual(set(page['items'][0]), set(db.CORE_FIELDS))
        self.assertEqual(page['next_cursor'], self.page(limit=3)['next_cursor'])

    def test_filter_options_cover_the_catalog_not_only_the_page(self):
        options = self.client.get('/api/feed/filters').json()
        self.assertEqual(options, {'locations': ['Toronto', 'Vancouver'], 'terms': ['Fall 2027', 'Summer 2027']})

    def test_candidate_membership_state_and_counts_never_leak(self):
        ids = [item['job']['id'] for item in self.page(limit=4)['items']]
        for job_id, action in zip(ids, ('save', 'applied', 'dismiss', 'click')):
            self.client.post('/api/jobs/%s/interactions' % job_id, json={'type': action})
        own = self.page(limit=100)
        self.assertEqual(own['total'], 121)
        states = {item['job']['id']: item['interaction_state'] for item in own['items']}
        self.assertTrue(states[ids[0]]['saved'])
        self.assertNotIn(ids[1], states)
        self.assertNotIn(ids[2], states)
        other = TestClient(api.app)
        self.addCleanup(other.close)
        other.cookies.set('internshipfeed_session', str(self.other_user))
        page = other.get('/api/feed?limit=100').json()
        self.assertEqual(page['total'], 123)
        self.assertFalse(any(item['interaction_state']['saved'] or item['interaction_state']['applied'] or item['interaction_state']['dismissed'] for item in page['items']))
        for path in ('saved', 'applied', 'dismissed'):
            self.assertEqual(other.get('/api/' + path).json(), [])

    def test_recommendations_limit_is_applied_after_complete_ranking(self):
        profile = CandidateProfile.model_validate(self.profile)
        details = [item for item, _ in db.candidate_job_details(self.conn, profile.id)]
        expected = rank_recommendations(profile, details, build_behavior_profile(db.behavior_history(self.conn, profile.id)))
        first = self.client.get('/api/recommendations').json()
        second = self.client.get('/api/recommendations?limit=30&offset=30').json()
        self.assertEqual(len(first), 30)
        self.assertEqual([item['job']['id'] for item in first + second], [item.job.id for item in expected[:60]])
        self.assertEqual([item['final_score'] for item in first], [item.final_score for item in expected[:30]])
        self.assertEqual(len(self.client.get('/api/recommendations?limit=100').json()), 100)
        self.assertEqual(len(self.client.get('/api/recommendations?offset=100').json()), 23)
        self.assertEqual(self.client.get('/api/recommendations?offset=9999').json(), [])
        self.assertEqual(self.client.get('/api/recommendations?limit=101').status_code, 422)

    def test_history_collections_have_bounded_offset_pages(self):
        ids = [item['job']['id'] for item in self.page(limit=8)['items']]
        for job_id in ids:
            for action in ('save', 'applied', 'dismiss'):
                self.client.post('/api/jobs/%s/interactions' % job_id, json={'type': action})
        for collection in ('applied', 'dismissed'):
            first = self.client.get('/api/' + collection + '?limit=3').json()
            second = self.client.get('/api/' + collection + '?limit=3&offset=3').json()
            self.assertEqual(len(first), 3)
            self.assertEqual(len(second), 3)
            self.assertFalse({item['job']['id'] for item in first} & {item['job']['id'] for item in second})
        self.assertEqual(self.client.get('/api/saved').json(), [])
        for job_id in ids:
            self.client.post('/api/jobs/%s/interactions' % job_id, json={'type': 'undismiss'})
        first = self.client.get('/api/saved?limit=3').json()
        second = self.client.get('/api/saved?limit=3&offset=3').json()
        self.assertEqual(len(first), 3)
        self.assertEqual(len(second), 3)
        self.assertFalse({item['job']['id'] for item in first} & {item['job']['id'] for item in second})

    def test_directory_query_count_does_not_grow_with_page_size(self):
        class Counted:
            def __init__(self, conn):
                self.conn, self.calls = conn, 0
            def execute(self, *args, **kwargs):
                self.calls += 1
                return self.conn.execute(*args, **kwargs)
        for limit in (1, 50, 100):
            counted = Counted(self.conn)
            self.assertEqual(len(db.list_job_summaries(counted, self.profile['id'], limit=limit).items), limit)
            self.assertEqual(counted.calls, 2)

    def test_new_jobs_do_not_shift_the_cursor_boundary(self):
        first = self.page(limit=4)
        expected = self.page(limit=4, cursor=first['next_cursor'])
        db.save_job(self.conn, JobListing(company='New arrival', title='Backend Intern', location='Toronto',
            url='https://example.test/jobs/new-arrival', source=self.source['repository'], source_id=self.source['id'],
            snapshot_id=str(self.snapshot['id']), external_id='new-arrival', source_line=124, status='open', posted_at=date(2027, 1, 1)))
        self.assertEqual(self.page()['total'], 124)
        self.assertEqual(self.page(limit=4, cursor=first['next_cursor'])['items'], expected['items'])
