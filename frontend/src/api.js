// API 客户端：fetch 封装 + 离线 mock（仅 DEV 后端不可用时自动降级；结构对齐 §五 契约）
// 也可用 URL 参数 ?offline=1 强制走 mock 演示（无后端时可离线演示全流程）。
import {
  ANALYSTS,
  buildChatMeta,
  buildMockJobResult,
  buildMockExtraIndices,
  buildMockMarketStyle,
  buildMockMarketWeather,
  buildMockRealtime,
  buildMockReviewDetail,
  buildMockSectorHeat,
  isoDate,
  mockChatReply,
  mockChatSessionList,
  mockWatchlistAdd,
  mockWatchlistDelete,
  mockWatchlistList,
  mockWatchlistReorder,
  mockSearchStocks,
  mockStockDetail,
  mockStockQuote,
  mockReviewList,
  nowTime,
  seedMockChatSessions,
  seedMockReviews,
} from "./mock.js";

export class ApiError extends Error {
  constructor(message) {
    super(message);
    this.name = "ApiError";
  }
}

const REQUEST_TIMEOUT_MS = 30000;
// 聊天消息：多分析师交叉为串行 LLM 调用（每轮最长 120s），允许最长 4 分钟
const CHAT_MESSAGE_TIMEOUT_MS = 240000;

// 带超时的 fetch：杜绝无限卡死（AbortController 中止后抛中文超时错误）
async function fetchWithTimeout(path, options = {}, timeoutMs = REQUEST_TIMEOUT_MS) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(path, { ...options, signal: controller.signal });
  } catch (err) {
    if (err && err.name === "AbortError") {
      throw new ApiError(`请求超时（${Math.round(timeoutMs / 1000)} 秒），请检查后端服务后重试`);
    }
    // 网络层错误（浏览器 TypeError "Failed to fetch" 等）统一转中文，禁止英文原文上屏
    throw new ApiError("无法连接后端服务（网络错误），请确认服务已启动、代理已开启后重试");
  } finally {
    clearTimeout(timer);
  }
}

async function errorFrom(res, fallback) {
  let detail = "";
  try {
    const data = await res.json();
    detail = (data && (data.detail || data.message || data.error)) || "";
  } catch {
    /* 非 JSON 错误体 */
  }
  return new ApiError(detail || `${fallback}（HTTP ${res.status}）`);
}

async function requestJson(path, options, timeoutMs = REQUEST_TIMEOUT_MS) {
  let res;
  try {
    res = await fetchWithTimeout(path, options, timeoutMs);
  } catch (err) {
    if (err instanceof ApiError) throw err; // 超时等已带中文信息
    throw new ApiError("无法连接后端服务，请确认服务已启动");
  }
  if (!res.ok) throw await errorFrom(res, "请求失败");
  if (res.status === 204) return undefined;
  return res.json();
}

function forceOffline() {
  try {
    return new URLSearchParams(window.location.search).get("offline") === "1";
  } catch {
    return false;
  }
}

// 仅 DEV 降级；生产环境报错不静默
function canFallback() {
  return import.meta.env.DEV || forceOffline();
}

// 离线 mock 仅用于 DEV/CI 演示，生产走真实后端；与真实契约的差异以 normalize.js 为准
// ---------- 离线 mock 存储（结构完全对齐契约） ----------
const mockReviews = seedMockReviews();
const mockChatSessions = seedMockChatSessions();
const mockJobs = new Map();
let mockSeq = 0;

