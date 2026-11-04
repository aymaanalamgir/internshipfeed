"""Verify bounded interaction affinities and deterministic personalized ranking."""
import unittest
from datetime import date

from internshipfeed.behavioral import (
    AFFINITY_BOUND, DIMENSION_WEIGHTS, INTERACTION_WEIGHTS, BehaviorEvidence,
    BehaviorProfile, build_behavior_profile, job_features, score_behavior,
)
from internshipfeed.matching import rank_jobs, score_job
from internshipfeed.models import JobKeywords, MatchResult
from internshipfeed.recommendations import rank_recommendations
from test_matching import details, observation, profile


def evidence(job=None, *actions):
    return BehaviorEvidence(job or details(), frozenset(actions))


class BehaviorAggregationTests(unittest.TestCase):
    def test_exact_weights_and_dimensions(self):
        self.assertEqual(INTERACTION_WEIGHTS, {'click': 1, 'save': 2, 'applied': 4, 'dismiss': -3})
        self.assertEqual(DIMENSION_WEIGHTS, {'roles': 4, 'skills': 3, 'companies': .5, 'locations': 1, 'terms': .5})

    def test_known_history_produces_expected_affinities(self):
        history = [
            evidence(details(id=1, term='Summer 2027', keywords=JobKeywords(languages=['Python', 'SQL'])), 'save'),
            evidence(details(id=2, title='Data Engineer Intern', term='Summer 2027',
                             keywords=JobKeywords(languages=['Python'], tools_and_platforms=['Kafka'])), 'applied'),
            evidence(details(id=3, title='Frontend Developer Intern', location='Vancouver, BC', term='Winter 2027',
                             keywords=JobKeywords(languages=['JS'], frameworks=['React'])), 'dismiss'),
        ]
        result = build_behavior_profile(history)
        self.assertEqual(result.roles, {'backend engineering': 2, 'data engineering': 4, 'frontend engineering': -3})
        self.assertEqual(result.skills, {'python': 6, 'sql': 2, 'kafka': 4, 'react': -3, 'javascript': -3})
        self.assertEqual(result.companies, {'acme': 3})
        self.assertEqual(result.locations, {'canada ontario toronto': 6, 'british columbia canada vancouver': -3})
        self.assertEqual(result.terms, {'summer 2027': 6, 'winter 2027': -3})

    def test_repeats_capped_per_type_per_job_and_conflicts_preserved(self):
        job = details()
        repeated = [evidence(job, 'save'), evidence(job, 'save', 'click'), evidence(job, 'applied', 'dismiss', 'click', 'view')]
        self.assertEqual(build_behavior_profile(repeated).roles, {'backend engineering': 4})
        self.assertEqual(build_behavior_profile([evidence(job, 'save', 'dismiss')]).roles, {'backend engineering': -1})
        self.assertEqual(build_behavior_profile([evidence(job, 'save', 'applied')]).roles, {'backend engineering': 6})
        self.assertEqual(build_behavior_profile([evidence(job, 'save')] * 3).roles, {'backend engineering': 2})

    def test_view_is_not_a_preference_signal(self):
        self.assertEqual(build_behavior_profile([evidence(details(), 'view')]), BehaviorProfile())

    def test_affinities_clamped_after_all_evidence_independent_of_input_order(self):
        history = [evidence(details(id=index + 1), 'applied') for index in range(5)]
        history += [evidence(details(id=index + 10), 'dismiss') for index in range(5)]
        result = build_behavior_profile(history)
        self.assertEqual(result.roles, {'backend engineering': 5})
        self.assertEqual(result, build_behavior_profile(reversed(history)))
        self.assertEqual(build_behavior_profile(history[:5]).roles['backend engineering'], AFFINITY_BOUND)
        self.assertEqual(build_behavior_profile(history[5:]).roles['backend engineering'], -AFFINITY_BOUND)

    def test_shared_normalization_and_no_requirements_prose(self):
        job = details(keywords=JobKeywords(languages=['JS', 'JavaScript', 'C++'], tools_and_platforms=['k8s'],
                                          concepts=['distributed systems', 'teamwork'], requirements=['Python']))
        result = build_behavior_profile([evidence(job, 'save')])
        self.assertEqual(result.skills, {'javascript': 2, 'c++': 2, 'kubernetes': 2, 'distributed systems': 2})
        self.assertNotIn('python', result.skills)

    def test_closed_history_and_source_metadata_remain_useful(self):
        job = details(title='Intern', location='Unknown', status='closed', sources=[observation(status='closed', term='Summer 2027')])
        result = build_behavior_profile([evidence(job, 'applied')])
        self.assertEqual(result.roles['backend engineering'], 4)
        self.assertEqual(result.terms, {'summer 2027': 4})

    def test_terms_use_complete_parsed_evidence_never_posting_dates(self):
        self.assertEqual(job_features(details(term='2027 Summer'))['terms'], {'summer 2027'})
        self.assertEqual(job_features(details(term='Autumn 2027'))['terms'], {'fall 2027'})
        for term in (None, 'Summer', '2027'):
            self.assertEqual(job_features(details(term=term, posted_at=date(2027, 6, 1)))['terms'], set())


