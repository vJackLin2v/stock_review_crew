// 复盘任务阶段（R15/R2：趋势派+情绪派合并为单一「分析师」阶段，2026-08-12 用户确认）
export const STAGES = [
  { key: "fetch", label: "取数" },
  { key: "news", label: "资讯" },
  { key: "analysts", label: "分析师" },
  { key: "host", label: "主持人" },
  { key: "debate", label: "辩论" },
  { key: "report", label: "报告" },
  { key: "done", label: "完成" },
];

// 2026-08-12：资讯开关——后端 /api/health 返回 news_enabled（默认关闭=屏蔽资讯）。
// 关闭时进度条隐藏「资讯」步骤；恢复资讯后重新显示。
let newsEnabled = true; // 未拿到后端配置前保守显示（离线 mock 等场景）

export function setNewsEnabled(v) {
  newsEnabled = v !== false;
}

export function getNewsEnabled() {
  return newsEnabled;
}

export function visibleStages() {
  return newsEnabled ? STAGES : STAGES.filter((s) => s.key !== "news");
}

export function stageIndex(stage) {
  const s = String(stage || "").toLowerCase();
  if (s.includes("fetch") || s.includes("data") || s.includes("取数")) return 0;
  if (s.includes("news") || s.includes("info") || s.includes("资讯")) return 1;
  // R15/R2：趋势派/情绪派旧阶段事件统一映射到「分析师」（兼容旧 job 数据）
  if (s.includes("analyst") || s.includes("trend") || s.includes("sentiment")
      || s.includes("趋势") || s.includes("情绪")) return 2;
  if (s.includes("host") || s.includes("moderator") || s.includes("主持")) return 4;
  if (s.includes("debat") || s.includes("辩论")) return 5;
  if (s.includes("report") || s.includes("报告")) return 6;
  if (s.includes("done") || s.includes("finish") || s.includes("完成")) return 7;
  return 0;
}

export function stageLabel(stage) {
  const s = String(stage || "").toLowerCase();
  if (s === "queued" || s.includes("queue") || s.includes("提交") || s.includes("排队")) {
    return "排队等待";
  }
  return STAGES[Math.min(stageIndex(stage), STAGES.length - 1)].label;
}

// 分析师阶段显示「分析师 n/总数」（兼容旧 trend/sentiment 事件）
export function stageStepLabel(stage, done, total) {
  const s = String(stage || "").toLowerCase();
  if ((s.includes("analyst") || s.includes("trend") || s.includes("sentiment")
       || s.includes("趋势") || s.includes("情绪")) && total) return `分析师 ${done}/${total}`;
  return stageLabel(stage);
}
