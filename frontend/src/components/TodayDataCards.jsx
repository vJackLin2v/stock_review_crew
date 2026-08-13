// R10 今日数据扩展卡片（requirements-r10 §二/§三）：
//   F3 市场天气/情绪温度（P0）、F6 纳斯达克（P0）、F5 板块温度榜（P1）、
//   F2 额外指数（P2）、F4 风格坐标（P2）
// 展示口径：比率小数 ×100；None → 「—」；全中文；API 失败/降级 → 中文「数据缺失/降级」态，禁止白屏。
import React, { useCallback, useEffect, useMemo, useState } from "react";
import * as api from "../api";
import {
  normalizeExtraIndices,
  normalizeMarketStyle,
  normalizeMarketSentiment,
  normalizeMarketWeather,
  normalizeSectorHeat,
  normalizeUsNasdaq,
} from "../normalize";
import { fmtSignedPct, num, pctClass } from "../format";
import { paletteOf, useSettings } from "../settings";
import { Chart } from "./Charts";

const TEXT2 = "#7a7f87";

const baseTooltip = {
  backgroundColor: "#fff",
  borderColor: "#e7e8eb",
  textStyle: { color: "#17181d", fontSize: 11 },
  extraCssText: "box-shadow:0 4px 14px rgba(0,0,0,.08);border-radius:8px;",
};

const fmtPrice2 = (v) => {
  const n = num(v, NaN);
  return Number.isFinite(n) ? n.toFixed(2) : "—";
};

// ---- 通用卡片数据 hook：加载/错误/降级，全部中文态 ----
function useCardData(fetcher, normalizer) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const raw = await fetcher();
      const out = normalizer(raw == null ? {} : raw);
      setData(out == null ? {} : out);
    } catch (err) {
      setData(null);
      setError((err && err.message) || "数据加载失败");
    } finally {
      setLoading(false);
    }
  }, [fetcher, normalizer]);
  useEffect(() => {
    load();
  }, [load]);
  return { data, loading, error, reload: load };
}

// 卡片主体状态：加载中 / 失败（可重试）/ 空
function CardState({ loading, error, onRetry, empty, children }) {
  if (loading) {
    return (
      <div className="card-state">
        <span className="spinner card-state-spinner" />
        正在加载…
      </div>
    );
  }
  if (error) {
    return (
      <div className="card-state card-state-error">
        数据缺失/降级：{error}
        <button type="button" className="btn btn-ghost btn-xs" onClick={onRetry}>
          重试
        </button>
      </div>
    );
  }
  if (empty) {
    return <div className="card-state">数据缺失/降级：暂无数据</div>;
  }
  return children;
}

// R3 去噪（R12 补全）：降级/来源提示在首页数据卡一律不展示（缺失显示 —，
// 来源信息保留在各卡 tooltip）；本组件保留签名兼容，恒返回 null。
function DegradeNote({ degraded, reason }) {
  return null;
}

// ---- 情绪温度环（SVG，0-100）----
export function scoreColor(score) {
  if (score >= 80) return "#e03131";
  if (score >= 65) return "#f08c00";
  if (score >= 45) return "#fab005";
  if (score >= 30) return "#339af0";
  return "#1c7ed6";
}

export function EmotionRing({ score }) {
  const R = 44;
  const C = 2 * Math.PI * R;
  const valid = Number.isFinite(num(score, NaN));
  const pct = valid ? Math.max(0, Math.min(100, Number(score))) / 100 : 0;
  const color = valid ? scoreColor(Number(score)) : "#adb5bd";
  return (
    <svg width="118" height="118" viewBox="0 0 118 118" className="emotion-ring" aria-label="情绪温度环">
      <circle cx="59" cy="59" r={R} fill="none" stroke="var(--border)" strokeWidth="10" />
      <circle
        cx="59"
        cy="59"
        r={R}
        fill="none"
        stroke={color}
        strokeWidth="10"
        strokeLinecap="round"
        strokeDasharray={`${(C * pct).toFixed(1)} ${C.toFixed(1)}`}
        transform="rotate(-90 59 59)"
        style={{ transition: "stroke-dasharray .4s ease" }}
      />
      <text x="59" y="57" textAnchor="middle" className="emotion-score">
        {valid ? Math.round(Number(score)) : "—"}
      </text>
      <text x="59" y="77" textAnchor="middle" className="emotion-unit">
        情绪温度
      </text>
    </svg>
  );
}

