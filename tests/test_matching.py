"""Verify explainable role, skill, location, and term scoring rules."""
import unittest
from datetime import date, datetime, timezone

from internshipfeed.matching import (
    PROVINCES, normalize_skill, normalize_text, rank_jobs, score_job,
    score_location, score_role, score_skills, score_term,
)
from internshipfeed.models import CandidateProfile, CanonicalJob, JobDetails, JobKeywords, SourceJobReference

NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def profile(**changes):
    return CandidateProfile(**dict({'id': 1, 'target_roles': [], 'skills': [],
                                   'preferred_locations': [], 'preferred_terms': [], 'graduation_year': None,
                                   'created_at': NOW, 'updated_at': NOW}, **changes))


def details(title='Backend Developer Intern', location='Toronto, ON', term=None,
            keywords=None, sources=(), **changes):
    job = CanonicalJob(**dict({'id': 1, 'company': 'Acme', 'title': title, 'location': location,
                              'application_url': 'https://example.com/jobs/1', 'posted_at': None, 'status': 'open'}, **changes))
    return JobDetails(job=job, term=term, description=None, keywords=keywords or JobKeywords(), sources=list(sources))


def observation(title='Backend Software Engineering Intern', location='Toronto, Ontario', term=None, status='open'):
    return SourceJobReference(id=2, source='https://github.com/example/source', source_id=2, external_id='875',
                              match_method='exact_application_url', match_confidence=1, matched_url=None,
                              company='Acme', title=title, location=location, url='https://example.com/jobs/1',
                              last_known_url=None, posted_at=None, term=term, status=status, snapshot_id='1',
                              source_line=1, first_seen_at=NOW, last_seen_at=NOW)


class NormalizationTests(unittest.TestCase):
    def test_separators_accents_case_and_whitespace(self):
        self.assertEqual(normalize_text('  Back–end / Engineer, (Montréal)  '), 'back end engineer montreal')

    def test_technical_identifiers_survive_normalization(self):
        self.assertEqual(normalize_text('C++ / C# / .NET / Node.js'), 'c++ c# .net node.js')
        self.assertNotEqual(normalize_skill('C'), normalize_skill('C++'))
        self.assertNotEqual(normalize_skill('Java'), normalize_skill('JavaScript'))


class RoleTests(unittest.TestCase):
    def test_explicit_role_families(self):
        cases = [('Backend Engineering', 'Backend Software Engineer Intern'),
                 ('Software Engineering', 'Software Developer Intern'),
                 ('Data Engineering', 'Data Platform Co-op'),
                 ('Machine Learning', 'ML Engineer Intern'),
                 ('Data Science', 'Data Scientist Intern'),
                 ('Product Management', 'Technical Product Manager Intern'),
                 ('Frontend Engineering', 'Front-End Developer Intern'),
                 ('DevOps', 'Site Reliability Engineering Intern')]
        for target, title in cases:
            with self.subTest(target=target):
                score, reason = score_role([target], [title])
                self.assertEqual(score, 40)
                self.assertIn(target, reason)
                self.assertIn(title, reason)

    def test_normalized_exact_phrase_is_full_match(self):
        self.assertEqual(score_role(['Quality Assurance'], ['Quality Assurance (Intern)'])[0], 40)

    def test_partial_and_weak_token_rules_are_explicit(self):
        self.assertEqual(score_role(['Security Engineering'], ['Security Engineer Intern'])[0], 30)
        self.assertEqual(score_role(['Security Operations'], ['Security Engineering Intern'])[0], 15)

    def test_unrelated_and_generic_engineering_overlap_do_not_match(self):
        for target, title in [('Data Engineering', 'Product Management Intern'),
                              ('Backend Engineering', 'Civil Engineer Intern'),
                              ('Java Developer', 'JavaScript Developer Intern')]:
            with self.subTest(target=target, title=title):
                self.assertEqual(score_role([target], [title]), (0, None))

    def test_alternate_active_source_title_supplies_role_evidence(self):
        result = score_job(profile(target_roles=['Backend Engineering']),
                           details(title='Software Developer Intern', sources=[observation()]))
        self.assertEqual(result.role_score, 40)
        self.assertIn('Backend Software Engineering Intern', result.reasons[0])

    def test_inactive_alternate_source_is_not_matching_evidence(self):
        for status in ('closed', 'dead'):
            result = score_job(profile(target_roles=['Backend Engineering'], preferred_locations=['Toronto'],
                                       preferred_terms=['Summer 2027']),
                               details(title='Product Management Intern', location='Vancouver, BC',
                                       sources=[observation(term='Summer 2027', status=status)]))
            self.assertEqual(result.score, 0)
            self.assertEqual(result.reasons, [])


