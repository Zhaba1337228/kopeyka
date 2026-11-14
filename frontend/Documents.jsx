import React, { useState } from "react";
import "./documents.css";

const money = (v) =>
  v == null
    ? "—"
    : Number(v).toLocaleString("ru-RU", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 6,
      });
const names = {
  accepted: "Принят",
  excluded: "Исключён",
  ambiguous: "Требует решения",
  error: "Не прочитан",
};
const place = (ev) =>
  [
    ev.page && `Страница ${ev.page}`,
    ev.sheet && `Лист ${ev.sheet}`,
    ev.cell && `Ячейка ${ev.cell}`,
    ev.table && `Таблица ${ev.table}`,
    ev.row && `Строка ${ev.row}`,
    ev.paragraph && `Абзац ${ev.paragraph}`,
  ]
    .filter(Boolean)
    .join(" · ");

export function EvidenceViewer({ evidence, session, id }) {
  const ev = evidence[id];
  if (!ev) return <p className="muted">Выберите фрагмент источника.</p>;
  const base = `/api/session/${session}/evidence/${encodeURIComponent(id)}`;
  return (
    <div className="review-source">
      <div className="subhead">
        <strong>{ev.file}</strong>
        <a href={base + "/original"}>Скачать оригинал</a>
      </div>
      <p className="muted">{place(ev)}</p>
      {ev.image_path ? (
        <img src={base + "/image"} alt="Фрагмент исходного документа" />
      ) : (
        <pre>{ev.fragment_text || ev.text}</pre>
      )}
      {ev.raw_value != null && (
        <p className="source-conversion">
          В источнике: {ev.raw_value} · Множитель:{" "}
          {ev.multiplier || "требует уточнения"} · {ev.unit === "чел.-мес." ? "В чел.-мес." : "В рублях"}: {money(ev.value)}
        </p>
      )}
      {ev.formula && (
        <details>
          <summary>Как рассчитано значение ячейки</summary>
          <pre>{ev.formula}</pre>
          <ul>{(ev.formula_sources || []).map(r => <li key={r.sheet + r.cell}>{r.sheet} · {r.cell}: {String(r.raw)}</li>)}</ul>
        </details>
      )}
      {ev.manual && (
        <p className="source-conversion">
          Подтверждено проверяющим: {ev.review_reason}
        </p>
      )}
    </div>
  );
}

export function ReportReview({result, session, busy, onRecalculate}) {
  const options=[...result.rows,result.total,...(result.indicators || [])].map(row=>({
    ...row,source:row.report_evidence_id || (row.report_evidence_ids?.length===1 ? row.report_evidence_ids[0] : null),
  })).filter(row=>row.source && result.evidence[row.source]);
  const [code,setCode]=useState(options[0]?.code || '');
  const selected=options.find(row=>row.code===code);
  const [raw,setRaw]=useState(selected?.reported || ''), [unit,setUnit]=useState('1'), [reason,setReason]=useState('');
  if (!selected) return null;
  const labor=selected.unit==='чел.-мес.';
  function choose(value) {
    setCode(value);setRaw(options.find(row=>row.code===value)?.reported || '');setUnit('1');setReason('');
  }
  function submit(event) {
    event.preventDefault();
    onRecalculate({report_decision:{code,source:selected.source,raw,multiplier:unit,reason}});
  }
  return <details className="document-review report-review">
    <summary>Проверить значение в КФЗ</summary>
    <p className="muted">Сверьте значение с выделенным фрагментом. Исправление и основание сохранятся в новой версии; оригинал останется прежним.</p>
    <label htmlFor="report-metric">Показатель КФЗ</label>
    <select id="report-metric" value={code} onChange={event=>choose(event.target.value)} disabled={busy}>
      {options.map(row=><option key={row.code} value={row.code}>{row.title}</option>)}
    </select>
    <div className="document-review-grid">
      <EvidenceViewer evidence={result.evidence} session={session} id={selected.source}/>
      <form onSubmit={submit}>
        <label htmlFor="report-value">Значение в КФЗ</label>
        <input id="report-value" value={raw} onChange={event=>setRaw(event.target.value)} required inputMode="decimal" maxLength={40} disabled={busy}/>
        <label htmlFor="report-unit">Единица КФЗ</label>
        <select id="report-unit" value={unit} onChange={event=>setUnit(event.target.value)} disabled={busy}>
          {labor ? <option value="1">Человеко-месяцы</option> : <><option value="1">Рубли</option><option value="1000">Тысячи рублей</option><option value="1000000">Миллионы рублей</option></>}
        </select>
        <label htmlFor="report-reason">Основание исправления КФЗ</label>
        <textarea id="report-reason" value={reason} onChange={event=>setReason(event.target.value)} required maxLength={1000} disabled={busy} rows={3}/>
        <button className="primary" disabled={busy || !raw.trim() || !reason.trim()}>Подтвердить значение КФЗ</button>
      </form>
    </div>
  </details>;
}

