"""Find monetary cells by row labels and column headings, never sheet addresses."""
import re
from collections import defaultdict
from openpyxl.utils.cell import coordinate_from_string, column_index_from_string
from .money import decimal_value


def normalized(text):
    return re.sub(r'\s+', ' ', str(text).lower().replace('ё', 'е')).strip()


def financial_field(label, context=''):
    text = normalized(label)
    contribution = bool(re.search(r'взнос', text + ' ' + normalized(context)))
    if re.search(r'база|аванс|справочно|удержан|к выплате', text):
        return None
    salary = bool(re.search(r'зарплат|заработн|оплат[ау]\s+труда', text))
    if contribution or salary or re.search(r'доплат|резерв', text):
        prefix = '0300' if contribution else '0200'
        extra = bool(re.search(r'дополнительн|доплат', text))
        reserve = bool(re.search(r'резерв', text))
        if extra and reserve:
            return prefix + '.extra_reserve'
        if reserve:
            return prefix + '.reserve'
        if re.search(r'основн', text):
            return prefix + '.main'
        if extra:
            return prefix + '.extra'
        if contribution and re.search(r'начислен|всего|итого', text + ' ' + normalized(context)):
            return prefix + '.total'
    return None


def amount_heading(text):
    text = normalized(text)
    if re.search(r'база|ставка|процент|количество|код|дата|номер|аванс|удержан', text):
        return False
    return bool(re.search(r'^(?:сумма|стоимость|начислено|начисленные|всего начислено|итого начислено)\b', text)
                or financial_field(text)) and not re.search(r'\d', text)


def numeric(value):
    if isinstance(value, bool):
        return False
    try:
        decimal_value(value)
        return True
    except (ValueError, ArithmeticError):
        return False


def spreadsheet_cells(extracted):
    """Yield candidates with exact source cells and the labels that selected them.

    Supports row-oriented registers and amounts below explicit column headings.
    Multiple monetary columns are retained as candidates instead of guessed away.
    """
    for name, cells in extracted['sheets'].items():
        rows = defaultdict(list)
        headings = defaultdict(list)
        for address, value in cells.items():
            column, row = coordinate_from_string(address)
            col = column_index_from_string(column)
            rows[row].append((col, address, value))
            if isinstance(value, str) and amount_heading(value):
                headings[col].append((row, address, value))
        for row, items in sorted(rows.items()):
            items.sort()
            for col, address, raw in items:
                formula = isinstance(raw, str) and raw.startswith('=')
                if not numeric(raw) and not formula:
                    continue
                above = sorted((h for h in headings[col] if h[0] < row), reverse=True)
                header = above[0] if above else None
                labels = [(c, a, str(v)) for c, a, v in items
                          if isinstance(v, str) and not v.startswith('=') and not numeric(v)]
                # Do not use commentary to the right when the row has a label to the left.
                left = [v for v in labels if v[0] < col]
                label = left[-1][2] if left else (labels[0][2] if labels else '')
                if not header and not re.search(r'итого|всего|к оплате|сумма|стоимость', normalized(label)):
                    continue
                context = header[2] if header else ''
                field = financial_field(label, context) or financial_field(context)
                excluded = bool(re.search(r'база|аванс|справочно|удержан|ставка', normalized(label)))
                if excluded:
                    field = None
                text = f'{label} | {raw}' + (f' · {context}' if context else '')
                label_cell=left[-1][1] if left else (labels[0][1] if labels else None)
                yield {'line': {'text': text, 'sheet': name, 'cell': address,
                               'label_cell':label_cell,'header_cell':header[1] if header else None}, 'raw': raw,
                       'local': label + '\n' + context, 'field': field, 'formula': formula,
                       'excluded': excluded, 'label': label or context,
                       'header': header[1] if header else None}


def component_plan(candidates):
    """Select disjoint semantic components; overlapping/partial splits need review."""
    fields = defaultdict(list)
    for candidate in candidates:
        if candidate.get('field'):
            fields[candidate['field']].append(candidate)
    parents = {field.split('.')[0] for field in fields}
    if len(parents) != 1:
        return None, None, None
    code = next(iter(parents))
    parts = {key.split('.')[1]: values for key, values in fields.items()}
    if any(len(values) != 1 for values in parts.values()):
        return code, None, 'Несколько строк одного показателя: уточните итог и детализацию'
    if 'main' in parts and 'extra_reserve' in parts and not ({'extra', 'reserve'} & parts.keys()):
        chosen = [parts['main'][0], parts['extra_reserve'][0]]
    elif {'main', 'extra', 'reserve'} <= parts.keys() and 'extra_reserve' not in parts:
        chosen = [parts[key][0] for key in ('main', 'extra', 'reserve')]
    elif 'total' in parts and not (parts.keys() - {'total'}):
        chosen = parts['total']
    elif code == '0200' and {'main','extra'} <= parts.keys() and 'reserve' not in parts:
        # Separate additional pay does not establish whether the reserve is absent.
        return code, None, 'Не определена полнота начислений: нужен итог или отдельный резерв'
    else:
        return code, None, 'Не определена полная структура начислений; подтвердите итог по источнику'
    return code, chosen, None
