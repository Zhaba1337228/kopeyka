"""HTTP interface to the local reconciliation engine."""
import json
import os
import re
import shutil
from copy import deepcopy
from threading import RLock
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.encoders import jsonable_encoder
from starlette.concurrency import run_in_threadpool

from kfz_core import reconcile, ReportSelectionRequired
from kfz_core.review import build_review, compare_results, rub, location
from kfz_core.amounts import pin_source
from ui.uploads import stage_uploads

app = FastAPI()
sessions = {}
mutation_lock = RLock()
STORE = Path(os.environ.get('KOPEYKA_STORAGE', 'runtime/jobs')).resolve()
STORE.mkdir(parents=True, exist_ok=True)
for manifest in STORE.glob('*/job.json'):
    try:
        job = json.loads(manifest.read_text(encoding='utf-8'))
        job['base'] = manifest.parent
        job['report'] = Path(job['report'])
        job['documents'] = list(map(Path, job['documents']))
        if job.get('status') == 'Обработка':
            job['status'] = 'Обработка прервана перезапуском'
        sessions[manifest.parent.name] = job
    except (ValueError, KeyError, OSError):
        continue


def save(job):
    target = job['base'] / 'job.json'
    pending = target.with_suffix('.' + uuid4().hex + '.tmp')
    pending.write_text(json.dumps(jsonable_encoder(job), ensure_ascii=False), encoding='utf-8')
    pending.replace(target)


def version_info(sid, job):
    return {'session': sid, 'root': job.get('root', sid), 'parent': job.get('parent'),
            'version': job.get('version', 1), 'created': job['created'], 'status': job.get('status'),
            'operation': job.get('operation', 'Первичная сверка')}


def payload(sid, job):
    result = deepcopy(job.get('result'))
    if result:
        result['review'] = build_review(result)
    root = job.get('root', sid)
    versions = sorted([version_info(key, item) for key, item in list(sessions.items())
                       if item.get('root', key) == root and item.get('owner') == job['owner']], key=lambda v: v['version'])
    parent = sessions.get(job.get('parent'))
    return {'session': sid, 'result': result, 'candidates': job.get('candidates', []),
            'version': version_info(sid, job), 'versions': versions, 'drafts': job.get('drafts', {}),
            'comparison': compare_results(parent['result'], result) if parent and parent.get('result') and result else None}


def fork_job(parent_id, parent, operation):
    sid = uuid4().hex
    base = STORE / sid
    base.mkdir(mode=0o700)
    root = parent.get('root', parent_id)
    version = max(j.get('version', 1) for key, j in list(sessions.items()) if j.get('root', key) == root) + 1
    report_dir = base / 'input' / 'report'
    report_dir.mkdir(parents=True)
    report = report_dir / parent['report'].name
    shutil.copy2(parent['report'], report)
    paths = []
    for index, old in enumerate(parent['documents']):
        folder = base / 'input' / 'documents' / f'{index:04d}'
        folder.mkdir(parents=True)
        target = folder / old.name
        shutil.copy2(old, target)
        paths.append(target)
    job = {'owner': parent['owner'], 'created': datetime.now(timezone.utc).isoformat(),
           'base': base, 'report': report, 'documents': paths, 'journal': deepcopy(parent['journal']),
           'contract': parent['contract'], 'start': parent['start'], 'end': parent['end'],
           'root': root, 'parent': parent_id, 'version': version, 'operation': operation, 'status': 'Обработка',
           'decisions': {doc_id:pin_source(decision,doc_id,parent.get('result',{})) for doc_id,decision in parent.get('decisions',{}).items()}, 'row_id': parent.get('row_id')}
    sessions[sid] = job
    save(job)
    return sid, job


