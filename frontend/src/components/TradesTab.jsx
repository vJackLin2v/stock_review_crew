import React, { useCallback, useEffect, useRef, useState } from "react";
import * as api from "../api";
import { normalizeJob } from "../normalize";
import MarkdownView from "./MarkdownView";

// R11-R1 交割单分析 Tab：上传 xlsx/xls/csv/txt → 解析表格与指标 → AI 分析报告
// 契约（requirements-r11 §四 + backend/trades_routes.py，T4 提供）：
//   POST /api/trades/upload (multipart) → {trade_id, summary, records, metrics}
//   GET  /api/trades/{trade_id} → {records, metrics, analysis?}
//   POST /api/trades/{trade_id}/analyze → {report, disclaimer}（同步；LLM 不可用降级）
// api.js 提供 DEV mock（后端未就绪时前端契约照常跑通）。
// 样式复用本项目 styles.css 现有类（card/btn/form-input/pos/neg/md/disclaimer/
// progress-*），记录表格包在 .md 容器内复用其表格样式与手机端横向滚动。
const OP_LABEL = { buy: "买入", sell: "卖出", "": "—" };

const finiteNum = (v) => (v == null || v === "" ? NaN : Number(v));
const fmtPrice = (v) => {
  const n = finiteNum(v);
  return Number.isFinite(n) ? n.toFixed(2) : "—";
};
const fmtPct = (v) => {
  const n = finiteNum(v);
  return Number.isFinite(n) ? `${(n * 100).toFixed(2)}%` : "—";
};
const fmtSigned = (v) => {
  const n = finiteNum(v);
  return Number.isFinite(n) ? `${n >= 0 ? "+" : ""}${n.toFixed(2)}` : "—";
};
const fmtQty = (v) => {
  const n = finiteNum(v);
  return Number.isFinite(n) ? String(n) : "—";
};
const fmtMoney = (v) => {
  const n = finiteNum(v);
  return Number.isFinite(n)
    ? n.toLocaleString("zh-CN", { maximumFractionDigits: 2 })
    : "—";
};
const fmtSignedMoney = (v) => {
  const n = finiteNum(v);
  return Number.isFinite(n)
    ? `${n >= 0 ? "+" : "-"}${Math.abs(n).toLocaleString("zh-CN", { maximumFractionDigits: 2 })}`
    : "—";
};
// R13：买卖汇总展示「x 笔 · x 股 · 总计 x 元」（可读性优于紧凑点分）
const tradeSummary = (count, qty, amount) =>
  `${count} 笔 · ${fmtQty(qty)} 股 · 总计 ${fmtMoney(amount)} 元`;

// 合同号脱敏（保留前 4 / 后 4，中间打码；后端已按契约脱敏，此处双保险）
function maskContract(no) {
  const s = String(no || "");
  if (!s) return "—";
  if (s.length <= 8) return "****";
  return `${s.slice(0, 4)}****${s.slice(-4)}`;
}

// 金额列：发生金额（带符号现金变动）优先，无则回退成交金额
function tradeAmount(r) {
  const cash = finiteNum(r.cash_amount);
  if (Number.isFinite(cash) && cash !== 0) return fmtSigned(cash);
  return fmtPrice(r.amount);
}

// R12 优化：盈亏按买入金额显示百分比，格式「金额（百分比）」
function pnlCell(pnl, buyAmount) {
  const v = finiteNum(pnl);
  if (!Number.isFinite(v)) return "—";
  const base = finiteNum(buyAmount);
  const pct = Number.isFinite(base) && base > 0 ? (v / base) * 100 : null;
  return pct != null
    ? `${fmtSignedMoney(v)}（${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%）`
    : fmtSignedMoney(v);
}

