import React, { useEffect, useMemo, useRef } from "react";
import * as echarts from "echarts";
import { fmtCount, fmtPct, num } from "../format";
import { paletteOf, useSettings } from "../settings";

// R12：图表主色与全局强调色（A股复盘红）保持一致
const ACCENT = "#c8423c";
// 涨跌配色跟随设置（红涨绿跌默认 / 绿涨红跌可切换）；零中性灰固定
const NEUTRAL = "#adb5bd";
const TEXT2 = "#7a7f87";

const baseTooltip = {
  backgroundColor: "#fff",
  borderColor: "#e7e8eb",
  textStyle: { color: "#17181d", fontSize: 12 },
  extraCssText: "box-shadow:0 4px 14px rgba(0,0,0,.08);border-radius:8px;",
};

export function Chart({ option, height = 320, emptyText = "暂无数据" }) {
  const ref = useRef(null);
  const inst = useRef(null);

  useEffect(() => {
    if (!ref.current) return undefined;
    const chart = echarts.init(ref.current);
    inst.current = chart;
    let ro = null;
    if (typeof ResizeObserver !== "undefined") {
      ro = new ResizeObserver(() => chart.resize());
      ro.observe(ref.current);
    }
    return () => {
      if (ro) ro.disconnect();
      chart.dispose();
      inst.current = null;
    };
  }, []);

  useEffect(() => {
    if (inst.current && option) inst.current.setOption(option, true);
  }, [option]);

  if (!option || !option.series || !option.series.length) {
    return <div className="chart-empty" style={{ height }}>{emptyText}</div>;
  }
  return <div ref={ref} style={{ width: "100%", height }} />;
}

// 指数分时（中文 tooltip，含较开盘涨跌）
export function IndexMinuteChart({ indices }) {
  const palette = paletteOf(useSettings().upDownColors);
  const rows = useMemo(
    () =>
      (Array.isArray(indices) ? indices : [])
        .map((ix) => ({
          name: (ix && ix.name) || "指数",
          points: (Array.isArray(ix && ix.points) ? ix.points : [])
            .map((p) => ({ time: (p && p.time) || "", value: num(p && p.value, NaN) }))
            .filter((p) => p.time && Number.isFinite(p.value)),
        }))
        .filter((ix) => ix.points.length > 0),
    [indices]
  );
  const option = useMemo(() => {
    if (!rows.length) return { series: [] };
    const UP_RED = palette.up;
    const DOWN_GREEN = palette.down;
    const times = rows[0].points.map((p) => p.time);
    return {
      tooltip: {
        ...baseTooltip,
        trigger: "axis",
        formatter(params) {
          const i = params[0].dataIndex;
          let html = `<b>${times[i]}</b>`;
          params.forEach((pp, k) => {
            const r = rows[k];
            const v = r.points[i].value;
            const first = r.points[0].value;
            const chg = first ? (v - first) / first : null;
            html += `<br/>${r.name}：<b>${v.toFixed(2)}</b> 点`;
            if (chg != null) {
              html += `（<span style="color:${chg >= 0 ? UP_RED : DOWN_GREEN}">${chg >= 0 ? "+" : ""}${(chg * 100).toFixed(2)}%</span>）`;
            }
          });
          return html;
        },
      },
      legend: { data: rows.map((r) => r.name), top: 4, textStyle: { color: TEXT2, fontSize: 12 } },
      grid: { left: 64, right: 20, top: 40, bottom: 30 },
      xAxis: {
        type: "category",
        data: times,
        boundaryGap: false,
        axisLine: { lineStyle: { color: "#d8dad5" } },
        axisLabel: { color: TEXT2, fontSize: 11 },
      },
      yAxis: {
        type: "value",
        scale: true,
        name: "点位",
        nameTextStyle: { color: TEXT2, fontSize: 11 },
        axisLabel: { color: TEXT2, fontSize: 11 },
        splitLine: { lineStyle: { color: "#eef0ec" } },
      },
      series: rows.map((r, k) => ({
        name: r.name,
        type: "line",
        smooth: 0.3,
        symbol: "circle",
        symbolSize: 4,
        data: r.points.map((p) => Math.round(p.value * 100) / 100),
        lineStyle: { width: 2, color: k === 0 ? ACCENT : "#a93226" },
        itemStyle: { color: k === 0 ? ACCENT : "#a93226" },
        areaStyle:
          k === 0
            ? {
                color: {
                  type: "linear",
                  x: 0, y: 0, x2: 0, y2: 1,
                  colorStops: [
                    { offset: 0, color: "rgba(16,163,127,.18)" },
                    { offset: 1, color: "rgba(16,163,127,0)" },
                  ],
                },
              }
            : undefined,
      })),
    };
  }, [rows, palette]);
  return <Chart option={option} height={320} emptyText="暂无指数分时数据" />;
}

