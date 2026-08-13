# -*- coding: utf-8 -*-
"""浏览器解析 HTML 数据获取（Playwright + 系统 Edge 无头）。

适用场景：接口/页面需要 JS 签名或反爬（如同花顺 q.10jqka.com.cn 的 v Cookie），
普通 requests 拿不到；真实浏览器内 JS 自动执行生成会话态，再用**页面内 fetch**
（同源 + X-Requested-With 头）取数据，绕开独立请求 403 的问题。

实测（2026-08-04）：无头 Edge 打开同花顺行业页 → v Cookie 自动生成 →
页面内 fetch 返回 200/35KB HTML 表格（50 行板块数据，0.1s 内完成）。

约定（契约 §六）：比率小数、金额元（万元×10000）、None=无数据、
超时/失败抛 ``BrowserCrawlError``（中文）由数据层降级链处理。
"""

from __future__ import annotations

import time
from typing import Any, Optional

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_PROXY = "http://127.0.0.1:7890"
_BASE = "http://q.10jqka.com.cn"
_PAGE_SIZE = 50


class BrowserCrawlError(RuntimeError):
    """浏览器爬虫错误（中文消息，供降级链标注）。"""


def available() -> bool:
    """playwright 是否可用（.venv 已装；系统 Python 未装时降级）。"""
    try:
        import playwright  # noqa: F401

        return True
    except Exception:
        return False


def _proxy_available() -> bool:
    """代理客户端是否在运行（127.0.0.1:7890）；未运行时不传代理直连。"""
    import socket

    s = socket.socket()
    s.settimeout(1)
    try:
        s.connect(("127.0.0.1", 7890))
        return True
    except Exception:
        return False
    finally:
        s.close()


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _normalize_rows(df: Any) -> list[dict]:
    """按列位置映射（同花顺行业页固定 12 列）：
    序号/板块/涨跌幅(%)/总成交量(手)/总成交额(万元)/净流入(万元)/上涨家数/下跌家数/
    均价/领涨股/领涨股-最新价/领涨股-涨跌幅(%)。
    金额万元→元；涨跌幅保持百分数值（_sector_row 统一 ÷100 为小数）。
    """
    rows: list[dict] = []
    for _, r in df.iterrows():
        vals = [r.iloc[i] for i in range(min(12, len(r)))]
        rows.append(
            {
                "板块": vals[1],
                "涨跌幅": _to_float(vals[2]),
                "总成交额": _to_float(vals[4]) * 10000 if _to_float(vals[4]) is not None else None,
                "净流入": _to_float(vals[5]) * 10000 if _to_float(vals[5]) is not None else None,
                "上涨家数": _to_float(vals[6]),
                "下跌家数": _to_float(vals[7]),
                "均价": _to_float(vals[8]),
                "领涨股": vals[9],
                "领涨股-最新价": _to_float(vals[10]),
                "领涨股-涨跌幅": _to_float(vals[11]),
            }
        )
    return rows


def fetch_ths_sector_rows(max_pages: int = 2, budget: float = 40.0) -> list[dict]:
    """无头浏览器抓取同花顺行业板块列表（分页）。返回归一化行列表。"""
    if not available():
        raise BrowserCrawlError("playwright 未安装，浏览器数据源不可用")
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover
        raise BrowserCrawlError(f"playwright 导入失败：{exc}") from exc

    rows: list[dict] = []
    t0 = time.time()
    try:
        with sync_playwright() as p:
            proxy_args = [f"--proxy-server={_PROXY}"] if _proxy_available() else []
            browser = p.chromium.launch(
                channel="msedge",
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-gpu",
                    "--disable-dev-shm-usage",
                ] + proxy_args,
            )
            try:
                page = browser.new_page(user_agent=_UA)
                page.goto(_BASE + "/thshy/", timeout=20000, wait_until="domcontentloaded")
                time.sleep(1.5)  # 等待页面 JS 生成 v Cookie
                for page_no in range(1, max_pages + 1):
                    if time.time() - t0 > budget:
                        break
                    url = f"{_BASE}/thshy/index/field/199112/order/desc/page/{page_no}/ajax/1/"
                    result = page.evaluate(
                        """async (url) => {
                            const r = await fetch(url, {
                                headers: {
                                    'X-Requested-With': 'XMLHttpRequest',
                                    'Referer': 'http://q.10jqka.com.cn/thshy/'
                                }
                            });
                            return {status: r.status, text: await r.text()};
                        }""",
                        url,
                    )
                    if result["status"] != 200:
                        raise BrowserCrawlError(f"同花顺板块第 {page_no} 页返回 {result['status']}")
                    import pandas as pd
                    from io import StringIO

                    tables = pd.read_html(StringIO(result["text"]))
                    if not tables or tables[0].empty:
                        break
                    rows.extend(_normalize_rows(tables[0]))
                    if len(tables[0]) < _PAGE_SIZE:
                        break
            finally:
                try:
                    browser.close()
                except Exception:
                    pass
    except BrowserCrawlError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise BrowserCrawlError(f"浏览器抓取失败：{exc}") from exc
    if not rows:
        raise BrowserCrawlError("同花顺行业板块无数据（浏览器渲染为空）")
    return rows
