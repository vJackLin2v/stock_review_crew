// normalize 层：严格对照 requirements §五 API 契约做字段映射
// 约定：比率一律小数（0.5=50%，展示层 ×100）；None=null → 前端「—」（禁 0）；
//       日期 YYYY-MM-DD；时间点 HHMMSS；响应恒含 disclaimer；degraded[] 可见标注。
//       补充（I4/I5 对齐）：record_id / session_id = "{date}_{time}"（复盘时间点可带 -N 后缀）；
//       created_at 为无时区本地 ISO "YYYY-MM-DDTHH:mm:ss"（后端 datetime.now().isoformat(timespec="seconds")）。
import { num } from "./format.js";

export const arr = (v) => (Array.isArray(v) ? v : []);
const n = (v, d = 0) => num(v, d);
const nullable = (v) => num(v, NaN);
// 账户类字段判空：null/undefined/''/NaN → null（禁止 NaN 上屏），否则数值原样
const nullableOrNull = (v) => {
  const n = num(v, NaN);
  return Number.isFinite(n) ? n : null;
};
const str = (v, d = "") => (v == null || v === "" ? d : String(v));

export const DEFAULT_DISCLAIMER = "仅供参考，不构成投资建议";

// ---- GET /api/health ----
export function normalizeHealth(raw = {}) {
  return { status: str(raw.status, "unknown") };
}

// ---- GET /api/jobs/{job_id} ----
export function normalizeJob(job = {}) {
  const j = job && typeof job === "object" ? job : {};
  const status = str(j.status, "queued");
  return {
    job_id: str(j.job_id),
    status,
    stage: str(j.stage, "queued"),
    pct: n(j.pct),
    message: str(j.message),
    analysts_done: n(j.analysts_done),
    analysts_total: n(j.analysts_total),
    error: str(j.error),
    result: j.result || null,
    offline: !!j.offline,
    isRunning: status === "queued" || status === "running",
    isDone: status === "done",
    isError: status === "error",
  };
}

// ---- meta（嵌套字段）----
export function normalizeMeta(meta = {}) {
  const m = meta && typeof meta === "object" ? meta : {};
  const degraded = arr(m.degraded).filter((x) => x != null && x !== "");
  return {
    date: str(m.date),
    mode: str(m.mode),
    mode_label: str(m.mode_label),
    time: str(m.time),
    created_at: str(m.created_at),
    degraded,
    disclaimer: str(m.disclaimer) || DEFAULT_DISCLAIMER,
    summary: str(m.summary),
    isDegraded: degraded.length > 0,
  };
}

// ---- 分析师（skill_name / analysis / suggestion / tags）----
export function normalizeAnalyst(x = {}, i = 0) {
  const a = x && typeof x === "object" ? x : {};
  return {
    skill_name: str(a.skill_name || a.name, `分析师${i + 1}`),
    skill_id: str(a.skill_id || a.id),
    analysis: str(a.analysis || a.content || a.report || a.comment),
    suggestion: str(a.suggestion),
    tags: arr(a.tags),
  };
}

// ---- 辩论记录 [{round, topic, responses:[{skill_name, response}]}] ----
export function normalizeDebateHistory(raw) {
  return arr(raw)
    .map((d, i) => {
      if (!d || typeof d !== "object") return null;
      if (Array.isArray(d.responses) || d.round != null || d.topic != null) {
        return {
          round: n(d.round) || i + 1,
          topic: str(d.topic),
          responses: arr(d.responses).map((r) => ({
            skill_name: str(r && (r.skill_name || r.name || r.speaker)),
            response: str(r && (r.response || r.point || r.content)),
          })),
        };
      }
      return {
        round: i + 1,
        topic: "",
        responses: [
          {
            skill_name: str(d.speaker || d.name),
            response: str(d.point || d.content),
          },
        ],
      };
    })
    .filter(Boolean);
}

const ladderRow = (x) => ({
  label: str(x && x.label),
  count: nullable(x && x.count),
  stocks: arr(x && x.stocks),
});

