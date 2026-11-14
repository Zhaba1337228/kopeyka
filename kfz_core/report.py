"""The supplied KFZ form: detect year rows, then OCR individual columns."""
from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path

import fitz
from PIL import Image, ImageOps

from .extract import data_lines, file_hash, make_evidence, ocr_image, page_image
from .money import MONEY_PATTERN, amount_text, money, scaled_money, resolve_units
from .articles import KNOWN_TITLES, title_for
from .report_tables import table_rows
from .metadata import contracts_in

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


def _detail_indicators(image,bands,columns,candidate,path,doc_id,scale,top,bottom,multiplier):
    fine = _vertical_edges(image,bands[0][0],bands[-1][1])
    # Stage and month are merged in the annual rows of the supplied form.
    # Validate the monetary anchors before accounting for that missing border.
    merged = 1 if len(fine)==17 else 0
    profile = {'0200':4,'0300':9,'0800':14,'0900':15}
    if len(fine) not in (17,18) or any(code not in columns or abs(columns[code][1]-fine[index-merged])>8 for code,index in profile.items()):
        return []
    specs = [(3,'Трудоёмкость','чел.-мес.',None), (4,'Материальные затраты','₽',None),
             (6,'Заработная плата без резерва','₽',None),(7,'Основная заработная плата','₽','0200.main'),
             (8,'Дополнительная заработная плата','₽',None),(9,'Резерв отпусков','₽',None),
             (11,'Взносы с зарплаты без резерва','₽',None),(12,'Взносы с основной зарплаты','₽',None),
             (13,'Взносы с дополнительной зарплаты','₽',None),(14,'Взносы с резерва','₽',None)]
    result=[]
    for col,title,unit,field in specs:
        box=[fine[col-1-merged]+5,top,fine[col-merged]-5,bottom]
        raw=_crop_text(image,box,psm=7)
        matches=MONEY_PATTERN.findall(raw)
        if len(matches)!=1:
            # A grid line touching a short numeric cell can swallow its comma.
            # Retry inside the same cell; never infer a decimal separator.
            tighter=[box[0],top+1,box[2],bottom-4]
            retry=_crop_text(image,tighter,psm=7)
            retry_matches=MONEY_PATTERN.findall(retry)
            if len(retry_matches)==1:
                raw,matches=retry,retry_matches
        value=amount_text(scaled_money(matches[0],multiplier if unit=='₽' else 1)) if len(matches)==1 and (multiplier is not None or unit!='₽') else None
        code=f'detail-{col}'
        ev=make_evidence(path,doc_id,{'text':f'{title}: {raw}','page':candidate['page'],'bbox':[v/scale for v in box]},value or 0,title,f'{candidate["id"]}-{code}')
        ev.update(report=True,value=value)
        candidate['evidence'][code]=ev
        result.append({'code':code,'title':title,'unit':unit,'reported':value,'report_evidence_ids':[ev['id']],'field':field})
    parts=[r for r in result if r['code'] in ('detail-8','detail-9')]
    result.append({'code':'detail-extra-reserve','title':'Доплаты и резерв вместе','unit':'₽',
                   'reported':amount_text(sum(money(r['reported']) for r in parts)) if all(r['reported'] is not None for r in parts) else None,
                   'report_evidence_ids':[ref for r in parts for ref in r['report_evidence_ids']],'field':'0200.extra_reserve'})
    return result


def inspect_report(kfz_path, output_dir=None, *, year=2026):
    path = Path(kfz_path)
    out = Path(output_dir) if output_dir else path.parent / '.kfz-work'
    out.mkdir(parents=True, exist_ok=True)
    cache = out / (file_hash(path) + f'-report-{year}-v11.json')
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
        contract_matches = contracts_in(contract_text)
        contract = contract_matches[0] if len(contract_matches)==1 else None
        bands = _boundaries(image)
        columns = _columns(image, bands) if len(bands) > 1 else {}
        header_units = _crop_text(image,(0,h*.20,w,h*.395),psm=6) if columns else ''
        multiplier, unit_error = resolve_units(header_units)
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
            stage_match = re.search(r'(\d{1,3})\s*(?:этап|зтап|этал)', label, re.I)
            stage = int(stage_match[1]) if stage_match else None
            if stage is None:
                continue
            row_id = f'p{page}-r{row_index}'
            candidate = {'id': row_id, 'label': f'{label.strip()} (страница {page})', 'page': page, 'year': year, 'stage': stage, 'contract': contract, 'contract_text': contract_text, 'values': {}, 'evidence': {}, 'errors': [], 'column_errors': {}, 'articles': {}}
            if unit_error:
                candidate['errors'].append(unit_error)
            for code, (col, left, right, title) in columns.items():
                box = [left + 5, top, right - 5, bottom]
                raw = _crop_text(image, box, psm=7)
                matches = MONEY_PATTERN.findall(raw)
                # Uncoded zero columns do not silently acquire an invented article code.
                if code.startswith('column-') and len(matches) == 1 and money(matches[0]) == 0:
                    continue
                if code != 'total':
                    candidate['articles'][code] = title
                if len(matches) != 1 or unit_error:
                    candidate['column_errors'][code] = f'Не удалось однозначно распознать графу {col}: {raw or "пусто"}'
                    candidate['values'][code] = None
                    continue
                value = amount_text(scaled_money(matches[0],multiplier))
                candidate['values'][code] = value
                bbox = [v / scale for v in box]
                ev = make_evidence(path, doc_id, {'text': f'{label}; графа {col}: {raw}', 'page': page, 'bbox': bbox}, value, f'КФЗ · {label} · графа {col}', f'{row_id}-{code}')
                ev['report'] = True
                candidate['evidence'][code] = ev
            candidate['indicators'] = _detail_indicators(image,bands,columns,candidate,path,doc_id,scale,top,bottom,multiplier)
            candidates.append(candidate)
    if not candidates:
        raise ValueError(f'В КФЗ не найдена читаемая строка этапа за {year} год и графы статей. Поддержаны исходная форма с кодами в заголовках и таблица «Код статьи / Наименование / Сумма».')
    cache.write_text(json.dumps(candidates, ensure_ascii=False, indent=2), encoding='utf-8')
    return candidates