// ---------- 复盘 ----------
// POST /api/reviews {date, mode, max_rounds?} → {job_id}
export async function startReview(payload) {
  try {
    const res = await fetchWithTimeout("/api/reviews", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw await errorFrom(res, "提交复盘任务失败");
    return await res.json();
  } catch (err) {
    if (canFallback()) return startOfflineJob(payload);
    if (err instanceof ApiError) throw err;
    throw new ApiError("无法连接后端服务，请确认服务已启动");
  }
}

// GET /api/jobs/{job_id}
export function getJob(jobId) {
  if (mockJobs.has(jobId)) return Promise.resolve(pollMockJob(jobId));
  return requestJson(`/api/jobs/${encodeURIComponent(jobId)}`);
}

// GET /api/data/realtime — 盘中实时数据快照（指数/涨跌停/板块/快讯）
export async function getRealtimeData() {
  try {
    return await requestJson("/api/data/realtime", {}, 45000);
  } catch (err) {
    if (canFallback()) return buildMockRealtime();
    throw err;
  }
}

// GET /api/data/quote?code=600519 — 个股实时行情
export async function getStockQuote(code) {
  try {
    return await requestJson(`/api/data/quote?code=${encodeURIComponent(String(code).trim())}`);
  } catch (err) {
    if (canFallback()) return mockStockQuote(code);
    throw err;
  }
}

// GET /api/data/stock/{code}?days=60 — R9 个股详情聚合（实时/日K/分时/分钟K/均线）
export async function getStockDetail(code, days = 60) {
  try {
    return await requestJson(
      `/api/data/stock/${encodeURIComponent(String(code).trim())}?days=${days}`,
      {},
      60000
    );
  } catch (err) {
    if (canFallback()) return mockStockDetail(code);
    throw err;
  }
}

// GET /api/data/search?q=茅台 — 股票名称/代码搜索
export async function searchStocks(q) {
  try {
    const data = await requestJson(`/api/data/search?q=${encodeURIComponent(String(q).trim())}`);
    return Array.isArray(data) ? data : [];
  } catch (err) {
    if (canFallback()) return mockSearchStocks(q);
    throw err;
  }
}

// ---------- M5：股票代码批量转名称（2026-08-12 新增；前端展示用） ----------
// 模块级缓存 + 在途请求合并：多条消息/报告并发渲染时同批代码只发一次请求；
// 名称缺失或接口失败一律返回 {}（渲染层保持代码原样），绝不抛错。
const stockNameCache = new Map(); // code -> {name, t}
const STOCK_NAME_CACHE_TTL_MS = 60000;
let stockNamesInFlight = null; // {key, promise}

// POST /api/data/stock-names {codes: [...]} -> {names: {code: name|null}}
export async function getStockNames(codes) {
  const now = Date.now();
  const seen = new Set();
  const list = [];
  for (const raw of Array.isArray(codes) ? codes : []) {
    const c = String(raw === null || raw === undefined ? "" : raw).trim();
    if (/^\d{6}$/.test(c) && !seen.has(c)) {
      seen.add(c);
      list.push(c);
    }
  }
  const out = {};
  if (list.length === 0) return out;
  const missing = [];
  for (const c of list) {
    const hit = stockNameCache.get(c);
    if (hit && now - hit.t < STOCK_NAME_CACHE_TTL_MS) out[c] = hit.name;
    else missing.push(c);
  }
  if (missing.length === 0) return out;
  const key = [...missing].sort().join(",");
  let promise =
    stockNamesInFlight && stockNamesInFlight.key === key ? stockNamesInFlight.promise : null;
  if (!promise) {
    promise = requestJson("/api/data/stock-names", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ codes: missing }),
    }).catch((err) => {
      // 离线 mock / 后端不可用：保持代码原样，绝不报错
      console.warn(
        canFallback()
          ? "批量名称接口不可用（离线模式），保持代码原样"
          : `批量名称接口请求失败：${(err && err.message) || err}`
      );
      return {};
    });
    stockNamesInFlight = { key, promise };
    promise
      .finally(() => {
        if (stockNamesInFlight && stockNamesInFlight.promise === promise) {
          stockNamesInFlight = null;
        }
      })
      .catch(() => {});
  }
  const data = await promise;
  const names = (data && data.names) || {};
  const done = Date.now();
  for (const c of missing) {
    const n = typeof names[c] === "string" && names[c] ? names[c] : null;
    stockNameCache.set(c, { name: n, t: done });
    out[c] = n;
  }
  return out;
}

// ---------- R10：今日数据扩展卡片（requirements-r10 §三 契约；DEV 降级 mock） ----------

// GET /api/data/market-weather — F3 市场天气/情绪温度
export async function getMarketWeather() {
  try {
    return await requestJson("/api/data/market-weather");
  } catch (err) {
    if (canFallback()) return buildMockMarketWeather();
    throw err;
  }
}

// R12 统一情绪数据源：结构化情绪指标（涨跌家数比/炸板率/连板高度等）。
// 模块级全局缓存（60s TTL）：首页天气卡与复盘展示共用同一份数据，避免重复取数。
let sentimentCache = { t: 0, data: null };
export async function getMarketSentiment() {
  const now = Date.now();
  if (sentimentCache.data && now - sentimentCache.t < 60000) return sentimentCache.data;
  try {
    const d = await requestJson("/api/data/market-sentiment");
    sentimentCache = { t: now, data: d };
    return d;
  } catch (err) {
    if (canFallback()) return buildMockMarketWeather();
    throw err;
  }
}

// GET /api/data/sector-heat — F5 板块温度榜（hot/cold）
export async function getSectorHeat() {
  try {
    return await requestJson("/api/data/sector-heat");
  } catch (err) {
    if (canFallback()) return buildMockSectorHeat();
    throw err;
  }
}

// GET /api/data/extra-indices — F2 额外指数（平均股价/微盘股/高股息）
export async function getExtraIndices() {
  try {
    return await requestJson("/api/data/extra-indices");
  } catch (err) {
    if (canFallback()) return buildMockExtraIndices();
    throw err;
  }
}

// GET /api/data/market-style — F4 风格坐标（大盘vs小盘 / 价值vs成长）
export async function getMarketStyle() {
  try {
    return await requestJson("/api/data/market-style");
  } catch (err) {
    if (canFallback()) return buildMockMarketStyle();
    throw err;
  }
}

// ---------- R10：观察列表（F9；本地文件 data/watchlist.json） ----------

// GET /api/watchlist → [{code, name, note, pinned, created_at}]
export async function getWatchlist() {
  try {
    const data = await requestJson("/api/watchlist");
    return Array.isArray(data) ? data : [];
  } catch (err) {
    if (canFallback()) return mockWatchlistList();
    throw err;
  }
}

