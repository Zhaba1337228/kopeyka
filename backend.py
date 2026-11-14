"""HTTP interface to the local reconciliation engine."""
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.encoders import jsonable_encoder
from starlette.concurrency import run_in_threadpool

from kfz_core import reconcile, ReportSelectionRequired
from ui.uploads import stage_uploads

app = FastAPI()
sessions = {}
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
    pending = target.with_suffix('.tmp')
    pending.write_text(json.dumps(jsonable_encoder(job), ensure_ascii=False), encoding='utf-8')
    pending.replace(target)


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
    return sorted([{'session': sid, 'created': j['created'], 'contract': j['contract'],
                    'status': j.get('status', 'Завершена')} for sid, j in sessions.items()
                   if j.get('owner') == request.state.owner], key=lambda j: j['created'], reverse=True)


@app.get('/api/session/{session}')
def restore(session: str, request: Request):
    job = owned(session, request)
    return {'session': session, 'result': job.get('result'), 'candidates': job.get('candidates', [])}


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
        for row in [*result['rows'], result['total']]:
            row['status'] = 'Недостаточно данных'
            row.setdefault('reasons', []).append('Часть комплекта не прочитана: ' + ', '.join(failures))
    job['result'] = result
    job['row_id'] = row_id
    job['decisions'] = decisions or {}
    job['status'] = 'Завершена'
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
               'status': 'Обработка', 'base': base, 'report': report_path, 'documents': paths,
               'journal': journal, 'contract': contract, 'start': start, 'end': end}
        sessions[session] = job
        save(job)
        return jsonable_encoder({'session': session, **await run_in_threadpool(calculate, job)})
    except Exception as exc:
        if session in sessions:
            sessions[session]['status'] = 'Ошибка обработки'
            save(sessions[session])
        raise HTTPException(400, str(exc)) from exc


@app.post('/api/session/{session}/recalculate')
def recalculate(session: str, body: dict, request: Request):
    job = owned(session, request)
    if not job:
        raise HTTPException(404, 'Сессия завершена. Загрузите документы снова.')
    try:
        decisions = dict(job.get('decisions', {}))
        if body.get('document_id'):
            decisions[body['document_id']] = body['decision']
        return jsonable_encoder(calculate(job, body.get('row_id', job.get('row_id')), decisions))
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get('/api/session/{session}/evidence/{evidence_id}/{kind}')
def evidence(session: str, evidence_id: str, kind: str, request: Request):
    job = owned(session, request)
    ev = job.get('result', {}).get('evidence', {}).get(evidence_id) if job else None
    key = {'image': 'image_path', 'original': 'path', 'context': 'context_image_path'}.get(kind)
    path = Path(ev[key]) if ev and key and ev.get(key) else None
    if not path or not path.is_file() or not path.resolve().is_relative_to(job['base'].resolve()):
        raise HTTPException(404, 'Файл недоступен')
    return FileResponse(path, filename=path.name if kind == 'original' else None)


if Path('frontend/dist').is_dir():
    app.mount('/', StaticFiles(directory='frontend/dist', html=True), name='frontend')
