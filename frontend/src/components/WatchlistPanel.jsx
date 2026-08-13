// R10 F9 观察列表：代码/名称/最新价/涨跌幅，支持添加（代码或搜索复用 /api/data/search）、
// 删除、拖拽 + 上下移排序（PUT /api/watchlist/order）。
// 行情只读消费 /api/data/quote；None → 「—」；失败/降级 → 中文态，禁止白屏。
import React, { useCallback, useEffect, useState } from "react";
import * as api from "../api";
import { normalizeQuote, normalizeWatchlist } from "../normalize";
import { fmtSignedPct, num, pctClass } from "../format";

const fmtPrice = (v) => {
  const n = num(v, NaN);
  return Number.isFinite(n) ? n.toFixed(2) : "—";
};

export default function WatchlistPanel({ onOpenStockDetail }) {
  const [items, setItems] = useState([]);
  const [quotes, setQuotes] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [input, setInput] = useState("");
  const [matches, setMatches] = useState([]);
  const [busy, setBusy] = useState(false);
  // 拖拽排序状态
  const [dragFrom, setDragFrom] = useState(null);
  const [dragOver, setDragOver] = useState(null);

  // 加载列表 + 逐只行情（任一失败不阻塞整体，缺失显示「—」）
  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const list = normalizeWatchlist(await api.getWatchlist());
      setItems(list);
      const settled = await Promise.allSettled(
        list.map((it) => api.getStockQuote(it.code).then(normalizeQuote))
      );
      const q = {};
      settled.forEach((r, i) => {
        if (r.status === "fulfilled" && r.value) q[list[i].code] = r.value;
      });
      setQuotes(q);
    } catch (err) {
      setItems([]);
      setQuotes({});
      setError((err && err.message) || "观察列表加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // 添加：6 位代码直加；名称先搜索，多候选让用户选择
  const handleAdd = useCallback(
    async (text) => {
      const t = String(text == null ? input : text).trim();
      if (!t) {
        setError("请输入股票代码或名称");
        return;
      }
      setBusy(true);
      setError("");
      setMatches([]);
      try {
        if (/^\d{6}$/.test(t)) {
          await api.addWatchlist(t);
        } else {
          const found = await api.searchStocks(t);
          if (!found || !found.length) throw new Error(`未找到与「${t}」匹配的股票`);
          if (found.length > 1) {
            setMatches(found.slice(0, 5));
            return; // 等待用户选择候选
          }
          await api.addWatchlist(found[0].code);
        }
        setInput("");
        await load();
      } catch (err) {
        setError((err && err.message) || "添加失败，请重试");
      } finally {
        setBusy(false);
      }
    },
    [input, load]
  );

  const handleDelete = useCallback(
    async (code) => {
      setBusy(true);
      setError("");
      try {
        await api.deleteWatchlist(code);
        setItems((prev) => prev.filter((x) => x.code !== code));
        setQuotes((prev) => {
          const next = { ...prev };
          delete next[code];
          return next;
        });
      } catch (err) {
        setError((err && err.message) || "删除失败，请重试");
      } finally {
        setBusy(false);
      }
    },
    []
  );

  // 上下移 / 拖拽落点共用：本地即时重排 + PUT order 持久化（失败回滚并提示）
  const move = useCallback(
    async (from, to) => {
      if (from == null || to == null || from === to) return;
      if (to < 0 || to >= items.length) return;
      const next = items.slice();
      const [it] = next.splice(from, 1);
      next.splice(to, 0, it);
      const prev = items;
      setItems(next);
      setError("");
      try {
        await api.reorderWatchlist(next.map((x) => x.code));
      } catch (err) {
        setItems(prev);
        setError((err && err.message) || "保存排序失败，已还原");
      }
    },
    [items]
  );

  const onDragStart = (i) => (e) => {
    setDragFrom(i);
    if (e && e.dataTransfer) e.dataTransfer.effectAllowed = "move";
  };
  const onDragOver = (i) => (e) => {
    e.preventDefault();
    if (dragOver !== i) setDragOver(i);
  };
  const onDrop = (i) => (e) => {
    e.preventDefault();
    if (dragFrom != null && dragFrom !== i) move(dragFrom, i);
    setDragFrom(null);
    setDragOver(null);
  };
  const onDragEnd = () => {
    setDragFrom(null);
    setDragOver(null);
  };

  return (
    <div className="card">
      <div className="card-head-row">
        <div>
          <h3 className="card-title">我的观察</h3>
          <p className="card-sub">自选股列表 · 拖拽或上下移排序，行情只读展示</p>
        </div>
        {loading ? (
          <span className="pill offline">加载中…</span>
        ) : (
          <button
            className="btn btn-ghost btn-xs"
            onClick={load}
            disabled={loading}
            title="刷新观察列表"
          >
            刷新
          </button>
        )}
      </div>

      <div className="wl-add-row">
        <input
          className="search-input"
          style={{ maxWidth: 220 }}
          placeholder="添加自选：代码或名称，如 600519 / 茅台"
          value={input}
          disabled={busy}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") handleAdd(input);
          }}
        />
        <button
          className="btn btn-outline btn-sm"
          onClick={() => handleAdd(input)}
          disabled={busy}
        >
          {busy ? "处理中…" : "添加"}
        </button>
      </div>

      {error ? <div className="upload-error">{error}</div> : null}
      {matches.length ? (
        <div className="wl-matches">
          <span>找到多个匹配，请选择：</span>
          {matches.map((m) => (
            <button
              key={m.code}
              type="button"
              className="tag-chip light"
              disabled={busy}
              onClick={() => handleAdd(m.code)}
            >
              {m.name || m.code}（{m.code}）
            </button>
          ))}
        </div>
      ) : null}

      {loading ? (
        <div className="loading-card" style={{ padding: 22 }}>
          <div className="spinner" />
          <div style={{ marginTop: 8 }}>正在加载观察列表…</div>
        </div>
      ) : items.length === 0 ? (
        <div className="empty">
          {error ? "数据缺失/降级：观察列表不可用" : "暂无自选股，输入代码或名称添加"}
          {error ? (
            <div>
              <button className="btn btn-outline btn-sm" onClick={load}>
                重试
              </button>
            </div>
          ) : null}
        </div>
      ) : (
        <div className="wl-list">
          {items.map((it, i) => {
            const q = quotes[it.code];
            const pct = q ? num(q.pct_change, NaN) : NaN;
            return (
              <div
                key={it.code}
                className={`wl-item${dragFrom === i ? " dragging" : ""}${
                  dragOver === i ? " drag-over" : ""
                }`}
                draggable
                onDragStart={onDragStart(i)}
                onDragOver={onDragOver(i)}
                onDrop={onDrop(i)}
                onDragEnd={onDragEnd}
                title="拖拽排序；点击查看详情"
              >
                <span className="wl-drag" aria-hidden="true">⋮⋮</span>
                <div
                  className="wl-info"
                  role="button"
                  tabIndex={0}
                  onClick={() => onOpenStockDetail(it.code, q && q.name ? q.name : it.name)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onOpenStockDetail(it.code, q && q.name ? q.name : it.name);
                    }
                  }}
                >
                  <span className="wl-name">
                    {q && q.name ? q.name : it.name || "—"}
                  </span>
                  <span className="wl-code">{it.code}</span>
                </div>
                <div className="wl-quote">
                  <div className={`wl-price ${pctClass(pct)}`}>
                    {q ? fmtPrice(q.price) : "—"}
                  </div>
                  <div className={`wl-pct ${pctClass(pct)}`}>
                    {Number.isFinite(pct) ? fmtSignedPct(pct) : "—"}
                  </div>
                </div>
                <div className="wl-actions">
                  <button
                    className="wl-btn"
                    title="上移"
                    aria-label={`上移 ${it.code}`}
                    disabled={busy || i === 0}
                    onClick={() => move(i, i - 1)}
                  >
                    ↑
                  </button>
                  <button
                    className="wl-btn"
                    title="下移"
                    aria-label={`下移 ${it.code}`}
                    disabled={busy || i === items.length - 1}
                    onClick={() => move(i, i + 1)}
                  >
                    ↓
                  </button>
                  <button
                    className="wl-btn del"
                    title="删除"
                    aria-label={`删除 ${it.code}`}
                    disabled={busy}
                    onClick={() => handleDelete(it.code)}
                  >
                    ✕
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
      <p className="wl-foot">行情仅供研究参考，不构成投资建议；数据缺失时显示「—」。</p>
    </div>
  );
}
