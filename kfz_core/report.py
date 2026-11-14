"""Read coded KFZ tables by their actual grid, hierarchy and printed headings."""
from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path

import fitz
from PIL import Image, ImageOps

from .extract import data_lines, file_hash, make_evidence, ocr_image, page_image
from .money import AMOUNT_PATTERN, amount_text, money, scaled_money, resolve_units
from .articles import KNOWN_TITLES, title_for
from .report_tables import table_rows
from .metadata import contracts_in
from .grid import horizontal_rules, vertical_rules, vertical_start, body_top
from .amounts import declarations
from .extract import extract

# Accounting labels are explicit domain rules; page geometry comes from the file.
PARSER_REVISION = hashlib.sha256(b''.join(Path(__file__).with_name(name).read_bytes() for name in (
    'report.py','report_tables.py','grid.py','money.py','metadata.py','extract.py','amounts.py'
))).hexdigest()[:12]


class ReportSelectionRequired(ValueError):
    def __init__(self, candidates):
        super().__init__('Найдено несколько строк КФЗ. Выберите строку для сверки.')
        self.candidates = candidates


def _crop_text(image, box, psm=7, digits=False, language=None):
    crop = image.crop(tuple(round(v) for v in box)).convert('L')
    # White padding helps Tesseract distinguish a dense table cell from a border.
    crop = ImageOps.expand(crop, border=15, fill='white')
    data = ocr_image(crop, psm=psm, digits=digits,language=language)
    return ' '.join(str(t) for t in data['text'] if str(t).strip())


def _boundaries(image):
    return horizontal_rules(image)


def _vertical_edges(image, top, bottom):
    return vertical_rules(image,top,bottom)


def _columns(image, bands):
    top=bands[0][1]+3
    header_level=bands[1][0]-3
    major=_vertical_edges(image,top,header_level)
    fine=_vertical_edges(image,body_top(bands),bands[-1][1])
    columns={}
    for index,(left,right) in enumerate(zip(major,major[1:]),1):
        if right-left<30:continue
        heading=_crop_text(image,(left+4,top,right-4,header_level),psm=6)
        codes=set(re.findall(r'(?:стать[яи]|код).*?(\d{4})(?!\d)',heading,re.I))
        full_heading=_crop_text(image,(left+4,top,right-4,bands[-2][0]-3),psm=6) if not codes else heading
        if len(codes)==1:
            code=next(iter(codes))
            title=KNOWN_TITLES.get(code) or re.sub(r'\(?Стать[яи].*','',heading,flags=re.I).strip(' :(')
        elif re.search(r'итого',full_heading,re.I):
            code,title='total','Итого'
        elif re.search(r'матер.*затрат',full_heading,re.I|re.S):
            code,title=f'column-{index}','Материальные затраты'
        else:continue
        edges=[x for x in fine if left-8<=x<=right+8]
        intervals=list(zip(edges,edges[1:]))
        chosen=(left,right)
        if len(intervals)>1:
            depths=[max(vertical_start(image,a,top,bands[-2][0]),vertical_start(image,b,top,bands[-2][0])) for a,b in intervals]
            minimum=min(depths)
            totals=[pair for pair,depth in zip(intervals,depths) if abs(depth-minimum)<8]
            if len(totals)!=1:
                raise ValueError(f'Не определена итоговая графа статьи {code}: неоднозначная структура заголовка')
            chosen=totals[0]
            label=_crop_text(image,(chosen[0]+4,top,chosen[1]-4,bands[-2][0]-3),psm=6)
            if not re.search(r'всего|итого',label,re.I):
                raise ValueError(f'Не подтверждена подпись итоговой графы статьи {code}')
        if code in columns:raise ValueError(f'Код {code} повторяется в заголовках КФЗ')
        columns[code]=(index,chosen[0],chosen[1],title_for(code,title))
    return columns