function DocumentReview({ doc, result, session, busy, onRecalculate }) {
  const candidates = doc.amount_candidates || [];
  const detailOnly = !!doc.indicator_only;
  const initial =
    candidates.find((c) => doc.evidence_ids?.includes(c.id)) || candidates[0];
  const [source, setSource] = useState(
    initial?.id || doc.preview_evidence_ids?.[0] || doc.evidence_ids?.[0] || "",
  );
  const [confirm, setConfirm] = useState(false),
    [raw, setRaw] = useState(initial?.raw_value || ""),
    [multiplier, setMultiplier] = useState(initial?.multiplier || "1");
  const [code, setCode] = useState(
    result.rows.some((r) => r.code === doc.code)
      ? doc.code
      : result.rows[0]?.code || "",
  );
  const [action, setAction] = useState("include"),
    [reason, setReason] = useState(""),
    [error, setError] = useState("");
  const [metadata, setMetadata] = useState(
    Object.fromEntries(
      ["contract", "period_start", "period_end", "number", "counterparty"].map(
        (k) => [k, doc[k] || ""],
      ),
    ),
  );
  function choose(id) {
    setSource(id);
    const item = candidates.find((c) => c.id === id);
    if (item) {
      setRaw(item.raw_value || "");
      setMultiplier(item.multiplier || "1");
    }
  }
  function submit(e) {
    e.preventDefault();
    setError("");
    if (action === "include" && doc.amount == null && !confirm && !detailOnly) {
      setError("Подтвердите сумму по источнику перед включением.");
      return;
    }
    if (confirm && (!source || !raw.trim())) {
      setError(
        "Выберите фрагмент и укажите сумму в единицах исходного документа.",
      );
      return;
    }
    const corrections = Object.fromEntries(
      Object.entries(metadata).filter(
        ([k, v]) => v.trim() !== String(doc[k] || ""),
      ),
    );
    const decision = { action, code: detailOnly ? null : code, reason, metadata: corrections };
    if (confirm && action === "include")
      Object.assign(decision, {
        amount_source: source,
        amount_raw: raw,
        amount_multiplier: multiplier,
      });
    onRecalculate({ document_id: doc.id, decision });
  }
  const refs = candidates.length
    ? candidates.map((c) => c.id)
    : doc.preview_evidence_ids || doc.evidence_ids || [];
  return (
    <section
      className="document-review"
      aria-labelledby="document-review-title"
    >
      <div className="document-review-head">
        <div>
          <div className="eyebrow">ПРОВЕРКА ПО ИСТОЧНИКУ</div>
          <h2 id="document-review-title">{doc.file}</h2>
          <p>{doc.reason}</p>
        </div>
        <a href={`/api/session/${session}/documents/${doc.id}/original`}>
          Скачать документ
        </a>
      </div>
      <div className="document-review-grid">
        <div>
          <label htmlFor="document-fragment">Фрагмент документа</label>
          <select
            id="document-fragment"
            value={source}
            onChange={(e) => choose(e.target.value)}
            disabled={busy || !refs.length}
          >
            {refs.map((id) => (
              <option key={id} value={id}>
                {place(result.evidence[id] || {})} ·{" "}
                {candidates.find((c) => c.id === id)?.label ||
                  result.evidence[id]?.label ||
                  "Источник"}
              </option>
            ))}
          </select>
          <EvidenceViewer
            evidence={result.evidence}
            session={session}
            id={source}
          />
          {!refs.length && (
            <p className="muted">
              Файл не удалось прочитать. Скачайте оригинал и добавьте
              исправленный документ новой версией.
            </p>
          )}
        </div>
        <form onSubmit={submit}>
          <h3>Решение проверяющего</h3>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <label htmlFor="review-action">Действие</label>
          <select
            id="review-action"
            value={action}
            onChange={(e) => setAction(e.target.value)}
            disabled={busy}
          >
            <option value="include">Включить в расчёт</option>
            <option value="exclude">Исключить из расчёта</option>
          </select>
          {action === "include" && (
            <>
              {detailOnly ? <p className="muted">Документ подтверждает детальные показатели. Его значения не добавляются к сумме статей.</p> : <><label htmlFor="review-code">Статья</label>
              <select
                id="review-code"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                disabled={busy}
              >
                {result.rows.map((r) => (
                  <option key={r.code} value={r.code}>
                    {r.code} · {r.title}
                  </option>
                ))}
              </select></>}
              <label className="confirm-amount">
                <input
                  type="checkbox"
                  checked={confirm}
                  onChange={(e) => setConfirm(e.target.checked)}
                  disabled={busy || !refs.length}
                />
                {detailOnly ? "Подтвердить значение по выбранному фрагменту" : "Подтвердить сумму по выбранному фрагменту"}
              </label>
              {confirm && (
                <>
                  <label htmlFor="review-amount">
                    Сумма в исходном документе
                  </label>
                  <input
                    id="review-amount"
                    value={raw}
                    onChange={(e) => setRaw(e.target.value)}
                    required
                    inputMode="decimal"
                    disabled={busy}
                  />
                  <label htmlFor="review-unit">Единица измерения</label>
                  <select
                    id="review-unit"
                    value={multiplier}
                    onChange={(e) => setMultiplier(e.target.value)}
                    disabled={busy}
                  >
                    {result.evidence[source]?.field === "labor.total" ? <option value="1">Человеко-месяцы</option> : <><option value="1">Рубли</option>
                    <option value="1000">Тысячи рублей</option>
                    <option value="1000000">Миллионы рублей</option></>}
                  </select>
                  <p className="muted">
                    В расчёт попадёт подтверждённая сумма этого фрагмента.
                    Основание и исходное распознанное значение сохранятся.
                  </p>
                </>
              )}
            </>
          )}
          <details className="metadata-editor">
            <summary>Уточнить реквизиты</summary>
            {[
              ["contract", "Договор"],
              ["period_start", "Период с"],
              ["period_end", "Период по"],
              ["number", "Номер документа"],
              ["counterparty", "Контрагент"],
            ].map(([key, label]) => (
              <label key={key}>
                {label}
                <input
                  aria-label={label}
                  type={key.startsWith("period_") ? "date" : "text"}
                  value={metadata[key]}
                  onChange={(e) =>
                    setMetadata((m) => ({ ...m, [key]: e.target.value }))
                  }
                  maxLength={300}
                  disabled={busy}
                />
              </label>
            ))}
          </details>
          <label htmlFor="review-reason">Основание решения</label>
          <textarea
            id="review-reason"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={3}
            maxLength={1000}
            required
            disabled={busy}
            placeholder="Что проверили в источнике и почему приняли это значение"
          />
          <button
            className="primary"
            disabled={
              busy ||
              !reason.trim() ||
              (action === "include" && !!doc.duplicate_of)
            }
          >
            {busy ? "Пересчитываем…" : "Подтвердить и пересчитать"}
          </button>
          {doc.duplicate_of && (
            <p className="muted">
              Дубликат нельзя включить повторно. Откройте исходный документ.
            </p>
          )}
          <p className="muted">
            Будет создана новая версия. Предыдущий результат сохранится.
          </p>
        </form>
      </div>
      {doc.decision && (
        <details>
          <summary>Сохранённое решение</summary>
          <p>{doc.decision.reason}</p>
        </details>
      )}
    </section>
  );
}

