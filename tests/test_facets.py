"""Normalization of raw location and term cells into canonical filter values."""
import unittest

from internshipfeed.facets import canonical_location, canonical_locations, canonical_terms


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
        cases = {'Remote': 'Remote', 'Remote in USA': 'Remote (US)', 'Remote in US': 'Remote (US)',
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
