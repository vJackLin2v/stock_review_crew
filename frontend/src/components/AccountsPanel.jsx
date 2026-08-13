import React, { useCallback, useEffect, useMemo, useState } from "react";
import * as api from "../api";
import { normalizeAccountDetail, normalizeAccounts } from "../normalize";
import { fmtAmount, fmtCount, fmtPct, num } from "../format";
import { Chart } from "./Charts";

// R12-A3：侧栏「模拟账户」独立视图
//   GET /api/accounts            → 5 位分析师摘要列表
//   GET /api/accounts/{skill_id} → 单人详情（持仓/交易记录含拒绝/收益率曲线/盈亏）
const ACCENT = "#6859cf";
const GRAY = "#adb5bd";
const BLUE = "#5c7cfa";
const INITIAL_CAPITAL = 100000; // 初始资金 10 万
const AVATAR_CLASSES = ["", "v2", "v3", "v4", "v5"];

// 带符号金额（正红负绿），None → 「—」
export function fmtSignedMoney(v) {
  const n = num(v, NaN);
  if (!Number.isFinite(n)) return "—";
  return `${n > 0 ? "+" : ""}${fmtAmount(n)}`;
}

// 收益率曲线 ECharts option（points: [{date, value}]，value 为小数收益率）
export function buildReturnCurveOption(points) {
  const pts = (Array.isArray(points) ? points : []).filter(
    (p) => p && p.value != null && Number.isFinite(num(p.value, NaN))
  );
  if (!pts.length) return { series: [] };
  const dates = pts.map((p) => p.date || "—");
  const series = [
    {
      name: "累计收益率",
      type: "line",
      smooth: 0.3,
      symbol: "circle",
      symbolSize: 4,
      data: pts.map((p) => Math.round(p.value * 10000) / 100), // 小数 → 百分比
      lineStyle: { width: 2, color: ACCENT },
      itemStyle: { color: ACCENT },
      areaStyle: {
        color: {
          type: "linear",
          x: 0,
          y: 0,
          x2: 0,
          y2: 1,
          colorStops: [
            { offset: 0, color: "rgba(104,89,207,.16)" },
            { offset: 1, color: "rgba(104,89,207,0)" },
          ],
        },
      },
      markLine: {
        silent: true,
        symbol: "none",
        label: { formatter: "0%", position: "insideEndTop", color: GRAY, fontSize: 10 },
        lineStyle: { type: "dashed", color: GRAY, width: 1 },
        data: [{ yAxis: 0 }],
      },
    },
  ];
  return {
    tooltip: {
      trigger: "axis",
      backgroundColor: "#fff",
      borderColor: "#e7e8eb",
      textStyle: { color: "#17181d", fontSize: 12 },
      extraCssText: "box-shadow:0 4px 14px rgba(0,0,0,.08);border-radius:8px;",
      formatter(params) {
        const i = params[0].dataIndex;
        const v = params[0].value;
        const cls = v > 0 ? "#e03131" : v < 0 ? "#0ca678" : "#7a7f87";
        return `<b>${dates[i]}</b><br/>累计收益率：<b style="color:${cls}">${v.toFixed(2)}%</b>`;
      },
    },
    legend: { data: ["累计收益率"], top: 4, textStyle: { color: "#7a7f87", fontSize: 11 } },
    grid: { left: 52, right: 20, top: 34, bottom: 28 },
    xAxis: {
      type: "category",
      data: dates,
      boundaryGap: false,
      axisLine: { lineStyle: { color: "#d8dad5" } },
      axisLabel: { color: "#7a7f87", fontSize: 10.5, hideOverlap: true },
    },
    yAxis: {
      type: "value",
      scale: true,
      axisLabel: { color: "#7a7f87", fontSize: 10.5, formatter: (v) => `${v}%` },
      splitLine: { lineStyle: { color: "#eef0ec" } },
    },
    series,
  };
}

