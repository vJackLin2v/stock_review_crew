import React, { useCallback, useEffect, useRef, useState } from "react";
import * as api from "../api";
import {
  ExtraIndicesStrip,
  NasdaqCard,
  SectorHeatCard,
  StyleCard,
  WeatherCard,
} from "./TodayDataCards";
import WatchlistPanel from "./WatchlistPanel";

// R11-R3：数据卡不再渲染降级横幅/来源文本（来源信息保留在 title tooltip），无数据显示「—」
const finiteNum = (v) => (v == null || v === "" ? NaN : Number(v));
const fmtPrice2 = (v) => {
  const n = finiteNum(v);
  return Number.isFinite(n) ? n.toFixed(2) : "—";
};
const fmtSignedPct2 = (v) => {
  const n = finiteNum(v);
  return Number.isFinite(n) ? `${n >= 0 ? "+" : ""}${(n * 100).toFixed(2)}%` : "—";
};
const fmtPct2 = (v) => {
  const n = finiteNum(v);
  return Number.isFinite(n) ? `${(n * 100).toFixed(2)}%` : "—";
};
const fmtYi = (v) => {
  const n = finiteNum(v);
  return Number.isFinite(n) ? `${(n / 1e8).toFixed(1)} 亿` : "—";
};

export default function DataTab({ onOpenStockDetail }) {
  const [rtData, setRtData] = useState(null);
  const [rtLoading, setRtLoading] = useState(false);
  const [rtError, setRtError] = useState("");
  const [rtAuto, setRtAuto] = useState(false);
  const rtTimer = useRef(null);
  const [quoteCode, setQuoteCode] = useState("");
  const [quote, setQuote] = useState(null);
  const [quoteLoading, setQuoteLoading] = useState(false);
  const [quoteError, setQuoteError] = useState("");
  const [quoteMatches, setQuoteMatches] = useState([]);

  const loadRealtime = useCallback(async () => {
    setRtLoading(true);
    setRtError("");
    try {
      const d = await api.getRealtimeData();
      setRtData(d);
    } catch (err) {
      setRtError((err && err.message) || "实时数据加载失败");
    } finally {
      setRtLoading(false);
    }
  }, []);

  useEffect(() => {
    loadRealtime();
    return () => {
      if (rtTimer.current) clearInterval(rtTimer.current);
    };
  }, [loadRealtime]);

  useEffect(() => {
    if (rtTimer.current) clearInterval(rtTimer.current);
    if (rtAuto) {
      rtTimer.current = setInterval(loadRealtime, 30000);
    }
    return () => {
      if (rtTimer.current) clearInterval(rtTimer.current);
    };
  }, [rtAuto, loadRealtime]);

  const loadQuote = useCallback(async (code) => {
    const c = String(code || "").trim();
    if (!/^\d{6}$/.test(c)) {
      setQuoteError("请输入 6 位股票代码，或先搜索股票名称选择代码");
      return;
    }
    setQuoteLoading(true);
    setQuoteError("");
    try {
      const q = await api.getStockQuote(c);
      setQuote(q);
    } catch (err) {
      setQuoteError((err && err.message) || "行情查询失败");
      setQuote(null);
    } finally {
      setQuoteLoading(false);
    }
  }, []);

  const handleQuoteQuery = useCallback(
    async (text) => {
      const t = String(text || "").trim();
      if (!t) {
        setQuoteError("请输入股票名称或 6 位代码");
        return;
      }
      setQuoteMatches([]);
      if (/^\d{6}$/.test(t)) {
        loadQuote(t);
        return;
      }
      setQuoteLoading(true);
      setQuoteError("");
      try {
        const list = await api.searchStocks(t);
        if (!list || list.length === 0) {
          setQuoteError(`未找到与「${t}」匹配的股票`);
          return;
        }
        if (list.length === 1) {
          loadQuote(list[0].code);
          return;
        }
        setQuoteMatches(list.slice(0, 5));
      } catch (err) {
        setQuoteError((err && err.message) || "搜索失败，请重试");
      } finally {
        setQuoteLoading(false);
      }
    },
    [loadQuote]
  );

  return (
    <div className="data-tab">
      {/* 今日数据大卡（迁移自 HomeView；R3：隐藏降级横幅与来源文本，来源进 tooltip） */}
      <div className="card realtime-card">
        <div className="realtime-head">
          <div>
            <h3 className="card-title">今日数据</h3>
            <p
              className="card-sub"
              title={
                rtData && rtData.sources && rtData.sources.length
                  ? `数据来源：${rtData.sources.join(" / ")}`
                  : undefined
              }
            >
              {rtData && rtData.status
                ? `${rtData.status.phase} · 更新于 ${rtData.status.time}`
                : "实时行情概览"}
            </p>
          </div>
          <div className="realtime-actions">
            <label className="rt-auto">
              <input type="checkbox" checked={rtAuto} onChange={(e) => setRtAuto(e.target.checked)} />
              30 秒自动刷新
            </label>
            <button className="btn btn-outline btn-sm" onClick={loadRealtime} disabled={rtLoading}>
              {rtLoading ? "刷新中…" : "刷新"}
            </button>
          </div>
        </div>

        {rtError ? <div className="upload-error">{rtError}</div> : null}

        {!rtData && !rtError ? (
          <div className="loading-card">
            <div className="spinner" />
            <div style={{ marginTop: 10 }}>正在加载实时数据…</div>
          </div>
        ) : null}

        {rtData && rtData.indices ? (
          <div className="kpi-grid">
            {rtData.indices.indices
              .filter((ix) => ["上证指数", "创业板指", "科创50"].includes(ix.name))
              .map((ix) => {
                const pct = finiteNum(ix.pct_change);
                const cls = !Number.isFinite(pct) ? "zero" : pct > 0 ? "pos" : pct < 0 ? "neg" : "zero";
                return (
                  <div key={ix.name} className="kpi">
                    <div className="kpi-label">{ix.name}</div>
                    <div className="kpi-value">{fmtPrice2(ix.price)}</div>
                    <div
                      className={`kpi-sub ${cls}`}
                      title={ix.source ? `数据来源：${ix.source}` : undefined}
                    >
                      {fmtSignedPct2(ix.pct_change)}
                    </div>
                    {ix.up_count != null || ix.down_count != null ? (
                      <div
                        className="kpi-breadth"
                        title={ix.updown_source ? `涨跌家数来源：${ix.updown_source}` : undefined}
                      >
                        <div className="kb-row">
                          <span className="kb-up">涨 {ix.up_count ?? "—"}</span>
                          <span className="kb-down">跌 {ix.down_count ?? "—"}</span>
                        </div>
                        <div className="kb-bar">
                          <span
                            className="kb-bar-up"
                            style={{
                              width: `${
                                (Number(ix.up_count) || 0) + (Number(ix.down_count) || 0) > 0
                                  ? ((Number(ix.up_count) || 0) /
                                      ((Number(ix.up_count) || 0) + (Number(ix.down_count) || 0))) *
                                    100
                                  : 0
                              }%`,
                            }}
                          />
                          <span className="kb-bar-down" />
                        </div>
                      </div>
                    ) : null}
                  </div>
                );
              })}
          </div>
        ) : null}

        <ExtraIndicesStrip />

        {rtData && rtData.zt ? (
          <div className="rt-zt">
            <span className="tag-chip light">
              涨停 <b className="pos">{rtData.zt.limit_up_count ?? "—"}</b>
            </span>
            <span className="tag-chip light">
              跌停 <b className="neg">{rtData.zt.limit_down_count ?? "—"}</b>
            </span>
            <span className="tag-chip light">
              炸板率{" "}
              <b className="zero">
                {rtData.zt.zhaban_rate != null ? `${(rtData.zt.zhaban_rate * 100).toFixed(1)}%` : "—"}
              </b>
            </span>
            <span className="tag-chip light">
              封板率{" "}
              <b className="zero">
                {rtData.zt.seal_rate != null ? `${(rtData.zt.seal_rate * 100).toFixed(1)}%` : "—"}
              </b>
            </span>
            <span className="tag-chip light">
              昨日涨停 <b>{rtData.zt.yesterday_zt_count ?? "—"}</b>
            </span>
            {rtData.zt.tier && Object.keys(rtData.zt.tier).length ? (
              <span className="tag-chip light">
                梯队{" "}
                <b>
                  {Object.entries(rtData.zt.tier)
                    .map(([k, v]) => `${k} ${v}`)
                    .join(" / ")}
                </b>
              </span>
            ) : null}
          </div>
        ) : null}

        {rtData && rtData.sectors ? (
          <div className="rt-grid">
            <div className="rt-col">
              <div className="rt-col-title">主力净流入 Top5（亿）</div>
              {(rtData.sectors.flow_in || []).map((s) => (
                <div key={s.name} className="rt-row">
                  <span className="rt-name">{s.name}</span>
                  <span className="pos">{fmtYi(s.net_inflow)}</span>
                </div>
              ))}
            </div>
            <div className="rt-col">
              <div className="rt-col-title">主力净流出 Top5（亿）</div>
              {(rtData.sectors.flow_out || []).map((s) => (
                <div key={s.name} className="rt-row">
                  <span className="rt-name">{s.name}</span>
                  <span className="neg">{fmtYi(s.net_inflow)}</span>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {rtData && rtData.news && rtData.news.news && rtData.news.news.length ? (
          <div className="rt-news">
            <div className="rt-col-title">最新快讯</div>
            {rtData.news.news.slice(0, 6).map((n, i) => (
              <div key={i} className="rt-news-item">
                <span className="rt-news-time">{n.time || ""}</span>
                <span className="rt-news-text">{n.text}</span>
              </div>
            ))}
          </div>
        ) : null}

        {/* 个股实时行情 */}
        <div className="rt-quote">
          <div className="rt-col-title">个股实时行情</div>
          <div className="rt-quote-row">
            <input
              className="search-input"
              style={{ maxWidth: 200 }}
              placeholder="输入名称或代码，如：茅台 / 600519"
              value={quoteCode}
              onChange={(e) => setQuoteCode(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleQuoteQuery(quoteCode);
              }}
            />
            <button className="btn btn-outline btn-sm" onClick={() => handleQuoteQuery(quoteCode)} disabled={quoteLoading}>
              {quoteLoading ? "查询中…" : "查询"}
            </button>
          </div>
          {quoteError ? <div className="upload-error">{quoteError}</div> : null}
          {quoteMatches.length ? (
            <div className="rt-quote-matches">
              <span>找到多个匹配，请选择：</span>
              {quoteMatches.map((m) => (
                <button
                  key={m.code}
                  type="button"
                  className="tag-chip light"
                  onClick={() => {
                    setQuoteMatches([]);
                    loadQuote(m.code);
                  }}
                >
                  {m.name || m.code}（{m.code}）
                </button>
              ))}
            </div>
          ) : null}
          {quote ? (
            <div className="rt-quote-box">
              <div className="rt-quote-name">
                {quote.name || "—"}
                <span className="rt-quote-code">{quote.code}</span>
              </div>
              <div className={`rt-quote-price ${finiteNum(quote.pct_change) > 0 ? "pos" : finiteNum(quote.pct_change) < 0 ? "neg" : "zero"}`}>
                {fmtPrice2(quote.price)}
              </div>
              <div className={`rt-quote-pct ${finiteNum(quote.pct_change) > 0 ? "pos" : finiteNum(quote.pct_change) < 0 ? "neg" : "zero"}`}>
                {fmtSignedPct2(quote.pct_change)}
              </div>
              <div className="rt-quote-meta" title={quote.source ? `数据来源：${quote.source}` : undefined}>
                <span>今开 {fmtPrice2(quote.open)}</span>
                <span>最高 {fmtPrice2(quote.high)}</span>
                <span>最低 {fmtPrice2(quote.low)}</span>
                <span>昨收 {fmtPrice2(quote.pre_close)}</span>
                <span>换手 {fmtPct2(quote.turnover_rate)}</span>
              </div>
              {(quote.bids && quote.bids.length) || (quote.asks && quote.asks.length) ? (
                <div className="rt-quote-levels">
                  <div className="rt-levels-col">
                    {[...(quote.asks || [])].reverse().map((l, i) => (
                      <div key={`a${i}`} className="rt-level neg">
                        <span>卖{5 - i}</span>
                        <span>{Number.isFinite(Number(l.price)) ? Number(l.price).toFixed(Number(l.price) < 1 ? 3 : 2) : "—"}</span>
                        <span>{l.volume ?? "—"}</span>
                      </div>
                    ))}
                  </div>
                  <div className="rt-levels-col">
                    {(quote.bids || []).map((l, i) => (
                      <div key={`b${i}`} className="rt-level pos">
                        <span>买{i + 1}</span>
                        <span>{Number.isFinite(Number(l.price)) ? Number(l.price).toFixed(Number(l.price) < 1 ? 3 : 2) : "—"}</span>
                        <span>{l.volume ?? "—"}</span>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}
              <div className="rt-quote-actions">
                <button
                  className="btn btn-outline btn-sm"
                  onClick={() => onOpenStockDetail(quote.code, quote.name)}
                >
                  查看详情（分时/日K/分钟K/五档）
                </button>
              </div>
            </div>
          ) : null}
        </div>
        <p className="rt-foot">数据仅供研究参考，实时性以各数据源为准；市场有风险，决策需谨慎。</p>
      </div>

      {/* 今日数据扩展卡：市场天气 / 纳斯达克 / 板块温度榜 / 风格坐标 */}
      <div className="today-cards">
        <div className="tc-grid">
          <WeatherCard />
          <NasdaqCard />
        </div>
        <SectorHeatCard />
        <StyleCard />
      </div>

      {/* 我的观察 + 观察列表 */}
      <div className="home-cols" style={{ gridTemplateColumns: "minmax(0, 1fr)" }}>
        <WatchlistPanel onOpenStockDetail={onOpenStockDetail} />
      </div>
    </div>
  );
}
