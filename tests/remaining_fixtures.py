"""Public synthetic fixtures for formulas and independent detail versioning."""
import argparse
import shutil
import sys
from pathlib import Path
from openpyxl import Workbook
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_remaining_criteria import RemainingCriteriaTests
from test_articles import report


def generate(output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    helper=RemainingCriteriaTests();helper.setUp()
    try:
        shutil.copyfile(helper.detailed_report(),output/'report.pdf')
        report(output/'ambiguous.pdf',[('0400','Услуги','1200,00 / 1300,00')],'1200,00')
        path=helper.doc('details',['Регистр № Д-1','Договор 24/03/15-01','Период: с 01.01.2026 по 31.05.2026',
                                    'Трудоёмкость, чел.-мес.: 3,50','Материальные затраты: 0,00 руб.'])
        shutil.copyfile(path,output/'details.docx')
        book=Workbook();sheet=book.active;sheet.title='Расходы'
        for row in [['Акт № Ф-1'],['Договор 24/03/15-01 от 09.04.2024'],['Дата акта: 15.03.2026'],
                    ['Исполнитель: Учебная организация'],['Статья калькуляции: 0400'],
                    ['Позиция','Сумма, руб.'],['Услуги',1000],['Материалы',200],['Итого','=SUM(B7:B8)']]:sheet.append(row)
        book.save(output/'formula.xlsx')
    finally:
        helper.doCleanups()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='work/remaining-fixtures')
    generate(parser.parse_args().output)
