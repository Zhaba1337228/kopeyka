"""Portable, formula-safe workbook of a reconciliation and its evidence."""
from io import BytesIO
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from .review import build_review, location


def safe(value):
    if value is None:return 'Не определено'
    if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@')):
        return "'"+value
    return value


def workbook_bytes(result,version,created):
    book=Workbook(); book.remove(book.active)
    review=build_review(result)
    def sheet(name,headers,rows):
        ws=book.create_sheet(name);ws.append(headers)
        for row in rows:ws.append([safe(v) for v in row])
        ws.freeze_panes='A2';ws.auto_filter.ref=ws.dimensions
        for cell in ws[1]:cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='285D49')
        for col in ws.columns:
            letter=col[0].column_letter
            ws.column_dimensions[letter].width=min(65,max(18,max(len(str(c.value or '')) for c in col)+2))
            for c in col:c.alignment=Alignment(vertical='top',wrap_text=True)
        return ws
    summary=[['Версия',version],['Дата',created],['Договор',result['context']['contract']],['Этап',result['context'].get('stage')],
             ['Период',result['context']['start_date']+' — '+result['context']['end_date']],['Заключение',review['conclusion']],
             ['Область проверки',review['scope']],['Совпавшие статьи',review['counts']['matched']],
             ['Статьи с расхождением',review['counts']['mismatch']],['Неполностью проверенные статьи',review['counts']['unverified']],
             ['Итог по КФЗ, руб.',result['total']['reported']],['Подтверждено, руб.',result['total']['confirmed']],
             ['Разница, руб.',result['total']['difference']],['Статус итога',result['total']['status']]]
    summary += [['Ограничение',v] for v in result.get('limitations',[])]
    sheet('Сводка',['Показатель','Значение'],summary)
    sheet('Статьи',['Код','Статья','КФЗ, руб.','Документы, руб.','Разница, руб.','Статус','Причина','Источники'],
          [[r['code'],r['title'],r['reported'],r['confirmed'],r['difference'],r['status'],'; '.join(r['reasons']),'; '.join(([r['report_evidence_id']] if r.get('report_evidence_id') else [])+r['evidence_ids'])] for r in result['rows']])
    sheet('Другие показатели',['Показатель','Единица','КФЗ','Подтверждено','Разница','Статус','Причина','Источники'],
          [[r['title'],r['unit'],r['reported'],r['confirmed'],r['difference'],r['status'],'; '.join(r['reasons']),'; '.join(r.get('report_evidence_ids',[])+r['evidence_ids'])] for r in result.get('indicators',[])])
    sheet('Источники',['Идентификатор','Документ и место','Фрагмент','Исходное значение','Множитель','Подтверждённое значение','Ручное подтверждение','Единица','Формула источника','Ячейки расчёта'],
          [[key,location(ev),ev.get('fragment_text') or ev.get('text'),ev.get('raw_value'),ev.get('multiplier'),ev.get('value'),ev.get('review_reason',''),ev.get('unit',''),ev.get('formula',''),'; '.join(f"{r['sheet']}!{r['cell']}: {r['raw']}" for r in ev.get('formula_sources',[]))] for key,ev in result['evidence'].items()])
    sheet('Документы и решения',['Файл','Статья','Сумма, руб.','Статус','Причина','Основание ручного решения'],
          [[d['file'],'Детальные показатели' if d.get('indicator_only') else d.get('code'),d.get('amount'),d['status'],d.get('reason'),d.get('decision',{}).get('reason','')] for d in result['documents']])
    output=BytesIO();book.save(output);return output.getvalue()
