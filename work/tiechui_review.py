# -*- coding: utf-8 -*-
"""铁锤狂砸盘 · 周五(2026-08-28)复盘生成器（规则引擎版）。

数据完全来自：①首页基本数据（市场天气/情绪 fetch_market_sentiment）
②涨停复盘 Tab（主线题材 board_stats、个股标签 labels、资讯影响 news、梯队、席位）。
输出与项目 close 复盘报告格式一致（一…七 章节 + 免责声明），写入
docs/复盘报告-铁锤狂砸盘-2026-08-28.md。
"""

import sys, io, json
sys.path.insert(0, "src")

from typing import Any

from stock_review_crew.tools import market_extra
from stock_review_crew import zt_labels
from stock_review_crew.tools import news_impact
from stock_review_crew.storage import zt_tracking as zt_store

DATE = "2026-08-28"


def pct(v: Any) -> str:
    return "—" if v is None else f"{v * 100:.1f}%"


def main() -> str:
    sent = market_extra.fetch_market_sentiment(DATE)
    lbl = zt_labels.label_mainline_stocks(DATE)
    news = news_impact.load_news(DATE) or news_impact.run_news_impact(DATE)
    snap = zt_store.load_daily(DATE) or {}
    stocks = snap.get("stocks", [])
    seats_counts = {"youzi": 0, "jigou": 0, "lianghua": 0, "waizi": 0}
    seat_names = []
    for s in stocks:
        se = s.get("seats") or {}
        for side in ("buy", "sell"):
            for it in se.get(side, []):
                if it.get("type") in seats_counts:
                    seats_counts[it["type"]] += 1
                if it.get("type") in ("youzi", "jigou", "lianghua", "waizi"):
                    seat_names.append((it.get("name"), it.get("type_label")))

    board_stats = lbl.get("board_stats", [])
    labels = lbl.get("labels", [])
    phase = lbl.get("phase", "数据不足")
    score = lbl.get("emotion_score")

    # ---------- 组装报告 ----------
    L: list[str] = []
    L.append(f"# {DATE} A股收盘复盘报告（铁锤狂砸盘 · 涨停复盘工作台数据版本）")
    L.append("")
    L.append("> 本报告由铁锤狂砸盘（超短实战游资人格）基于涨停复盘工作台数据 + 首页情绪数据规则生成；缺失项标注「无数据」。")
    L.append("")
    L.append("## 一、市场概况")
    L.append("")
    L.append(f"- 复盘日期：{DATE}；模式：收盘复盘（15:00 后（默认））；情绪阶段：{phase}（温度 {score if score is not None else '—'} 分）")
    L.append(f"- 市场天气：{sent.get('weather') or '—'}；上涨占比 {pct(sent.get('up_down_ratio'))}；涨停 {sent.get('limit_up_count') or '—'} 家；跌停 {sent.get('limit_down_count') or '—'} 家")
    L.append(f"- 炸板率 {pct(sent.get('break_rate'))}；连板高度 {sent.get('lianban_height') or '—'} 板；昨日涨停今日红盘率 {pct(sent.get('red_rate'))}；平均收益 {pct(sent.get('avg_return'))}")
    L.append(f"- 情绪评分依据：{'；'.join(sent.get('reasons') or []) or '—'}")
    L.append("")
    L.append("## 二、各分析师核心观点")
    L.append("")
    L.append("### 铁锤狂砸盘")
    L.append("")
    L.append("## 1、大盘判断")
    L.append("")
    if score is not None and score >= 80:
        L.append(f"情绪温度 {int(score)} 分，已是高潮段。我的铁律排第一：敬畏市场。涨了就是涨了——但这个位置我不会去追加速，高潮次日不做加速（语料4）。")
    elif score is not None and score >= 60:
        L.append(f"情绪温度 {int(score)} 分，发酵升温。盘面有赚钱效应，但越是热闹越要盯紧封单和承接——一致是卖点，分歧才是买点。")
    elif score is not None and score >= 45:
        L.append(f"情绪温度 {int(score)} 分，分歧阶段。这种时候最值得看盘：看核心票承接，回踩不破关键位、有人接，就是我的进场窗口。")
    else:
        L.append(f"情绪温度 {score if score is not None else '—'} 分，偏冷。缩量/冰点行情我的铁律是空仓保命，不硬做（铁律11、12）。")
    L.append("")
    L.append("## 2、主线题材复盘")
    L.append("")
    if not board_stats:
        L.append("无满足准入的主线题材。")
    else:
        L.append("按赚钱效应（统计日起累计涨幅均值+中位数打分）排序：")
        L.append("")
        for b in board_stats:
            warn = "" if b.get("warn") else ""
            L.append(f"- **{b['board']}**：赚钱效应 {b['earn_score'] if b['earn_score'] is not None else '—'} 分（均值 {pct(b.get('earn_mean'))} / 中位 {pct(b.get('earn_median'))}）；当日涨停 {b['zt_count_today']} 家 / 断板跟踪 {b['trend_count']} 家；{b.get('warn_reason') or '梯队正常'}")
        L.append("")
    L.append("## 3、核心个股点评（只做有辨识度的票）")
    L.append("")
    cores = [l for l in labels if l["role"].startswith(("核心", "中军")) or l["role"].startswith("辨识度")]
    if not cores:
        L.append("主线内无明显核心（或数据不足）。")
    else:
        for c in cores[:6]:
            nd = (c.get("next_day") or {}).get("plan", "次日验证承接后再定")
            L.append(f"- **{c['name']}（{c['code']}）** {c['board']}·{c['lianban']}板 · {c['role']} · {c['action']}（热度{c['heat_level']}）")
            L.append(f"  - 依据：{'；'.join(c.get('role_reason', [])[:2])}")
            L.append(f"  - 次日：{nd}")
    L.append("")
    L.append("## 4、杂毛警示")
    L.append("")
    zahmao = [l for l in labels if l["role"].startswith("杂毛")]
    L.append(f"杂毛/弱跟风 {len(zahmao)} 只，全部回避（我的铁律6：没有辨识度的涨停就是主力给散户画的饼）：" if zahmao else "主线内无杂毛跟风。")
    if zahmao:
        L.append(" - " + "；".join(z["name"] for z in zahmao[:8]))
    L.append("")
    L.append("## 5、资讯影响")
    L.append("")
    items = (news or {}).get("items", [])
    if not items:
        L.append("暂无 A 股相关快讯。")
    else:
        for it in items[:5]:
            L.append(f"- [{it.get('direction')}] {it.get('text', '')[:80]}")
            if it.get("boards"):
                L.append(f"  - 影响板块：{'、'.join(it['boards'])}")
            if it.get("stocks"):
                L.append(f"  - 影响个股：{'、'.join(s['name'] for s in it['stocks'])}（主线★）")
    L.append("")
    L.append("## 6、龙虎榜席位")
    L.append("")
    total = sum(seats_counts.values())
    L.append(f"- 上榜席位合计 {total} 例：游资×{seats_counts['youzi']}、机构×{seats_counts['jigou']}、量化×{seats_counts['lianghua']}、外资×{seats_counts['waizi']}；其余为未知（可人工标注）")
    L.append("")
    L.append("## 7、明日策略")
    L.append("")
    L.append("铁律再念一遍：不预判只跟随，分歧买、一致卖，竞价验消息、午拉是实，缩量空仓。")
    L.append(f"- 情绪阶段 {phase}：开盘先看竞价与板块联动，核心票竞价不弱、回踩有承接才动手；竞价转弱直接放弃。")
    L.append("- 主线内只做（次日操作指引标注的）分歧低吸/弱转强确认后的核心；杂毛一律不碰；末期警告板块（如有）坚决回避高位接力。")
    L.append("- 仓位：确认信号出现再上（非退潮期单票3成、总仓≤7成、最多2只）；缩量/无信号就空仓等主线明确（铁律11、12）。")
    L.append("")
    L.append("## 三、分歧点")
    L.append("")
    L.append("本轮铁锤单一视角未发现实质性分歧（如需多分析师交叉，可切换其他分析师）。")
    L.append("")
    L.append("## 四、明日操作计划")
    L.append("")
    L.append("- 收盘后先看竞价：验证今日主线是否延续（跟风股活跃度、龙头竞价态度）；")
    L.append("- 10:00 前不动手，看承接（回踩不破关键位/主动买盘/封板坚决）；午盘共识确认再出手（铁律9）；")
    L.append("- 持有纪律：放量不板离场一部分，破位止损，加速缩量冲高不板分批兑现（铁律5/14）。")
    L.append("")
    L.append("## 五、总结")
    L.append("")
    L.append(f"- 市场情绪 {phase}，主线赚钱效应排序见上文；低吸核心、回避杂毛、盯竞价承接；无确认信号宁可空仓。")
    L.append("")
    L.append("## 六、风险提示")
    L.append("")
    L.append("- 本报告由规则引擎基于涨停复盘工作台数据与首页情绪数据生成，不构成任何预测；市场有风险，请独立判断、控制仓位。")
    L.append("- 历史数据不代表未来表现；涨停复盘数据有降级/缺失可能，投资需谨慎，仅供参考，不构成投资建议。")
    L.append("")
    L.append("## 七、综合标签")
    L.append("")
    tag_parts = [f"#{phase}"]
    if score is not None and score >= 80:
        tag_parts.append("#高潮谨慎")
    if any(b.get("warn") for b in board_stats):
        tag_parts.append("#炒作业末期")
    tag_parts.append("#低吸核心")
    L.append(" ".join(tag_parts))
    L.append("")
    L.append("---")
    L.append("")
    L.append("> 仅供参考，不构成投资建议")
    report = "\n".join(L)

    # 落盘
    import pathlib
    out = pathlib.Path("docs") / "复盘报告-铁锤狂砸盘-2026-08-28.md"
    out.write_text(report, encoding="utf-8")
    (pathlib.Path(".tmp") / "tiechui_review.json").write_text(
        json.dumps({"date": DATE, "length": len(report)}, ensure_ascii=False), encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()

