"""Normalize contract identifiers without prescribing a particular numbering scheme."""
import re


def normalize_contract(value):
    return re.sub(r'\s+', '', str(value)).replace('–', '-').replace('—', '-').upper().strip('№:;,')


def contracts_in(text):
    found = set()
    # Legacy forms sometimes omit a label or have OCR spaces inside the number.
    for value in re.findall(r'\d{2}\s*/\s*\d{2}\s*/\s*\d{2}\s*[-–]\s*\d{2}', text):
        found.add(normalize_contract(value))
    for match in re.finditer(r'(?im)\b(?:договор(?:а|у|ом)?|контракт(?:а|у|ом)?)\s*(?:№|номер|N)?\s*[:№]?\s*(\d{2}\s*/\s*\d{2}\s*/\s*\d{2}\s*[-–]\s*\d{2}|[\w][\w./\-–—]*)', text):
        value = match[1].strip('.,;:')
        if any(ch.isdigit() for ch in value):
            found.add(normalize_contract(value))
    return sorted(found)
