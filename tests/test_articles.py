"""Synthetic, non-confidential forms exercise unseen article codes end to end."""
import os
import tempfile
import unittest
from pathlib import Path

import fitz
from docx import Document
from openpyxl import Workbook

from kfz_core import reconcile
from kfz_core.report import inspect_report
from kfz_core.review import build_review, compare_results


def report(path, rows, total, *, scan=False):
    font = fitz.Font('cjk')
    with fitz.open() as pdf:
        page = pdf.new_page(width=600, height=500)
        page.insert_font(fontname='casefont', fontbuffer=font.buffer)
        lines = ['Карточка фактических затрат', 'Договор 24/03/15-01', '3 этап, 2026 г.']
        for i, text in enumerate(lines):
            page.insert_text((30, 45+i*28), text, fontname='casefont', fontsize=12)
        for x, text in [(30,'Код статьи'), (140,'Наименование'), (420,'Сумма, руб.')]:
            page.insert_text((x,129),text,fontname='casefont',fontsize=12)
        for i,(code,title,amount) in enumerate(rows):
            for x,text in [(30,code),(140,title),(420,amount)]:
                page.insert_text((x,157+i*28),text,fontname='casefont',fontsize=12)
        page.insert_text((30,157+len(rows)*28),'Итого '+total,fontname='casefont',fontsize=12)
        if scan:
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            with fitz.open() as raster:
                raster.new_page(width=600, height=500).insert_image(page.rect, stream=pix.tobytes('png'))
                raster.save(path)
        else:
            pdf.save(path)


def document(path, code, amount, *, extra=''):
    doc = Document()
    for text in [f'АКТ № TEST-{code}', 'Договор 24/03/15-01', 'Дата 15.03.2026',
                 'Исполнитель: Тестовая организация', f'Статья калькуляции: {code}',
                 f'Сумма: {amount} руб.', extra]:
        doc.add_paragraph(text)
    doc.save(path)


class NewArticlesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.report = self.root/'report.pdf'
        self.rows = [(f'0{i}00', f'Тестовая статья {i}', f'{i*100},00') for i in range(4, 8)]
        report(self.report, self.rows, '2200,00')
        self.documents = []
        for code, _, amount in self.rows:
            path = self.root/f'{code}.docx'
            document(path, code, amount)
            self.documents.append(path)

    def run_check(self, docs=None, source=None):
        return reconcile(source or self.report, self.documents if docs is None else docs,
                         output_dir=self.root/'run', start_date='2026-03-01', end_date='2026-03-31')

    def test_unseen_articles_are_read_and_reconciled(self):
        result = self.run_check()
        self.assertEqual([r['code'] for r in result['rows']], ['0400', '0500', '0600', '0700'])
        self.assertTrue(all(r['status']=='Совпадает' for r in result['rows']))
        self.assertEqual(result['total']['confirmed'], '2200.00')
        self.assertEqual(result['total']['status'], 'Совпадает')
        for row in result['rows']:
            self.assertTrue(result['evidence'][row['report_evidence_id']]['bbox'])
        self.assertIn('0700', build_review(result)['scope'])

    def test_missing_source_is_not_a_zero_expense(self):
        result = self.run_check(self.documents[:-1])
        row = next(r for r in result['rows'] if r['code']=='0700')
        self.assertEqual(row['status'], 'Недостаточно данных')
        self.assertEqual(result['total']['status'], 'Недостаточно данных')

    def test_new_code_only_in_document_remains_unverified_and_comparable(self):
        before = self.run_check()
        extra = self.root/'1500.docx'
        document(extra, '1500', '50,00')
        after = self.run_check([*self.documents, extra])
        row = next(r for r in after['rows'] if r['code']=='1500')
        self.assertIsNone(row['reported'])
        self.assertEqual(row['status'], 'Недостаточно данных')
        change = next(r for r in compare_results(before, after)['rows'] if r['code']=='1500')
        self.assertIsNone(change['confirmed_change'])
        self.assertFalse(change['resolved'])
        self.assertTrue(change['changed'])
        self.assertEqual(len(compare_results(after, before)['rows']), 5)

    def test_xlsx_explicit_article_and_labeled_total(self):
        path = self.root/'new.xlsx'
        book = Workbook()
        sheet = book.active
        for value in ['АКТ № TEST-0400', 'Договор 24/03/15-01', 'Дата 15.03.2026',
                      'Статья калькуляции: 0400']:
            sheet.append([value])
        sheet.append(['Итого, руб.', 400])
        book.save(path)
        result = self.run_check([path, *self.documents[1:]])
        self.assertEqual(result['total']['status'], 'Совпадает')
        ev = result['evidence'][result['rows'][0]['evidence_ids'][0]]
        self.assertEqual(ev['cell'], 'B5')

    def test_multiple_codes_require_a_decision(self):
        document(self.documents[0], '0400', '400,00', extra='Статья калькуляции: 0500')
        result = self.run_check()
        self.assertEqual(result['documents'][0]['status'], 'ambiguous')
        self.assertEqual(result['total']['status'], 'Недостаточно данных')
        decision = {result['documents'][0]['id']: {'action':'include', 'code':'0400', 'reason':'Подтверждена статья по регистру'}}
        revised = reconcile(self.report, self.documents, output_dir=self.root/'reviewed',
                            start_date='2026-03-01', end_date='2026-03-31', decisions=decision)
        self.assertEqual(revised['total']['status'], 'Совпадает')

    def test_duplicate_report_code_is_not_silently_overwritten(self):
        report(self.report, [*self.rows, ('0400', 'Другая строка', '5,00')], '2205,00')
        result = self.run_check()
        row = result['rows'][0]
        self.assertIsNone(row['reported'])
        self.assertEqual(row['status'], 'Недостаточно данных')

    def test_unknown_workbook_is_not_silently_excluded(self):
        path = self.root/'unreadable-amount.xlsx'
        book = Workbook()
        book.active.append(['Договор 24/03/15-01', 'Статья 0400', 'Неизвестная форма'])
        book.save(path)
        result = self.run_check([*self.documents, path])
        self.assertEqual(result['documents'][-1]['status'], 'ambiguous')
        self.assertEqual(result['rows'][0]['status'], 'Недостаточно данных')

    def test_reviewed_assignment_does_not_leave_a_phantom_article(self):
        document(self.documents[0], '1500', '400,00')
        result = self.run_check()
        record = result['documents'][0]
        self.assertEqual(record['status'], 'ambiguous')
        self.assertIn('1500', [row['code'] for row in result['rows']])
        revised = reconcile(self.report, self.documents, output_dir=self.root/'corrected',
                            start_date='2026-03-01', end_date='2026-03-31',
                            decisions={record['id']: {'action':'include','code':'0400','reason':'Код уточнён по регистру'}})
        self.assertNotIn('1500', [row['code'] for row in revised['rows']])
        self.assertEqual(revised['total']['status'], 'Совпадает')

    def test_multiple_amounts_in_report_row_require_review(self):
        report(self.report, [('0400', 'Неоднозначная строка', '500,00 400,00')], '400,00')
        result = self.run_check([self.documents[0]])
        self.assertIsNone(result['rows'][0]['reported'])
        self.assertEqual(result['total']['status'], 'Недостаточно данных')

    @unittest.skipUnless(os.environ.get('KOPEYKA_TEST_OCR'), 'Enable with KOPEYKA_TEST_OCR=1 and Tesseract installed')
    def test_scanned_row_table(self):
        report(self.report, self.rows, '2200,00', scan=True)
        result = self.run_check()
        self.assertEqual([r['code'] for r in result['rows']], ['0400', '0500', '0600', '0700'])
        self.assertEqual(result['total']['status'], 'Совпадает')

    @unittest.skipUnless(os.environ.get('KOPEYKA_TEST_OCR'), 'Enable with KOPEYKA_TEST_OCR=1 and Tesseract installed')
    def test_landscape_columns_discovered_by_heading_not_fixed_position(self):
        font = fitz.Font('cjk')
        with fitz.open() as pdf:
            page = pdf.new_page(width=600, height=500)
            page.insert_font(fontname='casefont', fontbuffer=font.buffer)
            edges = [13, 90, 190, 290, 390, 490, 590]
            for x in edges:
                page.draw_line((x,125),(x,236),width=.6)
            for y in [125,205,214,225,236]:
                page.draw_line((13,y),(590,y),width=.6)
            for i,(code,_,amount) in enumerate(self.rows):
                page.insert_text((edges[i+1]+8,137),'Статья '+code,fontname='casefont',fontsize=7)
                page.insert_text((edges[i+1]+8,222),amount,fontname='casefont',fontsize=7)
            page.insert_text((500,175),'Итого',fontname='casefont',fontsize=8)
            page.insert_text((500,222),'2200,00',fontname='casefont',fontsize=7)
            page.insert_text((15,222),'3 этап, 2026 г.',fontname='casefont',fontsize=6.5)
            page.insert_text((452,85),'Договор 24/03/15-01',fontname='casefont',fontsize=7)
            pdf.save(self.root/'horizontal.pdf')
        candidate = inspect_report(self.root/'horizontal.pdf',self.root/'horizontal')[0]
        self.assertEqual(set(candidate['articles']), {'0400','0500','0600','0700'})
        self.assertEqual(candidate['values']['total'],'2200.00')
        self.assertEqual(candidate['values']['0400'],'400.00')


if __name__ == '__main__':
    unittest.main()