// ---- snapshot（图表数据快照，比率一律小数）----
export function normalizeSnapshot(raw = {}) {
  const s = raw && typeof raw === "object" ? raw : {};
  return {
    index_minute: arr(s.index_minute)
      .map((ix) => ({
        name: str(ix && ix.name),
        points: arr(ix && ix.points)
          .map((p) => ({
            time: str(p && p.time),
            value: nullable(p && p.value),
          }))
          .filter((p) => p.time),
      }))
      .filter((ix) => ix.name),
    limit_ladder: {
      up: arr(s.limit_ladder && s.limit_ladder.up).map(ladderRow),
      down: arr(s.limit_ladder && s.limit_ladder.down).map(ladderRow),
    },
    sectors: arr(s.sectors).map((x) => ({
      name: str(x && x.name),
      pct_change: nullable(x && x.pct_change),
      leader: str(x && x.leader),
    })),
    sentiment: {
      up_count: nullable(s.sentiment && s.sentiment.up_count),
      down_count: nullable(s.sentiment && s.sentiment.down_count),
      limit_up_count: nullable(s.sentiment && s.sentiment.limit_up_count),
      limit_down_count: nullable(s.sentiment && s.sentiment.limit_down_count),
      red_rate: nullable(s.sentiment && s.sentiment.red_rate),
      continue_rate: nullable(s.sentiment && s.sentiment.continue_rate),
      break_rate: nullable(s.sentiment && s.sentiment.break_rate),
      button_rate: nullable(s.sentiment && s.sentiment.button_rate),
      avg_return: nullable(s.sentiment && s.sentiment.avg_return),
      up_down_ratio: nullable(s.sentiment && s.sentiment.up_down_ratio),
      source: str(s.sentiment && s.sentiment.source),
      degraded: arr(s.sentiment && s.sentiment.degraded),
    },
    source: str(s.source),
    degraded: arr(s.degraded),
  };
}

// ---- 报告详情（GET /api/reviews/{date}/{time} 与 job result 共用部分）----
export function normalizeReport(raw = {}) {
  const r = raw && typeof raw === "object" ? raw : {};
  const meta = normalizeMeta(r.meta);
  const degraded = arr(r.degraded && r.degraded.length ? r.degraded : meta.degraded);
  return {
    meta,
    report: str(r.report || r.final_report),
    analyses: arr(r.analyses || r.analyst_reports).map((x, i) => normalizeAnalyst(x, i)),
    debate_history: normalizeDebateHistory(r.debate_history || r.debate),
    snapshot: normalizeSnapshot(r.snapshot),
    disclaimer: str(r.disclaimer) || meta.disclaimer || DEFAULT_DISCLAIMER,
    degraded,
    isDegraded: degraded.length > 0,
  };
}

// ---- job result（{record_id, meta, report, snapshot}）----
export function normalizeJobResult(raw = {}) {
  const r = raw && typeof raw === "object" ? raw : {};
  const meta = normalizeMeta(r.meta);
  return {
    record_id: str(r.record_id),
    meta,
    report: str(r.report || r.final_report),
    snapshot: normalizeSnapshot(r.snapshot),
    disclaimer: meta.disclaimer || DEFAULT_DISCLAIMER,
  };
}

// ---- GET /api/reviews（历史分组列表）----
export function normalizeReviewList(raw) {
  return arr(raw)
    .map((g) => ({
      date: str(g && g.date),
      items: arr(g && g.items).map((it) => ({
        record_id: str(it && it.record_id),
        mode: str(it && it.mode),
        mode_label: str(it && it.mode_label),
        time: str(it && it.time),
        created_at: str(it && it.created_at),
        summary: str(it && it.summary),
      })),
    }))
    .filter((g) => g.date);
}

// ---- GET /api/reviews/{date}/{time} ----
export function normalizeReviewDetail(raw = {}) {
  return normalizeReport(raw);
}

// ---- 聊天 ----
export function normalizeChatMessage(m = {}, i = 0) {
  const x = m && typeof m === "object" ? m : {};
  return {
    id: str(x.id, `m${i}`),
    role: str(x.role, "assistant"),
    content: str(x.content),
    analyst_name: str(x.analyst_name),
    analyst_parts: arr(x.analyst_parts).map((p) => ({
      skill_name: str(p && (p.skill_name || p.name)),
      content: str(p && (p.content || p.response)),
    })),
    created_at: str(x.created_at),
    isUser: String(x.role) === "user",
    isMulti: arr(x.analyst_parts).length > 1,
  };
}

function normalizeChatAnalysts(list) {
  return arr(list).map((a) => (typeof a === "string" ? a : str(a && a.skill_name)));
}

