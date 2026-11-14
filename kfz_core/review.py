"""Evidence-based explanations and version comparisons; no network or language model."""
from collections import Counter
from decimal import Decimal


def rub(value):
    if value is None:
        return 'не определено'
    return f'{Decimal(str(value)):,.2f}'.replace(',', ' ').replace('.', ',') + ' ₽'


def category(document):
    reason = document.get('reason', '').lower()
    if document.get('duplicate_of') or 'повторный документ' in reason or 'копия по sha' in reason:
        return 'Дублирование'
    if 'другой период' in reason:
        return 'Неверный период'
    if 'другой договор' in reason:
        return 'Другой договор'
    if document.get('status') == 'error':
        return 'Ошибка чтения'
    if document.get('status') == 'ambiguous':
        if 'единиц' in reason:
            return 'Единицы измерения'
        if 'сумм' in reason or 'итог' in reason:
            return 'Неоднозначная сумма'
        return 'Неоднозначная связь'
    if document.get('decision'):
        return 'Ручное решение'
    return 'Служебный файл' if document.get('status') == 'excluded' else 'Подтверждённый источник'


def location(evidence):
    parts = [evidence.get('file', 'Источник')]
    for key, name in [('page', 'стр.'), ('sheet', 'лист'), ('cell', 'ячейка'), ('table', 'таблица'), ('row', 'строка'), ('paragraph', 'абзац')]:
        if evidence.get(key) is not None:
            parts.append(f'{name} {evidence[key]}')
    return ', '.join(parts)


def build_review(result):
    counts = Counter(r['status'] for r in result['rows'])
    matched, mismatch, unknown = (counts[s] for s in ('Совпадает', 'Расхождение', 'Недостаточно данных'))
    detail_counts = Counter(r['status'] for r in result.get('indicators',[]))
    if mismatch or detail_counts['Расхождение']:
        conclusion = 'В проверенных статьях найдены расхождения. Достоверность отчёта в проверяемом объёме не подтверждена.'
    elif unknown or detail_counts['Недостаточно данных'] or result.get('total', {}).get('status') == 'Недостаточно данных':
        conclusion = 'Данных недостаточно для заключения по всем показателям отчёта.'
    elif result.get('total', {}).get('status') == 'Расхождение':
        conclusion = 'Суммы статей согласуются с документами, но общий итог отчёта требует уточнения.'
    else:
        conclusion = 'Суммы проверяемых статей согласуются с принятыми документами. Это не подтверждает полноту комплекта и остальные показатели отчёта.'
    excluded = [{'id': d.get('id'), 'file': d['file'], 'category': category(d), 'reason': d.get('reason', ''),
                 'status': d['status'], 'code': d.get('code'), 'evidence_ids': d.get('evidence_ids', [])}
                for d in result['documents'] if d['status'] != 'accepted']
    rows = {}
    context = result['context']
    for row in result['rows']:
        complete = row['status'] != 'Недостаточно данных'
        delta = Decimal(row['difference']) if row.get('difference') is not None else None
        if not complete:
            statement = f"По статье {row['code']} подтверждена только часть расходов: {rub(row['confirmed'])}. Окончательный вывод невозможен."
            cause = 'Для вывода нужно устранить перечисленные пробелы в данных. Предварительная разница не означает ошибку отчёта.'
        elif delta == 0:
            statement = f"По статье {row['code']} суммы КФЗ и принятых документов совпадают: {rub(row['confirmed'])}."
            cause = 'В пределах принятых документов расхождение суммы не обнаружено; полнота комплекта отдельно не подтверждена.'
        else:
            direction = 'больше' if delta is not None and delta > 0 else 'меньше'
            statement = f"По статье {row['code']} в КФЗ указано на {rub(abs(delta))} {direction}, чем подтверждают принятые документы."
            cause = 'Причина расхождения пока не установлена. Нужны регистр формирования статьи и проверка полноты подтверждающих документов.'
        tags = []
        if not complete:
            tags.append('Недостаточно данных')
        if any('Нет месячных документов' in reason for reason in row.get('reasons', [])):
            tags.append('Пропущенный месячный документ')
        if complete and delta:
            tags.append('Расхождение суммы')
        if not tags:
            tags.append('Совпадение сумм')
        sources = [{'id': ref, 'location': location(result['evidence'][ref]), 'value': result['evidence'][ref]['value']}
                   for ref in row['evidence_ids'] if ref in result['evidence']]
        report_ref = row.get('report_evidence_id')
        report_source = location(result['evidence'][report_ref]) if report_ref in result['evidence'] else 'Источник КФЗ не распознан'
        actions = list(row.get('reasons', [])) if not complete else []
        if row['status'] != 'Совпадает':
            actions += ['Запросить регистр формирования статьи и сверить состав расходов.',
                        'Проверить документы по договору и периоду; при получении ответа добавить файлы и пересчитать.']
        subject = f"Уточнение статьи {row['code']} по договору {context['contract']}"
        text = '\n'.join([
            'Коллеги, добрый день!', '',
            f"Проверяем КФЗ по договору {context['contract']} за период {context['start_date']} — {context['end_date']}.",
            f"Статья {row['code']} «{row['title']}».",
            f"В КФЗ: {rub(row['reported'])}. По принятым документам: {rub(row['confirmed'])}.",
            f"Разница (КФЗ минус документы): {rub(row.get('difference'))}.",
            f"Статус: {row['status']}.", '', statement, cause, '',
            'Подтверждающие источники:',
            *[f"— {s['location']}: {rub(s['value'])}" for s in sources],
            f'Значение отчёта: {report_source}.', '',
            *(['Не хватает данных:', *[f'— {r}' for r in row.get('reasons', [])], ''] if not complete else []),
            ('Просьба предоставить регистр формирования статьи и уточнить состав первичных документов. '
             'При наличии недостающих документов направьте их для повторной сверки.' if row['status'] != 'Совпадает'
             else 'Просьба подтвердить полноту переданного комплекта документов.'),
            '', 'Причина ошибки не предполагается заранее; вывод ограничен проверенными показателями.'
        ])
        rows[row['code']] = {'statement': statement, 'cause': cause, 'categories': tags, 'sources': sources,
                             'report_source': report_source, 'actions': actions, 'subject': subject, 'request_text': text}
    return {'conclusion': conclusion, 'counts': {'matched': matched, 'mismatch': mismatch, 'unverified': unknown},
            'scope': 'Статьи в сверке: ' + ', '.join(r['code'] for r in result['rows']) + '. Полнота комплекта и показатели вне этой таблицы не подтверждены.',
            'indicator_counts':{'matched':detail_counts['Совпадает'],'mismatch':detail_counts['Расхождение'],'unverified':detail_counts['Недостаточно данных']},
            'rows': rows, 'document_issues': excluded}


