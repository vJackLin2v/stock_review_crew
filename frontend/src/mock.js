// 离线 mock 仅用于 DEV/CI 演示，生产走真实后端；与真实契约的差异以 normalize.js 为准
// 离线 mock 数据（仅 DEV 降级 / 无后端演示用）
// 结构严格对齐 requirements §五 API 契约：
//   比率一律小数（0.5=50%）、None=null、日期 YYYY-MM-DD、时间点 HHMMSS、
//   响应恒含 disclaimer、meta.degraded[] 可见标注。
import { MODES, modeLabel } from "./modes.js";

export const DEFAULT_DISCLAIMER = "仅供参考，不构成投资建议";

// 分析师池（趋势派 2 人 + 情绪派 3 人，聊天卡片多选用）
export const ANALYSTS = [
  { id: "alang", skill_name: "阿狼", role: "趋势波段" },
  { id: "bingchuan", skill_name: "爱在冰川", role: "低吸" },
  { id: "baxiaoxian", skill_name: "拔小弦", role: "短线" },
  { id: "yangjia", skill_name: "炒股养家", role: "情绪" },
  { id: "tiechui", skill_name: "铁锤狂砸盘", role: "实战" },
];

const pad = (x) => String(x).padStart(2, "0");

export function isoDate(d = new Date()) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function nowTime() {
  const d = new Date();
  return `${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
}

// 无时区本地 ISO（后端 datetime.now().isoformat(timespec="seconds")，如 2026-08-03T10:30:00）
export function localIso(d = new Date()) {
  return `${isoDate(d)}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function addDays(base, days) {
  const d = new Date(base);
  d.setDate(d.getDate() + days);
  return d;
}

function hashStr(s) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i += 1) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

