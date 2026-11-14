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

# Geometry is the public form template. Monetary contents are never preset.
COLUMNS = {'0200': (5, .244, .294), '0300': (10, .491, .549), '0800': (15, .756, .845), '0900': (16, .845, .922), 'total': (17, .922, .981)}


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


def inspect_report(kfz_path, output_dir=None, *, year=2026):
    path = Path(kfz_path)
    out = Path(output_dir) if output_dir else path.parent / '.kfz-work'
    out.mkdir(parents=True, exist_ok=True)
    cache = out / (file_hash(path) + f'-report-{year}-v6.json')
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
        contract_text = _crop_text(image, (w * .75, h * .14, w * .978, h * .20), psm=6)
        contract_matches = re.findall(r'\d{2}\s*/\s*\d{2}\s*/\s*\d{2}\s*[-–]\s*\d{2}', contract_text)
        contract = re.sub(r'\s+', '', contract_matches[0]).replace('–', '-') if contract_matches else None
        bands = _boundaries(image)
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
            candidate = {'id': row_id, 'label': f'{label.strip()} (страница {page})', 'page': page, 'year': year, 'stage': stage, 'contract': contract, 'contract_text': contract_text, 'values': {}, 'evidence': {}, 'errors': [], 'column_errors': {}}
            for code, (col, left, right) in COLUMNS.items():
                box = [w * left + 5, top, w * right - 5, bottom]
                raw = _crop_text(image, box, psm=7)
                matches = MONEY_PATTERN.findall(raw)
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
        raise ValueError(f'В КФЗ не найдена читаемая строка «3 этап, {year} г.». Первая версия поддерживает выданную табличную форму.')
    cache.write_text(json.dumps(candidates, ensure_ascii=False, indent=2), encoding='utf-8')
    return candidates
