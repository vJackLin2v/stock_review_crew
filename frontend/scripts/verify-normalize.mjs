// 契约断言脚本：node scripts/verify-normalize.mjs
// 验证 src/normalize.js 严格对照 requirements §五 API 契约，
// 并以离线 mock 数据（mock.js）作为后端 Schema 样本做往返断言：
//   比率一律小数（0.5=50%，展示 ×100）；None → 「—」；日期 YYYY-MM-DD；
//   时间点 HHMMSS；meta.degraded[]；响应恒含 disclaimer；聊天逐位片段+汇总。
import assert from "node:assert/strict";
import {
  BREADTH_KEYS,
  normalizeBreadthDistribution,
  normalizeChatMessage,
  normalizeChatSession,
  normalizeExtraIndices,
  normalizeJob,
  normalizeJobResult,
  normalizeMarketStyle,
  normalizeMarketWeather,
  normalizeQuote,
  normalizeReviewDetail,
  normalizeReviewList,
  normalizeSectorHeat,
  normalizeSnapshot,
  normalizeUsNasdaq,
  normalizeWatchlist,
  normalizeWatchlistItem,
} from "../src/normalize.js";
import { fmtAmount, fmtCount, fmtPct, fmtTimeHM, pctClass } from "../src/format.js";
import { MODES, modeLabel } from "../src/modes.js";
import {
  buildMockExtraIndices,
  buildMockJobResult,
  buildMockMarketStyle,
  buildMockMarketWeather,
  buildMockReviewDetail,
  buildMockSectorHeat,
  buildMockUsNasdaq,
  mockChatReply,
  mockChatSessionList,
  mockReviewList,
  mockWatchlistAdd,
  mockWatchlistDelete,
  mockWatchlistList,
  mockWatchlistReorder,
  seedMockChatSessions,
  seedMockReviews,
} from "../src/mock.js";

const DATE = "2026-08-03";

// ---- §五：比率 ×100 / None → — / 月份中文 / HHMMSS ----
assert.equal(fmtPct(0.5), "50.00%", "比率 0.5 必须展示为 50.00%");
assert.equal(fmtPct(-0.123), "-12.30%");
assert.equal(fmtPct(null), "—", "None 必须展示为 —，禁止 0");
assert.equal(fmtPct(undefined), "—");
assert.equal(fmtCount(null), "—");
assert.equal(pctClass(null), "zero");
assert.equal(pctClass(0), "zero");
assert.equal(pctClass(0.01), "pos");
assert.equal(pctClass(-0.01), "neg");
assert.equal(fmtTimeHM("150500"), "15:05");
// P1-M：金额智能缩写（元 → 万/亿/万亿，保留 2 位小数；None → —）
assert.equal(fmtAmount(5004070000), "50.04 亿");
assert.equal(fmtAmount(12345), "1.23 万");
assert.equal(fmtAmount(999), "999.00");
assert.equal(fmtAmount(1.5e12), "1.50 万亿");
assert.equal(fmtAmount(null), "—");

// ---- §五：6 模式中文标签 ----
assert.deepEqual(
  MODES.map((m) => m.code),
  ["pre_market", "auction", "intraday_am", "noon", "intraday_pm", "close"],
  "6 模式代码必须齐全"
);
assert.equal(modeLabel("pre_market"), "早盘前决策");
assert.equal(modeLabel("close"), "收盘复盘");

// ---- Job 轮询（running）字段齐备 ----
const runningJob = {
  job_id: "mock-test-1",
  status: "running",
  stage: "sentiment",
  pct: 58,
  message: "情绪派 2/3 · 铁锤狂砸盘 完成点评",
  analysts_done: 2,
  analysts_total: 5,
  result: null,
  error: null,
  offline: true,
};
const j1 = normalizeJob(runningJob);
assert.equal(j1.job_id, "mock-test-1");
assert.equal(j1.status, "running");
assert.equal(j1.stage, "sentiment");
assert.equal(j1.pct, 58);
assert.equal(j1.analysts_done, 2);
assert.equal(j1.analysts_total, 5);
assert.equal(j1.result, null);
assert.equal(j1.isRunning, true);
assert.equal(j1.isDone, false);
assert.equal(j1.isError, false);

