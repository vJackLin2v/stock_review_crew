import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as api from "../api";
import {
  normalizeBacktest,
  normalizeBacktestHistoryItem,
} from "../normalize";
import { isNone, num } from "../format";
import { stageLabel } from "../stages";
import { Chart } from "./Charts";

// 历史回测「回测」tab（requirements-backtest-ui §七）
//   顶部选范围 + force/max_rounds → 启动；2s 轮询进度（终态停）；
//   总进度条 + 每天耗时折线图 + 每天可展开列表（settle/节点/分析师耗时）；
//   底部历史回测（可点开终态只读查看）。
const ACCENT = "#6859cf";
const TEXT2 = "#7a7f87";

// 阶段 key → 中文（固定：fetch / analysts / host / report）
const STAGE_LABELS = {
  fetch: "取数",
  analysts: "分析师",
  host: "主持人",
  report: "报告",
};
const STAGE_ORDER = ["fetch", "analysts", "host", "report"];

// 耗时毫秒 → 中文可读（<1000 显示毫秒，其余显示 秒/分钟）
export function fmtMS(v) {
  const n = num(v, NaN);
  if (!Number.isFinite(n)) return "—";
  if (n < 1000) return `${Math.round(n)} ms`;
  if (n < 60000) return `${(n / 1000).toFixed(1)} 秒`;
  const m = Math.floor(n / 60000);
  const s = ((n % 60000) / 1000).toFixed(0);
  return `${m} 分 ${s} 秒`;
}

const STATUS_LABEL = {
  pending: "待处理",
  running: "进行中",
  done: "成功",
  failed: "失败",
  skipped: "跳过",
};
const STATUS_CLASS = {
  done: "bt-status-ok",
  failed: "bt-status-fail",
  running: "bt-status-run",
  skipped: "bt-status-skip",
  pending: "bt-status-pending",
};

const BT_TOP_STATUS = {
  queued: "排队中",
  running: "进行中",
  done: "已完成",
  error: "失败",
  cancelled: "已取消",
};

// 单日行的横向耗时条（settle / 各节点 / 各分析师，按最大值归一）
function DurationBar({ label, value, max, accent }) {
  const n = num(value, NaN);
  if (!Number.isFinite(n)) {
    return (
      <div className="bt-bar-row">
        <span className="bt-bar-label">{label}</span>
        <span className="bt-bar-value">—</span>
      </div>
    );
  }
  const ratio = max > 0 ? Math.min(100, (n / max) * 100) : 0;
  return (
    <div className="bt-bar-row">
      <span className="bt-bar-label">{label}</span>
      <div className="bt-bar-track">
        <div className="bt-bar-fill" style={{ width: `${ratio}%`, background: accent || ACCENT }} />
      </div>
      <span className="bt-bar-value">{fmtMS(n)}</span>
    </div>
  );
}

