import React, { useCallback, useEffect, useRef, useState } from "react";
import * as api from "./api";
import {
  normalizeJob,
  normalizeJobResult,
  normalizeReviewDetail,
  normalizeReviewList,
  normalizeChatSessionList,
} from "./normalize";
import { modeLabel } from "./modes";
import ErrorBoundary from "./components/ErrorBoundary";
import Sidebar from "./components/Sidebar";
import HomeView from "./components/HomeView";
import ProgressView from "./components/ProgressView";
import ReportView from "./components/ReportView";
import StockDetail from "./components/StockDetail";
import SettingsView from "./components/SettingsView";
// R12-A3：侧栏「模拟账户」独立视图（分析师模拟账户展示）
import AccountsPanel from "./components/AccountsPanel";
import { applyToDom, loadSettings } from "./settings";
import { setNewsEnabled } from "./stages";

export default function App() {
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem("stock-review.sidebarCollapsed") === "1";
    } catch {
      return false;
    }
  });
  const [sidebarOpen, setSidebarOpen] = useState(false); // 移动端抽屉
  // 视图：home | progress | report | stock | settings | accounts
  const [view, setView] = useState("home");
  // R11-R1：首页 Tab（data | review | chat | trades），默认首个 Tab = 数据展示
  const [homeTab, setHomeTab] = useState("data");
  // 运行中任务全局保留：切换视图不停止轮询、不丢失任务
  const [job, setJob] = useState(null);
  const [report, setReport] = useState(null);
  const [reportLoading, setReportLoading] = useState(false);
  const [reportError, setReportError] = useState(null);
  // 历史复盘
  const [history, setHistory] = useState({ groups: [], loading: true, error: null, offline: false });
  // 聊天
  const [chatSessions, setChatSessions] = useState([]);
  const [chatSessionsLoading, setChatSessionsLoading] = useState(true);
  const [chatSessionsError, setChatSessionsError] = useState("");
  const [stockQuery, setStockQuery] = useState(null); // {code, name, from}
  const [activeChatSessionId, setActiveChatSessionId] = useState(null);
  const [chatIntent, setChatIntent] = useState(null);

  const pollRef = useRef(null);
  const viewRef = useRef(view);

  // 启动时应用本地设置（主题/涨跌配色/字号；图表周期由各视图自行读取）
  useEffect(() => {
    applyToDom(loadSettings());
    // 2026-08-12：拉取后端资讯开关（NEWS_ENABLED），关闭时进度条隐藏「资讯」步骤；
    // 失败保守显示（离线 mock 等场景），不阻塞首屏
    fetch("/api/health")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => d && setNewsEnabled(d.news_enabled))
      .catch(() => {});
  }, []);

  useEffect(() => {
    viewRef.current = view;
  }, [view]);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const loadHistory = useCallback(async () => {
    setHistory((h) => ({ ...h, loading: true, error: null }));
    try {
      const res = await api.listReviews();
      setHistory({
        groups: normalizeReviewList(res.data),
        loading: false,
        error: null,
        offline: !!(res && res.offline),
        onRetry: loadHistory,
      });
    } catch (err) {
      setHistory({
        groups: [],
        loading: false,
        error: err.message || "历史记录加载失败",
        offline: false,
        onRetry: loadHistory,
      });
    }
  }, []);

  const loadChatSessions = useCallback(async () => {
    setChatSessionsLoading(true);
    try {
      const res = await api.listChatSessions();
      setChatSessions(normalizeChatSessionList(res.data));
      setChatSessionsError("");
    } catch (err) {
      setChatSessionsError((err && err.message) || "会话列表加载失败");
    } finally {
      setChatSessionsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadHistory();
    loadChatSessions();
  }, [loadHistory, loadChatSessions]);

  useEffect(() => () => stopPolling(), [stopPolling]);

  // 「首页」（侧栏主按钮）：只切换视图，进行中的任务继续轮询
  const handleNew = useCallback(() => {
    setSidebarOpen(false);
    setReport(null);
    setReportError(null);
    setChatIntent(null);
    setView("home");
  }, []);

  const beginPolling = useCallback(
    (jobId, metaLabel, offlineFlag) => {
      stopPolling();
      setJob((prev) => ({ ...(prev || {}), id: jobId, metaLabel, offline: offlineFlag }));
      pollRef.current = setInterval(async () => {
        try {
          const snap = await api.getJob(jobId);
          const norm = normalizeJob(snap);
          setJob((prev) => ({ ...(prev || {}), ...norm, metaLabel }));
          if (norm.isDone || norm.isError) {
            stopPolling();
            if (norm.isDone) {
              const fresh = normalizeJobResult(norm.result);
              setReport((prev) => {
                const sameRecord =
                  prev &&
                  prev.meta &&
                  fresh.meta &&
                  prev.meta.date === fresh.meta.date &&
                  prev.meta.time === fresh.meta.time;
                if (viewRef.current !== "progress" && !sameRecord) return prev;
                return { ...fresh, analyses: [], debate_history: [], offline: offlineFlag };
              });
              if (viewRef.current === "progress") setView("report");
              loadHistory();
              // 详情接口补全分析师点评与辩论记录（失败不阻塞报告展示）
              api
                .getReviewByDate(fresh.meta.date)
                .then((raw) => {
                  const detail = normalizeReviewDetail(raw);
                  setReport((prev) =>
                    prev && prev.record_id === fresh.record_id
                      ? { ...prev, analyses: detail.analyses, debate_history: detail.debate_history }
                      : prev
                  );
                })
                .catch(() => {});
            }
          }
        } catch (err) {
          stopPolling();
          setJob((prev) => ({
            ...(prev || {}),
            status: "error",
            error: err.message || "轮询任务状态失败",
          }));
        }
      }, 1000);
    },
    [stopPolling, loadHistory]
  );

  const handleStartReview = useCallback(
    async (payload) => {
      setReport(null);
      setReportError(null);
      setJob({
        id: null,
        metaLabel: modeLabel(payload.mode),
        status: "queued",
        stage: "queued",
        pct: 0,
        message: "正在提交复盘任务…",
        analysts_done: 0,
        analysts_total: 0,
        offline: false,
      });
      setView("progress");
      try {
        const res = await api.startReview(payload);
        beginPolling(res.job_id, modeLabel(payload.mode), !!res.offline);
      } catch (err) {
        setJob((prev) => ({ ...(prev || {}), status: "error", error: err.message || "提交失败" }));
      }
    },
    [beginPolling]
  );

  // 运行中点击历史记录：只切换展示内容，轮询继续
  const handleOpenReview = useCallback(
    async (date, time) => {
      setReportError(null);
      setReportLoading(true);
      setView("report");
      try {
        // R4 修复：每日一份存储，历史详情一律走单日接口（旧 /{date}/{time} 已移除 → 404）
        const raw = await api.getReviewByDate(date);
        const detail = normalizeReviewDetail(raw);
        const t = (detail.meta && detail.meta.time) || time || "";
        setReport({
          ...detail,
          record_id: `${date}_${t}`,
          offline: false,
        });
      } catch (err) {
        setReportError((err && err.message) || "复盘详情加载失败");
        setReport(null);
      } finally {
        setReportLoading(false);
      }
    },
    []
  );

  const handleDeleteReview = useCallback(
    async (date, time) => {
      await api.deleteReview(date, time);
      await loadHistory();
      if (
        report &&
        report.meta &&
        report.meta.date === date &&
        report.meta.time === time
      ) {
        setReport(null);
        setView("home");
      }
    },
    [loadHistory, report]
  );

  // 侧栏指示器：进行中 → 回到进度；已完成 → 查看结果；失败 → 查看原因
  const handleBackToJob = useCallback(() => {
    if (!job) return;
    if (job.status === "done" && report) {
      setView("report");
      return;
    }
    setView("progress");
  }, [job, report]);

  const toggleCollapsed = useCallback(() => {
    setCollapsed((c) => {
      const next = !c;
      try {
        localStorage.setItem("stock-review.sidebarCollapsed", next ? "1" : "0");
      } catch {
        /* 忽略存储异常 */
      }
      return next;
    });
  }, []);

  // 聊天：统一跳首页「分析师聊天」Tab（会话全程在首页 Tab 内呈现）
  const handleOpenChat = useCallback((payload) => {
    setChatIntent(payload);
    setHomeTab("chat");
    setView("home");
  }, []);

  const handleOpenStockDetail = useCallback((code, name) => {
    setStockQuery({ code: String(code || "").trim(), name: String(name || ""), from: viewRef.current || "home" });
    setView("stock");
  }, []);

  const handleCloseStockDetail = useCallback(() => {
    const from = stockQuery && stockQuery.from ? stockQuery.from : "home";
    setStockQuery(null);
    setView(from);
  }, [stockQuery]);

  const handleOpenSettings = useCallback(() => {
    setSidebarOpen(false);
    setView("settings");
  }, []);

  // R12-A3：侧栏「模拟账户」→ 独立视图（AccountsPanel 自带返回）
  const handleOpenAccounts = useCallback(() => {
    setSidebarOpen(false);
    setView("accounts");
  }, []);

  const handleSelectChatSession = useCallback((id) => {
    setChatIntent(null);
    setActiveChatSessionId(id);
    setHomeTab("chat");
    setView("home");
  }, []);

  const handleDeleteChatSession = useCallback(
    async (id) => {
      await api.deleteChatSession(id);
      if (activeChatSessionId === id) setActiveChatSessionId(null);
      await loadChatSessions();
    },
    [activeChatSessionId, loadChatSessions]
  );

  const handleSessionCreated = useCallback(
    (id) => {
      setActiveChatSessionId(id);
      loadChatSessions();
    },
    [loadChatSessions]
  );

  const activeReviewKey =
    report && report.meta && report.meta.date
      ? `${report.meta.date}/${report.meta.time}`
      : null;

  const title =
    view === "progress" && job
      ? "复盘进行中"
      : view === "report"
        ? report
          ? `复盘报告 · ${report.meta.mode_label || ""}`
          : "复盘报告"
          : view === "accounts"
            ? "模拟账户"
          : view === "settings"
            ? "设置"
          : "首页";

  return (
    <ErrorBoundary onReset={handleNew}>
      <div className="app">
        {sidebarOpen ? (
          <div className="sidebar-backdrop" onClick={() => setSidebarOpen(false)} />
        ) : null}
        <Sidebar
          collapsed={collapsed}
          open={sidebarOpen}
          onCloseMobile={() => setSidebarOpen(false)}
          onToggle={toggleCollapsed}
          history={history}
          activeReviewKey={activeReviewKey}
          // 2026-08-06 修复：Sidebar 回调签名是 (date, time) 双参；此前只取首个参数
          // 导致 getReviewDetail(date, undefined) → /api/reviews/{date}/undefined → 404
          // 「复盘记录不存在」（点击任意历史记录必现）。
          onSelectReview={(date, time) => { setSidebarOpen(false); handleOpenReview(date, time); }}
          onNew={() => { setSidebarOpen(false); handleNew(); }}
          job={job}
          onBackToJob={() => { setSidebarOpen(false); handleBackToJob(); }}
          chatSessions={chatSessions}
          activeChatId={activeChatSessionId}
          onOpenChatSession={(id) => { setSidebarOpen(false); handleSelectChatSession(id); }}
          // R11-R1：手动补做复盘入口（侧栏），复用全局 job 轮询
          onStartReview={handleStartReview}
          onDeleteReview={handleDeleteReview}
          onDeleteChatSession={handleDeleteChatSession}
          onOpenSettings={handleOpenSettings}
          onOpenAccounts={handleOpenAccounts}
        />
        <main className="main">
          <header className="topbar">
            <div className="topbar-title">
              <button
                className="menu-btn"
                aria-label="打开菜单"
                onClick={() => setSidebarOpen((o) => !o)}
              >
                ☰
              </button>
              {title}
              {history.offline ? <span className="pill offline topbar-badge">离线 mock</span> : null}
            </div>
            {job && (job.isRunning || job.isDone || job.isError) ? (
              <button className="btn btn-outline btn-sm" onClick={handleBackToJob}>
                {job.isRunning ? `任务进行中 ${Math.round(job.pct || 0)}%` : job.isDone ? "查看结果" : "查看失败原因"}
              </button>
            ) : null}
          </header>
          <div className="content">
            {reportLoading ? (
              <div className="card loading-card">
                <div className="spinner" />
                <div>正在加载复盘报告…</div>
              </div>
            ) : reportError ? (
              <div className="card error-panel">
                <div className="error-icon">⚠️</div>
                <div className="error-title">加载失败</div>
                <div className="error-detail">{reportError}</div>
                <div className="error-actions">
                  <button className="btn btn-outline" onClick={loadHistory}>刷新历史记录</button>
                  <button className="btn btn-accent" onClick={handleNew}>返回首页</button>
                </div>
              </div>
            ) : view === "progress" && job ? (
              <ProgressView job={job} onNew={handleNew} />
            ) : view === "report" && report ? (
              <ReportView
                report={report}
                offline={!!report.offline}
                history={history}
                onSwitchReview={handleOpenReview}
              />
            ) : view === "stock" && stockQuery ? (
              <StockDetail query={stockQuery} onBack={handleCloseStockDetail} />
            ) : view === "settings" ? (
              <SettingsView />
            ) : view === "accounts" ? (
              <AccountsPanel onBack={handleNew} />
            ) : (
              <HomeView
                homeTab={homeTab}
                onHomeTabChange={setHomeTab}
                history={history}
                onRetryHistory={loadHistory}
                onDeleteReview={handleDeleteReview}
                onOpenChat={handleOpenChat}
                onOpenStockDetail={handleOpenStockDetail}
                chatSessions={chatSessions}
                chatSessionsLoading={chatSessionsLoading}
                chatSessionsError={chatSessionsError}
                activeChatSessionId={activeChatSessionId}
                chatIntent={chatIntent}
                onChatIntentConsumed={() => setChatIntent(null)}
                onSelectChatSession={handleSelectChatSession}
                onRefreshChatSessions={loadChatSessions}
                onChatSessionCreated={handleSessionCreated}
              />
            )}
          </div>
        </main>
      </div>
    </ErrorBoundary>
  );
}
