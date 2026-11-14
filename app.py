"""Local, evidence-first interface for reconciliation of КФЗ documents."""
from __future__ import annotations

import html
import tempfile
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd
import streamlit as st

from ui.styles import CSS
from ui.uploads import stage_uploads

st.set_page_config(page_title="Копейка — сверка КФЗ", page_icon="▦", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)

DOC_STATUSES = {"accepted": "Принят", "excluded": "Исключён", "error": "Не прочитан", "ambiguous": "Требует решения"}
ROW_CODES = ("0200", "0300", "0800", "0900")


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


def money(value, signed=False) -> str:
    if value is None or value == "":
        return "—"
    try:
        number = Decimal(str(value))
        sign = "+" if signed and number > 0 else ""
        return sign + f"{number:,.2f}".replace(",", "\u202f").replace(".", ",")
    except (InvalidOperation, ValueError, TypeError):
        return str(value)


def badge(status) -> str:
    style = {"Совпадает": "match", "Расхождение": "diff"}.get(status, "missing")
    return f'<span class="badge badge-{style}">{esc(status)}</span>'


def set_source(evidence_id: str, code: str) -> None:
    st.session_state[f"source_{code}"] = evidence_id


def safe_report(result: dict, upload_journal: list[dict]) -> dict:
    result.setdefault("documents", []).extend(upload_journal)
    failures = [item for item in upload_journal if item.get("status") == "error"]
    if failures:
        reason = "Часть комплекта не прочитана: " + "; ".join(str(i["file"]) for i in failures)
        for row in [*result.get("rows", []), result.get("total", {})]:
            row["status"] = "Недостаточно данных"
            row.setdefault("reasons", []).append(reason)
    return result


def run_check(row_id=None) -> None:
    from kfz_core import reconcile

    job = st.session_state["job"]
    progress = st.progress(0, text="Читаем карточку учёта фактических затрат…")

    def on_progress(fraction, message):
        progress.progress(max(0.0, min(float(fraction), 1.0)), text=str(message))

    try:
        result = reconcile(
            job["report_path"], job["document_paths"], output_dir=job["output_dir"],
            contract=job["contract"], start_date=job["start_date"], end_date=job["end_date"],
            report_row_id=row_id, decisions=st.session_state.get("decisions", {}), progress=on_progress,
        )
        st.session_state["result"] = safe_report(result, job["upload_journal"])
        st.session_state["chosen_report_row"] = row_id
        st.session_state.pop("candidates", None)
        st.session_state.pop("run_error", None)
        progress.progress(1.0, text="Сверка завершена. Все суммы доступны с источниками.")
    except Exception as exc:
        if hasattr(exc, "candidates"):
            st.session_state["candidates"] = exc.candidates
            st.session_state.pop("result", None)
        else:
            st.session_state["run_error"] = str(exc) or type(exc).__name__
    finally:
        progress.empty()


