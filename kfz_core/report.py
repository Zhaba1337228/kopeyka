"""The supplied KFZ form: detect year rows, then OCR individual columns."""
from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path

import fitz
from PIL import Image, ImageOps

from .extract import data_lines, file_hash, make_evidence, ocr_image, page_image
from .money import MONEY_PATTERN, amount_text, money
from .articles import KNOWN_TITLES, title_for
from .report_tables import table_rows

# The supplied landscape form retains its row geometry. Column positions and
# article identities are discovered from the actual grid and its headings.


class ReportSelectionRequired(ValueError):
    def __init__(self, candidates):
        super().__init__('Найдено несколько строк КФЗ. Выберите строку для сверки.')
        self.candidates = candidates


def _crop_text(image, box, psm=7, digits=False):
    crop = image.crop(tuple(round(v) for v in box)).convert('L')
    # White padding helps Tesseract distinguish a dense table cell from a border.
    crop = ImageOps.expand(crop, border=15, fill='white')
    data = ocr_image(crop, psm=psm, digits=digits)
    return ' '.join(str(t) for t in data['text'] if str(t).strip())


def _boundaries(image):
    w, h = image.size
    gray = image.convert('L')
    x1, x2 = round(w * .023), round(w * .976)
    positions = []
    for y in range(round(h * .395), round(h * .49)):
        histogram = gray.crop((x1, y, x2, y + 1)).histogram()
        if sum(histogram[:170]) / (x2 - x1) > .58:
            positions.append(y)
    bands = []
    for y in positions:
        if bands and y <= bands[-1][-1] + 2:
            bands[-1].append(y)
        else:
            bands.append([y])
    return [(min(b), max(b)) for b in bands]


def _vertical_edges(image, top, bottom):
    gray = image.convert('L')
    top, bottom = round(top), round(bottom)
    bands = []
    for x in range(round(image.width*.015), image.width):
        histogram = gray.crop((x, top, x+1, bottom)).histogram()
        if sum(histogram[:170]) / max(1, bottom-top) > .8:
            if bands and x <= bands[-1][-1]+2:
                bands[-1].append(x)
            else:
                bands.append([x])
    return [sum(b)/len(b) for b in bands]


def _columns(image, bands):
    h = image.height
    major = _vertical_edges(image, h*.253, h*.282)
    fine = _vertical_edges(image, bands[0][0], bands[-1][1])
    columns = {}
    for index, (left, right) in enumerate(zip(major, major[1:]), 1):
        if right-left < 30:
            continue
        heading = _crop_text(image, (left+4, h*.25, right-4, h*.285), psm=6)
        codes = set(re.findall(r'(?<!\d)(\d{4})(?!\d)', heading))
        # Ordinary cells such as stage/month are vertically merged across the header.
        full_heading = heading if codes else _crop_text(image, (left+4, h*.25, right-4, bands[0][0]-3), psm=6)
        if len(codes) == 1:
            code = next(iter(codes))
            title = KNOWN_TITLES.get(code) or re.sub(r'\(?Стать[яи].*', '', heading, flags=re.I).strip(' :(')
        elif re.search(r'итого|всего', full_heading, re.I):
            code, title = 'total', 'Итого'
        elif re.search(r'затрат|расход', full_heading, re.I):
            code, title = f'column-{index}', full_heading.strip()
        else:
            continue
        # A grouped heading (payroll/contributions) contains a total followed by
        # breakdowns. Only the first numeric cell belongs in the aggregate.
        end = min((x for x in fine if x > left+12 and x <= right+8), default=right)
        if code in columns:
            raise ValueError(f'Код {code} повторяется в заголовках КФЗ; требуется уточнение формы')
        columns[code] = (index, left, end, title_for(code, title))
    return columns