// 空对象兜底（禁止白屏）：缺失字段 → queued / 0 / 空串
const jEmpty = normalizeJob({});
assert.equal(jEmpty.status, "queued");
assert.equal(jEmpty.stage, "queued");
assert.equal(jEmpty.pct, 0);
assert.equal(jEmpty.isRunning, true);

// ---- Job result（done）：{record_id, meta, report, snapshot} ----
const jobResult = buildMockJobResult({ date: DATE, mode: "close" });
assert.match(
  jobResult.record_id,
  /^\d{4}-\d{2}-\d{2}_\d{6}(?:-\d+)?$/,
  "record_id = 日期_时间点（I4/I5 契约：_ 分隔，时间点可带 -N 后缀）"
);
const jr = normalizeJobResult(jobResult);
assert.match(jr.meta.date, /^\d{4}-\d{2}-\d{2}$/, "meta.date 必须为 YYYY-MM-DD");
assert.match(jr.meta.time, /^\d{6}$/, "meta.time 必须为 HHMMSS");
assert.match(
  jr.meta.created_at,
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$/,
  "created_at 必须为无时区本地 ISO（YYYY-MM-DDTHH:mm:ss）"
);
assert.ok(
  !jr.meta.created_at.includes("+") && !jr.meta.created_at.includes("Z"),
  "created_at 不得携带时区后缀（+08:00 / Z）"
);
assert.equal(jr.meta.mode, "close");
assert.equal(jr.meta.mode_label, "收盘复盘");
assert.ok(Array.isArray(jr.meta.degraded) && jr.meta.degraded.length > 0, "meta.degraded[] 必须可见标注");
assert.ok(jr.meta.disclaimer && jr.meta.disclaimer.includes("仅供参考"), "meta 恒含 disclaimer");
assert.ok(jr.report.length > 0 && jr.report.includes("#"), "report 必须为非空 Markdown");
assert.equal(jr.record_id, jobResult.record_id);

// ---- 历史分组列表：按日期倒序、同日多份按时间点倒序、HH:MM + 模式标签 ----
const reviewMap = seedMockReviews(new Date(`${DATE}T00:00:00`));
const list = mockReviewList(reviewMap);
assert.ok(list.length >= 2, "历史至少 2 个日期分组");
const dates = list.map((g) => g.date);
assert.deepEqual(dates, [...dates].sort((a, b) => (a < b ? 1 : -1)), "日期必须倒序");
const firstGroup = list[0];
assert.equal(firstGroup.date, DATE);
assert.ok(firstGroup.items.length >= 2, "同日多份（今日至少 2 份）");
const times = firstGroup.items.map((it) => it.time);
assert.deepEqual(times, [...times].sort((a, b) => (a < b ? 1 : -1)), "同日按时间点倒序");
for (const it of firstGroup.items) {
  assert.match(it.time, /^\d{6}$/);
  assert.match(it.record_id, /^\d{4}-\d{2}-\d{2}_\d{6}(?:-\d+)?$/, "列表 record_id 必须为 日期_时间点");
  assert.match(it.created_at, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$/, "列表 created_at 必须为无时区本地 ISO");
  assert.ok(MODES.some((m) => m.code === it.mode), "item.mode 必须为 6 模式之一");
  assert.equal(it.mode_label, modeLabel(it.mode));
}
const nl = normalizeReviewList(list);
assert.equal(nl[0].date, DATE);
assert.equal(nl[0].items[0].mode_label, modeLabel(nl[0].items[0].mode));
assert.ok(nl[0].items[0].summary.length > 0);

// ---- 详情：analyses / debate_history / snapshot 严格对齐 ----
const detail = buildMockReviewDetail({ date: DATE, mode: "noon" });
const d = normalizeReviewDetail(detail);
assert.equal(d.meta.date, DATE);
assert.equal(d.meta.mode_label, "午间复盘");
assert.match(d.meta.created_at, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$/, "详情 meta.created_at 必须为无时区本地 ISO");
assert.equal(d.analyses.length, 5, "5 位分析师（趋势派 2 + 情绪派 3）");
for (const a of d.analyses) {
  assert.ok(a.skill_name && a.skill_name.length > 0, "analyst.skill_name 必须存在");
  assert.ok(typeof a.analysis === "string" && a.analysis.length > 0, "analyst.analysis 必须存在");
  assert.ok(typeof a.suggestion === "string" && a.suggestion.length > 0, "analyst.suggestion 必须存在");
  assert.ok(Array.isArray(a.tags), "analyst.tags 必须为数组");
}
assert.ok(d.debate_history.length >= 1, "辩论记录至少 1 轮");
for (const r of d.debate_history) {
  assert.ok(Number.isInteger(r.round) && r.round >= 1);
  assert.ok(Array.isArray(r.responses) && r.responses.length >= 1);
  for (const resp of r.responses) {
    assert.ok(resp.skill_name, "辩论 response.skill_name 必须存在");
    assert.ok(resp.response, "辩论 response.response 必须存在");
  }
}
assert.ok(d.report.includes("#"), "详情 report 为非空 Markdown");
assert.ok(d.disclaimer.includes("仅供参考"), "详情恒含 disclaimer");