const LADDER_ORDER = ["首板", "2板", "3板", "4板", "5板+"];

// 涨跌停梯队（红涨绿跌）
export function LimitLadderChart({ ladder }) {
  const palette = paletteOf(useSettings().upDownColors);
  const rows = useMemo(() => {
    const up = Array.isArray(ladder && ladder.up) ? ladder.up : [];
    const down = Array.isArray(ladder && ladder.down) ? ladder.down : [];
    const labels = LADDER_ORDER.filter(
      (l) => up.some((x) => x.label === l) || down.some((x) => x.label === l)
    );
    const at = (list, label) => {
      const x = list.find((v) => v.label === label);
      return x && x.count != null ? num(x.count) : null;
    };
    const stocksOf = (label) => {
      const upRow = up.find((x) => x.label === label);
      const downRow = down.find((x) => x.label === label);
      const list = (upRow && upRow.stocks) || (downRow && downRow.stocks) || [];
      return (Array.isArray(list) ? list : []).filter(Boolean).slice(0, 5);
    };
    return {
      labels,
      up: labels.map((l) => at(up, l)),
      down: labels.map((l) => at(down, l)),
      // P1-L：龙头名单（如首板）已取数但 tooltip 未展示，这里透传给 tooltip
      stocks: labels.map(stocksOf),
    };
  }, [ladder]);
  const option = useMemo(() => {
    if (!rows.labels.length) return { series: [] };
    const UP_RED = palette.up;
    const DOWN_GREEN = palette.down;
    return {
      tooltip: {
        ...baseTooltip,
        trigger: "axis",
        formatter(params) {
          const i = params[0].dataIndex;
          const label = rows.labels[i];
          let html = `<b>${label}</b>`;
          params.forEach((pp) => {
            html += `<br/>${pp.seriesName}：<b>${pp.value == null ? "—" : pp.value}</b> 家`;
          });
          const stocks = rows.stocks[i];
          html += `<br/>${label}个股：<b>${stocks.length ? stocks.join("、") : "—"}</b>`;
          return html;
        },
      },
      legend: { data: ["涨停家数", "跌停家数"], top: 4, textStyle: { color: TEXT2, fontSize: 12 } },
      grid: { left: 48, right: 20, top: 40, bottom: 30 },
      xAxis: {
        type: "category",
        data: rows.labels,
        axisLine: { lineStyle: { color: "#d8dad5" } },
        axisLabel: { color: TEXT2, fontSize: 11 },
      },
      yAxis: {
        type: "value",
        minInterval: 1,
        axisLabel: { color: TEXT2 },
        splitLine: { lineStyle: { color: "#eef0ec" } },
      },
      series: [
        {
          name: "涨停家数",
          type: "bar",
          barMaxWidth: 24,
          data: rows.up.map((v) => ({ value: v, itemStyle: { color: UP_RED, borderRadius: [3, 3, 0, 0] } })),
        },
        {
          name: "跌停家数",
          type: "bar",
          barMaxWidth: 24,
          data: rows.down.map((v) => ({ value: v, itemStyle: { color: DOWN_GREEN, borderRadius: [3, 3, 0, 0] } })),
        },
      ],
    };
  }, [rows, palette]);
  return <Chart option={option} height={300} emptyText="暂无涨跌停梯队数据" />;
}