class SkillTests(unittest.TestCase):
    def test_job_focused_overlap_and_sql_postgresql_distinction(self):
        score, matched, reason = score_skills(['Python', 'SQL', 'Docker'],
                                            JobKeywords(languages=['Python'], tools_and_platforms=['PostgreSQL', 'Docker', 'AWS']))
        self.assertEqual(score, 15)
        self.assertEqual(matched, ['Docker', 'Python'])
        self.assertIn('2 of 4', reason)

    def test_two_of_three_is_twenty_points(self):
        self.assertEqual(score_skills(['Python', 'SQL', 'Java'], JobKeywords(languages=['Python', 'SQL'], tools_and_platforms=['Docker']))[0], 20)

    def test_aliases_return_readable_labels_and_do_not_duplicate_skills(self):
        score, matched, _ = score_skills(['Postgres', 'JS', 'TS', 'Node', 'k8s', 'Google Cloud', 'Amazon Web Services', 'dotnet'],
                                        JobKeywords(languages=['JavaScript', 'TypeScript'], frameworks=['.NET'],
                                                    tools_and_platforms=['PostgreSQL', 'postgres', 'Node.js', 'Kubernetes', 'GCP', 'AWS']))
        self.assertEqual(score, 30)
        self.assertEqual(matched, ['.NET', 'AWS', 'GCP', 'JavaScript', 'Kubernetes', 'Node.js', 'PostgreSQL', 'TypeScript'])

    def test_no_substring_matching_for_technical_identifiers(self):
        self.assertEqual(score_skills(['C', 'Java', 'SQL'], JobKeywords(languages=['C++', 'C#', 'JavaScript'], tools_and_platforms=['PostgreSQL']))[:2], (0, []))

    def test_duplicate_categories_and_long_profiles_do_not_reduce_score(self):
        keywords = JobKeywords(languages=['Python', 'python'], frameworks=['Python'])
        self.assertEqual(score_skills(['Python'] + ['Unrelated %s' % n for n in range(100)], keywords)[:2], (30, ['Python']))

    def test_requirements_and_nontechnical_concept_prose_are_not_skills(self):
        keywords = JobKeywords(requirements=['Python'], concepts=['agile', 'Experience developing APIs', 'distributed systems'])
        self.assertEqual(score_skills(['Python'], keywords)[:2], (0, []))
        self.assertEqual(score_skills(['Distributed Systems'], keywords)[:2], (30, ['distributed systems']))

    def test_rounding_is_nearest_integer_with_halves_up(self):
        self.assertEqual(score_skills(['Python'], JobKeywords(languages=['Python', 'Java', 'C', 'Go']))[0], 8)

    def test_missing_job_or_candidate_skills_produce_zero(self):
        self.assertEqual(score_skills(['Python'], JobKeywords()), (0, [], None))
        self.assertEqual(score_skills([], JobKeywords(languages=['Python'])), (0, [], None))


class LocationTests(unittest.TestCase):
    def test_city_and_province_aliases(self):
        for location in ('Toronto, ON', 'Toronto, Ontario', 'Toronto, ON, Canada'):
            self.assertEqual(score_location(['Toronto'], [location])[0], 20)
            self.assertEqual(score_location(['Toronto, Ontario'], [location])[0], 20)

    def test_all_explicit_province_aliases(self):
        for code, name in PROVINCES.items():
            with self.subTest(province=code):
                self.assertEqual(score_location([name], ['Example City, ' + code.upper()])[0], 15)
                self.assertEqual(score_location([code], [name])[0], 20)
        self.assertEqual(score_location(['Québec'], ['Montréal, QC'])[0], 15)

    def test_remote_canada_requires_geographic_evidence(self):
        for preference in ('Remote Canada', 'Canada Remote'):
            for location in ('Remote, Canada', 'Canada - Remote', 'Remote, Ontario'):
                self.assertEqual(score_location([preference], [location])[0], 20)
            for location in ('Remote', 'Remote, USA', 'Remote, CA', 'Toronto, ON'):
                self.assertEqual(score_location([preference], [location]), (0, None))

    def test_generic_remote_accepts_explicit_remote_anywhere(self):
        self.assertEqual(score_location(['Remote'], ['Remote, USA'])[0], 20)
        self.assertEqual(score_location(['Remote'], ['Toronto, ON'])[0], 0)

    def test_province_and_country_partial_scores(self):
        self.assertEqual(score_location(['Toronto, Ontario'], ['Ottawa, ON'])[0], 15)
        self.assertEqual(score_location(['Canada'], ['Toronto, ON'])[0], 8)

    def test_different_city_or_explicit_region_conflict(self):
        for preference, location in [('Ottawa', 'Vancouver, BC'),
                                     ('Toronto, Ontario', 'Toronto, British Columbia'),
                                     ('Toronto, Canada', 'Toronto, USA')]:
            self.assertEqual(score_location([preference], [location])[0], 0)
        self.assertEqual(score_location(['Toronto'], ['Torontoville, ON'])[0], 0)

    def test_multiple_locations_and_active_source_locations(self):
        self.assertEqual(score_location(['Ottawa'], ['Toronto, ON; Ottawa, ON'])[0], 20)
        result = score_job(profile(preferred_locations=['Toronto']),
                           details(location='Unknown', sources=[observation()]))
        self.assertEqual(result.location_score, 20)
        self.assertIn('Toronto, Ontario', result.reasons[0])

    def test_missing_generic_location_and_empty_preferences(self):
        self.assertEqual(score_location(['Toronto'], ['Unknown']), (0, None))
        self.assertEqual(score_location([], ['Toronto, ON']), (0, None))
        self.assertEqual(score_location(['Unknown'], ['Unknown']), (0, None))