// snapshot：指数分时 / 涨跌停梯队 / 板块 / 情绪
const snap = d.snapshot;
assert.ok(snap.index_minute.length >= 1, "指数分时至少 1 条");
assert.ok(snap.index_minute[0].points.length >= 2, "分时至少 2 个点");
for (const p of snap.index_minute[0].points) {
  assert.match(p.time, /^\d{2}:\d{2}$/, "分时点 time 为 HH:MM");
  assert.ok(Number.isFinite(p.value), "分时点 value 必须为有限数");
}
assert.ok(snap.limit_ladder.up.length >= 2, "涨停梯队至少 2 档");
for (const row of [...snap.limit_ladder.up, ...snap.limit_ladder.down]) {
  assert.ok(row.label, "梯队 label 必须存在");
  assert.ok(Number.isFinite(row.count), "梯队 count 必须为有限数");
}
assert.ok(snap.sectors.length >= 5, "板块至少 5 条");
for (const s of snap.sectors) {
  assert.ok(s.name, "板块 name 必须存在");
  assert.ok(Number.isFinite(s.pct_change) && Math.abs(s.pct_change) < 1, "板块 pct_change 必须为小数（<100%）");
}
// P1-K：板块跌幅榜 sectors_bottom 透传（数据层即可，渲染可后续）
assert.ok(Array.isArray(snap.sectors_bottom) && snap.sectors_bottom.length >= 1, "快照必须含板块跌幅榜 sectors_bottom");
for (const s of snap.sectors_bottom) {
  assert.ok(s.name, "跌幅榜 name 必须存在");
  assert.ok(Number.isFinite(s.pct_change) && Math.abs(s.pct_change) < 1, "跌幅榜 pct_change 必须为小数（<100%）");
}
const st = snap.sentiment;
for (const key of ["red_rate", "continue_rate", "break_rate", "button_rate", "up_down_ratio"]) {
  assert.ok(Number.isFinite(st[key]) && st[key] > 0 && st[key] < 5, `情绪比率 ${key} 必须为小数`);
}
assert.ok(Number.isFinite(st.avg_return), "昨日涨停平均收益必须为小数");
// R11-7：涨跌分布快照（distribution 区间 + 家数，比率小数）
assert.ok(snap.breadth && typeof snap.breadth.distribution === "object", "快照必须含涨跌分布 breadth.distribution");
assert.ok(Number.isFinite(snap.breadth.up) && Number.isFinite(snap.breadth.down), "涨跌家数必须为有限数");
assert.ok(
  Object.keys(snap.breadth.distribution).length >= 5,
  "涨跌分布区间至少 5 档",
);
assert.equal(typeof snap.breadth.note, "string", "breadth.note 必须透传（P1-H 分布降级说明）");
// P0-2：分布键名 ⊆ 允许集合（9 档规范键，含 %）；旧键名（历史快照）兼容映射后归一
const ALLOWED_KEYS = new Set(BREADTH_KEYS);
assert.equal(ALLOWED_KEYS.size, 9, "允许集合恰为 9 档");
for (const k of Object.keys(snap.breadth.distribution)) {
  assert.ok(ALLOWED_KEYS.has(k), `分布键名必须为规范键（含 %）：${k}`);
}
const legacyDist = normalizeBreadthDistribution({
  "涨停": 10, "跌停": 1, "涨5-10%": 2, "涨2-5%": 3, "涨0-2%": 4,
  "平盘": 1, "跌0-2%": 5, "跌2-5%": 2, "跌5%以上": 1,
});
assert.deepEqual(
  Object.keys(legacyDist).sort(),
  [...ALLOWED_KEYS].sort(),
  "旧键名快照经兼容映射后键名必须归一为规范键"
);
assert.equal(legacyDist["涨5%-10%"], 2, "旧键「涨5-10%」映射到「涨5%-10%」");
assert.equal(legacyDist["跌2%-5%"], 2, "旧键「跌2-5%」映射到「跌2%-5%」");
assert.equal(legacyDist["平盘"], 1, "无歧义键原样透传");
assert.ok(Number.isInteger(st.up_count) && Number.isInteger(st.down_count));

