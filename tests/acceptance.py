"""Acceptance checks against a separately supplied, private case dataset.

This module is never imported by the application and is excluded from its image.
Run with ``python tests/acceptance.py --input PATH --reference PATH``.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import zipfile
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def money(value):
    return Decimal(str(value)).quantize(Decimal("0.01"))


def expected_values(reference: Path):
    from openpyxl import load_workbook

    book = load_workbook(reference, read_only=True, data_only=True)
    sheet = book["Эталон"]
    values = {}
    for code, row in zip(("0200", "0300", "0800", "0900", "total"), range(4, 9)):
        values[code] = {
            key: money(sheet.cell(row, col).value)
            for key, col in (("reported", 2), ("confirmed", 3), ("difference", 4))
        }
    book.close()
    return values


def inputs(input_dir: Path):
    files = sorted(p for p in input_dir.rglob("*") if p.is_file())
    reports = [p for p in files if p.name.startswith("01_") and p.suffix.lower() == ".pdf"]
    assert len(reports) == 1, "Нужна одна исходная КФЗ с префиксом 01_"
    report = reports[0]
    documents = [
        p for p in files
        if p != report and not p.name.startswith(("!", "00_"))
        and p.suffix.lower() in {".pdf", ".png", ".jpg", ".jpeg", ".docx", ".xlsx"}
    ]
    assert len(documents) == 22, f"Ожидалось 22 файла первички, найдено {len(documents)}"
    return report, documents


def check_evidence(result, row, output_dir):
    from PIL import Image
    from kfz_core import render_evidence

    ids = list(row["evidence_ids"])
    assert len(ids) == 4, f"0800: ожидалось 4 источника, найдено {len(ids)}"
    ids.append(row["report_evidence_id"])
    evidence = result["evidence"]
    for evidence_id in ids:
        entry = evidence[evidence_id]
        assert entry.get("text"), f"Нет исходного текста: {entry}"
        assert entry.get("value") is not None, f"Нет распознанного значения: {entry}"
        assert Path(entry["path"]).is_file(), f"Источник не существует: {entry}"
        if entry.get("kind") in {"pdf", "png", "jpg", "jpeg"}:
            assert entry.get("bbox"), f"Нет координат фрагмента: {entry}"
            render_evidence(entry, output_dir / "evidence")
        if entry.get("image_path"):
            with Image.open(entry["image_path"]) as image:
                assert image.width > 30 and image.height > 10
                image.verify()
        else:
            assert entry.get("kind") in {"docx", "xlsx", "table", "paragraph"}, entry
            assert entry.get("cell") or entry.get("label") or entry.get("text"), entry


def check_baseline(result, expected, output_dir):
    rows = {row["code"]: row for row in result["rows"]}
    assert set(rows) == {"0200", "0300", "0800", "0900"}, set(rows)
    combined = {**rows, "total": result["total"]}
    for code, expected_row in expected.items():
        row = combined[code]
        for key, value in expected_row.items():
            assert money(row[key]) == value, f"{code} {key}: {row[key]} != {value}"
        status = "Расхождение" if expected_row["difference"] else "Совпадает"
        assert row["status"] == status, f"{code}: {row['status']} != {status}"
    assert money(rows["0800"]["reported"]) - money(rows["0800"]["confirmed"]) == money(rows["0800"]["difference"])
    documents = result["documents"]
    adm = [d for d in documents if "ADM-27" in d["file"]]
    assert len(adm) == 2 and sum(d["status"] == "accepted" for d in adm) == 1, adm
    assert sum(d["status"] == "excluded" for d in adm) == 1, adm
    june = next(d for d in documents if "0603" in d["file"])
    other = next(d for d in documents if "ADM-75" in d["file"])
    assert june["status"] == other["status"] == "excluded"
    assert "период" in june["reason"].lower(), june
    assert "договор" in other["reason"].lower(), other
    assert june["reason"] != other["reason"]
    assert len([d for d in documents if d["status"] == "accepted"]) == 19
    assert result.get("limitations"), "Не отмечены непроверяемые показатели"
    check_evidence(result, rows["0800"], output_dir)


def signature(result):
    return {
        row["code"]: tuple(row[key] for key in ("reported", "confirmed", "difference", "status"))
        for row in [*result["rows"], result["total"]]
    }


def check_mutations(args, baseline, report, documents, expected):
    """Only called after the full, unchanged baseline has passed."""
    from docx import Document
    from openpyxl import load_workbook
    import fitz
    from kfz_core import ReportSelectionRequired, reconcile

    passed = []
    work = args.output / "mutations"
    work.mkdir(parents=True, exist_ok=True)

    def run_scenario(name, scenario_documents, scenario_report=report, decisions=None, report_row_id=None):
        # Byte-keyed OCR cache is shared across cases; current paths are re-bound.
        result = reconcile(scenario_report, scenario_documents, output_dir=args.output / "baseline", decisions=decisions, report_row_id=report_row_id)
        (work / f"{name}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return result

    def record(name):
        passed.append(name)
        print(f"PASS {name}", flush=True)

    renamed = work / "renamed"
    renamed.mkdir(exist_ok=True)
    renamed_documents = []
    for i, source in enumerate(documents):
        target = renamed / f"file_{i:03d}{source.suffix}"
        shutil.copyfile(source, target)
        renamed_documents.append(target)
    renamed_report = renamed / "report.pdf"
    shutil.copyfile(report, renamed_report)
    result = run_scenario("rename_and_order", list(reversed(renamed_documents)), renamed_report)
    assert signature(result) == signature(baseline), "Переименование/перестановка изменили результат"
    assert sum(d["status"] == "accepted" for d in result["documents"]) == 19
    record("rename_and_order")

    source_docx = next(p for p in documents if p.name.startswith("06_"))
    changed = work / "amount_plus_1000.docx"
    doc = Document(source_docx)
    cell = doc.tables[0].rows[1].cells[1]
    previous = money(cell.text.replace(" ", "").replace(",", "."))
    cell.text = f"{previous + Decimal('1000'):,.2f}".replace(",", " ").replace(".", ",")
    doc.save(changed)
    replacement = [changed if p == source_docx else p for p in documents]
    result = run_scenario("amount_plus_1000", replacement)
    rows = {row["code"]: row for row in result["rows"]}
    assert money(rows["0800"]["difference"]) == Decimal("11500.00")
    assert money(rows["0800"]["confirmed"]) == expected["0800"]["confirmed"] + 1000
    assert rows["0800"]["status"] == "Расхождение"
    assert money(result["total"]["difference"]) == Decimal("11500.00")
    record("amount_plus_1000")

    february = next(p for p in documents if p.name.startswith("02_02_"))
    book = load_workbook(february, read_only=True, data_only=True)
    omitted_amount = money(book["Ведомость"]["C6"].value) + money(book["Ведомость"]["C7"].value)
    book.close()
    result = run_scenario("missing_february_payroll", [p for p in documents if p != february])
    rows = {row["code"]: row for row in result["rows"]}
    assert rows["0200"]["status"] == result["total"]["status"] == "Недостаточно данных"
    assert money(rows["0200"]["confirmed"]) == expected["0200"]["confirmed"] - omitted_amount
    assert "феврал" in " ".join(rows["0200"]["reasons"]).lower()
    assert rows["0800"]["status"] == "Расхождение"
    assert money(rows["0800"]["difference"]) == Decimal("12500.00")
    record("missing_february_payroll")

    corrupt = work / "unreadable_document.pdf"
    corrupt.write_bytes(b"This is deliberately not a PDF. Acceptance test only.")
    result = run_scenario("corrupt_document", [*documents, corrupt])
    errors = [d for d in result["documents"] if d["status"] == "error"]
    assert len(errors) == 1 and errors[0]["reason"], errors
    assert result["total"]["status"] == "Недостаточно данных"
    assert money(result["total"]["confirmed"]) == expected["total"]["confirmed"]
    assert sum(d["status"] == "accepted" for d in result["documents"]) == 19
    record("corrupt_document")

    semantic = work / "same_content_new_bytes.docx"
    shutil.copyfile(source_docx, semantic)
    with zipfile.ZipFile(semantic, "a") as archive:
        archive.comment = b"Different bytes, same document contents; acceptance check."
    result = run_scenario("semantic_duplicate", [*documents, semantic])
    assert signature(result) == signature(baseline)
    duplicates = [d for d in result["documents"] if d["file"] == semantic.name]
    assert len(duplicates) == 1 and duplicates[0]["status"] == "excluded"
    assert "повтор" in duplicates[0]["reason"].lower(), duplicates
    record("semantic_duplicate")

    result = run_scenario("conflicting_amounts", [*documents, changed])
    rows = {row["code"]: row for row in result["rows"]}
    conflicts = [d for d in result["documents"] if "конфликт" in d["reason"].lower()]
    assert len(conflicts) == 2 and all(d["status"] == "ambiguous" for d in conflicts)
    assert rows["0800"]["status"] == result["total"]["status"] == "Недостаточно данных"
    assert rows["0200"]["status"] == "Совпадает"
    record("conflicting_amounts")

    conflict_decisions = {
        entry["id"]: {"action": "include", "code": "0800", "reason": "Проверка защиты от двойного включения"}
        for entry in conflicts
    }
    try:
        overridden = run_scenario("conflicting_manual_include", [*documents, changed], decisions=conflict_decisions)
    except ValueError as error:
        assert any(word in str(error).lower() for word in ("конфликт", "один", "повтор", "дубл")), str(error)
    else:
        conflict_ids = set(conflict_decisions)
        included = [d for d in overridden["documents"] if d["id"] in conflict_ids and d["status"] == "accepted"]
        assert len(included) <= 1, "Ручное решение позволило дважды принять документы с конфликтующими суммами"
    record("conflicting_manual_include_guard")

    ambiguous = work / "ambiguous_purpose.docx"
    doc = Document(source_docx)
    doc.paragraphs[4].text = "Выполнены разовые работы по проекту."
    doc.paragraphs[5].text = "Назначение расходов требует подтверждения проверяющего."
    doc.tables[0].rows[1].cells[0].text = "Акт работ КЛ-31"
    doc.save(ambiguous)
    scenario_docs = [ambiguous if p == source_docx else p for p in documents]
    pending = run_scenario("ambiguous_classification", scenario_docs)
    entry = next(d for d in pending["documents"] if d["file"] == ambiguous.name)
    assert entry["status"] == "ambiguous" and entry["code"] is None, entry
    decision = {"action": "include", "code": "0800", "reason": "Проверено: работы обеспечивают производственное оборудование"}
    resolved = run_scenario("manual_classification", scenario_docs, decisions={entry["id"]: decision})
    assert signature(resolved) == signature(baseline), "Ручная классификация не пересчитала сумму"
    accepted = next(d for d in resolved["documents"] if d["id"] == entry["id"])
    assert accepted["status"] == "accepted" and accepted["decision"] == decision
    assert accepted["before_decision"]["status"] == "ambiguous"
    assert decision["reason"] in accepted["reason"]
    record("manual_classification_with_audit")

    duplicated_report = work / "two_matching_report_rows.pdf"
    with fitz.open(report) as original, fitz.open() as duplicate:
        duplicate.insert_pdf(original)
        duplicate.insert_pdf(original)
        duplicate.save(duplicated_report)
    try:
        run_scenario("multiple_report_rows_without_choice", documents, duplicated_report)
    except ReportSelectionRequired as error:
        candidates = error.candidates
        assert len(candidates) == 2, candidates
    else:
        raise AssertionError("Две подходящие строки КФЗ не потребовали явного выбора")
    selected = run_scenario("multiple_report_rows_selected", documents, duplicated_report, report_row_id=candidates[-1]["id"])
    assert signature(selected) == signature(baseline)
    assert selected["context"]["report_row_id"] == candidates[-1]["id"]
    for row in [*selected["rows"], selected["total"]]:
        assert selected["evidence"][row["report_evidence_id"]]["page"] == 2
    record("multiple_report_row_selection")

    return passed


def run(args):
    from kfz_core import reconcile

    report, documents = inputs(args.input)
    expected = expected_values(args.reference)
    args.output.mkdir(parents=True, exist_ok=True)
    result = reconcile(report, documents, output_dir=args.output / "baseline")
    (args.output / "baseline.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    check_baseline(result, expected, args.output)
    print("PASS baseline: five exact totals, 19 accepted documents, three exclusions, four 0800 sources and KFZ fragment", flush=True)
    passed = ["baseline"]
    if args.mutations:
        passed += check_mutations(args, result, report, documents, expected)
    (args.output / "acceptance_summary.json").write_text(json.dumps({"passed": passed, "failed": []}, ensure_ascii=False, indent=2), encoding="utf-8")
    return result, report, documents, expected


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "work" / "input")
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "work" / "acceptance" / "results")
    parser.add_argument("--mutations", action="store_true", help="После baseline проверить изменённые копии; исходники не меняются")
    run(parser.parse_args())
