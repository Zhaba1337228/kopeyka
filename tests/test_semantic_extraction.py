"""Layout changes must preserve meaning; unchanged cell addresses prove nothing."""
import os
import tempfile
import unittest
from pathlib import Path
from openpyxl import Workbook
from docx import Document

from kfz_core import reconcile
from kfz_core.amounts import read_amounts, pin_source
from kfz_core.extract import extract
from test_articles import report
from kfz_core.report import inspect_report


class SemanticExtractionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def parse(self,path):
        meta={}
        result=read_amounts(path,'fixture',extract(path),meta)
        return result,meta

    def payroll(self,title='Совсем другой лист',offset=0,swapped=False,reverse=False,total=1200):
        path=self.root/'renamed.xlsx';book=Workbook();sheet=book.active;sheet.title=title
        for text in ['Расчет начислений','Документ: ВЕД-TEST','Договор 24/03/15-01','Период: март 2026']:
            sheet.append([text])
        rows=[('Справочно: выплаченный аванс',500),('Основная заработная плата',1000),('Доплаты и резерв',200)]
        if reverse:rows.reverse()
        start=7+offset
        label_col,amount_col=(5,2) if swapped else (2,5)
        sheet.cell(start,label_col,'Вид начисления');sheet.cell(start,amount_col,'Сумма, руб.')
        for i,(label,value) in enumerate(rows,start+1):
            sheet.cell(i,label_col,label);sheet.cell(i,amount_col,value)
        sheet.cell(start+len(rows)+1,label_col,'Итого начислено')
        sheet.cell(start+len(rows)+1,amount_col,total)
        book.save(path);return path

    def test_payroll_uses_labels_across_names_rows_and_columns(self):
        for name,offset,swapped,reverse in [('Ведомость',0,False,False),('Начисления',8,False,True),('Новая форма',3,True,True)]:
            with self.subTest(name=name):
                result,meta=self.parse(self.payroll(name,offset,swapped,reverse))
                self.assertEqual(result['amount'],'1200.00',result['reason'])
                self.assertEqual(meta['code'],'0200')
                refs=[e for e in result['evidence'] if e['id'] in result['evidence_ids']]
                self.assertEqual({e['field'] for e in refs},{'0200.main','0200.extra_reserve'})
                self.assertTrue(all(e['cell'].startswith('B' if swapped else 'E') for e in refs))

    def test_shifted_payroll_no_longer_creates_false_discrepancy(self):
        source=self.payroll(title='Ведомость')
        report(self.root/'report.pdf',[('0200','Оплата труда','1200,00')],'1200,00')
        result=reconcile(self.root/'report.pdf',[source],output_dir=self.root/'out',start_date='2026-03-01',end_date='2026-03-31')
        self.assertEqual(result['rows'][0]['status'],'Совпадает')
        self.assertEqual(result['rows'][0]['confirmed'],'1200.00')

    def test_inconsistent_total_requires_review(self):
        result,_=self.parse(self.payroll(total=1300))
        self.assertIsNone(result['amount'])
        self.assertIn('противоречат',result['reason'])

    def test_sheet_name_does_not_define_document_type_or_amount(self):
        path=self.root/'not-payroll.xlsx';book=Workbook();sheet=book.active;sheet.title='Ведомость'
        sheet['C6']=1000;sheet['C7']=200
        book.save(path)
        result,meta=self.parse(path)
        self.assertIsNone(result['amount'])
        self.assertNotIn('code',meta)

    def test_contribution_heading_distinguishes_base_from_amount(self):
        path=self.root/'fees.xlsx';book=Workbook();sheet=book.active;sheet.title='Данные'
        sheet['E9']='База начислений, руб.';sheet['B9']='Начислено взносов, руб.'
        sheet['E10']=100000;sheet['B10']=30000
        book.save(path)
        result,meta=self.parse(path)
        self.assertEqual(result['amount'],'30000.00')
        self.assertEqual(meta['code'],'0300')
        self.assertEqual(result['evidence'][0]['cell'],'B10')

    def test_integer_amount_and_vat_on_same_line(self):
        for text in ['Итого к оплате: 1200 руб.','Итого к оплате: 1200,00 руб., в том числе НДС 200,00 руб.']:
            with self.subTest(text=text):
                path=self.root/'act.docx';doc=Document();doc.add_paragraph(text);doc.save(path)
                result,_=self.parse(path)
                self.assertEqual(result['amount'],'1200.00')

    def test_integer_report_and_primary_reconcile(self):
        from test_articles import document
        report(self.root/'report.pdf',[('0400','Услуги','1200')],'1200')
        document(self.root/'act.docx','0400','1200')
        result=reconcile(self.root/'report.pdf',[self.root/'act.docx'],output_dir=self.root/'out',start_date='2026-03-01',end_date='2026-03-31')
        self.assertEqual(result['total']['status'],'Совпадает')
        self.assertEqual(result['total']['confirmed'],'1200.00')

    def test_tax_without_grand_total_is_not_the_document_amount(self):
        path=self.root/'tax-only.xlsx';book=Workbook();sheet=book.active
        sheet.append(['Показатель','Сумма, руб.'])
        sheet.append(['Стоимость без НДС',1000]);sheet.append(['НДС',200]);book.save(path)
        result,_=self.parse(path)
        self.assertIsNone(result['amount'])

    def test_separate_components_verify_composite_detail_without_double_count(self):
        from kfz_core.coverage import verify_indicators
        path=self.root/'split.xlsx';book=Workbook();sheet=book.active;sheet.title='Регистр'
        for row in [('Начисление','Сумма, руб.'),('Основная заработная плата',1000),('Дополнительная заработная плата',120),('Резерв отпусков',80),('Итого',1200)]:sheet.append(row)
        book.save(path);parsed,meta=self.parse(path)
        self.assertEqual(parsed['amount'],'1200.00')
        evidence={e['id']:e for e in parsed['evidence']}
        doc={'status':'accepted','code':meta['code'],'evidence_ids':parsed['evidence_ids'],'period_start':'2026-03-01'}
        metrics=[{'code':'extra-reserve','field':'0200.extra_reserve','reported':'200.00','report_evidence_ids':[]},
                 {'code':'without-reserve','field':'0200.without_reserve','reported':'1120.00','report_evidence_ids':[]}]
        result=verify_indicators({'indicators':metrics},[doc],evidence,[],['2026-03'])
        self.assertTrue(all(x['status']=='Совпадает' and not x['included_in_total'] for x in result))

    def test_old_manual_decision_is_rebound_by_source_not_candidate_number(self):
        path=self.payroll()
        parsed,_=self.parse(path)
        original=next(e for e in parsed['evidence'] if e.get('field')=='0200.main')
        legacy=dict(original,id='fixture:amount-4')
        result={'documents':[{'id':'fixture','preview_evidence_ids':[legacy['id']]}],'evidence':{legacy['id']:legacy}}
        decision=pin_source({'action':'include','amount_source':legacy['id'],'amount_raw':'1000','amount_multiplier':'1','reason':'Подтверждено'},'fixture',result)
        after=read_amounts(path,'fixture',extract(path),{},decision)
        self.assertEqual(after['amount'],'1000.00')
        self.assertEqual(decision['amount_source'],original['id'])
        self.assertEqual(after['evidence'][-1]['cell'],original['cell'])
        with self.assertRaises(ValueError):pin_source({**decision,'amount_source':'foreign'},'fixture',result)

    def test_unknown_extra_accrual_is_not_silently_ignored(self):
        path=self.payroll(total=1200)
        from openpyxl import load_workbook
        book=load_workbook(path);book.active['B15']='Премия';book.active['E15']=300;book.save(path)
        result,_=self.parse(path)
        self.assertIsNone(result['amount'])

    @unittest.skipUnless(os.environ.get('KOPEYKA_TEST_OCR'), 'Requires local OCR')
    def test_report_grid_moves_and_columns_reorder(self):
        import fitz
        font=fitz.Font('cjk')
        for index,(dx,dy,scale) in enumerate([(110,210,.9),(30,40,1.1)]):
            path=self.root/f'moved-{index}.pdf'
            with fitz.open() as pdf:
                page=pdf.new_page(width=750,height=650)
                page.insert_font(fontname='fixture',fontbuffer=font.buffer)
                def point(x,y):return (dx+x*scale,dy+y*scale)
                def text(x,y,value,size=7):page.insert_text(point(x,y),value,fontname='fixture',fontsize=size*scale)
                edges=[13,90,190,290,390,490,590]
                for x in edges:page.draw_line(point(x,125),point(x,236),width=.6*scale)
                for y in [125,205,214,225,236]:page.draw_line(point(13,y),point(590,y),width=.6*scale)
                for i,code in enumerate(['0700','0400','0600','0500']):
                    text(edges[i+1]+8,137,'Статья '+code)
                    text(edges[i+1]+8,222,str(int(code))+',00')
                text(500,175,'Итого',8);text(500,222,'2200,00')
                text(15,222,'4 этап, 2026 г.',6.5)
                text(20,85,'Договор МФТИ-2026/01')
                pdf.save(path)
            candidate=inspect_report(path,self.root/f'grid-{index}')[0]
            self.assertEqual(candidate['stage'],4)
            self.assertEqual(candidate['contract'],'МФТИ-2026/01')
            self.assertEqual(candidate['values'],{'0700':'700.00','0400':'400.00','0600':'600.00','0500':'500.00','total':'2200.00'})


if __name__=='__main__':unittest.main()
