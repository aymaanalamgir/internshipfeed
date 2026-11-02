"""Profile browser checks: restores preferences, uploads generated sample PDFs."""
import argparse
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tests'))
from pdf_fixtures import make_pdf

parser = argparse.ArgumentParser()
parser.add_argument('--url', default='http://127.0.0.1:8000')
parser.add_argument('--browser-executable')
args = parser.parse_args()
base = args.url.rstrip('/')
editable = ('target_roles', 'skills', 'preferred_locations', 'preferred_terms', 'graduation_year')

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(executable_path=args.browser_executable, headless=True)
    page = browser.new_page(viewport={'width': 1280, 'height': 1050})
    errors = []
    writes = []
    uploads = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('request', lambda request: writes.append(request) if request.method == 'PUT' and request.url.endswith('/api/profile') else None)
    page.on('request', lambda request: uploads.append(request) if request.method == 'POST' and request.url.endswith('/api/resumes') else None)
    initial = page.request.get(base + '/api/profile').json()
    try:
        page.goto(base + '/')
        expect(page.get_by_role('heading', name='Recommended', exact=True)).to_be_visible()
        trigger = page.get_by_role('button', name='Profile', exact=True)
        trigger.click()
        expect(page.get_by_role('dialog', name='Profile', exact=True)).to_be_visible()
        expect(page.get_by_label('Target roles', exact=True)).to_be_visible()
        for _ in range(30):
            page.keyboard.press('Tab')
            assert page.evaluate("Boolean(document.activeElement.closest('dialog[open]'))")
        page.keyboard.press('Escape')
        expect(page.get_by_role('dialog')).to_have_count(0)
        expect(trigger).to_be_focused()
        page.route('**/api/profile', lambda route: route.fulfill(status=503, json={'detail': 'internal diagnostics'}))
        page.goto(base + '/profile')
        expect(page.get_by_text('Could not load profile.', exact=True)).to_be_visible()
        expect(page.get_by_text('internal diagnostics', exact=True)).to_have_count(0)
        page.unroute('**/api/profile')
        page.get_by_role('button', name='Try again', exact=True).click()
        expect(page.get_by_label('Target roles', exact=True)).to_be_visible()
        for field in editable[:-1]:
            control = page.locator('.profile-field').filter(has=page.locator('#' + field))
            expect(control.locator('.skill-tag > span')).to_have_text(initial[field])
        expect(page.get_by_label('Graduation year', exact=False)).to_have_value(str(initial['graduation_year'] or ''))

        while page.locator('.tag button').count():
            page.locator('.tag button').first.click()
        additions = {
            'Target roles': ['Software Engineering', 'Backend Engineering'],
            'Skills': ['Python', 'SQL'], 'Preferred locations': ['Toronto', 'Ottawa'],
        }
        for label, values in additions.items():
            for value in values:
                page.get_by_label(label, exact=True).fill(value)
                page.get_by_label(label, exact=True).press('Enter')
        terms = page.get_by_label('Preferred terms', exact=True)
        assert terms.locator('option').all_text_contents() == ['Select term'] + [f'{season} {year}' for year in range(2026, 2031) for season in ('Winter', 'Summer', 'Fall')]
        terms.select_option(label='Summer 2027')
        expect(terms.locator('option', has_text='Summer 2027')).to_be_disabled()
        terms.select_option(label='Fall 2027')
        page.get_by_role('button', name='Remove Fall 2027 from preferred terms', exact=True).click()
        terms.select_option(label='Fall 2027')
        year = page.get_by_label('Graduation year', exact=False)
        assert year.locator('option').all_text_contents() == ['Not specified', '2026', '2027', '2028', '2029', '2030']
        skill_tags = page.locator('.profile-field').filter(has=page.locator('#skills')).locator('.tag')
        page.get_by_label('Skills', exact=True).fill(' Python ')
        page.get_by_label('Skills', exact=True).press('Enter')
        expect(skill_tags).to_have_count(2)
        page.get_by_role('button', name='Remove SQL from skills', exact=True).click()
        expect(skill_tags).to_have_count(1)
        page.get_by_label('Skills', exact=True).fill('SQL')
        page.get_by_label('Skills', exact=True).press('Enter')
        page.get_by_label('Skills', exact=True).fill('Docker')  # Save includes pending text.
        page.get_by_label('Graduation year', exact=False).select_option('2027')
        expect(page.get_by_text('Unsaved changes', exact=True)).to_be_visible()
        with page.expect_response(lambda response: response.url.endswith('/api/profile') and response.request.method == 'PUT') as saved:
            page.get_by_role('button', name='Save changes', exact=True).click()
        assert saved.value.status == 200
        stored = saved.value.json()
        assert stored['target_roles'] == additions['Target roles']
        assert stored['skills'] == ['Python', 'SQL', 'Docker']
        assert stored['preferred_locations'] == additions['Preferred locations']
        assert stored['preferred_terms'] == ['Summer 2027', 'Fall 2027']
        assert stored['graduation_year'] == 2027
        assert set(writes[-1].post_data_json) == set(editable)
        expect(page.locator('.save-message')).to_contain_text('Saved')
        page.reload()
        expect(page.get_by_label('Target roles', exact=True)).to_be_visible()
        expect(page.get_by_role('button', name='Remove Docker from skills', exact=True)).to_be_visible()
        expect(page.get_by_label('Graduation year', exact=False)).to_have_value('2027')
        assert page.request.get(base + '/api/profile').json() == stored

        page.get_by_label('Graduation year', exact=False).select_option('')
        with page.expect_response(lambda response: response.url.endswith('/api/profile') and response.request.method == 'PUT') as cleared:
            page.get_by_role('button', name='Save changes', exact=True).click()
        assert cleared.value.json()['graduation_year'] is None
        page.reload()
        expect(page.get_by_label('Graduation year', exact=False)).to_have_value('')
        page.get_by_label('Graduation year', exact=False).select_option('2027')
        page.get_by_role('button', name='Save changes', exact=True).click()
        expect(page.locator('.save-message')).to_contain_text('Saved')

        pdf = {'name': 'sample-resume.pdf', 'mimeType': 'application/pdf',
               'buffer': make_pdf(['Sample Candidate\nEducation\nComputer Science\nProjects\nPython SQL FastAPI Postgres Docker K8s NodeJS Google Cloud JavaScript C++'])}
        assert page.locator('#resume-file').get_attribute('accept') == '.pdf,application/pdf'
        before_upload = page.request.get(base + '/api/profile').json()
        page.route('**/skill-suggestions', lambda route: route.fulfill(status=503, json={'detail': 'private diagnostics'}))
        with page.expect_response(lambda response: response.url.endswith('/api/resumes')) as uploaded:
            page.locator('#resume-file').set_input_files(pdf)
        assert uploaded.value.status == 201
        metadata = uploaded.value.json()
        expect(page.locator('#upload-status')).to_contain_text('Uploaded ')
        expect(page.locator('.filename')).to_have_text('sample-resume.pdf')
        assert page.request.get(base + '/api/profile').json() == before_upload
        expect(page.get_by_text('Could not load suggestions.', exact=False)).to_be_visible()
        assert page.request.get(base + '/api/resumes/latest').json()['id'] == metadata['id']
        page.unroute('**/skill-suggestions')
        page.get_by_role('region', name='Suggested from résumé').get_by_role('button', name='Try again', exact=True).click()
        expect(page.get_by_role('button', name='Add PostgreSQL to skills', exact=True)).to_be_visible()
        for skill in ('Python', 'SQL', 'Docker'):
            expect(page.get_by_role('button', name=f'Add {skill} to skills', exact=True)).to_have_count(0)
        page.get_by_label('Skills', exact=True).fill('Postgres')
        expect(page.get_by_role('button', name='Add PostgreSQL to skills', exact=True)).to_have_count(0)
        page.get_by_label('Skills', exact=True).fill('')
        before_suggestion = len(writes)
        page.get_by_role('button', name='Add PostgreSQL to skills', exact=True).click()
        expect(page.get_by_role('button', name='Remove PostgreSQL from skills', exact=True)).to_be_visible()
        expect(page.get_by_role('button', name='Add PostgreSQL to skills', exact=True)).to_have_count(0)
        assert len(writes) == before_suggestion
        assert page.request.get(base + '/api/profile').json() == before_upload
        page.get_by_role('button', name='Save changes', exact=True).click()
        expect(page.locator('.save-message')).to_contain_text('Saved')
        assert 'PostgreSQL' in page.request.get(base + '/api/profile').json()['skills']
        page.reload()
        expect(page.get_by_role('button', name='Remove PostgreSQL from skills', exact=True)).to_be_visible()
        expect(page.get_by_role('button', name='Add FastAPI to skills', exact=True)).to_be_visible()
        page.get_by_role('heading', name='Profile', exact=True).click()
        page.evaluate('document.fonts.ready')
        screenshots = Path('data/screenshots')
        screenshots.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(screenshots / 'profile-desktop.png'))
        page.set_viewport_size({'width': 390, 'height': 844})
        page.evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        page.screenshot(path=str(screenshots / 'profile-mobile.png'))
        page.get_by_role('dialog').evaluate('(node) => node.scrollTop = node.scrollHeight')
        expect(page.get_by_role('button', name='Save changes', exact=True)).to_be_visible()
        expect(page.get_by_role('button', name='Close profile', exact=True)).to_be_visible()
        page.screenshot(path=str(screenshots / 'profile-mobile-bottom.png'))
        page.set_viewport_size({'width': 1280, 'height': 1050})
        page.get_by_role('button', name='Close profile', exact=True).click()
        page.get_by_role('button', name='Profile', exact=True).click()
        expect(page.locator('.filename')).to_have_text('sample-resume.pdf')
        expect(page.get_by_role('button', name='Replace PDF', exact=True)).to_be_visible()

        # Latest upload replaces suggestions, preserving selected skills and draft edits.
        page.get_by_label('Skills', exact=True).fill('Redis')
        page.get_by_label('Skills', exact=True).press('Enter')
        replacement = {'name': 'replacement.pdf', 'mimeType': 'application/pdf', 'buffer': make_pdf(['Python Kafka'])}
        page.locator('#resume-file').set_input_files(replacement)
        expect(page.get_by_role('button', name='Add Kafka to skills', exact=True)).to_be_visible()
        expect(page.get_by_role('button', name='Add FastAPI to skills', exact=True)).to_have_count(0)
        expect(page.get_by_role('button', name='Remove PostgreSQL from skills', exact=True)).to_be_visible()
        expect(page.get_by_role('button', name='Remove Redis from skills', exact=True)).to_be_visible()
        assert 'Redis' not in page.request.get(base + '/api/profile').json()['skills']

        before_bad_file = len(uploads)
        page.locator('#resume-file').set_input_files({'name': 'resume.docx', 'mimeType': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'buffer': b'invalid'})
        expect(page.get_by_text('Only PDF files are supported.', exact=True)).to_be_visible()
        assert len(uploads) == before_bad_file
        page.locator('#resume-file').set_input_files({'name': 'fake.pdf', 'mimeType': 'application/pdf', 'buffer': b'not a PDF'})
        expect(page.get_by_text('Could not read this PDF. Try a valid, text-based PDF.', exact=True)).to_be_visible()

        page.route('**/api/resumes', lambda route: route.fulfill(status=503, json={'detail': 'internal diagnostics'}))
        page.locator('#resume-file').set_input_files(pdf)
        expect(page.get_by_text('PDF upload failed. Please try again.', exact=True)).to_be_visible()
        page.unroute('**/api/resumes')
        # A successful retry fixture avoids inserting another sample résumé.
        metadata = page.request.get(base + '/api/resumes/latest').json()
        page.route('**/api/resumes', lambda route: route.fulfill(status=201, json=metadata))
        page.locator('#resume-file').set_input_files(pdf)
        expect(page.locator('#upload-status')).to_contain_text('Uploaded ')
        page.unroute('**/api/resumes')

        page.route('**/api/profile', lambda route: route.fulfill(status=503, json={'detail': 'internal diagnostics'}))
        page.get_by_label('Skills', exact=True).fill('Java')
        page.get_by_role('button', name='Save changes', exact=True).click()
        expect(page.get_by_text('Could not save changes. Please try again.', exact=True)).to_be_visible()
        expect(page.get_by_label('Skills', exact=True)).to_have_value('Java')
        page.unroute('**/api/profile')
        page.get_by_role('button', name='Save changes', exact=True).click()
        expect(page.locator('.save-message')).to_contain_text('Saved')
        page.get_by_role('button', name='Close profile', exact=True).click()
        page.get_by_role('link', name='All listings', exact=True).click()
        page.wait_for_function("document.querySelector('#summary')?.textContent.includes(' of ')")
        assert page.locator('.listing-row').count() > 0
        page.get_by_role('button', name='Profile', exact=True).click()
        expect(page.get_by_label('Target roles', exact=True)).to_be_visible()
        page.keyboard.press('Escape')
        expect(page.get_by_role('heading', name='All listings', exact=True)).to_be_visible()
        assert not errors, errors
        print('Profile browser checks passed: home, drawer open/close, native focus trap/restore, Escape, load/retry, all tags, pending input, save/refresh, bounded year/term selection, real PDF upload, suggestions/retry/draft/save/latest replacement, persistent metadata/replace, invalid files, failure/retry, mobile, and preserved listing context.')
    finally:
        restored = page.request.put(base + '/api/profile', data={key: initial[key] for key in editable})
        assert restored.status == 200, 'Original profile preferences could not be restored'
        print('Original profile preferences restored. Generated sample résumés remain in upload history (use the isolated workflow runner).')
        browser.close()
