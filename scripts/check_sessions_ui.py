"""Verify two independent browser identities, workflow state, and resume ownership on isolated PostgreSQL data."""
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.request import urlopen

import psycopg
from dotenv import load_dotenv
from psycopg import sql
from psycopg.conninfo import make_conninfo
from playwright.sync_api import expect, sync_playwright

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'tests'))
from internshipfeed import db
from internshipfeed.ingestion import run_ingestion
from pdf_fixtures import make_pdf

load_dotenv(ROOT/'.env',override=False)
base_dsn=os.environ.get('TEST_DATABASE_URL') or os.environ.get('DATABASE_URL')
if not base_dsn:raise SystemExit('Set TEST_DATABASE_URL to a development PostgreSQL database')
schema='sessions_ui_'+uuid.uuid4().hex
server=None
with db.connect(base_dsn) as admin:
    admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
dsn=make_conninfo(base_dsn,options='-csearch_path='+schema)
try:
    run=run_ingestion(conn=db.connect(dsn),input_path=ROOT/'tests/fixtures/listings.md',enrich=False,write_files=False)
    assert run['status']=='success'
    with db.connect(dsn) as conn:
        job_id=next(job.id for job in db.list_jobs(conn) if job.status=='open')
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    base='http://127.0.0.1:'+str(port)
    server=subprocess.Popen([sys.executable,'-m','uvicorn','internshipfeed.api:app','--host','127.0.0.1','--port',str(port)],
                            cwd=ROOT,env=dict(os.environ,DATABASE_URL=dsn,VERCEL='0'),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            with urlopen(base+'/api/health',timeout=1):break
        except OSError:
            if server.poll() is not None:raise RuntimeError('Test API exited during startup')
            time.sleep(.1)
    with sync_playwright() as p:
        browser=p.chromium.launch()
        contexts=[browser.new_context(viewport={'width':1280,'height':1000}) for _ in range(2)]
        pages=[context.new_page() for context in contexts]
        errors=[]
        for page in pages:page.on('pageerror',lambda error:errors.append(str(error)))
        def profile(page,role,skill,location):
            page.goto(base+'/listings')
            page.get_by_role('button',name='Profile',exact=True).click()
            dialog=page.get_by_role('dialog',name='Profile',exact=True)
            for label,value in [('Target roles',role),('Skills',skill),('Preferred locations',location)]:
                control=dialog.get_by_label(label,exact=True);control.fill(value);control.press('Enter')
            with page.expect_response(lambda r:r.url.endswith('/api/profile') and r.request.method=='PUT' and r.status==200):
                dialog.get_by_role('button',name='Save changes',exact=True).click()
            dialog.get_by_role('button',name='Close profile',exact=True).click()
        a,b=pages
        profile(a,'Backend','Python','Toronto')
        profile(b,'Frontend','React','Vancouver')
        card=lambda page:page.locator('[data-job-id="%s"]'%job_id)
        card(a).get_by_role('button',name='Save job',exact=True).click()
        expect(card(a).get_by_role('button',name='Unsave job',exact=True)).to_be_visible()
        card(b).get_by_role('button',name='Dismiss job',exact=True).click()
        expect(card(b)).to_have_count(0)
        for page in pages:page.reload()
        assert contexts[0].request.get(base+'/api/profile').json()['skills']==['Python']
        assert contexts[1].request.get(base+'/api/profile').json()['skills']==['React']
        assert [row['job']['id'] for row in contexts[0].request.get(base+'/api/saved').json()]==[job_id]
        assert contexts[1].request.get(base+'/api/saved').json()==[]
        assert contexts[0].request.get(base+'/api/dismissed').json()==[]
        assert [row['job']['id'] for row in contexts[1].request.get(base+'/api/dismissed').json()]==[job_id]
        for page,tab in [(a,'Saved'),(b,'Dismissed')]:
            page.get_by_role('navigation').get_by_role('link',name=tab,exact=True).click()
            expect(card(page)).to_be_visible()
        assert contexts[0].request.post(base+f'/api/jobs/{job_id}/interactions',data={'type':'applied'}).status==201
        assert contexts[0].request.patch(base+f'/api/jobs/{job_id}/application',data={'status':'interview'}).status==200
        assert contexts[1].request.get(base+'/api/applied').json()==[]
        upload=contexts[0].request.post(base+'/api/resumes',multipart={'file':{'name':'fixture.pdf','mimeType':'application/pdf','buffer':make_pdf(['Python backend sample resume'])}})
        assert upload.status==201
        resume_id=upload.json()['id']
        assert contexts[1].request.get(base+'/api/resumes/latest').json() is None
        assert contexts[1].request.get(base+f'/api/resumes/{resume_id}/skill-suggestions').status==404
        assert contexts[0].request.get(base+f'/api/resumes/{resume_id}/skill-suggestions').status==200
        with db.connect(dsn) as conn:
            assert conn.execute('SELECT count(*) AS n FROM users').fetchone()['n']==3,'Parallel first-page requests must create one identity per browser'
        assert not errors,errors
        output=ROOT/'data/screenshots';output.mkdir(exist_ok=True)
        a.screenshot(path=str(output/'anonymous-a-saved.png'))
        b.screenshot(path=str(output/'anonymous-b-dismissed.png'))
        for context in contexts:context.close()
        browser.close()
        print('PASS: independent A/B profiles, save/dismiss, refresh persistence, application/resume ownership, and one identity per browser')
finally:
    if server:
        server.terminate()
        try:server.wait(timeout=10)
        except subprocess.TimeoutExpired:server.kill();server.wait()
    with db.connect(base_dsn) as admin:
        admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
