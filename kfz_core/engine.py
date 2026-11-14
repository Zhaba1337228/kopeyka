from __future__ import annotations

import calendar
import json
import re
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

from .extract import extract, file_hash, make_evidence, render_evidence
from .money import MONEY_PATTERN, amount_text, money, unit_multiplier
from .report import ReportSelectionRequired, inspect_report
from .articles import KNOWN_TITLES, explicit_codes, title_for
from .amounts import read_amounts
from .coverage import verify_indicators
from .metadata import contracts_in, normalize_contract

MONTHS = ['январ', 'феврал', 'март', 'апрел', 'ма[йя]', 'июн', 'июл', 'август', 'сентябр', 'октябр', 'ноябр', 'декабр']
MONTH_LABELS = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь', 'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь']


def _normalize(value):
    return re.sub(r'\s+', ' ', value.casefold()).strip()


def _identity_key(record):
    return tuple(_normalize(str(record.get(field) or '')) for field in ('document_type', 'number', 'document_date', 'period_start', 'period_end', 'counterparty', 'contract'))


def _month_period(text):
    # An explicitly stated service/accounting period takes precedence over invoice date.
    for number, month in enumerate(MONTHS, 1):
        match = re.search(r'(?:период\s*[:\-]?|за)\s*(?:полный\s+)?' + month + r'[а-я]*\s+(20\d{2})', text, re.I)
        if match:
            year = int(match[1])
            return date(year, number, 1), date(year, number, calendar.monthrange(year, number)[1]), 'указанный период'
    match = re.search(r'(?:период\s*[:\-]?|с)\s*(\d{2}\.\d{2}\.20\d{2})\s*(?:по|[-–—])\s*(\d{2}\.\d{2}\.20\d{2})', text, re.I)
    if match:
        return _date(match[1]), _date(match[2]), 'указанный период'
    match = re.search(r'\b\d{2}\.\d{2}\.20\d{2}\b', text)
    if match:
        value = _date(match[0])
        return value, value, 'дата документа (период не указан)'
    return None, None, None


def _date(value):
    day, month, year = map(int, value.split('.'))
    return date(year, month, day)


def _classify(text, rules):
    stated = explicit_codes(text)
    if stated:
        return stated[0] if len(stated) == 1 else None
    normalized = _normalize(text)
    codes = [code for code, words in rules.items() if any(word.casefold() in normalized for word in words)]
    return codes[0] if len(codes) == 1 else None


def _metadata(extracted, rules):
    text = extracted['text']
    lines = text.splitlines()
    contracts = set(contracts_in(text))
    period_start, period_end, date_rule = _month_period(text)
    document_date_match = re.search(r'\b\d{2}\.\d{2}\.20\d{2}\b', text)
    document_date = _date(document_date_match[0]).isoformat() if document_date_match else None
    first = lines[0] if lines else ''
    number = re.search(r'(?:№\s*|документ\s*:?\s*|записка\s+|расходах\s+ИТ\s+|документ\s+)([A-Za-zА-Яа-я0-9]+(?:[-/][A-Za-zА-Яа-я0-9]+)*)', text, re.I)
    document_number = number[1].upper() if number else None
    # Avoid treating the literal title "Документ" as a serial number.
    if document_number in ('ДАТА', 'ДОГОВОР'):
        document_number = None
    if 'УНИВЕРСАЛЬНЫЙ ПЕРЕДАТОЧНЫЙ' in first.upper():
        document_type = 'УПД'
    elif 'СЧЕТ-АКТ' in first.upper() or 'СЧЁТ-АКТ' in first.upper():
        document_type = 'Счет-акт'
    elif 'АКТ' in first.upper():
        document_type = 'Акт'
    elif 'записка' in first.lower():
        document_type = 'Служебная записка'
    elif 'справка' in first.lower():
        document_type = 'Справка'
    else:
        document_type = first[:80]
    counterpart = None
    for i, line in enumerate(lines):
        if re.search(r'Исполнитель|Поставщик', line, re.I):
            counterpart = re.sub(r'^(?:Исполнитель|Поставщик)\s*[:\-]?\s*', '', line, flags=re.I).strip()
            if not counterpart and i + 1 < len(lines):
                counterpart = lines[i + 1].strip()
            break
    return {'contract': next(iter(contracts)) if len(contracts) == 1 else None, 'contracts': sorted(contracts), 'period_start': period_start.isoformat() if period_start else None, 'period_end': period_end.isoformat() if period_end else None, 'document_date': document_date, 'date_rule': date_rule, 'number': document_number, 'document_type': document_type, 'counterparty': counterpart or ('Внутренний документ' if extracted['kind'] in ('docx', 'xlsx') else None), 'code': _classify(text, rules)}