// None 字段：normalize 归一为 null（禁 0 禁 NaN 上屏），展示层 → —
const noneDetail = normalizeSnapshot({
  index_minute: [],
  limit_ladder: { up: [], down: [] },
  sectors: [],
  sentiment: { red_rate: null, break_rate: undefined, up_count: null, down_count: 0 },
});
assert.equal(noneDetail.sentiment.red_rate, null, "null 比率必须归一为 null（禁 0 禁 NaN 上屏）");
assert.equal(fmtPct(noneDetail.sentiment.red_rate), "—");
assert.equal(fmtCount(noneDetail.sentiment.up_count), "—");
assert.equal(noneDetail.sentiment.down_count, 0, "显式 0 不是 None，保留 0");
assert.deepEqual(noneDetail.index_minute, []);
assert.deepEqual(noneDetail.limit_ladder.up, []);

// ---- 聊天：会话结构 / 逐位片段 + 汇总 / 免责声明恒有 ----
const sessions = seedMockChatSessions(new Date(`${DATE}T00:00:00`));
assert.ok(sessions.length >= 2, "至少 2 个种子会话（多分析师 + 单分析师）");
const listRes = mockChatSessionList(sessions, {});
assert.ok(listRes.length >= 2);
const s1 = normalizeChatSession(sessions[0]);
assert.equal(s1.meta.target_type, "sector");
assert.match(s1.meta.session_id, /^\d{4}-\d{2}-\d{2}_\d{6}$/, "session_id 必须为 日期_时间点（I3 契约）");
assert.match(s1.meta.created_at, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$/, "会话 created_at 必须为无时区本地 ISO");
assert.ok(s1.meta.analysts.length >= 2, "多分析师会话");
assert.ok(s1.disclaimer.includes("仅供参考"), "会话恒含 disclaimer");
const multi = s1.messages.find((m) => m.isMulti);
assert.ok(multi, "多分析师交叉回复必须存在");
assert.ok(multi.analyst_parts.length === s1.meta.analysts.length, "逐位片段数 = 分析师数");
assert.ok(multi.content.length > 0, "必须含汇总内容");
const s2 = normalizeChatSession(sessions[1]);
assert.equal(s2.meta.analysts.length, 1, "单分析师会话");
assert.match(s2.meta.session_id, /^\d{4}-\d{2}-\d{2}_\d{6}$/);
assert.ok(s2.messages.some((m) => !m.isUser), "单分析师直接回复");
for (const m of [...s1.messages, ...s2.messages]) {
  assert.match(m.created_at, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$/, "消息 created_at 必须为无时区本地 ISO");
}

// 发送消息：逐位片段 + 汇总
const [userMsg, reply] = mockChatReply(sessions[0].meta, "明天怎么看？");
assert.equal(normalizeChatMessage(userMsg).isUser, true);
const r = normalizeChatMessage(reply);
assert.equal(r.isMulti, true);
assert.ok(r.analyst_parts.length >= 2);
assert.ok(r.content.length > 0);

// 按标的 / 日期过滤
const byTarget = mockChatSessionList(sessions, { target: "茅台" });
assert.equal(byTarget.length, 1);
const byDate = mockChatSessionList(sessions, { date: DATE });
assert.equal(byDate.length, sessions.length);

// ==================== R10：今日数据扩展卡片（requirements-r10 §二/§三 契约断言） ====================

