"""Safe, bounded staging of browser uploads. Source files are never modified."""
from __future__ import annotations

import io
import hashlib
import re
import zipfile
from pathlib import Path

SUPPORTED = {".pdf", ".png", ".jpg", ".jpeg", ".xlsx", ".docx"}
MAX_FILES = 500
MAX_TOTAL_BYTES = 250 * 1024 * 1024


def validate_content(name: str, data: bytes) -> None:
    suffix = Path(name).suffix.lower()
    valid = {'.pdf': data.startswith(b'%PDF-'), '.png': data.startswith(b'\x89PNG\r\n\x1a\n'),
             '.jpg': data.startswith(b'\xff\xd8\xff'), '.jpeg': data.startswith(b'\xff\xd8\xff')}
    if suffix in valid and not valid[suffix]:
        raise ValueError('Содержимое файла не соответствует формату: ' + name)
    if suffix in {'.xlsx', '.docx'}:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                members = archive.infolist()
                if len(members) > 10000 or sum(i.file_size for i in members) > MAX_TOTAL_BYTES:
                    raise ValueError('Слишком большой распакованный документ Office')
                names = {i.filename for i in members}
                required = 'xl/workbook.xml' if suffix == '.xlsx' else 'word/document.xml'
                if required not in names or any('vbaproject' in n.lower() for n in names):
                    raise ValueError('Неверный формат Office или документ содержит макросы')
        except zipfile.BadZipFile as exc:
            raise ValueError('Повреждённый документ Office: ' + name) from exc


def archive_name(info: zipfile.ZipInfo) -> str:
    name = info.filename
    if not info.flag_bits & 0x800:
        try:
            name = name.encode("cp437").decode("utf-8")
        except (UnicodeError, LookupError):
            pass
    return name


def safe_name(name: str) -> str:
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(". ") or "документ"


def excluded_input(name: str) -> str | None:
    normalized = name.lower().replace("ё", "е")
    if "правильные" in normalized and "ответ" in normalized:
        return "Эталон для приёмки: не является входом сверки и не обрабатывается"
    if "readme" in Path(normalized).name:
        return "README набора: служебное описание, не подтверждающий документ"
    if Path(name).suffix.lower() not in SUPPORTED:
        return "Служебный файл или неподдерживаемый формат: не включён в расчёт"
    if Path(name).name.startswith("~$"):
        return "Временный файл Office: не включён в расчёт"
    return None


def stage_uploads(report, uploads, directory: Path) -> tuple[Path, list[Path], list[dict]]:
    """Write individual files into numbered folders; ZIP paths are never trusted."""
    directory.mkdir(parents=True, exist_ok=True)
    report_dir = directory / "report"
    report_dir.mkdir(exist_ok=True)
    report_path = report_dir / safe_name(report.name)
    report_data = report.getvalue()
    if len(report_data) > MAX_TOTAL_BYTES or report_path.suffix.lower() not in {'.pdf', '.png', '.jpg', '.jpeg'}:
        raise ValueError('Неверный формат или размер карточки КФЗ')
    validate_content(report_path.name, report_data)
    report_path.write_bytes(report_data)
    report_hash = hashlib.sha256(report_data).digest()
    paths: list[Path] = []
    journal: list[dict] = []
    total_bytes = 0
    entry_count = 0

    def stage(name: str, data: bytes) -> None:
        nonlocal total_bytes, entry_count
        entry_count += 1
        total_bytes += len(data)
        if entry_count > MAX_FILES or total_bytes > MAX_TOTAL_BYTES:
            raise ValueError("Комплект превышает лимит: 500 файлов или 250 МБ после распаковки.")
        reason = excluded_input(name)
        if hashlib.sha256(data).digest() == report_hash:
            reason = "Карточка КФЗ уже загружена отдельно; не включается в подтверждающие документы"
        if reason:
            journal.append({"file": name, "status": "excluded", "reason": reason, "code": "", "amount": None})
            return
        try:
            validate_content(name, data)
        except ValueError as exc:
            journal.append({'file': name, 'status': 'error', 'reason': str(exc), 'code': None, 'amount': None})
            return
        folder = directory / "documents" / f"{entry_count:04d}"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / safe_name(name)
        target.write_bytes(data)
        paths.append(target)

    for upload in uploads:
        if Path(upload.name).suffix.lower() != ".zip":
            stage(upload.name, upload.getvalue())
            continue
        try:
            with zipfile.ZipFile(io.BytesIO(upload.getvalue())) as archive:
                members = [i for i in archive.infolist() if not i.is_dir()]
                if len(members) + entry_count > MAX_FILES or sum(i.file_size for i in members) + total_bytes > MAX_TOTAL_BYTES:
                    raise ValueError("Архив превышает лимит: 500 файлов или 250 МБ после распаковки.")
                for info in members:
                    name = archive_name(info)
                    if info.flag_bits & 1:
                        journal.append({"file": name, "status": "error", "reason": "Файл в архиве защищён паролем", "code": "", "amount": None})
                        continue
                    try:
                        stage(name, archive.read(info))
                    except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
                        journal.append({"file": name, "status": "error", "reason": f"Не удалось прочитать файл из ZIP: {exc}", "code": "", "amount": None})
        except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
            journal.append({"file": upload.name, "status": "error", "reason": f"Не удалось открыть ZIP: {exc}", "code": "", "amount": None})
    return report_path, paths, journal
