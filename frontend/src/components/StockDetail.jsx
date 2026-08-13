import React, { useEffect, useRef, useState } from "react";
import * as echarts from "echarts";
import * as api from "../api";
import { normalizeStockDetail } from "../normalize";
import { fmtAmount } from "../format";
import { loadSettings, paletteOf, useSettings } from "../settings";

const NEUTRAL = "#adb5bd";
const TEXT2 = "#7a7f87";

const fmtPrice = (v) => (v == null ? "—" : Number(v).toFixed(Number(v) < 1 ? 3 : 2));
const fmtPct2 = (v) => (v == null ? "—" : `${v >= 0 ? "+" : ""}${(v * 100).toFixed(2)}%`);
const fmtNum = (v) => (v == null ? "—" : Number(v).toLocaleString("zh-CN"));

const baseTooltip = {
  backgroundColor: "#fff",
  borderColor: "#e7e8eb",
  textStyle: { color: "#17181d", fontSize: 12 },
  extraCssText: "box-shadow:0 4px 14px rgba(0,0,0,.08);border-radius:8px;",
};

const maSeries = (key, name, color, data) => ({
  name,
  type: "line",
  data,
  smooth: true,
  showSymbol: false,
  lineStyle: { width: 1, color },
  itemStyle: { color },
});

function buildOption(tab, d, { up: UP, down: DOWN }) {
  if (tab === "minute") {
    const pts = d.minute || [];
    const last = pts.length ? Number(pts[pts.length - 1].close) : null;
    const color = last == null ? NEUTRAL : last > 0 ? UP : last < 0 ? DOWN : NEUTRAL;
    return {
      tooltip: { ...baseTooltip, trigger: "axis" },
      grid: { left: 8, right: 12, top: 12, bottom: 8, containLabel: true },
      xAxis: { type: "category", data: pts.map((p) => p.time || ""), axisLine: { lineStyle: { color: "#e3e5e8" } }, axisLabel: { color: TEXT2, fontSize: 10 } },
      yAxis: { type: "value", scale: true, axisLabel: { color: TEXT2, fontSize: 10 } },
      series: [{
        type: "line", data: pts.map((p) => p.close), smooth: false, showSymbol: false,
        lineStyle: { width: 1.4, color },
        areaStyle: { color: { type: "linear", x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: `${color}33` }, { offset: 1, color: `${color}05` }] } },
      }],
    };
  }
  const bars = tab === "daily" ? d.daily : tab === "kline_5m" ? d.kline_5m : d.kline_1m;
  const xKey = tab === "daily" ? "date" : "datetime";
  const xShort = (v) => (tab === "daily" ? String(v || "").slice(5) : String(v || "").slice(5, 16));
  const xs = bars.map((b) => xShort(b[xKey]));
  const candles = bars.map((b) => [b.open, b.close, b.low, b.high]);
  const series = [{
    name: "K线", type: "candlestick", data: candles,
    itemStyle: { color: UP, color0: DOWN, borderColor: UP, borderColor0: DOWN },
  }];
  if (tab === "daily" && bars.length) {
    const closes = bars.map((b) => b.close);
    const ma = (n) => closes.map((_, i) => {
      if (i < n - 1) return null;
      const seg = closes.slice(i - n + 1, i + 1).filter((x) => x != null);
      return seg.length === n ? Number((seg.reduce((a, b) => a + b, 0) / n).toFixed(4)) : null;
    });
    series.push(maSeries("ma5", "MA5", "#f59f00", ma(5)));
    series.push(maSeries("ma10", "MA10", "#3b82f6", ma(10)));
    series.push(maSeries("ma20", "MA20", "#8b5cf6", ma(20)));
  }
  return {
    tooltip: { ...baseTooltip, trigger: "axis", axisPointer: { type: "cross" } },
    legend: tab === "daily" ? { top: 0, textStyle: { color: TEXT2, fontSize: 11 }, data: ["K线", "MA5", "MA10", "MA20"] } : undefined,
    grid: { left: 8, right: 12, top: tab === "daily" ? 28 : 12, bottom: 8, containLabel: true },
    xAxis: { type: "category", data: xs, axisLine: { lineStyle: { color: "#e3e5e8" } }, axisLabel: { color: TEXT2, fontSize: 10 } },
    yAxis: { type: "value", scale: true, axisLabel: { color: TEXT2, fontSize: 10 } },
    series,
  };
}