// 兼容旧引用：资产曲线（总资产，元）——ReportView 等仍使用
export function buildAssetCurveOption(points, { heightLabel = "总资产" } = {}) {
  const pts = (Array.isArray(points) ? points : []).filter(
    (p) => p && p.value != null && Number.isFinite(num(p.value, NaN))
  );
  if (!pts.length) return { series: [] };
  const dates = pts.map((p) => p.date || "—");
  return {
    tooltip: {
      trigger: "axis",
      backgroundColor: "#fff",
      borderColor: "#e7e8eb",
      textStyle: { color: "#17181d", fontSize: 12 },
      formatter(params) {
        const i = params[0].dataIndex;
        let html = `<b>${dates[i]}</b>`;
        params.forEach((pp) => {
          if (pp.value == null) return;
          html += `<br/>${pp.seriesName}：<b>${fmtAmount(pp.value)}</b>`;
        });
        return html;
      },
    },
    legend: { data: series2(pts, heightLabel).map((s) => s.name), top: 4, textStyle: { color: "#7a7f87", fontSize: 11 } },
    grid: { left: 66, right: 20, top: 34, bottom: 28 },
    xAxis: {
      type: "category",
      data: dates,
      boundaryGap: false,
      axisLine: { lineStyle: { color: "#d8dad5" } },
      axisLabel: { color: "#7a7f87", fontSize: 10.5, hideOverlap: true },
    },
    yAxis: {
      type: "value",
      scale: true,
      axisLabel: { color: "#7a7f87", fontSize: 10.5 },
      splitLine: { lineStyle: { color: "#eef0ec" } },
    },
    series: series2(pts, heightLabel),
  };
}

function series2(pts, heightLabel) {
  return [
    {
      name: heightLabel,
      type: "line",
      smooth: 0.3,
      symbol: "circle",
      symbolSize: 4,
      data: pts.map((p) => Math.round(p.value * 100) / 100),
      lineStyle: { width: 2, color: ACCENT },
      itemStyle: { color: ACCENT },
      areaStyle: {
        color: {
          type: "linear",
          x: 0,
          y: 0,
          x2: 0,
          y2: 1,
          colorStops: [
            { offset: 0, color: "rgba(104,89,207,.16)" },
            { offset: 1, color: "rgba(104,89,207,0)" },
          ],
        },
      },
    },
  ];
}