def render_source(evidence_id, result, key_prefix="main") -> None:
    evidence = result.get("evidence", {}).get(evidence_id)
    if not evidence:
        st.info("Для выбранной суммы пока нет читаемого фрагмента источника.")
        return
    st.markdown(f"### {esc(evidence.get('file', 'Источник'))}")
    positions = []
    if evidence.get("page") is not None:
        positions.append(f"Страница {evidence['page']}")
    if evidence.get("sheet"):
        positions.append(f"Лист «{evidence['sheet']}»")
    if evidence.get("cell"):
        positions.append(f"Ячейка {evidence['cell']}")
    if evidence.get("table"):
        positions.append(f"Таблица {evidence['table']}, строка {evidence.get('row', '—')}")
    elif evidence.get("paragraph"):
        positions.append(f"Абзац {evidence['paragraph']}")
    if evidence.get("label"):
        positions.append(str(evidence["label"]))
    st.caption(" · ".join(positions) or "Фрагмент исходного документа")
    if evidence.get("value") is not None:
        st.markdown(f"**Распознанное значение: {money(evidence['value'])} руб.**")

    image_path = evidence.get("image_path")
    kind = str(evidence.get("kind", "")).lower()
    visual_source = kind in {"pdf", "png", "jpg", "jpeg", "image", "scan"}
    render_problem = evidence.get("render_error")
    image_available = bool(image_path and Path(image_path).is_file())
    if not image_available and visual_source and not render_problem:
        try:
            from kfz_core import render_evidence
            image_path = render_evidence(evidence, st.session_state["job"]["output_dir"])
            image_available = bool(image_path and Path(image_path).is_file())
        except Exception as exc:
            render_problem = str(exc)
    if image_available:
        try:
            st.image(str(image_path), caption="Фрагмент источника. Область значения выделена.", use_container_width=True)
        except Exception as exc:
            image_available = False
            render_problem = str(exc)
    if visual_source and not image_available:
        st.warning("Изображение подтверждающего фрагмента недоступно. Распознанный текст показан ниже; для визуальной проверки скачайте исходный файл.")
        if render_problem:
            st.caption("Причина: " + str(render_problem))
    context_image = evidence.get("context_image_path")
    if context_image and Path(context_image).is_file():
        with st.expander("Строка КФЗ целиком"):
            try:
                st.image(str(context_image), caption="Выбранная строка отчёта с обозначением года и выделенной графой.", use_container_width=True)
            except Exception as exc:
                st.warning(f"Не удалось открыть контекст строки: {exc}")

    source_text = evidence.get("fragment_text") or evidence.get("text") or evidence.get("raw_text")
    if source_text:
        if image_available:
            with st.expander("Распознанный текст"):
                st.text(str(source_text))
        else:
            st.markdown(f'<div class="evidence-text">{esc(source_text)}</div>', unsafe_allow_html=True)
            st.caption("Текст извлечён из указанного листа, строки таблицы или абзаца.")
            if evidence.get("fragment_text") and evidence.get("text") != evidence["fragment_text"]:
                with st.expander("Реквизиты и полный текст документа"):
                    st.text(str(evidence.get("text", "")))
    path = evidence.get("path")
    if path and Path(path).is_file():
        st.download_button("Скачать исходный файл", Path(path).read_bytes(), file_name=Path(path).name,
                           key=f"download_{key_prefix}_{evidence_id}", use_container_width=True)


def render_uploads(has_result: bool) -> None:
    with st.expander("Файлы и параметры сверки", expanded=not has_result):
        c1, c2 = st.columns([1, 1.35], gap="large")
        with c1:
            st.markdown("### 1. Карточка КФЗ")
            report = st.file_uploader("Загрузите КФЗ", type=["pdf", "png", "jpg", "jpeg"], key="report_upload",
                                      help="КФЗ в PDF или изображении PNG/JPG со строкой «3 этап, 2026 г.». Исходный файл не изменяется.")
        with c2:
            st.markdown("### 2. Подтверждающие документы")
            documents = st.file_uploader("Загрузите комплект", type=["zip", "pdf", "png", "jpg", "jpeg", "xlsx", "docx"],
                                         accept_multiple_files=True, key="documents_upload",
                                         help="Можно выбрать ZIP или несколько файлов. Эталон ответов не участвует в расчёте.")
        params = st.columns([1.3, 1, 1, 1.25])
        contract = params[0].text_input("Договор", value="24/03/15-01")
        start = params[1].date_input("Начало периода", value=date(2026, 1, 1), format="DD.MM.YYYY")
        end = params[2].date_input("Конец периода", value=date(2026, 5, 31), format="DD.MM.YYYY")
        params[3].text_input("Строка отчёта", value=f"3 этап, {start.year} г.", key=f"report_row_hint_{start.year}", disabled=True,
                             help="В первой версии поддерживается выданная форма. Если найдено несколько строк, появится выбор.")
        if start > end:
            st.error("Начало периода должно быть не позже его окончания.")
        action, explanation = st.columns([1, 3])
        clicked = action.button("Проверить", type="primary", use_container_width=True,
                                disabled=not report or not documents or not contract.strip() or start > end)
        explanation.caption("Распознавание и расчёт выполняются локально. Файлы не передаются во внешние сервисы.")
        if clicked:
            previous = st.session_state.pop("workspace", None)
            if previous:
                previous.cleanup()
            for state_key in ("result", "candidates", "run_error", "decisions", "decision_history", "result_table"):
                st.session_state.pop(state_key, None)
            workspace = tempfile.TemporaryDirectory(prefix="kfz_session_")
            st.session_state["workspace"] = workspace
            base = Path(workspace.name)
            try:
                report_path, document_paths, upload_journal = stage_uploads(report, documents, base / "input")
                st.session_state["job"] = {"report_path": report_path, "document_paths": document_paths,
                                          "output_dir": base / "evidence", "upload_journal": upload_journal,
                                          "contract": contract.strip(), "start_date": start.isoformat(), "end_date": end.isoformat()}
                st.session_state["decisions"] = {}
                run_check()
            except Exception as exc:
                st.session_state["run_error"] = str(exc)
            st.rerun()