const WEATHER_ICON = {
  晴: "☀️",
  晴间多云: "⛅",
  多云: "☁️",
  偏冷有雨: "🌧️",
  数据缺失: "⚠️",
};

// ---- F3 市场天气/情绪温度（P0）----
export function WeatherCard() {
  const { data, loading, error, reload } = useCardData(
    api.getMarketSentiment,
    normalizeMarketSentiment
  );
  const score = data ? num(data.emotion_score, NaN) : NaN;
  const valid = Number.isFinite(score);
  return (
    <div className="card">
      <h3 className="card-title">市场天气</h3>
      <p
        className="card-sub"
        title={data && data.source ? `数据来源：${data.source}` : undefined}
      >
        情绪温度 · {data && data.date ? data.date : "—"}
      </p>
      <CardState loading={loading} error={error} onRetry={reload} empty={!data}>
        {data ? (
          <>
            <div className="weather-head">
              <div className="weather-big">
                <span className="weather-emoji">{WEATHER_ICON[data.weather] || "🌤️"}</span>
                {data.weather || "数据缺失"}
              </div>
              <EmotionRing score={score} />
            </div>
            <div
              className="weather-label"
              style={valid ? { color: scoreColor(score) } : undefined}
            >
              {valid
                ? `情绪温度 ${Math.round(score)} 分 · ${data.emotion_label || "—"}`
                : `情绪温度：— · ${data.emotion_label || "—"}`}
            </div>
            {/* R12 统一情绪数据源：结构化指标（与复盘同源），缺失显示 — */}
            <div className="weather-metrics">
              <span className="tag-chip light" title="涨跌家数比（上涨/下跌）">
                涨跌比{" "}
                <b>{data.up_down_ratio != null ? `${(data.up_down_ratio * 100).toFixed(1)}%` : "—"}</b>
              </span>
              <span className="tag-chip light">
                涨停{" "}
                <b className="pos">{Number.isFinite(data.limit_up_count) ? data.limit_up_count : "—"}</b>
              </span>
              <span className="tag-chip light">
                跌停{" "}
                <b className="neg">{Number.isFinite(data.limit_down_count) ? data.limit_down_count : "—"}</b>
              </span>
              <span className="tag-chip light" title="炸板率：比率越高越负面">
                炸板率{" "}
                <b>{data.break_rate != null ? `${(data.break_rate * 100).toFixed(1)}%` : "—"}</b>
              </span>
              <span className="tag-chip light">
                连板高度{" "}
                <b>{data.lianban_height ?? "—"}</b>
              </span>
              <span className="tag-chip light" title="昨日涨停今日红盘率">
                红盘率{" "}
                <b>{data.red_rate != null ? `${(data.red_rate * 100).toFixed(1)}%` : "—"}</b>
              </span>
            </div>
            {data.reasons && data.reasons.length ? (
              <div className="weather-reasons">
                <div className="rt-col-title">评分依据</div>
                {data.reasons.map((r, i) => (
                  <div key={i} className="weather-reason">
                    {r}
                  </div>
                ))}
              </div>
            ) : null}
          </>
        ) : null}
      </CardState>
    </div>
  );
}

