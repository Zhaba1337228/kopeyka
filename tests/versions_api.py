"""Real HTTP regression: immutable versions, drafts, comparison and ownership.

Run against the running app with --input pointing to the private case directory.
The case files are only read and never modified.
"""
import argparse
import hashlib
import json
from pathlib import Path

import requests


def signature(result):
    return [(r['code'], r['reported'], r['confirmed'], r['difference'], r['status']) for r in result['rows']]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--url', default='http://127.0.0.1:8503')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    files = sorted(args.input.glob('*'))
    report = next(p for p in files if p.name.startswith('01_'))
    february = next(p for p in files if p.name.startswith('02_02'))
    docs = [p for p in files if p.name.startswith(tuple(f'{i:02d}_' for i in range(2, 16)))]
    originals = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in [report, *docs]}
    client = requests.Session()
    client.get(args.url).raise_for_status()
    data = {'contract': '24/03/15-01', 'start': '2026-01-01', 'end': '2026-05-31'}

    def upload(field, path):
        return field, (path.name, path.read_bytes())

    def call(path, **kwargs):
        response = client.post(args.url + path, timeout=240, **kwargs)
        assert response.status_code == 200, response.text
        return response.json()

    first = call('/api/check', data=data, files=[upload('report', report), *[upload('documents', p) for p in docs if p != february]])
    sid = first['session']
    rows = {r['code']: r for r in first['result']['rows']}
    assert rows['0200']['status'] == 'Недостаточно данных'
    assert rows['0800']['difference'] == '12500.00'
    assert first['result']['review']['counts'] == {'matched': 2, 'mismatch': 1, 'unverified': 1}
    explanation = first['result']['review']['rows']['0800']
    assert '12 500,00' in explanation['statement'] and 'не установлена' in explanation['cause']
    assert len(explanation['sources']) == 4
    request_text = explanation['request_text'] + '\nКомментарий проверяющего: проверить регистр.'
    draft = client.put(args.url + f'/api/session/{sid}/request/0800', json={'subject': explanation['subject'], 'text': request_text})
    assert draft.status_code == 200, draft.text
    second = call(f'/api/session/{sid}/documents', files=[upload('documents', february)], data={'note': 'Получена февральская ведомость'})
    assert second['version']['version'] == 2 and second['version']['parent'] == sid
    assert second['session'] != sid
    rows = {r['code']: r for r in second['result']['rows']}
    assert rows['0200']['status'] == 'Совпадает' and rows['0200']['confirmed'] == '5558474.83'
    assert rows['0800']['status'] == 'Расхождение' and rows['0800']['difference'] == '12500.00'
    changes = {r['code']: r for r in second['comparison']['rows']}
    assert changes['0200']['resolved'] and changes['0200']['confirmed_change'] == '1117000.00'
    assert not changes['0800']['changed']
    assert len(second['comparison']['added_documents']) == 1
    restored = client.get(args.url + f'/api/session/{sid}').json()
    assert signature(restored['result']) == signature(first['result'])
    assert restored['drafts']['0800']['text'] == request_text
    assert not second['drafts'], 'A request from the old calculation must not become a new request silently'
    assert len(restored['versions']) == 2
    for version in (first, second):
        ev = version['result']['rows'][2]['report_evidence_id']
        image = client.get(args.url + f'/api/session/{version["session"]}/evidence/{ev}/image')
        assert image.status_code == 200 and image.headers['content-type'].startswith('image/')
        original = client.get(args.url + f'/api/session/{version["session"]}/evidence/{ev}/original')
        assert hashlib.sha256(original.content).hexdigest() == originals[str(report)]
    third = call(f'/api/session/{second["session"]}/documents', files=[upload('documents', february)])
    assert signature(third['result']) == signature(second['result'])
    assert any(d['category'] == 'Дублирование' for d in third['comparison']['added_documents'])
    assert not any(r['changed'] for r in third['comparison']['rows'])
    broken = call(f'/api/session/{third["session"]}/documents', files=[('documents', ('broken.pdf', b'not a PDF'))])
    assert broken['result']['total']['status'] == 'Недостаточно данных'
    assert any(d['category'] == 'Ошибка чтения' for d in broken['result']['review']['document_issues'])
    assert not any(r['resolved'] for r in broken['comparison']['rows'])
    branch_response = client.post(args.url + f'/api/session/{sid}/recalculate', json={})
    assert branch_response.status_code == 200, branch_response.text
    branch = branch_response.json()
    assert branch['version']['parent'] == sid and branch['version']['version'] == 5
    assert signature(branch['result']) == signature(first['result'])
    assert not any(r['changed'] for r in branch['comparison']['rows'])
    assert signature(client.get(args.url + f'/api/session/{sid}').json()['result']) == signature(first['result'])
    other = requests.Session()
    assert other.get(args.url + f'/api/session/{sid}').status_code == 404
    assert other.put(args.url + f'/api/session/{sid}/request/0800', json={'subject': 'x', 'text': 'x'}).status_code == 404
    assert other.post(args.url + f'/api/session/{sid}/documents', files=[upload('documents', february)]).status_code == 404
    assert other.get(args.url + '/api/history').json() == []
    exported = client.get(args.url + f'/api/session/{second["session"]}/conclusion')
    assert exported.status_code == 200 and '12500' not in exported.text and '12 500,00' in exported.text
    assert 'Дублирование' in exported.text and 'Неверный период' in exported.text
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == value for p, value in originals.items())
    summary = {'passed': True, 'checks': ['missing-month', 'explanation', 'saved-request', 'new-version', 'comparison',
               'old-version-immutable', 'source-bytes', 'duplicate', 'corrupt-file', 'version-branch', 'owner-isolation', 'conclusion-export', 'originals-unchanged'],
               'root_session': sid, 'latest_session': broken['session'], 'cookies': client.cookies.get_dict()}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(summary, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({k: v for k, v in summary.items() if k != 'cookies'}, ensure_ascii=True))


if __name__ == '__main__':
    main()
