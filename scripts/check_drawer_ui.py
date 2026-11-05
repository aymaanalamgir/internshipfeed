"""Verify drawer motion, exit retention, and accessibility with local API fixtures."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument('--url', default='http://127.0.0.1:5173')
parser.add_argument('--screenshots-dir', type=Path)
args = parser.parse_args()
base = args.url.rstrip('/')
if args.screenshots_dir:
    args.screenshots_dir.mkdir(parents=True, exist_ok=True)
profile = {
    'id': 1, 'target_roles': [], 'skills': [], 'preferred_locations': [],
    'preferred_terms': [], 'graduation_year': None,
    'created_at': '2026-11-05T00:00:00Z', 'updated_at': '2026-11-05T00:00:00Z',
}
job = {'id': 1, 'company': 'Drawer fixture', 'title': 'Software Intern', 'location': 'Toronto',
       'application_url': None, 'posted_at': None, 'status': 'open'}
keywords = {'languages': ['Python'], 'frameworks': [], 'tools_and_platforms': [], 'concepts': [], 'requirements': []}
state = {'job_id': 1, 'saved': False, 'applied': False, 'dismissed': False, 'saved_at': None,
         'applied_at': None, 'dismissed_at': None, 'last_interaction_at': None, 'last_event_id': 0}
recommendation = {'job': job, 'score': 0, 'role_score': 0, 'skills_score': 0, 'location_score': 0,
                  'term_score': 0, 'profile_score': 0, 'behavior_score': 50, 'final_score': 13,
                  'matched_skills': [], 'reasons': [], 'behavior_reasons': [],
                  'interaction_state': state, 'term': 'Summer 2027', 'keywords': keywords}
responses = {
    '/api/session': {'anonymous': True, 'created': False}, '/api/profile': profile,
    '/api/sources': [], '/api/recommendations': [recommendation], '/api/resumes/latest': None,
    '/api/jobs/1/details': {'job': job, 'term': 'Summer 2027', 'keywords': keywords,
                            'description': 'A local drawer test fixture.', 'sources': []},
}

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width': 1280, 'height': 1000})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))

    def api(route):
        path = urlsplit(route.request.url).path
        assert route.request.method == 'GET' and path in responses, (route.request.method, path)
        route.fulfill(body=json.dumps(responses[path]), content_type='application/json')

    page.route(base + '/api/**', api)
    page.goto(base + '/')
    trigger = page.get_by_role('button', name='Profile', exact=True)
    expect(trigger).to_be_visible()

    def record():
        page.evaluate('''() => {
            cancelAnimationFrame(window.drawerSampleFrame);
            window.drawerFrames = [];
            const until = performance.now() + 650;
            const sample = () => {
                const dialog = document.querySelector('dialog');
                if (dialog) {
                    const style = getComputedStyle(dialog);
                    window.drawerFrames.push({
                        phase: dialog.dataset.state, open: dialog.open,
                        x: new DOMMatrixReadOnly(style.transform).m41, width: dialog.offsetWidth,
                        backdrop: Number(getComputedStyle(dialog, '::backdrop').opacity),
                        overflow: document.documentElement.scrollWidth > innerWidth
                    });
                }
                if (performance.now() < until) window.drawerSampleFrame = requestAnimationFrame(sample);
            };
            window.drawerSampleFrame = requestAnimationFrame(sample);
        }''')

    def settled():
        page.wait_for_function('''() => {
            const dialog = document.querySelector('dialog[open]');
            return dialog?.dataset.state === 'open' && Math.abs(new DOMMatrixReadOnly(getComputedStyle(dialog).transform).m41) < 0.1;
        }''')

    def finish_close():
        expect(page.get_by_role('dialog')).to_have_count(0)
        frames = page.evaluate('window.drawerFrames')
        closing = [frame for frame in frames if frame['phase'] == 'closing' and frame['width'] > 0]
        assert closing and all(frame['open'] for frame in closing), frames
        assert any(0 < frame['x'] < frame['width'] for frame in closing), closing
        assert any(0 < frame['backdrop'] < 1 for frame in closing), closing
        assert not any(frame['overflow'] for frame in frames), frames
        expect(trigger).to_be_focused()

    record()
    trigger.click()
    dialog = page.get_by_role('dialog', name='Profile', exact=True)
    settled()
    frames = page.evaluate('window.drawerFrames')
    assert any(frame['phase'] == 'opening' and frame['x'] >= frame['width'] - 1 and frame['backdrop'] == 0 for frame in frames), frames
    assert any(0 < frame['x'] < frame['width'] and 0 < frame['backdrop'] < 1 for frame in frames), frames
    assert not any(frame['overflow'] for frame in frames), frames
    assert dialog.evaluate("node => getComputedStyle(node).transitionDuration") == '0.2s'
    assert dialog.evaluate("node => getComputedStyle(node, '::backdrop').transitionDuration") == '0.2s'
    assert dialog.evaluate("node => getComputedStyle(node).transitionTimingFunction") == 'ease-out'
    if args.screenshots_dir:
        page.screenshot(path=str(args.screenshots_dir / 'profile-desktop.png'))
    for _ in range(20):
        page.keyboard.press('Tab')
        assert page.evaluate("Boolean(document.activeElement.closest('dialog[open]'))")
    record()
    dialog.get_by_role('button', name='Close profile', exact=True).click()
    expect(dialog).to_have_attribute('data-state', 'closing')
    finish_close()

    for method in ('escape', 'backdrop'):
        trigger.click()
        settled()
        record()
        if method == 'escape':
            page.keyboard.press('Escape')
        else:
            page.mouse.click(20, 120)
        finish_close()

    for width in (390, 320):
        page.set_viewport_size({'width': width, 'height': 844})
        record()
        trigger.click()
        settled()
        assert dialog.evaluate('node => node.scrollWidth <= node.clientWidth')
        assert not any(frame['overflow'] for frame in page.evaluate('window.drawerFrames'))
        if args.screenshots_dir:
            page.screenshot(path=str(args.screenshots_dir / ('profile-' + str(width) + '.png')))
        record()
        page.keyboard.press('Escape')
        finish_close()

    page.set_viewport_size({'width': 1280, 'height': 1000})
    title = page.get_by_role('button', name='Software Intern', exact=True)
    title.click()
    settled()
    job_dialog = page.get_by_role('dialog', name='Software Intern', exact=True)
    assert job_dialog.evaluate("node => getComputedStyle(node).transitionDuration") == '0.2s'
    record()
    job_dialog.get_by_role('button', name='Close job details', exact=True).click()
    expect(job_dialog).to_have_attribute('data-state', 'closing')
    expect(job_dialog).to_have_count(0)
    assert any(0 < frame['x'] < frame['width'] for frame in page.evaluate('window.drawerFrames') if frame['phase'] == 'closing')
    expect(title).to_be_focused()

    page.emulate_media(reduced_motion='reduce')
    trigger.click()
    settled()
    assert dialog.evaluate("node => getComputedStyle(node).transitionDuration") == '0s'
    assert dialog.evaluate("node => getComputedStyle(node, '::backdrop').transitionDuration") == '0s'
    assert dialog.evaluate("node => node.getAnimations().length") == 0
    page.keyboard.press('Escape')
    expect(dialog).to_have_count(0)
    expect(trigger).to_be_focused()
    assert not errors, errors
    browser.close()

print('Drawer browser checks passed: 200ms ease-out slide and backdrop fade; retained modal during close button/Escape/backdrop exits; focus trapping/restoration; mount without flicker; 320px/390px widths without overflow; shared Job Details motion; reduced-motion bypass. API calls used fixtures only.')
