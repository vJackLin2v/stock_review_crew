import React, { useState } from "react";
import * as api from "../api";
import { ANALYSTS } from "../mock";
import ChatView from "./ChatView";

// R11-R1 分析师聊天 Tab：会话发起卡片（迁移自 HomeView）+ 嵌入 ChatView（保持挂载）
export default function ChatTab({
  onOpenChat,
  onOpenStockDetail,
  chatSessions,
  chatSessionsLoading,
  chatSessionsError,
  activeChatSessionId,
  chatIntent,
  onChatIntentConsumed,
  onSelectChatSession,
  onRefreshChatSessions,
  onChatSessionCreated,
}) {
  const [targetType, setTargetType] = useState("stock");
  const [target, setTarget] = useState("");
  const [selectedAnalysts, setSelectedAnalysts] = useState([]);
  const [chatError, setChatError] = useState("");

  const toggleAnalyst = (id) => {
    setSelectedAnalysts((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    );
    setChatError("");
  };

  const handleOpenChat = () => {
    setChatError("");
    const t = target.trim();
    if (!t) {
      setChatError(targetType === "stock" ? "请输入股票代码或名称" : "请输入板块名称");
      return;
    }
    if (selectedAnalysts.length === 0) {
      setChatError("请至少选择一位分析师");
      return;
    }
    onOpenChat({
      target_type: targetType,
      target: t,
      target_name: t,
      analysts: selectedAnalysts,
      title: `${t} · 分析师对话`,
    });
  };

  // 个股详情入口：6 位代码直通，名称走搜索解析
  const handleOpenDetail = async () => {
    const t = target.trim();
    if (!t) {
      setChatError("请输入股票代码或名称");
      return;
    }
    setChatError("");
    try {
      if (/^\d{6}$/.test(t)) {
        onOpenStockDetail(t, t);
        return;
      }
      const matches = await api.searchStocks(t);
      if (!matches.length) {
        setChatError("未找到该股票，请检查名称或代码");
        return;
      }
      onOpenStockDetail(matches[0].code, matches[0].name || t);
    } catch (err) {
      setChatError((err && err.message) || "查询失败，请重试");
    }
  };

  return (
    <div className="chat-tab">
      <div className="card">
        <h3 className="card-title">发起分析师对话</h3>
        <p className="card-sub">选择标的与分析团队，多轮交叉问答（单分析师直接回复，多分析师逐位表态后汇总）</p>

        <div className="seg">
          <button
            className={`seg-btn${targetType === "stock" ? " active" : ""}`}
            onClick={() => setTargetType("stock")}
          >
            个股
          </button>
          <button
            className={`seg-btn${targetType === "sector" ? " active" : ""}`}
            onClick={() => setTargetType("sector")}
          >
            板块
          </button>
        </div>
        <input
          className="form-input"
          style={{ marginTop: 10 }}
          placeholder={targetType === "stock" ? "输入股票代码或名称，如 600519 / 贵州茅台" : "输入板块名称，如 半导体 / 券商"}
          value={target}
          onChange={(e) => setTarget(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") handleOpenChat();
          }}
        />

        <div className="analyst-pick">
          {ANALYSTS.map((a) => {
            const on = selectedAnalysts.includes(a.id);
            return (
              <button
                key={a.id}
                type="button"
                className={`pick-chip${on ? " on" : ""}`}
                onClick={() => toggleAnalyst(a.id)}
              >
                {on ? "✓" : ""}{a.skill_name}
              </button>
            );
          })}
        </div>

        {chatError ? <div className="upload-error">{chatError}</div> : null}
        <div className="form-actions">
          <button className="btn btn-accent" onClick={handleOpenChat}>进入聊天</button>
          {targetType === "stock" ? (
            <button className="btn btn-outline" onClick={handleOpenDetail}>查看详情</button>
          ) : null}
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <ChatView
          sessions={chatSessions}
          sessionsLoading={chatSessionsLoading}
          sessionsError={chatSessionsError}
          activeSessionId={activeChatSessionId}
          intent={chatIntent}
          onIntentConsumed={onChatIntentConsumed}
          onSelectSession={onSelectChatSession}
          onPickSession={(s) => {
            // R12：点击左侧会话记录时，把会话标的（股票/板块）自动赋值到发起表单输入框
            if (s && s.target_type) setTargetType(s.target_type);
            if (s && (s.target_name || s.target)) setTarget(s.target_name || s.target);
          }}
          onRefreshSessions={onRefreshChatSessions}
          onSessionCreated={onChatSessionCreated}
          onOpenStockDetail={onOpenStockDetail}
        />
      </div>
    </div>
  );
}
