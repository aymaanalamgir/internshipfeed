"""Verify anonymous session persistence, ownership isolation, CSRF boundaries, and legacy data preservation."""
import os
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

from internshipfeed import db
from internshipfeed.api import app
from internshipfeed.models import JobListing
from internshipfeed.sessions import COOKIE_NAME, COOKIE_MAX_AGE, user_mutation
from pdf_fixtures import make_pdf

PROFILE_A={'target_roles':['Backend'],'skills':['Python'],'preferred_locations':['Toronto'],'preferred_terms':[],'graduation_year':None}
PROFILE_B={'target_roles':['Frontend'],'skills':['React'],'preferred_locations':['Vancouver'],'preferred_terms':[],'graduation_year':None}


class UserOriginTests(unittest.TestCase):
    def request(self, origin='https://feed.example', forwarded=False, site='same-origin'):
        headers=[(b'host',b'127.0.0.1:9000' if forwarded else b'feed.example'),(b'origin',origin.encode()),(b'sec-fetch-site',site.encode())]
        if forwarded:headers += [(b'x-forwarded-host',b'feed.example'),(b'x-forwarded-proto',b'https')]
        return Request({'type':'http','method':'POST','scheme':'https','path':'/api/profile','root_path':'',
                        'query_string':b'','server':('feed.example',443),'client':('203.0.113.5',1234),'headers':headers})

    def test_hosted_user_writes_allow_same_origin_and_forwarded_public_host(self):
        with patch.dict(os.environ,{'VERCEL':'1','VERCEL_ENV':'production'}):
            user_mutation(self.request())
            user_mutation(self.request(forwarded=True))
            for origin in ['null','https://evil.example','https://feed.example.evil.test','http://feed.example','https://user@feed.example']:
                with self.assertRaises(HTTPException):user_mutation(self.request(origin,forwarded=True))
            with self.assertRaises(HTTPException):user_mutation(self.request(site='cross-site'))
        with patch.dict(os.environ,{'VERCEL':'0'}),self.assertRaises(HTTPException):
            user_mutation(self.request(forwarded=True))


