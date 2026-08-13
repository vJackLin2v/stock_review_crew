# -*- coding: utf-8 -*-
"""LLM 输入脱敏（2026-08-12）：发给 LLM 的文本不出现股票名称，只留 6 位代码。

背景：股票名称会触发 LLM 内容审核敏感词（实测 400 Content Exists Risk）；
落盘报告/前端展示/数据层存储不受影响——本模块只在「组装发给 LLM 的文本」前调用。

规则（容器感知，不做全文正则猜名称）：
- dict 含股票代码类键（代码/code/ts_code/symbol/stock_code）→ 删除 名称/name/股票名称 键；
- 任何 dict 删除 leading_stock/领涨股/领涨股票 键（板块行里的领涨股名）；
- 板块行 name（板块名）、指数名、分析师名等无代码键容器内的 name 保留；
- 幂等、失败返回原对象（绝不抛异常、绝不阻塞主流程）。
"""

import json

_NAME_KEYS = ("名称", "name", "股票名称", "股票简称")
_CODE_KEYS = ("代码", "code", "ts_code", "symbol", "stock_code")
_LEADING_KEYS = ("leading_stock", "领涨股", "领涨股票")


def sanitize_for_llm(obj):
    """递归脱敏 dict/list；str 原样返回；任何异常返回原对象。"""
    try:
        if isinstance(obj, dict):
            has_code = any(k in obj for k in _CODE_KEYS)
            out = {}
            for k, v in obj.items():
                if (has_code and k in _NAME_KEYS) or k in _LEADING_KEYS:
                    continue
                out[k] = sanitize_for_llm(v)
            return out
        if isinstance(obj, list):
            return [sanitize_for_llm(x) for x in obj]
        return obj
    except Exception:  # noqa: BLE001 - 脱敏失败绝不阻塞
        return obj


def sanitize_json_text(text):
    """对 JSON 字符串文本脱敏（stock_info 等 _dumps 输出）：parse → sanitize → dumps。
    非 JSON/解析失败原样返回。"""
    if not isinstance(text, str) or not text.strip():
        return text
    try:
        obj = json.loads(text)
        return json.dumps(sanitize_for_llm(obj), ensure_ascii=False)
    except (TypeError, ValueError):
        return text