// 单日展开明细：settle + 4 节点 + 5 分析师耗时（横向条形）
function DayDetail({ day }) {
  const settleMs = num(day.settle && day.settle.elapsed_ms, NaN);
  const reviewMs = num(day.review && day.review.elapsed_ms, NaN);
  const stageList = (Array.isArray(day.stages) ? day.stages : [])
    .filter((s) => s && s.key)
    .sort((a, b) => STAGE_ORDER.indexOf(a.key) - STAGE_ORDER.indexOf(b.key));
  const analystList = [];
  stageList.forEach((s) => {
    if (s.key === "analysts" && Array.isArray(s.analysts)) {
      analystList.push(...s.analysts);
    }
  });
  const maxMs = Math.max(
    Number.isFinite(settleMs) ? settleMs : 0,
    Number.isFinite(reviewMs) ? reviewMs : 0,
    ...stageList.map((s) => (Number.isFinite(num(s.elapsed_ms, NaN)) ? num(s.elapsed_ms) : 0)),
    ...analystList.map((a) => (Number.isFinite(num(a && a.elapsed_ms, NaN)) ? num(a.elapsed_ms) : 0))
  );
  const maxValue = maxMs > 0 ? maxMs : 1;

  return (
    <div className="bt-day-detail">
      <div className="bt-detail-title">当天耗时分解</div>
      {/* settle 成交·拒绝 摘要 */}
      <div className="bt-settle-summary">
        <span>settle：{fmtMS(settleMs)}</span>
        <span className="bt-settle-count">
          成交 <b className="pos">{num(day.settle && day.settle.filled, NaN) == null ? "—" : `${num(day.settle && day.settle.filled)} 笔`}</b>
          ·
          拒绝 <b className="neg">{num(day.settle && day.settle.rejected, NaN) == null ? "—" : `${num(day.settle && day.settle.rejected)} 笔`}</b>
        </span>
      </div>
      <DurationBar label="settle 撮合" value={settleMs} max={maxValue} accent="#5c7cfa" />
      <DurationBar label="review 复盘（合计）" value={reviewMs} max={maxValue} accent={ACCENT} />
      {stageList.length ? (
        <div className="bt-detail-sub">节点耗时</div>
      ) : null}
      {stageList.map((s) => (
        <DurationBar key={s.key} label={STAGE_LABELS[s.key] || stageLabel(s.key)} value={num(s.elapsed_ms, NaN)} max={maxValue} accent={ACCENT} />
      ))}
      {analystList.length ? (
        <div className="bt-detail-sub">分析师耗时</div>
      ) : null}
      {analystList.map((a, i) => (
        <DurationBar key={`${a.skill_id || i}-${i}`} label={a.name || `分析师${i + 1}`} value={num(a && a.elapsed_ms, NaN)} max={maxValue} accent="#6d5bd0" />
      ))}
      {/* 失败原因 */}
      {day.error ? (
        <div className="bt-day-error">
          <span className="bt-error-title">失败原因：</span>
          {day.error}
        </div>
      ) : null}
    </div>
  );
}

// 每天一行（可展开）
function DayRow({ day, isCurrent }) {
  const [open, setOpen] = useState(false);
  const status = day.status || "pending";
  const dayMs = num(day.elapsed_ms, NaN);
  const settle = (day.settle || {});
  const filled = num(settle.filled, NaN);
  const rejected = num(settle.rejected, NaN);
  const reviewMs = num(day.review && day.review.elapsed_ms, NaN);
  const hasDetail =
    Number.isFinite(reviewMs) ||
    (Array.isArray(day.stages) && day.stages.length > 0);
  return (
    <div
      className={`bt-day${isCurrent ? " current" : ""}${status === "failed" ? " failed" : ""}`}
    >
      <button
        type="button"
        className="bt-day-row"
        onClick={() => hasDetail && setOpen((o) => !o)}
        aria-expanded={open}
        disabled={!hasDetail}
      >
        <span className="bt-day-date">
          {isCurrent ? <span className="bt-current-dot" title="当前处理中" /> : null}
          {day.date || "—"}
        </span>
        <span className={`bt-status ${STATUS_CLASS[status] || STATUS_CLASS.pending}`}>
          {STATUS_LABEL[status] || status}
        </span>
        <span className="bt-cell bt-cell-ms">{fmtMS(dayMs)}</span>
        <span className="bt-cell">
          {settle.filled == null && settle.rejected == null
            ? "—"
            : `${isNone(filled) ? "—" : filled} 成 / ${isNone(rejected) ? "—" : rejected} 拒`}
        </span>
        <span className="bt-cell bt-cell-ms">{fmtMS(reviewMs)}</span>
        <span className="bt-cell bt-cell-reason">
          {day.error ? <span className="bt-fail-reason">{day.error}</span> : "—"}
        </span>
        <span className="bt-row-caret">{hasDetail ? (open ? "▾" : "▸") : ""}</span>
      </button>
      {open && hasDetail ? (
        <div className="bt-day-body">
          <DayDetail day={day} />
        </div>
      ) : null}
    </div>
  );
}

