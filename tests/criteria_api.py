"""HTTP proof of manual recovery, immutable provenance, exports and public demo."""
import argparse
import sys
import tempfile
from pathlib import Path
from io import BytesIO
from zipfile import ZipFile
import requests
from docx import Document
from openpyxl import load_workbook

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_articles import report

parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:8506');args=parser.parse_args()
client=requests.Session();client.get(args.url).raise_for_status()
with tempfile.TemporaryDirectory() as folder:
    folder=Path(folder);report_path=folder/'report.pdf';doc_path=folder/'review.docx'
    report(report_path,[('0400','Услуги','100000,00')],'100000,00')
    doc=Document()
    for line in ['АКТ № REVIEW-0400','Договор неразборчив','Дата 15.03.2026','Исполнитель: Учебная организация','Статья 0400',
                 'Единица измерения: тыс. руб.','Итого: 100,00 руб.']:doc.add_paragraph(line)
    doc.save(doc_path)
    response=client.post(args.url+'/api/check',data={'contract':'24/03/15-01','start':'2026-01-01','end':'2026-05-31'},
                         files=[('report',(report_path.name,report_path.read_bytes())),('documents',(doc_path.name,doc_path.read_bytes()))]);response.raise_for_status()
    before=response.json();sid=before['session'];document=before['result']['documents'][0]
    assert document['status']=='ambiguous' and document['amount_candidates']
    original=f'/api/session/{sid}/documents/{document["id"]}/original'
    assert client.get(args.url+original).content==doc_path.read_bytes()
    assert requests.get(args.url+original).status_code==404
    decision={'action':'include','code':'0400','reason':'Единицы и договор проверены по источнику',
              'amount_source':document['amount_candidates'][0]['id'],'amount_raw':'100,00','amount_multiplier':'1000',
              'metadata':{'contract':'24/03/15-01'}}
    response=client.post(args.url+f'/api/session/{sid}/recalculate',json={'document_id':document['id'],'decision':decision});response.raise_for_status()
    after=response.json();assert after['result']['total']['status']=='Совпадает'
    assert after['result']['total']['confirmed']=='100000.00'
    ref=after['result']['evidence'][after['result']['rows'][0]['evidence_ids'][0]]
    assert ref['manual'] and ref['raw_value']=='100.00' and ref['multiplier']=='1000'
    assert client.get(args.url+f'/api/session/{sid}').json()['result']['rows']==before['result']['rows']
    xlsx_url=args.url+f'/api/session/{after["session"]}/conclusion.xlsx'
    exported=client.get(xlsx_url);exported.raise_for_status();book=load_workbook(BytesIO(exported.content),data_only=False)
    assert len(book.sheetnames)==5
    assert any('100000.00'==row[1].value for row in book['Сводка'])
    assert requests.get(xlsx_url).status_code==404
    conclusion=client.get(args.url+f'/api/session/{after["session"]}/conclusion').text
    assert 'ОБЩИЙ ИТОГ' in conclusion and '100 000,00' in conclusion and 'Статьи: совпадают 1' in conclusion
    bad={**decision,'amount_source':'another-source'}
    assert client.post(args.url+f'/api/session/{after["session"]}/recalculate',json={'document_id':document['id'],'decision':bad}).status_code==400
    assert client.get(args.url+f'/api/session/{after["session"]}').json()['result']['total']['status']=='Совпадает'

demo=client.get(args.url+'/api/demo');demo.raise_for_status()
with ZipFile(BytesIO(demo.content)) as archive:
    files={name:archive.read(name) for name in archive.namelist()}
pdf=next((k,v) for k,v in files.items() if k.endswith('.pdf'))
primary=next((k,v) for k,v in files.items() if k.endswith('.zip'))
reply=next((k,v) for k,v in files.items() if k.endswith('.docx'))
response=client.post(args.url+'/api/check',data={'contract':'DEMO-2026/01','start':'2026-01-01','end':'2026-05-31'},files=[('report',pdf),('documents',primary)])
response.raise_for_status();first=response.json();assert first['result']['context']['stage']==4
rows={r['code']:r for r in first['result']['rows']}
assert rows['0400']['confirmed']=='100000.00' and rows['0500']['confirmed']=='1200.00'
assert rows['0700']['status']=='Недостаточно данных' and rows['0800']['difference']=='100.00'
response=client.post(args.url+f'/api/session/{first["session"]}/documents',files=[('documents',reply)])
response.raise_for_status();second=response.json()
assert second['result']['total']['confirmed']=='103100.00' and second['result']['total']['difference']=='100.00'
assert next(r for r in second['comparison']['rows'] if r['code']=='0700')['resolved']
print('PASS manual amount/units/metadata recovery; original download ownership; immutable parent; Excel/TXT; invalid source isolation; public demo and reply')
