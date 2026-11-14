import React, { useState } from "react";
import { Icon, UploadZone } from "./Controls";
import "./review.css";

const money = (value) =>
  value == null
    ? "—"
    : Number(value).toLocaleString("ru-RU", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 6,
      });
const stamp = (value) =>
  new Date(value).toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
async function api(url, options) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok)
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : "Не удалось выполнить действие. Повторите попытку.",
    );
  return data;
}
function downloadText(name, text) {
  const url = URL.createObjectURL(
    new Blob(["\ufeff" + text], { type: "text/plain;charset=utf-8" }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function Conclusion({ result, session }) {
  const review = result.review;
  if (!review) return null;
  return (
    <section className="review-conclusion" aria-label="Итоговое заключение">
      <div>
        <div className="eyebrow">ИТОГОВОЕ ЗАКЛЮЧЕНИЕ</div>
        <h2>{review.conclusion}</h2>
        <p>{review.scope}</p>
        <div className="conclusion-counts">
          <span>
            Совпадают <b>{review.counts.matched}</b>
          </span>
          <span>
            Расхождения <b>{review.counts.mismatch}</b>
          </span>
          <span>
            Не проверены полностью <b>{review.counts.unverified}</b>
          </span>
        </div>
        {result.indicators?.length > 0 && (
          <p>
            Другие показатели: совпадают {review.indicator_counts?.matched || 0}
            , расхождения {review.indicator_counts?.mismatch || 0}, не проверены
            полностью {review.indicator_counts?.unverified || 0}.
          </p>
        )}
      </div>
      <div className="conclusion-downloads">
        <a
          className="review-download"
          href={`/api/session/${session}/conclusion`}
        >
          <Icon name="file" />
          Скачать заключение
        </a>
        <a
          className="review-download"
          href={`/api/session/${session}/conclusion.xlsx`}
        >
          <Icon name="file" />
          Скачать Excel
        </a>
      </div>
    </section>
  );
}

export function Versions({ workflow, onOpen, busy }) {
  const [error, setError] = useState(""),
    [loading, setLoading] = useState(false),
    [expanded, setExpanded] = useState(true);
  if (!workflow?.version) return null;
  const { version, versions, comparison } = workflow;
  const parent = versions.find((v) => v.session === version.parent);
  async function select(id) {
    setError("");
    setLoading(true);
    try {
      const data = await api(`/api/session/${id}`);
      if (!data.result) throw new Error("Эта версия не завершена");
      onOpen(data);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }
  return (
    <section className="versions">
      <div className="version-bar">
        <label>
          Версия сверки
          <select
            aria-label="Версия сверки"
            disabled={busy || loading}
            value={version.session}
            onChange={(e) => select(e.target.value)}
          >
            {versions.map((v) => (
              <option
                key={v.session}
                value={v.session}
                disabled={v.status !== "Завершена"}
              >
                Версия {v.version} · {stamp(v.created)}
                {v.status !== "Завершена" ? ` · ${v.status}` : ""}
              </option>
            ))}
          </select>
        </label>
        <span>
          {version.operation}
          {parent ? ` · на основе версии ${parent.version}` : ""}
        </span>
        {comparison && (
          <button
            aria-expanded={expanded}
            onClick={() => setExpanded((v) => !v)}
          >
            {expanded ? "Скрыть сравнение" : "Сравнить с предыдущей"}
          </button>
        )}
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {comparison && expanded && (
        <div className="comparison">
          <h2>
            Что изменилось · версия {parent?.version} → {version.version}
          </h2>
          <p className="muted">
            Оригинал КФЗ сохранён. Подтверждения значений учитываются в новой версии. «Совпадает» означает совпадение
            проверяемых сумм, а не подтверждение полноты отчёта.
          </p>
          <div className="table-wrap">
            <table className="comparison-table">
              <thead>
                <tr>
                  <th>Статья</th>
                  <th>Было · версия {parent?.version}</th>
                  <th>Стало · версия {version.version}</th>
                  <th>Изменение подтверждённой суммы</th>
                </tr>
              </thead>
              <tbody>
                {comparison.rows.map((r) => (
                  <tr
                    key={r.code}
                    className={r.changed ? "comparison-changed" : ""}
                  >
                    <td>
                      {r.code}
                      <small>{r.title}</small>
                    </td>
                    {["before", "after"].map((k) => (
                      <td key={k}>
                        <strong>{money(r[k].confirmed)} ₽</strong>
                        <small>КФЗ: {money(r[k].reported)} ₽</small>
                        <small>Разница: {money(r[k].difference)} ₽</small>
                        <span
                          className={
                            r[k].status === "Совпадает"
                              ? "comparison-match"
                              : ""
                          }
                        >
                          {r[k].status}
                        </span>
                      </td>
                    ))}
                    <td>
                      {Number(r.confirmed_change) > 0 ? "+" : ""}
                      {money(r.confirmed_change)} ₽
                      <small>
                        {r.resolved
                          ? "Совпадение суммы подтверждено"
                          : r.changed
                            ? "Результат изменился"
                            : "Без изменений"}
                      </small>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="comparison-docs">
            {comparison.indicators?.length > 0 && <details>
              <summary>Изменения детальных показателей</summary>
              <div className="table-wrap"><table>
                <thead><tr><th>Показатель</th><th>Было</th><th>Стало</th></tr></thead>
                <tbody>{comparison.indicators.map(r => <tr key={r.code}>
                  <td>{r.title}<small>{r.unit}</small></td>
                  {["before","after"].map(k=><td key={k}>{money(r[k].confirmed)}<small>{r[k].status}</small></td>)}
                </tr>)}</tbody>
              </table></div>
            </details>}
            <h3>Новые документы: {comparison.added_documents.length}</h3>
            {comparison.added_documents.map((d, i) => (
              <p key={d.id || i}>
                <strong>{d.file}</strong>
                <span>
                  {d.category} ·{" "}
                  {d.indicator_only ? "Подтверждение детальных показателей" : d.amount == null
                    ? "Сумма не прочитана"
                    : money(d.amount) + " ₽"}
                </span>
                <small>{d.reason}</small>
              </p>
            ))}
            {comparison.changed_documents.length > 0 && (
              <>
                <h3>Изменённые решения</h3>
                {comparison.changed_documents.map((d) => (
                  <p key={d.id}>
                    <strong>{d.file}</strong>
                    <span>{d.reason}</span>
                  </p>
                ))}
              </>
            )}
            {!comparison.added_documents.length &&
              !comparison.changed_documents.length && (
                <p>Состав принятых документов и решения не изменились.</p>
              )}
          </div>
        </div>
      )}
    </section>
  );
}

export default function Review({
  row,
  result,
  session,
  draft,
  onDraft,
  onOpen,
  onSource,
  busy,
  setBusy,
}) {
  const detail = result.review?.rows[row.code];
  const [tab, setTab] = useState("analysis"),
    [subject, setSubject] = useState(draft?.subject || detail?.subject || ""),
    [text, setText] = useState(draft?.text || detail?.request_text || "");
  const [saved, setSaved] = useState(draft || null),
    [message, setMessage] = useState(""),
    [error, setError] = useState(""),
    [files, setFiles] = useState([]),
    [note, setNote] = useState("");
  if (!detail) return null;
  const dirty =
    subject !== (saved?.subject || detail.subject) ||
    text !== (saved?.text || detail.request_text);
  async function saveDraft() {
    setError("");
    setMessage("");
    setBusy(true);
    try {
      const data = await api(`/api/session/${session}/request/${row.code}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ subject, text }),
      });
      setSaved(data);
      onDraft(row.code, data);
      setMessage("Черновик сохранён в этой версии. Сообщение не отправлялось.");
      return true;
    } catch (e) {
      setError(e.message);
      return false;
    } finally {
      setBusy(false);
    }
  }
  async function copy() {
    if (!(await saveDraft())) return;
    try {
      await navigator.clipboard.writeText(subject + "\n\n" + text);
      setMessage("Запрос скопирован. Отправьте его коллеге удобным способом.");
    } catch {
      setMessage(
        "Автокопирование недоступно. Выделите текст запроса или скачайте TXT.",
      );
    }
  }
  async function append(e) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const data = new FormData();
      files.forEach((f) => data.append("documents", f));
      data.append("note", note);
      onOpen(
        await api(`/api/session/${session}/documents`, {
          method: "POST",
          body: data,
        }),
      );
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  const issues = result.review.document_issues.filter(
    (d) => !d.code || d.code === row.code,
  );
  return (
    <section className="review" aria-labelledby="review-title">
      <div className="review-heading">
        <span className="review-coin">к.</span>
        <div>
          <div className="eyebrow">ОТ РАСХОЖДЕНИЯ К РЕШЕНИЮ</div>
          <h2 id="review-title">Разобраться до копейки</h2>
          <p>
            Статья {row.code} · {row.title}
          </p>
        </div>
      </div>
      <div
        className="review-tabs"
        role="tablist"
        aria-label="Работа с расхождением"
      >
        {[
          ["analysis", "1. Разобраться"],
          ["request", "2. Запросить уточнение"],
          ["append", "3. Добавить ответ"],
        ].map(([id, label]) => (
          <button
            key={id}
            id={`tab-${id}`}
            role="tab"
            aria-selected={tab === id}
            aria-controls="review-panel"
            disabled={busy}
            onClick={() => {
              setTab(id);
              setError("");
              setMessage("");
            }}
          >
            {label}
          </button>
        ))}
      </div>
      <div id="review-panel" role="tabpanel" aria-labelledby={`tab-${tab}`}>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        {message && (
          <p className="review-message" role="status">
            {message}
          </p>
        )}
        {tab === "analysis" && (
          <>
            <div className="review-fact">
              <span>{detail.categories.join(" · ")}</span>
              <h3>{detail.statement}</h3>
              <p>{detail.cause}</p>
            </div>
            <div className="review-columns">
              <div>
                <h3>На чём основан вывод</h3>
                <p className="review-formula">
                  {detail.sources.map((s) => money(s.value)).join(" + ") ||
                    "Нет принятых слагаемых"}{" "}
                  = <strong>{money(row.confirmed)} ₽</strong>
                </p>
                <ul className="proof-list">
                  {detail.sources.map((s) => (
                    <li key={s.id}>
                      <button onClick={() => onSource(s.id)}>
                        {s.location}
                      </button>
                      <span>{money(s.value)} ₽</span>
                    </li>
                  ))}
                </ul>
                {row.report_evidence_id && (
                  <button
                    className="link review-report-link"
                    onClick={() => onSource(row.report_evidence_id)}
                  >
                    Проверить {money(row.reported)} ₽ в карточке КФЗ{" "}
                    <Icon name="arrow" />
                  </button>
                )}
              </div>
              <div>
                <h3>Что проверить дальше</h3>
                {detail.actions.length ? (
                  <ol>
                    {detail.actions.map((action, i) => (
                      <li key={i}>{action}</li>
                    ))}
                  </ol>
                ) : (
                  <p>
                    Суммы совпадают. При необходимости запросите подтверждение
                    полноты комплекта.
                  </p>
                )}
                <button onClick={() => setTab("request")}>
                  Подготовить запрос коллеге <Icon name="arrow" />
                </button>
              </div>
            </div>
            {issues.length > 0 && (
              <details className="review-issues">
                <summary>
                  Документы вне расчёта и вопросы к данным · {issues.length}
                </summary>
                {issues.map((d, i) => (
                  <div key={d.id || i}>
                    <strong>{d.category}</strong>
                    <span>{d.file}</span>
                    <p>{d.reason}</p>
                  </div>
                ))}
              </details>
            )}
          </>
        )}
        {tab === "request" && (
          <div className="request-editor">
            <p className="muted">
              Текст составлен из расчёта и источников. Проверьте формулировки
              перед отправкой. Копейка не отправляет сообщения автоматически.
            </p>
            <label htmlFor="request-subject">Тема запроса</label>
            <input
              id="request-subject"
              value={subject}
              maxLength={500}
              onChange={(e) => setSubject(e.target.value)}
            />
            <label htmlFor="request-body">Текст запроса</label>
            <textarea
              id="request-body"
              rows={14}
              maxLength={30000}
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
            <div className="request-actions">
              <button
                className="primary"
                onClick={copy}
                disabled={busy || !text.trim()}
              >
                Сохранить и скопировать
              </button>
              <button onClick={saveDraft} disabled={busy || !text.trim()}>
                Сохранить черновик
              </button>
              <button
                onClick={() =>
                  downloadText(
                    `kopeyka-request-${row.code}.txt`,
                    subject + "\n\n" + text,
                  )
                }
                disabled={!text.trim()}
              >
                Скачать TXT
              </button>
            </div>
            <p className="draft-state">
              {dirty
                ? "Есть несохранённые изменения"
                : saved
                  ? `Черновик сохранён ${stamp(saved.saved_at)}`
                  : "Черновик сформирован из текущей версии"}
            </p>
          </div>
        )}
        {tab === "append" && (
          <form className="append-form" onSubmit={append}>
            <h3>Получили ответ? Перепроверим комплект.</h3>
            <p>
              Документы добавятся к текущему комплекту. Будет создана новая
              версия: исходная КФЗ, старые документы и результат сохранятся.
            </p>
            <UploadZone
              title="Дополнительные документы"
              caption="ZIP, PDF, XLSX, DOCX, PNG, JPG"
              accept=".zip,.pdf,.png,.jpg,.jpeg,.xlsx,.docx"
              files={files}
              multiple
              onChange={setFiles}
              disabled={busy}
            />
            <label>
              Комментарий к версии
              <input
                value={note}
                maxLength={200}
                onChange={(e) => setNote(e.target.value)}
                placeholder="Например: получена ведомость за февраль"
                disabled={busy}
              />
            </label>
            <button className="primary" disabled={busy || !files.length}>
              {busy ? "Перепроверяем комплект…" : "Добавить и сравнить версии"}
              <Icon name="arrow" />
            </button>
            {busy && (
              <p role="status">
                Распознаём новые документы и пересчитываем комплект. Старый
                результат сохранён.
              </p>
            )}
          </form>
        )}
      </div>
    </section>
  );
}
