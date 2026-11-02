"""Profile browser checks: restores preferences, appends one generated sample PDF."""
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
        page.route('**/api/profile', lambda route: route.fulfill(status=503, json={'detail': 'internal diagnostics'}))
        page.goto(base + '/profile')
        expect(page.get_by_text('Could not load profile.', exact=True)).to_be_visible()
        expect(page.get_by_text('internal diagnostics', exact=True)).to_have_count(0)
        page.unroute('**/api/profile')
        page.get_by_role('button', name='Try again', exact=True).click()
        expect(page.get_by_label('Target roles', exact=True)).to_be_visible()
        for field in editable[:-1]:
            control = page.locator('.profile-field').filter(has=page.locator('#' + field))
            expect(control.locator('.tag > span')).to_have_text(initial[field])
        expect(page.get_by_label('Graduation year', exact=False)).to_have_value(str(initial['graduation_year'] or ''))

        while page.locator('.tag button').count():
            page.locator('.tag button').first.click()
        additions = {
            'Target roles': ['Software Engineering', 'Backend Engineering'],
            'Skills': ['Python', 'SQL'], 'Preferred locations': ['Toronto', 'Ottawa'],
            'Preferred terms': ['Summer 2027'],
        }
        for label, values in additions.items():
            for value in values:
                page.get_by_label(label, exact=True).fill(value)
                page.get_by_label(label, exact=True).press('Enter')
        skill_tags = page.locator('.profile-field').filter(has=page.locator('#skills')).locator('.tag')
        page.get_by_label('Skills', exact=True).fill(' Python ')
        page.get_by_label('Skills', exact=True).press('Enter')
        expect(skill_tags).to_have_count(2)
        page.get_by_role('button', name='Remove SQL from skills', exact=True).click()
        expect(skill_tags).to_have_count(1)
        page.get_by_label('Skills', exact=True).fill('SQL')
        page.get_by_label('Skills', exact=True).press('Enter')
        page.get_by_label('Skills', exact=True).fill('Docker')  # Save includes pending text.
        page.get_by_label('Graduation year', exact=False).fill('2027')
        expect(page.get_by_text('Unsaved changes', exact=True)).to_be_visible()
        with page.expect_response(lambda response: response.url.endswith('/api/profile') and response.request.method == 'PUT') as saved:
            page.get_by_role('button', name='Save profile', exact=True).click()
        assert saved.value.status == 200
        stored = saved.value.json()
        assert stored['target_roles'] == additions['Target roles']
        assert stored['skills'] == ['Python', 'SQL', 'Docker']
        assert stored['preferred_locations'] == additions['Preferred locations']
        assert stored['preferred_terms'] == additions['Preferred terms']
        assert stored['graduation_year'] == 2027
        assert set(writes[-1].post_data_json) == set(editable)
        expect(page.locator('.save-message')).to_contain_text('Saved')
        page.reload()
        expect(page.get_by_label('Target roles', exact=True)).to_be_visible()
        expect(page.get_by_role('button', name='Remove Docker from skills', exact=True)).to_be_visible()
        expect(page.get_by_label('Graduation year', exact=False)).to_have_value('2027')
        assert page.request.get(base + '/api/profile').json() == stored

        page.get_by_label('Graduation year', exact=False).fill('3000')
        before_invalid = len(writes)
        page.get_by_role('button', name='Save profile', exact=True).click()
        expect(page.locator('#year-error')).to_be_visible()
        expect(page.get_by_label('Graduation year', exact=False)).to_have_attribute('aria-invalid', 'true')
        assert len(writes) == before_invalid
        page.get_by_label('Graduation year', exact=False).fill('2027')

        pdf = {'name': 'sample-resume.pdf', 'mimeType': 'application/pdf',
               'buffer': make_pdf(['Sample Candidate\nEducation\nComputer Science\nProjects\nPython and SQL'])}
        assert page.locator('#resume-file').get_attribute('accept') == '.pdf,application/pdf'
        before_upload = page.request.get(base + '/api/profile').json()
        with page.expect_response(lambda response: response.url.endswith('/api/resumes')) as uploaded:
            page.locator('#resume-file').set_input_files(pdf)
        assert uploaded.value.status == 201
        metadata = uploaded.value.json()
        expect(page.get_by_text('Uploaded successfully', exact=True)).to_be_visible()
        expect(page.locator('.filename')).to_have_text('sample-resume.pdf')
        assert page.request.get(base + '/api/profile').json() == before_upload
        page.get_by_role('heading', name='Profile', exact=True).click()
        page.evaluate('document.fonts.ready')
        screenshots = Path('data/screenshots')
        screenshots.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(screenshots / 'profile-desktop.png'), full_page=True)
        page.set_viewport_size({'width': 390, 'height': 844})
        page.evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        page.screenshot(path=str(screenshots / 'profile-mobile.png'), full_page=True)
        page.set_viewport_size({'width': 1280, 'height': 1050})

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
        page.route('**/api/resumes', lambda route: route.fulfill(status=201, json=metadata))
        page.locator('#resume-file').set_input_files(pdf)
        expect(page.get_by_text('Uploaded successfully', exact=True)).to_be_visible()
        page.unroute('**/api/resumes')

        page.route('**/api/profile', lambda route: route.fulfill(status=503, json={'detail': 'internal diagnostics'}))
        page.get_by_label('Skills', exact=True).fill('Java')
        page.get_by_role('button', name='Save profile', exact=True).click()
        expect(page.get_by_text('Could not save changes. Please try again.', exact=True)).to_be_visible()
        expect(page.get_by_label('Skills', exact=True)).to_have_value('Java')
        page.unroute('**/api/profile')
        page.get_by_role('button', name='Save profile', exact=True).click()
        expect(page.locator('.save-message')).to_contain_text('Saved')
        page.get_by_role('link', name='Feed', exact=True).click()
        page.wait_for_function("document.querySelector('#summary')?.textContent.includes(' of ')")
        assert page.locator('#jobs tr').count() > 0
        page.get_by_role('link', name='Profile', exact=True).click()
        expect(page.get_by_label('Target roles', exact=True)).to_be_visible()
        assert not errors, errors
        print('Profile browser checks passed: load/retry, all tag fields, duplicates/removal, pending input, save/refresh, validation, real PDF upload, invalid files, failure/retry states, mobile layout, and feed navigation.')
    finally:
        restored = page.request.put(base + '/api/profile', data={key: initial[key] for key in editable})
        assert restored.status == 200, 'Original profile preferences could not be restored'
        print('Original profile preferences restored. One generated sample résumé remains in upload history.')
        browser.close()
