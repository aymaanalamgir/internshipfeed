"""Normalization of raw location and term cells into canonical filter values."""
import unittest

from internshipfeed.facets import (canonical_location, canonical_locations, canonical_terms, describe_location,
                                   location_facets, location_tree)


class LocationTests(unittest.TestCase):
    def test_variants_collapse_to_one_city(self):
        for raw in ('Toronto, ON, Canada', 'Toronto, ON', 'Toronto, ON,', 'Toronto, ON, CAN', 'Toronto, Ontario'):
            with self.subTest(raw=raw):
                self.assertEqual(canonical_location(raw), 'Toronto, ON')

    def test_aliases_neighborhoods_and_accents(self):
        cases = {
            'NYC': 'New York, NY', 'Manhattan, NYC': 'New York, NY', 'SF': 'San Francisco, CA',
            'South SF': 'South San Francisco, CA', 'LA': 'Los Angeles, CA',
            'Arlington County, Arlington, VA': 'Arlington, VA', 'Kanata, Ottawa, ON, Canada': 'Ottawa, ON',
            'Waterdown, Hamilton, ON, Canada': 'Hamilton, ON', 'Québec City, QC, Canada': 'Quebec City, QC',
            'St. John’s, NL': "St. John's, NL", 'Long Island, New York': 'Long Island, NY',
            'Riverside-San Bernardino-Ontario, CA, CA': 'Riverside-San Bernardino-Ontario, CA',
            'Tottenham, London, UK': 'London, United Kingdom', 'Paris, France': 'Paris, France',
            'Florida': 'Florida', 'USA': 'United States', 'Remoteville, OH': 'Remoteville, OH',
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(canonical_location(raw), expected)

    def test_remote_regions(self):
        cases = {'Remote': 'Remote (Anywhere)', 'Remote in USA': 'Remote (US)', 'Remote in US': 'Remote (US)',
                 'Remote, Canada': 'Remote (Canada)', 'Remote in Canada': 'Remote (Canada)',
                 'Remote (UK)': 'Remote (UK)', 'Remote India': 'Remote (India)'}
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(canonical_location(raw), expected)

    def test_multi_location_cells_split_dedupe_and_drop_placeholders(self):
        self.assertEqual(canonical_locations('Austin, TX / Milford, MI / Mountain View, CA / Warren, MI'),
                         ['Austin, TX', 'Milford, MI', 'Mountain View, CA', 'Warren, MI'])
        self.assertEqual(canonical_locations('NYC / New York, NY; Multiple locations | Toronto, ON, Canada'),
                         ['New York, NY', 'Toronto, ON'])
        for raw in ('', None, 'Unknown', 'TBD'):
            self.assertEqual(canonical_locations(raw), [])


class RegionTests(unittest.TestCase):
    def test_province_with_country_is_the_province(self):
        self.assertEqual(canonical_location('Ontario, Canada'), 'Ontario')
        self.assertEqual(canonical_location('Nova Scotia, Canada'), 'Nova Scotia')

    def test_labels_map_to_regions_area_and_city(self):
        cases = {
            'Toronto, ON': (['Canada'], 'Ontario', True), 'Ontario': (['Canada'], 'Ontario', False),
            'Remote (Canada)': (['Canada', 'Remote'], 'Remote (Canada)', False), 'Canada': (['Canada'], None, False),
            'New York, NY': (['United States'], 'New York', True), 'Florida': (['United States'], 'Florida', False),
            'Remote (US)': (['United States', 'Remote'], 'Remote (US)', False),
            'London, United Kingdom': (['International'], 'United Kingdom', True),
            'Singapore': (['International'], 'Singapore', False),
            'Remote (UK)': (['International', 'Remote'], 'Remote (UK)', False),
            'Remote (Anywhere)': (['Remote'], 'Remote (Anywhere)', False),
        }
        for label, expected in cases.items():
            with self.subTest(label=label):
                self.assertEqual(describe_location(label), expected)

    def test_job_facets_union_regions_and_areas(self):
        self.assertEqual(location_facets(['Toronto, ON', 'Remote (US)', 'Paris, France']),
                         (['Canada', 'United States', 'International', 'Remote'], ['France', 'Ontario', 'Remote (US)']))

    def test_tree_orders_regions_then_remote_first_areas_then_cities(self):
        tree = location_tree(['Toronto, ON', 'Ottawa, ON', 'Calgary, AB', 'Remote (Canada)', 'Canada', 'Austin, TX',
                              'Remote (Anywhere)', 'Remote (US)', 'London, United Kingdom', 'Singapore'])
        self.assertEqual(tree, [
            {'name': 'Canada', 'areas': [{'name': 'Remote (Canada)', 'cities': []},
                                         {'name': 'Alberta', 'cities': ['Calgary, AB']},
                                         {'name': 'Ontario', 'cities': ['Ottawa, ON', 'Toronto, ON']}]},
            {'name': 'United States', 'areas': [{'name': 'Remote (US)', 'cities': []},
                                                {'name': 'Texas', 'cities': ['Austin, TX']}]},
            {'name': 'International', 'areas': [{'name': 'Singapore', 'cities': []},
                                                {'name': 'United Kingdom', 'cities': ['London, United Kingdom']}]},
            {'name': 'Remote', 'areas': [{'name': 'Remote (Anywhere)', 'cities': []},
                                         {'name': 'Remote (Canada)', 'cities': []}, {'name': 'Remote (US)', 'cities': []}]},
        ])


class TermTests(unittest.TestCase):
    def test_combinations_split_dedupe_and_sort_chronologically(self):
        self.assertEqual(canonical_terms('Fall 2026, Summer 2026, Winter 2027, Spring 2027'),
                         ['Summer 2026', 'Fall 2026', 'Winter 2027', 'Spring 2027'])
        self.assertEqual(canonical_terms('Winter 2026; Winter 2026'), ['Winter 2026'])
        self.assertEqual(canonical_terms('Autumn 2027'), ['Fall 2027'])

    def test_title_is_fallback_only(self):
        self.assertEqual(canonical_terms(None, 'Data Intern (Winter 2027)'), ['Winter 2027'])
        self.assertEqual(canonical_terms('Summer 2027', 'Data Intern (Winter 2027)'), ['Summer 2027'])
        self.assertEqual(canonical_terms('', 'Software Intern'), [])


if __name__ == '__main__':
    unittest.main()