// ---- F3 市场天气/情绪温度：手算样例（r=0.67→+15；d=75→+14；炸板0.20→+10；
//      连板5→+10；红盘率0.68→+10；50+15+14+10+10+10=109 → clamp 100 → 晴/火热）----
const w = normalizeMarketWeather(buildMockMarketWeather("2026-08-05"));
assert.match(w.date, /^\d{4}-\d{2}-\d{2}$/, "weather.date 必须为 YYYY-MM-DD");
assert.equal(w.weather, "晴", "手算样例：score≥75 → 晴");
assert.equal(w.emotion_score, 100, "手算样例：109 clamp 100（验收用例冻结值）");
assert.equal(w.emotion_label, "火热", "手算样例：score≥80 → 火热");
assert.ok(w.reasons.length >= 5, "依据条目 ≥5（每条中文一句话）");
for (const r of w.reasons) {
  assert.ok(typeof r === "string" && r.length > 0, "reasons 条目必须为非空中文");
}
assert.equal(w.degraded, false);
assert.ok(w.source.length > 0, "source 必须存在");
// 数据缺失态：weather="数据缺失"、emotion_score=null → 前端「—」
const wNone = normalizeMarketWeather({ weather: "数据缺失", emotion_score: null, degraded: true });
assert.equal(wNone.weather, "数据缺失");
assert.equal(wNone.emotion_score, null, "score=null 必须保留 null（禁 0）");
assert.equal(wNone.emotion_label, "");
assert.equal(fmtCount(wNone.emotion_score), "—", "None → 「—」");
assert.equal(wNone.degraded, true, "degraded 可见标注");
assert.equal(normalizeMarketWeather(null).weather, "数据缺失", "空响应兜底为数据缺失态（禁止白屏）");

// ---- F6 纳斯达克：两指数、比率小数、近 60 点、is_today 标记 ----
const nq = normalizeUsNasdaq(buildMockUsNasdaq());
assert.equal(nq.items.length, 2, ".IXIC + .NDX 两指数");
const ix = nq.items[0];
assert.equal(ix.symbol, ".IXIC");
assert.ok(Number.isFinite(ix.close), "close 必须为有限数");
assert.ok(Math.abs(ix.pct_change) < 1, "pct_change 必须为小数（<100%）");
assert.ok(ix.points.length >= 60, "近 60 交易日走势点数 ≥60");
for (const p of ix.points) {
  assert.match(p.date, /^\d{4}-\d{2}-\d{2}$/, "走势点 date 必须为 YYYY-MM-DD");
  assert.ok(Number.isFinite(p.close), "走势点 close 必须为有限数");
}
assert.equal(typeof ix.is_today, "boolean");
assert.equal(fmtPct(ix.pct_change, { signed: true }), `${ix.pct_change >= 0 ? "+" : ""}${(ix.pct_change * 100).toFixed(2)}%`, "比率 ×100 展示");
assert.equal(fmtPct(ix.pct_change), `${(ix.pct_change * 100).toFixed(2)}%`);
// 历史补做：is_today=false（前端显示「非当日」），走势仍保留
const backfill = normalizeUsNasdaq({
  items: [{ symbol: ".IXIC", name: "纳斯达克综合", is_today: false, points: [{ date: "2026-08-04", close: 17234.5 }] }],
});
assert.equal(backfill.items[0].is_today, false);
assert.ok(backfill.items[0].points.length === 1);
// 两指数独立降级：空 items 不白屏
assert.deepEqual(normalizeUsNasdaq({ items: [], degraded: true }).items, []);

// ---- F5 板块温度榜：hot/cold 各 ≤5（不足 5 条按实际）、pct 小数、领涨股 ----
const sh = normalizeSectorHeat(buildMockSectorHeat());
assert.ok(sh.hot.length > 0 && sh.hot.length <= 5, "hot ≤5（不足 5 条按实际）");
assert.ok(sh.cold.length > 0 && sh.cold.length <= 5, "cold ≤5（不足 5 条按实际）");
for (const row of [...sh.hot, ...sh.cold]) {
  assert.ok(row.name, "板块 name 必须存在");
  assert.ok(Number.isFinite(row.pct_change) && Math.abs(row.pct_change) < 1, "pct_change 必须为小数");
  assert.ok(row.leader, "领涨股必须存在");
}
assert.equal(sh.degraded, false);
const shNone = normalizeSectorHeat({ hot: [], cold: [], degraded: true });
assert.equal(shNone.degraded, true);
assert.deepEqual(shNone.hot, [], "整体缺失 → hot/cold 空数组");
// 不足 5 条按实际
const shShort = normalizeSectorHeat({ hot: [{ name: "半导体", pct_change: 0.01, leader: "A" }], cold: [] });
assert.equal(shShort.hot.length, 1, "不足 5 条按实际数量");
assert.equal(shShort.cold.length, 0);