def compare_results(before, after):
    previous = {r['code']: r for r in before['rows']}
    current = {r['code']: r for r in after['rows']}
    rows = []
    missing = {'reported': None, 'confirmed': None, 'difference': None, 'status': 'Нет статьи в версии'}
    for code in sorted(previous.keys() | current.keys()):
        old, row = previous.get(code, missing), current.get(code, missing)
        closed = code in previous and code in current and old['status'] != 'Совпадает' and row['status'] == 'Совпадает'
        change = str((Decimal(row['confirmed']) - Decimal(old['confirmed'])).quantize(Decimal('.01'))) if code in previous and code in current else None
        rows.append({'code': code, 'title': row.get('title', old.get('title', code)),
                     'before': {k: old.get(k) for k in ('reported', 'confirmed', 'difference', 'status')},
                     'after': {k: row.get(k) for k in ('reported', 'confirmed', 'difference', 'status')},
                     'confirmed_change': change,
                     'changed': any(old.get(k) != row.get(k) for k in ('reported', 'confirmed', 'difference', 'status')),
                     'resolved': closed})
    def identity(document):
        return document.get('id') or ('journal', document.get('file'), document.get('reason'), document.get('status'))
    old_docs = {identity(d): d for d in before['documents']}
    remaining = Counter(identity(d) for d in before['documents'])
    added, changed = [], []
    for document in after['documents']:
        key = identity(document)
        old = old_docs.get(key) if remaining[key] else None
        remaining[key] -= 1
        item = {k: document.get(k) for k in ('id', 'file', 'status', 'code', 'amount', 'reason')}
        item['category'] = category(document)
        if old is None:
            added.append(item)
        elif any(old.get(k) != document.get(k) for k in ('status', 'amount', 'code')):
            item['before_status'] = old['status']
            changed.append(item)
    return {'rows': rows, 'added_documents': added, 'changed_documents': changed,
            'before_total': before['total'], 'after_total': after['total']}