def _read_amounts(path, doc_id, extracted, meta):
    parsed = read_amounts(path, doc_id, extracted, meta)
    if parsed['reason']:
        raise ValueError(parsed['reason'])
    return parsed['amount'], [ev for ev in parsed['evidence'] if ev['id'] in parsed['evidence_ids']]


def _expected_months(start, end):
    result = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        result.append(f'{year:04d}-{month:02d}')
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return result


def reconcile(kfz_path, document_paths, *, output_dir=None, contract='24/03/15-01', start_date='2026-01-01', end_date='2026-05-31', report_row_id=None, decisions=None, progress=None):
    out = Path(output_dir) if output_dir else Path(tempfile.mkdtemp(prefix='kfz-'))
    out.mkdir(parents=True, exist_ok=True)
    progress = progress or (lambda fraction, message: None)
    decisions = decisions or {}
    contract = normalize_contract(contract)
    start, end = date.fromisoformat(str(start_date)), date.fromisoformat(str(end_date))
    if end < start:
        raise ValueError('Конец периода раньше начала')
    if start.year != end.year:
        raise ValueError('В первой версии период сверки должен находиться в одном календарном году')
    rules = json.loads((Path(__file__).resolve().parent.parent / 'config' / 'article_rules.json').read_text(encoding='utf-8'))
    progress(.02, 'Распознаём строку и графы КФЗ')
    candidates = inspect_report(kfz_path, out / 'cache', year=start.year)
    if len(candidates) > 1 and report_row_id is None:
        raise ReportSelectionRequired(candidates)
    selected = next((row for row in candidates if row['id'] == report_row_id), None) if report_row_id else candidates[0]
    if selected is None:
        raise ValueError('Выбранная строка КФЗ не найдена')
    articles = dict(selected.get('articles') or {code: title_for(code) for code in selected['values'] if code != 'total'})
    report_reasons = list(selected['errors'])
    if not selected.get('contract'):
        report_reasons.append('Не удалось прочитать договор в КФЗ')
    elif selected['contract'] != contract:
        report_reasons.append(f"В КФЗ договор {selected['contract']}, выбран {contract}")
    evidence = {ev['id']: ev for ev in selected['evidence'].values()}
    documents = []
    seen_hashes = {}
    paths = [Path(p) for p in document_paths]
    for index, path in enumerate(paths):
        progress(.12 + .68 * index / max(1, len(paths)), f'Читаем {path.name}')
        record = {'id': '', 'file': path.name, 'path': str(path.resolve()), 'status': 'ambiguous', 'reason': '', 'code': None, 'amount': None, 'evidence_ids': []}
        documents.append(record)
        try:
            digest = file_hash(path)
            record['id'], record['hash'] = digest[:20], digest
            if digest in seen_hashes:
                record['id'] = digest[:20] + f'-copy-{index}'
                record.update(status='excluded', reason=f"Точная копия по SHA-256: {seen_hashes[digest]['file']}", duplicate_of=seen_hashes[digest]['id'])
                continue
            seen_hashes[digest] = record
            extracted = extract(path, out / 'cache')
            if re.search(r'НАБОР ДАННЫХ ДЛЯ КЕЙСА', extracted['text'], re.I):
                record.update(status='excluded', reason='Описание набора данных; не подтверждающий документ')
                continue
            meta = _metadata(extracted, rules)
            record.update(meta)
            record['extracted_metadata'] = dict(meta)
            decision = decisions.get(record['id'], {})
            if decision and not str(decision.get('reason','')).strip():
                raise ValueError('Для ручного решения требуется причина')
            corrections = decision.get('metadata', {})
            if not isinstance(corrections,dict) or set(corrections)-{'contract','period_start','period_end','number','counterparty'}:
                raise ValueError('Недопустимые реквизиты ручного решения')
            for field,value in corrections.items():
                if not isinstance(value,str) or len(value)>300 or not value.strip():
                    raise ValueError('Проверьте реквизиты ручного решения')
                record[field] = normalize_contract(value) if field=='contract' else value.strip()
            if record.get('period_start') and record.get('period_end'):
                if date.fromisoformat(record['period_start']) > date.fromisoformat(record['period_end']):
                    raise ValueError('Период документа указан в обратном порядке')
            meta = record
            # Metadata controls inclusion even if the document contains a valid amount.
            exclusion = None
            if meta['contract'] and meta['contract'] != contract:
                exclusion = f"Другой договор: {meta['contract']} (проверяется {contract})"
            elif meta['period_start'] and (date.fromisoformat(meta['period_start']) < start or date.fromisoformat(meta['period_end']) > end):
                exclusion = f"Другой период: {meta['period_start']} — {meta['period_end']} (проверяется {start} — {end})"
            try:
                parsed = read_amounts(path, record['id'], extracted, record, decision)
            except Exception as error:
                if exclusion:
                    record.update(status='excluded', reason=exclusion + f'; сумма не прочитана: {error}')
                    continue
                raise
            record['amount'] = parsed['amount']
            record['evidence_ids'] = parsed['evidence_ids']
            record['amount_candidates'] = parsed['candidates']
            record['preview_evidence_ids'] = [ev['id'] for ev in parsed['evidence']]
            evidence.update({ev['id']: ev for ev in parsed['evidence']})
            declared = explicit_codes(extracted['text'])
            if exclusion:
                record.update(status='excluded', reason=exclusion)
            elif parsed['reason']:
                record.update(status='ambiguous',reason=parsed['reason'])
            elif len(declared) > 1 or (declared and record['code'] != declared[0]):
                record.update(status='ambiguous', code=None, reason='В документе несколько статей либо код противоречит распознанной форме; требуется решение')
            elif not record['contract']:
                record.update(reason='Не найден единственный договор; требуется проверка реквизитов')
            elif not record['period_start']:
                record.update(reason='Не найдена дата или период документа')
            elif not record['number']:
                record.update(reason='Не найден номер документа; требуется проверка')
            elif not record['counterparty']:
                record.update(reason='Не найден контрагент; требуется проверка реквизитов')
            elif not record['code']:
                record.update(reason='Назначение неоднозначно или отсутствует в словаре статей')
            elif record['code'] not in articles:
                record.update(reason='Код статьи документа не найден в КФЗ; требуется проверка привязки')
            else:
                record.update(status='accepted', reason=f"Договор, период и назначение подтверждены; {record['date_rule']}")
        except Exception as error:
            record.update(status='error', reason=f'Не удалось прочитать документ: {error}')
            if not record['id']:
                record['id'] = f'error-{index}'
    for record in documents:
        if record.get('code') and record['status'] != 'excluded' and record['code'] not in articles:
            articles[record['code']] = title_for(record['code'])
    # Same requisites with different amounts are a conflict, never silently deduplicated.
    semantic_groups = {}
    for record in documents:
        if record['status'] == 'accepted':
            key = _identity_key(record)
            semantic_groups.setdefault(key, []).append(record)
    for group in semantic_groups.values():
        if len(group) <= 1:
            continue
        if len({record['amount'] for record in group}) > 1:
            for record in group:
                record.update(status='ambiguous', reason='Конфликт: совпадают реквизиты, но указаны разные суммы')
        else:
            for record in group[1:]:
                record.update(status='excluded', reason=f"Повторный документ: совпадают тип, номер, период, контрагент, договор и сумма с {group[0]['file']}", duplicate_of=group[0]['id'])
    # A review decision has an explicit reason and can only act on readable amounts.
    for record in documents:
        decision = decisions.get(record['id'])
        if not decision:
            continue
        if not str(decision.get('reason', '')).strip():
            raise ValueError('Для ручного решения требуется причина')
        action = decision.get('action')
        original = {'status': record['status'], 'reason': record['reason'], 'code': record['code'],
                    'metadata':record.get('extracted_metadata'), 'candidates':record.get('amount_candidates',[])}
        if action == 'exclude':
            record.update(status='excluded', reason='Решение проверяющего: ' + decision['reason'])
        elif action == 'include':
            if record['amount'] is None or decision.get('code') not in articles:
                raise ValueError('Для включения необходимы прочитанная сумма и допустимая статья')
            if record.get('duplicate_of'):
                raise ValueError('Нельзя повторно включить дубликат; работайте с исходным документом')
            record.update(status='accepted', code=decision['code'], reason='Решение проверяющего: ' + decision['reason'])
        else:
            raise ValueError('Неизвестное ручное решение')
        record['decision'] = dict(decision)
        record['before_decision'] = original
    # User decisions cannot make the same business document count twice.
    reviewed_groups = {}
    for record in documents:
        if record['status'] == 'accepted':
            reviewed_groups.setdefault(_identity_key(record), []).append(record)
    if any(len(group) > 1 for group in reviewed_groups.values()):
        raise ValueError('Совпадают реквизиты нескольких включённых документов. Выберите один документ, остальные исключите с причиной.')
    # Rebuild after review: a corrected or excluded assignment must not leave a
    # phantom article in the totals merely because it was a prior suggestion.
    articles = dict(selected.get('articles') or {code: title_for(code) for code in selected['values'] if code != 'total'})
    for record in documents:
        if record.get('code') and record['status'] != 'excluded':
            articles.setdefault(record['code'], title_for(record['code']))
    rows = []
    for code, title in sorted(articles.items()):
        accepted = [r for r in documents if r['code'] == code and r['status'] == 'accepted']
        refs = [ref for r in accepted for ref in r['evidence_ids']]
        confirmed = sum((money(r['amount']) for r in accepted), Decimal(0))
        reported = selected['values'].get(code)
        reasons = list(report_reasons)
        if code in selected.get('column_errors', {}):
            reasons.append(selected['column_errors'][code])
        uncertain = [r for r in documents if r['status'] in ('error', 'ambiguous') and r['code'] in (None, code)]
        reasons.extend(f"{r['file']}: {r['reason']}" for r in uncertain)
        if not accepted and code not in ('0200', '0300') and (code not in KNOWN_TITLES or reported is None or money(reported) != 0):
            reasons.append('Нет принятых подтверждающих документов по статье; отсутствие файлов не считается нулевыми расходами')
        if code in ('0200', '0300'):
            present = {r['period_start'][:7] for r in accepted if r.get('period_start')}
            missing = [month for month in _expected_months(start, end) if month not in present]
            if missing:
                labels = [f'{MONTH_LABELS[int(v[5:]) - 1]} {v[:4]}' for v in missing]
                reasons.append('Нет месячных документов: ' + ', '.join(labels))
        if reported is None:
            reasons.append('Сумма КФЗ не распознана')
        difference = money(reported) - confirmed if reported is not None else None
        status = 'Недостаточно данных' if reasons else ('Совпадает' if difference == 0 else 'Расхождение')
        report_ref = selected['evidence'].get(code, {}).get('id')
        rows.append({'code': code, 'title': title, 'reported': reported, 'confirmed': amount_text(confirmed), 'difference': amount_text(difference) if difference is not None else None, 'status': status, 'reasons': reasons, 'evidence_ids': refs, 'report_evidence_id': report_ref, 'formula': ' + '.join(evidence[ref]['value'] for ref in refs) + ' = ' + amount_text(confirmed), 'partial': status == 'Недостаточно данных'})
    confirmed_total = sum((money(row['confirmed']) for row in rows), Decimal(0))
    reported_total = selected['values'].get('total')
    total_reasons = [f"{row['code']}: {reason}" for row in rows for reason in row['reasons']]
    if reported_total is None:
        total_reasons.append('Итог КФЗ не распознан')
    elif all(row['reported'] is not None for row in rows):
        sum_reported = sum((money(row['reported']) for row in rows), Decimal(0))
        if sum_reported != money(reported_total):
            total_reasons.append('Итог КФЗ не равен сумме проверяемых верхнеуровневых статей')
    total_diff = money(reported_total) - confirmed_total if reported_total is not None else None
    total = {'code': 'total', 'title': f'Итого за {start.year} год по этапу {selected["stage"]}', 'reported': reported_total, 'confirmed': amount_text(confirmed_total), 'difference': amount_text(total_diff) if total_diff is not None else None, 'status': 'Недостаточно данных' if total_reasons else ('Совпадает' if total_diff == 0 else 'Расхождение'), 'reasons': total_reasons, 'evidence_ids': [ref for row in rows for ref in row['evidence_ids']], 'report_evidence_id': selected['evidence'].get('total', {}).get('id'), 'formula': ' + '.join(row['confirmed'] for row in rows) + ' = ' + amount_text(confirmed_total), 'partial': bool(total_reasons)}
    progress(.85, 'Подготавливаем фрагменты источников')
    for ev in evidence.values():
        try:
            render_evidence(ev, out / 'evidence')
        except Exception as error:
            ev['render_error'] = str(error)
    result = {'context': {'contract': contract, 'start_date': str(start), 'end_date': str(end), 'stage': selected['stage'], 'year': start.year, 'report_row_id': selected['id'], 'report_row_label': selected['label']}, 'rows': rows, 'indicators': verify_indicators(selected,documents,evidence,rows,_expected_months(start,end)), 'total': total, 'documents': documents, 'evidence': evidence, 'candidate_rows': [{'id': c['id'], 'label': c['label']} for c in candidates], 'limitations': ['Полнота разовых актов без реестра не проверена. Вывод относится к принятым документам.', 'Статьи определяются по кодам в отчёте и документах. Неоднозначная привязка и непрочитанные суммы требуют уточнения; трудоёмкость не сверяется.', 'C6 подтверждает основную зарплату. C7 подтверждает доплаты и резерв вместе; раздельные показатели дополнительных начислений и резерва не проверены.', 'Детальная разбивка страховых взносов не проверена. Для причины расхождения требуется регистр формирования КФЗ.']}
    progress(1.0, 'Сверка завершена')
    return result