export function normalizeChatSession(raw = {}) {
  const s = raw && typeof raw === "object" ? raw : {};
  const meta = s.meta || {};
  return {
    meta: {
      session_id: str(s.session_id || meta.session_id),
      target_type: str(meta.target_type),
      target: str(meta.target),
      target_name: str(meta.target_name || meta.target),
      analysts: normalizeChatAnalysts(meta.analysts),
      title: str(meta.title),
      created_at: str(meta.created_at),
      date: str(meta.date),
    },
    messages: arr(s.messages).map((x, i) => normalizeChatMessage(x, i)),
    disclaimer: str(s.disclaimer) || DEFAULT_DISCLAIMER,
  };
}

export function normalizeChatSessionList(raw) {
  return arr(raw).map((s) => {
    const x = s && typeof s === "object" ? s : {};
    const meta = x.meta || {};
    return {
      session_id: str(x.session_id || meta.session_id),
      target_type: str(x.target_type || meta.target_type),
      target: str(x.target || meta.target),
      target_name: str(x.target_name || meta.target_name || x.target || meta.target),
      analysts: normalizeChatAnalysts(x.analysts || meta.analysts),
      title: str(x.title || meta.title),
      created_at: str(x.created_at || meta.created_at),
      date: str(x.date || meta.date),
      last_message: str(x.last_message),
      message_count: n(x.message_count),
    };
  });
}

// ---- R12 模拟账户（requirements-r12 契约）----
export function normalizeAccountPosition(x = {}) {
  const p = x && typeof x === "object" ? x : {};
  return {
    code: str(p.code),
    name: str(p.name),
    qty: nullable(p.qty),
    available_qty: nullable(p.available_qty),
    avg_cost: nullable(p.avg_cost),
    price: nullable(p.price),
    market_value: nullable(p.market_value),
    pnl: nullableOrNull(p.pnl),
    pnl_rate: nullableOrNull(p.pnl_rate),
  };
}

export function normalizeAccountTrade(x = {}) {
  const t = x && typeof x === "object" ? x : {};
  const status = str(t.status);
  return {
    date: str(t.date),
    time: str(t.time),
    code: str(t.code),
    name: str(t.name),
    side: str(t.side),
    qty: nullable(t.qty),
    price: nullable(t.price),
    amount: nullable(t.amount),
    fee: nullable(t.fee),
    stamp_tax: nullable(t.stamp_tax),
    transfer_fee: nullable(t.transfer_fee),
    status,
    isRejected: status !== "成交",
    reject_reason: str(t.reject_reason),
    realized: nullable(t.realized),
  };
}

export function normalizeAccountSnapshot(x = {}) {
  const s = x && typeof x === "object" ? x : {};
  return {
    date: str(s.date),
    cash: nullable(s.cash),
    position_mv: nullable(s.position_mv),
    total_assets: nullable(s.total_assets),
    realized_pnl: nullableOrNull(s.realized_pnl),
  };
}

// 2026-08-13：历史持仓（已平仓，字段契约与后端 closed_positions 对齐）
export function normalizeClosedPosition(x = {}) {
  const p = x && typeof x === "object" ? x : {};
  return {
    code: str(p.code),
    name: str(p.name),
    buy_qty: nullable(p.buy_qty),
    sell_qty: nullable(p.sell_qty),
    avg_buy_price: nullableOrNull(p.avg_buy_price),
    avg_sell_price: nullableOrNull(p.avg_sell_price),
    realized_pnl: nullableOrNull(p.realized_pnl),
    first_buy_date: str(p.first_buy_date),
    last_sell_date: str(p.last_sell_date),
    holding_days: nullableOrNull(p.holding_days),
  };
}

export function normalizeAccountDetail(raw = {}) {
  const x = raw && typeof raw === "object" ? raw : {};
  return {
    skill_id: str(x.skill_id || x.id),
    name: str(x.name || x.skill_name),
    cash: nullable(x.cash),
    total_assets: nullable(x.total_assets),
    total_return_rate: nullableOrNull(x.total_return_rate),
    unrealized_pnl: nullableOrNull(x.unrealized_pnl),
    realized_pnl: nullableOrNull(x.realized_pnl),
    win_rate: nullableOrNull(x.win_rate),
    profit_loss_ratio: nullableOrNull(x.profit_loss_ratio),
    avg_holding_days: nullableOrNull(x.avg_holding_days),
    positions: arr(x.positions).map(normalizeAccountPosition).filter((p) => p.code),
    closed_positions: arr(x.closed_positions)
      .map(normalizeClosedPosition)
      .filter((p) => p.code),
    trades: arr(x.trades).map(normalizeAccountTrade),
    snapshots: arr(x.snapshots)
      .map(normalizeAccountSnapshot)
      .filter((s) => s.date),
    updated_at: str(x.updated_at),
  };
}

