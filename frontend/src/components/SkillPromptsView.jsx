// 2026-08-17：分析师提示词独立页面
// 与设置页分离，使用 Markdown 预览让提示词更清晰可读；同时保留编辑/保存能力。
import React, { useEffect, useState } from "react";
import * as api from "../api";
import MarkdownView from "./MarkdownView";

export default function SkillPromptsView({ onBack }) {
  const [skills, setSkills] = useState([]);
  const [selectedId, setSelectedId] = useState("");
  const [drafts, setDrafts] = useState({ prompt: "", style_anchor: "", trading_model: "" });
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    api.getSkills()
      .then((data) => {
        if (!alive || !data || !Array.isArray(data.skills)) return;
        setSkills(data.skills);
        if (data.skills.length > 0) {
          const first = data.skills[0];
          setSelectedId(first.id);
          setDrafts({
            prompt: first.prompt || "",
            style_anchor: first.style_anchor || "",
            trading_model: first.trading_model || "",
          });
        }
      })
      .catch(() => setStatus("无法读取分析师提示词，请确认后端服务已启动"))
      .finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, []);

  const selectSkill = (skill) => {
    setSelectedId(skill.id);
    setDrafts({
      prompt: skill.prompt || "",
      style_anchor: skill.style_anchor || "",
      trading_model: skill.trading_model || "",
    });
    setStatus("");
  };

  const save = async () => {
    if (!selectedId) return;
    try {
      await api.saveSkill(selectedId, {
        prompt: drafts.prompt,
        style_anchor: drafts.style_anchor,
        trading_model: drafts.trading_model,
      });
      setStatus("提示词已保存，下次复盘/回测生效");
    } catch (err) {
      setStatus(`保存失败：${(err && err.message) || "请检查后端服务"}`);
    }
  };

  const selected = skills.find((s) => s.id === selectedId) || null;

  return (
    <div className="settings-page">
      <div className="card">
        <div className="accounts-header">
          <button type="button" className="btn btn-outline btn-sm" onClick={onBack}>
            ← 返回设置
          </button>
          <div>
            <h3 className="card-title" style={{ marginBottom: 0 }}>分析师提示词</h3>
            <p className="card-sub">查看/编辑 5 位分析师的系统提示词、风格锚点与交易模型，使用 Markdown 预览。</p>
          </div>
        </div>
      </div>

      {loading ? (
        <div className="card loading-card"><div className="spinner" />正在加载提示词…</div>
      ) : (
        <>
          <div className="card">
            <div className="set-options" style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              {skills.map((skill) => (
                <button
                  key={skill.id}
                  type="button"
                  className={`set-option${selectedId === skill.id ? " active" : ""}`}
                  onClick={() => selectSkill(skill)}
                  aria-pressed={selectedId === skill.id}
                >
                  <span className="set-option-label">{skill.name || skill.id}</span>
                  <span className="set-option-hint">{skill.role || skill.group || ""}</span>
                </button>
              ))}
            </div>
          </div>

          {selected ? (
            <div className="card">
              <div className="card-head-row">
                <div>
                  <h3 className="card-title">{selected.name || selected.id} · prompt 预览</h3>
                  <p className="card-sub">下方为 Markdown 渲染效果；如需修改请使用底部编辑区。</p>
                </div>
                <button type="button" className="btn btn-accent btn-sm" onClick={save}>保存当前分析师提示词</button>
              </div>
              <div className="skill-prompt-preview">
                <MarkdownView text={drafts.prompt} />
              </div>

              <div className="set-section" style={{ marginTop: 18 }}>
                <div className="set-label">style_anchor（风格锚点）</div>
                <div className="skill-prompt-preview"><MarkdownView text={drafts.style_anchor || "（空）"} /></div>
                <textarea
                  value={drafts.style_anchor}
                  onChange={(e) => setDrafts({ ...drafts, style_anchor: e.target.value })}
                  rows={4}
                  style={{ width: "100%", fontFamily: "monospace", fontSize: 12 }}
                />
              </div>

              <div className="set-section" style={{ marginTop: 12 }}>
                <div className="set-label">trading_model（交易模型）</div>
                <div className="skill-prompt-preview"><MarkdownView text={drafts.trading_model || "（空）"} /></div>
                <textarea
                  value={drafts.trading_model}
                  onChange={(e) => setDrafts({ ...drafts, trading_model: e.target.value })}
                  rows={6}
                  style={{ width: "100%", fontFamily: "monospace", fontSize: 12 }}
                />
              </div>

              <div className="set-section" style={{ marginTop: 12 }}>
                <div className="set-label">prompt（系统提示词原文）</div>
                <textarea
                  value={drafts.prompt}
                  onChange={(e) => setDrafts({ ...drafts, prompt: e.target.value })}
                  rows={18}
                  style={{ width: "100%", fontFamily: "monospace", fontSize: 12 }}
                />
              </div>

              {status ? (
                <p className="card-sub" style={{ marginTop: 8, marginBottom: 0 }}>{status}</p>
              ) : null}
            </div>
          ) : (
            <div className="card"><p className="card-sub">暂无分析师可编辑。</p></div>
          )}
        </>
      )}

      <div className="card">
        <p className="card-sub" style={{ marginBottom: 0 }}>
          所有投资内容仅供参考，不构成投资建议；提示词修改会直接影响后续复盘与回测。
        </p>
      </div>
    </div>
  );
}
