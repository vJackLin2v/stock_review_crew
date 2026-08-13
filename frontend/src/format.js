// 格式化工具：全中文展示口径（数据字典 §六）
export function num(v, fallback = 0) {
  if (v === null || v === undefined || v === "") return fallback;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : fallback;
}

export function isNone(v) {
  if (v === null || v === undefined || v === "") return true;
  const n = typeof v === "number" ? v : Number(v);
  return !Number.isFinite(n);
}

// 比率一律小数存储、前端 ×100 展示（0.5 = 50%）
export function fmtPct(v, { digits = 2, signed = false } = {}) {
  const n = num(v, NaN);
  if (!Number.isFinite(n)) return "—";
  const sign = signed && n > 0 ? "+" : "";
  return `${sign}${(n * 100).toFixed(digits)}%`;
}

export function fmtSignedPct(v, digits = 2) {
  return fmtPct(v, { digits, signed: true });
}

// 红涨绿跌：正红 #E03131、负绿 #0CA678、零中性灰
export function pctClass(v) {
  const n = num(v, NaN);
  if (!Number.isFinite(n) || n === 0) return "zero";
  return n > 0 ? "pos" : "neg";
}

export function moneyClass(v) {
  const n = num(v, NaN);
  if (!Number.isFinite(n)) return "";
  if (n === 0) return "zero";
  return n > 0 ? "pos" : "neg";
}

// P1-M：金额智能缩写（元 → 万/亿/万亿，保留 2 位小数），None → 「—」
export function fmtAmount(v, digits = 2) {
  const n = num(v, NaN);
  if (!Number.isFinite(n)) return "—";
  const abs = Math.abs(n);
  let value = n;
  let unit = "";
  if (abs >= 1e12) {
    value = n / 1e12;
    unit = "万亿";
  } else if (abs >= 1e8) {
    value = n / 1e8;
    unit = "亿";
  } else if (abs >= 1e4) {
    value = n / 1e4;
    unit = "万";
  }
  const text = value.toLocaleString("zh-CN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
  return unit ? `${text} ${unit}` : text;
}

export function fmtCount(v) {
  const n = num(v, NaN);
  return Number.isFinite(n) ? n.toLocaleString("zh-CN") : "—";
}

// 日期统一「2025-11-27」
export function fmtDate(d) {
  if (!d) return "—";
  const match = String(d).trim().match(/^(\d{4})-(\d{2})-(\d{2})/);
  return match ? `${match[1]}-${match[2]}-${match[3]}` : String(d).slice(0, 10);
}

// HHMMSS → HH:MM（历史「08:50 早盘前决策」）
export function fmtTimeHM(t) {
  if (!t) return "—";
  const m = String(t).trim().match(/^(\d{2})(\d{2})(\d{2})$/);
  if (m) return `${m[1]}:${m[2]}`;
  const s = String(t).trim();
  const iso = s.match(/T(\d{2}):(\d{2})/);
  if (iso) return `${iso[1]}:${iso[2]}`;
  return s.slice(0, 5);
}

export function fmtDateTime(ts) {
  if (!ts) return "—";
  const s = String(ts).trim();
  const compact = s.match(/^(\d{4})(\d{2})(\d{2})[-_/]?(\d{2})(\d{2})(\d{2})$/);
  if (compact) return `${compact[1]}-${compact[2]}-${compact[3]} ${compact[4]}:${compact[5]}`;
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return s.slice(0, 16);
  const p = (x) => String(x).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

// ---------- M5：6 位股票代码 → 「名称(代码)」展示（2026-08-12 新增） ----------
// 只替换名称表中查得到的代码，天然防误伤：日期（20260812 为 8 位）、统计数字、
// 随机 6 位数字查不到名称均保持原样；正则 (?<!\d)(\d{6})(?!\d) 要求前后均非数字，
// 允许带后缀/括号的写法（600519.SH、(600519)）。
const STOCK_CODE_RE = /(?<!\d)(\d{6})(?!\d)/g;

// 提取文本中的 6 位代码候选（排除围栏代码块），去重
export function collectStockCodes(text) {
  const codes = new Set();
  const plainText = String(text || "").replace(/```[\s\S]*?```/g, "");
  const re = new RegExp(STOCK_CODE_RE.source, "g");
  let m;
  while ((m = re.exec(plainText))) codes.add(m[1]);
  return [...codes];
}

// 将文本中「查得到名称」的 6 位代码替换为「名称(代码)」；未命中保持原样
export function withStockNames(text, names) {
  const raw = String(text || "");
  if (!names || Object.keys(names).length === 0) return raw;
  const re = new RegExp(STOCK_CODE_RE.source, "g");
  return raw.replace(re, (hit, code) => {
    const name = names[code];
    return name ? `${name}(${code})` : hit;
  });
}