def _detail_indicators(image,bands,columns,candidate,path,doc_id,scale,top,bottom,multiplier,header_bottom):
    fine=_vertical_edges(image,body_top(bands),bands[-1][1])
    header_top=bands[0][1]+3
    major=_vertical_edges(image,header_top,bands[1][0]-3)
    specs={
        'labor':('detail-3','Трудоёмкость','чел.-мес.',None),
        'material':('detail-4','Материальные затраты','₽',None),
        '0200.without_reserve':('detail-6','Заработная плата без резерва','₽','0200.without_reserve'),
        '0200.main':('detail-7','Основная заработная плата','₽','0200.main'),
        '0200.extra':('detail-8','Дополнительная заработная плата','₽','0200.extra'),
        '0200.reserve':('detail-9','Резерв отпусков','₽','0200.reserve'),
        '0300.without_reserve':('detail-11','Взносы с зарплаты без резерва','₽','0300.without_reserve'),
        '0300.main':('detail-12','Взносы с основной зарплаты','₽','0300.main'),
        '0300.extra':('detail-13','Взносы с дополнительной зарплаты','₽','0300.extra'),
        '0300.reserve':('detail-14','Взносы с резерва','₽','0300.reserve'),
    }
    result=[]
    found=set()
    for left,right in zip(fine,fine[1:]):
        if any(abs(left-c[1])<8 and abs(right-c[2])<8 for code,c in columns.items() if not code.startswith('column-')):continue
        heading=_crop_text(image,(left+5,header_top,right-5,header_bottom-3),psm=6).lower().replace('ё','е')
        leaf_top=max(vertical_start(image,left,header_top,header_bottom),vertical_start(image,right,header_top,header_bottom))
        heading+=' '+_crop_text(image,(left+5,leaf_top+3,right-5,header_bottom-3),psm=6,language='rus').lower().replace('ё','е')
        parent=next((code for code,c in columns.items() if code in ('0200','0300') and major[c[0]-1]-8<=left and right<=major[c[0]]+8),None)
        key=None
        if re.search(r'трудоем|человеко.?месяц',heading):key='labor'
        elif re.search(r'матер.*затрат',heading,re.S):key='material'
        elif parent:
            suffix='reserve' if 'резерв' in heading else 'main' if 'основн' in heading else 'extra' if re.search(r'допол[а-я]*',heading) else 'without_reserve' if 'всего' in heading else None
            if suffix:key=parent+'.'+suffix
        if key not in specs:continue
        if key in found:raise ValueError('Неоднозначные подписи детального показателя КФЗ: '+key)
        found.add(key)
        code,title,unit,field=specs[key]
        box=[left+5,top,right-5,bottom]
        raw=_crop_text(image,box,psm=7)
        matches=AMOUNT_PATTERN.findall(raw)
        if len(matches)!=1 or not re.search(r'[,.]\d',raw):
            retry=_crop_text(image,[box[0],top+1,box[2],bottom-4],psm=7)
            retry_matches=AMOUNT_PATTERN.findall(retry)
            if len(retry_matches)==1:raw,matches=retry,retry_matches
        value=amount_text(scaled_money(matches[0],multiplier if unit=='₽' else 1)) if len(matches)==1 and (multiplier is not None or unit!='₽') else None
        ev=make_evidence(path,doc_id,{'text':f'{heading}: {raw}','page':candidate['page'],'bbox':[v/scale for v in box]},value or 0,title,f'{candidate["id"]}-{code}')
        ev.update(report=True,value=value)
        candidate['evidence'][code]=ev
        result.append({'code':code,'title':title,'unit':unit,'reported':value,'report_evidence_ids':[ev['id']],'field':field})
    parts=[r for r in result if r['code'] in ('detail-8','detail-9')]
    if len(parts)==2:
        result.append({'code':'detail-extra-reserve','title':'Доплаты и резерв вместе','unit':'₽',
                       'reported':amount_text(sum(money(r['reported']) for r in parts)) if all(r['reported'] is not None for r in parts) else None,
                       'report_evidence_ids':[ref for r in parts for ref in r['report_evidence_ids']],'field':'0200.extra_reserve'})
    return result