// 折线图：每天 elapsed_ms 趋势
function DailyTrendChart({ days }) {
  const rows = useMemo(() => {
    const list = (Array.isArray(days) ? days : [])
      .map((d) => ({ date: (d && d.date) || "—", ms: num(d && d.elapsed_ms, NaN) }))
      .filter((r) => Number.isFinite(r.ms));
    return list;
  }, [days]);
  const option = useMemo(() => {
    if (!rows.length) return { series: [] };
    return {
      tooltip: {
        trigger: "axis",
        backgroundColor: "#fff",
        borderColor: "#e7e8eb",
        textStyle: { color: "#17181d", fontSize: 12 },
        extraCssText: "box-shadow:0 4px 14px rgba(0,0,0,.08);border-radius:8px;",
        formatter(params) {
          const i = params[0].dataIndex;
          const r = rows[i];
          const s = fmtMS(r.ms);
          return `<b>${r.date}</b><br/>每天耗时：<b style="color:${ACCENT}">${s}</b>`;
        },
      },
      grid: { left: 70, right: 20, top: 30, bottom: 32 },
      xAxis: {
        type: "category",
        data: rows.map((r) => r.date),
        boundaryGap: false,
        axisLine: { lineStyle: { color: "#d8dad5" } },
        axisLabel: { color: TEXT2, fontSize: 10.5, interval: "auto" },
      },
      yAxis: {
        type: "value",
        name: "耗时(秒)",
        nameTextStyle: { color: TEXT2, fontSize: 11 },
        axisLabel: {
          color: TEXT2,
          fontSize: 11,
          formatter: (v) => (v >= 1000 ? `${(v / 1000).toFixed(0)}s` : `${v}`),
        },
        splitLine: { lineStyle: { color: "#eef0ec" } },
      },
      series: [
        {
          name: "每天耗时",
          type: "line",
          smooth: 0.3,
          symbol: "circle",
          symbolSize: 4,
          data: rows.map((r) => Math.round(r.ms)),
          lineStyle: { width: 2, color: ACCENT },
          itemStyle: { color: ACCENT },
          areaStyle: {
            color: {
              type: "linear",
              x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [
                { offset: 0, color: "rgba(104,89,207,.18)" },
                { offset: 1, color: "rgba(104,89,207,0)" },
              ],
            },
          },
        },
      ],
    };
  }, [rows]);
  return <Chart option={option} height={280} emptyText="暂无每天耗时数据" />;
}

function HistoryList({ history, onOpen, loading, error }) {
  const runs = (history && history.runs) || [];
  if (loading) {
    return <div className="bt-hist-empty">正在加载历史回测…</div>;
  }
  if (error) {
    return <div className="bt-hist-empty">{error}</div>;
  }
  if (!runs.length) {
    return <div className="bt-hist-empty">暂无历史回测记录</div>;
  }
  return (
    <div className="bt-hist">
      {runs.map((r, i) => (
        <button
          key={i}
          type="button"
          className="bt-hist-item"
          onClick={() => onOpen(r.id)}
          title={`${r.start} ~ ${r.end} · ${BT_TOP_STATUS[r.status] || r.status}`}
        >
          <span className="bt-hist-range">
            {r.start} ~ {r.end}
          </span>
          <span className={`bt-status ${STATUS_CLASS[r.status] || ""}`}>
            {BT_TOP_STATUS[r.status] || r.status}
          </span>
          <span className="bt-hist-meta">
            {r.days_done}/{r.days_total} 天 · {fmtMS(r.total_elapsed_ms)}
          </span>
        </button>
      ))}
    </div>
  );
}