export default function TradesTab() {
  const [file, setFile] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [trade, setTrade] = useState(null); // {trade_id, records, metrics}
  const [records, setRecords] = useState([]);
  const [metrics, setMetrics] = useState(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [analyzeJob, setAnalyzeJob] = useState(null); // {id, snap}
  const [report, setReport] = useState(null); // {report, disclaimer}
  const [analyzeError, setAnalyzeError] = useState("");
  const pollTimerRef = useRef(null);
  // R12：历史交割单列表（上传后可回看）
  const [historyList, setHistoryList] = useState([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const [expandedCode, setExpandedCode] = useState(null); // R13 股票汇总行展开

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true);
    setHistoryError("");
    try {
      const res = await api.getTradesList();
      setHistoryList(Array.isArray(res.data) ? res.data : []);
    } catch (err) {
      setHistoryError((err && err.message) || "历史交割单加载失败");
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  useEffect(() => {
    loadHistory();
  }, [loadHistory]);

  const handleLoadHistoryTrade = async (tradeId) => {
    setUploadError("");
    setAnalyzeError("");
    try {
      const data = await api.getTrades(tradeId);
      const recs = Array.isArray(data.records) ? data.records : [];
      setTrade({ trade_id: tradeId, records: recs, metrics: data.metrics });
      setRecords(recs);
      setMetrics(data.metrics || null);
      setReport(
        data.analysis && data.analysis.final_report
          ? {
              report: data.analysis.final_report,
              disclaimer: data.analysis.disclaimer || "仅供参考，不构成投资建议",
            }
          : null
      );
      setAnalyzeJob(null);
    } catch (err) {
      setUploadError((err && err.message) || "加载交割单失败");
    }
  };

  useEffect(
    () => () => {
      if (pollTimerRef.current) clearInterval(pollTimerRef.current);
    },
    []
  );

  const handleFileChange = (e) => {
    const f = e.target.files && e.target.files[0];
    setFile(f || null);
    setUploadError("");
  };

  const handleUpload = async () => {
    if (!file) {
      setUploadError("请先选择交割单文件（xlsx / xls / csv / txt）");
      return;
    }
    setUploading(true);
    setUploadError("");
    setTrade(null);
    setRecords([]);
    setMetrics(null);
    setReport(null);
    setAnalyzeError("");
    try {
      const res = await api.uploadTrades(file);
      const recs = Array.isArray(res.records) ? res.records : [];
      setTrade({ trade_id: res.trade_id, records: recs, metrics: res.metrics });
      setRecords(recs);
      setMetrics(res.metrics || null);
      setReport(null);
      loadHistory();
    } catch (err) {
      setUploadError((err && err.message) || "上传失败，请稍后重试");
    } finally {
      setUploading(false);
    }
  };

  const handleAnalyze = async () => {
    if (!trade || !trade.trade_id) return;
    setAnalyzing(true);
    setAnalyzeError("");
    setReport(null);
    try {
      const res = await api.analyzeTrades(trade.trade_id);
      if (res && res.job_id) {
        // 兼容异步任务契约（jobs 轮询）；当前后端为同步返回，此分支仅防回归
        setAnalyzeJob({ id: res.job_id, snap: { status: "queued", stage: "queued", pct: 0, message: "正在排队…" } });
        pollTimerRef.current = setInterval(async () => {
          try {
            const raw = await api.getJob(res.job_id);
            const norm = normalizeJob(raw);
            setAnalyzeJob((prev) => ({ id: res.job_id, snap: norm }));
            if (norm.isDone) {
              clearInterval(pollTimerRef.current);
              pollTimerRef.current = null;
              const result = norm.result || {};
              setReport({
                report: result.report || "",
                disclaimer: result.disclaimer || "仅供参考，不构成投资建议",
              });
              setAnalyzeJob(null);
              setAnalyzing(false);
            } else if (norm.isError) {
              clearInterval(pollTimerRef.current);
              pollTimerRef.current = null;
              setAnalyzeJob(null);
              setAnalyzeError((norm.error && norm.error.message) || norm.error || "分析任务失败，请重试");
              setAnalyzing(false);
            }
          } catch {
            clearInterval(pollTimerRef.current);
            pollTimerRef.current = null;
            setAnalyzeJob(null);
            setAnalyzeError("分析任务状态查询失败，请重试");
            setAnalyzing(false);
          }
        }, 1000);
      } else if (res && res.report) {
        setReport({ report: res.report, disclaimer: res.disclaimer || "仅供参考，不构成投资建议" });
        setAnalyzing(false);
      } else {
        setAnalyzeError("后端返回格式异常");
        setAnalyzing(false);
      }
    } catch (err) {
      setAnalyzeError((err && err.message) || "发起分析失败，请稍后重试");
      setAnalyzing(false);
    }
  };

  const metricItems = metrics
    ? [
        { label: "成交笔数", value: fmtQty(metrics.trade_count || (metrics.trading && metrics.trading.total_count)) },
        { label: "TWR 收益率", value: fmtPct(metrics.account && metrics.account.total_return_rate) },
        { label: "胜率", value: fmtPct(metrics.pnl && metrics.pnl.win_rate) },
        { label: "盈亏比", value: fmtPrice(metrics.pnl && metrics.pnl.profit_loss_ratio) },
        { label: "累计盈亏（元）", value: fmtSigned(metrics.pnl && metrics.pnl.realized_pnl) },
        { label: "最大回撤", value: fmtPct(metrics.pnl && metrics.pnl.max_drawdown) },
        { label: "平均持仓天数", value: fmtPrice(metrics.trading && metrics.trading.avg_holding_period_days) },
        { label: "翻倍 / 腰斩", value: `${fmtQty(metrics.pnl && metrics.pnl.double_count)} / ${fmtQty(metrics.pnl && metrics.pnl.halved_count)}` },
      ]
    : [];

  const isPartial = metrics && metrics.meta && metrics.meta.is_partial;
  const balanceSource = metrics && metrics.meta && metrics.meta.balance_source;

  return (
    <div className="trades-tab">
      <div className="card">
        <h3 className="card-title">交割单分析</h3>
        <p className="card-sub">
          上传券商交割单（同花顺 / 涨乐财富通 xlsx 为主，支持 csv / txt），自动解析成交记录并计算
          TWR / 胜率 / 盈亏比 / 持仓周期等指标，随后生成 AI 行为画像报告
          {trade ? ` · 当前交易单：${trade.trade_id}` : ""}
        </p>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <input
            type="file"
            accept=".xlsx,.xls,.csv,.txt"
            className="form-input trades-file-input"
            style={{ flex: "1 1 220px", maxWidth: 420 }}
            onChange={handleFileChange}
          />
          <button className="btn btn-accent" disabled={uploading || !file} onClick={handleUpload}>
            {uploading ? "解析中…" : "上传并解析"}
          </button>
        </div>
    {uploadError ? <div className="upload-error">{uploadError}</div> : null}
    </div>

    {/* R12：历史交割单列表（按时间倒序，点击回看明细/指标/已生成报告） */}
    <div className="card" style={{ marginTop: 16 }}>
      <div className="card-head-row">
        <div>
          <h3 className="card-title">历史交割单</h3>
          <p className="card-sub">已上传的交割单记录；点击回看明细与指标，已生成的分析报告可直接查看</p>
        </div>
        <button className="btn btn-ghost btn-xs" onClick={loadHistory} disabled={historyLoading}>
          {historyLoading ? "加载中…" : "刷新"}
        </button>
      </div>
      {historyLoading ? (
        <div className="loading-card" style={{ padding: 18 }}>
          <div className="spinner" />
          <div style={{ marginTop: 8 }}>正在加载历史交割单…</div>
        </div>
      ) : historyError ? (
        <div className="error-panel" style={{ padding: 16 }}>
          <div className="error-detail">{historyError}</div>
          <button className="btn btn-outline btn-sm" onClick={loadHistory}>
            重试
          </button>
        </div>
      ) : historyList.length === 0 ? (
        <div className="empty">暂无历史交割单，上传后会自动出现在这里</div>
      ) : (
        <div className="trades-history-list">
          {historyList.map((h) => (
            <div
              key={h.trade_id}
              role="button"
              tabIndex={0}
              className={`trades-history-item${trade && trade.trade_id === h.trade_id ? " active" : ""}`}
              onClick={() => handleLoadHistoryTrade(h.trade_id)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  handleLoadHistoryTrade(h.trade_id);
                }
              }}
            >
              <span className="trades-history-id">{h.trade_id}</span>
              <span className="trades-history-time">{h.created_at ? h.created_at.slice(0, 16).replace("T", " ") : "—"}</span>
              <span className="tag-chip light">{h.records_count} 笔</span>
              {h.has_analysis ? (
                <span className="tag-chip light">已生成报告</span>
              ) : (
                <span className="tag-chip light">未分析</span>
              )}
              {h.error ? <span className="upload-error" style={{ marginLeft: 8 }}>{h.error}</span> : null}
            </div>
          ))}
        </div>
      )}
    </div>

    {metrics && metricItems.length ? (
        <div className="card" style={{ marginTop: 16 }}>
          <div className="card-head-row">
            <div>
              <h3 className="card-title">核心指标</h3>
              <p className="card-sub">
                {isPartial ? "区间口径（文件从账户中途开始，不冒充总收益）" : "完整历史口径"}
                {balanceSource === "flow" ? " · 流水模式（无资金余额，按发生金额推算）" : ""}
              </p>
            </div>
          </div>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))",
              gap: 10,
            }}
          >
            {metricItems.map((m) => (
              <div
                key={m.label}
                style={{
                  background: "var(--field-bg, #f4f4f2)",
                  border: "1px solid var(--border, #e5e5e2)",
                  borderRadius: 10,
                  padding: "10px 12px",
                }}
              >
                <div style={{ fontSize: 12, color: "var(--text-2, #666)" }}>{m.label}</div>
                <div style={{ fontSize: 17, fontWeight: 700, marginTop: 2 }}>{m.value}</div>
              </div>
            ))}
          </div>
        </div>
      ) : null}