// POST /api/watchlist {code, note?} → 200；重复代码 409 中文
export async function addWatchlist(code, note = "") {
  try {
    const res = await fetchWithTimeout("/api/watchlist", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: String(code || "").trim(), note: String(note || "") }),
    });
    if (!res.ok) throw await errorFrom(res, "添加观察失败");
    return await res.json();
  } catch (err) {
    if (canFallback()) return mockWatchlistAdd(code, note);
    if (err instanceof ApiError) throw err;
    throw new ApiError("无法连接后端服务，请确认服务已启动");
  }
}

// DELETE /api/watchlist/{code} → 204 / 404 中文
export async function deleteWatchlist(code) {
  try {
    const res = await fetchWithTimeout(
      `/api/watchlist/${encodeURIComponent(String(code || "").trim())}`,
      { method: "DELETE" }
    );
    if (!res.ok) throw await errorFrom(res, "删除观察失败");
    return undefined; // 204
  } catch (err) {
    if (canFallback()) return mockWatchlistDelete(code);
    if (err instanceof ApiError) throw err;
    throw new ApiError("无法连接后端服务，请确认服务已启动");
  }
}

// PUT /api/watchlist/order {codes: [...]} → 200（重排序，缺失代码忽略）
export async function reorderWatchlist(codes) {
  try {
    const res = await fetchWithTimeout("/api/watchlist/order", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ codes: (Array.isArray(codes) ? codes : []).map((c) => String(c || "").trim()) }),
    });
    if (!res.ok) throw await errorFrom(res, "保存排序失败");
    return await res.json();
  } catch (err) {
    if (canFallback()) return mockWatchlistReorder(codes);
    if (err instanceof ApiError) throw err;
    throw new ApiError("无法连接后端服务，请确认服务已启动");
  }
}

// GET /api/reviews（历史分组列表）
export async function listReviews() {
  try {
    const data = await requestJson("/api/reviews");
    return { data, offline: false };
  } catch (err) {
    if (canFallback()) return { data: mockReviewList(mockReviews), offline: true };
    throw err;
  }
}

// DELETE /api/reviews/{date} → 204 / 404（中文）
// 2026-08-10 修复：R11 R4 每日一份，旧 {date}/{time} 多份结构路由已废弃
// （后端仅 /api/reviews/{date}，带 time 请求返回 405 Method Not Allowed）
export async function deleteReview(date, time) {
  const key = `${date}/${time}`;
  if (mockReviews.has(key)) {
    mockReviews.delete(key);
    return undefined;
  }
  let res;
  try {
    res = await fetchWithTimeout(`/api/reviews/${encodeURIComponent(date)}`, {
      method: "DELETE",
    });
  } catch (err) {
    if (canFallback()) return undefined;
    if (err instanceof ApiError) throw err;
    throw new ApiError("无法连接后端服务，请确认服务已启动");
  }
  if (!res.ok) throw await errorFrom(res, "删除复盘记录失败");
  return undefined; // 204
}

// ---------- R11：自动复盘状态 / 单日复盘（requirements-r11 §四 契约；T2 后端提供）----------
function findMockReviewByDate(date) {
  const key = [...mockReviews.keys()].find((k) => k.startsWith(`${date}/`));
  return key ? mockReviews.get(key) : null;
}

// GET /api/reviews/status?date= → {date, is_trading_day, latest_date, latest_mode,
//   has_today_review, pending_job_id|null}；后端未实现时 DEV 降级为基于 mock 历史推导
export async function getReviewStatus(date) {
  try {
    return await requestJson(`/api/reviews/status?date=${encodeURIComponent(String(date || ""))}`);
  } catch (err) {
    if (canFallback()) return buildMockReviewStatus(date);
    throw err;
  }
}

function buildMockReviewStatus(date) {
  const dates = [...new Set([...mockReviews.keys()].map((k) => k.split("/")[0]))].sort().reverse();
  const latestDate = dates[0] || null;
  const latestKey = latestDate ? [...mockReviews.keys()].find((k) => k.startsWith(`${latestDate}/`)) : null;
  const latestDetail = latestKey ? mockReviews.get(latestKey) : null;
  return {
    date,
    is_trading_day: true,
    latest_date: latestDate,
    latest_mode: (latestDetail && latestDetail.meta && latestDetail.meta.mode) || null,
    has_today_review: dates.includes(date),
    pending_job_id: null,
  };
}

// GET /api/reviews/{date}（R4 单日复盘接口；旧后端返回 404 时由 ReviewTab 降级到历史详情）
export function getReviewByDate(date) {
  // R12 修复：请求优先，mock 仅在离线兜底（此前 mock 优先导致当日复盘 Tab 恒显示演示报告）
  return requestJson(`/api/reviews/${encodeURIComponent(String(date || ""))}`)
    .catch((err) => {
      if (canFallback()) {
        const mock = findMockReviewByDate(date);
        if (mock) return mock;
      }
      throw err;
    });
}

// ---------- R11：交割单分析（T4 契约；后端未就绪时 DEV mock 跑通 UI）----------
const mockTrades = new Map();

// POST /api/trades/upload (multipart) → {trade_id, records, metrics}
export async function uploadTrades(file) {
  const fd = new FormData();
  fd.append("file", file);
  let res;
  try {
    res = await fetchWithTimeout("/api/trades/upload", { method: "POST", body: fd }, 60000);
  } catch (err) {
    if (canFallback()) return createOfflineTrade(file);
    if (err instanceof ApiError) throw err;
    throw new ApiError("无法连接后端服务，请确认服务已启动");
  }
  if (!res.ok) throw await errorFrom(res, "交割单上传失败");
  return await res.json();
}