export default function StockDetail({ query, onBack }) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [detail, setDetail] = useState(null);
  const [name, setName] = useState(query.name || "");
  // R10 F12：首页默认图表周期（分时/日线）来自设置
  const [tab, setTab] = useState(() => {
    try {
      return loadSettings().defaultChartPeriod === "daily" ? "daily" : "minute";
    } catch {
      return "minute";
    }
  });
  const chartRef = useRef(null);
  const chartInst = useRef(null);
  const settings = useSettings();
  const palette = paletteOf(settings.upDownColors);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    (async () => {
      try {
        let code = String(query.code || "").trim();
        let resolvedName = String(query.name || "");
        if (!/^\d{6}$/.test(code)) {
          const matches = await api.searchStocks(code);
          if (!matches.length) throw new Error("未找到该股票，请检查名称或代码");
          code = matches[0].code;
          resolvedName = matches[0].name || resolvedName;
        }
        const raw = await api.getStockDetail(code, 60);
        if (cancelled) return;
        const d = normalizeStockDetail(raw);
        if (!resolvedName && d.quote && d.quote.name) resolvedName = d.quote.name;
        setName(resolvedName);
        setDetail(d);
        // 尊重用户设置的默认周期：所选周期无数据才自动回退
        setTab((prev) => {
          const hasData = (k) =>
            k === "minute"
              ? !!(d.minute && d.minute.length)
              : k === "daily"
                ? !!(d.daily && d.daily.length)
                : k === "kline_5m"
                  ? !!(d.kline_5m && d.kline_5m.length)
                  : !!(d.kline_1m && d.kline_1m.length);
          if (hasData(prev)) return prev;
          if (d.minute && d.minute.length) return "minute";
          if (d.daily && d.daily.length) return "daily";
          return "kline_5m";
        });
      } catch (err) {
        if (!cancelled) setError((err && err.message) || "加载失败，请重试");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [query.code, query.name]);

  useEffect(() => {
    if (!detail || !chartRef.current) return undefined;
    if (!chartInst.current) chartInst.current = echarts.init(chartRef.current);
    chartInst.current.setOption(buildOption(tab, detail, palette), true);
    const onResize = () => chartInst.current && chartInst.current.resize();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [detail, tab, palette]);

  useEffect(
    () => () => {
      if (chartInst.current) {
        chartInst.current.dispose();
        chartInst.current = null;
      }
    },
    []
  );

  const q = detail && detail.quote;
  const pct = q && q.pct_change;
  const pctCls = pct == null ? "zero" : pct > 0 ? "pos" : pct < 0 ? "neg" : "zero";
  const tabs = [
    ["minute", "分时", !!(detail && detail.minute && detail.minute.length)],
    ["daily", "日K", !!(detail && detail.daily && detail.daily.length)],
    ["kline_5m", "5分钟K", !!(detail && detail.kline_5m && detail.kline_5m.length)],
    ["kline_1m", "1分钟K", !!(detail && detail.kline_1m && detail.kline_1m.length)],
  ];

  return (
    <div className="stock-detail">
      <div className="stock-detail-head">
        <button className="btn btn-outline btn-sm" onClick={onBack}>← 返回</button>
        <div className="stock-detail-title">
          {name || "个股详情"} <span className="stock-detail-code">{detail ? detail.code : ""}</span>
          {detail && detail.date ? <span className="stock-detail-date">{detail.date}</span> : null}
        </div>
      </div>

      {loading ? (
        <div className="card loading-card"><div className="spinner" /><div>正在加载个股详情…</div></div>
      ) : error ? (
        <div className="card error-panel">
          <div className="error-icon">⚠️</div>
          <div className="error-title">加载失败</div>
          <div className="error-detail">{error}</div>
          <div className="error-actions"><button className="btn btn-outline" onClick={onBack}>返回</button></div>
        </div>
      ) : detail ? (
        <>
          <div className="card detail-quote">
            <div className="detail-quote-row">
              <div>
                <div className={`detail-price ${pctCls}`}>{q ? fmtPrice(q.price) : "—"}</div>
                <div className={`detail-pct ${pctCls}`}>{q ? fmtPct2(q.pct_change) : "—"}</div>
              </div>
              <div className="detail-meta">
                <div className="detail-meta-grid">
                  <span>今开 {q ? fmtPrice(q.open) : "—"}</span>
                  <span>最高 {q ? fmtPrice(q.high) : "—"}</span>
                  <span>最低 {q ? fmtPrice(q.low) : "—"}</span>
                  <span>昨收 {q ? fmtPrice(q.pre_close) : "—"}</span>
                  <span>成交量 {q ? fmtNum(q.volume) : "—"} 手</span>
                  <span>成交额 {q ? fmtAmount(q.amount) : "—"}</span>
                  <span>换手 {q && q.turnover_rate != null ? `${(q.turnover_rate * 100).toFixed(2)}%` : "—"}</span>
                  <span>来源 {q && q.source ? q.source : "—"}</span>
                </div>
                <div className="detail-ma">
                  MA5 {detail.ma.ma5 == null ? "—" : fmtPrice(detail.ma.ma5)} · MA10 {detail.ma.ma10 == null ? "—" : fmtPrice(detail.ma.ma10)} · MA20 {detail.ma.ma20 == null ? "—" : fmtPrice(detail.ma.ma20)}
                </div>
              </div>
            </div>
            <div className="detail-levels">
              <div className="detail-levels-col">
                {[...(q && q.asks ? q.asks : [])].reverse().map((l, i) => (
                  <div key={`a${i}`} className="detail-level neg">
                    <span>卖{5 - i}</span><span>{fmtPrice(l.price)}</span><span>{l.volume == null ? "—" : `${fmtNum(l.volume)} 手`}</span>
                  </div>
                ))}
              </div>
              <div className="detail-levels-col">
                {(q && q.bids ? q.bids : []).map((l, i) => (
                  <div key={`b${i}`} className="detail-level pos">
                    <span>买{i + 1}</span><span>{fmtPrice(l.price)}</span><span>{l.volume == null ? "—" : `${fmtNum(l.volume)} 手`}</span>
                  </div>
                ))}
              </div>
            </div>
            {detail.isDegraded ? (
              <div className="detail-degraded">
                降级说明：{detail.degraded.join("；")}
              </div>
            ) : null}
          </div>

          <div className="card detail-chart-card">
            <div className="detail-tabs">
              {tabs.map(([key, label, ok]) => (
                <button
                  key={key}
                  className={`seg-btn${tab === key ? " active" : ""}`}
                  disabled={!ok}
                  title={ok ? "" : "该数据源当前不可用"}
                  onClick={() => setTab(key)}
                >
                  {label}{!ok ? "（无数据）" : ""}
                </button>
              ))}
            </div>
            <div className="detail-chart" ref={chartRef} />
          </div>

          <div className="chat-disclaimer">
            <span>⚠️</span>
            <span>免责声明：仅供参考，不构成投资建议。市场有风险，入市需谨慎。</span>
          </div>
        </>
      ) : null}
    </div>
  );
}