def inspect_report(kfz_path, output_dir=None, *, year=2026):
    path = Path(kfz_path)
    out = Path(output_dir) if output_dir else path.parent / '.kfz-work'
    out.mkdir(parents=True, exist_ok=True)
    cache = out / (file_hash(path) + f'-report-{year}-{PARSER_REVISION}.json')
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
    source=extract(path,out / "source")
    doc_id = file_hash(path)[:20]
    for page in range(1, count + 1):
        image = page_image(path, page, scale)
        w, h = image.size
        row_table = table_rows(path, page, image, scale, year, doc_id)
        if row_table:
            candidates.append(row_table)
            continue
        contract_text=source['text']
        contract_matches=contracts_in(contract_text)
        bands=_boundaries(image)
        if not contract_matches and bands:
            contract_text=_crop_text(image,(0,0,w,bands[0][0]),psm=6)
            contract_matches=contracts_in(contract_text)
        contract=contract_matches[0] if len(contract_matches)==1 else None
        columns=_columns(image,bands) if len(bands)>1 else {}
        if not any(re.fullmatch(r'\d{4}',code) for code in columns):
            row_table=table_rows(path,page,image,scale,year,doc_id,use_ocr=True)
            if row_table:candidates.append(row_table)
            continue
        label_right=min(c[1] for c in columns.values())
        row_labels=[_crop_text(image,(0,upper[1]+3,label_right-5,lower[0]),psm=7) if lower[0]-upper[1]>12 else '' for upper,lower in zip(bands,bands[1:])]
        first_body=next((i for i,text in enumerate(row_labels) if re.search(r'\d+\s*(?:этап|зтап|этал)',text,re.I)),None)
        if first_body is None:continue
        header_bottom=bands[first_body][1]
        header_units=_crop_text(image,(0,bands[0][1]+3,w,header_bottom-3),psm=6)+'\n'+declarations(source['lines'])
        multiplier,unit_error=resolve_units(header_units)
        for row_index, (upper, lower) in enumerate(zip(bands, bands[1:])):
            top, bottom = upper[1] + 3, lower[0]
            if bottom - top < 12:
                continue
            label = row_labels[row_index]
            if str(year) not in label or not re.search(r'этап|зтап|этал', label, re.I):
                continue
            # The stage digit and year must both come from the selected row.
            stage_match = re.search(r'(\d{1,3})\s*(?:этап|зтап|этал)', label, re.I)
            stage = int(stage_match[1]) if stage_match else None
            if stage is None:
                continue
            # Count body rows, preserving ids from versions created before adaptive headers.
            row_id = f'p{page}-r{row_index-first_body+1}'
            candidate = {'id': row_id, 'label': f'{label.strip()} (страница {page})', 'page': page, 'year': year, 'stage': stage, 'contract': contract, 'contract_text': contract_text, 'values': {}, 'evidence': {}, 'errors': [], 'column_errors': {}, 'articles': {}}
            if unit_error:
                candidate['errors'].append(unit_error)
            for code, (col, left, right, title) in columns.items():
                box = [left + 5, top, right - 5, bottom]
                raw = _crop_text(image, box, psm=7)
                matches = AMOUNT_PATTERN.findall(raw)
                # Uncoded zero columns do not silently acquire an invented article code.
                if code.startswith('column-') and len(matches) == 1 and money(matches[0]) == 0:
                    continue
                if code != 'total':
                    candidate['articles'][code] = title
                if len(matches) != 1 or unit_error:
                    candidate['column_errors'][code] = f'Не удалось однозначно распознать показатель {code}: {raw or "пусто"}'
                    candidate['values'][code] = None
                    continue
                value = amount_text(scaled_money(matches[0],multiplier))
                candidate['values'][code] = value
                bbox = [v / scale for v in box]
                ev = make_evidence(path, doc_id, {'text': f'{label}; {title}: {raw}', 'page': page, 'bbox': bbox}, value, f'КФЗ · {label} · {title}', f'{row_id}-{code}')
                ev['report'] = True
                candidate['evidence'][code] = ev
            candidate['indicators'] = _detail_indicators(image,bands,columns,candidate,path,doc_id,scale,top,bottom,multiplier,header_bottom)
            candidates.append(candidate)
    if not candidates:
        raise ValueError(f'В КФЗ не найдена читаемая строка этапа за {year} год и графы статей. Поддержаны исходная форма с кодами в заголовках и таблица «Код статьи / Наименование / Сумма».')
    cache.write_text(json.dumps(candidates, ensure_ascii=False, indent=2), encoding='utf-8')
    return candidates