class TermTests(unittest.TestCase):
    def test_exact_and_autumn_alias(self):
        self.assertEqual(score_term(['Summer 2027'], ['Summer 2027'])[0], 10)
        self.assertEqual(score_term(['Fall 2027'], ['Autumn 2027'])[0], 10)
        self.assertEqual(score_term(['Summer 2027'], ['2027 Summer Internship'])[0], 10)

    def test_missing_year_or_season_uses_partial_score(self):
        self.assertEqual(score_term(['Summer 2027'], ['Summer'])[0], 6)
        self.assertEqual(score_term(['Summer'], ['Summer 2027'])[0], 6)
        self.assertEqual(score_term(['Summer 2027'], ['2027'])[0], 4)
        self.assertEqual(score_term(['2027'], ['Summer 2027'])[0], 4)

    def test_conflicting_season_or_year_does_not_match(self):
        self.assertEqual(score_term(['Summer 2027'], ['Winter 2027']), (0, None))
        self.assertEqual(score_term(['Summer 2027'], ['Summer 2028']), (0, None))

    def test_season_and_year_from_separate_terms_are_not_combined(self):
        self.assertEqual(score_term(['Summer 2027'], ['Winter 2027; Summer 2028']), (0, None))

    def test_title_fallback_and_alternate_source_terms(self):
        candidate = profile(preferred_terms=['Summer 2027'])
        self.assertEqual(score_job(candidate, details(title='Backend Intern (Summer 2027)')).term_score, 10)
        self.assertEqual(score_job(candidate, details(term='Winter 2027', sources=[observation(term='Summer 2027')])).term_score, 10)
        self.assertEqual(score_job(candidate, details(title='Backend Intern (Summer 2027)', term='Winter 2027')).term_score, 0)

    def test_no_term_inferred_from_posted_at(self):
        self.assertEqual(score_job(profile(preferred_terms=['Fall 2027']), details(posted_at=date(2027, 10, 1))).term_score, 0)
        self.assertEqual(score_term([], ['Summer 2027']), (0, None))


class RankingTests(unittest.TestCase):
    def test_known_pair_exact_components_total_and_reasons(self):
        candidate = profile(target_roles=['Backend Engineering'], skills=['Python', 'SQL'],
                            preferred_locations=['Toronto'], preferred_terms=['Summer 2027'])
        result = score_job(candidate, details(term='Summer 2027',
                                             keywords=JobKeywords(languages=['Python', 'SQL'], tools_and_platforms=['Docker'])))
        self.assertEqual((result.role_score, result.skills_score, result.location_score, result.term_score), (40, 20, 20, 10))
        self.assertEqual(result.score, 90)
        self.assertEqual(result.matched_skills, ['Python', 'SQL'])
        self.assertEqual(len(result.reasons), 4)
        self.assertEqual([reason.split()[0] for reason in result.reasons], ['Role', 'Matched', 'Preferred', 'Preferred'])

    def test_full_match_reaches_one_hundred(self):
        result = score_job(profile(target_roles=['Backend Engineering'], skills=['Python'],
                                   preferred_locations=['Toronto'], preferred_terms=['Summer 2027']),
                           details(term='Summer 2027', keywords=JobKeywords(languages=['Python'])))
        self.assertEqual(result.score, 100)

    def test_empty_profile_and_missing_data_return_zero_without_reasons(self):
        result = score_job(profile(), details(location='Unknown'))
        self.assertEqual((result.score, result.role_score, result.skills_score, result.location_score, result.term_score), (0, 0, 0, 0, 0))
        self.assertEqual(result.reasons, [])
        self.assertEqual(result.matched_skills, [])

    def test_ties_use_date_then_id_with_missing_dates_last(self):
        jobs = [details(id=5), details(id=2, posted_at=date(2026, 10, 2)),
                details(id=3, posted_at=date(2026, 10, 2)), details(id=4, posted_at=date(2026, 10, 1)), details(id=6)]
        self.assertEqual([result.job.id for result in rank_jobs(profile(), jobs)], [3, 2, 4, 6, 5])

    def test_closed_dead_excluded_unknown_visible_and_min_score_applies(self):
        jobs = [details(id=1, status='closed'), details(id=2, status='dead'), details(id=3, status='unknown')]
        self.assertEqual([result.job.id for result in rank_jobs(profile(), jobs)], [3])
        self.assertEqual(rank_jobs(profile(), jobs, min_score=1), [])

    def test_scoring_does_not_modify_input_models(self):
        candidate = profile(target_roles=['Backend Engineering'], skills=['Python'])
        job = details(keywords=JobKeywords(languages=['Python']))
        before = candidate.model_dump(), job.model_dump()
        self.assertEqual(score_job(candidate, job), score_job(candidate, job))
        self.assertEqual((candidate.model_dump(), job.model_dump()), before)