def render_selection() -> None:
    candidates = st.session_state.get("candidates")
    if not candidates:
        return
    st.warning("В КФЗ найдено несколько подходящих строк. Выберите строку для сверки.")
    candidate = st.selectbox("Строка КФЗ", candidates, format_func=lambda row: row.get("label", str(row.get("id"))))
    if st.button("Проверить выбранную строку", type="primary"):
        run_check(candidate["id"])
        st.rerun()


def render_summary(result) -> None:
    total = result.get("total", {})
    rows = result.get("rows", [])
    differences = [row for row in rows if row.get("difference") is not None and Decimal(str(row["difference"])) != 0 and row.get("status") != "Недостаточно данных"]
    incomplete = [row for row in rows if row.get("status") == "Недостаточно данных"]
    provisional = [row for row in incomplete if row.get("difference") is not None and Decimal(str(row["difference"])) != 0]
    if incomplete:
        st.info("Сверка частичная: не все расходы подтверждены. Ниже показана подтверждённая часть; причины указаны у каждой статьи.")
    elif differences:
        st.warning("Обнаружено расхождение. Откройте статью, чтобы проверить расчёт и подтверждающие фрагменты.")
    else:
        st.success("Суммы укрупнённых статей совпадают в пределах принятых документов.")
    for row in differences:
        delta = Decimal(str(row["difference"]))
        direction = "больше" if delta > 0 else "меньше"
        st.markdown(f"По статье **{row['code']}** в КФЗ указано на **{money(abs(delta))} руб. {direction}**, чем подтверждают принятые документы.")
    for row in provisional:
        st.caption(f"По статье {row['code']} сохранена разница с подтверждённой частью: {money(row['difference'], True)} руб. Из-за недостатка данных это предварительная разница, а не окончательный вывод.")
    c1, c2, c3 = st.columns(3)
    cards = [(c1, "В КФЗ за выбранный период", total.get("reported"), "По строке отчёта", False),
             (c2, "Подтверждённая часть" if incomplete else "По принятым документам", total.get("confirmed"), "Сумма верхнеуровневых статей", False),
             (c3, "Разница: КФЗ − документы", total.get("difference"), total.get("status", ""), bool(differences or provisional))]
    for col, label, value, note, amber in cards:
        with col:
            st.markdown(f'<div class="kpi {"kpi-amber" if amber else ""}"><div class="kpi-label">{esc(label)}</div><div class="kpi-value">{money(value, signed=col is c3)} <small>₽</small></div><div class="kpi-note">{esc(note)}</div></div>', unsafe_allow_html=True)
    st.caption("Полнота разовых актов без реестра не проверена. Для выяснения причины расхождения потребуется регистр формирования отчёта.")
    render_failures = [e for e in result.get("evidence", {}).values() if e.get("render_error")]
    if render_failures:
        st.warning(f"Не удалось построить фрагменты источников: {len(render_failures)}. Суммы извлечены, но визуальная проверка этих доказательств требует открытия исходных файлов.")


