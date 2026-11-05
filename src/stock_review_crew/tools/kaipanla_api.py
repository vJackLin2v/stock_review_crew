# -*- coding: utf-8 -*-
"""开盘啦 App API 数据层（免登录；2026-08-29 子代理实测通过）。

关键点：
- 域名：apphis.longhuvip.com/w1/api/index.php（历史/复盘）；apphwshhq（个股/板块）
- UA 必须用 Android Dalvik（Chrome UA 返回空列表）；Token/UserID 可不传
- 主接口：
  * GetZhangTingTianTi(FuPanLa)：当日全部涨停股 + 题材板块(涨停原因) + 连板数 + 封板时间
  * GetPlateInfo_w38(HisLimitResumption)：按题材分组 + 概念标签 + 涨停原因 + 详细文本
- 不做登录接口（pchq 概念成分需登录，不用）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any, Optional

import requests

_APPHIS = "https://apphis.longhuvip.com/w1/api/index.php"
_UA = ("Dalvik/2.1.0 (Linux; U; Android 9; SHARK PRS-A0 Build/PQ3A.190605.01141736)")


class KaipanlaError(RuntimeError):
    """开盘啦接口错误（中文）。"""


def _common() -> dict:
    return {"DeviceID": str(uuid.uuid4()), "PhoneOSNew": "2",
            "VerSion": "5.23.0.1", "apiv": "w44"}


def _session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False
    s.headers.update({
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "User-Agent": _UA,
        "Accept-Encoding": "gzip",
    })
    return s


def _call(params: dict, method: str = "POST", timeout: float = 20) -> dict:
    s = _session()
    if method == "POST":
        r = s.post(_APPHIS, data=params, timeout=timeout)
    else:
        r = s.get(_APPHIS, params=params, timeout=timeout)
    try:
        j = r.json()
    except Exception as exc:
        raise KaipanlaError(f"开盘啦响应非 JSON：{str(exc)[:80]}") from exc
    err = j.get("errcode") if isinstance(j, dict) else None
    if err not in (None, 0) and str(err) != "0":
        raise KaipanlaError(f"开盘啦接口错误 errcode={err} msg={j.get('errmsg') if isinstance(j, dict) else ''}")
    return j if isinstance(j, dict) else {}


def _seal_time(epoch: Any) -> Optional[str]:
    """epoch 秒 → 北京时间 HHMMSS。"""
    try:
        t = datetime.utcfromtimestamp(int(epoch)) + timedelta(hours=8)
        return t.strftime("%H%M%S")
    except Exception:
        return None


def fetch_zhangting_tianti(date: str) -> dict:
    """当日全部涨停股（代码/名称/连板数/封板时间/题材名）。"""
    j = _call({**_common(), "a": "GetZhangTingTianTi", "c": "FuPanLa", "Date": date})
    rows: list[dict] = []
    for it in j.get("StockList", []) or []:
        try:
            code = str(it[0]).zfill(6)
            rows.append({
                "code": code, "name": it[1], "lianban": it[2],
                "seal_epoch": it[3] if len(it) > 3 else None,
                "seal_time": _seal_time(it[3]) if len(it) > 3 else None,
                "topic_code": it[4] if len(it) > 4 else None,
                "topic": it[5] if len(it) > 5 else None,
            })
        except Exception:
            continue
    return {"date": date, "stocks": rows, "zhushu": j.get("ZhuShuList") or [],
            "source": "开盘啦AppAPI"}


def fetch_plate_info(date: str, max_pages: int = 10) -> dict:
    """按题材分组：概念标签 + 涨停原因 + 详细文本（分页拉全）。"""
    groups: list[dict] = []
    for index in range(max_pages):
        j = _call({**_common(), "a": "GetPlateInfo_w38", "c": "HisLimitResumption",
                   "Date": date, "Index": str(index), "st": "20"})
        lst = j.get("list") or []
        if not lst:
            break
        for g in lst:
            item: dict[str, Any] = {"topic": g.get("ZSName"), "topic_code": g.get("ZSCode"),
                                    "explain": g.get("TCExplain"), "stocks": []}
            for it in g.get("StockList", []) or []:
                try:
                    tags = []
                    if len(it) > 11 and it[11]:
                        tags = [t.strip() for t in str(it[11]).replace("、", ",").split(",") if t.strip()]
                    item["stocks"].append({
                        "code": str(it[0]).zfill(6), "name": it[1],
                        "tags": tags,
                        "reason": it[16] if len(it) > 16 else "",
                        "reason_detail": it[17] if len(it) > 17 else "",
                    })
                except Exception:
                    continue
            groups.append(item)
        if len(lst) < 20:
            break
    return {"date": date, "groups": groups, "source": "开盘啦AppAPI"}
