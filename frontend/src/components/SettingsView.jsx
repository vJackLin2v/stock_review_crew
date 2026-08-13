// R10 F12 设置页：深色/浅色主题、涨跌配色、首页默认图表周期、全站字号。
// 全部 localStorage 立即生效，纯前端无后端接口。
import React from "react";
import { saveSettings, UP_DOWN_PALETTES, useSettings } from "../settings";

const SECTIONS = [
  {
    key: "theme",
    label: "深色 / 浅色主题",
    desc: "切换整站明暗配色，修改后立即生效并保存在本机浏览器。",
    options: [
      { value: "light", label: "浅色主题", hint: "明亮（默认）" },
      { value: "dark", label: "深色主题", hint: "暗色护眼" },
    ],
  },
  {
    key: "upDownColors",
    label: "涨跌配色",
    desc: "全站生效（行情数值、图表、涨跌停等）；警示框与免责声明不参与配色切换。",
    options: [
      { value: "redUpGreenDown", label: "红涨绿跌", hint: "A股惯例（默认）" },
      { value: "greenUpRedDown", label: "绿涨红跌", hint: "欧美习惯" },
    ],
    swatch: true,
  },
  {
    key: "defaultChartPeriod",
    label: "首页默认图表周期",
    desc: "个股详情页打开时默认展示的图表周期，可在详情页内随时切换。",
    options: [
      { value: "minute", label: "分时", hint: "当日分时走势" },
      { value: "daily", label: "日线", hint: "日K 与均线" },
    ],
  },
  {
    key: "fontSize",
    label: "全站字号",
    desc: "调整整站基础字号，正文、卡片与表格随之缩放。",
    options: [
      { value: "small", label: "较小", hint: "信息密度最高" },
      { value: "standard", label: "标准", hint: "默认" },
      { value: "large", label: "较大", hint: "阅读更舒适" },
      { value: "xlarge", label: "特大", hint: "长辈模式" },
    ],
  },
];

export default function SettingsView() {
  const s = useSettings();
  const set = (key, value) => saveSettings({ [key]: value });
  const palette = UP_DOWN_PALETTES[s.upDownColors] || UP_DOWN_PALETTES.redUpGreenDown;

  return (
    <div className="settings-page">
      <div className="card">
        <h3 className="card-title">设置</h3>
        <p className="card-sub">
          所有设置仅保存在本机浏览器（localStorage），修改立即生效，无需后端接口。
        </p>
        {SECTIONS.map((sec) => (
          <div key={sec.key} className="set-section">
            <div className="set-label">{sec.label}</div>
            <div className="set-desc">{sec.desc}</div>
            <div className="set-options">
              {sec.options.map((o) => (
                <button
                  key={o.value}
                  type="button"
                  className={`set-option${s[sec.key] === o.value ? " active" : ""}`}
                  onClick={() => set(sec.key, o.value)}
                  aria-pressed={s[sec.key] === o.value}
                >
                  <span className="set-option-label">{o.label}</span>
                  <span className="set-option-hint">
                    {o.hint}
                    {sec.swatch ? (
                      <span className="set-swatch">
                        <i style={{ background: palette.up }} />涨
                        <i style={{ background: palette.down }} />跌
                      </span>
                    ) : null}
                  </span>
                </button>
              ))}
            </div>
          </div>
        ))}
        <p className="card-sub" style={{ marginTop: 14, marginBottom: 0 }}>
          当前配置：{s.theme === "dark" ? "深色" : "浅色"}主题 ·{" "}
          {s.upDownColors === "redUpGreenDown" ? "红涨绿跌" : "绿涨红跌"} ·{" "}
          {s.defaultChartPeriod === "minute" ? "分时" : "日线"} ·{" "}
          {({ small: "较小", standard: "标准", large: "较大", xlarge: "特大" })[s.fontSize]}字号
        </p>
      </div>

      <div className="card">
        <h3 className="card-title">说明</h3>
        <p className="card-sub" style={{ marginBottom: 0 }}>
          涨跌配色切换仅作用于行情数值与图表等展示元素，风险警示、免责声明等固定语义区域保持
          原配色，避免弱化提示效果。所有投资内容仅供参考，不构成投资建议；市场有风险，入市需谨慎。
        </p>
      </div>
    </div>
  );
}