def render_results(result) -> None:
    rows = result.get("rows", [])
    if not rows:
        st.warning("Не удалось сформировать строки результата. Проверьте журнал документов.")
        return
    st.markdown("## Результаты по статьям")
    st.caption("Выберите строку таблицы — ниже появятся формула и источники каждой суммы. Все суммы в рублях.")
    display_rows = []
    for row in rows:
        display_rows.append({"Статья": f"{row['code']}  {row.get('title', '')}", "В отчёте": money(row.get("reported")),
                             "По документам": money(row.get("confirmed")), "Разница": money(row.get("difference"), True),
                             "Статус": row.get("status", "Недостаточно данных")})
    frame = pd.DataFrame(display_rows)

    def status_style(value):
        return {"Совпадает": "color:#166348;background-color:#e7f3ed", "Расхождение": "color:#8a4d00;background-color:#fff0cf"}.get(value, "color:#344e7e;background-color:#e9eef8")

    styled = frame.style.map(status_style, subset=["Статус"])
    selection = st.dataframe(styled, hide_index=True, use_container_width=True, selection_mode="single-row", on_select="rerun",
                             key="result_table", row_height=52, height=255,
                             column_config={"Статья": st.column_config.TextColumn(width="large"), "Статус": st.column_config.TextColumn(width="medium")})
    index = selection.selection.rows[0] if selection.selection.rows else next((i for i, r in enumerate(rows) if r["code"] == "0800"), 0)
    row = rows[min(index, len(rows) - 1)]
    total = result.get("total", {})
    st.markdown(f"**Итого по верхнеуровневым статьям:** {money(total.get('confirmed'))} руб. · Разница **{money(total.get('difference'), True)} руб.** &nbsp; {badge(total.get('status', 'Недостаточно данных'))}", unsafe_allow_html=True)
    if total.get("reasons"):
        st.caption(" · ".join(map(str, total["reasons"])))
    with st.expander("Проверить общий итог"):
        total_formula = " + ".join(f"{r['code']}: {money(r.get('confirmed'))}" for r in rows)
        st.markdown(f"**Сумма по документам:** {total_formula} = **{money(total.get('confirmed'))} руб.**")
        st.caption("Чтобы раскрыть слагаемые статьи, выберите её строку в таблице выше.")
        if total.get("report_evidence_id"):
            render_source(total["report_evidence_id"], result, "total")
    st.divider()
    st.markdown(f"## {esc(row['code'])} · {esc(row.get('title', ''))}")
    st.markdown(badge(row.get("status", "Недостаточно данных")), unsafe_allow_html=True)
    if row.get("status") == "Недостаточно данных":
        st.info(f"Подтверждённая часть: {money(row.get('confirmed'))} руб. Окончательный вывод по статье невозможен.")
    for reason in row.get("reasons", []):
        st.caption(str(reason))
    sources = [result.get("evidence", {}).get(i, {}) for i in row.get("evidence_ids", [])]
    if sources:
        parts = [money(s.get("value")) for s in sources if s.get("value") is not None]
        formula = " + ".join(parts) + " = " + money(row.get("confirmed")) + " руб."
        st.markdown(f'<div class="formula"><strong>По документам</strong><br>{esc(formula)}</div>', unsafe_allow_html=True)
    else:
        st.caption("Нет принятых слагаемых для этой статьи.")
    st.caption(f"Разница = {money(row.get('reported'))} − {money(row.get('confirmed'))} = {money(row.get('difference'), True)} руб.")
    source_list, viewer = st.columns([1, 1.7], gap="large")
    source_ids = row.get("evidence_ids", [])
    code = row["code"]
    available = [*source_ids]
    report_evidence_id = row.get("report_evidence_id")
    if report_evidence_id:
        available.append(report_evidence_id)
    selected = st.session_state.get(f"source_{code}")
    if selected not in available:
        selected = source_ids[0] if source_ids else report_evidence_id
    with source_list:
        st.markdown("### Слагаемые")
        for number, source_id in enumerate(source_ids, 1):
            source = result["evidence"].get(source_id, {})
            label = f"{number:02d} · {money(source.get('value'))} руб."
            st.button(label, key=f"source_btn_{code}_{source_id}", type="primary" if selected == source_id else "secondary",
                      use_container_width=True, on_click=set_source, args=(source_id, code))
            st.caption(source.get("label") or source.get("file", "Документ"))
        st.divider()
        if report_evidence_id:
            st.button(f"КФЗ · {money(row.get('reported'))} руб.", key=f"source_report_{code}",
                      type="primary" if selected == report_evidence_id else "secondary", use_container_width=True,
                      on_click=set_source, args=(report_evidence_id, code))
            st.caption("Значение из выбранной строки отчёта")
    with viewer:
        with st.container(border=True):
            render_source(selected, result, key_prefix=f"row_{code}")


def render_journal(result) -> None:
    documents = result.get("documents", [])
    counts = Counter(d.get("status") for d in documents)
    st.markdown("## Журнал документов")
    st.caption("Каждый файл имеет решение и причину. Исключённые документы в суммы не входят.")
    options = ["Все", *[f"{name} ({counts.get(status, 0)})" for status, name in DOC_STATUSES.items()]]
    chosen = st.radio("Показать документы", options, horizontal=True)
    chosen_status = list(DOC_STATUSES)[options.index(chosen) - 1] if chosen != "Все" else None
    visible = [doc for doc in documents if not chosen_status or doc.get("status") == chosen_status]
    records = [{"Файл": doc.get("file", ""), "Решение": DOC_STATUSES.get(doc.get("status"), doc.get("status", "")),
                "Статья": doc.get("code", "") or "—", "Сумма, руб.": money(doc.get("amount")), "Причина": doc.get("reason", "")} for doc in visible]
    st.dataframe(pd.DataFrame(records), hide_index=True, use_container_width=True,
                 column_config={"Файл": st.column_config.TextColumn(width="large"), "Причина": st.column_config.TextColumn(width="large")})
    reviewable = [doc for doc in documents if doc.get("status") == "ambiguous" and doc.get("id")]
    if reviewable:
        st.markdown("### Решение проверяющего")
        st.caption("Изменение применяется к текущей сверке. Укажите основание — оно останется в журнале.")
        doc = st.selectbox("Спорный документ", reviewable, format_func=lambda item: item.get("file", item["id"]))
        if doc.get("evidence_ids"):
            render_source(doc["evidence_ids"][0], result, "manual")
        choice = st.selectbox("Решение", ["Отнести к 0800", "Отнести к 0900", "Исключить из сверки"])
        reason = st.text_input("Основание решения", placeholder="Например: оборудование относится к производственному подразделению")
        if st.button("Сохранить решение и пересчитать", disabled=not reason.strip(), type="primary"):
            decision = {"action": "exclude" if choice == "Исключить из сверки" else "include", "reason": reason.strip()}
            if decision["action"] == "include":
                decision["code"] = "0800" if "0800" in choice else "0900"
            st.session_state.setdefault("decisions", {})[doc["id"]] = decision
            st.session_state.setdefault("decision_history", []).append({"Файл": doc.get("file"), "Решение": choice, "Основание": reason.strip()})
            run_check(st.session_state.get("chosen_report_row"))
            st.rerun()
    if st.session_state.get("decision_history"):
        st.markdown("### Ручные решения текущей сверки")
        st.dataframe(pd.DataFrame(st.session_state["decision_history"]), hide_index=True, use_container_width=True)


