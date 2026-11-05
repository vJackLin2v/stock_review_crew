import React from "react";
import DataTab from "./DataTab";
import ReviewTab from "./ReviewTab";
import ChatTab from "./ChatTab";
import TradesTab from "./TradesTab";
import ZTReviewTab from "./ZTReviewTab";

// R11-R1：首页重构为 4 个 Tab（数据展示 | 当日复盘 | 分析师聊天 | 交割单分析）
// - 默认首个 Tab = 数据展示
// - tab 切换使用 display:none（CSS .home-tab-panel）而非条件卸载，保持 ChatView 等挂载，
//   避免聊天在途状态 / 轮询丢失（R10）
const TABS = [
  { key: "data", label: "数据展示" },
  { key: "review", label: "当日复盘" },
  { key: "zt", label: "涨停复盘" },
  { key: "chat", label: "分析师聊天" },
  { key: "trades", label: "交割单分析" },
];

export default function HomeView({
  homeTab,
  onHomeTabChange,
  history,
  onRetryHistory,
  onDeleteReview,
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
  return (
    <>
      <div className="page-hero">
        <h1 className="page-title">A股市场复盘</h1>
        <p className="page-sub">
          多智能体复盘系统 · 6 时间模式 · 实时行情总览 · 仅供参考，不构成投资建议
        </p>
      </div>

      <div className="home-tabs" role="tablist" aria-label="首页功能页签">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={homeTab === t.key}
            className={`home-tab-btn${homeTab === t.key ? " active" : ""}`}
            onClick={() => onHomeTabChange(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="home-tab-panels">
        <div
          role="tabpanel"
          className={`home-tab-panel${homeTab === "data" ? " active" : ""}`}
          aria-hidden={homeTab !== "data"}
        >
          <DataTab onOpenStockDetail={onOpenStockDetail} />
        </div>
        <div
          role="tabpanel"
          className={`home-tab-panel${homeTab === "review" ? " active" : ""}`}
          aria-hidden={homeTab !== "review"}
        >
          <ReviewTab
            active={homeTab === "review"}
            history={history}
            onRetryHistory={onRetryHistory}
            onDeleteReview={onDeleteReview}
          />
        </div>
        <div
          role="tabpanel"
          className={`home-tab-panel${homeTab === "zt" ? " active" : ""}`}
          aria-hidden={homeTab !== "zt"}
        >
          <ZTReviewTab />
        </div>
        <div
          role="tabpanel"
          className={`home-tab-panel${homeTab === "chat" ? " active" : ""}`}
          aria-hidden={homeTab !== "chat"}
        >
          <ChatTab
            onOpenChat={onOpenChat}
            onOpenStockDetail={onOpenStockDetail}
            chatSessions={chatSessions}
            chatSessionsLoading={chatSessionsLoading}
            chatSessionsError={chatSessionsError}
            activeChatSessionId={activeChatSessionId}
            chatIntent={chatIntent}
            onChatIntentConsumed={onChatIntentConsumed}
            onSelectChatSession={onSelectChatSession}
            onRefreshChatSessions={onRefreshChatSessions}
            onChatSessionCreated={onChatSessionCreated}
          />
        </div>
        <div
          role="tabpanel"
          className={`home-tab-panel${homeTab === "trades" ? " active" : ""}`}
          aria-hidden={homeTab !== "trades"}
        >
          <TradesTab />
        </div>
      </div>
    </>
  );
}
