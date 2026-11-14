import argparse
import json
from pathlib import Path

from . import reconcile


def main():
    parser = argparse.ArgumentParser(description='Локальная сверка КФЗ')
    parser.add_argument('--report', required=True, type=Path)
    parser.add_argument('--documents', required=True, type=Path)
    parser.add_argument('--output', type=Path, default=Path('work/result.json'))
    parser.add_argument('--work-dir', type=Path, default=Path('work/run'))
    parser.add_argument('--contract', default='24/03/15-01')
    parser.add_argument('--start-date', default='2026-01-01')
    parser.add_argument('--end-date', default='2026-05-31')
    args = parser.parse_args()
    paths = [p for p in args.documents.rglob('*') if p.is_file() and p.resolve() != args.report.resolve() and p.suffix.lower() in ('.pdf', '.png', '.jpg', '.jpeg', '.docx', '.xlsx') and not p.name.startswith('!')]
    result = reconcile(args.report, paths, output_dir=args.work_dir, contract=args.contract, start_date=args.start_date, end_date=args.end_date)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'rows': result['rows'], 'total': result['total']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
