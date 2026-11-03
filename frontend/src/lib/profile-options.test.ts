// Verify year/term choices, duplicate prevention, and normalized skill filtering.
import assert from 'node:assert/strict';
import test from 'node:test';
import { GRADUATION_YEARS, INTERNSHIP_TERMS, addTerm, availableSkills, skillKey } from './profile-options';

test('bounded years and all fifteen terms are available', () => {
  assert.deepEqual(GRADUATION_YEARS, [2026, 2027, 2028, 2029, 2030]);
  assert.equal(INTERNSHIP_TERMS.length, 15);
  assert.equal(INTERNSHIP_TERMS[0], 'Winter 2026');
  assert.equal(INTERNSHIP_TERMS[14], 'Fall 2030');
});
test('term addition rejects duplicates and unknown values', () => {
  const terms = ['Summer 2027'];
  assert.deepEqual(addTerm(terms, 'Fall 2027'), ['Summer 2027', 'Fall 2027']);
  assert.deepEqual(addTerm(terms, 'Summer 2027'), terms);
  assert.deepEqual(addTerm(['summer 2027'], 'Summer 2027'), ['summer 2027']);
  assert.deepEqual(addTerm(terms, 'Spring 2027'), terms);
});
test('suggestions filter saved and draft aliases without conflating technologies', () => {
  const aliases = { postgres: 'postgresql', k8s: 'kubernetes', nodejs: 'node.js' };
  assert.deepEqual(availableSkills(['Python', 'PostgreSQL', 'Kubernetes', 'Docker'], ['PYTHON', ' Postgres ', 'k8s'], aliases), ['Docker']);
  assert.equal(skillKey('NodeJS', aliases), 'node.js');
  assert.deepEqual(availableSkills(['C', 'C++', 'C#', 'Java', 'React'], ['C++', 'JavaScript', 'React Native'], aliases), ['C', 'C#', 'Java', 'React']);
});
