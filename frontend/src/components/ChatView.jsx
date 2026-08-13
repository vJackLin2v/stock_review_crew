import React, { useEffect, useMemo, useRef, useState } from "react";
import * as api from "../api";
import { normalizeChatMessage, normalizeChatSession } from "../normalize";
import { fmtDate, fmtDateTime } from "../format";

// R8 聊天智能流程：当前步骤提示文案（Q7：数据需求清单绝不展示，stage/need 事件也不携带清单）
const STAGE_NOTE = {
  parse: "正在解析问题…",
  need: "分析师正在确认所需数据…",
  fetch: "正在获取数据…",
  analyze: "分析师分析中…",
  summarize: "正在综合总结…",
};

// 分析师简报展示：分析完成后逐字展开（流式观感），4 字符/16ms ≈ 250 字/s
const REVEAL_CHUNK = 4;
const REVEAL_MS = 16;

const emptyLive = (sid = null) => ({
  sessionId: sid,
  parts: [],
  summary: "",
  phase: "idle", // analyzing → reveal → summary → done
  stage: "",
  stageNote: "",
  revealPos: 0,
  revealing: false,
  summaryReady: false,
});

export default function ChatView({
  sessions,
  sessionsLoading,
  sessionsError,
  activeSessionId,
  intent,
  onIntentConsumed,
  onSelectSession,
  onPickSession,
  onRefreshSessions,
  onSessionCreated,
  onOpenStockDetail,
}) {
  const [filterTarget, setFilterTarget] = useState("");
  const [filterDate, setFilterDate] = useState("");
  const [session, setSession] = useState(null); // 归一化后的会话 {meta, messages, disclaimer}
  const [sessionLoading, setSessionLoading] = useState(false);
  const [sessionError, setSessionError] = useState("");
  const [input, setInput] = useState("");
  const [sendError, setSendError] = useState("");
  // P1-V：创建会话失败标记（错误面板据此显示「重新发起」）
  const [createFailed, setCreateFailed] = useState(false);
  // P1-V：重试计数（作为 intent effect 依赖，重新消费同一 intent）
  const [intentTick, setIntentTick] = useState(0);
  // P1-S：最近复制成功的消息 id（按钮短暂显示 ✓）
  const [copiedId, setCopiedId] = useState(null);
  // M5（R5）：聊天气泡 6 位代码 → 「名称(代码)」；null = 未加载（先显示代码），查不到/离线保持原样
  // 在途轮次按会话分桶：{sessionId: liveState}——各会话互不阻塞（可同时问 A/B）
  const [rounds, setRounds] = useState({});
  const [polling, setPolling] = useState(false); // 在途轮次接续轮询中（切走再回来）
  const createdRef = useRef(null);
  const scrollRef = useRef(null);
  const inputRef = useRef(null);
  const revealTimers = useRef(new Map());
  const activeSessionIdRef = useRef(activeSessionId);

  // P1-S：剪贴板写入（navigator.clipboard 优先，降级 execCommand）
  const copyText = async (text) => {
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
  };

  const handleCopy = async (id, text) => {
    const ok = await copyText(text);
    if (!ok) return;
    setCopiedId(id);
    window.setTimeout(() => setCopiedId((c) => (c === id ? null : c)), 1600);
  };

  useEffect(() => {
    activeSessionIdRef.current = activeSessionId;
  }, [activeSessionId]);

  // R11-R10：挂载时恢复在途标记——整页刷新/浏览器后台回收后模块级标记丢失，
  // sessionStorage 残留标记重新登记，触发下方 streamingElsewhere 轮询续接完整流程。
  useEffect(() => {
    const ids = api.restoreChatStreamingFromStorage();
    ids.forEach((sid) => {
      if (!api.isChatStreaming(sid)) api.markChatStreaming(sid);
    });
  }, []);

  // 移动端输入框：自动长高（最多 120px），换行不撑破布局
  const resizeInput = (el) => {
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 120)}px`;
  };

  // 更新指定会话的在途轮次状态（分桶写，不互相覆盖）
  const updateRound = (sid, updater) => {
    setRounds((prev) => {
      const cur = prev[sid] || emptyLive(sid);
      return { ...prev, [sid]: updater(cur) };
    });
  };

  const clearRound = (sid) => {
    stopReveal(sid);
    setRounds((prev) => {
      const next = { ...prev };
      delete next[sid];
      return next;
    });
  };

  // 分析完成后把各分析师简报逐字展开（每会话独立计时器，多会话并行互不干扰）
  const startReveal = (sid) => {
    if (revealTimers.current.has(sid)) return;
    const timer = setInterval(() => {
      setRounds((prev) => {
        const cur = prev[sid];
        if (!cur) {
          clearInterval(timer);
          revealTimers.current.delete(sid);
          return prev;
        }
        const total = cur.parts.reduce((n, p) => n + (p.full || "").length, 0);
        const pos = Math.min(total, (cur.revealPos || 0) + REVEAL_CHUNK);
        const done = pos >= total;
        if (done) {
          clearInterval(timer);
          revealTimers.current.delete(sid);
        }
        return {
          ...prev,
          [sid]: {
            ...cur,
            revealPos: pos,
            revealing: !done,
            phase: done ? (cur.summaryReady ? "summary" : "idle") : "reveal",
            stageNote: done ? (cur.summaryReady ? cur.stageNote : "") : "正在展开分析简报…",
          },
        };
      });
    }, REVEAL_MS);
    revealTimers.current.set(sid, timer);
  };

  const stopReveal = (sid) => {
    const timer = revealTimers.current.get(sid);
    if (timer) {
      clearInterval(timer);
      revealTimers.current.delete(sid);
    }
  };

  useEffect(
    () => () => {
      revealTimers.current.forEach((t) => clearInterval(t));
      revealTimers.current.clear();
    },
    []
  );

  // 展示层清理：聊天回复为自然口语文本，防御性剥离遗留 Markdown 标记
  const plain = (t) =>
    String(t || "")
      .replace(/\*\*/g, "")
      .replace(/^#{1,6}\s*/gm, "")
      .replace(/^>\s?/gm, "")
      .trim();

  // P1-S：组装多分析师消息纯文本（各分析师简评 + 总结）
  const multiPlain = (m) => {
    const parts = (Array.isArray(m.analyst_parts) ? m.analyst_parts : [])
      .map((p) => `【${p.skill_name || "分析师"}】\n${plain(p.content)}`)
      .join("\n\n");
    const summary = m.summary ? `【总结】\n${plain(m.summary)}` : "";
    return [parts, summary].filter(Boolean).join("\n\n");
  };

  const filtered = useMemo(() => {
    const t = filterTarget.trim();
    const d = filterDate.trim();
    return sessions.filter((s) => {
      const tOk = !t || (s.target || "").includes(t) || (s.target_name || "").includes(t);
      const dOk = !d || s.date === d;
      return tOk && dOk;
    });
  }, [sessions, filterTarget, filterDate]);

  // 从首页聊天卡片进入：创建会话
  // 2026-08-06 修复：effect 只依赖 intent——此前把 onIntentConsumed/onSessionCreated
  // 放入依赖数组，而 App 传入的是内联箭头函数（每次渲染新引用），创建会话期间
  // 一次无关渲染就会 cleanup（cancelled=true），导致 create 完成后跳过
  // onSessionCreated，UI 永久停在「正在加载会话消息…」（无输入框）。
  const intentCallbacksRef = useRef({ onIntentConsumed, onSessionCreated });
  useEffect(() => {
    intentCallbacksRef.current = { onIntentConsumed, onSessionCreated };
  }, [onIntentConsumed, onSessionCreated]);

  // P1-V：创建会话失败后「重新发起」——清掉消费标记并递增 tick，让 effect 重新消费同一 intent
  const retryCreate = () => {
    if (!intent) return;
    createdRef.current = null;
    setCreateFailed(false);
    setIntentTick((n) => n + 1);
  };

  useEffect(() => {
    if (!intent) return;
    const key = JSON.stringify(intent);
    if (createdRef.current === key) return;
    createdRef.current = key;
    let cancelled = false;
    setCreateFailed(false);
    (async () => {
      setSessionError("");
      setSessionLoading(true);
      try {
        const res = await api.createChatSession(intent);
        if (cancelled) return;
        intentCallbacksRef.current.onIntentConsumed();
        intentCallbacksRef.current.onSessionCreated(res.session_id);
      } catch (err) {
        if (cancelled) return;
        setSessionError((err && err.message) || "创建会话失败，请重试");
        setSessionLoading(false);
        setCreateFailed(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [intent, intentTick]);

  // 切换会话：加载消息
  useEffect(() => {
    if (!activeSessionId) {
      setSession(null);
      setSessionError("");
      return;
    }
    let cancelled = false;
    setSessionLoading(true);
    setSessionError("");
    api
      .getChatSession(activeSessionId)
      .then((raw) => {
        if (cancelled) return;
        setSession(normalizeChatSession(raw));
        setSessionLoading(false);
      })
      .catch((err) => {
        if (cancelled) return;
        setSessionError((err && err.message) || "加载会话失败");
        setSessionLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [activeSessionId]);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [session && session.messages.length, rounds[activeSessionId]]);

  const handleSend = async () => {
    const text = input.trim();
    if (!text || !session) return;
    const roundSessionId = session.meta.session_id;
    if (api.isChatStreaming(roundSessionId)) {
      setSendError("该会话正在生成回复，请稍候再试");
      return;
    }
    setSendError("");
    setInput("");
    if (inputRef.current) inputRef.current.style.height = "auto";
    api.markChatStreaming(roundSessionId);
    setRounds((prev) => ({ ...prev, [roundSessionId]: { ...emptyLive(roundSessionId) } }));
    try {
      await api.streamChatMessage(session.meta.session_id, text, (ev, pl) => {
        if (ev === "user_msg") {
          if (activeSessionIdRef.current !== roundSessionId) return; // 只写入所属会话
          // R12 修复：原始 payload 无 isUser 字段，未归一化时渲染走左侧分支
          // （用户消息显示在左边，回答完成整体归一化后才回右侧）
          const userMsg = normalizeChatMessage(pl, prev?.messages?.length ?? 0);
          setSession((prev) =>
            prev ? { ...prev, messages: [...prev.messages, userMsg] } : prev
          );
        } else if (ev === "stage") {
          // 阶段提示：优先用后端 note，缺失时按 stage 名兜底（绝不渲染需求清单）
          updateRound(roundSessionId, (base) => {
            return {
              ...base,
              stage: pl.stage || base.stage,
              stageNote: pl.note || STAGE_NOTE[pl.stage] || base.stageNote,
            };
          });
        } else if (ev === "analyst_start") {
          // 分析期间只显示「xx 正在分析…」，不展示过程文本
          updateRound(roundSessionId, (prev) => ({
            ...prev,
            phase: "analyzing",
            stage: "analyze",
            stageNote: `${pl.name || "分析师"} 正在分析…`,
            parts: [...prev.parts, { name: pl.name || "", full: "" }],
          }));
        } else if (ev === "analyst_delta") {
          // 分析过程文本先缓冲，分析完成后统一展开
          updateRound(roundSessionId, (prev) => {
            const parts = prev.parts.slice();
            const idx = Math.min(Number(pl.index) || 0, parts.length - 1);
            if (idx < 0) return prev;
            parts[idx] = {
              name: pl.name || parts[idx].name,
              full: (parts[idx].full || "") + (pl.delta || ""),
            };
            return { ...prev, parts };
          });
        } else if (ev === "analyst_end") {
          updateRound(roundSessionId, (prev) => {
            const parts = prev.parts.slice();
            const idx = Math.min(Number(pl.index) || 0, parts.length - 1);
            if (idx >= 0) parts[idx] = { name: pl.name || parts[idx].name, full: pl.content || parts[idx].full || "" };
            return {
              ...prev,
              parts,
              phase: prev.phase === "analyzing" ? "reveal" : prev.phase,
              stageNote: "正在展开分析简报…",
            };
          });
          startReveal(roundSessionId);
        } else if (ev === "summary_start") {
          updateRound(roundSessionId, (prev) => ({
            ...prev,
            summaryReady: true,
            phase: prev.revealing ? prev.phase : "summary",
            stage: "summarize",
            stageNote: "正在综合总结…",
          }));
        } else if (ev === "summary_delta") {
          updateRound(roundSessionId, (prev) =>
            ({ ...prev, phase: "summary", summary: prev.summary + (pl.delta || "") })
          );
        } else if (ev === "summary_end") {
          updateRound(roundSessionId, (prev) =>
            ({ ...prev, summary: pl.content || prev.summary, phase: "done" })
          );
        } else if (ev === "done") {
          stopReveal(roundSessionId);
          api.markChatStreamDone(roundSessionId);
          const msgs = (Array.isArray(pl.messages) ? pl.messages : []).map((m, i) => normalizeChatMessage(m, i));
          if (activeSessionIdRef.current === roundSessionId) {
            setSession((prev) => ({
              ...(prev || {}),
              messages: msgs,
              disclaimer: pl.disclaimer || (prev && prev.disclaimer) || "仅供参考，不构成投资建议",
            }));
          }
          clearRound(roundSessionId);
          onRefreshSessions();
        } else if (ev === "error") {
          stopReveal(roundSessionId);
          api.markChatStreamDone(roundSessionId);
          if (activeSessionIdRef.current === roundSessionId) {
            setSendError((pl && pl.message) || "发送失败，请重试");
          }
          clearRound(roundSessionId);
        }
      });
    } catch (err) {
      // 连接中断：后端 worker 仍会跑完整轮并落盘，由轮询接续，不注销在途标记
      stopReveal(roundSessionId);
      if (activeSessionIdRef.current === roundSessionId) {
        setSendError((err && err.message) || "连接中断，回复将在后台生成，请稍候查看");
      }
      clearRound(roundSessionId);
    }
  };

  // 在途轮次接续：切走再回来时轮询会话，直到本轮回复落盘（完整流程不丢结果）
  const streamingHere = !!rounds[activeSessionId];
  const streamingElsewhere = api.isChatStreaming(activeSessionId) && !streamingHere;
  useEffect(() => {
    if (!activeSessionId || !streamingElsewhere) {
      setPolling(false);
      return undefined;
    }
    let cancelled = false;
    let timer = null;
    let fails = 0;
    setPolling(true);
    const tick = async () => {
      if (cancelled) return;
      try {
        const raw = await api.getChatSession(activeSessionId);
        if (cancelled) return;
        const normalized = normalizeChatSession(raw);
        const msgs = normalized.messages || [];
        const roundDone = msgs.length > 0 && !msgs[msgs.length - 1].isUser;
        if (roundDone) {
          api.markChatStreamDone(activeSessionId);
          setSession(normalized);
          onRefreshSessions();
          setPolling(false);
          return;
        }
        fails = 0;
      } catch {
        fails += 1;
        if (fails >= 10) {
          api.markChatStreamDone(activeSessionId); // 后端不可达：停止轮询，避免无限重试
          setPolling(false);
          return;
        }
      }
      timer = setTimeout(tick, 2000);
    };
    timer = setTimeout(tick, 400);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [activeSessionId, streamingElsewhere, onRefreshSessions]);

  const active = session ? session.meta : sessions.find((s) => s.session_id === activeSessionId) || null;
  const round = activeSessionId ? rounds[activeSessionId] : null; // 分桶后在途轮次，不会串到其它会话
  const roundActive = !!round;

  // M5（R5）：从全部可见消息文本聚合 6 位代码候选（含在途 reveal 增量），供一次性批量查询
  const bubbleTexts = useMemo(() => {
    const out = [];
    (session && session.messages ? session.messages : []).forEach((m) => {
      if (m && m.content) out.push(String(m.content));
      (Array.isArray(m && m.analyst_parts) ? m.analyst_parts : []).forEach((p) => {
        if (p && p.content) out.push(String(p.content));
      });
      if (m && m.summary) out.push(String(m.summary));
    });
    if (round) {
      (Array.isArray(round.parts) ? round.parts : []).forEach((p) => {
        if (p && p.full) out.push(String(p.full));
      });
      if (round.summary) out.push(String(round.summary));
    }
    return out;
  }, [session, round]);

  return (
    <div className="chat-layout">
      {/* 会话列表 */}
      <div className="chat-sessions">
        <div className="chat-sessions-head">
          <div className="card-title">会话列表</div>
          <div className="chat-filters">
            <input
              className="search-input"
              placeholder="按标的过滤"
              value={filterTarget}
              onChange={(e) => setFilterTarget(e.target.value)}
            />
            <input
              className="search-input"
              type="date"
              value={filterDate}
              onChange={(e) => setFilterDate(e.target.value)}
            />
          </div>
        </div>
        <div className="chat-session-scroll">
          {sessionsLoading ? (
            <div className="sidebar-empty">正在加载会话…</div>
          ) : sessionsError ? (
            <div className="sidebar-empty">
              {sessionsError}
              <button className="btn btn-ghost btn-xs" style={{ marginTop: 8 }} onClick={onRefreshSessions}>
                重试
              </button>
            </div>
          ) : filtered.length === 0 ? (
            <div className="sidebar-empty">暂无匹配会话，可从首页聊天卡片发起新对话</div>
          ) : (
            filtered.map((s) => (
              <div
                key={s.session_id}
                role="button"
                tabIndex={0}
                className={`chat-session-item${activeSessionId === s.session_id ? " active" : ""}`}
                      onClick={() => {
                        onSelectSession(s.session_id);
                        if (onPickSession) onPickSession(s);
                      }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onSelectSession(s.session_id);
                  }
                }}
              >
                <div className="chat-session-title">{s.title || `${s.target_name || s.target} · 对话`}</div>
                <div className="chat-session-meta">
                  <span className="tag-chip light">{s.target_type === "sector" ? "板块" : "个股"}</span>
                  <span className="zero">{s.analysts.length} 位分析师 · {s.message_count} 条</span>
                  <span className="hist-time">{fmtDateTime(s.created_at)}</span>
                </div>
              </div>
            ))
          )}
        </div>
      </div>

      {/* 消息流 */}
      <div className="chat-main">
        {sessionLoading || (!session && activeSessionId) ? (
          <div className="loading-card">
            <div className="spinner" />
            <div style={{ marginTop: 10 }}>正在加载会话消息…</div>
          </div>
        ) : sessionError ? (
          <div className="error-panel">
            <div className="error-icon">⚠️</div>
            <div className="error-title">会话加载失败</div>
            <div className="error-detail">{sessionError}</div>
            {createFailed ? (
              <div className="error-actions">
                <button type="button" className="btn btn-accent" onClick={retryCreate}>
                  重新发起
                </button>
              </div>
            ) : null}
          </div>
        ) : !active ? (
            <div className="chat-empty">
          <div className="chat-empty-icon">💬</div>
          <div>选择会话查看对话，或返回首页聊天卡片发起新对话</div>
          </div>
        ) : (
          <>
            <div className="chat-head">
              <div>
                <div className="chat-head-title">{active.title || `${active.target_name} · 分析师对话`}</div>
                <div className="chat-head-sub">
                  {active.target_type === "sector" ? "板块" : "个股"} · {active.target_name || active.target}
                  {active.date ? ` · ${fmtDate(active.date)}` : ""}
                </div>
              </div>
              <div className="chat-head-analysts">
                {(active.analysts && active.analysts.length ? active.analysts : []).map((name) => (
                  <span key={name} className="tag-chip light">{name}</span>
                ))}
                {active.target_type === "stock" ? (
                  <button
                    className="btn btn-outline btn-sm"
                    aria-label="查看个股详情"
                    onClick={() => onOpenStockDetail(active.target, active.target_name || active.target)}
                  >
                    📊 详情
                  </button>
                ) : null}
              </div>
            </div>

            {/* P1-T：移动端会话切换（横向 chips；≤760px 会话列表隐藏后显示） */}
            {filtered.length ? (
              <div className="session-switch" role="tablist" aria-label="切换会话">
                {filtered.map((s) => (
                  <button
                    key={s.session_id}
                    type="button"
                    role="tab"
                    aria-selected={activeSessionId === s.session_id}
                    className={`session-chip${activeSessionId === s.session_id ? " active" : ""}`}
                        onClick={() => {
                          onSelectSession(s.session_id);
                          if (onPickSession) onPickSession(s);
                        }}
                  >
                    {s.title || `${s.target_name || s.target} · 对话`}
                  </button>
                ))}
              </div>
            ) : null}

            {round && round.stageNote && !round.summary && round.phase !== "done" ? (
              <div className="chat-stage-hint" role="status" aria-live="polite">
                <span className="stage-spinner" />
                <span>{round.stageNote}</span>
              </div>
            ) : null}
            {polling ? (
              <div className="chat-stage-hint" role="status" aria-live="polite">
                <span className="stage-spinner" />
                <span>正在生成回复（后台进行中），请稍候…</span>
              </div>
            ) : null}

            <div className="msg-scroll" ref={scrollRef}>
              {session.messages.length === 0 ? (
                <div className="empty">暂无消息，输入问题开始对话</div>
              ) : (
                session.messages.map((m) =>
                  m.isUser ? (
                    <div key={m.id} className="msg user">
                      <div className="msg-bubble">{plain(m.content)}</div>
                      <button
                        type="button"
                        className={`msg-copy${copiedId === m.id ? " copied" : ""}`}
                        aria-label="复制消息"
                        onClick={() => handleCopy(m.id, plain(m.content))}
                      >
                        {copiedId === m.id ? "✓" : "复制"}
                      </button>
                    </div>
                  ) : m.isMulti ? (
                    <div key={m.id} className="msg multi">
                      <div className="msg-multi-head">
                        <span>🤝 分析师简报</span>
                        <button
                          type="button"
                          className={`msg-copy${copiedId === m.id ? " copied" : ""}`}
                          aria-label="复制消息"
                          onClick={() => handleCopy(m.id, multiPlain(m))}
                        >
                          {copiedId === m.id ? "✓" : "复制"}
                        </button>
                      </div>
                      {m.analyst_parts.map((p, i) => (
                        <div key={i} className="msg-part">
                          <span className="msg-part-name">{p.skill_name || `分析师 ${i + 1}`}</span>
                          <div className="msg-part-content">{plain(p.content)}</div>
                        </div>
                      ))}
                      {m.summary ? (
                        <div className="msg-summary">
                          <b>总结</b>
                          <div>{plain(m.summary)}</div>
                        </div>
                      ) : null}
                    </div>
                  ) : (
                    <div key={m.id} className="msg">
                      <div className="msg-bubble">
                        {m.analyst_name ? <div className="msg-analyst-name">{m.analyst_name}</div> : null}
                        {plain(m.content)}
                      </div>
                      <button
                        type="button"
                        className={`msg-copy${copiedId === m.id ? " copied" : ""}`}
                        aria-label="复制消息"
                        onClick={() => handleCopy(m.id, plain(m.content))}
                      >
                        {copiedId === m.id ? "✓" : "复制"}
                      </button>
                    </div>
                  )
                )
              )}
              {round && (round.parts.length > 0 || round.summary) ? (
                <div className="msg multi streaming">
                  <div className="msg-multi-head">
                    {round.phase === "analyzing" ? "🤝 分析师分析中" : "🤝 分析师简报"}
                  </div>
                  {(() => {
                    // 按 revealPos 逐字展开各分析师简报（先到的先展示，不重复展开）
                    let acc = 0;
                    const items = round.parts.map((p) => {
                      const start = acc;
                      const len = (p.full || "").length;
                      acc += len;
                      return { p, start, len };
                    });
                    return items.map((it, i) => {
                      const { p, start, len } = it;
                      const pos = round.revealPos || 0;
                      const reached = pos > start || !round.revealing;
                      if (!reached) return null;
                      const shown = Math.max(0, Math.min(len, pos - start));
                      const cursorOn = round.revealing && pos >= start && pos <= start + len;
                      return (
                        <div key={i} className="msg-part">
                          <span className="msg-part-name">{p.name || `分析师 ${i + 1}`}</span>
                          <div className="msg-part-content">
                            {plain(p.full.slice(0, shown))}
                            {cursorOn ? <span className="cursor">▍</span> : null}
                          </div>
                        </div>
                      );
                    });
                  })()}
                  {round.summary ? (
                    <div className="msg-summary">
                      <b>总结</b>
                      <div>
                        {plain(round.summary)}
                        {round.phase === "summary" ? <span className="cursor">▍</span> : null}
                      </div>
                    </div>
                  ) : null}
                </div>
              ) : null}
            </div>

            {sendError ? <div className="upload-error">{sendError}</div> : null}
            <div className="chat-input-row">
              <textarea
                ref={inputRef}
                className="chat-input"
                placeholder="输入问题，Enter 发送（Shift+Enter 换行）"
                value={input}
                disabled={roundActive}
                rows={1}
                enterKeyHint="send"
                onChange={(e) => {
                  setInput(e.target.value);
                  resizeInput(e.target);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                    e.preventDefault();
                    handleSend();
                  }
                }}
              />
              <button className="btn btn-accent" disabled={roundActive || !input.trim()} onClick={handleSend}>
                {roundActive ? "发送中…" : "发送"}
              </button>
            </div>
            <div className="chat-disclaimer">
              <span>⚠️</span>
              <span>免责声明：{session.disclaimer || "仅供参考，不构成投资建议"}。市场有风险，入市需谨慎。</span>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