// 板块涨幅榜（红涨绿跌，中文 tooltip）
export function SectorChart({ sectors }) {
  const palette = paletteOf(useSettings().upDownColors);
  const rows = useMemo(
    () =>
      (Array.isArray(sectors) ? sectors : [])
        .map((s) => ({
          name: (s && s.name) || "—",
          pct: num(s && s.pct_change, NaN),
          leader: (s && s.leader) || "",
        }))
        .filter((s) => Number.isFinite(s.pct))
        .sort((a, b) => b.pct - a.pct)
        .slice(0, 12),
    [sectors]
  );
  const option = useMemo(() => {
    if (!rows.length) return { series: [] };
    const UP_RED = palette.up;
    const DOWN_GREEN = palette.down;
    const names = rows.map((r) => r.name).reverse();
    return {
      tooltip: {
        ...baseTooltip,
        trigger: "item",
        formatter(params) {
          const r = rows[rows.length - 1 - params.dataIndex];
          const color = r.pct > 0 ? UP_RED : r.pct < 0 ? DOWN_GREEN : NEUTRAL;
          return `<b>${r.name}</b><br/>涨幅：<b style="color:${color}">${r.pct >= 0 ? "+" : ""}${(r.pct * 100).toFixed(2)}%</b><br/>领涨股：${r.leader || "—"}`;
        },
      },
      grid: { left: 90, right: 46, top: 18, bottom: 28 },
      xAxis: {
        type: "value",
        axisLabel: { color: TEXT2, formatter: (v) => `${v}%` },
        splitLine: { lineStyle: { color: "#eef0ec" } },
      },
      yAxis: {
        type: "category",
        data: names,
        axisLine: { lineStyle: { color: "#d8dad5" } },
        axisLabel: { color: TEXT2, fontSize: 11 },
      },
      series: [
        {
          name: "板块涨幅",
          type: "bar",
          barMaxWidth: 16,
          label: {
            show: true,
            position: "right",
            formatter: (p) => {
              const r = rows[rows.length - 1 - p.dataIndex];
              return r ? `${r.pct >= 0 ? "+" : ""}${(r.pct * 100).toFixed(2)}%` : "";
            },
            color: TEXT2,
            fontSize: 10.5,
          },
          data: rows
            .map((r) => Math.round(r.pct * 10000) / 100)
            .reverse()
            .map((v, i) => {
              const r = rows[rows.length - 1 - i];
              return {
                value: v,
                itemStyle: {
                  color: r.pct > 0 ? UP_RED : r.pct < 0 ? DOWN_GREEN : NEUTRAL,
                  borderRadius: [0, 3, 3, 0],
                },
              };
            }),
        },
      ],
    };
  }, [rows, palette]);
  return <Chart option={option} height={300} emptyText="暂无板块数据" />;
}

const SENTIMENT_ITEMS = [
  // P1-J：比率类（0-1 区间）用中性蓝，不参与红涨绿跌；方向性指标（avg_return）才用红绿。
  // break_rate/button_rate 为负向指标，tooltip 注明「比率越高越负面」。
  { key: "red_rate", label: "红盘率", ratio: true },
  { key: "continue_rate", label: "连板率", ratio: true },
  { key: "break_rate", label: "炸板率", ratio: true, negative: true },
  { key: "button_rate", label: "核按钮率", ratio: true, negative: true },
  { key: "avg_return", label: "昨日涨停平均收益" },
];

