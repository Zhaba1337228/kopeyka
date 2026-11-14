import json
import tempfile
import unittest
from pathlib import Path
from io import BytesIO
from docx import Document
from openpyxl import load_workbook

from test_articles import report, document
from kfz_core import reconcile
from kfz_core.export import workbook_bytes
from kfz_core.coverage import verify_indicators


class CriteriaTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.report=self.root/'report.pdf';self.doc=self.root/'doc.docx'
        report(self.report,[('0400','Проверяемые услуги','100000,00')],'100000,00')

    def check(self,decisions=None):
        return reconcile(self.report,[self.doc],output_dir=self.root/'result',start_date='2026-03-01',end_date='2026-03-31',decisions=decisions)

    def write(self,lines):
        doc=Document()
        for text in ['АКТ № TEST-0400','Договор 24/03/15-01','Дата 15.03.2026','Исполнитель: Тест','Статья 0400',*lines]:doc.add_paragraph(text)
        doc.save(self.doc)

    def test_manual_unit_and_amount_provenance(self):
        self.write(['Единица измерения: тыс. руб.','Сумма: 100,00 руб.'])
        before=self.check();record=before['documents'][0]
        self.assertEqual(record['status'],'ambiguous');self.assertIsNone(record['amount'])
        choice={'action':'include','code':'0400','reason':'Уточнены единицы по оригиналу',
                'amount_source':record['amount_candidates'][0]['id'],'amount_raw':'100,00','amount_multiplier':'1000'}
        after=self.check({record['id']:choice})
        self.assertEqual(after['rows'][0]['status'],'Совпадает')
        ref=after['evidence'][after['rows'][0]['evidence_ids'][0]]
        self.assertTrue(ref['manual']);self.assertEqual(ref['raw_value'],'100.00');self.assertEqual(ref['multiplier'],'1000')
        self.assertIsNone(before['documents'][0]['amount'])
        choice['amount_source']='another-document:source'
        with self.assertRaises(ValueError):self.check({record['id']:choice})

    def test_metadata_correction_is_audited(self):
        self.write(['Сумма: 100000,00 руб.'])
        doc=Document(self.doc);doc.paragraphs[1].text='Договор неразборчив';doc.save(self.doc)
        before=self.check();record=before['documents'][0]
        after=self.check({record['id']:{'action':'include','code':'0400','reason':'Номер договора проверен по оригиналу','metadata':{'contract':'24/03/15-01'}}})
        self.assertEqual(after['total']['status'],'Совпадает')
        self.assertIsNone(after['documents'][0]['extracted_metadata']['contract'])
        self.assertEqual(after['documents'][0]['contract'],'24/03/15-01')

    def test_workbook_has_summary_evidence_and_no_executable_formulas(self):
        self.write(['Итого: 100000,00 руб.'])
        result=self.check();result['documents'][0]['file']='=HYPERLINK("https://example.invalid")'
        data=workbook_bytes(result,1,'2026-11-14')
        book=load_workbook(BytesIO(data),data_only=False)
        self.assertEqual(len(book.sheetnames),5)
        self.assertTrue(any(row[0].value=='Итог по КФЗ, руб.' and row[1].value=='100000.00' for row in book['Сводка']))
        self.assertFalse(any(c.data_type=='f' for ws in book for row in ws for c in row))
        self.assertTrue(book['Документы и решения']['A2'].value.startswith("'="))

    def test_detail_does_not_count_source_reclassified_to_other_article(self):
        metric={'code':'detail-7','field':'0200.main','reported':'100.00','report_evidence_ids':['report']}
        doc={'status':'accepted','code':'0400','evidence_ids':['source'],'period_start':'2026-03-01'}
        evidence={'source':{'value':'100.00','field':'0200.main'}}
        detail=verify_indicators({'indicators':[metric]},[doc],evidence,[],['2026-03'])[0]
        self.assertIsNone(detail['confirmed'])
        self.assertEqual(detail['status'],'Недостаточно данных')


if __name__=='__main__':unittest.main()
