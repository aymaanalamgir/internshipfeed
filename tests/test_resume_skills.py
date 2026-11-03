"""Verify technical resume skill aliases, phrase boundaries, deduplication, and ordering."""
import unittest

from internshipfeed.enrich import extract_keywords
from internshipfeed.matching import normalize_skill
from internshipfeed.resume_skills import extract_resume_skills, skill_aliases


class ResumeSkillTests(unittest.TestCase):
    def test_canonical_labels_and_aliases(self):
        self.assertEqual(extract_resume_skills('Python FastAPI Postgres Docker K8s NodeJS Google Cloud JavaScript C++'),
                         ['C++', 'Docker', 'FastAPI', 'GCP', 'JavaScript', 'Kubernetes', 'Node.js', 'PostgreSQL', 'Python'])
        self.assertEqual(extract_resume_skills('Amazon Web Services, JS, TS, dotnet'), ['.NET', 'AWS', 'JavaScript', 'TypeScript'])

    def test_collisions_and_longest_phrases(self):
        for text, expected in [('JavaScript', ['JavaScript']), ('C++', ['C++']), ('C#', ['C#']),
                               ('React Native', ['React Native']), ('React / React Native', ['React', 'React Native']),
                               ('Java, JavaScript; C C++ C#', ['C', 'C#', 'C++', 'Java', 'JavaScript'])]:
            self.assertEqual(extract_resume_skills(text), expected)
        self.assertEqual(extract_keywords('React Native')['frameworks'], ['React Native'])

    def test_duplicates_case_separators_and_order(self):
        self.assertEqual(extract_resume_skills('python Python PYTHON Postgres PostgreSQL'), ['PostgreSQL', 'Python'])
        self.assertEqual(extract_resume_skills('machine-learning and Google\nCloud, CI/CD'), ['CI/CD', 'GCP', 'machine learning'])
        self.assertEqual(extract_resume_skills('Docker Python'), extract_resume_skills('Python Docker'))

    def test_no_generic_prose_or_embedded_names(self):
        self.assertEqual(extract_resume_skills('leadership teamwork communication development software project Microsoft agile '
                                              'JavaScripting cplusplus GitHub R&D R/S go home aspiring springboard'), [])
        self.assertEqual(extract_resume_skills(''), [])

    def test_aliases_are_shared_with_profile_matching(self):
        for alias, canonical in skill_aliases().items():
            self.assertEqual(normalize_skill(alias), canonical)