function mulberry32(seed) {
  let a = seed >>> 0;
  return function () {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const round4 = (v) => Math.round(v * 1e4) / 1e4;
const round2 = (v) => Math.round(v * 100) / 100;

// 各模式默认时间点（HHMMSS，演示用）
const MODE_TIME = {
  pre_market: "085000",
  auction: "092000",
  intraday_am: "103000",
  noon: "123000",
  intraday_pm: "140000",
  close: "150500",
};

const SECTOR_POOL = [
  "半导体", "券商", "银行", "光伏设备", "白酒", "创新药",
  "算力租赁", "机器人", "汽车整车", "电力", "军工电子", "房地产",
];

function buildSnapshot(date, mode, rng) {
  // 指数分时：09:30–15:00 每 30 分钟一个点
  const indexNames = mode === "pre_market" ? ["上证指数（昨收参考）", "创业板指（昨收参考）"] : ["上证指数", "创业板指"];
  const indexMinute = indexNames.map((name, k) => {
    const base = k === 0 ? 3050 + Math.floor(rng() * 80) : 2050 + Math.floor(rng() * 60);
    const drift = round2((rng() - 0.42) * 1.6); // 全天累计涨跌（%）
    const points = [];
    for (let i = 0; i <= 12; i += 1) {
      const hh = 9 + Math.floor((30 + i * 30) / 60);
      const mm = (30 + i * 30) % 60;
      const wave = Math.sin(i / 2.2 + k * 1.7) * (rng() * 0.35 + 0.15);
      const progress = i / 12;
      const value = round2(base * (1 + drift / 100 * progress + wave / 100));
      points.push({ time: `${pad(hh)}:${pad(mm)}`, value });
    }
    return { name, points };
  });

  // 涨跌停梯队
  const upLadder = [
    { label: "首板", count: 38 + Math.floor(rng() * 18), stocks: ["示例涨停股A", "示例涨停股B", "示例涨停股C"] },
    { label: "2板", count: 8 + Math.floor(rng() * 7) },
    { label: "3板", count: 3 + Math.floor(rng() * 4) },
    { label: "4板", count: 1 + Math.floor(rng() * 3) },
    { label: "5板+", count: Math.floor(rng() * 3) },
  ];
  const downLadder = [
    { label: "首板", count: 2 + Math.floor(rng() * 6) },
    { label: "2板", count: Math.floor(rng() * 3) },
    { label: "3板", count: Math.floor(rng() * 2) },
    { label: "4板", count: Math.floor(rng() * 1) },
    { label: "5板+", count: 0 },
  ];

  // 板块涨幅（小数，正红负绿）；P1-K：跌幅榜 sectors_bottom 同步样例
  const sectorPool = SECTOR_POOL.slice()
    .sort(() => rng() - 0.5)
    .slice(0, 10)
    .map((name, i) => ({
      name,
      pct_change: round4((rng() - 0.38) * 0.065),
      leader: `领涨示例${i + 1}`,
    }));
  const sectors = [...sectorPool].sort((a, b) => b.pct_change - a.pct_change);
  const sectors_bottom = [...sectorPool]
    .sort((a, b) => a.pct_change - b.pct_change)
    .slice(0, 5);

  // 情绪指标（口径 §六.5：炸板率=炸板÷摸板、红盘率、连板率、核按钮率、昨日涨停平均收益）
  const upCount = 2800 + Math.floor(rng() * 700);
  const downCount = 1800 + Math.floor(rng() * 600);
  const limitUp = 45 + Math.floor(rng() * 35);
  const limitDown = 4 + Math.floor(rng() * 8);
  const touched = limitUp + 12 + Math.floor(rng() * 18);
  const sentiment = {
    up_count: upCount,
    down_count: downCount,
    limit_up_count: limitUp,
    limit_down_count: limitDown,
    red_rate: round4(upCount / (upCount + downCount)),
    continue_rate: round4(0.18 + rng() * 0.25),
    break_rate: round4((touched - limitUp) / touched),
    button_rate: round4(0.05 + rng() * 0.16),
    avg_return: round4((rng() - 0.35) * 0.09),
    up_down_ratio: round4(upCount / downCount),
    source: "东财/Tushare 计算（mock 演示）",
    degraded: ["炸板率按当日摸板数估算（mock 演示）"],
  };
  // R11-7：涨跌分布（分布区间柱状图）
  const breadth = {
    total: upCount + downCount + 120,
    up: upCount,
    down: downCount,
    flat: 120,
    limit_up: limitUp,
    limit_down: limitDown,
    zhaban_rate: round4((touched - limitUp) / touched),
    distribution: {
      "涨停": limitUp,
      "跌停": limitDown,
      "涨5%-10%": 90 + Math.floor(rng() * 60),
      "涨2%-5%": 380 + Math.floor(rng() * 200),
      "涨0%-2%": Math.floor(upCount * 0.62),
      "平盘": 120,
      "跌0%-2%": Math.floor(downCount * 0.76),
      "跌2%-5%": 220 + Math.floor(rng() * 120),
      "跌5%以上": 30 + Math.floor(rng() * 25),
    },
    source: "Tushare 计算（mock 演示）",
    degraded: [],
  };

  return {
    index_minute: indexMinute,
    limit_ladder: { up: upLadder, down: downLadder },
    sectors,
    sectors_bottom,
    sentiment,
    breadth,
    source: "东财/同花顺/Tushare（mock 演示数据）",
    degraded: ["竞价数据源未配置 KAIPANLA_COOKIE，竞价块走降级链标注（mock 演示）"],
  };
}

function buildReport(date, mode, snapshot, meta) {
  const s = snapshot.sentiment;
  const idx = snapshot.index_minute[0];
  const first = idx.points[0];
  const last = idx.points[idx.points.length - 1];
  const indexPct = first && last ? ((last.value - first.value) / first.value) : 0;
  const top = snapshot.sectors.slice(0, 3);
  const pct = (v) => `${(v * 100).toFixed(2)}%`;
  return [
    `# ${modeLabel(mode)} · ${date}`,
    "",
    `> ${meta.summary}`,
    "",
    "## 一、市场概览",
    "",
    `- 指数：${idx.name} 收于 **${last ? last.value.toFixed(2) : "—"}**，较开盘 ${indexPct >= 0 ? "+" : ""}${pct(indexPct)}；`,
    `- 涨跌家数：上涨 **${s.up_count}** 家 / 下跌 ${s.down_count} 家，涨跌家数比 ${s.up_down_ratio != null ? `${s.up_down_ratio.toFixed(2)} : 1` : "—"}；`,
    `- 涨停 ${s.limit_up_count} 家 / 跌停 ${s.limit_down_count} 家，炸板率 ${pct(s.break_rate)}，连板率 ${pct(s.continue_rate)}。`,
    "",
    "## 二、板块与资金",
    "",
    ...top.map((x, i) => `- 第 ${i + 1} 名：**${x.name}** ${x.pct_change >= 0 ? "+" : ""}${pct(x.pct_change)}，领涨股 ${x.leader}`),
    "",
    "## 三、情绪与梯队",
    "",
    `- 昨日涨停今日平均收益 **${pct(s.avg_return)}**，红盘率 ${pct(s.red_rate)}，核按钮率 ${pct(s.button_rate)}；`,
    `- 涨停梯队：首板 ${snapshot.limit_ladder.up[0].count} 家、2板 ${snapshot.limit_ladder.up[1].count} 家、3板以上 ${snapshot.limit_ladder.up.slice(2).reduce((a, b) => a + b.count, 0)} 家。`,
    "",
    "## 四、操作计划",
    "",
    "- 关注梯队高度与板块持续性，优先低吸龙头分歧点；",
    "- 若红盘率与连板率同步走强，可适度提升仓位；炸板率抬升时降低追高频率；",
    "- 严格止损纪律，仓位上限不超过单票 20%。",
    "",
    `> 本报告由多智能体复盘系统自动生成，${DEFAULT_DISCLAIMER}。市场有风险，入市需谨慎。`,
  ].join("\n");
}

function buildAnalysts(mode, degraded, rng) {
  const base = [
    {
      skill_name: "趋势派·阿狼",
      skill_id: "trend_zhou",
      analysis: "**趋势视角：** 指数分时重心上移，午后若回踩不破早盘低点则趋势延续。\n\n- 板块轮动较快，资金更偏向低位补涨方向；\n- 连板梯队保持完整，高度板回封说明接力情绪尚可。",
      suggestion: "关注指数 5 日均线得失，回踩不破可持有；破位则先降仓避险。",
      tags: ["顺势而为", "均线纪律"],
    },
    {
      skill_name: "趋势派·爱在冰川",
      skill_id: "trend_lin",
      analysis: "**结构视角：** 今日量能较昨日温和放大，指数波动区间收敛，属于震荡偏强结构。\n\n- 领涨板块由题材切换至权重，说明资金在做高低切换；\n- 尾盘若守住分时均价线，明日仍有惯性。",
      suggestion: "高低切换阶段避免追高当日已大涨板块，优先潜伏次日轮动方向。",
      tags: ["结构分析", "高低切换"],
    },
    {
      skill_name: "情绪派·炒股养家",
      skill_id: "sentiment_kai",
      analysis: `**情绪量化：** 红盘率 ${(0.647 * 100).toFixed(1)}%、连板率 ${(0.31 * 100).toFixed(1)}%，情绪处于中高位。\n\n- 炸板率偏高，说明分歧加大，接力需谨慎；\n- 昨日涨停今日平均收益转正，赚钱效应尚可。`,
      suggestion: "情绪高位分歧期，控制连板接力仓位，优先做首板与低吸。",
      tags: ["数据驱动", "情绪周期"],
    },
    {
      skill_name: "情绪派·铁锤狂砸盘",
      skill_id: "sentiment_chen",
      analysis: "**风险视角：** 跌停家数与核按钮率可控，但高位股尾盘有松动迹象。\n\n- 若明日高标集体断板，情绪将进入退潮期；\n- 当前仓位建议不超过六成，留出应对退潮的现金。",
      suggestion: "退潮信号确认前不加仓，若高标核按钮率抬升则果断离场。",
      tags: ["风险第一", "仓位管理"],
    },
    {
      skill_name: "情绪派·拔小弦",
      skill_id: "sentiment_xiong",
      analysis: "**盘感点评：** 今天市场属于「指数稳、个股浪」的格局，涨停家数不少但连板梯度一般。\n\n- 炸板率不低说明追高资金在挨打，低吸资金在吃肉；\n- 这种行情最怕手痒乱追，管住手就赢了一半。",
      suggestion: "次日优先看竞价情绪：高开太多不追，低开缩量可吸。",
      tags: ["气氛组", "盘感"],
    },
  ];
  return base.map((a) => ({
    ...a,
    analysis:
      degraded.length > 0
        ? `${a.analysis}\n\n> ⚠️ 降级说明：本次复盘部分数据源走降级链（${degraded.join("；")}），以上分析基于可得数据。`
        : a.analysis,
  }));
}

// 改动4：buildDebate 已移除（后端 debate_history 恒空，前端不再展示辩论记录）

// 生成一份完整复盘详情（对齐 GET /api/reviews/{date}/{time} 响应）
export function buildMockReviewDetail({ date, mode, time = null, max_rounds = 2 }) {
  const modeCode = MODES.some((m) => m.code === mode) ? mode : "close";
  const t = time || MODE_TIME[modeCode];
  const rng = mulberry32(hashStr(`${date}:${modeCode}`));
  const snapshot = buildSnapshot(date, modeCode, rng);
  const degraded = [
    ...snapshot.degraded,
    ...snapshot.sentiment.degraded,
    "资讯源（财新/央视）仅当日可用，历史补做资讯块标注缺失（mock 演示）",
  ];
  const s = snapshot.sentiment;
  const summary =
    `${modeLabel(modeCode)}：指数震荡偏强，涨跌家数比 ${s.up_down_ratio != null ? `${s.up_down_ratio.toFixed(2)} : 1` : "—"}，` +
    `涨停 ${s.limit_up_count} 家、炸板率 ${(s.break_rate * 100).toFixed(1)}%，情绪中高位分歧（mock 演示）`;
  const meta = {
    date,
    mode: modeCode,
    mode_label: modeLabel(modeCode),
    time: t,
    created_at: `${date}T${t.slice(0, 2)}:${t.slice(2, 4)}:${t.slice(4, 6)}`,
    degraded,
    disclaimer: DEFAULT_DISCLAIMER,
    summary,
  };
  const report = buildReport(date, modeCode, snapshot, meta);
  return {
    meta,
    report,
    analyses: buildAnalysts(modeCode, degraded, rng),
    snapshot,
  };
}

// job 完成后的 result（对齐契约：{record_id, meta, report, snapshot}；record_id = "{date}_{time}"）
export function buildMockJobResult(payload) {
  const detail = buildMockReviewDetail(payload);
  return {
    record_id: `${detail.meta.date}_${detail.meta.time}`,
    meta: detail.meta,
    report: detail.report,
    snapshot: detail.snapshot,
  };
}

// 实时数据 mock（结构与 GET /api/data/realtime 一致）
export function buildMockRealtime() {
  const now = new Date();
  const pad = (x) => String(x).padStart(2, "0");
  const stamp = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
  return {
    status: {
      date: isoDate(now),
      time: stamp,
      is_trading_day: now.getDay() >= 1 && now.getDay() <= 5,
      phase: "交易中(上午)",
    },
    indices: {
      indices: [
        { name: "上证指数", price: 3806.79, pct_change: -0.0008, open: 3816.37, high: 3818.27, low: 3799.52, source: "东财实时" },
        { name: "深证成指", price: 13703.19, pct_change: 0.019, open: 13680.5, high: 13740.2, low: 13620.1, source: "东财实时" },
        { name: "创业板指", price: 3413.89, pct_change: 0.0337, open: 3390.0, high: 3425.3, low: 3380.4, source: "东财实时" },
        { name: "科创50", price: 1575.22, pct_change: 0.0144, open: 1568.0, high: 1580.1, low: 1560.9, source: "东财实时" },
      ],
      source: "东财实时",
      degraded: false,
      degraded_reason: [],
    },
    zt: {
      limit_up_count: 67,
      limit_down_count: 0,
      zhaban_count: 11,
      touched_count: 78,
      zhaban_rate: 0.141,
      seal_rate: 0.859,
      yesterday_zt_count: 75,
      tier: { 首板: 42, "2板及以上": 25 },
      source: "同花顺实时",
      degraded: false,
      degraded_reason: [],
      units: {},
    },
    sectors: {
      top: [
        { name: "电子", pct_change: 0.0309, net_inflow: 14177361920 },
        { name: "通信", pct_change: 0.0338, net_inflow: 12492857600 },
        { name: "通信设备", pct_change: 0.0361, net_inflow: 12465150208 },
        { name: "元件", pct_change: 0.0465, net_inflow: 8381961472 },
        { name: "电池", pct_change: 0.025, net_inflow: 5000000000 },
      ],
      bottom: [
        { name: "贵金属", pct_change: -0.021, net_inflow: -3000000000 },
        { name: "煤炭", pct_change: -0.018, net_inflow: -2500000000 },
        { name: "石油", pct_change: -0.015, net_inflow: -2200000000 },
        { name: "钢铁", pct_change: -0.012, net_inflow: -1800000000 },
        { name: "银行", pct_change: -0.008, net_inflow: -1500000000 },
      ],
      flow_in: [
        { name: "电子", pct_change: 0.0309, net_inflow: 14177361920 },
        { name: "通信", pct_change: 0.0338, net_inflow: 12492857600 },
        { name: "通信设备", pct_change: 0.0361, net_inflow: 12465150208 },
      ],
      flow_out: [
        { name: "贵金属", pct_change: -0.021, net_inflow: -3000000000 },
        { name: "煤炭", pct_change: -0.018, net_inflow: -2500000000 },
      ],
      source: "东财实时",
      degraded: false,
      degraded_reason: [],
      units: {},
    },
    news: {
      news: [
        { time: stamp, text: "【快讯】三井物产将回购至多 2000 亿日元的股份。", source: "新浪7x24" },
        { time: stamp, text: "【快讯】我国牵头制定的工业通信国际标准发布。", source: "新浪7x24" },
        { time: stamp, text: "【快讯】沪深两市成交额突破 5000 亿元。", source: "新浪7x24" },
      ],
      source: "新浪7x24",
      degraded: false,
      degraded_reason: [],
    },
    auction: { window: false, note: "非竞价窗口（09:15-09:25），竞价数据不可用" },
    sources: ["东财实时", "同花顺实时", "新浪7x24"],
    degraded: [],
    updated_at: stamp,
  };
}

// 个股实时行情 mock（结构同 GET /api/data/quote）
export function mockStockQuote(code) {
  const c = String(code || "600519").trim().padStart(6, "0");
  const seed = c.split("").reduce((s, x) => s + Number(x), 0);
  const price = 10 + (seed % 200) + (seed % 97) / 100;
  const pct = ((seed % 50) - 20) / 1000;
  return {
    code: c,
    name: `示例股${c.slice(-3)}`,
    price,
    pct_change: pct,
    open: price * (1 - 0.004),
    high: price * (1 + 0.012),
    low: price * (1 - 0.011),
    pre_close: price / (1 + pct),
    volume: 100000 + seed * 137,
    amount: price * 100000 * 100,
    turnover_rate: 0.02 + (seed % 30) / 1000,
    source: "离线mock",
    units: { pct_change: "小数(0.05=5%)", turnover_rate: "小数" },
  };
}

// 股票搜索 mock（结构同 GET /api/data/search）
export function mockSearchStocks(q) {
  const text = String(q || "").trim();
  return [
    { code: "600519", name: "贵州茅台", market: "sh", type: "GP" },
    { code: "000858", name: "五粮液", market: "sz", type: "GP" },
  ].filter((s) => !text || s.name.includes(text) || s.code.includes(text)).slice(0, 5);
}

// 个股详情 mock（结构同 GET /api/data/stock/{code}，供离线演示 R9 详情视图）
export function mockStockDetail(code, days = 60) {
  const c = String(code || "600519").trim().padStart(6, "0");
  const q = mockStockQuote(c);
  const daily = [];
  const now = new Date();
  for (let i = days; i >= 0; i -= 1) {
    const d = new Date(now.getTime() - i * 86400000).toISOString().slice(0, 10);
    const base = q.price * (1 - (days - i) * 0.002);
    daily.push({
      date: d, open: base * 0.995, high: base * 1.01, low: base * 0.99,
      close: base, volume: 100000 + i * 100, amount: base * 100000 * 100,
    });
  }
  const minute = [];
  for (let m = 0; m < 48; m += 1) {
    minute.push({ time: `09:${String(m % 60).padStart(2, "0")}`, close: q.price * (1 - 0.01 + m * 0.0004), volume: 100, amount: 1000 });
  }
  const mkKline = (step, count) =>
    Array.from({ length: count }, (_, i) => ({
      date: "2026-08-04", time: `${String(9 + Math.floor(i / 12))}:${String((i % 12) * step).padStart(2, "0")}`,
      datetime: `2026-08-04 ${String(9 + Math.floor(i / 12))}:${String((i % 12) * step).padStart(2, "0")}`,
      open: q.price * 0.998, high: q.price * 1.005, low: q.price * 0.995, close: q.price,
      volume: 100, amount: 1000,
    }));
  const closes = daily.map((d) => d.close);
  const ma = (n) => (closes.length >= n ? closes.slice(-n).reduce((a, b) => a + b, 0) / n : null);
  return {
    code: c, date: now.toISOString().slice(0, 10),
    quote: { ...q, bids: [{ price: q.price * 0.999, volume: 5 }], asks: [{ price: q.price * 1.001, volume: 3 }] },
    daily, minute, kline_5m: mkKline(5, 96), kline_1m: mkKline(1, 240),
    ma: { ma5: ma(5), ma10: ma(10), ma20: ma(20) },
    sources: { quote: "离线mock", daily: "离线mock", minute: "离线mock", kline_5m: "离线mock", kline_1m: "离线mock" },
    degraded: [], updated_at: now.toISOString().slice(0, 19).replace("T", " "),
  };
}

// 历史列表种子（按日期分组、同日多份、倒序）
export function seedMockReviews(today = new Date()) {
  const map = new Map();
  const register = (detail) => {
    const key = `${detail.meta.date}/${detail.meta.time}`;
    map.set(key, detail);
  };
  const days = [0, -1, -2];
  const plans = [
    { mode: "close", time: "150500" },
    { mode: "noon", time: "123000" },
    { mode: "auction", time: "092000" },
  ];
  days.forEach((off, di) => {
    const date = isoDate(addDays(today, off));
    // 每个历史日至少一份收盘复盘；今天再补一份午间，昨天补一份竞价（演示同日多份）
    const list = off === 0 ? [plans[1], plans[0]] : off === -1 ? [plans[2], plans[0]] : [plans[0]];
    list.forEach((p, i) => {
      const time = off === 0 && i === 0 ? "123000" : p.time;
      register(buildMockReviewDetail({ date, mode: p.mode, time }));
    });
  });
  return map;
}

export function mockReviewList(map) {
  const byDate = new Map();
  [...map.values()].forEach((d) => {
    const date = d.meta.date;
    if (!byDate.has(date)) byDate.set(date, []);
    byDate.get(date).push({
      record_id: `${date}_${d.meta.time}`,
      mode: d.meta.mode,
      mode_label: d.meta.mode_label,
      time: d.meta.time,
      created_at: d.meta.created_at,
      summary: d.meta.summary,
    });
  });
  return [...byDate.entries()]
    .sort((a, b) => (a[0] < b[0] ? 1 : -1))
    .map(([date, items]) => ({
      date,
      items: items.sort((a, b) => (a.time < b.time ? 1 : -1)),
    }));
}

// ---------- 聊天 mock（对齐 §五 聊天端点契约） ----------
export function buildChatMeta({ target_type, target, target_name, analysts, title, date }) {
  // 允许注入日期（seed/断言用固定日期），默认当前时间
  const d = date ? new Date(`${date}T12:00:00`) : new Date();
  const ts = localIso(d);
  return {
    target_type: target_type === "sector" ? "sector" : "stock",
    target,
    target_name: target_name || target,
    analysts: (Array.isArray(analysts) ? analysts : []).map((a) =>
      typeof a === "string" ? { skill_name: a } : { skill_name: a.skill_name || a.name || "" }
    ),
    title: title || `${target_name || target} · 分析师对话`,
    created_at: ts,
    date: isoDate(d),
  };
}

export function seedMockChatSessions(today = new Date()) {
  const date = isoDate(today);
  const sessions = [];
  // 会话 1：板块多分析师交叉
  const s1Analysts = ANALYSTS.slice(0, 3).map((a) => ({ skill_name: a.skill_name }));
  sessions.push({
    session_id: `${date}_101200`,
    meta: buildChatMeta({
      target_type: "sector",
      target: "半导体",
      target_name: "半导体",
      analysts: s1Analysts,
      title: "半导体板块午后走势怎么看",
      date,
    }),
    messages: [
      {
        id: "c1-m1",
        role: "user",
        content: "半导体板块午后还有冲高机会吗？",
        created_at: `${date}T10:12:00`,
      },
      {
        id: "c1-m2",
        role: "assistant",
        content:
          "综合三位分析师意见：板块情绪仍在高位但分歧加大，午后冲高需放量确认，缩量冲高建议减仓兑现。",
        analyst_parts: [
          { skill_name: "趋势派·阿狼", content: "分时重心未破，若午后放量突破早盘高点可看高一线，否则视为震荡。" },
          { skill_name: "趋势派·爱在冰川", content: "资金正在高低切换，半导体属于昨日强势方向，追高风险大于收益。" },
          { skill_name: "情绪派·炒股养家", content: "板块内炸板率偏高，情绪分歧期冲高容易回落，建议等回踩。" },
        ],
        created_at: `${date}T10:12:08`,
      },
    ],
    disclaimer: DEFAULT_DISCLAIMER,
  });
  // 会话 2：单分析师
  sessions.push({
    session_id: `${date}_094000`,
    meta: buildChatMeta({
      target_type: "stock",
      target: "600519",
      target_name: "贵州茅台",
      analysts: [{ skill_name: "阿狼" }],
      title: "贵州茅台中线怎么看",
      date,
    }),
    messages: [
      {
        id: "c2-m1",
        role: "user",
        content: "贵州茅台目前位置适合中线建仓吗？",
        created_at: `${date}T09:40:00`,
      },
      {
        id: "c2-m2",
        role: "assistant",
        content:
          "趋势视角：贵州茅台处于年线附近的震荡筑底阶段，量能温和，中线可分批布局，跌破年线则先止损观望。",
        analyst_parts: [],
        created_at: `${date}T09:40:15`,
      },
    ],
    disclaimer: DEFAULT_DISCLAIMER,
  });
  return sessions;
}

export function mockChatReply(meta, content) {
  const now = localIso();
  const userMsg = { id: `m-${Date.now()}-u`, role: "user", content, created_at: now };
  const n = meta.analysts.length;
  let assistantMsg;
  if (n <= 1) {
    const name = (meta.analysts[0] && meta.analysts[0].skill_name) || "分析师";
    assistantMsg = {
      id: `m-${Date.now()}-a`,
      role: "assistant",
      analyst_name: name,
      content: `收到您关于「${meta.target_name || meta.target}」的问题。结合当前数据，${name}认为：${content}相关方向需结合大盘情绪与板块轮动综合判断，建议控制仓位、严格止损。`,
      analyst_parts: [],
      created_at: now,
    };
  } else {
    const stances = ["同意", "补充", "谨慎"];
    assistantMsg = {
      id: `m-${Date.now()}-a`,
      role: "assistant",
      content:
        "汇总：三位分析师整体偏谨慎，均建议结合量能与情绪确认后再操作；分歧点在于短线节奏。",
      analyst_parts: meta.analysts.map((a, i) => ({
        skill_name: a.skill_name,
        content: `${stances[i % stances.length]}：针对「${content}」，我的判断是${i % 2 === 0 ? "趋势未破坏，可持有观察" : "情绪分歧加大，建议等待更明确信号"}，具体还需结合次日竞价与板块持续性。`,
      })),
      created_at: now,
    };
  }
  return [userMsg, assistantMsg];
}

export function mockChatSessionList(sessions, { target = "", date = "" } = {}) {
  return sessions
    .filter((s) => {
      const t = String(target || "").trim();
      const d = String(date || "").trim();
      const targetOk = !t || (s.meta.target || "").includes(t) || (s.meta.target_name || "").includes(t);
      const dateOk = !d || s.meta.date === d;
      return targetOk && dateOk;
    })
    .map((s) => ({
      session_id: s.session_id,
      target_type: s.meta.target_type,
      target: s.meta.target,
      target_name: s.meta.target_name,
      analysts: s.meta.analysts,
      title: s.meta.title,
      created_at: s.meta.created_at,
      date: s.meta.date,
      last_message: s.messages.length ? s.messages[s.messages.length - 1].content : "",
      message_count: s.messages.length,
    }))
    .sort((a, b) => (a.created_at < b.created_at ? 1 : -1));
}

// ==================== R10 mock（结构对齐 requirements-r10 §二/§三 契约） ====================

// ---- F3 市场天气/情绪温度（手算样例：r=0.67→+15；d=75→+14；炸板0.20→+10；
//      连板5→+10；红盘率0.68→+10；score=50+15+14+10+10+10=109→clamp 100→晴/火热）----
export function buildMockMarketWeather(date = isoDate(new Date())) {
  return {
    date,
    weather: "晴",
    emotion_score: 100,
    emotion_label: "火热",
    reasons: [
      "涨跌家数比 0.67：+15",
      "涨停 80 家、跌停 5 家：+14",
      "炸板率 0.20：+10",
      "连板高度 5：+10",
      "昨日涨停红盘率 0.68：+10",
    ],
    source: "规则引擎（基于现有行情快照）",
    degraded: false,
    degraded_reason: [],
  };
}

// ---- F5 板块温度榜（hot/cold 各 top5，含领涨股）----
export function buildMockSectorHeat() {
  return {
    hot: [
      { name: "半导体", pct_change: 0.023, leader: "中芯国际" },
      { name: "算力租赁", pct_change: 0.019, leader: "鸿博股份" },
      { name: "通信设备", pct_change: 0.016, leader: "中兴通讯" },
      { name: "机器人", pct_change: 0.014, leader: "汇川技术" },
      { name: "创新药", pct_change: 0.012, leader: "恒瑞医药" },
    ],
    cold: [
      { name: "煤炭", pct_change: -0.015, leader: "中国神华" },
      { name: "银行", pct_change: -0.011, leader: "招商银行" },
      { name: "石油", pct_change: -0.009, leader: "中国石油" },
      { name: "房地产", pct_change: -0.007, leader: "万科A" },
      { name: "钢铁", pct_change: -0.005, leader: "宝钢股份" },
    ],
    source: "东财行业",
    degraded: false,
    degraded_reason: [],
  };
}

// ---- F2 额外指数（平均股价/微盘股/高股息，通达信指数代码）----
export function buildMockExtraIndices() {
  return {
    items: [
      { name: "平均股价", code: "880003", close: 22.15, pct_change: 0.008, source: "通达信指数" },
      { name: "微盘股", code: "880823", close: 1680.35, pct_change: -0.012, source: "通达信指数" },
      { name: "高股息", code: "880522", close: 3050.12, pct_change: 0.005, source: "通达信指数" },
    ],
    source: "AKShare",
    degraded: false,
    degraded_reason: [],
  };
}

// ---- F4 风格坐标（大盘 vs 小盘、价值 vs 成长）----
export function buildMockMarketStyle() {
  return {
    large_vs_small: {
      score: 62,
      label: "大盘占优",
      desc: "沪深300 +0.8%，中证1000 -0.3%",
    },
    value_vs_growth: {
      score: 45,
      label: "均衡",
      desc: "价值/成长数据缺失或接近",
    },
    source: "指数代理+全市场PE分档",
    degraded: false,
    degraded_reason: [],
  };
}

// ---- F9 观察列表（本地文件 data/watchlist.json 的前端 mock 镜像）----
const WATCHLIST_SEED = [
  { code: "600519", name: "贵州茅台", note: "", pinned: true, created_at: localIso() },
  { code: "000858", name: "五粮液", note: "", pinned: false, created_at: localIso() },
  { code: "300750", name: "宁德时代", note: "", pinned: false, created_at: localIso() },
];

let mockWatchlist = null;

export function seedMockWatchlist() {
  if (!mockWatchlist) {
    mockWatchlist = WATCHLIST_SEED.map((x) => ({ ...x }));
  }
  return mockWatchlist;
}

export function mockWatchlistList() {
  // 保持存储顺序（PUT /order 为唯一排序依据；pinned 只是置顶标记，不参与排序）
  return seedMockWatchlist().slice().map((x) => ({ ...x }));
}

export function mockWatchlistAdd(code, note = "") {
  const list = seedMockWatchlist();
  const c = String(code || "").trim();
  if (!/^\d{6}$/.test(c)) {
    throw new Error("股票代码格式不正确，应为 6 位数字");
  }
  if (list.some((x) => x.code === c)) {
    throw new Error("该股票已在观察列表中"); // 对齐后端 409 中文
  }
  const item = {
    code: c,
    name: `示例股${c.slice(-3)}`,
    note: String(note || ""),
    pinned: false,
    created_at: localIso(),
  };
  list.push(item);
  return { ...item };
}

export function mockWatchlistDelete(code) {
  const list = seedMockWatchlist();
  const idx = list.findIndex((x) => x.code === String(code || "").trim());
  if (idx < 0) {
    throw new Error("该股票不在观察列表中"); // 对齐后端 404 中文
  }
  list.splice(idx, 1);
  return undefined; // 204
}

export function mockWatchlistReorder(codes) {
  const list = seedMockWatchlist();
  const wanted = Array.isArray(codes)
    ? codes.map((c) => String(c || "").trim()).filter(Boolean)
    : [];
  const byCode = new Map(list.map((x) => [x.code, x]));
  const ordered = wanted
    .filter((c) => byCode.has(c))
    .map((c) => byCode.get(c));
  // 缺失代码忽略；未出现在请求中的（理论上后端会忽略）保留在末尾
  const rest = list.filter((x) => !ordered.includes(x));
  mockWatchlist = [...ordered, ...rest];
  return { codes: mockWatchlist.map((x) => x.code) };
}

// ==================== 回测 mock（结构对齐 requirements-backtest-ui §三 契约） ====================
// 仅 DEV 降级 / 离线演示：生成一个「逐日跑复盘 + 撮合」的进度快照，含每天 settle/review
// 耗时、4 个节点耗时、5 位分析师各自耗时，与后端 GET /api/backtest/{id} 返回结构一致。

const BACKTEST_POOL = [
  { start: "2026-07-01", end: "2026-08-14" },
  { start: "2026-06-01", end: "2026-07-01" },
  { start: "2026-05-01", end: "2026-05-29" },
];

// 生成 start~end 区间的交易日列表（跳过周末，与后端一致）
function tradingDays(start, end) {
  const days = [];
  const d = new Date(`${start}T00:00:00`);
  const ed = new Date(`${end}T00:00:00`);
  const p = (x) => String(x).padStart(2, "0");
  while (d <= ed) {
    const wd = d.getDay();
    if (wd !== 0 && wd !== 6) {
      days.push(`${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`);
    }
    d.setDate(d.getDate() + 1);
  }
  return days;
}

// 以 s 为种子生成确定性伪随机（复读同一步骤得到相同耗时）
function btRng(seed) {
  let a = seed >>> 0;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function buildBtDay(date, rng, done) {
  if (!done) {
    // 尚未开始/进行中的占位
    return {
      date,
      status: "pending",
      attempt: 0,
      attempts: 1,
      elapsed_ms: 0,
      settle: { elapsed_ms: 0, filled: 0, rejected: 0, quotes_missing: 0 },
      review: { elapsed_ms: 0 },
      stages: [],
      error: null,
    };
  }
  const settleMs = Math.round((80 + rng() * 200) * rng());
  const fetchMs = Math.round(300 + rng() * 2500);
  const analystMs = Math.round(4000 + rng() * 9000);
  const hostMs = Math.round(300 + rng() * 1500);
  const reportMs = Math.round(400 + rng() * 2000);
  const reviewMs = fetchMs + analystMs + hostMs + reportMs;
  const filled = Math.floor(rng() * 5);
  const rejected = Math.floor(rng() * 4);
  const failed = rng() < 0.08; // 少量样本失败（演示 fail-fast）
  return {
    date,
    status: failed ? "failed" : "done",
    attempt: failed ? 1 : (rng() < 0.15 ? 2 : 1),
    attempts: failed ? 1 : 2,
    elapsed_ms: failed ? reviewMs + settleMs : reviewMs + settleMs + Math.round(rng() * 200),
    settle: {
      elapsed_ms: settleMs,
      filled,
      rejected,
      quotes_missing: rng() < 0.2 ? Math.floor(rng() * 3) : 0,
    },
    review: { elapsed_ms: reviewMs },
    stages: [
      { key: "fetch", elapsed_ms: fetchMs },
      {
        key: "analysts",
        elapsed_ms: analystMs,
        analysts: ANALYSTS.map((a) => ({
          skill_id: a.id,
          name: a.skill_name,
          elapsed_ms: Math.round((analystMs * (0.12 + rng() * 0.35)) / 10) * 10,
        })),
      },
      { key: "host", elapsed_ms: hostMs },
      { key: "report", elapsed_ms: reportMs },
    ],
    error: failed ? "单日复盘在报告阶段失败（演示 fail-fast，对齐回测契约）" : null,
  };
}

// 生成 upTo 天已完成、其余 pending 的回测快照（status 由 doneCount 与 total 推导）
function buildBtSnapshot(id, rangeIndex, doneCount) {
  const days = tradingDays(BACKTEST_POOL[rangeIndex].start, BACKTEST_POOL[rangeIndex].end);
  const total = days.length;
  const done = Math.max(0, Math.min(doneCount, total));
  const expectedToday = Math.floor(done * (0.55 + 0.45 * rangeIndex) * 100) / 100;
  const rng = btRng((done + 1) * 977);
  const list = days.map((date, i) => {
    if (i < done) {
      return buildBtDay(date, btRng(i * 31 + rangeIndex * 71), true);
    }
    if (i === done && done < total) {
      return { ...buildBtDay(date, rng, false), status: "running" };
    }
    return buildBtDay(date, rng, false);
  });
  // 若当前日（running 前一日）失败，则整场 status=error（fail-fast）
  const failedToday = done > 0 && list[done - 1].status === "failed";
  const completedMs = list
    .filter((x) => x.status === "done" || x.status === "failed")
    .reduce((a, x) => a + (x.elapsed_ms || 0), 0);
  const current =
    done < total
      ? { date: list[Math.max(0, done)].date, stage: "analysts" }
      : { date: days[days.length - 1], stage: "done" };
  const status =
    done >= total ? "done" : failedToday ? "error" : "running";
  const now = new Date();
  const pad2 = (x) => String(x).padStart(2, "0");
  return {
    id,
    status,
    start: BACKTEST_POOL[rangeIndex].start,
    end: BACKTEST_POOL[rangeIndex].end,
    params: { force: false, max_rounds: 3, mode: "close" },
    created_at: `${isoDate(now)}T${pad2(now.getHours())}:${pad2(now.getMinutes())}:${pad2(now.getSeconds())}`,
    finished_at: status === "done" ? `${isoDate(now)}T${pad2(now.getHours())}:${pad2(now.getMinutes())}:30` : null,
    days_total: total,
    days_done: done,
    current,
    total_elapsed_ms: Math.round(completedMs * expectedToday),
    days: list,
  };
}

// 历史 runs（GET /api/backtest/history · {runs:[...]} 完整快照 + 摘要）
export function buildMockBacktestHistory() {
  return {
    runs: [
      { id: "bt_20260810_103000", start: "2026-07-01", end: "2026-08-14", status: "done", created_at: "2026-08-10T10:30:00", finished_at: "2026-08-10T10:52:10", days_done: 33, days_total: 33, total_elapsed_ms: 1582000 },
      { id: "bt_20260805_091500", start: "2026-06-01", end: "2026-07-01", status: "done", created_at: "2026-08-05T09:15:00", finished_at: "2026-08-05T09:30:42", days_done: 22, days_total: 22, total_elapsed_ms: 942000 },
      { id: "bt_20260811_143000", start: "2026-07-01", end: "2026-08-14", status: "cancelled", created_at: "2026-08-11T14:30:00", finished_at: null, days_done: 9, days_total: 33, total_elapsed_ms: 310000 },
    ],
  };
}

// 单个回测完整快照（GET /api/backtest/{id}）
let mockBtSeq = 0;
export function buildMockBacktestDetail(id, doneCount) {
  return buildBtSnapshot(id || `bt_mock_${++mockBtSeq}`, 0, doneCount);
}
