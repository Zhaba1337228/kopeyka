"""Adversarial accounting fixtures for the gaps found in the second case audit."""
import tempfile
import unittest
from pathlib import Path

import fitz
from docx import Document
from openpyxl import Workbook

from kfz_core import reconcile
from kfz_core.amounts import read_amounts
from kfz_core.engine import _metadata
from kfz_core.extract import extract
from kfz_core.formulas import FormulaReader, FormulaError
from kfz_core.periods import dates_in, missing_months
from kfz_core.review import compare_results
from test_articles import report, document


class RemainingCriteriaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def doc(self, name, lines):
        path = self.root / (name+'.docx'); doc = Document()
        for line in lines:doc.add_paragraph(line)
        doc.save(path);return path

    def source(self, name='act', code='0200', period='с 01.01.2026 по 31.03.2026', amount='1200,00'):
        return self.doc(name,[f'Справка № {name}', 'Договор 24/03/15-01', 'Период: '+period,
                              'Исполнитель: Учебная организация', 'Статья калькуляции: '+code,
                              'Итого начислено: '+amount+' руб.'])

    def run_check(self, sources, code='0200', amount='1200,00', start='2026-01-01', end='2026-03-31'):
        target=self.root/'report.pdf'
        if target.exists():target.unlink()
        report(target,[(code,'Тестовая статья',amount)],amount)
        return reconcile(target,sources,output_dir=self.root/'out',start_date=start,end_date=end)

    def test_contract_date_never_replaces_act_date_or_number(self):
        path=self.doc('act',['Договор № 24/03/15-01 от 09.04.2024','АКТ № А-1',
                             'Дата акта: 15.03.2026','Исполнитель: Учебная организация',
                             'Статья калькуляции: 0400','Итого к оплате: 1200,00 руб.'])
        result=self.run_check([path],code='0400',start='2026-03-01')
        self.assertEqual(result['total']['status'],'Совпадает')
        doc=result['documents'][0]
        self.assertEqual((doc['number'],doc['document_date'],doc['document_type']),('А-1','2026-03-15','Акт'))

    def test_contract_date_alone_does_not_establish_document_period(self):
        parsed=dates_in('Договор от 09.04.2024\nАкт № 123')
        self.assertIsNone(parsed['period_start'])
        self.assertIsNone(parsed['document_date'])
        parsed=dates_in('Срок действия договора: с 01.01.2026 по 31.12.2026\nАкт № 123 от 15.03.2026')
        self.assertEqual(parsed['period_start'],'2026-03-15')

    def test_explicit_period_wins_over_act_date_and_due_date(self):
        parsed=dates_in('Договор № 123 от 09.04.2024\nАкт № А-1 от 05.04.2026\nПериод: март 2026\nСрок оплаты: 30.04.2026')
        self.assertEqual(parsed['period_start'],'2026-03-01')
        self.assertEqual(parsed['document_date'],'2026-04-05')

    def test_conflicting_and_invalid_dates_require_review(self):
        for text in ['Дата акта: 01.03.2026\nДата документа: 02.03.2026',
                     'Период: март 2026\nПериод: февраль 2026',
                     'Период: с 31.03.2026 по 01.03.2026', 'Дата: 31.02.2026']:
            with self.subTest(text=text):self.assertTrue(dates_in(text)['date_issue'])

    def test_date_in_adjacent_table_cell(self):
        self.assertEqual(dates_in('Дата документа\n15.03.2026\nДоговор № 123')['document_date'],'2026-03-15')

    def test_quarter_summary_covers_all_months(self):
        for period in ['с 01.01.2026 по 31.03.2026','1 квартал 2026']:
            with self.subTest(period=period):
                result=self.run_check([self.source(period=period)])
                self.assertEqual(result['total']['status'],'Совпадает',result['rows'])

    def test_partial_month_is_not_claimed_as_full_month(self):
        result=self.run_check([self.source(period='с 15.01.2026 по 31.03.2026')])
        self.assertEqual(result['total']['status'],'Недостаточно данных')
        self.assertIn('январь',str(result['rows'][0]['reasons']))
        self.assertNotIn('февраль',str(result['rows'][0]['reasons']))

    def test_adjacent_intervals_cover_month_without_double_count(self):
        paths=[self.source('first',period='с 01.03.2026 по 15.03.2026',amount='700,00'),
               self.source('second',period='с 16.03.2026 по 31.03.2026',amount='500,00')]
        result=self.run_check(paths,start='2026-03-01')
        self.assertEqual(result['total']['status'],'Совпадает')

    def test_quarter_plus_month_is_never_silently_added(self):
        paths=[self.source('quarter'),self.source('march',period='март 2026',amount='400,00')]
        result=self.run_check(paths)
        self.assertEqual(result['total']['status'],'Недостаточно данных')
        self.assertTrue(all(d['status']=='ambiguous' for d in result['documents']))
        self.assertTrue(all('Пересекаются' in d['reason'] for d in result['documents']))
        decisions={result['documents'][0]['id']:{'action':'exclude','reason':'Квартальная справка повторяет месячные начисления'}}
        after=reconcile(self.root/'report.pdf',paths,output_dir=self.root/'review',start_date='2026-01-01',end_date='2026-03-31',decisions=decisions)
        self.assertEqual(after['documents'][1]['status'],'accepted')

    def test_duplicate_quarter_is_excluded_before_overlap_check(self):
        path=self.source(); copy=self.root/'copy.docx';copy.write_bytes(path.read_bytes())
        result=self.run_check([path,copy])
        self.assertEqual(result['total']['status'],'Совпадает')
        self.assertEqual(result['documents'][1]['status'],'excluded')

    def test_wrong_contract_and_period_remain_excluded(self):
        path=self.source(period='март 2025')
        result=self.run_check([path])
        self.assertEqual(result['documents'][0]['status'],'excluded')

    def parse_book(self, rows):
        book=Workbook();sheet=book.active;sheet.title='Регистр'
        for row in rows:sheet.append(row)
        path=self.root/'register.xlsx';book.save(path)
        meta={};parsed=read_amounts(path,'fixture',extract(path),meta)
        return parsed,meta

    def test_formula_sum_is_recomputed_and_has_cell_provenance(self):
        parsed,_=self.parse_book([['Позиция','Сумма, руб.'],['Услуга 1',1000],['Услуга 2',200],['Итого','=SUM(B2:B3)']])
        self.assertEqual(parsed['amount'],'1200.00',parsed['reason'])
        ev=next(e for e in parsed['evidence'] if e['id'] in parsed['evidence_ids'])
        self.assertEqual(ev['formula'],'=SUM(B2:B3)')
        self.assertEqual({r['cell'] for r in ev['formula_sources']},{'B2','B3','B4'})

    def test_unsupported_formula_cannot_fall_back_to_partial_amount(self):
        for formula in ['=WEBSERVICE("https://example.invalid")','=IF(B2>0,B2,0)','=B3','=SUM(B2:B99)','=1/0']:
            with self.subTest(formula=formula):
                parsed,_=self.parse_book([['Позиция','Сумма, руб.'],['Услуга',1000],['Итого',formula]])
                self.assertIsNone(parsed['amount'])
                self.assertIn('формул',parsed['reason'])

    def test_decimal_formula_rounding_references_and_limits(self):
        reader=FormulaReader({'Данные':{'A1': '1.005', 'A2': "=ROUND(A1,2)", 'A3': "='Другой лист'!$B$1*2+A2"},'Другой лист':{'B1':10}})
        self.assertEqual(str(reader.cell('Данные','A3')[0]),'21.01')
        for expr in ['=[external.xlsx]Sheet1!A1','=2^999999','=SUM(A1:ZZ99999)','=ROUND(1,1E1000000)']:
            with self.subTest(expr=expr), self.assertRaises(FormulaError):
                FormulaReader({'S':{'A1':expr}}).cell('S','A1')

    def test_employee_register_subtotals_are_not_counted_twice(self):
        parsed,meta=self.parse_book([
            ['Сотрудник','Основная заработная плата','Доплаты и резерв'],
            ['Иванов',1000,200],['Петров',800,100],
            ['Итого','=SUM(B2:B3)','=SUM(C2:C3)']])
        self.assertEqual(parsed['amount'],'2100.00',parsed['reason'])
        self.assertEqual(meta['code'],'0200')
        self.assertEqual(len(parsed['evidence_ids']),4)

    def test_employee_subtotal_conflict_requires_review(self):
        parsed,_=self.parse_book([['Сотрудник','Основная заработная плата','Доплаты и резерв'],
                                 ['Иванов',1000,200],['Петров',800,100],['Итого',1900,300]])
        self.assertIsNone(parsed['amount'])

    def test_contribution_employee_register(self):
        parsed,meta=self.parse_book([['Сотрудник','Начислено взносов, руб.'],['Иванов',300],['Петров',200],['Итого','=SUM(B2:B3)']])
        self.assertEqual(parsed['amount'],'500.00',parsed['reason'])
        self.assertEqual(meta['code'],'0300')

    def detailed_report(self, changed=False, swapped=False):
        path=self.root/'detail.pdf';font=fitz.Font('cjk')
        with fitz.open() as pdf:
            page=pdf.new_page(width=700,height=550)
            page.insert_font(fontname='fixture',fontbuffer=font.buffer)
            def text(x,y,value):page.insert_text((x,y),value,fontname='fixture',fontsize=12)
            text(30,35,'Договор 24/03/15-01');text(30,65,'3 этап, 2026 г.')
            code_x, amount_x, title_x=(200,30,350) if swapped else (30,500,150)
            text(code_x,100,'Код статьи');text(amount_x,100,'Сумма, руб.');text(title_x,100,'Наименование')
            text(code_x,130,'0400');text(amount_x,130,'1200,00');text(title_x,130,'Услуги по заявке 765')
            text(30,160,'Итого 1200,00')
            text(30,220,'Трудоёмкость, чел.-мес.: '+('3,75' if changed else '3,50'))
            text(30,250,'Материальные затраты: 0,00 руб.')
            pdf.save(path)
        return path

    def test_independent_details_reconcile_without_inflating_money(self):
        primary=self.root/'act.docx';document(primary,'0400','1200,00')
        detail=self.doc('details',['Регистр № Д-1','Договор 24/03/15-01','Период: март 2026',
                                   'Трудоёмкость, чел.-мес.: 3,50','Материальные затраты: 0,00 руб.'])
        for changed,expected in [(False,'Совпадает'),(True,'Расхождение')]:
            with self.subTest(changed=changed):
                target=self.detailed_report(changed)
                result=reconcile(target,[primary,detail],output_dir=self.root/str(changed),start_date='2026-03-01',end_date='2026-03-31')
                self.assertEqual(result['total']['confirmed'],'1200.00')
                self.assertEqual(len(result['rows']),1)
                metrics={m['field']:m for m in result['indicators']}
                self.assertEqual(metrics['labor.total']['status'],expected)
                self.assertEqual(metrics['material.total']['status'],'Совпадает')
                self.assertEqual(metrics['labor.total']['difference'],'0.25' if changed else '0.00')
                self.assertTrue(all(m['evidence_ids'] and not m['included_in_total'] for m in metrics.values()))

    def test_missing_detail_is_not_zero(self):
        primary=self.root/'act.docx';document(primary,'0400','1200,00')
        result=reconcile(self.detailed_report(),[primary],output_dir=self.root/'missing',start_date='2026-03-01',end_date='2026-03-31')
        self.assertTrue(all(m['status']=='Недостаточно данных' for m in result['indicators']))
        self.assertTrue(all(m['confirmed'] is None for m in result['indicators']))

    def test_labor_hours_are_not_silently_converted_to_months(self):
        path=self.doc('hours',['Трудоёмкость, чел.-час.: 168,00'])
        meta={};parsed=read_amounts(path,'hours',extract(path),meta)
        self.assertIsNone(parsed['amount'])
        self.assertTrue(parsed['reason'])

    def test_labor_precision_is_not_rounded_to_money_cents(self):
        primary=self.root/'act.docx';document(primary,'0400','1200,00')
        detail=self.doc('fraction',['Регистр № Д-2','Договор 24/03/15-01','Период: март 2026','Трудоёмкость, чел.-мес.: 3,499'])
        result=reconcile(self.detailed_report(),[primary,detail],output_dir=self.root/'fraction',start_date='2026-03-01',end_date='2026-03-31')
        metric=next(m for m in result['indicators'] if m['field']=='labor.total')
        self.assertEqual(metric['confirmed'],'3.499')
        self.assertEqual(metric['difference'],'0.001')
        self.assertEqual(metric['status'],'Расхождение')

    def test_detail_versions_and_units_are_independent_of_money(self):
        primary=self.root/'act.docx';document(primary,'0400','1200,00')
        detail=self.doc('details',['Регистр № Д-1','Договор 24/03/15-01','Период: март 2026','Трудоёмкость, чел.-мес.: 3,50'])
        target=self.detailed_report()
        before=reconcile(target,[primary],output_dir=self.root/'before',start_date='2026-03-01',end_date='2026-03-31')
        after=reconcile(target,[primary,detail],output_dir=self.root/'after',start_date='2026-03-01',end_date='2026-03-31')
        compared=compare_results(before,after)
        metric=next(m for m in compared['indicators'] if m['code']=='detail-3')
        self.assertTrue(metric['resolved'])
        self.assertIsNone(metric['before']['confirmed'])
        self.assertEqual(metric['after']['confirmed'],'3.50')
        self.assertEqual(metric['unit'],'чел.-мес.')
        self.assertIsNone(after['documents'][1]['amount'])
        source=after['evidence'][after['documents'][1]['indicator_evidence_ids'][0]]
        self.assertEqual(source['unit'],'чел.-мес.')
        with self.assertRaises(ValueError):
            reconcile(target,[primary,detail],output_dir=self.root/'bad-review',start_date='2026-03-01',end_date='2026-03-31',
                      decisions={after['documents'][1]['id']:{'action':'include','code':'0400','reason':'Ошибочная денежная привязка'}})

    def test_pdf_amounts_in_separate_text_blocks_keep_their_labels(self):
        path=self.root/'primary.pdf';font=fitz.Font('cjk')
        with fitz.open() as pdf:
            page=pdf.new_page();page.insert_font(fontname='fixture',fontbuffer=font.buffer)
            for index,(label,value) in enumerate([('Основная заработная плата','1000,00'),('Дополнительная заработная плата','120,00'),('Резерв отпусков','80,00'),('Итого начислено','1200,00')]):
                page.insert_text((30,50+index*30),label,fontname='fixture',fontsize=11)
                page.insert_text((400,50+index*30),value,fontname='fixture',fontsize=11)
            pdf.save(path)
        meta={};parsed=read_amounts(path,'pdf',extract(path),meta)
        self.assertEqual(parsed['amount'],'1200.00',parsed['reason'])
        self.assertEqual(len(parsed['evidence_ids']),3)
        self.assertTrue(all(e['bbox'] for e in parsed['evidence']))

    def test_all_salary_and_contribution_details_use_separate_components(self):
        from kfz_core.coverage import verify_indicators
        for parent,labels in [('0200',['Основная заработная плата','Дополнительная заработная плата','Резерв отпусков']),
                              ('0300',['Взносы с основной зарплаты','Взносы с дополнительной зарплаты','Взносы с резерва'])]:
            parsed,meta=self.parse_book([['Показатель','Сумма, руб.'],*[list(pair) for pair in zip(labels,[1000,120,80])],['Итого','=SUM(B2:B4)']])
            self.assertEqual(parsed['amount'],'1200.00',parsed['reason'])
            metrics=[{'code':part,'field':parent+'.'+part,'reported':value,'report_evidence_ids':[]}
                     for part,value in [('main','1000.00'),('extra','120.00'),('reserve','80.00'),('without_reserve','1120.00'),('extra_reserve','200.00')]]
            sources=[{'status':'accepted','code':parent,'evidence_ids':parsed['evidence_ids'],
                      'period_start':'2026-01-01','period_end':'2026-03-31','date_rule':'указанный период'}]
            evidence={e['id']:e for e in parsed['evidence']}
            result=verify_indicators({'indicators':metrics},sources,evidence,[],['2026-01','2026-02','2026-03'])
            self.assertTrue(all(m['status']=='Совпадает' for m in result),result)
            evidence[parsed['evidence_ids'][1]]['value']='125.00'
            result=verify_indicators({'indicators':metrics},sources,evidence,[],['2026-01','2026-02','2026-03'])
            self.assertEqual([m['status'] for m in result],['Совпадает','Расхождение','Совпадает','Расхождение','Расхождение'])

    def test_swapped_report_columns_and_numbers_in_description(self):
        primary=self.root/'act.docx';document(primary,'0400','1200,00')
        result=reconcile(self.detailed_report(swapped=True),[primary],output_dir=self.root/'swapped',start_date='2026-03-01',end_date='2026-03-31')
        self.assertEqual(result['rows'][0]['reported'],'1200.00')
        self.assertEqual(result['total']['status'],'Совпадает')

    def test_ambiguous_report_value_can_only_be_corrected_against_its_source(self):
        primary=self.root/'act.docx';document(primary,'0400','1200,00')
        target=self.root/'ambiguous.pdf';report(target,[('0400','Услуги','1200,00 / 1300,00')],'1200,00')
        before=reconcile(target,[primary],output_dir=self.root/'before',start_date='2026-03-01',end_date='2026-03-31')
        row=before['rows'][0];self.assertIsNone(row['reported'])
        source=row['report_evidence_id'];self.assertTrue(before['evidence'][source]['bbox'])
        decision={'source':source,'raw':'1200','multiplier':'1','reason':'Сумма проверена по оригиналу КФЗ'}
        after=reconcile(target,[primary],output_dir=self.root/'after',start_date='2026-03-01',end_date='2026-03-31',report_decisions={'0400':decision})
        self.assertEqual(after['total']['status'],'Совпадает')
        proof=after['evidence'][after['rows'][0]['report_evidence_id']]
        self.assertTrue(proof['manual']);self.assertIsNone(proof['original_value'])
        self.assertIn(source,after['evidence']);self.assertIsNone(after['evidence'][source]['value'])
        self.assertIsNone(before['rows'][0]['reported'])
        for invalid in [{**decision,'reason':''},{**decision,'source':before['documents'][0]['evidence_ids'][0]},{**decision,'multiplier':'100'}]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                reconcile(target,[primary],output_dir=self.root/'bad',start_date='2026-03-01',end_date='2026-03-31',report_decisions={'0400':invalid})

    def test_report_total_correction_does_not_hide_arithmetic_error(self):
        primary=self.root/'act.docx';document(primary,'0400','1200,00')
        target=self.root/'bad-total.pdf';report(target,[('0400','Услуги','1200,00')],'неразборчиво')
        before=reconcile(target,[primary],output_dir=self.root/'before',start_date='2026-03-01',end_date='2026-03-31')
        source=before['total']['report_evidence_id'];self.assertTrue(source)
        for raw,expected in [('1200','Совпадает'),('1300','Недостаточно данных')]:
            after=reconcile(target,[primary],output_dir=self.root/raw,start_date='2026-03-01',end_date='2026-03-31',report_decisions={'total':{'source':source,'raw':raw,'reason':'Проверен итог','multiplier':'1'}})
            self.assertEqual(after['total']['status'],expected)
            if raw=='1300':self.assertIn('Итог КФЗ не равен',str(after['total']['reasons']))


if __name__=='__main__':unittest.main()
