import assert from 'node:assert/strict';
import test from 'node:test';
import { addTag, buildProfilePayload, emptyPending, graduationYearError, removeTag, toDraft, validatePdf } from './form';
import type { CandidateProfile } from '../types/profile';

const profile: CandidateProfile = {
  id: 1, target_roles: ['Software Engineering'], skills: ['Python'],
  preferred_locations: ['Toronto'], preferred_terms: ['Summer 2027'], graduation_year: 2027,
  created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
};

test('tag addition trims whitespace and preserves order', () => {
  assert.deepEqual(addTag(['Python'], ' SQL '), ['Python', 'SQL']);
});
test('blank tags and exact duplicates are ignored, with spelling preserved', () => {
  assert.deepEqual(addTag(['Python'], ' Python '), ['Python']);
  assert.deepEqual(addTag(['Python'], '   '), ['Python']);
  assert.deepEqual(addTag(['Python'], 'python'), ['Python', 'python']);
});
test('tag removal is by position without mutating the original', () => {
  const values = ['Python', 'SQL'];
  assert.deepEqual(removeTag(values, 0), ['SQL']);
  assert.deepEqual(values, ['Python', 'SQL']);
});
test('payload includes all five editable fields and excludes stored identity/timestamps', () => {
  const payload = buildProfilePayload(toDraft(profile), emptyPending());
  assert.deepEqual(payload, {
    target_roles: ['Software Engineering'], skills: ['Python'], preferred_locations: ['Toronto'],
    preferred_terms: ['Summer 2027'], graduation_year: 2027,
  });
});
test('saving includes pending input even without Enter and suppresses duplicates', () => {
  const pending = { target_roles: 'Backend Engineering', skills: ' Python ', preferred_locations: 'Ottawa', preferred_terms: '' };
  assert.deepEqual(buildProfilePayload(toDraft(profile), pending), {
    target_roles: ['Software Engineering', 'Backend Engineering'], skills: ['Python'],
    preferred_locations: ['Toronto', 'Ottawa'], preferred_terms: ['Summer 2027'], graduation_year: 2027,
  });
});
test('empty arrays and nullable graduation year can intentionally clear preferences', () => {
  const draft = { target_roles: [], skills: [], preferred_locations: [], preferred_terms: [], graduation_year: '' };
  assert.deepEqual(buildProfilePayload(draft, emptyPending()), { ...draft, graduation_year: null });
});
test('graduation year supports the existing range and rejects invalid values', () => {
  for (const year of ['', '1900', '2027', '2200']) assert.equal(graduationYearError(year), undefined);
  for (const year of ['1899', '2201', '2027.5', 'abc']) assert.ok(graduationYearError(year));
  assert.throws(() => buildProfilePayload({ ...toDraft(profile), graduation_year: '3000' }, emptyPending()));
});
test('PDF extension is case insensitive, with empty or generic MIME allowed', () => {
  for (const name of ['resume.pdf', 'RESUME.PDF', 'resume.Pdf']) {
    for (const type of ['', 'application/pdf', 'application/octet-stream']) assert.equal(validatePdf({ name, type, size: 100 }), undefined);
  }
});
test('non-PDF filenames, incompatible MIME, and oversized files are rejected', () => {
  assert.equal(validatePdf({ name: 'resume.docx', type: 'application/pdf', size: 100 }), 'Only PDF files are supported.');
  assert.equal(validatePdf({ name: 'resume.pdf', type: 'image/png', size: 100 }), 'Only PDF files are supported.');
  assert.equal(validatePdf({ name: 'resume.pdf', type: 'application/pdf', size: 5 * 1024 * 1024 }), undefined);
  assert.ok(validatePdf({ name: 'resume.pdf', type: 'application/pdf', size: 5 * 1024 * 1024 + 1 }));
});