// ---- F6 纳斯达克（P0）：.IXIC/.NDX + 近 60 交易日 mini 走势 ----
export function NasdaqCard() {
  const { data, loading, error, reload } = useCardData(api.getUsNasdaq, normalizeUsNasdaq);
  const settings = useSettings();
  const palette = paletteOf(settings.upDownColors);
  const option = useMemo(() => {
    if (!data || !data.items || !data.items.length) return { series: [] };
    const withPts = data.items.filter((it) => it.points && it.points.length);
    if (!withPts.length) return { series: [] };
    return {
      tooltip: {
        ...baseTooltip,
        trigger: "axis",
        formatter(params) {
          const i = params[0].dataIndex;
          let html = `<b>${withPts[0].points[i].date}</b>`;
          params.forEach((pp, k) => {
            const r = withPts[k];
            const v = r.points[i].close;
            if (v == null) return;
            const first = r.points[0].close;
            const chg = first ? (v - first) / first : null;
            html += `<br/>${r.name}：<b>${Number(v).toFixed(2)}</b> 点`;
            if (chg != null) {
              html += `（<span style="color:${chg >= 0 ? palette.up : palette.down}">${
                chg >= 0 ? "+" : ""
              }${(chg * 100).toFixed(2)}%</span>）`;
            }
          });
          return html;
        },
      },
      legend: {
        data: withPts.map((it) => it.name),
        top: 0,
        textStyle: { color: TEXT2, fontSize: 10 },
      },
      grid: { left: 48, right: 10, top: 22, bottom: 16 },
      xAxis: {
        type: "category",
        data: withPts[0].points.map((p) => p.date),
        boundaryGap: false,
        axisLine: { lineStyle: { color: "#d8dad5" } },
        axisLabel: { show: false },
        axisTick: { show: false },
      },
      yAxis: {
        type: "value",
        scale: true,
        axisLabel: { color: TEXT2, fontSize: 9.5 },
        splitLine: { lineStyle: { color: "#eef0ec" } },
      },
      series: withPts.map((it, k) => ({
        name: it.name,
        type: "line",
        showSymbol: false,
        smooth: 0.25,
        lineStyle: { width: 1.5, color: k === 0 ? palette.up : "#6d5bd0" },
        itemStyle: { color: k === 0 ? palette.up : "#6d5bd0" },
        data: it.points.map((p) => p.close),
      })),
    };
  }, [data, palette]);
  return (
    <div className="card">
      <h3 className="card-title">纳斯达克</h3>
      <p className="card-sub">
        最新价/涨跌幅 · 近 60 交易日走势
        {data && data.source ? ` · ${data.source}` : ""}
      </p>
      <CardState loading={loading} error={error} onRetry={reload} empty={!data}>
        {data ? (
          <>
            {data.items.map((it) => (
              <div key={it.symbol || it.name} className="nasdaq-item">
                <div className="nasdaq-id">
                  <span className="nasdaq-name">{it.name || "—"}</span>
                  <span className="nasdaq-symbol">{it.symbol}</span>
                  {it.is_today === false ? <span className="nasdaq-badge">非当日</span> : null}
                </div>
                <div className="nasdaq-quote">
                  <span className={`nasdaq-price ${pctClass(it.pct_change)}`}>
                    {fmtPrice2(it.close)}
                  </span>
                  <span className={`nasdaq-pct ${pctClass(it.pct_change)}`}>
                    {fmtSignedPct(it.pct_change)}
                  </span>
                </div>
              </div>
            ))}
            <Chart option={option} height={150} emptyText="暂无走势数据" />
            <DegradeNote
              degraded={data.degraded}
              reason={(data.degraded_reason || []).join("；")}
            />
          </>
        ) : null}
      </CardState>
    </div>
  );
}

// ---- F5 板块温度榜（P1）：hot/cold 左右对照 ----
export function SectorHeatCard() {
  const { data, loading, error, reload } = useCardData(api.getSectorHeat, normalizeSectorHeat);
  const col = (list, hot) => (
    <div className={`sh-col ${hot ? "hot" : "cold"}`}>
      <div className="sh-col-title">{hot ? "热门板块 · 涨幅榜" : "冰点板块 · 跌幅榜"}</div>
      {list.length === 0 ? (
        <div className="card-state">数据缺失/降级：暂无数据</div>
      ) : (
        list.map((s, i) => (
          <div key={`${s.name}-${i}`} className="sh-row">
            <div className="sh-row-top">
              <span className="sh-rank">{i + 1}</span>
              <span className="sh-name" title={s.name}>{s.name}</span>
              <span className={`sh-pct ${pctClass(s.pct_change)}`}>
                {fmtSignedPct(s.pct_change)}
              </span>
            </div>
            <div className="sh-leader">领涨 {s.leader || "—"}</div>
          </div>
        ))
      )}
    </div>
  );
  return (
    <div className="card">
      <h3 className="card-title">板块温度榜</h3>
      <p className="card-sub">
        涨幅榜 / 跌幅榜 Top5（不足 5 条按实际）
        {data && data.source ? ` · ${data.source}` : ""}
      </p>
      <CardState loading={loading} error={error} onRetry={reload} empty={!data}>
        {data ? (
          <>
            <div className="sh-grid">{col(data.hot || [], true)}{col(data.cold || [], false)}</div>
            <DegradeNote
              degraded={data.degraded}
              reason={(data.degraded_reason || []).join("；")}
            />
          </>
        ) : null}
      </CardState>
    </div>
  );
}