export function normalizeAccounts(raw) {
  return arr(raw)
    .map((x) => {
      const a = x && typeof x === "object" ? x : {};
      return {
        skill_id: str(a.skill_id || a.id),
        name: str(a.name || a.skill_name),
        cash: nullable(a.cash),
        total_assets: nullable(a.total_assets),
        total_return_rate: nullableOrNull(a.total_return_rate),
        unrealized_pnl: nullableOrNull(a.unrealized_pnl),
        realized_pnl: nullableOrNull(a.realized_pnl),
        win_rate: nullableOrNull(a.win_rate),
        today_trades_count: nullable(a.today_trades_count),
        updated_at: str(a.updated_at),
      };
    })
    .filter((a) => a.skill_id);
}

// ---- R10 数据扩展 normalize（个股详情/自选/板块/情绪/天气/纳指等）----
export function normalizeStockDetail(raw = {}) {
  const x = raw && typeof raw === "object" ? raw : {};
  const quote = x.quote && typeof x.quote === "object" ? x.quote : {};
  const daily = Array.isArray(x.daily)
    ? x.daily.map((d) => ({
        date: str(d.date || d["日期"]),
        open: nullable(d.open ?? d["开盘"]),
        close: nullable(d.close ?? d["收盘"]),
        high: nullable(d.high ?? d["最高"]),
        low: nullable(d.low ?? d["最低"]),
        volume: nullable(d.volume ?? d["成交量"]),
        pct_change: nullable(d.pct_change ?? d["涨跌幅"]),
      }))
    : [];
  return {
    code: str(x.code),
    name: str(x.name || quote.name),
    date: str(x.date),
    quote: {
      price: nullable(quote.price),
      pre_close: nullable(quote.pre_close),
      open: nullable(quote.open),
      high: nullable(quote.high),
      low: nullable(quote.low),
      pct_change: nullable(quote.pct_change),
      volume: nullable(quote.volume),
      amount: nullable(quote.amount),
    },
    daily,
    minute: Array.isArray(x.minute)
      ? x.minute.map((m) => ({
          time: str(m.time),
          price: nullable(m.price),
          avg: nullable(m.avg),
          volume: nullable(m.volume),
        }))
      : [],
    kline_5m: Array.isArray(x.kline_5m) ? x.kline_5m : [],
    kline_1m: Array.isArray(x.kline_1m) ? x.kline_1m : [],
    // 2026-08-11 修复：后端与 mock 均返回 ma:{ma5,ma10,ma20} 对象，
    // 此前只映射顶层 ma5/ma10/ma20 导致 detail.ma 为 undefined，
    // 详情页渲染崩溃（Cannot read properties of undefined (reading 'ma5')）。
    ma: {
      ma5: nullable((x.ma && x.ma.ma5) ?? x.ma5),
      ma10: nullable((x.ma && x.ma.ma10) ?? x.ma10),
      ma20: nullable((x.ma && x.ma.ma20) ?? x.ma20),
    },
    ma5: nullable((x.ma && x.ma.ma5) ?? x.ma5),
    ma10: nullable((x.ma && x.ma.ma10) ?? x.ma10),
    ma20: nullable((x.ma && x.ma.ma20) ?? x.ma20),
    degraded: Boolean(x.degraded),
    degraded_reason: arr(x.degraded_reason),
  };
}

export function normalizeWatchlist(raw = {}) {
  const x = raw && typeof raw === "object" ? raw : {};
  const list = Array.isArray(x.items) ? x.items : Array.isArray(x) ? x : [];
  return list.map((w) => ({
    code: str(w.code || w["代码"]),
    name: str(w.name || w["名称"]),
    price: nullable(w.price),
    pct_change: nullable(w.pct_change ?? w["涨跌幅"]),
    added_at: str(w.added_at),
  }));
}

