import React, { useEffect, useMemo, useState } from "react";
import MarkdownView, { buildToc } from "./MarkdownView";
import { BreadthChart, Chart, IndexMinuteChart, LimitLadderChart, SectorChart, SentimentChart, SentimentKpis } from "./Charts";
import { fmtDate, fmtDateTime, fmtTimeHM } from "../format";

const AVATAR_CLASSES = ["", "v2", "v3", "v4", "v5"];

// P1-S：剪贴板写入（navigator.clipboard 优先，降级 execCommand）
async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* 继续走降级方案 */
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

// P1-S：Markdown → 纯文本（去标记符，保留内容）
function toPlainText(md) {
  return String(md || "")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/\*([^*]+)\*/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/^\s*[-*]\s+/gm, "· ")
    .replace(/^\|/gm, "")
    .replace(/\|/g, " | ")
    .trim();
}

export default function ReportView({ report, offline, history, onSwitchReview }) {
  const meta = report.meta || {};
  const snapshot = report.snapshot || {};
  const analyses = Array.isArray(report.analyses) ? report.analyses : [];
  const debate = Array.isArray(report.debate_history) ? report.debate_history : [];
  const degraded = Array.isArray(report.degraded) ? report.degraded : [];
  const responseCount = debate.reduce((s, r) => s + (Array.isArray(r.responses) ? r.responses.length : 0), 0);
  const [copied, setCopied] = useState("");

  // P1-R：报告目录（来自 MarkdownView 解析的标题锚点）
  const toc = useMemo(() => buildToc(report.report), [report.report]);
  // R11-R5：目录随滚动高亮当前章节（可选项）
  const [activeToc, setActiveToc] = useState("");
  useEffect(() => {
    if (!toc.length) return;
    const obs = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible.length) setActiveToc(visible[0].target.id);
      },
      { rootMargin: "-80px 0px -70% 0px", threshold: 0 }
    );
    toc.forEach((t) => {
      const el = document.getElementById(t.id);
      if (el) obs.observe(el);
    });
    return () => obs.disconnect();
  }, [toc]);

  // P1-W：同日其他时间点（从前端已加载的 history 分组取，无则隐藏）
  const sameDayItems = useMemo(() => {
    if (!meta.date) return [];
    const groups = Array.isArray(history && history.groups) ? history.groups : [];
    const g = groups.find((x) => x.date === meta.date);
    if (!g) return [];
    return (Array.isArray(g.items) ? g.items : []).filter(
      (it) => `${meta.date}/${it.time}` !== `${meta.date}/${meta.time}`
    );
  }, [history, meta]);

  const scrollToId = (id) => {
    const el = document.getElementById(id);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const scrollToTop = () => {
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const doCopy = async (text, kind) => {
    const ok = await copyText(text);
    if (!ok) return;
    setCopied(kind);
    window.setTimeout(() => setCopied((c) => (c === kind ? "" : c)), 1600);
  };

  // R11-R3：数据快照去噪——来源/降级文本移入 tooltip，不再渲染横幅与红字
  const sentDegraded =
    snapshot.sentiment && Array.isArray(snapshot.sentiment.degraded)
      ? snapshot.sentiment.degraded
      : [];
  const snapshotTitle = [
    snapshot.source ? `数据来源：${snapshot.source}` : "",
    snapshot.degraded && snapshot.degraded.length ? `降级：${snapshot.degraded.join("；")}` : "",
    sentDegraded.length ? `情绪数据降级：${sentDegraded.join("；")}` : "",
  ]
    .filter(Boolean)
    .join("；") || undefined;

  const reportCard = (
    <div className="card">
      <div className="card-head-row">
        <div>
          <h3 className="card-title">复盘报告（Markdown）</h3>
          <p className="card-sub">由趋势派 / 情绪派分析师点评、主持人总结与辩论后自动生成</p>
        </div>
        <div className="report-copy-row">
          <button
            type="button"
            className="btn btn-outline btn-sm"
            aria-label="复制 Markdown 全文"
            onClick={() => doCopy(report.report, "md")}
          >
            {copied === "md" ? "已复制 ✓" : "复制 Markdown"}
          </button>
          <button
            type="button"
            className="btn btn-outline btn-sm"
            aria-label="复制纯文本"
            onClick={() => doCopy(toPlainText(report.report), "txt")}
          >
            {copied === "txt" ? "已复制 ✓" : "复制纯文本"}
          </button>
        </div>
      </div>
      <MarkdownView text={report.report} />
    </div>
  );

  return (
    <div className="grid" style={{ gap: 16, gridTemplateColumns: "minmax(0, 1fr)" }}>
      <div className="card result-head-card">
        <div className="result-head">
          <div>
            <div className="result-head-title">{meta.mode_label || "复盘"}报告{meta.date ? ` · ${fmtDate(meta.date)}` : ""}</div>
            <div className="result-head-sub">
              <span>日期：{fmtDate(meta.date)}</span>
              <span>时间点：{fmtTimeHM(meta.time)}</span>
              <span>创建：{fmtDateTime(meta.created_at)}</span>
              {offline ? <span className="pill offline">离线 mock</span> : null}
            </div>
            {meta.summary ? <div className="result-summary">{meta.summary}</div> : null}
          </div>
        </div>
        {sameDayItems.length ? (
          <div className="same-day-bar">
            <span className="same-day-label">同日其他时间点：</span>
            <div className="same-day-chips">
              {sameDayItems.map((it) => (
                <button
                  key={it.record_id || `${meta.date}/${it.time}`}
                  type="button"
                  className="tag-chip light"
                  aria-label={`切换查看 ${fmtTimeHM(it.time)} ${it.mode_label || ""}复盘`}
                  onClick={() => {
                    if (onSwitchReview) onSwitchReview(meta.date, it.time);
                  }}
                >
                  {fmtTimeHM(it.time)} {it.mode_label || ""}
                </button>
              ))}
            </div>
          </div>
        ) : null}
        {degraded.length ? (
          <div className="degraded-banner">
            ⚠️ 本次复盘存在降级标注（数据或能力受限，结果可能不完整）：
            <ul className="degraded-list">
              {degraded.map((d) => (
                <li key={d}>{d}</li>
              ))}
            </ul>
          </div>
        ) : null}
        <div className="disclaimer" style={{ marginTop: 12 }}>
          <span>⚠️</span>
          <div>
            <b>免责声明：</b>
            {report.disclaimer || "仅供参考，不构成投资建议。市场有风险，入市需谨慎。"}
          </div>
        </div>
      </div>

      {/* R11-R5：目录固定定位——桌面右侧 sticky 列，移动端顶部横向胶囊；「返回顶部」保留 */}
      {toc.length ? (
        <div className="report-layout">
          <aside className="report-toc report-toc-sticky" aria-label="报告目录">
            <span className="report-toc-title">📑 目录</span>
            <div className="report-toc-list">
              {toc.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  className={`toc-chip${t.level === 1 ? " l1" : t.level === 2 ? " l2" : " l3"}${activeToc === t.id ? " active" : ""}`}
                  aria-label={`跳转到 ${t.text}`}
                  onClick={() => scrollToId(t.id)}
                >
                  {t.text}
                </button>
              ))}
            </div>
            <button type="button" className="btn btn-outline btn-sm report-toc-top" onClick={scrollToTop}>
              ↑ 返回顶部
            </button>
          </aside>
          {reportCard}
        </div>
      ) : (
        reportCard
      )}

      <div className="card">
        <h3 className="card-title">{analyses.length ? `${analyses.length} 位分析师点评` : "分析师点评"}</h3>
        <p className="card-sub">点击卡片展开查看完整点评与建议</p>
        {analyses.length ? (
          <div className="analyst-list">
            {analyses.map((an, i) => (
              <details key={an.skill_id || an.skill_name || i} className="analyst">
                <summary>
                  <span className={`avatar ${AVATAR_CLASSES[i % AVATAR_CLASSES.length]}`}>
                    {String(an.skill_name || "分").slice(0, 1)}
                  </span>
                  <span>
                    <span className="a-name">{an.skill_name || `分析师${i + 1}`}</span>
                  </span>
                  {Array.isArray(an.tags) && an.tags.length ? (
                    <span className="a-tags">
                      {an.tags.map((t) => (
                        <span key={t} className="tag-chip">{t}</span>
                      ))}
                    </span>
                  ) : null}
                </summary>
                <div className="analyst-content">
                  <MarkdownView text={an.analysis || "（暂无点评内容）"} />
                  {an.suggestion ? (
                    <div className="a-suggestion">
                      <div className="a-suggestion-title">💡 操作建议</div>
                      <MarkdownView text={an.suggestion} />
                    </div>
                  ) : null}
                </div>
              </details>
            ))}
          </div>
        ) : (
          <div className="empty">后端未返回分析师点评记录</div>
        )}
      </div>

      <div className="debate-block">
        <details>
          <summary>
            🗣️ 辩论记录（{debate.length} 轮 / {responseCount} 条）
          </summary>
          <div>
            {debate.length ? (
              debate.map((r, ri) => (
                <div key={ri} className="debate-round">
                  <div className="debate-round-head">
                    第 {r.round || ri + 1} 轮{r.topic ? ` · ${r.topic}` : ""}
                  </div>
                  {(Array.isArray(r.responses) ? r.responses : []).map((resp, i) => (
                    <div key={i} className="debate-item">
                      <span className="debate-speaker">{resp.skill_name || `分析师 ${i + 1}`}</span>
                      <div style={{ marginTop: 4 }}>{resp.response || ""}</div>
                    </div>
                  ))}
                </div>
              ))
            ) : (
              <div className="empty">本期无辩论记录</div>
            )}
          </div>
        </details>
      </div>

      <div className="card">
        <h3 className="card-title">数据快照</h3>
        <p className="card-sub" title={snapshotTitle}>
          当日取数快照（比率已 ×100 展示）
        </p>
        <div className="chart-grid">
          {snapshot.index_minute &&
          snapshot.index_minute.some(
            (ix) => ix && Array.isArray(ix.points) && ix.points.length > 0
          ) ? (
            <div className="chart-block">
              <div className="chart-title">指数分时</div>
              <IndexMinuteChart indices={snapshot.index_minute} />
            </div>
          ) : null}
          <div className="chart-block">
            <div className="chart-title">涨跌停梯队</div>
            <LimitLadderChart ladder={snapshot.limit_ladder} />
          </div>
          <div className="chart-block">
            <div className="chart-title">板块涨幅榜</div>
            <SectorChart sectors={snapshot.sectors} />
          </div>
          <div className="chart-block">
            <div className="chart-title">情绪指标</div>
            <SentimentChart sentiment={snapshot.sentiment} />
          </div>
          <div className="chart-block">
            <div className="chart-title">全市场涨跌分布</div>
            <BreadthChart breadth={snapshot.breadth} />
          </div>
        </div>
        <div style={{ marginTop: 14 }}>
          <SentimentKpis sentiment={snapshot.sentiment} />
        </div>
      </div>
    </div>
  );
}