def inspect_report(kfz_path, output_dir=None, *, year=2026):
    path = Path(kfz_path)
    out = Path(output_dir) if output_dir else path.parent / '.kfz-work'
    out.mkdir(parents=True, exist_ok=True)
    cache = out / (file_hash(path) + f'-report-{year}-v7.json')
    if cache.exists():
        candidates = json.loads(cache.read_text(encoding='utf-8'))
        # Source names/paths are current even after a rename.
        for candidate in candidates:
            for ev in candidate['evidence'].values():
                ev['path'], ev['file'] = str(path.resolve()), path.name
        return candidates
    if path.suffix.lower() == '.pdf':
        with fitz.open(path) as document:
            count = len(document)
        scale = 6.0
    else:
        count, scale = 1, 3.0
    candidates = []
    doc_id = file_hash(path)[:20]
    for page in range(1, count + 1):
        image = page_image(path, page, scale)
        w, h = image.size
        row_table = table_rows(path, page, image, scale, year, doc_id)
        if row_table:
            candidates.append(row_table)
            continue
        contract_text = _crop_text(image, (w * .75, h * .14, w * .978, h * .20), psm=6)
        contract_matches = re.findall(r'\d{2}\s*/\s*\d{2}\s*/\s*\d{2}\s*[-–]\s*\d{2}', contract_text)
        contract = re.sub(r'\s+', '', contract_matches[0]).replace('–', '-') if contract_matches else None
        bands = _boundaries(image)
        columns = _columns(image, bands) if len(bands) > 1 else {}
        if not any(re.fullmatch(r'\d{4}', code) for code in columns):
            row_table = table_rows(path, page, image, scale, year, doc_id, use_ocr=True)
            if row_table:
                candidates.append(row_table)
            continue
        for row_index, (upper, lower) in enumerate(zip(bands, bands[1:])):
            top, bottom = upper[1] + 3, lower[0]
            if bottom - top < 12:
                continue
            label = _crop_text(image, (w * .021, top, w * .135, bottom), psm=7)
            if str(year) not in label or not re.search(r'этап|зтап|этал', label, re.I):
                continue
            # The stage digit and year must both come from the selected row.
            stage_match = re.search(r'([123456789])\s*(?:этап|зтап|этал)', label, re.I)
            stage = int(stage_match[1]) if stage_match else None
            if stage != 3:
                continue
            row_id = f'p{page}-r{row_index}'
            candidate = {'id': row_id, 'label': f'{label.strip()} (страница {page})', 'page': page, 'year': year, 'stage': stage, 'contract': contract, 'contract_text': contract_text, 'values': {}, 'evidence': {}, 'errors': [], 'column_errors': {}, 'articles': {}}
            for code, (col, left, right, title) in columns.items():
                box = [left + 5, top, right - 5, bottom]
                raw = _crop_text(image, box, psm=7)
                matches = MONEY_PATTERN.findall(raw)
                # Uncoded zero columns do not silently acquire an invented article code.
                if code.startswith('column-') and len(matches) == 1 and money(matches[0]) == 0:
                    continue
                if code != 'total':
                    candidate['articles'][code] = title
                if len(matches) != 1:
                    candidate['column_errors'][code] = f'Не удалось однозначно распознать графу {col}: {raw or "пусто"}'
                    candidate['values'][code] = None
                    continue
                value = amount_text(matches[0])
                candidate['values'][code] = value
                bbox = [v / scale for v in box]
                ev = make_evidence(path, doc_id, {'text': f'{label}; графа {col}: {raw}', 'page': page, 'bbox': bbox}, value, f'КФЗ · {label} · графа {col}', f'{row_id}-{code}')
                ev['report'] = True
                candidate['evidence'][code] = ev
            candidates.append(candidate)
    if not candidates:
        raise ValueError(f'В КФЗ не найдена читаемая строка «3 этап, {year} г.» и графы статей. Поддержаны исходная форма с кодами в заголовках и таблица «Код статьи / Наименование / Сумма».')
    cache.write_text(json.dumps(candidates, ensure_ascii=False, indent=2), encoding='utf-8')
    return candidates
