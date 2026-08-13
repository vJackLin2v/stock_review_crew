import React, { useCallback, useEffect, useRef, useState } from "react";
import * as api from "../api";
import { normalizeJob, normalizeReviewDetail } from "../normalize";
import { fmtDate, fmtTimeHM } from "../format";
import { modeLabel, nowMinutes, todayStr } from "../modes";
import { stageLabel, visibleStages } from "../stages";
import ReportView from "./ReportView";

// R11-R2：当日复盘 Tab
// - 打开时调 GET /api/reviews/status?date=today（T2 后端提供）
// - has_today_review=true → 直接加载展示；false 且 15:00 后（交易日）→ 自动 POST 收盘复盘并轮询进度；
//   15:00 前/非交易日 → 展示 latest_date 复盘
// - 请求失败/后端未实现 → 静默降级为展示历史列表（无报错弹窗）
const AUTO_SS_KEY = "review_auto_";

export default function ReviewTab({
  active,
  history,
  onRetryHistory,
  onDeleteReview,
}) {
  const today = todayStr();
  const [status, setStatus] = useState(null);
  const [statusLoading, setStatusLoading] = useState(false);
  const [current, setCurrent] = useState(null); // {date, detail}
  const [currentLoading, setCurrentLoading] = useState(false);
  const [autoJob, setAutoJob] = useState(null); // {id, snap}
  const [confirmDel, setConfirmDel] = useState(null); // {date, time}
  const [delBusy, setDelBusy] = useState(false);
  const [delError, setDelError] = useState("");

  const pollTimerRef = useRef(null);
  const checkingRef = useRef(false);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      if (pollTimerRef.current) clearInterval(pollTimerRef.current);
    };
  }, []);

  const stopJobPoll = useCallback(() => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  // 单日复盘加载：优先 R4 新接口 GET /api/reviews/{date}，旧后端 404 时降级到历史详情
  const loadReviewByDate = useCallback(
    async (date, time) => {
      // R12 修复：R4 起每日一份，忽略 time（旧多份已由后端兼容为每日一条）
      try {
        const raw = await api.getReviewByDate(date);
        const detail = normalizeReviewDetail(raw);
        const t = (detail.meta && detail.meta.time) || time || "";
        return { ...detail, record_id: `${date}_${t}` };
      } catch {
        const groups = Array.isArray(history && history.groups) ? history.groups : [];
        const g = groups.find((x) => x.date === date);
        const item = g && g.items && g.items[0];
        if (!item) throw new Error("该日期暂无复盘记录");
        const raw2 = await api.getReviewByDate(date);
        const detail2 = normalizeReviewDetail(raw2);
        return { ...detail2, record_id: `${date}_${(item.time || "")}` };
      }
    },
    [history]
  );

  const showReview = useCallback(async (date, time) => {
    setCurrentLoading(true);
    try {
      const detail = await loadReviewByDate(date, time);
      if (!mountedRef.current) return;
      setCurrent({ date, detail });
    } catch {
      if (mountedRef.current) setCurrent(null);
    } finally {
      if (mountedRef.current) setCurrentLoading(false);
    }
  }, [loadReviewByDate]);

  // 自动任务进度轮询（复用 api.getJob + normalizeJob）
  const startJobPoll = useCallback(
    (jobId) => {
      stopJobPoll();
      setAutoJob({
        id: jobId,
        snap: { status: "queued", stage: "queued", pct: 0, message: "正在提交复盘任务…" },
      });
      pollTimerRef.current = setInterval(async () => {
        try {
          const raw = await api.getJob(jobId);
          const norm = normalizeJob(raw);
          if (!mountedRef.current) return;
          setAutoJob((prev) => ({ id: jobId, snap: { ...norm, metaLabel: modeLabel(norm.meta && norm.meta.mode) || "收盘复盘" } }));
          if (norm.isDone) {
            stopJobPoll();
            if (mountedRef.current) setAutoJob(null);
            try {
              const detail = await loadReviewByDate(today);
              if (mountedRef.current) {
                setCurrent({ date: today, detail });
                setStatus((s) => ({ ...(s || {}), has_today_review: true, pending_job_id: null }));
              }
            } catch {
              /* 静默 */
            }
            if (onRetryHistory) onRetryHistory();
          } else if (norm.isError) {
            stopJobPoll();
            if (mountedRef.current) {
              setAutoJob(null);
              setStatus((s) => ({ ...(s || {}), auto_failed: true }));
            }
          }
        } catch {
          stopJobPoll();
          if (mountedRef.current) {
            setAutoJob(null);
            setStatus((s) => ({ ...(s || {}), auto_failed: true }));
          }
        }
      }, 1000);
    },
    [stopJobPoll, loadReviewByDate, today, onRetryHistory]
  );

  const runAutoCheck = useCallback(async () => {
    if (checkingRef.current) return;
    checkingRef.current = true;
    setStatusLoading(true);
    try {
      const st = await api.getReviewStatus(today);
      if (!mountedRef.current) return;
      setStatus(st);
      const afterClose = nowMinutes() >= 15 * 60;
      if (st.has_today_review) {
        await showReview(today);
      } else if (st.pending_job_id) {
        // 进行中任务：直接轮询，不重复触发
        startJobPoll(st.pending_job_id);
      } else if (st.is_trading_day !== false && afterClose) {
        // 15:00 后且当天无复盘 → 自动发起收盘复盘（当天仅一次，sessionStorage 防重复）
        let already = false;
        try {
          already = window.sessionStorage.getItem(`${AUTO_SS_KEY}${today}`) === "1";
        } catch {
          already = false;
        }
        if (!already) {
          try {
            const res = await api.startReview({ date: today, mode: "close" });
            if (res && res.job_id) {
              try {
                window.sessionStorage.setItem(`${AUTO_SS_KEY}${today}`, "1");
              } catch {
                /* 忽略 */
              }
              startJobPoll(res.job_id);
            }
          } catch {
            // 静默降级：展示历史列表，无报错弹窗
            if (mountedRef.current) setStatus((s) => ({ ...(s || {}), auto_failed: true }));
          }
        }
      } else {
        // 15:00 前 / 非交易日：展示最近交易日复盘
        const d = st.latest_date || null;
        if (d) await showReview(d);
      }
    } catch {
      // 请求失败/后端未实现 → 静默降级为展示历史列表（无报错弹窗）
    } finally {
      checkingRef.current = false;
      if (mountedRef.current) setStatusLoading(false);
    }
  }, [today, showReview, startJobPoll]);

  // Tab 打开时检测（display:none 保持挂载，仅 active 时触发）
  useEffect(() => {
    if (!active) return;
    runAutoCheck();
  }, [active, runAutoCheck]);

  const confirmDeleteReview = useCallback(
    async (date, time) => {
      setDelBusy(true);
      setDelError("");
      try {
        await onDeleteReview(date, time);
        setConfirmDel(null);
        if (current && current.date === date) {
          setCurrent(null);
          if (date === today) runAutoCheck();
        }
      } catch (err) {
        setDelError((err && err.message) || "删除失败，请稍后重试");
      } finally {
        setDelBusy(false);
      }
    },
    [onDeleteReview, current, today, runAutoCheck]
  );

  const groups = Array.isArray(history && history.groups) ? history.groups : [];
  const reportObj = current
    ? {
        ...current.detail,
        record_id:
          current.detail.record_id || `${current.date}_${(current.detail.meta && current.detail.meta.time) || ""}`,
      }
    : null;

  return (
    <div className="review-tab">
      {statusLoading && !autoJob && !reportObj ? (
        <div className="card loading-card">
          <div className="spinner" />
          <div style={{ marginTop: 10 }}>正在检测当日复盘状态…</div>
        </div>
      ) : null}

      {autoJob ? (
        <div className="card progress-card review-auto-progress">
          <div className="progress-head">
            <div className="spinner" />
            <div className="progress-title">正在自动生成收盘复盘</div>
          </div>
          <p className="card-sub">15:00 后首次打开自动触发，任务每 1 秒自动刷新进度</p>
          <div className="steps">
            {visibleStages().map((s, i, stages) => {
              const currentIdx = stages.findIndex((x) => x.key === autoJob.snap.stage);
              const done = currentIdx >= 0 && i < currentIdx;
              const cur = currentIdx === i;
              return (
                <div key={s.key} className={`step ${done ? "done" : cur ? "current" : "pending"}`}>
                  <div className="step-dot">{done ? "✓" : cur ? String(i + 1) : ""}</div>
                  <div className="step-label">{stageLabel(s.key)}</div>
                </div>
              );
            })}
          </div>
          <div className="progress-track">
            <div className="progress-fill" style={{ width: `${Math.max(0, Math.min(100, Number(autoJob.snap.pct) || 0))}%` }} />
          </div>
          <div className="progress-pct">{Math.round(Number(autoJob.snap.pct) || 0)}%</div>
          <div className="progress-msg">{autoJob.snap.message || `阶段：${stageLabel(autoJob.snap.stage)}`}</div>
        </div>
      ) : null}

      {currentLoading ? (
        <div className="card loading-card">
          <div className="spinner" />
          <div style={{ marginTop: 10 }}>正在加载复盘报告…</div>
        </div>
      ) : reportObj ? (
        <ReportView
          report={reportObj}
          offline={!!reportObj.offline}
          history={history}
          onSwitchReview={(date, time) => showReview(date, time)}
        />
      ) : null}

      {/* 历史复盘列表（复用现有历史分组逻辑） */}
      <div className="card" style={{ marginTop: 18 }}>
        <div className="card-head-row">
          <h3 className="card-title">历史复盘</h3>
          {history.offline ? <span className="pill offline">离线 mock</span> : null}
        </div>
        {delError ? <div className="upload-error" style={{ marginBottom: 8 }}>{delError}</div> : null}
        {history.loading ? (
          <div className="loading-card" style={{ padding: 28 }}>
            <div className="spinner" />
            <div style={{ marginTop: 10 }}>正在加载历史复盘…</div>
          </div>
        ) : history.error ? (
          <div className="error-panel" style={{ padding: 24 }}>
            <div className="error-title">加载失败</div>
            <div className="error-detail">{history.error}</div>
            <button className="btn btn-outline btn-sm" onClick={onRetryHistory}>重试</button>
          </div>
        ) : groups.length === 0 ? (
          <div className="empty">
            暂无可展示的复盘
            <br />
            15:00 后将自动生成收盘复盘，或从侧栏「补做复盘」手动发起
          </div>
        ) : (
          <div className="home-history">
            {groups.map((g) => (
              <div key={g.date} className="hh-group">
                <div className="hh-date">
                  {fmtDate(g.date)}
                  <span className="hh-count">{g.items.length} 份</span>
                </div>
                {g.items.map((it) => (
                  <div
                    key={`${g.date}/${it.time}`}
                    role="button"
                    tabIndex={0}
                    className={`hh-item${current && current.date === g.date && current.detail && current.detail.meta && current.detail.meta.time === it.time ? " active" : ""}`}
                    onClick={() => showReview(g.date, it.time)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        showReview(g.date, it.time);
                      }
                    }}
                  >
                    <span className="hh-time">{fmtTimeHM(it.time)}</span>
                    <span className="tag-chip light">{it.mode_label}</span>
                    <span className="hh-summary" title={it.summary}>{it.summary || "（无摘要）"}</span>
                    {confirmDel && confirmDel.date === g.date && confirmDel.time === it.time ? (
                      <div
                        className="hist-confirm"
                        onClick={(e) => e.stopPropagation()}
                        onKeyDown={(e) => e.stopPropagation()}
                      >
                        <span className="hist-confirm-text">
                          确认删除「{fmtTimeHM(it.time)} {it.mode_label}」？
                        </span>
                        <button
                          className="btn btn-danger btn-xs"
                          disabled={delBusy}
                          onClick={() => confirmDeleteReview(g.date, it.time)}
                        >
                          {delBusy ? "删除中…" : "确认删除"}
                        </button>
                        <button
                          className="btn btn-ghost btn-xs"
                          disabled={delBusy}
                          onClick={() => setConfirmDel(null)}
                        >
                          取消
                        </button>
                      </div>
                    ) : (
                      <button
                        className="hh-del"
                        title="删除该复盘"
                        disabled={delBusy}
                        onClick={(e) => {
                          e.stopPropagation();
                          setDelError("");
                          setConfirmDel({ date: g.date, time: it.time });
                        }}
                      >
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                          <path d="M3 6h18" />
                          <path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2" />
                          <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
                        </svg>
                      </button>
                    )}
                  </div>
                ))}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