export default function Documents({ result, session, busy, onRecalculate }) {
  const [filter, setFilter] = useState("all"),
    [selected, setSelected] = useState("");
  const visible = result.documents.filter(
    (d) => filter === "all" || d.status === filter,
  );
  const doc = result.documents.find((d) => d.id === selected);
  return (
    <>
      <h1>Журнал документов</h1>
      <p className="muted">
        Откройте документ, проверьте источник суммы и при необходимости уточните
        решение.
      </p>
      <div className="toolbar">
        <label>
          Показать{" "}
          <select value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="all">Все документы</option>
            {Object.entries(names).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Документ</th>
              <th>Статья</th>
              <th>Сумма, ₽</th>
              <th>Решение</th>
              <th>Причина</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((d, i) => (
              <tr key={d.id || i}>
                <td>
                  {d.id ? (
                    <button className="link" onClick={() => setSelected(d.id)}>
                      {d.file}
                    </button>
                  ) : (
                    d.file
                  )}
                </td>
                <td>{d.indicator_only ? "Детальные показатели" : d.code || "—"}</td>
                <td>{money(d.amount)}</td>
                <td>{names[d.status] || d.status}</td>
                <td>{d.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {doc && (
        <DocumentReview
          key={session + doc.id}
          doc={doc}
          result={result}
          session={session}
          busy={busy}
          onRecalculate={onRecalculate}
        />
      )}
    </>
  );
}

export function Coverage({ result, session }) {
  const [source, setSource] = useState("");
  const indicators = result.indicators || [];
  if (!indicators.length) return null;
  return (
    <details className="coverage">
      <summary>Другие показатели КФЗ · {indicators.length} · не проверены полностью: {indicators.filter(r=>r.status==='Недостаточно данных').length}</summary>
      <p className="muted">
        Расшифровки проверяются отдельно и повторно не прибавляются к итоговым
        статьям.
      </p>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Показатель</th>
              <th>По КФЗ</th>
              <th>Подтверждено</th>
              <th>Разница</th>
              <th>Проверка и источник</th>
            </tr>
          </thead>
          <tbody>
            {indicators.map((r) => (
              <tr key={r.code}>
                <td>
                  {r.title}
                  <small>{r.unit}</small>
                </td>
                <td>{money(r.reported)}</td>
                <td>{money(r.confirmed)}</td>
                <td>{money(r.difference)}</td>
                <td>
                  <strong>{r.status}</strong>
                  {r.reasons.map((v, i) => (
                    <small key={i}>{v}</small>
                  ))}
                  <div className="coverage-sources">
                    {[...(r.report_evidence_ids || []), ...r.evidence_ids].map(
                      (id) => (
                        <button
                          className="link"
                          key={id}
                          onClick={() => setSource(id)}
                        >
                          {result.evidence[id]?.report
                            ? "КФЗ"
                            : result.evidence[id]?.file}{" "}
                          · {place(result.evidence[id] || {})}
                        </button>
                      ),
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {source && (
        <EvidenceViewer
          evidence={result.evidence}
          session={session}
          id={source}
        />
      )}
    </details>
  );
}