// GET /api/trades/{trade_id} → {records, metrics, analysis?}
export function getTrades(tradeId) {
  // R12 修复：请求优先，mock 仅在离线兜底（此前 mock 优先导致历史详情被演示数据覆盖）
  return requestJson(`/api/trades/${encodeURIComponent(String(tradeId || ""))}`).catch(
    (err) => {
      if (canFallback()) {
        const t = mockTrades.get(String(tradeId || ""));
        if (t) return t;
      }
      throw err;
    }
  );
}

// GET /api/trades → 历史交割单列表（R12；请求优先，离线 mock 兜底）
export async function getTradesList() {
  try {
    const data = await requestJson("/api/trades");
    const list = Array.isArray(data?.trades) ? data.trades : [];
    return { data: list, offline: false };
  } catch (err) {
    if (canFallback()) {
      const seed = Array.from(mockTrades.keys()).map((id) => ({
        trade_id: id,
        created_at: "",
        records_count: 4,
        has_analysis: false,
      }));
      return { data: seed, offline: true };
    }
    throw err;
  }
}

// POST /api/trades/{trade_id}/analyze → {job_id}（异步，复用 jobs 轮询）或 {report, disclaimer}
export async function analyzeTrades(tradeId) {
  let res;
  try {
    res = await fetchWithTimeout(
      `/api/trades/${encodeURIComponent(String(tradeId || ""))}/analyze`,
      { method: "POST" },
      60000
    );
  } catch (err) {
    if (canFallback()) return buildMockTradeAnalysis(tradeId);
    if (err instanceof ApiError) throw err;
    throw new ApiError("无法连接后端服务，请确认服务已启动");
  }
  if (!res.ok) throw await errorFrom(res, "发起交割单分析失败");
  return await res.json();
}

function createOfflineTrade() {
  const tradeId = `mock-trade-${Date.now()}`;
  const records = [
    { code: "600519", name: "贵州茅台", op_type: "buy", qty: 100, price: 1688.0, fee: 8.44, transfer_fee: 0.17, balance: 0, cash_amount: -168808.61, contract_no: "MOCK-20260808-001" },
    { code: "600519", name: "贵州茅台", op_type: "sell", qty: 100, price: 1712.5, fee: 8.56, transfer_fee: 0.17, balance: 0, cash_amount: 171241.27, contract_no: "MOCK-20260808-002" },
    { code: "002466", name: "天齐锂业", op_type: "buy", qty: 1000, price: 42.3, fee: 21.15, transfer_fee: 0.42, balance: 0, cash_amount: -42321.57, contract_no: "MOCK-20260808-003" },
    { code: "002466", name: "天齐锂业", op_type: "sell", qty: 500, price: 44.1, fee: 11.03, transfer_fee: 0.22, balance: 0, cash_amount: 22038.75, contract_no: "MOCK-20260808-004" },
  ];
  const metrics = {
    trade_count: 4,
    win_rate: 0.5,
    profit_loss_ratio: 1.35,
    twr: 0.032,
    total_pnl: 8149.84,
    max_drawdown: 0.011,
    avg_holding_days: 2.5,
    open_trades: 1,
  };
  const t = { trade_id: tradeId, records, metrics, analysis: null };
  mockTrades.set(tradeId, t);
  return t;
}

function buildMockTradeAnalysis() {
  return {
    report:
      "### 交割单分析报告（离线演示）\n\n" +
      "本次为 DEV mock 数据（后端 T4 接口未就绪），用于验证前端契约：\n\n" +
      "- 共解析 **4** 笔成交，覆盖 **2** 只标的（贵州茅台 / 天齐锂业）。\n" +
      "- 胜率 **50.0%**，盈亏比 **1.35**，TWR **3.2%**。\n" +
      "- 行为画像：持仓周期偏短（平均 2.5 天），止盈纪律较好；建议关注单笔仓位集中度。\n\n" +
      "> 仅供参考，不构成投资建议。",
    disclaimer: "仅供参考，不构成投资建议",
  };
}

// ---------- R12-A3：分析师模拟账户（requirements-r12 契约；请求优先，mock 仅离线兜底）----------
// GET /api/accounts → [{skill_id, name, cash, total_assets, total_return_rate,
//                        today_trades_count, updated_at}]
export async function getAccounts() {
  try {
    const data = await requestJson("/api/accounts");
    // 后端契约：{accounts: [...], disclaimer}；兼容裸数组
    const list = Array.isArray(data?.accounts)
      ? data.accounts
      : Array.isArray(data)
        ? data
        : [];
    return { data: list, offline: false };
  } catch (err) {
    if (canFallback()) return { data: mockAccountSummaries(), offline: true };
    throw err;
  }
}

// GET /api/accounts/{skill_id} → {skill_id, name, cash, positions[], trades[], snapshots[],
//                                   total_return_rate}
// R12 修复沿用：请求优先，mock 仅在离线兜底（后端未就绪时 DEV 用 mock 跑通 UI 链路）
export function getAccountDetail(skillId) {
  return requestJson(`/api/accounts/${encodeURIComponent(String(skillId || "").trim())}`).catch(
    (err) => {
      if (canFallback()) return mockAccountDetail(String(skillId || ""));
      throw err;
    }
  );
}