// 情绪指标（比率 ×100 展示，None → —）
export function SentimentChart({ sentiment }) {
  const palette = paletteOf(useSettings().upDownColors);
  const rows = useMemo(
    () =>
      SENTIMENT_ITEMS.map((it) => ({
        label: it.label,
        value: sentiment ? num(sentiment[it.key], NaN) : NaN,
        ratio: !!it.ratio,
        negative: !!it.negative,
      })),
    [sentiment]
  );
  // 涨跌家数比是比值（如 1.56 : 1），不进百分比柱，单独文本展示
  const udr = sentiment ? num(sentiment.up_down_ratio, NaN) : NaN;
  const option = useMemo(() => {
    if (!rows.some((r) => Number.isFinite(r.value))) return { series: [] };
    const UP_RED = palette.up;
    const DOWN_GREEN = palette.down;
    const RATIO_BLUE = "#5c7cfa"; // 比率类中性蓝（不暗示涨跌方向）
    return {
      tooltip: {
        ...baseTooltip,
        trigger: "axis",
        formatter(params) {
          const r = rows[params[0].dataIndex];
          const v = r.value;
          if (!Number.isFinite(v)) return `<b>${r.label}</b><br/>数值：—`;
          const color = r.ratio ? RATIO_BLUE : v > 0 ? UP_RED : v < 0 ? DOWN_GREEN : NEUTRAL;
          const note = r.negative ? "<br/>（比率越高越负面）" : "";
          return `<b>${r.label}</b><br/>数值：<b style="color:${color}">${(v * 100).toFixed(2)}%</b>${note}`;
        },
      },
      grid: { left: 120, right: 46, top: 18, bottom: 28 },
      xAxis: {
        type: "value",
        axisLabel: { color: TEXT2, formatter: (v) => `${v}%` },
        splitLine: { lineStyle: { color: "#eef0ec" } },
      },
      yAxis: {
        type: "category",
        data: rows.map((r) => r.label),
        axisLine: { lineStyle: { color: "#d8dad5" } },
        axisLabel: { color: TEXT2, fontSize: 11 },
      },
      series: [
        {
          name: "情绪指标",
          type: "bar",
          barMaxWidth: 16,
          label: {
            show: true,
            position: "right",
            formatter: (p) => {
              const r = rows[p.dataIndex];
              return Number.isFinite(r.value) ? `${(r.value * 100).toFixed(2)}%` : "—";
            },
            color: TEXT2,
            fontSize: 10.5,
          },
          data: rows.map((r) => {
            if (!Number.isFinite(r.value)) return { value: null };
            const v = Math.round(r.value * 10000) / 100;
            const color = r.ratio ? RATIO_BLUE : r.value > 0 ? UP_RED : r.value < 0 ? DOWN_GREEN : NEUTRAL;
            return { value: v, itemStyle: { color, borderRadius: [0, 3, 3, 0] } };
          }),
        },
      ],
    };
  }, [rows, palette]);
  return (
    <div>
      {Number.isFinite(udr) ? (
        <div className="chart-sub" style={{ marginBottom: 8 }}>
          涨跌家数比：<b>{udr.toFixed(2)} : 1</b>
        </div>
      ) : null}
      <Chart option={option} height={300} emptyText="暂无情绪指标数据" />
    </div>
  );
}

// 改动2：全市场涨跌分布 BreadthChart 已移除（ReportView 不再引用该图，删除导出）

// 情绪 KPI 小卡（计数类：None → —）
export function SentimentKpis({ sentiment }) {
  const s = sentiment || {};
  const items = [
    { label: "上涨家数", value: fmtCount(s.up_count), cls: "pos" },
    { label: "下跌家数", value: fmtCount(s.down_count), cls: "neg" },
    { label: "涨停家数", value: fmtCount(s.limit_up_count), cls: "pos" },
    { label: "跌停家数", value: fmtCount(s.limit_down_count), cls: "neg" },
    { label: "炸板率", value: fmtPct(s.break_rate), cls: "zero" },
    { label: "昨日涨停平均收益", value: fmtPct(s.avg_return, { signed: true }), cls: "zero" },
  ];
  return (
    <div className="kpi-grid">
      {items.map((it) => (
        <div key={it.label} className="kpi">
          <div className="kpi-label">{it.label}</div>
          <div className={`kpi-value ${it.cls}`}>{it.value}</div>
        </div>
      ))}
    </div>
  );
}