@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'),'Requires disposable PostgreSQL')
class AnonymousSessionTests(unittest.TestCase):
    def setUp(self):
        self.schema='sessions_'+uuid.uuid4().hex
        with psycopg.connect(os.environ['TEST_DATABASE_URL'],autocommit=True) as conn:
            conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        dsn=make_conninfo(os.environ['TEST_DATABASE_URL'],options='-csearch_path='+self.schema)
        original=db.connect
        self.connect=lambda:original(dsn)
        self.conn=self.connect();db.initialize(self.conn)
        connection=patch('internshipfeed.api.db.connect',side_effect=self.connect)
        connection.start();self.addCleanup(connection.stop)
        source=self.conn.execute("SELECT * FROM job_sources WHERE key='canadian-tech'").fetchone()
        snapshot=db.save_snapshot(self.conn,source['id'],'session isolation fixture')
        listing=JobListing(company='Acme',title='Software Intern',location='Toronto',status='open',
            source=source['repository'],source_id=source['id'],snapshot_id=str(snapshot['id']),source_line=1,
            external_id='session-fixture',url='https://example.com/jobs/session-fixture')
        db.save_job(self.conn,listing)
        self.job=db.list_jobs(self.conn)[0].id
        self.a=TestClient(app,base_url='https://feed.example')
        self.b=TestClient(app,base_url='https://feed.example')
        self.addCleanup(self.a.close);self.addCleanup(self.b.close)

    def tearDown(self):
        self.conn.close()
        with psycopg.connect(os.environ['TEST_DATABASE_URL'],autocommit=True) as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))

    def test_new_session_sets_secure_opaque_cookie_and_reuses_blank_profile(self):
        with patch.dict(os.environ,{'VERCEL':'1','VERCEL_ENV':'production'}):
            response=self.a.get('/api/session')
        self.assertEqual(response.json(),{'anonymous':True,'created':True})
        cookie=response.headers['set-cookie']
        for attribute in ['HttpOnly','Secure','SameSite=lax','Path=/','Max-Age='+str(COOKIE_MAX_AGE)]:self.assertIn(attribute,cookie)
        self.assertNotIn('Domain=',cookie)
        identity=self.a.cookies.get(COOKIE_NAME)
        self.assertEqual(uuid.UUID(identity).version,4)
        first=self.a.get('/api/profile').json()
        for field in PROFILE_A:
            self.assertEqual(first[field],[] if field!='graduation_year' else None)
        self.assertNotIn('user_id',first)
        self.assertNotEqual(first['id'],db.development_profile(self.conn)['id'])
        self.assertEqual(self.a.get('/api/profile').json()['id'],first['id'])
        self.assertFalse(self.a.get('/api/session').json()['created'])
        self.assertIn('no-store',response.headers['cache-control'])
        self.assertIn('Cookie',response.headers['vary'])

    def test_two_users_keep_profiles_and_notifications_separate(self):
        a=self.a.get('/api/profile').json();b=self.b.get('/api/profile').json()
        self.assertNotEqual(a['id'],b['id'])
        self.assertEqual(self.a.put('/api/profile',json=PROFILE_A).status_code,200)
        self.assertEqual(self.b.put('/api/profile',json=PROFILE_B).status_code,200)
        self.assertEqual(self.a.get('/api/profile').json()['skills'],['Python'])
        self.assertEqual(self.b.get('/api/profile').json()['skills'],['React'])
        db.record_job_notifications(self.conn,a['id'],[self.job],'test-receipt')
        self.assertEqual(db.unnotified_job_ids(self.conn,b['id'],[self.job]),[self.job])
        self.assertEqual(db.unnotified_job_ids(self.conn,a['id'],[self.job]),[])

    def test_save_dismiss_and_discovery_membership_are_candidate_scoped(self):
        self.assertEqual(self.a.post(f'/api/jobs/{self.job}/interactions',json={'type':'save'}).status_code,201)
        self.assertEqual(self.b.post(f'/api/jobs/{self.job}/interactions',json={'type':'dismiss'}).status_code,201)
        self.assertEqual([row['job']['id'] for row in self.a.get('/api/saved').json()],[self.job])
        self.assertEqual(self.b.get('/api/saved').json(),[])
        self.assertEqual(self.a.get('/api/dismissed').json(),[])
        self.assertEqual([row['job']['id'] for row in self.b.get('/api/dismissed').json()],[self.job])
        self.assertTrue(self.a.get('/api/feed').json())
        self.assertEqual(self.b.get('/api/feed').json(),[])
        self.assertEqual(self.a.get(f'/api/jobs/{self.job}/details').json(),self.b.get(f'/api/jobs/{self.job}/details').json())
        cookies=self.a.cookies
        with TestClient(app,base_url='https://feed.example',cookies=cookies) as revisited:
            self.assertEqual(len(revisited.get('/api/saved').json()),1)

    def test_application_progress_cannot_change_another_candidates_tracker(self):
        self.a.post(f'/api/jobs/{self.job}/interactions',json={'type':'applied'})
        self.assertEqual(self.a.patch(f'/api/jobs/{self.job}/application',json={'status':'interview'}).status_code,200)
        self.assertEqual(self.b.get('/api/applied').json(),[])
        self.assertEqual(self.b.patch(f'/api/jobs/{self.job}/application',json={'status':'rejected'}).status_code,404)
        self.assertEqual(self.a.get('/api/applied').json()[0]['application']['status'],'interview')

    def test_resume_latest_and_suggestions_verify_ownership(self):
        response=self.a.post('/api/resumes',files={'file':('resume.pdf',make_pdf(['Python Django backend development']),'application/pdf')})
        self.assertEqual(response.status_code,201)
        resume_id=response.json()['id']
        self.assertIsNone(self.b.get('/api/resumes/latest').json())
        blocked=self.b.get(f'/api/resumes/{resume_id}/skill-suggestions')
        self.assertEqual(blocked.status_code,404)
        self.assertIn('no-store',blocked.headers['cache-control'])
        self.assertEqual(self.a.get(f'/api/resumes/{resume_id}/skill-suggestions').status_code,200)
        self.assertEqual(self.a.get('/api/resumes/latest').json()['id'],resume_id)

    def test_payload_and_query_ids_cannot_select_another_user(self):
        a=self.a.get('/api/profile').json();b=self.b.get('/api/profile').json()
        for extra in ['user_id','candidate_profile_id','id']:
            self.assertEqual(self.a.put('/api/profile',json={**PROFILE_A,extra:b['id']}).status_code,422)
            self.assertEqual(self.a.post(f'/api/jobs/{self.job}/interactions',json={'type':'save',extra:b['id']}).status_code,422)
        result=self.a.get('/api/profile',params={'candidate_profile_id':b['id'],'user_id':self.b.cookies.get(COOKIE_NAME)}).json()
        self.assertEqual(result['id'],a['id'])
        self.assertEqual(self.b.get('/api/profile').json()['skills'],[])

    def test_invalid_unknown_or_non_v4_cookie_gets_new_server_generated_identity(self):
        for cookie in ['invalid',str(uuid.uuid4()),str(uuid.uuid1())]:
            with TestClient(app,base_url='https://feed.example',cookies={COOKIE_NAME:cookie}) as client:
                response=client.get('/api/session')
                self.assertEqual(response.status_code,200)
                self.assertTrue(response.json()['created'])
                value=response.headers['set-cookie'].split('=',1)[1].split(';',1)[0]
                self.assertNotEqual(value,cookie)
                self.assertEqual(uuid.UUID(value).version,4)

    def test_production_writes_are_allowed_but_ingestion_remains_protected(self):
        with patch.dict(os.environ,{'VERCEL':'1','VERCEL_ENV':'production','CRON_SECRET':'test-admin-secret'}):
            self.assertEqual(self.a.put('/api/profile',json=PROFILE_A,headers={'Origin':'https://feed.example'}).status_code,200)
            self.assertEqual(self.a.post(f'/api/jobs/{self.job}/interactions',json={'type':'save'},headers={'Origin':'https://feed.example'}).status_code,201)
            self.assertEqual(self.a.put('/api/profile',json=PROFILE_B,headers={'Origin':'https://evil.example'}).status_code,403)
            with patch('internshipfeed.api.start_sync') as sync:
                self.assertEqual(self.a.post('/api/sync').status_code,403)
                self.assertEqual(self.a.get('/api/cron/ingest').status_code,401)
                sync.assert_not_called()
        self.assertEqual(self.a.get('/api/profile').json()['skills'],['Python'])

    def test_last_seen_is_throttled_and_global_catalog_creates_no_identity(self):
        before=self.conn.execute('SELECT count(*) AS n FROM users').fetchone()['n']
        self.a.get('/api/sources');self.a.get(f'/api/jobs/{self.job}/details')
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM users').fetchone()['n'],before)
        self.a.get('/api/profile')
        identity=self.a.cookies.get(COOKIE_NAME)
        self.conn.execute("UPDATE users SET last_seen_at=now()-interval '1 day' WHERE id=%s",(identity,))
        self.a.get('/api/profile')
        stamp=self.conn.execute('SELECT last_seen_at FROM users WHERE id=%s',(identity,)).fetchone()['last_seen_at']
        self.a.get('/api/profile')
        self.assertEqual(self.conn.execute('SELECT last_seen_at FROM users WHERE id=%s',(identity,)).fetchone()['last_seen_at'],stamp)

    def test_recommendations_use_only_current_profile_and_behavior(self):
        self.a.put('/api/profile',json=PROFILE_A);self.b.put('/api/profile',json=PROFILE_B)
        a=self.a.get('/api/recommendations').json();b=self.b.get('/api/recommendations').json()
        self.assertGreater(a[0]['final_score'],b[0]['final_score'])
        self.a.post(f'/api/jobs/{self.job}/interactions',json={'type':'save'})
        self.assertEqual(self.a.get('/api/recommendations').json(),[])
        self.assertEqual(len(self.b.get('/api/recommendations').json()),1)

    def test_legacy_profiles_migrate_once_without_changing_history_or_timestamps(self):
        legacy=db.development_profile(self.conn)
        db.record_interaction(self.conn,legacy['id'],self.job,'applied')
        db.save_resume_text(self.conn,legacy['id'],'legacy.pdf','Preserved legacy resume')
        db.record_job_notifications(self.conn,legacy['id'],[self.job],'legacy-receipt')
        self.conn.execute('ALTER TABLE candidate_profiles DROP COLUMN user_id')
        self.conn.execute('DROP TABLE users')
        self.conn.execute("DELETE FROM schema_migrations WHERE version='015_anonymous_users.sql'")
        db.initialize(self.conn);db.initialize(self.conn)
        self.assertEqual(db.development_profile(self.conn),legacy)
        for table in ['job_interactions','job_applications','resumes','job_notifications']:
            self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM '+table+' WHERE candidate_profile_id=%s',(legacy['id'],)).fetchone()['n'],1)
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM users').fetchone()['n'],1)
        self.assertEqual(self.a.get('/api/profile').json()['skills'],[])
        self.assertEqual(self.a.get('/api/applied').json(),[])