// ---- DEV mock：5 位分析师、初始 10 万、示例交易与资产曲线（仅离线兜底）----
function acctHash(s) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i += 1) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

function acctRng(seed) {
  let a = seed >>> 0;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// 回测区间 2026-07-15 → 2026-08-07（约 18 个交易日，跳过周末）
const ACCT_DAYS = (() => {
  const days = [];
  const d = new Date("2026-07-15T00:00:00");
  const end = new Date("2026-08-07T00:00:00");
  const pad = (x) => String(x).padStart(2, "0");
  while (d <= end) {
    const wd = d.getDay();
    if (wd !== 0 && wd !== 6) {
      days.push(`${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`);
    }
    d.setDate(d.getDate() + 1);
  }
  return days;
})();

const ACCT_POOL = [
  { code: "600519", name: "贵州茅台" },
  { code: "002466", name: "天齐锂业" },
  { code: "300750", name: "宁德时代" },
  { code: "601127", name: "赛力斯" },
  { code: "000858", name: "五粮液" },
  { code: "688981", name: "中芯国际" },
];

const ACCT_REJECT_REASONS = [
  "可用资金不足：买入金额（含费用）超过可用现金（用了太多的钱）",
  "违反 T+1：当日买入的股票当日不可卖出",
  "涨停封板买入拒绝（目标价 ≥ 涨停价，封板买不进）",
  "数量非 100 股整数倍",
  "卖出数量超过可用持仓（裸卖空拒绝）",
];

function acctSnapshots(skillId, rng) {
  // 确定性资产曲线：初始 10 万，回测区间内漂移 + 波浪
  const drift = (acctHash(`${skillId}:drift`) % 3000) / 10000 - 0.1; // -10% ~ +20%
  const waveA = 0.4 + rng() * 0.8;
  const waveB = 1.1 + rng() * 1.4;
  const cashRatio = 0.28 + rng() * 0.3;
  let realized = 0;
  return ACCT_DAYS.map((date, i) => {
    const progress = i / Math.max(1, ACCT_DAYS.length - 1);
    // 首日严格等于初始资金 10 万（资产曲线从起点出发）
    const wave =
      i === 0
        ? 0
        : Math.sin(i / waveA) * 0.012 + Math.cos(i / waveB) * 0.009 + (rng() - 0.5) * 0.006;
    const total = Math.round(100000 * (1 + drift * progress + wave));
    const cash = Math.max(0, Math.round(total * cashRatio));
    const positionMv = Math.max(0, total - cash);
    realized = Math.round((total - 100000) * 0.3);
    return {
      date,
      cash,
      position_mv: positionMv,
      total_assets: total,
      realized_pnl: realized,
    };
  });
}

function acctPositions(skillId, rng) {
  const count = 2 + Math.floor(rng() * 3); // 2-4 只持仓
  const picked = ACCT_POOL.slice()
    .sort(() => rng() - 0.5)
    .slice(0, count);
  return picked.map((s) => {
    const qty = (1 + Math.floor(rng() * 8)) * 100;
    const base = 12 + rng() * 90;
    const avgCost = Math.round(base * 100) / 100;
    const price = Math.round(base * (0.93 + rng() * 0.16) * 100) / 100;
    // 当日买入的仓位 T+1 冻结：可用 < 持仓
    const availableQty = Math.floor(qty * (0.5 + rng() * 0.5) / 100) * 100;
    return {
      code: s.code,
      name: s.name,
      qty,
      available_qty: availableQty,
      avg_cost: avgCost,
      price,
      market_value: Math.round(qty * price * 100) / 100,
      pnl: Math.round((price - avgCost) * qty * 100) / 100,
    };
  });
}

function acctTrades(skillId, rng) {
  const trades = [];
  const days = ACCT_DAYS.slice(-4);
  const round2v = (v) => Math.round(v * 100) / 100;
  days.forEach((date) => {
    const n = 1 + Math.floor(rng() * 3);
    for (let k = 0; k < n; k += 1) {
      const s = ACCT_POOL[Math.floor(rng() * ACCT_POOL.length)];
      const side = rng() > 0.42 ? "buy" : "sell";
      const price = round2v(12 + rng() * 90);
      const qty = (1 + Math.floor(rng() * 9)) * 100;
      const rejected = rng() < 0.28;
      const time = `${String(9 + Math.floor(rng() * 5)).padStart(2, "0")}${String(Math.floor(rng() * 60)).padStart(2, "0")}00`;
      trades.push({
        date,
        time,
        code: s.code,
        name: s.name,
        side,
        target_price: round2v(price * (side === "buy" ? 1 - rng() * 0.01 : 1 + rng() * 0.01)),
        qty,
        price: rejected ? null : price,
        amount: rejected ? null : Math.round(qty * price * 100) / 100,
        fee: rejected ? null : round2v(qty * price * 0.00025),
        stamp_tax: rejected || side !== "sell" ? null : round2v(qty * price * 0.0005),
        transfer_fee: rejected ? null : round2v(qty * price * 0.00001),
        status: rejected ? "拒绝" : "成交",
        reject_reason: rejected
          ? ACCT_REJECT_REASONS[Math.floor(rng() * ACCT_REJECT_REASONS.length)]
          : "",
      });
    }
  });
  return trades.reverse();
}

function mockAccountSummaries() {
  return ANALYSTS.map((a) => {
    const rng = acctRng(acctHash(`acct:${a.id}`));
    const snapshots = acctSnapshots(a.id, rng);
    const last = snapshots[snapshots.length - 1] || { total_assets: 100000, cash: 100000 };
    const today = acctTrades(a.id, rng).filter((t) => t.date === ACCT_DAYS[ACCT_DAYS.length - 1]);
    return {
      skill_id: a.id,
      name: a.skill_name,
      cash: last.cash,
      total_assets: last.total_assets,
      total_return_rate: Math.round((last.total_assets / 100000 - 1) * 10000) / 10000,
      today_trades_count: today.length,
      updated_at: `${ACCT_DAYS[ACCT_DAYS.length - 1]} 15:05:00`,
    };
  });
}

function mockAccountDetail(skillId) {
  const a = ANALYSTS.find((x) => x.id === skillId);
  if (!a) return undefined;
  const rng = acctRng(acctHash(`acct:${a.id}`));
  const snapshots = acctSnapshots(a.id, rng);
  const last = snapshots[snapshots.length - 1] || { total_assets: 100000 };
  return {
    skill_id: a.id,
    name: a.skill_name,
    cash: last.cash,
    positions: acctPositions(a.id, rng),
    trades: acctTrades(a.id, rng),
    snapshots,
    total_return_rate: Math.round((last.total_assets / 100000 - 1) * 10000) / 10000,
    updated_at: `${ACCT_DAYS[ACCT_DAYS.length - 1]} 15:05:00`,
  };
}

// ---------- 聊天 ----------
// POST /api/chat/sessions
export async function createChatSession(payload) {
  try {
    const res = await fetchWithTimeout("/api/chat/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw await errorFrom(res, "创建会话失败");
    return await res.json();
  } catch (err) {
    if (canFallback()) return createOfflineChatSession(payload);
    if (err instanceof ApiError) throw err;
    throw new ApiError("无法连接后端服务，请确认服务已启动");
  }
}

// SSE 流式聊天：POST /api/chat/sessions/{id}/messages/stream
// onEvent(event, payload)：meta / user_msg / analyst_start / analyst_delta / analyst_end /
//                         summary_start / summary_delta / summary_end / done / error
// 通用透传：stage {stage, note} 等事件按 R8 §五 契约原样上抛（前端自行渲染阶段提示）；
// SSE 注释行（如 15s 空闲保活 `: keepalive`）一律跳过，不产生事件。

// 在途聊天轮次登记（模块级，跨组件挂载存活）：
// 用户切走再回来时，聊天视图据此轮询接续「完整流程」——后端 worker 会跑完整轮并落盘，
// 前端只要认领在途轮次并在回来时同步结果即可（2026-08-04 稳定性修复）。
const chatStreams = new Set();
// R11-R10：在途标记同时写入 sessionStorage（key 形如 chat_streaming_{sessionId}），
// 整页刷新/浏览器后台回收后模块级标记丢失，由 ChatView 挂载时扫描恢复（见 ChatView.jsx）。
const STREAM_SS_PREFIX = "chat_streaming_";
function ssGet(key) {
  try {
    return window.sessionStorage.getItem(key);
  } catch {
    return null;
  }
}
function ssSet(key, value) {
  try {
    window.sessionStorage.setItem(key, value);
  } catch {
    /* 隐私模式等不可用时仅保留模块级标记 */
  }
}
function ssRemove(key) {
  try {
    window.sessionStorage.removeItem(key);
  } catch {
    /* 忽略存储异常 */
  }
}

export function markChatStreaming(sessionId) {
  if (!sessionId) return;
  chatStreams.add(sessionId);
  ssSet(`${STREAM_SS_PREFIX}${sessionId}`, "1");
}

export function markChatStreamDone(sessionId) {
  if (!sessionId) return;
  chatStreams.delete(sessionId);
  ssRemove(`${STREAM_SS_PREFIX}${sessionId}`);
}

export function isChatStreaming(sessionId) {
  return !!sessionId && chatStreams.has(sessionId);
}

// R11-R10：扫描 sessionStorage 中的在途标记（供 ChatView 挂载时恢复轮询）
export function restoreChatStreamingFromStorage() {
  const ids = [];
  try {
    for (let i = 0; i < window.sessionStorage.length; i += 1) {
      const key = window.sessionStorage.key(i);
      if (key && key.startsWith(STREAM_SS_PREFIX)) {
        ids.push(key.slice(STREAM_SS_PREFIX.length));
      }
    }
  } catch {
    return ids;
  }
  return ids;
}

export async function streamChatMessage(sessionId, content, onEvent) {
  markChatStreaming(sessionId);
  const session = mockChatSessions.find((s) => s.session_id === sessionId);
  if (session) return mockStreamChatMessage(session, content, onEvent);

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), CHAT_MESSAGE_TIMEOUT_MS);
  let res;
  try {
    res = await fetch(`/api/chat/sessions/${encodeURIComponent(sessionId)}/messages/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
      signal: controller.signal,
    });
  } catch (err) {
    clearTimeout(timer);
    if (canFallback()) return mockStreamChatMessage(sessionId, content, onEvent);
    markChatStreamDone(sessionId); // 连接失败且无 mock：无在途轮次可认领
    if (err && err.name === "AbortError") {
      throw new ApiError(`请求超时（${Math.round(CHAT_MESSAGE_TIMEOUT_MS / 1000)} 秒），请检查后端服务后重试`);
    }
    throw new ApiError("无法连接后端服务（网络错误），请确认服务已启动、代理已开启后重试");
  }
  clearTimeout(timer);
  if (!res.ok) {
    markChatStreamDone(sessionId); // 请求被拒（400/404）：轮次未开始
    throw await errorFrom(res, "发送消息失败");
  }
  if (!res.body) throw new ApiError("后端不支持流式响应，请刷新后重试");

  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      let ev = "message";
      let data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith(":")) continue; // SSE 注释行（: keepalive 等），跳过
        else if (line.startsWith("event:")) ev = line.slice(6).trim();
        else if (line.startsWith("data:")) data = line.slice(5).trim();
      }
      if (data) {
        let payload;
        try {
          payload = JSON.parse(data);
        } catch {
          payload = { raw: data };
        }
        if (ev === "done" || ev === "error") markChatStreamDone(sessionId);
        onEvent(ev, payload);
      }
    }
  }
}

// 生成 mock 综合总结文本（summary_delta 逐字流式演示用）
function buildMockSummary(meta, userQuestion) {
  const target = String((meta && (meta.target_name || meta.target)) || "标的");
  const names = (meta && meta.analysts && meta.analysts.length
    ? meta.analysts
    : [{ skill_name: "分析师" }]
  ).map((a) => (a && (a.skill_name || a.name)) || "分析师");
  const q = String(userQuestion || "").replace(/\s+/g, " ").trim();
  const brief = q.length > 40 ? `${q.slice(0, 40)}…` : q;
  return (
    `针对「${target}」的「${brief}」，${names.join("、")}已结合当前数据给出分项判断，` +
    "核心结论以上方汇总为准，最终操作请结合大盘情绪与板块轮动。以上内容仅供参考，不构成投资建议。"
  );
}

// 离线 mock：模拟 R8 六步流水线事件序列（结构对齐 §五：stage parse/need/fetch → analyst_* → stage summarize → summary_delta* → summary_end → done）
function mockStreamChatMessage(session, content, onEvent) {
  const reply = mockChatReply(session.meta, content);
  const [userMsg, assistantMsg] = reply;
  const sid = typeof session === "string" ? session : session.session_id;
  const meta = typeof session === "string" ? { target_type: "stock", target: "mock", analysts: [] } : session.meta;
  onEvent("meta", { session_id: sid });
  onEvent("user_msg", userMsg);
  if (typeof session !== "string") session.messages.push(userMsg);
  const text = assistantMsg.content || "";
  const name = assistantMsg.analyst_name || (meta.analysts && meta.analysts[0]) || "分析师";
  const summary = buildMockSummary(meta, content);
  const step = Math.max(3, Math.ceil(text.length / 25));
  const summaryStep = Math.max(3, Math.ceil(summary.length / 12));
  const events = [
    ["stage", { stage: "parse", note: "正在解析问题…" }],
    ["stage", { stage: "need", note: "分析师正在确认所需数据…" }],
    ["stage", { stage: "fetch", note: "正在获取数据…" }],
    ["analyst_start", { index: 0, name }],
    ...Array.from({ length: Math.ceil(text.length / step) }, (_, i) => [
      "analyst_delta",
      { index: 0, name, delta: text.slice(i * step, (i + 1) * step) },
    ]),
    ["analyst_end", { index: 0, name, content: text }],
    ["stage", { stage: "summarize", note: "正在综合总结…" }],
    ["summary_start", {}],
    ...Array.from({ length: Math.ceil(summary.length / summaryStep) }, (_, i) => [
      "summary_delta",
      { delta: summary.slice(i * summaryStep, (i + 1) * summaryStep) },
    ]),
    ["summary_end", { content: summary }],
  ];
  const donePayload = {
    session_id: sid,
    messages: typeof session !== "string" ? session.messages : [userMsg, assistantMsg],
    disclaimer: typeof session !== "string" ? session.disclaimer || "仅供参考，不构成投资建议" : "仅供参考，不构成投资建议",
  };
  events.push(["done", donePayload]);

  return new Promise((resolve) => {
    let i = 0;
    const timer = setInterval(() => {
      if (i < events.length) {
        const [ev, pl] = events[i];
        if (ev === "done" && typeof session !== "string") session.messages.push(assistantMsg);
        onEvent(ev, pl);
        if (ev === "done" || ev === "error") markChatStreamDone(sid);
        i += 1;
      } else {
        clearInterval(timer);
        resolve();
      }
    }, 45);
  });
}

// GET /api/chat/sessions?target=&date=
export async function listChatSessions(filters = {}) {
  try {
    const q = new URLSearchParams();
    if (filters.target) q.set("target", String(filters.target));
    if (filters.date) q.set("date", String(filters.date));
    const suffix = q.toString() ? `?${q.toString()}` : "";
    const data = await requestJson(`/api/chat/sessions${suffix}`);
    return { data, offline: false };
  } catch (err) {
    if (canFallback()) return { data: mockChatSessionList(mockChatSessions, filters), offline: true };
    throw err;
  }
}

// GET /api/chat/sessions/{id} → {meta, messages[], disclaimer}
export function getChatSession(id) {
  const session = mockChatSessions.find((s) => s.session_id === id);
  if (session) return Promise.resolve(session);
  return requestJson(`/api/chat/sessions/${encodeURIComponent(id)}`);
}

// DELETE /api/chat/sessions/{id} → 204 / 404（中文）
export async function deleteChatSession(id) {
  const idx = mockChatSessions.findIndex((s) => s.session_id === id);
  if (idx >= 0) {
    mockChatSessions.splice(idx, 1);
    return undefined;
  }
  let res;
  try {
    res = await fetchWithTimeout(`/api/chat/sessions/${encodeURIComponent(id)}`, { method: "DELETE" });
  } catch (err) {
    if (canFallback()) return undefined;
    if (err instanceof ApiError) throw err;
    throw new ApiError("无法连接后端服务，请确认服务已启动");
  }
  if (!res.ok) throw await errorFrom(res, "删除会话失败");
  return undefined;
}

// ---------- 离线 mock 任务（模拟进度，仅 DEV 降级使用） ----------
const MOCK_STEPS = [
  { stage: "fetch", pct: 8, msg: "正在模式化取数（指数/涨跌停/板块/情绪…）…" },
  { stage: "fetch", pct: 16, msg: "取数完成：主要数据块就绪，竞价块走降级链标注" },
  { stage: "news", pct: 24, msg: "正在解析财经资讯与经济日历…" },
  { stage: "trend", pct: 32, done: 1, msg: "趋势派 1/2 · 阿狼 完成点评" },
  { stage: "trend", pct: 40, done: 2, msg: "趋势派 2/2 · 爱在冰川 完成点评" },
  { stage: "sentiment", pct: 50, done: 1, msg: "情绪派 1/3 · 炒股养家 完成点评" },
  { stage: "sentiment", pct: 58, done: 2, msg: "情绪派 2/3 · 铁锤狂砸盘 完成点评" },
  { stage: "sentiment", pct: 66, done: 3, msg: "情绪派 3/3 · 拔小弦 完成点评" },
  { stage: "host", pct: 76, msg: "主持人正在汇总各方观点…" },
  { stage: "report", pct: 97, msg: "正在生成 Markdown 报告（强制附加免责声明）…" },
  { stage: "done", pct: 100, msg: "复盘完成" },
];

function startOfflineJob(payload) {
  const jobId = `mock-${Date.now()}-${mockSeq++}`;
  mockJobs.set(jobId, { step: -1, payload: { ...payload } });
  return { job_id: jobId, offline: true };
}

function pollMockJob(jobId) {
  const job = mockJobs.get(jobId);
  if (!job) throw new ApiError("演示任务不存在");
  job.step += 1;
  const idx = Math.min(job.step, MOCK_STEPS.length - 1);
  const s = MOCK_STEPS[idx];
  if (idx === MOCK_STEPS.length - 1) {
    if (!job.recordId) {
      const payload = { ...job.payload, time: nowTime() };
      const detail = buildMockReviewDetail(payload);
      const result = buildMockJobResult(payload);
      job.recordId = result.record_id;
      mockReviews.set(`${detail.meta.date}/${detail.meta.time}`, detail);
      job.result = result;
    }
    return {
      job_id: jobId,
      status: "done",
      stage: "done",
      pct: 100,
      message: "复盘完成",
      analysts_done: 5,
      analysts_total: 5,
      error: null,
      result: job.result,
      offline: true,
    };
  }
  return {
    job_id: jobId,
    status: "running",
    stage: s.stage,
    pct: s.pct,
    message: s.msg,
    analysts_done: s.done || 0,
    analysts_total: 5,
    error: null,
    result: null,
    offline: true,
  };
}

function createOfflineChatSession(payload) {
  // 会话 ID 对齐后端契约："{YYYY-MM-DD}_{HHMMSS}"（同一秒冲突顺延 1 秒，对齐 I3 storage）
  const pad2 = (x) => String(x).padStart(2, "0");
  let sessionId = `${isoDate()}_${nowTime()}`;
  let guard = 0;
  while (mockChatSessions.some((s) => s.session_id === sessionId) && guard < 60) {
    guard += 1;
    const d = new Date();
    d.setSeconds(d.getSeconds() + guard);
    sessionId = `${isoDate()}_${pad2(d.getHours())}${pad2(d.getMinutes())}${pad2(d.getSeconds())}`;
  }
  const meta = buildChatMeta(payload);
  const session = {
    session_id: sessionId,
    meta,
    messages: [],
    disclaimer: "仅供参考，不构成投资建议",
  };
  // 创建后自动生成一条欢迎回复（多分析师交叉演示）
  const welcome = mockChatReply(meta, "请先给出一段开场分析");
  session.messages.push(...welcome);
  mockChatSessions.push(session);
  return { session_id: sessionId, offline: true };
}

export { ANALYSTS };