@app.middleware('http')
async def browser_identity(request: Request, call_next):
    owner = request.cookies.get('kopeyka_owner', '')
    if not re.fullmatch('[0-9a-f]{64}', owner):
        owner = uuid4().hex + uuid4().hex
    request.state.owner = owner
    response = await call_next(request)
    response.set_cookie('kopeyka_owner', owner, httponly=True, samesite='strict',
                        secure=request.url.scheme == 'https', max_age=31536000)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Cache-Control'] = 'no-store'
    return response


def owned(session, request):
    job = sessions.get(session)
    if not job or job.get('owner') != request.state.owner:
        raise HTTPException(404, 'Сверка не найдена')
    return job


@app.get('/api/history')
def history(request: Request):
    return sorted([{**version_info(sid, j), 'contract': j['contract'],
                    'status': j.get('status', 'Завершена')} for sid, j in list(sessions.items())
                   if j.get('owner') == request.state.owner], key=lambda j: j['created'], reverse=True)


@app.get('/api/demo')
def demo():
    from kfz_core.demo import demo_bundle
    return Response(demo_bundle(),media_type='application/zip',
                    headers={'Content-Disposition':'attachment; filename="kopeyka-demo.zip"'})


@app.get('/api/session/{session}')
def restore(session: str, request: Request):
    job = owned(session, request)
    return jsonable_encoder(payload(session, job))


class Upload:
    def __init__(self, name, data):
        self.name, self.data = name, data

    def getvalue(self):
        return self.data


def calculate(job, row_id=None, decisions=None):
    try:
        result = reconcile(job['report'], job['documents'], output_dir=job['base'] / 'evidence',
                           contract=job['contract'], start_date=job['start'], end_date=job['end'],
                           report_row_id=row_id, decisions=decisions or {})
    except ReportSelectionRequired as exc:
        job['candidates'] = exc.candidates
        job['status'] = 'Выберите строку КФЗ'
        save(job)
        return {'candidates': exc.candidates}
    result['documents'].extend(job['journal'])
    failures = [d['file'] for d in job['journal'] if d['status'] == 'error']
    if failures:
        for row in [*result['rows'], *result.get('indicators', []), result['total']]:
            row['status'] = 'Недостаточно данных'
            row.setdefault('reasons', []).append('Часть комплекта не прочитана: ' + ', '.join(failures))
    job['result'] = result
    job['row_id'] = result['context']['report_row_id']
    job['decisions'] = decisions or {}
    job['status'] = 'Завершена'
    job.pop('candidates', None)
    save(job)
    return {'result': result}


@app.post('/api/check')
async def check(request: Request, report: UploadFile = File(...), documents: list[UploadFile] = File(...),
                contract: str = Form(...), start: str = Form(...), end: str = Form(...)):
    if not contract.strip() or start > end:
        raise HTTPException(400, 'Проверьте договор и период')
    if len(documents) > 500 or sum(f.size or 0 for f in [report, *documents]) > 250 * 1024 * 1024:
        raise HTTPException(413, 'Лимит: 500 файлов и 250 МБ на комплект')
    session = uuid4().hex
    base = STORE / session
    base.mkdir(mode=0o700)
    try:
        report_file = Upload(report.filename, await report.read())
        files = [Upload(f.filename, await f.read()) for f in documents]
        report_path, paths, journal = stage_uploads(report_file, files, base / 'input')
        job = {'owner': request.state.owner, 'created': datetime.now(timezone.utc).isoformat(),
               'status': 'Обработка', 'root': session, 'version': 1, 'operation': 'Первичная сверка',
               'base': base, 'report': report_path, 'documents': paths,
               'journal': journal, 'contract': contract, 'start': start, 'end': end}
        sessions[session] = job
        save(job)
        await run_in_threadpool(calculate, job)
        return jsonable_encoder(payload(session, job))
    except Exception as exc:
        if session in sessions:
            sessions[session]['status'] = 'Ошибка обработки'
            save(sessions[session])
        raise HTTPException(400, str(exc)) from exc


