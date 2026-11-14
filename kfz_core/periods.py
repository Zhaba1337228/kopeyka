"""Document dates and accounting intervals are different pieces of evidence."""
import calendar
import re
from datetime import date, timedelta

DATE = r'\d{2}\.\d{2}\.20\d{2}'
MONTHS = ['январ', 'феврал', 'март', 'апрел', 'ма[йя]', 'июн', 'июл', 'август', 'сентябр', 'октябр', 'ноябр', 'декабр']
CONTRACT = re.compile(r'договор|контракт|соглашени', re.I)


def parse_date(value):
    day, month, year = map(int, value.split('.'))
    return date(year, month, day)


def dates_in(text):
    """Return explicit period and document date; never borrow a contract date."""
    lines = text.splitlines()
    periods = set()
    issues = []
    # Cell separators and line breaks are allowed in labelled table fields.
    period_text = '\n'.join(line for line in lines if not CONTRACT.search(line) or
                            (re.search(r'период',line,re.I) and not re.search(r'действи',line,re.I)))
    flat = re.sub(r'[|\n\r]+', ' ', period_text)
    for match in re.finditer(r'(?:период\s*[:\-]?\s*(?:с\s*)?|\bс\s+)(' + DATE + r')\s*(?:по|[-–—])\s*(' + DATE + r')', flat, re.I):
        try:
            start, end = parse_date(match[1]), parse_date(match[2])
            if start > end:
                raise ValueError()
            periods.add((start, end))
        except ValueError:
            issues.append('Некорректные даты периода документа')
    for number, month in enumerate(MONTHS, 1):
        for match in re.finditer(r'(?:период\s*[:\-]?|\bза)\s*(?:полный\s+)?' + month + r'[а-я]*\s+(20\d{2})', flat, re.I):
            year = int(match[1])
            periods.add((date(year, number, 1), date(year, number, calendar.monthrange(year, number)[1])))
    for match in re.finditer(r'(?:период\s*[:\-]?|\bза)\s*([1-4])\s*(?:квартал|кв\.)\s*(20\d{2})', flat, re.I):
        quarter, year = int(match[1]), int(match[2])
        month = quarter * 3
        periods.add((date(year, month-2, 1), date(year, month, calendar.monthrange(year, month)[1])))
    labelled, fallback = set(), set()
    for index, line in enumerate(lines):
        # Do not let a prior line containing the contract swallow a labelled act date.
        if CONTRACT.search(line) and not re.search(r'дата\s+(?:акта|документа|сч[её]та|справки)', line, re.I):
            continue
        if re.search(r'период|срок\s+оплат|оплатить\s+до|срок\s+действ', line, re.I):
            continue
        explicit = re.search(r'дата(?:\s+(?:акта|документа|сч[её]та|справки|упд))?\s*[:|\-]?\s*(' + DATE + r')', line, re.I)
        typed = re.search(r'(?:акт|сч[её]т|справка|ведомость|упд|записка).*?\bот\s*(' + DATE + r')', line, re.I)
        # Excel/date fields may occupy two adjacent cells in extracted text.
        if not explicit and re.fullmatch(r'\s*дата(?:\s+(?:акта|документа|сч[её]та|справки))?\s*[:|]?\s*', line, re.I) and index+1 < len(lines):
            explicit = re.search('(' + DATE + ')', lines[index+1])
        matches = [explicit or typed] if explicit or typed else list(re.finditer('(' + DATE + ')', line))
        for match in matches:
            try:
                (labelled if explicit or typed else fallback).add(parse_date(match[1]))
            except ValueError:
                issues.append('Некорректная дата документа')
    chosen = labelled or fallback
    document_date = next(iter(chosen)) if len(chosen) == 1 else None
    if len(labelled) > 1:
        issues.append('Указаны разные даты документа; уточните реквизиты')
    if len(periods) > 1:
        issues.append('Указаны разные периоды документа; уточните реквизиты')
    if len(periods) == 1:
        start, end = next(iter(periods)); rule = 'указанный период'
    elif not periods and document_date:
        start = end = document_date; rule = 'дата документа (период не указан)'
    else:
        start = end = None; rule = None
    return {'period_start': start.isoformat() if start else None,
            'period_end': end.isoformat() if end else None,
            'document_date': document_date.isoformat() if document_date else None,
            'date_rule': rule, 'date_issue': '; '.join(dict.fromkeys(issues)) or None}


def interval(record):
    if not record.get('period_start'):
        return None
    start = date.fromisoformat(record['period_start'])
    end = date.fromisoformat(record.get('period_end') or record['period_start'])
    # Legacy single-date payroll forms establish the accounting month, not one day.
    if record.get('date_rule') != 'указанный период' and start == end:
        return date(start.year, start.month, 1), date(start.year, start.month, calendar.monthrange(start.year, start.month)[1])
    return start, end


def missing_months(records, months, start=None, end=None):
    intervals = sorted(value for record in records if (value := interval(record)))
    missing = []
    for month in months:
        year, number = map(int, month.split('-'))
        left = max(date(year, number, 1), start or date.min)
        right = min(date(year, number, calendar.monthrange(year, number)[1]), end or date.max)
        cursor = left
        for first, last in intervals:
            if first > cursor:
                break
            if last >= cursor:
                cursor = last + timedelta(days=1)
        if cursor <= right:
            missing.append(month)
    return missing


def overlap_groups(records):
    """Overlapping summaries require review; never silently add quarter and month."""
    conflicts = set()
    for index, first in enumerate(records):
        a = interval(first)
        if not a:
            continue
        for second in records[index+1:]:
            b = interval(second)
            if not b or first.get('code') != second.get('code'):
                continue
            if first.get('indicator_only') and second.get('indicator_only') and not (set(first['indicator_only'].split(',')) & set(second['indicator_only'].split(','))):
                continue
            if max(a[0], b[0]) <= min(a[1], b[1]):
                conflicts.update((first['id'], second['id']))
    return conflicts
