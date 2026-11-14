"""Read-only extraction with source coordinates. No network services are used."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from pathlib import Path

import fitz
import pytesseract
from PIL import Image, ImageDraw
from docx import Document
from openpyxl import load_workbook

from .money import MONEY_PATTERN, amount_text


def setup_ocr():
    executable = os.environ.get('TESSERACT_CMD')
    if not executable:
        executable = shutil.which('tesseract')
    if not executable and os.name == 'nt':
        candidate = Path(r'C:\Program Files\Tesseract-OCR\tesseract.exe')
        if candidate.exists():
            executable = str(candidate)
    if not executable:
        raise RuntimeError('Tesseract не найден. Установите Tesseract с языками rus и eng или задайте TESSERACT_CMD.')
    pytesseract.pytesseract.tesseract_cmd = executable


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def ocr_image(image, *, psm=6, digits=False, language=None):
    setup_ocr()
    config = f'--psm {psm}'
    if digits:
        config += ' -c tessedit_char_whitelist=0123456789.,-'
    return pytesseract.image_to_data(image, lang=language or ('eng' if digits else 'rus+eng'), config=config, output_type=pytesseract.Output.DICT, timeout=120)


def data_lines(data, scale=1.0):
    groups = {}
    for i, word in enumerate(data['text']):
        if not word.strip():
            continue
        key = (data['block_num'][i], data['par_num'][i], data['line_num'][i])
        group = groups.setdefault(key, [])
        x, y, w, h = (data[n][i] / scale for n in ('left', 'top', 'width', 'height'))
        group.append((word, (x, y, x + w, y + h), float(data['conf'][i])))
    lines = []
    for words in groups.values():
        lines.append({'text': ' '.join(w[0] for w in words), 'bbox': [min(w[1][0] for w in words), min(w[1][1] for w in words), max(w[1][2] for w in words), max(w[1][3] for w in words)], 'confidence': min(w[2] for w in words)})
    return lines


def page_image(path, page=1, scale=2.0):
    path = Path(path)
    if path.suffix.lower() == '.pdf':
        with fitz.open(path) as doc:
            pix = doc[page - 1].get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            return Image.frombytes('RGB', [pix.width, pix.height], pix.samples)
    with Image.open(path) as src:
        image = src.convert('RGB')
        return image.resize((round(image.width * scale), round(image.height * scale))) if scale != 1 else image.copy()


def extract(path, cache_dir=None):
    """Return complete text and source lines. Cache keyed only by file bytes/version."""
    path = Path(path)
    cache = Path(cache_dir) / (file_hash(path) + '-v3.json') if cache_dir else None
    if cache and cache.exists():
        return json.loads(cache.read_text(encoding='utf-8'))
    suffix = path.suffix.lower()
    result = {'kind': suffix.lstrip('.'), 'text': '', 'lines': [], 'tables': [], 'sheets': {}}
    if suffix == '.pdf':
        with fitz.open(path) as doc:
            if len(doc) > 80:
                raise ValueError('Слишком много страниц: максимум 80 в одном документе')
            for page_no, page in enumerate(doc, 1):
                text = page.get_text().strip()
                if len(text) > 30:
                    words = page.get_text('words')
                    from .report_tables import word_lines
                    lines = [{k:v for k,v in line.items() if k!='words'} | {'confidence':100} for line in word_lines(words)]
                else:
                    scale = 2.0 if page.rect.width > 700 else 4.0
                    image = page_image(path, page_no, scale)
                    lines = data_lines(ocr_image(image), scale)
                for line in lines:
                    line.update(page=page_no)
                    result['lines'].append(line)
    elif suffix in ('.png', '.jpg', '.jpeg', '.tif', '.tiff'):
        image = page_image(path, scale=1)
        lines = data_lines(ocr_image(image))
        for line in lines:
            line['page'] = 1
        result['lines'] = lines
    elif suffix == '.docx':
        doc = Document(path)
        result['lines'] = [{'text': p.text, 'paragraph': i + 1} for i, p in enumerate(doc.paragraphs) if p.text.strip()]
        for t_idx, table in enumerate(doc.tables, 1):
            for r_idx, row in enumerate(table.rows, 1):
                values = [cell.text for cell in row.cells]
                line = {'text': ' | '.join(values), 'table': t_idx, 'row': r_idx, 'cells': values}
                result['lines'].append(line)
                result['tables'].append(line)
    elif suffix == '.xlsx':
        with_data = load_workbook(path, data_only=False, read_only=True)
        for sheet in with_data:
            cells = {}
            if sheet.max_row > 10000 or sheet.max_column > 300:
                raise ValueError('Лист превышает лимит первой версии (10 000 строк / 300 столбцов)')
            for row in sheet:
                for cell in row:
                    if cell.value is not None:
                        value = cell.value
                        if not isinstance(value, (str, int, float, bool)):
                            value = str(value)
                        cells[cell.coordinate] = value
                        result['lines'].append({'text': str(value), 'sheet': sheet.title, 'cell': cell.coordinate})
            result['sheets'][sheet.title] = cells
        with_data.close()
    elif suffix in ('.txt', '.md'):
        result['lines'] = [{'text': path.read_text(encoding='utf-8-sig'), 'paragraph': 1}]
    else:
        raise ValueError(f'Неподдерживаемый формат {suffix or "без расширения"}')
    result['text'] = '\n'.join(line['text'] for line in result['lines'])
    if not result['text'].strip():
        raise ValueError('Не удалось распознать текст документа')
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    return result


def make_evidence(path, doc_id, line, value, label, index=0):
    source = {k: line[k] for k in ('page', 'bbox', 'sheet', 'cell', 'label_cell', 'header_cell', 'table', 'row', 'paragraph', 'confidence','token_start','formula','formula_sources') if k in line}
    return {'id': f'{doc_id}:{index}', 'file': Path(path).name, 'path': str(Path(path).resolve()), 'kind': Path(path).suffix.lstrip('.').lower(), 'text': line['text'], 'value': amount_text(value), 'label': label, 'image_path': None, **source}


def render_evidence(evidence, output_dir):
    """A context crop with red highlight, from the original PDF/image."""
    if evidence.get('image_path') and Path(evidence['image_path']).exists():
        return evidence['image_path']
    if evidence.get('kind') not in ('pdf', 'png', 'jpg', 'jpeg', 'tif', 'tiff'):
        return None
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    bbox = evidence.get('bbox')
    scale = (6.0 if evidence.get('report') else 3.0) if evidence['kind'] == 'pdf' else 1.0
    image = page_image(evidence['path'], evidence.get('page', 1), scale)
    if bbox:
        box = [round(v * scale) for v in bbox]
        # Retain local table context. The exact row label is also stored as text.
        if evidence.get('report'):
            crop = (max(0, box[0] - 55), max(0, box[1] - 140), min(image.width, box[2] + 55), min(image.height, box[3] + 70))
        else:
            crop = (max(0, box[0] - 50), max(0, min(box[1] - 180, round(image.height * .02))), min(image.width, max(box[2] + 80, round(image.width * .95))), min(image.height, box[3] + 70))
        draw = ImageDraw.Draw(image)
        draw.rectangle((box[0] - 4, box[1] - 4, box[2] + 4, box[3] + 4), outline='#c83328', width=max(2, round(scale)))
        if evidence.get('report'):
            context_path = out / (hashlib.sha256(evidence['id'].encode()).hexdigest()[:20] + '-context.png')
            image.crop((0, max(0, box[1] - 140), image.width, min(image.height, box[3] + 70))).save(context_path)
            evidence['context_image_path'] = str(context_path.resolve())
        image = image.crop(crop)
    name = hashlib.sha256(evidence['id'].encode()).hexdigest()[:20] + '.png'
    target = out / name
    image.save(target)
    evidence['image_path'] = str(target.resolve())
    return evidence['image_path']
