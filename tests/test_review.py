import unittest
from copy import deepcopy
from kfz_core.review import build_review, compare_results


class ReviewTests(unittest.TestCase):
    def result(self, status='Расхождение', difference='0.01'):
        row = {'code': '0800', 'title': 'Расходы', 'reported': '10.01', 'confirmed': '10.00', 'difference': difference,
               'status': status, 'reasons': [], 'evidence_ids': ['a'], 'report_evidence_id': 'b'}
        return {'context': {'contract': 'test', 'start_date': '2026-01-01', 'end_date': '2026-05-31'}, 'rows': [row],
                'total': row, 'documents': [{'file': 'readme', 'status': 'excluded', 'reason': 'служебный'}],
                'evidence': {'a': {'file': 'act.pdf', 'page': 2, 'value': '10.00'}, 'b': {'file': 'report.pdf', 'page': 1, 'value': '10.01'}}}

    def test_signed_explanation(self):
        result = self.result(difference='-0.01')
        review = build_review(result)['rows']['0800']
        self.assertIn('меньше', review['statement'])
        self.assertIn('0,01', review['statement'])
        self.assertIn('не установлена', review['cause'])
        self.assertIn('стр. 2', review['request_text'])

    def test_zero_with_incomplete_data_is_not_resolved(self):
        before = self.result()
        after = self.result('Недостаточно данных', '0.00')
        self.assertFalse(compare_results(before, after)['rows'][0]['resolved'])
        self.assertIn('Окончательный вывод невозможен', build_review(after)['rows']['0800']['statement'])

    def test_decimal_changes_and_journal_stability(self):
        before = self.result()
        after = deepcopy(before)
        after['rows'][0]['confirmed'] = '10.01'
        after['rows'][0]['difference'] = '0.00'
        after['rows'][0]['status'] = 'Совпадает'
        compared = compare_results(before, after)
        self.assertEqual(compared['rows'][0]['confirmed_change'], '0.01')
        self.assertTrue(compared['rows'][0]['resolved'])
        self.assertEqual(compared['added_documents'], [])

    def test_incomplete_total_prevents_a_complete_conclusion(self):
        result = self.result('Совпадает', '0.00')
        result['total'] = {'status': 'Недостаточно данных'}
        self.assertIn('недостаточно', build_review(result)['conclusion'])


if __name__ == '__main__':
    unittest.main()
