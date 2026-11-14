"""Read explicitly coded report rows, retaining coordinates and ambiguities."""
import re

import fitz

from .articles import title_for
from .extract import make_evidence, ocr_image
from .money import AMOUNT_PATTERN, amount_text, scaled_money, resolve_units
from .metadata import contracts_in


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
    coded = [(line, re.match(r'^\s*(\d{4})\b\s*(.*)', line['text'])) for line in lines]
    coded = [(line, match) for line, match in coded if match and match[1] != str(year)]
    # An explicit row-table header distinguishes article codes from arbitrary numbers.
    headings = [line for line in lines if re.search(r'код\s+(?:статьи|затрат)', line['text'], re.I)]
    amount_headers = [word for line in headings for word in line['words'] if re.fullmatch(r'сумма[,:(]?', word[4], re.I)]
    if not coded or len(amount_headers) != 1:
        return None
    amount_left = amount_headers[0][0] - 5
    contracts = contracts_in(text)
    candidate = {'id': f'p{page}-table', 'label': f'{stage} этап, {year} г. (страница {page}, статьи по строкам)',
                 'page': page, 'year': year, 'stage': stage, 'contract': next(iter(contracts)) if len(contracts)==1 else None,
                 'contract_text': text, 'values': {}, 'evidence': {}, 'errors': [], 'column_errors': {}, 'articles': {}}
    first_y = min(line['bbox'][1] for line, _ in coded)
    header = '\n'.join(line['text'] for line in lines if line['bbox'][1] < first_y)
    multiplier, unit_error = resolve_units(header)
    if unit_error:
        candidate['errors'].append(unit_error)
    for line, match in coded:
        code = match[1]
        value_text = ' '.join(word[4] for word in line['words'] if word[0] >= amount_left)
        amounts = list(AMOUNT_PATTERN.finditer(value_text))
        title = ' '.join(word[4] for word in line['words'][1:] if word[0] < amount_left).strip(' |:;-')
        candidate['articles'][code] = title_for(code, title)
        if code in candidate['values']:
            candidate['values'][code] = None
            candidate['evidence'].pop(code, None)
            candidate['column_errors'][code] = 'Код статьи встречается в нескольких строках; требуется уточнение'
            continue
        candidate['values'][code] = None
        if len(amounts) != 1 or unit_error:
            candidate['column_errors'][code] = 'Нет единственной денежной суммы в строке статьи'
            continue
        value = amount_text(scaled_money(amounts[0][0],multiplier))
        candidate['values'][code] = value
        ev = make_evidence(path, doc_id, dict(line, page=page), value, f'КФЗ · статья {code}', f'{candidate["id"]}-{code}')
        ev['report'] = True
        candidate['evidence'][code] = ev
    totals = [line for line in lines if re.match(r'^\s*(?:итого|всего)\b', line['text'], re.I)]
    candidate['values']['total'] = None
    if len(totals) == 1 and len(AMOUNT_PATTERN.findall(totals[0]['text'])) == 1 and not unit_error:
        value = amount_text(scaled_money(AMOUNT_PATTERN.findall(totals[0]['text'])[0],multiplier))
        candidate['values']['total'] = value
        ev = make_evidence(path, doc_id, dict(totals[0], page=page), value, 'Итог КФЗ', f'{candidate["id"]}-total')
        ev['report'] = True
        candidate['evidence']['total'] = ev
    else:
        candidate['errors'].append('Не найден единственный итог КФЗ; сумма строк не подставляется вместо отчётного итога')
    return candidate