// ---- F2 额外指数：平均股价/微盘股/高股息 三项 ----
const xi = normalizeExtraIndices(buildMockExtraIndices());
assert.deepEqual(xi.items.map((x) => x.name), ["平均股价", "微盘股", "高股息"], "三项指数齐全");
for (const it of xi.items) {
  assert.ok(Number.isFinite(it.close), "close 必须为有限数");
  assert.ok(Number.isFinite(it.pct_change) && Math.abs(it.pct_change) < 1, "pct_change 必须为小数");
  assert.ok(it.code, "通达信指数代码必须存在");
}
assert.equal(normalizeExtraIndices({ items: [], degraded: true }).degraded, true);
assert.equal(fmtPct(xi.items[0].pct_change), `${(xi.items[0].pct_change * 100).toFixed(2)}%`);

// ---- F4 风格坐标：维度 score 0-100 + label + desc；缺失维度 score=null label=数据缺失 ----
const sty = normalizeMarketStyle(buildMockMarketStyle());
assert.ok(sty.large_vs_small.score >= 0 && sty.large_vs_small.score <= 100, "score 必须在 0-100");
assert.ok(["大盘占优", "小盘占优", "均衡"].includes(sty.large_vs_small.label), "label 按规则映射");
assert.ok(sty.large_vs_small.desc.length > 0, "desc 必须存在");
assert.ok(sty.value_vs_growth.score >= 0 && sty.value_vs_growth.score <= 100);
const styNone = normalizeMarketStyle({ large_vs_small: { score: null }, value_vs_growth: null });
assert.equal(styNone.large_vs_small.score, null, "维度数据缺失 → score=null（禁 0）");
assert.equal(styNone.large_vs_small.label, "数据缺失", "维度数据缺失 → label=数据缺失（不整体降级）");
assert.equal(styNone.value_vs_growth.label, "数据缺失");
assert.equal(fmtCount(styNone.large_vs_small.score), "—");

// ---- F9 观察列表：结构 / 增（409 中文）/ 删（404 中文）/ 排序（PUT order）----
const wl0 = normalizeWatchlist(mockWatchlistList());
assert.ok(wl0.length >= 2, "种子列表 ≥2 条");
for (const it of wl0) {
  assert.match(it.code, /^\d{6}$/, "code 必须为 6 位数字");
  assert.ok(it.name, "name 必须存在");
  assert.equal(typeof it.pinned, "boolean", "pinned 必须为布尔（置顶）");
  assert.match(it.created_at, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$/, "created_at 必须为无时区本地 ISO");
}
const added = normalizeWatchlistItem(mockWatchlistAdd("000001", "测试备注"));
assert.equal(added.code, "000001");
assert.equal(added.note, "测试备注");
assert.throws(() => mockWatchlistAdd("000001"), /已在观察列表中/, "重复代码 → 409 中文提示");
mockWatchlistDelete("000001");
assert.throws(() => mockWatchlistDelete("999999"), /不在观察列表中/, "不存在 → 404 中文提示");
const codes = mockWatchlistList().map((x) => x.code);
const reversed = codes.slice().reverse();
mockWatchlistReorder(reversed);
assert.deepEqual(
  mockWatchlistList().map((x) => x.code),
  reversed,
  "PUT order 重排生效（缺失代码忽略）"
);
mockWatchlistReorder(codes);
// 行情消费（/api/data/quote 子集）：None → null → 「—」
const q = normalizeQuote({ code: "600519", name: "贵州茅台", price: 1328.36, pct_change: 0.0123, open: null });
assert.equal(q.code, "600519");
assert.equal(q.price, 1328.36);
assert.equal(q.pct_change, 0.0123);
assert.equal(q.open, null, "None 字段必须保留 null");
assert.equal(fmtCount(q.open), "—");
assert.equal(fmtPct(q.pct_change), "1.23%", "0.0123 小数 → 1.23%");
assert.equal(normalizeQuote({}).price, null, "空响应不白屏");

console.log("ALL NORMALIZE ASSERTIONS PASSED（mock 结构严格对齐 §五 契约）");
