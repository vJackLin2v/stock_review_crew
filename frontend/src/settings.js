// 设置模块（R10 F12）：深色/浅色主题、涨跌配色、首页默认图表周期、全站字号
// 全部纯前端 localStorage，立即生效；无后端接口。
// 生效机制：
//   - 主题/字号 → <html data-theme / data-font>（CSS 选择器接管）
//   - 涨跌配色 → <html> 内联 CSS 变量 --up/--down（.pos/.neg 与 ECharts 共用）
//   - 图表周期 → StockDetail 初始 tab
// 警示框/免责声明不参与涨跌配色切换（使用 --danger 等固定色）。
import { useSyncExternalStore } from "react";

export const SETTINGS_STORAGE_KEY = "stock-review.settings";

export const DEFAULT_SETTINGS = {
  theme: "light",
  upDownColors: "redUpGreenDown",
  defaultChartPeriod: "minute",
  fontSize: "standard",
};

// 涨跌配色（JS 侧 ECharts 取色用；CSS 侧走 --up/--down 变量）
export const UP_DOWN_PALETTES = {
  redUpGreenDown: { up: "#e03131", down: "#0ca678" },
  greenUpRedDown: { up: "#0ca678", down: "#e03131" },
};

// 全站字号（px）
export const FONT_SIZE_MAP = {
  small: 13,
  standard: 14,
  large: 15.5,
  xlarge: 17,
};

const ALLOWED = {
  theme: ["light", "dark"],
  upDownColors: ["redUpGreenDown", "greenUpRedDown"],
  defaultChartPeriod: ["minute", "daily"],
  fontSize: ["small", "standard", "large", "xlarge"],
};

let cache = null;
const listeners = new Set();

// 逐键校验本地存储值，非法回退默认（防手改/旧版本脏数据）
function sanitize(raw) {
  const out = { ...DEFAULT_SETTINGS };
  if (!raw || typeof raw !== "object") return out;
  Object.keys(ALLOWED).forEach((key) => {
    if (ALLOWED[key].includes(raw[key])) out[key] = raw[key];
  });
  return out;
}

function readCache() {
  if (cache) return cache;
  try {
    const raw = localStorage.getItem(SETTINGS_STORAGE_KEY);
    cache = sanitize(raw ? JSON.parse(raw) : null);
  } catch {
    cache = { ...DEFAULT_SETTINGS };
  }
  return cache;
}

function persist(next) {
  cache = next;
  try {
    localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(cache));
  } catch {
    /* 忽略存储异常（隐私模式等），本次生效仍保留 */
  }
  applyToDom(cache);
  listeners.forEach((fn) => fn());
}

// 将设置应用到 DOM：data-theme / data-font / --up / --down
export function applyToDom(s) {
  const st = sanitize(s);
  const root = document.documentElement;
  root.dataset.theme = st.theme;
  root.dataset.font = st.fontSize;
  const palette =
    UP_DOWN_PALETTES[st.upDownColors] || UP_DOWN_PALETTES[DEFAULT_SETTINGS.upDownColors];
  root.style.setProperty("--up", palette.up);
  root.style.setProperty("--down", palette.down);
}

export function loadSettings() {
  return { ...readCache() };
}

// 局部更新并持久化（立即生效）
export function saveSettings(patch) {
  persist(sanitize({ ...readCache(), ...patch }));
}

export function subscribeSettings(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

// React hook：订阅设置变更自动重渲染（ECharts 配色等 JS 侧消费）
export function useSettings() {
  return useSyncExternalStore(subscribeSettings, readCache);
}

export function paletteOf(upDownColors) {
  return (
    UP_DOWN_PALETTES[upDownColors] ||
    UP_DOWN_PALETTES[DEFAULT_SETTINGS.upDownColors]
  );
}
