"""I2 提示词测试：6 模式模板、昨日/更早复盘对照注入、主持人/复盘助手角色。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from stock_review_crew import prompts


def _state(**overrides):
    base = {
        "date": "2026-08-03",
        "mode": "close",
        "market_data": "指数快照内容",
        "yesterday_report": "",
        "earlier_today": "",
        "analyses": [{"skill_name": "阿狼", "analysis": "看多"}],
        "debate_history": [],
        "degraded": False,
    }
    base.update(overrides)
    return base


def test_six_modes_defined():
    assert set(prompts.MODES) == {
        "pre_market", "auction", "intraday_am", "noon", "intraday_pm", "close",
    }
    assert prompts.DEFAULT_MODE == "close"
    for mode in prompts.MODES:
        info = prompts.MODE_INFO[mode]
        assert info["label"]
        assert info["desc"]
        assert len(info["sections"]) >= 6
        assert len(info["analyst_titles"]) == 3
        assert any("核心观点" in s for s in info["sections"])
        # R16：报告章节「分歧与讨论」改「分歧点」（无辩论语义）
        assert any("分歧点" in s for s in info["sections"])
        assert any("风险" in s for s in info["sections"])


def test_analyst_prompt_injection():
    p = prompts.build_analyst_prompt(_state(
        yesterday_report="昨日：预判看多",
        earlier_today="午间：维持看多",
        market_weather="晴（79 分/偏暖）",
    ))
    assert "2026-08-03" in p
    assert "收盘复盘" in p
    assert "指数快照内容" in p
    assert "昨日：预判看多" in p
    assert "午间：维持看多" in p
    # R11-4：固定三段格式 + 昨日验证内嵌「大盘判断」首行
    assert "## 1、大盘判断" in p
    assert "## 2、明日策略" in p
    assert "## 3、明日个股推荐" in p
    # P0-1：本模式分析要点注入（focus/judgment/plan 不再是死代码）
    assert "## 本模式分析要点" in p
    assert "本模式聚焦：今日大盘判断" in p
    assert "明日计划要点" in p
    assert "昨日验证：" in p
    # R12：市场天气/情绪温度纳入分析师 prompt
    assert "晴（79 分/偏暖）" in p
    assert "市场天气/情绪温度" in p


def test_analyst_prompt_weather_missing():
    """市场天气缺失时 prompt 标注「无数据」而非留空。"""
    p = prompts.build_analyst_prompt(_state())
    assert "市场天气/情绪温度（复盘指标，R12）：无数据" in p


def test_yesterday_missing_skip_rule():
    p = prompts.build_analyst_prompt(_state(mode="auction"))
    assert "（无昨日报告）" in p
    assert "无昨日报告，跳过验证" in p
    assert "（无当日更早复盘）" in p


def test_all_modes_analyst_prompt():
    for mode in prompts.MODES:
        p = prompts.build_analyst_prompt(_state(mode=mode))
        assert prompts.MODE_INFO[mode]["label"] in p
        assert "无昨日报告，跳过验证" in p
        # P0-1：本模式分析要点注入 + 三段章节标题按模式生成
        assert "## 本模式分析要点" in p
        assert prompts.MODE_INFO[mode]["focus_zh"] in p
        assert prompts.MODE_INFO[mode]["judgment"] in p
        assert prompts.MODE_INFO[mode]["plan_zh"] in p
        t1, t2, t3 = prompts.MODE_INFO[mode]["analyst_titles"]
        assert f"## 1、{t1}" in p
        assert f"## 2、{t2}" in p
        assert f"## 3、{t3}" in p


def test_pre_market_no_tomorrow_stock_rec():
    """P0-1 验收：pre_market 不再写「明日个股推荐」，标题随模式变化。"""
    p = prompts.build_analyst_prompt(_state(mode="pre_market"))
    assert "明日个股推荐" not in p
    assert "## 3、今日关注标的" in p
    assert "## 1、今日大盘预判" in p


def test_sentiment_gets_trend_context():
    p = prompts.build_analyst_prompt(_state(), group="sentiment", trend_context="阿狼：趋势偏多")
    assert "阿狼：趋势偏多" in p
    assert "趋势分析师的大盘判断" in p
    p2 = prompts.build_analyst_prompt(_state(), group="trend", trend_context="不应出现")
    assert "趋势分析师的大盘判断" not in p2


def test_unknown_mode_falls_back_to_close():
    p = prompts.build_analyst_prompt(_state(mode="bad_mode"))
    assert "收盘复盘" in p
    assert prompts.mode_info(None)["label"] == "收盘复盘"


def test_news_prompt():
    p = prompts.build_news_prompt("财新：降息预期升温")
    assert "资讯内容" in p
    assert "财新：降息预期升温" in p
    assert "利多板块及个股" in p


def test_host_role_and_prompt():
    assert "分歧判断" in prompts.HOST_SYSTEM_PROMPT
    assert "讨论议题" in prompts.HOST_SYSTEM_PROMPT
    assert "实质性分歧" in prompts.HOST_SYSTEM_PROMPT
    p = prompts.build_host_prompt(_state(), "首轮")
    assert "首轮" in p
    assert "阿狼" in p


def test_debate_prompt_cross_reference_and_anti_fabrication():
    skill = {"name": "阿狼", "prompt": "趋势派"}
    p = prompts.build_debate_prompt(skill, "多空方向分歧", "养家：看空")
    assert "多空方向分歧" in p
    assert "养家：看空" in p
    assert "不要编造" in p
    assert "不要做模拟交易" in p


def test_report_prompt_close_keeps_legacy_sections():
    p = prompts.build_report_prompt(_state())
    assert "## 二、各分析师核心观点" in p
    assert "## 四、明日操作计划" in p
    assert "唯一一份" in p
    assert "禁止编造" in p


def test_report_prompt_all_modes_sections():
    for mode in prompts.MODES:
        p = prompts.build_report_prompt(_state(mode=mode))
        for s in prompts.MODE_INFO[mode]["sections"]:
            assert f"## {s}" in p


def test_report_prompt_yesterday_context_injected():
    """P1-G：昨日/更早复盘注入报告 prompt（各截断 3000 字，缺失写「（无）」）。"""
    p = prompts.build_report_prompt(_state())
    assert "## 昨日/更早复盘（对照验证，P1-G）" in p
    assert "昨日收盘复盘：\n（无）" in p
    assert "当日更早复盘：\n（无）" in p
    assert "无昨日复盘，跳过验证" in p

    long_yesterday = "昨日" * 4000  # 8000 字，应截断到 3000
    p2 = prompts.build_report_prompt(_state(yesterday_report=long_yesterday))
    assert "昨日" * 1500 in p2  # 3000 字
    assert ("昨日" * 1500 + "昨日") not in p2


def test_report_prompt_length_and_dedup_constraints():
    """P1-B：总字数/章节长度上限、禁复制原文、禁套话。"""
    p = prompts.build_report_prompt(_state())
    assert "总字数 ≤1500 字" in p
    assert "每个章节 ≤300 字" in p
    assert "禁止整段复制分析师原文" in p
    assert "免责声明由系统在报告末尾统一追加" in p


def test_report_prompt_unit_layers_no_conflict():
    """P1-A + R16：单位指令分层（成交额列转亿元 / 成交量原样 / 正文亿元）。"""
    p = prompts.build_report_prompt(_state())
    assert "表格列（成交量/成交额）" in p
    assert "成交额" in p and "一律换算为" in p and "亿元" in p
    assert "1164203130880 元 → 11642.03 亿元" in p
    assert "禁止把成交量当成交额" in p
    assert "一律用「亿元」展示" in p
    assert "547852512 元 → 5.48 亿元" in p
    assert "5479 万元 → 0.55 亿元" not in p


def test_report_prompt_r6_expression_constraints():
    """R6（2026-08-08）：表述铁律注入——禁止比喻/夸张/口语化修辞，用数据+百分点对照。"""
    p = prompts.build_report_prompt(_state())
    assert "## 表述铁律（R6" in p
    assert "近乎腰斩" in p
    assert "断崖" in p
    assert "崩了" in p
    assert "禁止比喻、夸张与口语化修辞" in p
    assert "23.35 个百分点" in p  # 数据 + 百分点对照示例
    assert "章节结构与 Markdown 格式与本 prompt 约定完全一致" in p  # R7 格式不变


def test_quality_detect_sensitive_phrases():
    """R6 P2（2026-08-08）：quality 敏感表述检测——比喻/夸张/口语化修辞命中，正常措辞不误报。"""
    from stock_review_crew import quality

    r = quality.detect_sensitive_phrases("股价近乎腰斩，随后断崖式下跌，崩了")
    assert r["total"] == 5  # 近乎腰斩/腰斩/断崖/断崖式/崩了（词表允许重叠，检出即命中）
    phrases = {h["phrase"] for h in r["hits"]}
    assert {"近乎腰斩", "腰斩", "断崖", "断崖式", "崩了"} <= phrases

    # 正常数据 + 百分点对照表达不误报
    ok = quality.detect_sensitive_phrases(
        "昨日涨停红盘率 53.57%，较前日 76.92% 下降 23.35 个百分点"
    )
    assert ok["total"] == 0
    assert quality.detect_sensitive_phrases(None) == {"hits": [], "total": 0}


def test_report_prompt_host_analysis_and_emotion_cycle_injected():
    """R16：报告 prompt 注入主持人分歧判断（host_analysis 原文）与情绪周期定位；
    无辩论——不组装「讨论记录」，且约束「分歧点」章节只写判断不写逐轮转写。"""
    p = prompts.build_report_prompt(_state(
        host_analysis="分歧判断：有\n讨论议题：多空方向分歧",
        emotion_cycle="上升期",
    ))
    assert "主持人分歧判断" in p
    assert "分歧判断：有" in p
    assert "多空方向分歧" in p
    assert "情绪周期定位：上升期" in p
    assert "禁止出现辩论逐条转写" in p
    assert "无讨论记录" not in p  # 旧「讨论记录」占位已移除
    p2 = prompts.build_report_prompt(_state())
    assert "无分歧判断" in p2
    assert "情绪周期定位：数据不足" in p2


def test_report_title():
    assert prompts.mode_report_title("2026-08-03", "close") == "2026-08-03 A股收盘复盘报告"
    assert prompts.mode_report_title("2026-08-03", "auction") == "2026-08-03 A股竞价复盘报告"


def test_assistant_role_and_disclaimer():
    assert "复盘" in prompts.REVIEW_ASSISTANT_SYSTEM_PROMPT
    assert "Markdown" in prompts.REVIEW_ASSISTANT_SYSTEM_PROMPT
    assert prompts.DISCLAIMER == "仅供参考，不构成投资建议"


def test_role_json_backs_prompts():
    """主持人/复盘助手角色 JSON 与 prompts 生效文本一致（JSON 为真源）。"""
    from stock_review_crew.skills import load_skill
    host = load_skill("host")
    assert host and host["prompt"] == prompts.HOST_SYSTEM_PROMPT
    assistant = load_skill("review_assistant")
    assert assistant and assistant["prompt"] == prompts.REVIEW_ASSISTANT_SYSTEM_PROMPT
