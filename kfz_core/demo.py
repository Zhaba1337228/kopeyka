"""Entirely synthetic public demonstration data; no source case files are read."""
from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED
import fitz
from docx import Document


def demo_bundle():
    contract='DEMO-2026/01'
    def act(code,lines):
        doc=Document()
        for text in [f'АКТ № DEMO-{code}',f'Договор {contract}','Дата 15.03.2026',
                     'Исполнитель: Учебная организация (вымышленные данные)',f'Статья калькуляции: {code}',*lines]:
            doc.add_paragraph(text)
        stream=BytesIO();doc.save(stream);return stream.getvalue()
    with fitz.open() as pdf:
        page=pdf.new_page(width=650,height=500)
        page.insert_font(fontname='demo',fontbuffer=fitz.Font('cjk').buffer)
        def text(x,y,value,size=12):page.insert_text((x,y),value,fontname='demo',fontsize=size)
        text(30,35,'КОПЕЙКА — ВЫМЫШЛЕННЫЙ УЧЕБНЫЙ ПРИМЕР',14)
        text(30,65,'Карточка фактических затрат');text(30,93,'Договор '+contract)
        text(30,121,'4 этап, 2026 г.')
        for x,label in [(30,'Код статьи'),(150,'Наименование'),(500,'Сумма, руб.')]:text(x,155,label)
        for i,(code,title,value) in enumerate([('0400','Учебные услуги','100000,00'),('0500','Услуги с НДС','1200,00'),
                                              ('0700','Дополнительные услуги','500,00'),('0800','Общепроизводственные расходы','1500,00')]):
            for x,label in [(30,code),(150,title),(500,value)]:text(x,185+i*32,label)
        text(30,325,'Итого 103200,00')
        report=pdf.tobytes()
    first=BytesIO()
    with ZipFile(first,'w',ZIP_DEFLATED) as archive:
        archive.writestr('Акт_0400.docx',act('0400',['Единица измерения: тыс. руб.','Итого: 100,00']))
        archive.writestr('Акт_0500.docx',act('0500',['Стоимость без НДС: 1000,00 руб.','Сумма НДС: 200,00 руб.','Итого к оплате: 1200,00 руб.']))
        archive.writestr('Акт_0800.docx',act('0800',['Итого к оплате: 1400,00 руб.']))
    package=BytesIO()
    with ZipFile(package,'w',ZIP_DEFLATED) as archive:
        archive.writestr('01_Учебная_КФЗ.pdf',report)
        archive.writestr('02_Комплект_первой_сверки.zip',first.getvalue())
        archive.writestr('03_Ответ_коллеги.docx',act('0700',['Итого: 500,00 руб.']))
        archive.writestr('ИНСТРУКЦИЯ.txt','''Это вымышленные открытые данные для демонстрации, не документы заказчика.
Распакуйте этот внешний архив.
1. Загрузите 01_Учебная_КФЗ.pdf в поле карточки.
2. Загрузите 02_Комплект_первой_сверки.zip в подтверждающие документы.
3. Договор DEMO-2026/01, период 01.01.2026–31.05.2026.
4. Ожидается: 0400 — 100 000,00, 0500 — 1 200,00, обе совпадают.
   0700 — недостаточно данных; 0800 — расхождение 100,00.
5. Выберите 0700, подготовьте запрос, затем добавьте 03_Ответ_коллеги.docx.
6. Новая версия: 0700 совпадает; по 0800 всё ещё расхождение 100,00.
   Итог КФЗ 103 200,00; подтверждено 103 100,00; разница 100,00.
7. Откройте прежнюю версию и скачайте заключение TXT/Excel.
Не загружайте внешний архив целиком вместо комплекта первой сверки.
''')
    return package.getvalue()
