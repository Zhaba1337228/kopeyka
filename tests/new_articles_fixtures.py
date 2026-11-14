"""Generate synthetic browser fixtures; contains no case documents or answers."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_articles import report, document

parser = argparse.ArgumentParser()
parser.add_argument('--output', required=True, type=Path)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
report(args.output/'report.pdf', [(f'0{i}00', f'Тестовая статья {i}', f'{i*100},00') for i in range(4,8)], '2200,00')
for i in range(4,8):
    document(args.output/f'0{i}00.docx', f'0{i}00', f'{i*100},00', extra='Статья 0500' if i==4 else '')
document(args.output/'1500.docx', '1500', '50,00')
print('Synthetic fixtures created:', args.output)
