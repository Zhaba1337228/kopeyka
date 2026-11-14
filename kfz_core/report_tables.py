"""Read explicitly coded report rows, retaining coordinates and ambiguities."""
import re

import fitz

from .articles import title_for
from .extract import make_evidence, ocr_image
from .money import AMOUNT_PATTERN, amount_text, scaled_money, resolve_units, quantity_text
from .metadata import contracts_in
from .tabular import financial_field


def word_lines(words):
    """Group table cells on the same baseline, even across PDF text blocks."""
    groups = []
    for word in sorted(words, key=lambda w: ((w[1]+w[3])/2, w[0])):
        middle = (word[1]+word[3])/2
        height = word[3]-word[1]
        group = next((g for g in reversed(groups) if abs(g['y']-middle) < max(2, height*.4)), None)
        if group is None:
            group = {'y': middle, 'words': []}
            groups.append(group)
        group['words'].append(word)
    result = []
    for group in groups:
        ordered = sorted(group['words'], key=lambda w: w[0])
        result.append({'text': ' '.join(w[4] for w in ordered), 'words': ordered,
                       'bbox': [min(w[0] for w in ordered), min(w[1] for w in ordered),
                                max(w[2] for w in ordered), max(w[3] for w in ordered)]})
    return result


def table_rows(path, page, image, scale, year, doc_id, *, use_ocr=False):
    if use_ocr:
        data = ocr_image(image, psm=6)
        words = [(data['left'][i]/scale, data['top'][i]/scale,
                  (data['left'][i]+data['width'][i])/scale,
                  (data['top'][i]+data['height'][i])/scale, text)
                 for i, text in enumerate(data['text']) if text.strip()]
    elif path.suffix.lower() == '.pdf':
        with fitz.open(path) as document:
            words = document[page-1].get_text('words')
    else:
        return None
    lines = word_lines(words)
    text = '\n'.join(line['text'] for line in lines)
    stages = set(re.findall(r'(\d{1,3})\s*(?:этап|зтап)\s*[,;:]?\s*' + str(year), text, re.I))
    if len(stages) != 1:
        return None
    stage = int(next(iter(stages)))
    # An explicit row-table header distinguishes article codes from arbitrary numbers.
    headings = [line for line in lines if re.search(r'код\s+(?:статьи|затрат)', line['text'], re.I)]
    amount_headers = [word for line in headings for word in line['words'] if re.fullmatch(r'сумма[,:(]?', word[4], re.I)]
    if len(amount_headers) != 1:
        return None
    amount_left = amount_headers[0][0] - 5
    header_line = next(line for line in headings if amount_headers[0] in line['words'])
    boundaries = [word[0]-5 for word in header_line['words'] if re.fullmatch(r'код|наименование|описание|примечание|показатель',word[4],re.I)]
    amount_right = min((x for x in boundaries if x > amount_left), default=float('inf'))
    code_header = next((w for w in header_line['words'] if w[4].lower()=='код'), None)
    if code_header is None:return None
    coded = []
    for line in lines:
        if line['bbox'][1] <= header_line['bbox'][1]:continue
        codes = [w for w in line['words'] if re.fullmatch(r'\d{4}',w[4]) and abs(w[0]-code_header[0]) < 25 and w[4]!=str(year)]
        if len(codes)==1:coded.append((line,codes[0]))
    if not coded:return None
    contracts = contracts_in(text)
    candidate = {'id': f'p{page}-table', 'label': f'{stage} этап, {year} г. (страница {page}, статьи по строкам)',
                 'page': page, 'year': year, 'stage': stage, 'contract': next(iter(contracts)) if len(contracts)==1 else None,
                 'contract_text': text, 'values': {}, 'evidence': {}, 'errors': [], 'column_errors': {}, 'articles': {}}
    first_y = min(line['bbox'][1] for line, _ in coded)
    header = '\n'.join(line['text'] for line in lines if line['bbox'][1] < first_y)
    multiplier, unit_error = resolve_units(header)
    if unit_error:
        candidate['errors'].append(unit_error)
    for line, code_word in coded:
        code = code_word[4]
        value_text = ' '.join(word[4] for word in line['words'] if amount_left <= word[0] < amount_right and word != code_word)
        amounts = list(AMOUNT_PATTERN.finditer(value_text))
        title = ' '.join(word[4] for word in line['words'] if word != code_word and not amount_left <= word[0] < amount_right).strip(' |:;-')
        candidate['articles'][code] = title_for(code, title)
        if code in candidate['values']:
            candidate['values'][code] = None
            candidate['evidence'].pop(code, None)
            candidate['column_errors'][code] = 'Код статьи встречается в нескольких строках; требуется уточнение'
            continue
        candidate['values'][code] = None
        if len(amounts) != 1 or unit_error:
            candidate['column_errors'][code] = 'Нет единственной денежной суммы в строке статьи'
            ev=make_evidence(path,doc_id,dict(line,page=page),0,f'КФЗ · статья {code}',f'{candidate["id"]}-{code}')
            ev.update(report=True,value=None);candidate['evidence'][code]=ev
            continue
        value = amount_text(scaled_money(amounts[0][0],multiplier))
        candidate['values'][code] = value
        ev = make_evidence(path, doc_id, dict(line, page=page), value, f'КФЗ · статья {code}', f'{candidate["id"]}-{code}')
        ev['report'] = True
        candidate['evidence'][code] = ev
    totals = [line for line in lines if re.match(r'^\s*(?:итого|всего)\b', line['text'], re.I) and financial_field(line['text']) not in ('labor.total','material.total')]
    candidate['values']['total'] = None
    if len(totals) == 1 and len(AMOUNT_PATTERN.findall(totals[0]['text'])) == 1 and not unit_error:
        value = amount_text(scaled_money(AMOUNT_PATTERN.findall(totals[0]['text'])[0],multiplier))
        candidate['values']['total'] = value
        ev = make_evidence(path, doc_id, dict(totals[0], page=page), value, 'Итог КФЗ', f'{candidate["id"]}-total')
        ev['report'] = True
        candidate['evidence']['total'] = ev
    else:
        candidate['errors'].append('Не найден единственный итог КФЗ; сумма строк не подставляется вместо отчётного итога')
        if len(totals)==1:
            ev=make_evidence(path,doc_id,dict(totals[0],page=page),0,'Итог КФЗ',f'{candidate["id"]}-total')
            ev.update(report=True,value=None);candidate['evidence']['total']=ev
    candidate['indicators'] = []
    fields = {}
    for line in lines:
        field = financial_field(line['text'])
        if not field or any(line is coded_line for coded_line,_ in coded):continue
        matches = AMOUNT_PATTERN.findall(line['text'])
        if len(matches)!=1:continue
        if field == 'labor.total' and not re.search(r'чел\.?\s*[-.]?\s*мес|человеко.?месяц',line['text'],re.I):continue
        value = amount_text(scaled_money(matches[0],1 if field=='labor.total' else multiplier)) if field=='labor.total' or multiplier is not None else None
        if field=='labor.total':value=quantity_text(matches[0])
        if field in fields:
            fields[field]['reported']=None
            continue
        ident = {'labor.total':'detail-3','material.total':'detail-4'}.get(field,'detail-'+field)
        ev = make_evidence(path,doc_id,dict(line,page=page),value or 0,line['text'],f'{candidate["id"]}-{ident}')
        ev.update(report=True,value=value,unit='чел.-мес.' if field=='labor.total' else '₽');candidate['evidence'][ident]=ev
        metric={'code':ident,'title':line['text'][:120],'unit':'чел.-мес.' if field=='labor.total' else '₽',
                'reported':value,'report_evidence_ids':[ev['id']],'field':field}
        candidate['indicators'].append(metric);fields[field]=metric
    return candidate