class BehaviorScoringTests(unittest.TestCase):
    def test_no_history_unknown_metadata_and_unseen_features_are_neutral(self):
        self.assertEqual(score_behavior(BehaviorProfile(), details()).score, 50)
        learned = build_behavior_profile([evidence(details(), 'applied')])
        empty = details(title='Intern', location='Unknown', company='Unknown')
        self.assertEqual(score_behavior(learned, empty).score, 50)
        self.assertEqual(score_behavior(learned, empty).reasons, [])

    def test_full_bounds_and_exact_contributions(self):
        job = details(term='Summer 2027', keywords=JobKeywords(languages=['Python']))
        features = job_features(job)
        for sign, expected in ((1, 100), (-1, 0)):
            learned = BehaviorProfile(**{dimension: {key: sign * 10 for key in keys} for dimension, keys in features.items()})
            result = score_behavior(learned, job)
            self.assertEqual(result.score, expected)
            self.assertEqual(result.contributions, {'roles': sign * 40, 'skills': sign * 30, 'companies': sign * 5,
                                                    'locations': sign * 10, 'terms': sign * 5})
            self.assertLessEqual(len(result.reasons), 2)
            if sign < 0:
                self.assertEqual(result.reasons, [])

    def test_backend_preference_breaks_equal_broad_profile_fit(self):
        backend = details(id=2, title='Backend Software Engineer Intern')
        frontend = details(id=3, title='Frontend Software Engineer Intern')
        candidate = profile(target_roles=['Software Engineering'])
        self.assertEqual(score_job(candidate, backend).score, score_job(candidate, frontend).score)
        learned = build_behavior_profile([evidence(details(title=backend.job.title), 'save', 'applied')])
        self.assertGreater(score_behavior(learned, backend).score, score_behavior(learned, frontend).score)
        self.assertEqual(rank_recommendations(candidate, [frontend, backend], learned)[0].job.id, 2)

    def test_skill_alignment_uses_job_keyword_aliases_and_mean(self):
        learned = build_behavior_profile([evidence(details(keywords=JobKeywords(languages=['Python', 'SQL'], tools_and_platforms=['Kafka'])), 'save', 'applied')])
        python = details(company='Other', title='Intern', location='Unknown', keywords=JobKeywords(languages=['Python'], tools_and_platforms=['Kafka']))
        react = details(company='Other', title='Intern', location='Unknown', keywords=JobKeywords(languages=['JS'], frameworks=['React']))
        self.assertGreater(score_behavior(learned, python).score, score_behavior(learned, react).score)
        # Adding unrelated skills cannot increase the skill contribution.
        broad = python.model_copy(update={'keywords': JobKeywords(languages=['Python', 'Java', 'Go'], tools_and_platforms=['Kafka'])})
        self.assertLess(score_behavior(learned, broad).contributions['skills'], score_behavior(learned, python).contributions['skills'])

    def test_company_signal_cannot_outweigh_strong_role_alignment(self):
        learned = build_behavior_profile([evidence(details(company='Shopify', title='Intern'), 'save'),
                                          evidence(details(id=2, company='Other'), 'applied')])
        backend = details(company='Another')
        unrelated = details(company='Shopify', title='Frontend Developer Intern')
        self.assertGreater(score_behavior(learned, backend).score, score_behavior(learned, unrelated).score)
        self.assertEqual(score_behavior(learned, unrelated).contributions['companies'], 1)

    def test_one_dismissal_is_bounded(self):
        job = details(title='Frontend Developer Intern', term='Summer 2027', keywords=JobKeywords(frameworks=['React']))
        result = score_behavior(build_behavior_profile([evidence(job, 'dismiss')]), job)
        self.assertEqual(result.score, 35)
        self.assertEqual(result.reasons, [])

    def test_applied_stronger_than_save_and_click(self):
        job = details()
        scores = [score_behavior(build_behavior_profile([evidence(job, event)]), job).score for event in ('click', 'save', 'applied')]
        self.assertLess(scores[0], scores[1])
        self.assertLess(scores[1], scores[2])

    def test_location_aliases_and_conflicts_follow_existing_rules(self):
        learned = build_behavior_profile([evidence(details(location='Toronto, ON'), 'save')])
        for location in ('Toronto', 'Toronto, Ontario', 'Toronto, ON, Canada'):
            self.assertEqual(score_behavior(learned, details(location=location)).contributions['locations'], 2)
        for location in ('Vancouver, BC', 'Ottawa, ON', 'Toronto, UK'):
            self.assertEqual(score_behavior(learned, details(location=location)).contributions['locations'], 0)
        remote = build_behavior_profile([evidence(details(location='Remote Canada'), 'applied')])
        self.assertEqual(score_behavior(remote, details(location='Remote, USA')).contributions['locations'], 0)