@app.post('/api/session/{session}/recalculate')
def recalculate(session: str, body: dict, request: Request):
    job = owned(session, request)
    with mutation_lock:
        parent = job if job.get('result') else None
        try:
            if job.get('result'):
                session, job = fork_job(session, job, 'Ручное решение проверяющего')
            decisions = dict(job.get('decisions', {}))
            if body.get('document_id'):
                decisions[body['document_id']] = pin_source(body['decision'],body['document_id'],parent.get('result',{}) if parent else job.get('result',{}))
            calculate(job, body.get('row_id', job.get('row_id')), decisions)
            return jsonable_encoder(payload(session, job))
        except Exception as exc:
            if job is not parent:
                job['status'] = 'Ошибка обработки'
                save(job)
            raise HTTPException(400, str(exc)) from exc


def add_documents(session, parent, files, note):
    with mutation_lock:
        sid, job = fork_job(session, parent, 'Добавление документов' + (': ' + note.strip()[:200] if note.strip() else ''))
        try:
            _, paths, journal = stage_uploads(Upload(job['report'].name, job['report'].read_bytes()), files, job['base'] / 'addition')
            all_paths = [*job['documents'], *paths]
            if len(all_paths) > 500 or sum(p.stat().st_size for p in all_paths) > 250 * 1024 * 1024:
                raise ValueError('Полный комплект версии превышает 500 файлов или 250 МБ')
            job['documents'] = all_paths
            job['journal'].extend(journal)
            job['added_files'] = [f.name for f in files]
            calculate(job, job.get('row_id'), job.get('decisions'))
            return jsonable_encoder(payload(sid, job))
        except Exception:
            job['status'] = 'Ошибка обработки'
            save(job)
            raise


@app.post('/api/session/{session}/documents')
async def append_documents(session: str, request: Request, documents: list[UploadFile] = File(...), note: str = Form('')):
    parent = owned(session, request)
    if not parent.get('result'):
        raise HTTPException(409, 'Сначала завершите исходную сверку')
    if len(documents) > 500 or sum(f.size or 0 for f in documents) > 250 * 1024 * 1024:
        raise HTTPException(413, 'Лимит: 500 файлов и 250 МБ')
    files = [Upload(f.filename, await f.read()) for f in documents]
    try:
        return await run_in_threadpool(add_documents, session, parent, files, note)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.put('/api/session/{session}/request/{code}')
def save_request(session: str, code: str, body: dict, request: Request):
    job = owned(session, request)
    if not job.get('result') or code not in {r['code'] for r in job['result']['rows']}:
        raise HTTPException(404, 'Статья не найдена')
    subject, text = body.get('subject'), body.get('text')
    if not isinstance(subject, str) or not isinstance(text, str) or not text.strip() or len(text) > 30000 or len(subject) > 500:
        raise HTTPException(400, 'Заполните текст запроса (до 30 000 символов) и тему (до 500 символов)')
    with mutation_lock:
        draft = {'subject': subject, 'text': text, 'saved_at': datetime.now(timezone.utc).isoformat(), 'status': 'Черновик сохранён'}
        job.setdefault('drafts', {})[code] = draft
        save(job)
    return draft


@app.get('/api/session/{session}/evidence/{evidence_id}/{kind}')
def evidence(session: str, evidence_id: str, kind: str, request: Request):
    job = owned(session, request)
    ev = job.get('result', {}).get('evidence', {}).get(evidence_id) if job else None
    key = {'image': 'image_path', 'original': 'path', 'context': 'context_image_path'}.get(kind)
    path = Path(ev[key]) if ev and key and ev.get(key) else None
    if not path or not path.is_file() or not path.resolve().is_relative_to(job['base'].resolve()):
        raise HTTPException(404, 'Файл недоступен')
    return FileResponse(path, filename=path.name if kind == 'original' else None)


