"""Synthetic files for source-based manual recovery in the browser."""
import argparse
import sys
from pathlib import Path
from docx import Document

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_articles import report

parser=argparse.ArgumentParser()
parser.add_argument('--output',required=True,type=Path)
args=parser.parse_args()
args.output.mkdir(parents=True,exist_ok=True)
report(args.output/'report.pdf',[('0400','Проверяемые услуги','100000,00')],'100000,00')
doc=Document()
for line in ['АКТ № REVIEW-0400','Договор неразборчив','Дата 15.03.2026',
             'Исполнитель: Учебная организация','Статья 0400',
             'Единица измерения: тыс. руб.','Итого: 100,00 руб.']:
    doc.add_paragraph(line)
doc.save(args.output/'review.docx')
print('Synthetic review fixtures created')