def main() -> None:
    heading = st.container()
    with heading:
        st.markdown('<div class="eyebrow">Контроль фактических затрат</div>', unsafe_allow_html=True)
        st.title("Копейка")
        st.markdown('<p class="subtitle">От суммы в отчёте — к каждому подтверждающему документу.</p>', unsafe_allow_html=True)
    result = st.session_state.get("result")
    render_uploads(bool(result))
    if st.session_state.get("run_error"):
        st.error("Не удалось завершить сверку: " + st.session_state["run_error"])
    render_selection()
    result = st.session_state.get("result")
    if not result:
        if not st.session_state.get("candidates"):
            st.markdown("## Проверка с прозрачным расчётом")
            for col, number, title, copy in zip(st.columns(3), ["01", "02", "03"],
                ["Загрузите документы", "Сравните суммы", "Проверьте доказательства"],
                ["КФЗ и комплект актов, ведомостей и расчётов. ZIP можно загрузить целиком.",
                 "Четыре укрупнённые статьи, общий итог и причины исключения файлов.",
                 "Каждое слагаемое открывает нужную страницу, строку или ячейку источника."]):
                with col:
                    st.markdown(f'<div class="step-card"><div class="step-number">{number}</div><div class="step-title">{title}</div><div class="step-copy">{copy}</div></div>', unsafe_allow_html=True)
            st.caption("Первая версия: выданная форма КФЗ, 3 этап за 2026 год. КФЗ: PDF, PNG или JPG. Первичка: PDF, PNG, JPG, XLSX и DOCX.")
        return
    job = st.session_state["job"]
    report_year = result.get("context", {}).get("year", date.fromisoformat(job["start_date"]).year)
    st.markdown(f'<div class="context-strip"><div class="context-item"><span>Договор</span><strong>{esc(job["contract"])}</strong></div><div class="context-item"><span>Период</span><strong>{date.fromisoformat(job["start_date"]).strftime("%d.%m.%Y")} — {date.fromisoformat(job["end_date"]).strftime("%d.%m.%Y")}</strong></div><div class="context-item"><span>КФЗ</span><strong>3 этап, {esc(report_year)} г.</strong></div></div>', unsafe_allow_html=True)
    render_summary(result)
    results_tab, journal_tab, limits_tab = st.tabs(["Сверка и доказательства", "Журнал документов", "Границы проверки"])
    with results_tab:
        render_results(result)
    with journal_tab:
        render_journal(result)
    with limits_tab:
        st.markdown("## Что подтверждает эта сверка")
        st.write("Сравниваются укрупнённые статьи 0200, 0300, 0800 и 0900 в пределах принятых документов за выбранный период и договор.")
        limitations = result.get("limitations") or ["Полнота разовых актов без реестра не проверена.", "Трудоёмкость, материальные затраты и недоступные разбивки отдельно не подтверждены."]
        for limitation in limitations:
            st.markdown(f"- {limitation}")
        st.write("Разница всегда рассчитывается как сумма в КФЗ минус сумма по документам. Положительное значение означает, что в отчёте указано больше подтверждённой суммы.")
        st.caption("Для установления причины расхождения нужен регистр формирования отчёта. Загруженные данные используются только в текущей локальной сессии.")


if __name__ == "__main__":
    main()