class RecommendationCompositionTests(unittest.TestCase):
    def test_profile_fields_unchanged_and_exact_half_up_final_formula(self):
        candidate, job = profile(target_roles=['Backend Engineering']), details()
        original = score_job(candidate, job)
        result = rank_recommendations(candidate, [job], BehaviorProfile())[0]
        self.assertEqual({key: value for key, value in result.model_dump().items() if key in MatchResult.model_fields}, original.model_dump())
        self.assertEqual((result.score, result.profile_score, result.behavior_score, result.final_score), (40, 40, 50, 43))

    def test_neutral_order_and_minimum_filter_preserve_profile_semantics(self):
        candidate = profile(target_roles=['Backend Engineering'], skills=['Python'])
        jobs = [details(id=1), details(id=2, posted_at=date(2026, 10, 1)),
                details(id=3, title='Frontend Intern'), details(id=4, keywords=JobKeywords(languages=['Python']))]
        for threshold in (0, 40, 60, 100):
            expected = rank_jobs(candidate, jobs, min_score=threshold)
            actual = rank_recommendations(candidate, jobs, BehaviorProfile(), min_score=threshold)
            self.assertEqual([item.job.id for item in actual], [item.job.id for item in expected])
            self.assertTrue(all(item.behavior_score == 50 for item in actual))

    def test_deterministic_order_under_input_permutations_and_closed_exclusion(self):
        jobs = [details(id=1), details(id=2), details(id=3, status='closed')]
        learned = build_behavior_profile([evidence(details(id=4), 'save')])
        actual = rank_recommendations(profile(), jobs, learned)
        self.assertEqual(actual, rank_recommendations(profile(), reversed(jobs), learned))
        self.assertEqual([item.job.id for item in actual], [2, 1])

    def test_final_score_ties_use_upstream_date_before_quantized_profile_fit(self):
        candidate = profile(target_roles=['Backend Engineering'], skills=['Python'])
        older = details(id=1, posted_at=date(2026, 11, 1),
                        keywords=JobKeywords(languages=['Python'] + ['Other%s' % n for n in range(29)]))
        newer = details(id=2, posted_at=date(2026, 11, 3))
        undated = older.model_copy(update={'job': older.job.model_copy(update={'id': 3, 'posted_at': None})})
        stronger = details(id=4, posted_at=date(2020, 1, 1), keywords=JobKeywords(languages=['Python']))
        matches = rank_recommendations(candidate, [older, undated, newer, stronger], BehaviorProfile())
        self.assertEqual([match.job.id for match in matches], [4, 2, 1, 3])
        self.assertEqual(matches[1].final_score, matches[2].final_score)
        self.assertGreater(matches[2].profile_score, matches[1].profile_score)