export function normalizeQuote(raw = {}) {
  const x = raw && typeof raw === "object" ? raw : {};
  return {
    code: str(x.code),
    name: str(x.name),
    price: nullable(x.price),
    pre_close: nullable(x.pre_close),
    open: nullable(x.open),
    high: nullable(x.high),
    low: nullable(x.low),
    pct_change: nullable(x.pct_change),
    volume: nullable(x.volume),
    amount: nullable(x.amount),
  };
}

export function normalizeMarketSentiment(raw = {}) {
  const x = raw && typeof raw === "object" ? raw : {};
  return {
    ...x,
    up: nullable(x.up ?? x.up_count),
    down: nullable(x.down ?? x.down_count),
    limit_up: nullable(x.limit_up ?? x.limit_up_count),
    limit_down: nullable(x.limit_down ?? x.limit_down_count),
    red_rate: nullable(x.red_rate),
    break_rate: nullable(x.break_rate ?? x.zhaban_rate),
    avg_return: nullable(x.avg_return),
    emotion_score: nullable(x.emotion_score),
    emotion_label: str(x.emotion_label),
    weather: str(x.weather),
    date: str(x.date),
    up_down_ratio: nullable(x.up_down_ratio),
    limit_up_count: nullable(x.limit_up_count),
    limit_down_count: nullable(x.limit_down_count),
    lianban_height: nullable(x.lianban_height),
    reasons: arr(x.reasons),
    source: str(x.source),
    degraded: Boolean(x.degraded),
    degraded_reason: arr(x.degraded_reason),
  };
}

export function normalizeSectorHeat(raw = {}) {
  const x = raw && typeof raw === "object" ? raw : {};
  const mapRow = (s) => ({
    name: str(s.name || s["板块"] || s.board),
    pct_change: nullable(s.pct_change ?? s["涨跌幅"]),
    amount: nullable(s.amount ?? s["成交额"]),
    leader: str(s.leader || s["领涨股"]),
  });
  const hot = arr(x.hot ?? x.hot_sectors ?? x.top5).map(mapRow);
  const cold = arr(x.cold ?? x.cold_sectors ?? x.bottom5).map(mapRow);
  return {
    ...x,
    hot,
    cold,
    source: str(x.source),
    degraded: Boolean(x.degraded),
    degraded_reason: arr(x.degraded_reason),
  };
}

export function normalizeMarketWeather(raw = {}) {
  const x = raw && typeof raw === "object" ? raw : {};
  return {
    weather: str(x.weather),
    emotion_score: nullable(x.emotion_score),
    emotion_label: str(x.emotion_label),
    reasons: arr(x.reasons),
    source: str(x.source),
    degraded: Boolean(x.degraded),
  };
}

export function normalizeExtraIndices(raw = {}) {
  const x = raw && typeof raw === "object" ? raw : {};
  const list = Array.isArray(x.items)
    ? x.items
    : Array.isArray(x.indices)
      ? x.indices
      : Array.isArray(x)
        ? x
        : [];
  const items = list.map((i) => ({
    code: str(i.code),
    name: str(i.name),
    close: nullable(i.close),
    pct_change: nullable(i.pct_change),
  }));
  return {
    ...x,
    items,
    source: str(x.source),
    degraded: Boolean(x.degraded),
    degraded_reason: arr(x.degraded_reason),
  };
}

export function normalizeMarketStyle(raw = {}) {
  const x = raw && typeof raw === "object" ? raw : {};
  return {
    ...x,
    large: nullable(x.large ?? x.large_cap),
    small: nullable(x.small ?? x.small_cap),
    value: nullable(x.value),
    growth: nullable(x.growth),
    hot: arr(x.hot),
    source: str(x.source),
    degraded: Boolean(x.degraded),
    degraded_reason: arr(x.degraded_reason),
  };
}

export function normalizeUsNasdaq(raw = {}) {
  const x = raw && typeof raw === "object" ? raw : {};
  const list = Array.isArray(x.items) ? x.items : Array.isArray(x) ? x : [];
  const items = list.map((d) => ({
    ...d,
    date: str(d.date),
    symbol: str(d.symbol),
    is_today: d.is_today,
    close: nullable(d.close),
    pct_change: nullable(d.pct_change),
  }));
  return {
    ...x,
    items,
    source: str(x.source),
    degraded: Boolean(x.degraded),
    degraded_reason: arr(x.degraded_reason),
  };
}