{(metrics && Array.isArray(metrics.stocks) && metrics.stocks.length) || records.length ? (
<div className="card" style={{ marginTop: 16 }}>
<div className="card-head-row">
<div>
<h3 className="card-title">股票汇总</h3>
<p className="card-sub">
R13：按股票统计（已过滤银行转账等非交易操作）
{metrics && Array.isArray(metrics.stocks) ? ` · ${metrics.stocks.length} 只股票` : ""}
</p>
</div>
<button className="btn btn-accent" disabled={analyzing} onClick={handleAnalyze}>
{analyzing ? "分析中…" : "生成分析报告"}
</button>
</div>
<div className="md" style={{ overflowX: "auto" }}>
<table style={{ minWidth: 760 }}>
<thead>
<tr>
<th>代码</th>
<th>名称</th>
<th>状态</th>
<th>买入（笔·股·额）</th>
<th>卖出（笔·股·额）</th>
<th>已实现盈亏</th>
<th>未实现盈亏</th>
</tr>
</thead>
<tbody>
{metrics && Array.isArray(metrics.stocks)
? metrics.stocks.map((s) => {
const expanded = expandedCode === s.code;
const buy = s.buy_count || 0;
const sell = s.sell_count || 0;
const status =
buy === 0 && sell > 0
? "期初持仓卖出"
: buy > 0 && sell === 0
? "持仓中（未卖出）"
: s.status === "held"
? "持仓中"
: "已平仓";
const detailRows = records.filter(
(r) => r.code === s.code && (r.op_type === "BUY" || r.op_type === "SELL" || r.op_type === "buy" || r.op_type === "sell")
);
return (
<React.Fragment key={s.code}>
<tr
className="trades-stock-row"
onClick={() => setExpandedCode(expanded ? null : s.code)}
style={{ cursor: "pointer" }}
>
<td>{s.code}</td>
<td>{s.name || "—"}</td>
<td>{status}</td>
<td>{buy ? tradeSummary(buy, s.buy_qty, s.buy_amount) : "—"}</td>
<td>{sell ? tradeSummary(sell, s.sell_qty, s.sell_amount) : "—"}</td>
      <td className={finiteNum(s.realized_pnl) > 0 ? "pos" : finiteNum(s.realized_pnl) < 0 ? "neg" : ""}>
        {s.realized_pnl != null ? pnlCell(s.realized_pnl, s.buy_amount) : "—"}
      </td>
      <td className={finiteNum(s.unrealized_pnl) > 0 ? "pos" : finiteNum(s.unrealized_pnl) < 0 ? "neg" : ""}>
        {s.unrealized_pnl && finiteNum(s.unrealized_pnl) !== 0 ? pnlCell(s.unrealized_pnl, s.buy_amount) : "—"}
      </td>
</tr>
{expanded ? (
<tr key={`${s.code}-detail`}>
<td colSpan={7} style={{ padding: 0 }}>
<div className="trades-stock-detail">
{detailRows.length ? (
<table style={{ minWidth: 640 }}>
<thead>
<tr>
<th>日期</th>
<th>方向</th>
<th>数量</th>
<th>价格</th>
<th>金额</th>
<th>费用</th>
</tr>
</thead>
<tbody>
{detailRows.map((r, i) => (
<tr key={i}>
<td>{r.trade_date || "—"}</td>
<td className={r.op_type === "SELL" || r.op_type === "sell" ? "neg" : "pos"}>
{r.op_type === "SELL" || r.op_type === "sell" ? "卖出" : "买入"}
</td>
<td>{fmtQty(r.qty)}</td>
<td>{fmtPrice(r.price)}</td>
<td>{tradeAmount(r)}</td>
<td>{fmtPrice(r.fee)}</td>
</tr>
))}
</tbody>
</table>
) : (
<div className="empty" style={{ padding: 12 }}>该股票无买卖记录（仅转账/其他操作，已过滤）</div>
)}
</div>
</td>
</tr>
) : null}
</React.Fragment>
);
})
: (
<tr>
<td colSpan={7} style={{ textAlign: "center", padding: 16 }}>
暂无可统计的股票交易（已过滤银行转账等非交易操作）
</td>
</tr>
)}
</tbody>
</table>
</div>
</div>
      ) : null}

      {analyzeJob ? (
        <div className="card progress-card" style={{ marginTop: 16 }}>
          <div className="progress-head">
            <div className="spinner" />
            <div className="progress-title">正在生成交割单分析报告</div>
          </div>
          <div className="progress-track">
            <div className="progress-fill" style={{ width: `${Math.max(0, Math.min(100, Number(analyzeJob.snap.pct) || 0))}%` }} />
          </div>
          <div className="progress-pct">{Math.round(Number(analyzeJob.snap.pct) || 0)}%</div>
          <div className="progress-msg">{analyzeJob.snap.message || "分析中…"}</div>
        </div>
      ) : null}

      {analyzeError ? <div className="upload-error" style={{ marginTop: 12 }}>{analyzeError}</div> : null}

      {report && report.report ? (
        <div className="card" style={{ marginTop: 16 }}>
          <div className="card-head-row">
            <h3 className="card-title">分析报告</h3>
          </div>
          <MarkdownView text={report.report} />
          <div className="disclaimer" style={{ marginTop: 12 }}>
            <span>⚠️</span>
            <div>
              <b>免责声明：</b>
              {report.disclaimer || "仅供参考，不构成投资建议。市场有风险，入市需谨慎。"}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
