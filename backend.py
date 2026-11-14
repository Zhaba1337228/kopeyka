"""HTTP interface to the local reconciliation engine."""
import tempfile
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.encoders import jsonable_encoder

from kfz_core import reconcile, ReportSelectionRequired
from ui.uploads import stage_uploads

app = FastAPI()
sessions = {}


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
    return {'result': result}


@app.post('/api/check')
async def check(report: UploadFile = File(...), documents: list[UploadFile] = File(...),
                contract: str = Form(...), start: str = Form(...), end: str = Form(...)):
    if not contract.strip() or start > end:
        raise HTTPException(400, 'Проверьте договор и период')
    temporary = tempfile.TemporaryDirectory(prefix='kopeyka_')
    base = Path(temporary.name)
    try:
        report_file = Upload(report.filename, await report.read())
        files = [Upload(f.filename, await f.read()) for f in documents]
        report_path, paths, journal = stage_uploads(report_file, files, base / 'input')
        job = {'temporary': temporary, 'base': base, 'report': report_path, 'documents': paths,
               'journal': journal, 'contract': contract, 'start': start, 'end': end}
        session = uuid4().hex
        sessions[session] = job
        return jsonable_encoder({'session': session, **calculate(job)})
    except Exception as exc:
        temporary.cleanup()
        raise HTTPException(400, str(exc)) from exc


@app.post('/api/session/{session}/recalculate')
def recalculate(session: str, body: dict):
    job = sessions.get(session)
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
def evidence(session: str, evidence_id: str, kind: str):
    job = sessions.get(session)
    ev = job.get('result', {}).get('evidence', {}).get(evidence_id) if job else None
    key = {'image': 'image_path', 'original': 'path', 'context': 'context_image_path'}.get(kind)
    path = Path(ev[key]) if ev and key and ev.get(key) else None
    if not path or not path.is_file() or not path.resolve().is_relative_to(job['base'].resolve()):
        raise HTTPException(404, 'Файл недоступен')
    return FileResponse(path, filename=path.name if kind == 'original' else None)


if Path('frontend/dist').is_dir():
    app.mount('/', StaticFiles(directory='frontend/dist', html=True), name='frontend')
