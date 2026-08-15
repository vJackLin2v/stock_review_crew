import React, { useState } from "react";
import { fmtDate, fmtTimeHM } from "../format";
import { stageLabel } from "../stages";
import { DEFAULT_MODE, MODES, modeLabel, todayStr } from "../modes";

export default function Sidebar({
  collapsed,
  open,
  onCloseMobile,
  onToggle,
  history,
  activeReviewKey,
  onSelectReview,
  onNew,
  job,
  onBackToJob,
  chatSessions,
  activeChatId,
  onOpenChatSession,
  onDeleteReview,
  onDeleteChatSession,
  onOpenSettings,
  onStartReview,
  // R12-A3：侧栏「模拟账户」入口
  onOpenAccounts,
  // requirements-backtest-ui：侧栏「回测」入口
  onOpenBacktest,
}) {
  const [confirmReviewKey, setConfirmReviewKey] = useState(null);
  const [confirmChatId, setConfirmChatId] = useState(null);
  const [busyKey, setBusyKey] = useState(null);
  const [errorMsg, setErrorMsg] = useState("");
  // R11-R1：手动补做复盘入口（原首页「新建复盘」表单迁移到侧栏）
  const [bfOpen, setBfOpen] = useState(false);
  const [bfDate, setBfDate] = useState(todayStr());
  const [bfMode, setBfMode] = useState(DEFAULT_MODE);
  const [bfBusy, setBfBusy] = useState(false);
  const [bfError, setBfError] = useState("");

  const groups = history.groups || [];
  const sessions = Array.isArray(chatSessions) ? chatSessions : [];

  const pick = (fn) => (...args) => {
    if (onCloseMobile) onCloseMobile();
    if (fn) fn(...args);
  };

  const askDeleteReview = (e, key) => {
    e.stopPropagation();
    setErrorMsg("");
    setConfirmReviewKey(key);
  };
  const confirmDeleteReview = async (e, item) => {
    e.stopPropagation();
    const key = `${item.date}/${item.time}`;
    setBusyKey(`r-${key}`);
    setErrorMsg("");
    try {
      await onDeleteReview(item.date, item.time);
      setConfirmReviewKey(null);
    } catch (err) {
      setErrorMsg((err && err.message) || "删除失败，请稍后重试");
    } finally {
      setBusyKey(null);
    }
  };

  const askDeleteChat = (e, id) => {
    e.stopPropagation();
    setErrorMsg("");
    setConfirmChatId(String(id));
  };
  const confirmDeleteChat = async (e, id) => {
    e.stopPropagation();
    setBusyKey(`c-${id}`);
    setErrorMsg("");
    try {
      await onDeleteChatSession(id);
      setConfirmChatId(null);
    } catch (err) {
      setErrorMsg((err && err.message) || "删除失败，请稍后重试");
    } finally {
      setBusyKey(null);
    }
  };

  const jobState = job ? job.status : null;
  const jobPct = job && Number.isFinite(Number(job.pct)) ? Math.round(Number(job.pct)) : 0;

  const handleBackfill = async () => {
    if (!onStartReview) return;
    if (!bfDate) {
      setBfError("请选择复盘日期");
      return;
    }
    setBfBusy(true);
    setBfError("");
    try {
      await onStartReview({ date: bfDate, mode: bfMode });
      setBfOpen(false);
      if (onCloseMobile) onCloseMobile();
    } catch (err) {
      setBfError((err && err.message) || "提交失败，请稍后重试");
    } finally {
      setBfBusy(false);
    }
  };

  return (
    <aside className={`sidebar${collapsed ? " collapsed" : ""}${open ? " mobile-open" : ""}`}>
      <div className="brand">
        <div
          className="brand-mark"
          title={collapsed ? "展开侧栏" : "A股多智能体复盘系统"}
          onClick={collapsed ? onToggle : undefined}
        >
          复
        </div>
        <div className="brand-text">
          <div className="brand-title">A股复盘</div>
          <div className="brand-sub">多智能体复盘系统</div>
        </div>
      </div>

      {/* 2026-08-07：侧栏主按钮文案「新建复盘」→「首页」，去掉 + 号图标 */}
      <button className="btn btn-accent new-btn" onClick={onNew} title="首页">
        <span className="new-text">首页</span>
      </button>

      {/* R11-R1：手动补做复盘（历史日期 / 任意模式） */}
      <button
        className="btn btn-outline new-btn backfill-btn"
        onClick={() => {
          setBfOpen((o) => !o);
          setBfError("");
        }}
        title="补做复盘"
      >
        <span className="new-text">{bfOpen ? "收起补做" : "补做复盘"}</span>
      </button>
      {bfOpen ? (
        <div className="backfill-panel">
          <div className="backfill-title">补做复盘</div>
          <p className="backfill-sub">历史日期可补做任意模式；数据源无历史时按降级链标注缺失并继续</p>
          <label className="form-label" htmlFor="backfill-date">复盘日期</label>
          <input
            id="backfill-date"
            type="date"
            className="form-input"
            value={bfDate}
            onChange={(e) => setBfDate(e.target.value)}
          />
          <label className="form-label" style={{ marginTop: 10 }} htmlFor="backfill-mode">时间模式</label>
          <select
            id="backfill-mode"
            className="form-input form-select backfill-select"
            value={bfMode}
            onChange={(e) => setBfMode(e.target.value)}
          >
            {MODES.map((m) => (
              <option key={m.code} value={m.code}>
                {m.label}（{m.window}）
              </option>
            ))}
          </select>
          {bfError ? <div className="sidebar-del-error">{bfError}</div> : null}
          <button
            className="btn btn-accent btn-sm backfill-go"
            disabled={bfBusy}
            onClick={handleBackfill}
          >
            {bfBusy ? "提交中…" : `开始${modeLabel(bfMode)}`}
          </button>
        </div>
      ) : null}

      {/* R12-A3：分析师模拟账户独立视图入口 */}
      <button
        className="btn btn-outline new-btn"
        onClick={pick(onOpenAccounts)}
        title="模拟账户"
      >
        <span className="new-text">模拟账户</span>
      </button>

      {/* requirements-backtest-ui：历史回测独立视图入口 */}
      <button
        className="btn btn-outline new-btn"
        onClick={pick(onOpenBacktest)}
        title="历史回测"
      >
        <span className="new-text">回测</span>
      </button>

      {/* 运行中任务指示器：切换视图不丢任务，一键回到进度/结果 */}
      {jobState === "queued" || jobState === "running" ? (
        <div className="job-indicator running">
          <div className="ji-title">
            <span className="ji-spinner" />
            复盘进行中 {jobPct}%
          </div>
          <div className="ji-stage">· {stageLabel(job.stage)}</div>
          <button className="btn btn-accent btn-sm ji-btn" onClick={onBackToJob}>回到进度</button>
        </div>
      ) : jobState === "done" ? (
        <div className="job-indicator done">
          <div className="ji-title">✓ 复盘已完成</div>
          <button className="btn btn-accent btn-sm ji-btn" onClick={onBackToJob}>查看复盘结果</button>
        </div>
      ) : jobState === "error" ? (
        <div className="job-indicator error">
          <div className="ji-title">⚠ 复盘失败</div>
          <button className="btn btn-outline btn-sm ji-btn" onClick={onBackToJob}>查看失败原因</button>
        </div>
      ) : null}

      {errorMsg ? <div className="sidebar-del-error">{errorMsg}</div> : null}

      <div className="history">
        <div className="history-title">复盘记录</div>
        {history.loading ? (
          <div className="sidebar-empty">正在加载历史复盘…</div>
        ) : history.error ? (
          <div className="sidebar-empty">
            {history.error}
            <button className="btn btn-ghost btn-xs" style={{ marginTop: 8 }} onClick={history.onRetry}>
              重试
            </button>
          </div>
        ) : groups.length === 0 ? (
          <div className="sidebar-empty">
            暂无复盘记录
            <br />
            完成一次复盘后会自动保存在这里
          </div>
        ) : (
          groups.map((g) => (
            <div key={g.date} className="hist-group">
              <div className="hist-group-date">{fmtDate(g.date)}</div>
              {g.items.map((it) => {
                const key = `${g.date}/${it.time}`;
                const isActive = activeReviewKey === key;
                const confirming = confirmReviewKey === key;
                const busy = busyKey === `r-${key}`;
                return (
                  <div
                    key={key}
                    role="button"
                    tabIndex={0}
                    className={`history-item${isActive ? " active" : ""}${confirming ? " confirming" : ""}`}
                    onClick={() => onSelectReview(g.date, it.time)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        onSelectReview(g.date, it.time);
                      }
                    }}
                  >
                    <div className="hist-top">
                      <span className="hist-time">
                        {fmtTimeHM(it.time)} {it.mode_label}
                      </span>
                      {confirming ? null : (
                        <button
                          className="hist-del"
                          title="删除该复盘"
                          aria-label={`删除复盘 ${fmtTimeHM(it.time)} ${it.mode_label}`}
                          disabled={busyKey !== null}
                          onClick={(e) => askDeleteReview(e, key)}
                        >
                          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                            <path d="M3 6h18" />
                            <path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2" />
                            <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
                          </svg>
                        </button>
                      )}
                    </div>
                    <div className="hist-name" title={it.summary}>{it.summary || "（无摘要）"}</div>
                    {confirming ? (
                      <div className="hist-confirm">
                        <span className="hist-confirm-text">确认删除「{fmtTimeHM(it.time)} {it.mode_label}」？</span>
                        <button className="btn btn-danger btn-xs" disabled={busy} onClick={(e) => confirmDeleteReview(e, { date: g.date, time: it.time })}>
                          {busy ? "删除中…" : "确认删除"}
                        </button>
                        <button className="btn btn-ghost btn-xs" disabled={busyKey !== null} onClick={(e) => { e.stopPropagation(); setConfirmReviewKey(null); }}>
                          取消
                        </button>
                      </div>
                    ) : null}
                  </div>
                );
              })}
            </div>
          ))
        )}

        <div className="history-title" style={{ marginTop: 14 }}>对话记录</div>
        {sessions.length === 0 ? (
          <div className="sidebar-empty">暂无对话记录，可从首页聊天卡片发起</div>
        ) : (
          sessions.map((s) => {
            const id = String(s.session_id);
            const isActive = activeChatId === id;
            const confirming = confirmChatId === id;
            const busy = busyKey === `c-${id}`;
            return (
              <div
                key={id}
                role="button"
                tabIndex={0}
                className={`history-item${isActive ? " active" : ""}${confirming ? " confirming" : ""}`}
                onClick={() => onOpenChatSession(id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onOpenChatSession(id);
                  }
                }}
              >
                <div className="hist-top">
                  <span className="hist-time">{s.target_type === "sector" ? "板块" : "个股"} · {s.target_name || s.target}</span>
                  {confirming ? null : (
                    <button
                      className="hist-del"
                      title="删除该会话"
                      aria-label={`删除会话 ${s.title || s.target_name || s.target}`}
                      disabled={busyKey !== null}
                      onClick={(e) => askDeleteChat(e, id)}
                    >
                      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                        <path d="M3 6h18" />
                        <path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2" />
                        <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
                      </svg>
                    </button>
                  )}
                </div>
                <div className="hist-name" title={s.title}>{s.title}</div>
                <div className="hist-return">
                  <span className="zero">{s.analysts.length} 位分析师 · {s.message_count} 条消息</span>
                </div>
                {confirming ? (
                  <div className="hist-confirm">
                    <span className="hist-confirm-text">确认删除会话「{s.title}」？</span>
                    <button className="btn btn-danger btn-xs" disabled={busy} onClick={(e) => confirmDeleteChat(e, id)}>
                      {busy ? "删除中…" : "确认删除"}
                    </button>
                    <button className="btn btn-ghost btn-xs" disabled={busyKey !== null} onClick={(e) => { e.stopPropagation(); setConfirmChatId(null); }}>
                      取消
                    </button>
                  </div>
                ) : null}
              </div>
            );
          })
        )}
      </div>

      <div className="sidebar-footer">
        <div className="sidebar-footer-actions">
          <button className="collapse-btn" onClick={onOpenSettings} title="设置" aria-label="打开设置">
            <span className="collapse-icon">⚙</span>
            <span className="collapse-text">设置</span>
          </button>
          <button
            className="collapse-btn"
            onClick={onToggle}
            title={collapsed ? "展开侧栏" : "收起侧栏"}
            aria-label={collapsed ? "展开侧栏" : "收起侧栏"}
          >
            <span className="collapse-icon">{collapsed ? "☰" : "◀"}</span>
            <span className="collapse-text">{collapsed ? "展开" : "收起侧栏"}</span>
          </button>
        </div>
      </div>
    </aside>
  );
}