@app.get('/api/session/{session}/documents/{document_id}/original')
def original_document(session: str, document_id: str, request: Request):
    job = owned(session, request)
    document = next((d for d in job.get('result',{}).get('documents',[]) if d.get('id')==document_id),None)
    path = Path(document['path']) if document and document.get('path') else None
    if not path or not path.is_file() or not path.resolve().is_relative_to(job['base'].resolve()):
        raise HTTPException(404,'Документ недоступен')
    return FileResponse(path,filename=path.name)


@app.get('/api/session/{session}/conclusion')
def conclusion(session: str, request: Request):
    job = owned(session, request)
    result = job.get('result')
    if not result:
        raise HTTPException(409, 'Проверка ещё не завершена')
    review = build_review(result)
    lines = ['КОПЕЙКА — ЗАКЛЮЧЕНИЕ ПО СВЕРКЕ', f"Версия {job.get('version', 1)} от {job['created']}",
             f"Договор {job['contract']}; период {job['start']} — {job['end']}", '', review['conclusion'], review['scope'], '']
    lines += [f"Статьи: совпадают {review['counts']['matched']}; расхождения {review['counts']['mismatch']}; не проверены полностью {review['counts']['unverified']}",
              f"Этап: {result['context'].get('stage', 'не определён')}",
              f"ОБЩИЙ ИТОГ: КФЗ {rub(result['total']['reported'])}; подтверждено {rub(result['total']['confirmed'])}; разница {rub(result['total']['difference'])}",
              'Статус итога: '+result['total']['status'], *result['total'].get('reasons',[]), '']
    for row in result['rows']:
        item = review['rows'][row['code']]
        lines += [f"{row['code']} {row['title']} — {row['status']}",
                  f"КФЗ: {rub(row['reported'])}; документы: {rub(row['confirmed'])}; разница: {rub(row['difference'])}",
                  'Категория: ' + ', '.join(item['categories']), item['statement'], item['cause'],
                  'КФЗ: ' + item['report_source'], *[f"Источник: {s['location']} — {rub(s['value'])}" for s in item['sources']],
                  *row.get('reasons', []), '']
    lines += ['ДРУГИЕ ПОКАЗАТЕЛИ — НЕ СУММИРУЮТСЯ ПОВТОРНО']
    for item in result.get('indicators',[]):
        lines += [f"{item['title']} ({item['unit']}): {item['status']}",
                  'КФЗ: '+(item['reported'] or 'не определено')+'; подтверждено: '+(item['confirmed'] or 'не определено')+'; разница: '+(item['difference'] or 'не определено'),
                  *item['reasons'],
                  *[f"Источник: {location(result['evidence'][ref])} — {result['evidence'][ref].get('label','')}" for ref in item.get('report_evidence_ids',[])+item['evidence_ids']], '']
    lines += ['ИСКЛЮЧЕНИЯ И НЕПРОВЕРЕННЫЕ ДОКУМЕНТЫ',
              *[f"{d['file']} — {d['category']}: {d['reason']}" for d in review['document_issues']], '',
              'ОГРАНИЧЕНИЯ', *result.get('limitations', [])]
    return Response('\ufeff' + '\n'.join(lines), media_type='text/plain; charset=utf-8',
                    headers={'Content-Disposition': f'attachment; filename="kopeyka-conclusion-v{job.get("version", 1)}.txt"'})


@app.get('/api/session/{session}/conclusion.xlsx')
def conclusion_excel(session: str, request: Request):
    from kfz_core.export import workbook_bytes
    job = owned(session, request)
    if not job.get('result'):
        raise HTTPException(409,'Проверка ещё не завершена')
    return Response(workbook_bytes(job['result'],version=job.get('version',1),created=job['created']),
                    media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition':f'attachment; filename="kopeyka-conclusion-v{job.get("version",1)}.xlsx"'})


if Path('frontend/dist').is_dir():
    app.mount('/', StaticFiles(directory='frontend/dist', html=True), name='frontend')
