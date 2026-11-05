// R10 F12 设置页：深色/浅色主题、涨跌配色、首页默认图表周期、全站字号。
// 以上偏好为 localStorage 立即生效；「模拟交易设置-初始资金」走后端 API 持久化。
import React, { useEffect, useState } from "react";
import { getAccountSettings, getLlmSettings, getSkills, saveAccountSettings, saveLlmSettings, saveSkill } from "../api";
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

export default function SettingsView({ onOpenPrompts }) {
  const s = useSettings();
  const set = (key, value) => saveSettings({ [key]: value });
  const palette = UP_DOWN_PALETTES[s.upDownColors] || UP_DOWN_PALETTES.redUpGreenDown;
  const [initialCash, setInitialCash] = useState(100000);
  const [cashDraft, setCashDraft] = useState("100000");
  const [cashStatus, setCashStatus] = useState("");
  const [llmModel, setLlmModel] = useState("");
  const [llmOptions, setLlmOptions] = useState([]);
  const [llmStatus, setLlmStatus] = useState("");

  useEffect(() => {
    let alive = true;
    getAccountSettings()
      .then((data) => {
        if (!alive || !data) return;
        setInitialCash(Number(data.initial_cash || 100000));
        setCashDraft(String(data.initial_cash || 100000));
      })
      .catch(() => {
        if (alive) setCashStatus("无法读取后端设置，当前显示默认值");
      });
    return () => { alive = false; };
  }, []);

  const saveCash = async () => {
    const value = Number(cashDraft);
    if (!Number.isFinite(value) || value < 10000) {
      setCashStatus("初始资金必须为不小于 10,000 元的数字");
      return;
    }
    try {
      const data = await saveAccountSettings({ initial_cash: value });
      setInitialCash(Number(data.initial_cash));
      setCashDraft(String(data.initial_cash));
      setCashStatus("已保存，下次开户/回测生效");
    } catch (err) {
      setCashStatus(`保存失败：${err.message || "请检查后端服务"}`);
    }
  };

  useEffect(() => {
    let alive = true;
    getLlmSettings()
      .then((data) => {
        if (!alive || !data) return;
        setLlmModel(data.model || "");
        setLlmOptions(Array.isArray(data.options) ? data.options : []);
      })
      .catch(() => setLlmStatus("无法读取模型设置"));
    return () => { alive = false; };
  }, []);

  const [skills, setSkills] = useState([]);
  const [selectedSkillId, setSelectedSkillId] = useState("");
  const [skillDrafts, setSkillDrafts] = useState({ prompt: "", style_anchor: "", trading_model: "" });
  const [skillStatus, setSkillStatus] = useState("");

  useEffect(() => {
    let alive = true;
    getSkills()
      .then((data) => {
        if (!alive || !data || !Array.isArray(data.skills)) return;
        setSkills(data.skills);
        if (data.skills.length > 0) {
          const first = data.skills[0];
          setSelectedSkillId(first.id);
          setSkillDrafts({
            prompt: first.prompt || "",
            style_anchor: first.style_anchor || "",
            trading_model: first.trading_model || "",
          });
        }
      })
      .catch(() => {
        if (alive) setSkillStatus("无法读取分析师提示词，请确认后端服务已启动");
      });
    return () => { alive = false; };
  }, []);

  const selectSkill = (skill) => {
    setSelectedSkillId(skill.id);
    setSkillDrafts({
      prompt: skill.prompt || "",
      style_anchor: skill.style_anchor || "",
      trading_model: skill.trading_model || "",
    });
    setSkillStatus("");
  };

  const saveSkillPrompt = async () => {
    if (!selectedSkillId) return;
    try {
      await saveSkill(selectedSkillId, {
        prompt: skillDrafts.prompt,
        style_anchor: skillDrafts.style_anchor,
        trading_model: skillDrafts.trading_model,
      });
      setSkillStatus("提示词已保存，下次复盘/回测生效");
    } catch (err) {
      setSkillStatus(`保存失败：${err.message || "请检查后端服务"}`);
    }
  };


  const saveLlm = async () => {
    if (!llmModel.trim()) {
      setLlmStatus("请选择或输入模型名称");
      return;
    }
    try {
      const data = await saveLlmSettings({ model: llmModel.trim() });
      setLlmModel(String(data.model || llmModel.trim()));
      setLlmStatus("模型已切换，下次 LLM 调用生效");
    } catch (err) {
      setLlmStatus(`保存失败：${err.message || "请检查后端服务"}`);
    }
  };

  return (
    <div className="settings-page">
      <div className="card">
        <h3 className="card-title">设置</h3>
        <p className="card-sub">
          外观与图表偏好保存在本机浏览器（localStorage）并立即生效；下方“模拟交易设置”由后端保存。
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
          <h3 className="card-title">模拟交易设置</h3>
          <p className="card-sub">分析师初始资金将用于模拟账户开户与历史回测（需重置账户/重新回测后生效）。</p>
          <div className="set-section">
            <div className="set-label">分析师初始账号资金（元）</div>
            <div className="set-desc">默认 100,000 元；修改后新开户与下次回测生效，已有账户不追溯。</div>
            <div className="set-options" style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <input
                type="number"
                min="10000"
                step="1000"
                value={cashDraft}
                onChange={(e) => setCashDraft(e.target.value)}
                style={{ padding: "6px 8px", width: 180 }}
              />
              <button type="button" className="set-option active" onClick={saveCash}>
                <span className="set-option-label">保存</span>
              </button>
            </div>
            {cashStatus ? (
              <p className="card-sub" style={{ marginTop: 8, marginBottom: 0 }}>{cashStatus}</p>
            ) : null}
            <p className="card-sub" style={{ marginTop: 8, marginBottom: 0 }}>
              当前生效：{Number(initialCash || 0).toLocaleString("zh-CN")} 元 / 位分析师
            </p>
          </div>
        </div>

          <div className="card">
            <h3 className="card-title">LLM 模型设置</h3>
            <p className="card-sub">选择用于复盘/聊天/交割单分析的 LLM 模型，保存后立即生效。</p>
            <div className="set-section">
              <div className="set-label">模型名称</div>
              <div className="set-desc">可选手动输入自定义模型名，需与 DEEPSEEK_BASE_URL 兼容。</div>
              <div className="set-options" style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                <select
                  value={llmModel}
                  onChange={(e) => { setLlmModel(e.target.value); setLlmStatus(""); }}
                  style={{ padding: "6px 8px", minWidth: 220 }}
                >
                  {llmOptions.map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                  {llmModel && !llmOptions.includes(llmModel) ? (
                    <option value={llmModel}>{llmModel}（当前自定义）</option>
                  ) : null}
                </select>
                <button type="button" className="set-option active" onClick={saveLlm}>
                  <span className="set-option-label">保存模型</span>
                </button>
              </div>
              {llmStatus ? (
                <p className="card-sub" style={{ marginTop: 8, marginBottom: 0 }}>{llmStatus}</p>
              ) : null}
            </div>
          </div>

          <div className="card">
            <h3 className="card-title">分析师提示词管理</h3>
            <p className="card-sub">提示词已迁移到独立页面，使用 Markdown 预览更清晰，并保留编辑/保存能力。</p>
            <div className="set-options" style={{ marginTop: 8 }}>
              <button type="button" className="set-option active" onClick={onOpenPrompts}>
                <span className="set-option-label">打开分析师提示词页面</span>
              </button>
            </div>
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