export function PositionsTable({ positions, nameMap = {} }) {
  const rows = Array.isArray(positions) ? positions : [];
  if (!rows.length) {
    return <div className="card-sub">暂无持仓</div>;
  }
  return (
    <table className="table table-sm">
      <thead>
        <tr>
          <th>代码</th>
          <th>名称</th>
          <th>持仓</th>
          <th>可卖</th>
          <th>成本价</th>
          <th>现价</th>
          <th>市值</th>
          <th>浮动盈亏</th>
          <th>盈亏率</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((p) => {
          const pnlCls = (p.pnl || 0) > 0 ? "pos" : (p.pnl || 0) < 0 ? "neg" : "zero";
          return (
            <tr key={p.code}>
              <td>{p.code}</td>
              <td>{nameMap[p.code] || p.name || p.code}</td>
              <td>{fmtCount(p.qty)}</td>
              <td>{fmtCount(p.available_qty)}</td>
              <td>{Number.isFinite(p.avg_cost) ? p.avg_cost.toFixed(2) : "—"}</td>
              <td>{Number.isFinite(p.price) ? p.price.toFixed(2) : "—"}</td>
              <td>{Number.isFinite(p.market_value) ? fmtAmount(p.market_value) : "—"}</td>
              <td className={pnlCls}>{fmtSignedMoney(p.pnl)}</td>
              <td className={pnlCls}>
                {Number.isFinite(p.pnl_rate) ? `${(p.pnl_rate * 100).toFixed(2)}%` : "—"}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

// 2026-08-13：历史持仓（已全部卖出的股票及其完整买卖周期与盈亏，红涨绿跌）
export function ClosedPositionsTable({ closedPositions, nameMap = {} }) {
  const rows = Array.isArray(closedPositions) ? closedPositions : [];
  if (!rows.length) {
    return <div className="card-sub">暂无历史持仓</div>;
  }
  return (
    <table className="table table-sm">
      <thead>
        <tr>
          <th>代码</th>
          <th>名称</th>
          <th>买入股数</th>
          <th>卖出股数</th>
          <th>平均买价</th>
          <th>平均卖价</th>
          <th>已实现盈亏</th>
          <th>首买日</th>
          <th>末卖日</th>
          <th>持仓天数</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((c) => {
          const pnlCls =
            (c.realized_pnl || 0) > 0 ? "pos" : (c.realized_pnl || 0) < 0 ? "neg" : "zero";
          return (
            <tr key={c.code}>
              <td>{c.code}</td>
              <td>{nameMap[c.code] || c.name || c.code}</td>
              <td>{fmtCount(c.buy_qty)}</td>
              <td>{fmtCount(c.sell_qty)}</td>
              <td>{Number.isFinite(c.avg_buy_price) ? c.avg_buy_price.toFixed(2) : "—"}</td>
              <td>{Number.isFinite(c.avg_sell_price) ? c.avg_sell_price.toFixed(2) : "—"}</td>
              <td className={pnlCls}>{fmtSignedMoney(c.realized_pnl)}</td>
              <td>{c.first_buy_date || "—"}</td>
              <td>{c.last_sell_date || "—"}</td>
              <td>{Number.isFinite(c.holding_days) ? `${c.holding_days} 天` : "—"}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export function AccountTradesTable({ trades, nameMap = {} }) {
  const rows = Array.isArray(trades) ? trades : [];
  // 2026-08-12：状态筛选，默认只展示成交（不展示被拒交易）
  const [statusFilter, setStatusFilter] = useState("成交");
  const visible = statusFilter === "全部" ? rows : rows.filter((t) => (statusFilter === "拒绝") === !!t.isRejected);
  if (!rows.length) {
    return <div className="card-sub">暂无交易记录</div>;
  }
  return (
    <div>
      <div className="trades-filter">
        {["成交", "拒绝", "全部"].map((f) => (
          <button
            key={f}
            className={`btn btn-sm ${statusFilter === f ? "btn-accent" : "btn-ghost"}`}
            onClick={() => setStatusFilter(f)}
          >
            {f}
          </button>
        ))}
      </div>
      <table className="table table-sm">
        <thead>
          <tr>
            <th>日期</th>
            <th>代码</th>
            <th>名称</th>
            <th>方向</th>
            <th>价格</th>
            <th>数量</th>
            <th>金额</th>
            <th>费用</th>
            <th>状态</th>
            <th>原因</th>
          </tr>
        </thead>
        <tbody>
          {visible.map((t, i) => (
            <tr key={`${t.date}-${t.code}-${i}`}>
              <td>{t.date || "—"}</td>
              <td>{t.code || "—"}</td>
              <td>{nameMap[t.code] || t.name || t.code}</td>
              <td className={t.side === "buy" ? "pos" : t.side === "sell" ? "neg" : "zero"}>
                {t.side === "buy" ? "买入" : t.side === "sell" ? "卖出" : "—"}
              </td>
              <td>{t.price != null ? t.price.toFixed(2) : "—"}</td>
              <td>{fmtCount(t.qty)}</td>
              <td>{t.amount != null ? fmtAmount(t.amount) : "—"}</td>
              <td>{t.fee != null ? fmtAmount(t.fee) : "—"}</td>
              <td className={t.isRejected ? "neg" : "pos"}>
                {t.isRejected ? "拒绝" : "成交"}
              </td>
              <td title={t.reject_reason}>{t.reject_reason ? t.reject_reason.slice(0, 24) : "—"}</td>
            </tr>
          ))}
          {!visible.length ? (
            <tr>
              <td colSpan={10} className="card-sub">无{statusFilter === "全部" ? "" : statusFilter}交易记录</td>
            </tr>
          ) : null}
        </tbody>
      </table>
    </div>
  );
}

export default function AccountsPanel({ onBack }) {
  const [accounts, setAccounts] = useState([]);
  const [listError, setListError] = useState("");
  const [offline, setOffline] = useState(false);
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");

  const loadList = useCallback(() => {
    setListError("");
    api.getAccounts().then(({ data, offline: off }) => {
      setAccounts(data || []);
      setOffline(Boolean(off));
    }).catch((e) => setListError(e.message || "加载失败"));
  }, []);

  useEffect(() => {
    loadList();
  }, [loadList]);

  const openDetail = useCallback((skillId) => {
    setSelectedId(skillId);
    setDetail(null);
    setDetailLoading(true);
    setDetailError("");
    setStockNames({});
    api.getAccountDetail(skillId).then((data) => {
      setDetail(normalizeAccountDetail(data));
      setDetailLoading(false);
    }).catch((e) => {
      setDetailError(e.message || "加载失败");
      setDetailLoading(false);
    });
  }, []);

  // 2026-08-12：持仓/交易记录的名称通过批量接口查询（数据层 name 可能落盘为代码）
  const [stockNames, setStockNames] = useState({});
  useEffect(() => {
    if (!detail) return;
    const codes = new Set();
    (detail.positions || []).forEach((p) => p.code && codes.add(p.code));
    (detail.closed_positions || []).forEach((c) => c.code && codes.add(c.code));
    (detail.trades || []).forEach((t) => t.code && codes.add(t.code));
    if (codes.size === 0) return;
    let cancelled = false;
    api.getStockNames([...codes]).then((m) => {
      if (!cancelled) setStockNames(m || {});
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [detail]);

  const backToList = useCallback(() => {
    setSelectedId(null);
    setDetail(null);
    setDetailError("");
    loadList();
  }, [loadList]);

  // 收益率曲线点：snapshots 的 total_assets 相对初始资金
  const curvePoints = useMemo(() => {
    if (!detail) return [];
    const snapshots = detail.snapshots || [];
    return snapshots
      .filter((s) => s.date && s.total_assets != null)
      .map((s) => ({
        date: s.date,
        value: (s.total_assets - INITIAL_CAPITAL) / INITIAL_CAPITAL,
      }));
  }, [detail]);

  const curveOption = useMemo(() => buildReturnCurveOption(curvePoints), [curvePoints]);

  if (selectedId) {
    const rateCls = (v) => ((v || 0) > 0 ? "pos" : (v || 0) < 0 ? "neg" : "zero");
    const kpis = [
      { label: "现金", value: fmtAmount(detail && detail.cash), cls: "zero" },
      { label: "总资产", value: fmtAmount(detail && detail.total_assets), cls: "zero" },
      {
        label: "累计收益率",
        value: fmtPct(detail && detail.total_return_rate, { signed: true }),
        cls: rateCls(detail && detail.total_return_rate),
        sub: "含浮动盈亏",
      },
      {
        label: "浮动盈亏（未实现）",
        value:
          detail && Number.isFinite(detail.unrealized_pnl)
            ? fmtSignedMoney(detail.unrealized_pnl)
            : "—",
        cls: detail && Number.isFinite(detail.unrealized_pnl) ? rateCls(detail.unrealized_pnl) : "zero",
        sub: "当前持仓按市价估值",
      },
      {
        label: "已实现盈亏",
        value:
          detail && Number.isFinite(detail.realized_pnl)
            ? fmtSignedMoney(detail.realized_pnl)
            : "—",
        cls: detail && Number.isFinite(detail.realized_pnl) ? rateCls(detail.realized_pnl) : "zero",
        sub: "卖出成交的盈亏",
      },
      {
        label: "胜率",
        value:
          detail && Number.isFinite(detail.win_rate) ? fmtPct(detail.win_rate, { signed: false }) : "—",
        cls: detail && Number.isFinite(detail.win_rate) ? (detail.win_rate > 0.5 ? "pos" : "zero") : "zero",
        sub:
          detail && Number.isFinite(detail.win_rate)
            ? "盈利卖出笔数占比"
            : "暂无卖出记录",
      },
      {
        label: "盈亏比",
        value:
          detail && Number.isFinite(detail.profit_loss_ratio)
            ? detail.profit_loss_ratio.toFixed(2)
            : "—",
        cls:
          detail && Number.isFinite(detail.profit_loss_ratio)
            ? (detail.profit_loss_ratio >= 1 ? "pos" : "zero")
            : "zero",
        sub:
          detail && Number.isFinite(detail.profit_loss_ratio)
            ? "平均盈利 / 平均亏损"
            : "暂无卖出记录",
      },
      {
        label: "平均持仓天数",
        value:
          detail && Number.isFinite(detail.avg_holding_days)
            ? `${detail.avg_holding_days} 天`
            : "—",
        cls: "zero",
        sub:
          detail && Number.isFinite(detail.avg_holding_days)
            ? "买入到卖出的自然日"
            : "暂无平仓记录",
      },
      {
        label: "持仓数量",
        value: fmtCount(detail && detail.positions ? detail.positions.length : 0),
        cls: "zero",
      },
    ];
    return (
      <div className="grid" style={{ gap: 16 }}>
        <div className="card">
          <div className="accounts-header">
            <button type="button" className="btn btn-outline btn-sm" onClick={backToList}>
              ← 返回账户列表
            </button>
            <div>
              <h3 className="card-title" style={{ marginBottom: 0 }}>
                {(detail && detail.name) || "分析师"} 模拟账户
              </h3>
              <p className="card-sub">
                {detail && detail.skill_id ? `技能 ID：${detail.skill_id}` : ""}
                {detail && detail.updated_at ? ` · 更新：${detail.updated_at}` : ""}
              </p>
            </div>
          </div>
        </div>

        {detailLoading ? (
          <div className="card loading-card">
            <div className="spinner" />
            <div>正在加载账户详情…</div>
          </div>
        ) : detailError ? (
          <div className="card error-panel">
            <div className="error-icon">⚠️</div>
            <div className="error-title">加载失败</div>
            <div className="error-detail">{detailError}</div>
            <div className="error-actions">
              <button className="btn btn-outline" onClick={() => openDetail(selectedId)}>
                重试
              </button>
              <button className="btn btn-accent" onClick={backToList}>
                返回账户列表
              </button>
            </div>
          </div>
        ) : detail ? (
          <>
            <div className="card">
              <h3 className="card-title">账户概览</h3>
              <p className="card-sub">初始资金 100,000 元；比率已 ×100 展示</p>
              <div className="kpi-grid" style={{ marginTop: 10 }}>
                {kpis.map((k) => (
                  <div key={k.label} className="kpi">
                    <div className="kpi-label">{k.label}</div>
                    <div className={`kpi-value ${k.cls}`}>{k.value}</div>
                    {k.sub ? <div className="kpi-sub">{k.sub}</div> : null}
                  </div>
                ))}
              </div>
            </div>

            <div className="card">
              <h3 className="card-title">收益率曲线（近 {curvePoints.length} 日）</h3>
              <p className="card-sub">累计收益率 =（总资产 − 初始资金）/ 初始资金</p>
              <div className="chart-block" style={{ marginTop: 10 }}>
                <Chart option={curveOption} height={300} emptyText="暂无收益率数据" />
              </div>
            </div>

            <div className="card">
              <h3 className="card-title">当前持仓</h3>
              <p className="card-sub">可用数量小于持仓数量表示当日买入部分按 T+1 冻结</p>
              <PositionsTable positions={detail.positions} nameMap={stockNames} />
            </div>

            <div className="card">
              <h3 className="card-title">历史持仓</h3>
              <p className="card-sub">已全部卖出的股票及其完整买卖周期与盈亏（按末卖日倒序）</p>
              <ClosedPositionsTable closedPositions={detail.closed_positions} nameMap={stockNames} />
            </div>

            <div className="card">
              <h3 className="card-title">交易记录</h3>
              <p className="card-sub">默认仅展示成交；拒绝记录（超资金/T+1/涨跌停/整百/裸卖空）可在筛选中查看</p>
              <AccountTradesTable trades={detail.trades} nameMap={stockNames} />
            </div>

            <div className="disclaimer">
              <span>⚠️</span>
              <div>
                <b>免责声明：</b>
                模拟账户为系统规则引擎演示数据，仅供参考，不构成投资建议。市场有风险，入市需谨慎。
              </div>
            </div>
          </>
        ) : null}
      </div>
    );
  }

  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="card">
        <div className="accounts-header">
          <button type="button" className="btn btn-outline btn-sm" onClick={onBack}>
            ← 返回首页
          </button>
          <div>
            <h3 className="card-title" style={{ marginBottom: 0 }}>
              模拟账户
            </h3>
            <p className="card-sub">5 位分析师 · 初始资金各 10 万 · 红涨绿跌</p>
          </div>
          {offline ? <span className="pill offline">离线 mock</span> : null}
        </div>
      </div>

      {listError ? (
        <div className="card error-panel">
          <div className="error-icon">⚠️</div>
          <div className="error-title">加载失败</div>
          <div className="error-detail">{listError}</div>
          <div className="error-actions">
            <button className="btn btn-outline" onClick={loadList}>
              重试
            </button>
          </div>
        </div>
      ) : (
        <div className="card">
          <div className="account-card-grid">
            {accounts.map((a, idx) => (
              <div
                key={a.skill_id}
                className="account-card"
                onClick={() => openDetail(a.skill_id)}
              >
                <div className="account-card-head">
                  <span className={`account-avatar avatar${AVATAR_CLASSES[idx % AVATAR_CLASSES.length] || ""}`}>
                    {(a.name || "?").slice(0, 1)}
                  </span>
                  <span className="account-card-name">{a.name || a.skill_id}</span>
                </div>
                <div className="account-card-kpis">
                  <div className="account-card-kpi">
                    <div className="account-card-kpi-label">总资产</div>
                    <div className="account-card-kpi-value">
                      {a.total_assets != null ? fmtAmount(a.total_assets) : "—"}
                    </div>
                  </div>
                  <div className="account-card-kpi">
                    <div className="account-card-kpi-label">累计收益率</div>
                    <div className={`account-card-kpi-value ${rateCls2(a.total_return_rate)}`}>
                      {a.total_return_rate != null
                        ? fmtPct(a.total_return_rate, { signed: true })
                        : "—"}
                    </div>
                  </div>
                  <div className="account-card-kpi">
                    <div className="account-card-kpi-label">现金</div>
                    <div className="account-card-kpi-value">
                      {a.cash != null ? fmtAmount(a.cash) : "—"}
                    </div>
                  </div>
                  <div className="account-card-kpi">
                    <div className="account-card-kpi-label">浮动盈亏</div>
                    <div className={`account-card-kpi-value ${rateCls2(a.unrealized_pnl)}`}>
                      {fmtSignedMoney(a.unrealized_pnl)}
                    </div>
                  </div>
                  <div className="account-card-kpi">
                    <div className="account-card-kpi-label">已实现盈亏</div>
                    <div className={`account-card-kpi-value ${rateCls2(a.realized_pnl)}`}>
                      {fmtSignedMoney(a.realized_pnl)}
                    </div>
                  </div>
                  <div className="account-card-kpi">
                    <div className="account-card-kpi-label">今日成交</div>
                    <div className="account-card-kpi-value">
                      {fmtCount(a.today_trades_count)}
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
          <div className="disclaimer">
            <span>⚠️</span>
            <div>
              <b>免责声明：</b>
              模拟账户为系统规则引擎演示数据，仅供参考，不构成投资建议。
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function rateCls2(v) {
  return (v || 0) > 0 ? "pos" : (v || 0) < 0 ? "neg" : "zero";
}