export default function BacktestView({ onBack }) {
  const [startDate, setStartDate] = useState("2026-07-01");
  const [endDate, setEndDate] = useState("2026-08-14");
  const [force, setForce] = useState(false);
  const [maxRounds, setMaxRounds] = useState(3);
  const [bt, setBt] = useState(null);   // 当前展示的回测快照（norm）
  const [btError, setBtError] = useState("");
  const [startBusy, setStartBusy] = useState(false);
  const [polling, setPolling] = useState(false);
  const pollRef = useRef(null);
  const [history, setHistory] = useState({ runs: [], loading: true, error: "" });

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    setPolling(false);
  }, []);

  const loadHistory = useCallback(async () => {
    try {
      const res = await api.getBacktestHistory();
      setHistory({
        runs: (Array.isArray(res.data && res.data.runs) ? res.data.runs : [])
          .map(normalizeBacktestHistoryItem),
        loading: false,
        error: "",
      });
    } catch (err) {
      setHistory({ runs: [], loading: false, error: (err && err.message) || "历史回测加载失败" });
    }
  }, []);

  useEffect(() => {
    loadHistory();
    return () => stopPolling();
  }, [loadHistory, stopPolling]);

  // 轮询 GET /api/backtest/{id}（2s），终态停止
  const beginPolling = useCallback(
    (id, initial) => {
      stopPolling();
      setBt(initial);
      setBtError("");
      setPolling(true);
      pollRef.current = setInterval(async () => {
        try {
          const snap = await api.getBacktest(id);
          const norm = normalizeBacktest(snap);
          setBt(norm);
          if (norm.isDone || norm.isError || norm.isCancelled) {
            stopPolling();
            loadHistory();
          }
        } catch (err) {
          stopPolling();
          setBtError((err && err.message) || "轮询回测进度失败");
        }
      }, 2000);
    },
    [stopPolling, loadHistory]
  );

  const handleStart = useCallback(async () => {
    if (!startDate || !endDate) {
      setBtError("请选择起止日期");
      return;
    }
    if (startDate > endDate) {
      setBtError("开始日期不能晚于结束日期");
      return;
    }
    setStartBusy(true);
    setBtError("");
    try {
      const res = await api.startBacktest({ start: startDate, end: endDate, force, max_rounds: maxRounds });
      const id = res && (res.backtest_id || res.id);
      if (!id) throw new Error("后端未返回回测编号");
      const init = normalizeBacktest({
        id,
        status: "queued",
        start: startDate,
        end: endDate,
        params: { force, max_rounds: maxRounds, mode: "close" },
        days_total: 0,
        days_done: 0,
        days: [],
        current: { date: "", stage: "queued" },
        created_at: "",
        finished_at: null,
        total_elapsed_ms: 0,
      });
      beginPolling(id, init);
      setPolling(true);
    } catch (err) {
      setBtError((err && err.message) || "启动回测失败");
    } finally {
      setStartBusy(false);
    }
  }, [startDate, endDate, force, maxRounds, beginPolling]);

  const handleCancel = useCallback(async () => {
    if (!bt || !bt.id) return;
    try {
      await api.cancelBacktest(bt.id);
    } catch (err) {
      setBtError((err && err.message) || "取消失败");
    }
  }, [bt]);

  const handleOpenHistory = useCallback(
    async (id) => {
      setBtError("");
      try {
        const snap = await api.getBacktest(id);
        setBt(normalizeBacktest(snap));
        setPolling(false);
        stopPolling();
      } catch (err) {
        setBtError((err && err.message) || "加载历史回测失败");
      }
    },
    [stopPolling]
  );

  const total = num(bt && bt.days_total, NaN);
  const done = num(bt && bt.days_done, NaN);
  const pct =
    Number.isFinite(total) && total > 0
      ? Math.round((done / total) * 100)
      : 0;
  const totalMs = num(bt && bt.total_elapsed_ms, NaN);
  const currentDate = (bt && bt.current && bt.current.date) || "";
  const currentStage = (bt && bt.current && bt.current.stage) || "";
  // 折线图用已完成/失败/跳过的天（进行中/待处理无实测耗时）
  const chartDays = useMemo(
    () =>
      (Array.isArray(bt && bt.days) ? bt.days : []).filter(
        (d) => d.status === "done" || d.status === "failed" || d.status === "skipped"
      ),
    [bt]
  );

  return (
    <div className="bt-wrap">
      {/* 顶部：范围 + 启动 */}
      <div className="card bt-top">
        <div className="card-head-row">
          <h2 className="card-title">历史回测</h2>
          <button className="btn btn-ghost btn-sm" onClick={onBack}>返回首页</button>
        </div>
        <p className="card-sub">选择起止日期，逐日跑「撮合 + 复盘」，实时查看每天/节点/分析师耗时</p>
        <div className="bt-fields">
          <label className="form-label bt-field-label">开始日期</label>
          <input
            type="date"
            className="form-input bt-field"
            value={startDate}
            max={endDate}
            onChange={(e) => setStartDate(e.target.value)}
          />
          <label className="form-label bt-field-label bt-sep">结束日期</label>
          <input
            type="date"
            className="form-input bt-field"
            value={endDate}
            min={startDate}
            onChange={(e) => setEndDate(e.target.value)}
          />
          <label className="bt-check">
            <input
              type="checkbox"
              checked={force}
              onChange={(e) => setForce(e.target.checked)}
            />
            强制重跑（忽略已有复盘）
          </label>
          <label className="form-label bt-field-label bt-sep">最大轮数</label>
          <input
            type="number"
            className="form-input bt-field bt-num"
            value={maxRounds}
            min={1}
            max={20}
            onChange={(e) => setMaxRounds(Math.max(1, Math.min(20, Number(e.target.value) || 3)))}
          />
        </div>
        {btError ? <div className="bt-error">{btError}</div> : null}
        <div className="bt-top-actions">
          {bt && (bt.isRunning || bt.status === "queued") ? (
            <button className="btn btn-outline btn-sm" onClick={handleCancel} title="取消后续回测日期">
              取消回测
            </button>
          ) : null}
          <button
            className="btn btn-accent"
            onClick={handleStart}
            disabled={startBusy || (bt && (bt.isRunning || bt.status === "queued"))}
          >
            {startBusy
              ? "启动中…"
              : bt && (bt.isRunning || bt.status === "queued")
                ? "回测进行中"
                : "开始回测"}
          </button>
        </div>
      </div>

      {/* 当前回测进度 */}
      {bt ? (
        <div className="card bt-progress">
          <div className="card-head-row">
            <h2 className="card-title">
              回测 {bt.id || ""}
              <span className={`bt-status ${STATUS_CLASS[bt.status] || ""}`} style={{ marginLeft: 8 }}>
                {BT_TOP_STATUS[bt.status] || bt.status}
              </span>
            </h2>
            {bt.offline ? <span className="pill offline">离线 mock 模式（DEV 降级）</span> : null}
          </div>

          {/* 总进度条 */}
          <div className="bt-progress-row">
            <span className="bt-progress-label">
              总进度 {Number.isFinite(done) ? done : 0}/{Number.isFinite(total) ? total : "—"} 天
            </span>
            <span className="bt-progress-pct">{pct}%</span>
          </div>
          <div className="progress-track">
            <div className="progress-fill" style={{ width: `${pct}%`, background: ACCENT }} />
          </div>
          <div className="bt-progress-meta">
            {currentDate ? (
              <span>当前日期：<b>{currentDate}</b></span>
            ) : null}
            {currentStage ? (
              <span>当前阶段：<b>{STAGE_LABELS[currentStage] || stageLabel(currentStage)}</b></span>
            ) : null}
            <span>总耗时：<b>{fmtMS(totalMs)}</b></span>
          </div>

          {/* 每天耗时折线图 */}
          <div className="bt-chart-block">
            <div className="bt-section-title">每天耗时趋势</div>
            <DailyTrendChart days={bt.days} />
          </div>

          {/* 每天列表 */}
          <div className="bt-section-title">每天明细{chartDays.length ? `（${chartDays.length} 天已实测）` : ""}</div>
          {Array.isArray(bt.days) && bt.days.length ? (
            <div className="bt-days">
              {bt.days.map((d) => (
                <DayRow
                  key={d.date || `${d.status}-${Math.random()}`}
                  day={d}
                  isCurrent={bt.isRunning && d.status === "running"}
                />
              ))}
            </div>
          ) : (
            <div className="bt-hist-empty">暂无每日数据（等待首日开始…）</div>
          )}
        </div>
      ) : (
        <div className="card bt-progress bt-empty-state">
          <div className="bt-empty-title">尚未开始回测</div>
          <div className="bt-empty-sub">设置起止日期后点击「开始回测」，这里会实时显示逐日进度。</div>
        </div>
      )}

      {/* 历史回测 */}
      <div className="card bt-history">
        <div className="card-head-row">
          <h2 className="card-title">历史回测</h2>
          <button className="btn btn-ghost btn-sm" onClick={loadHistory}>刷新</button>
        </div>
        <HistoryList history={history} onOpen={handleOpenHistory} loading={history.loading} error={history.error} />
      </div>
    </div>
  );
}
