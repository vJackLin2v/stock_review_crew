import React from "react";
import { stageLabel, visibleStages } from "../stages";

function stepText(s, job) {
  if (s.key === "analysts") {
    const done = Math.min(Math.max(0, job.analysts_done || 0), 5);
    return `分析师 ${done}/5`;
  }
  return s.label;
}

export default function ProgressView({ job, onNew }) {
  const isError = job.status === "error";
  const isQueued = job.status === "queued";
  const stages = visibleStages(); // 2026-08-12：资讯开关关闭时隐藏「资讯」步骤
  const idx = isQueued
    ? -1
    : stages.findIndex((s) => String(job.stage || "").toLowerCase().includes(s.key));
  const pct = Math.max(0, Math.min(100, Number(job.pct) || 0));

  if (isError) {
    return (
      <div className="card progress-card error-panel">
        <div className="error-icon">⚠️</div>
        <div className="error-title">复盘失败</div>
        <div className="error-detail">{job.error || "发生未知错误，请稍后重试"}</div>
        <div className="error-actions">
          <button className="btn btn-accent" onClick={onNew}>返回首页</button>
        </div>
      </div>
    );
  }

  return (
    <div className="card progress-card">
      <div className="progress-head">
        <div className="spinner" />
        <div className="progress-title">
          {isQueued ? "任务已提交，排队等待…" : `正在生成${job.metaLabel || ""}复盘`}
        </div>
        {job.offline ? <span className="pill offline">离线 mock 模式（DEV 降级）</span> : null}
      </div>
      <p className="card-sub">后端任务每 1 秒自动刷新进度，切换页面不中断任务，请勿重复提交</p>

      <div className="steps">
        {stages.map((s, i) => {
          const state = isQueued ? "pending" : i < idx ? "done" : i === idx ? "current" : "pending";
          return (
            <div key={s.key} className={`step ${state}`}>
              <div className="step-dot">{i < idx ? "✓" : i === idx && !isQueued ? String(i + 1) : ""}</div>
              <div className="step-label">{stepText(s, job)}</div>
            </div>
          );
        })}
      </div>

      <div className="progress-track">
        <div className="progress-fill" style={{ width: `${pct}%` }} />
      </div>
      <div className="progress-pct">{pct}%</div>
      <div className="progress-msg">
        {isQueued ? "排队等待中…" : job.message || `阶段：${stageLabel(job.stage)}`}
      </div>

      <div className="progress-actions">
        <button className="btn btn-ghost btn-sm" onClick={onNew}>返回首页（任务后台继续）</button>
      </div>
    </div>
  );
}