// ---- F2 额外指数（P2）：指数卡下方横条（平均股价/微盘股/高股息）----
export function ExtraIndicesStrip() {
  const { data, loading, error, reload } = useCardData(
    api.getExtraIndices,
    normalizeExtraIndices
  );
  if (loading) {
    return (
      <div className="card-state" style={{ marginTop: 12 }}>
        <span className="spinner card-state-spinner" />
        额外指数加载中…
      </div>
    );
  }
  if (error || !data || !data.items || !data.items.length) {
    return (
      <div className="card-state card-state-error" style={{ marginTop: 12 }}>
        额外指数：数据缺失/降级（{error || "暂无数据"}）
        <button type="button" className="btn btn-ghost btn-xs" onClick={reload}>
          重试
        </button>
      </div>
    );
  }
  return (
    <div className="xi-strip">
      {data.items.map((it) => (
        <div key={it.code || it.name} className="xi-cell">
          <div className="xi-name">
            {it.name || "—"}
            {it.code ? <span className="nasdaq-symbol">{it.code}</span> : null}
          </div>
          <div className={`xi-close ${pctClass(it.pct_change)}`}>{fmtPrice2(it.close)}</div>
          <div className={`xi-pct ${pctClass(it.pct_change)}`}>
            {fmtSignedPct(it.pct_change)}
          </div>
        </div>
      ))}
      {data.degraded ? (
        <DegradeNote
          degraded={data.degraded}
          reason={(data.degraded_reason || []).join("；")}
        />
      ) : null}
    </div>
  );
}

// ---- F4 风格坐标（P2）：两维度横条 + 标签 + 说明 ----
export function StyleCard() {
  const { data, loading, error, reload } = useCardData(api.getMarketStyle, normalizeMarketStyle);
  const dim = (title, left, right, d) => {
    const valid = d && Number.isFinite(num(d.score, NaN));
    const score = valid ? Math.max(0, Math.min(100, Number(d.score))) : null;
    return (
      <div className="style-dim">
        <div className="style-dim-title">{title}</div>
        <div className="style-bar">
          {valid ? <div className="style-marker" style={{ left: `${score}%` }} /> : null}
        </div>
        <div className="style-axis">
          <span>{left}</span>
          <span>{right}</span>
        </div>
        <div className="style-meta">
          <span className={`style-label ${valid ? "" : "zero"}`}>
            {d && d.label ? d.label : "数据缺失"}
          </span>
          <span className={`style-score ${valid ? "" : "zero"}`}>
            {valid ? score : "—"}
          </span>
        </div>
        <div className="style-desc">
          {d && d.desc ? d.desc : "该维度数据缺失，暂无法评估"}
        </div>
      </div>
    );
  };
  return (
    <div className="card">
      <h3 className="card-title">风格坐标</h3>
      <p className="card-sub">
        大盘 vs 小盘 · 价值 vs 成长
        {data && data.source ? ` · ${data.source}` : ""}
      </p>
      <CardState loading={loading} error={error} onRetry={reload} empty={!data}>
        {data ? (
          <>
            <div className="style-grid">
              {dim("大盘 vs 小盘", "小盘占优", "大盘占优", data.large_vs_small)}
              {dim("价值 vs 成长", "成长占优", "价值占优", data.value_vs_growth)}
            </div>
            <DegradeNote
              degraded={data.degraded}
              reason={(data.degraded_reason || []).join("；")}
            />
          </>
        ) : null}
      </CardState>
    </div>
  );
}

export default { WeatherCard, NasdaqCard, SectorHeatCard, ExtraIndicesStrip, StyleCard };
