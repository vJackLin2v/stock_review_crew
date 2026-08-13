# -*- coding: utf-8 -*-
r"""一键回测（回归）脚本：每次改动后必须全绿方可交付。

用法（需 .venv Python，服务已在 8501 运行）：
    powershell -ExecutionPolicy Bypass -Command ".venv\Scripts\python.exe scripts\regression.py"

覆盖：
  1. 全量 pytest（系统 Python 3.13，离线确定性）
  2. 前端 npm run build
  3. API 契约抽查（health / 今日数据 / 个股详情 / ETF 行情）
  4. 聊天链路（LLM 可用→正常回复；LLM 不可用→设计内中文终止说明）
  5. 无头浏览器点击冒烟（桌面 + 手机视口：详情/五档/成交额/会话列表隐藏/抽屉/无 JS 错误）

退出码：0=全绿；1=存在失败项（逐项打印 PASS/FAIL）。
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = r"H:\stock_review_crew"
BASE = "http://127.0.0.1:8501"
# pytest 用系统 Python 3.13（AGENTS 约定）；.venv 的 uv python 无 pytest
PYTEST_PY = r"C:\Python313\python.exe"
if not Path(PYTEST_PY).exists():
    PYTEST_PY = "python"

results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    results.append((name, bool(cond), extra))
    print(("PASS" if cond else "FAIL"), name, extra or "")


def run(cmd: list[str], cwd: str = ROOT, timeout: int = 600) -> tuple[int, str]:
    return run_env(cmd, cwd=cwd, timeout=timeout, env=None)


def run_env(cmd: list[str], cwd: str = ROOT, timeout: int = 600,
            env: dict | None = None) -> tuple[int, str]:
    try:
        import os

        proc_env = dict(os.environ)
        if env:
            proc_env.update(env)
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace", env=proc_env,
        )
        tail = (proc.stdout + proc.stderr).strip().splitlines()
        return proc.returncode, " | ".join(tail[-12:])
    except FileNotFoundError as exc:
        return 1, f"命令不存在：{cmd[0]}（{exc}）"


def http_get(path: str, timeout: int = 90):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 直连本机
    with opener.open(BASE + path, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def http_post(path: str, payload: dict, timeout: int = 600):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def main() -> int:
    # 0) 服务健康
    try:
        st, _ = http_get("/api/health", 8)
        check("后端健康（8501）", st == 200, str(st))
    except Exception as exc:  # noqa: BLE001
        check("后端健康（8501）", False, str(exc)[:100])
        print("请先启动后端（启动服务.bat 或 scripts/start_backend.ps1）再运行回测。")
        return 1

    # 1) 全量 pytest（注入假代理 127.0.0.1:9 保证「离线降级断言」确定性：
    # 提权/真实网络环境下数据源可用会让降级类用例失败，与沙箱行为保持一致）
    code, tail = run_env(
        [PYTEST_PY, "-m", "pytest", "-p", "no:cacheprovider", "-q"], timeout=900,
        env={"HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9"},
    )
    check("全量 pytest", code == 0, tail[-1] if tail else str(code))

    # 2) 前端构建
    code, tail = run(["npm.cmd", "run", "build"], cwd=ROOT + r"\frontend", timeout=240)
    check("前端 npm run build", code == 0, tail[-1] if tail else str(code))

    # 3) API 契约抽查
    try:
        st, rt = http_get("/api/data/realtime")
        rows = ((rt.get("indices") or {}).get("indices")) or []
        check("今日数据 indices 4 行", st == 200 and len(rows) == 4, f"rows={len(rows)} degraded={len(rt.get('degraded') or [])}")
    except Exception as exc:  # noqa: BLE001
        check("今日数据 indices 4 行", False, str(exc)[:100])

    try:
        st, d = http_get("/api/data/stock/600519")
        q = d.get("quote") or {}
        ok = st == 200 and q.get("price") is not None and q.get("amount") is not None and len(d.get("daily") or []) >= 5
        check("个股详情 600519（价/额/日K）", ok, f"price={q.get('price')} amount={q.get('amount')} daily={len(d.get('daily') or [])}")
    except Exception as exc:  # noqa: BLE001
        check("个股详情 600519（价/额/日K）", False, str(exc)[:100])

    try:
        st, qq = http_get("/api/data/quote?code=588170")
        ok = st == 200 and qq.get("price") is not None and qq.get("amount") is not None
        check("ETF 588170 行情（价/额）", ok, f"price={qq.get('price')} amount={qq.get('amount')}")
    except Exception as exc:  # noqa: BLE001
        check("ETF 588170 行情（价/额）", False, str(exc)[:100])

    # 2026-08-07 新增：额外指数降级原因必须全中文，禁止透传英文异常原文
    # （曾出现「通达信指数: time data 'i' does not match format '%Y-%m-%d'」「502 Bad Gateway for url:」直达前端横幅）。
    try:
        st, ex = http_get("/api/data/extra-indices")
        reasons = " ".join(ex.get("degraded_reason") or [])
        eng_bad = [k for k in ("time data", "does not match", "Traceback",
                               "Bad Gateway", "Server Error", "url:", "HTTPError")
                   if k in reasons]
        check("额外指数降级原因中文（无英文异常透传）", st == 200 and not eng_bad,
              f"reasons={reasons[:120]}" if eng_bad else f"reasons_len={len(reasons)}")
    except Exception as exc:  # noqa: BLE001
        check("额外指数降级原因中文（无英文异常透传）", False, str(exc)[:100])

    # R11-R2：自动复盘 status 契约（字段齐全、类型正确；can_auto 语义由后端测试覆盖）
    try:
        st, sts = http_get("/api/reviews/status?date=2026-08-07")
        keys = ("date", "is_trading_day", "latest_date", "latest_mode",
                "has_today_review", "can_auto", "pending_job_id")
        ok = st == 200 and all(k in sts for k in keys) and isinstance(sts["is_trading_day"], bool)
        check("自动复盘 status 契约", ok, f"date={sts.get('date')} latest={sts.get('latest_date')}")
    except Exception as exc:  # noqa: BLE001
        check("自动复盘 status 契约", False, str(exc)[:100])

    # 4) 聊天链路（LLM 可用→正常回复；不可用→设计内中文终止，禁止 500/空）
    try:
        st, sess = http_post("/api/chat/sessions", {
            "target_type": "stock", "target": "600519", "analysts": ["alang"], "title": "回归探针",
        }, timeout=30)
        sid = sess.get("session_id") if st == 200 else ""
        if not sid:
            check("聊天链路", False, "会话创建失败")
        else:
            req = urllib.request.Request(
                BASE + f"/api/chat/sessions/{sid}/messages/stream",
                data=json.dumps({"content": "近期走势怎么看？"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            evs = []
            with opener.open(req, timeout=420) as r:
                for raw in r:
                    line = raw.decode("utf-8", "replace").rstrip("\n")
                    if line.startswith("event:"):
                        evs.append(line[6:].strip())
            has_done = "done" in evs
            st2, det = http_get(f"/api/chat/sessions/{sid}")
            last = ((det.get("messages") or [])[-1] if det.get("messages") else {})
            content = str(last.get("content") or "")
            llm_ok = last.get("stage") == "done"
            graceful = llm_ok or (("数据缺失说明" in content or "失败" in content) and "仅供参考" in content)
            check("聊天链路（done+落盘）", has_done and st2 == 200 and graceful,
                  f"stage={last.get('stage')} msgs={len(det.get('messages') or [])}")
            try:
                del_req = urllib.request.Request(BASE + f"/api/chat/sessions/{sid}", method="DELETE")
                with opener.open(del_req, timeout=10):
                    pass
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        check("聊天链路", False, str(exc)[:120])

    # 5) 无头浏览器点击冒烟（桌面 + 手机）
    try:
        from playwright.sync_api import sync_playwright

        browser = sync_playwright().start()
        br = browser.chromium.launch(channel="msedge", headless=True, args=["--no-sandbox", "--disable-gpu"])
        errors: list[str] = []

        # 桌面：今日数据 + 600519 详情 + 行情卡详情
        pg = br.new_page(viewport={"width": 1280, "height": 900})
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(BASE, timeout=30000)
        pg.wait_for_selector("text=今日数据", timeout=25000)
        # 2026-08-07 新增：页面（含今日数据/额外指数降级横幅）不得出现英文异常原文
        body_text = pg.text_content("body") or ""
        eng_bad = [k for k in ("time data", "Bad Gateway", "Traceback", "Server Error")
                   if k in body_text]
        check("浏览器·页面无英文异常透传", not eng_bad, "; ".join(eng_bad))
        # R11-R1：首页改 Tabs 后查询流程 = 输入 → 查询 → 行情卡 → 查看详情
        pg.fill('input[placeholder*="输入名称或代码"]', "600519")
        pg.click("button:has-text('查询')", timeout=10000)
        pg.wait_for_selector(".rt-quote-box", timeout=30000)
        pg.click("button:has-text('查看详情（分时')", timeout=10000)
        pg.wait_for_selector(".stock-detail .detail-price", timeout=45000)
        meta = pg.text_content(".detail-meta-grid") or ""
        # 价格断言不写死具体数值（行情每天变化）；只要求非空且为数字
        price_text = (pg.text_content(".detail-price") or "").strip()
        check("浏览器·桌面详情 600519", bool(price_text) and any(ch.isdigit() for ch in price_text))
        check("浏览器·成交额无 NaN", "成交额" in meta and "NaN" not in meta)
        pg.click("button:has-text('返回')")
        pg.wait_for_selector('input[placeholder*="输入名称或代码"]', timeout=10000)
        # R12 补盲区（用户反馈）：侧栏「复盘记录」点击必须能打开报告，禁止 Not Found
        # （此前冒烟未覆盖侧栏历史点击路径；旧 /api/reviews/{date}/{time} 已移除，
        #  曾因前端仍走旧路由导致 404）。
        try:
            pg.click(".history-item", timeout=8000)
            pg.wait_for_selector(".report-layout", timeout=15000)
            body_hist = pg.text_content("body") or ""
            check("浏览器·侧栏历史复盘可打开（无 Not Found）",
                  "Not Found" not in body_hist and "复盘报告" in body_hist,
                  "report ok")
        except Exception as exc:  # noqa: BLE001
            check("浏览器·侧栏历史复盘可打开（无 Not Found）", False, str(exc)[:100])

        # 手机：聊天会话列表隐藏 + 抽屉 + 聊天详情
        pm = br.new_page(viewport={"width": 390, "height": 844})
        pm.on("pageerror", lambda e: errors.append(str(e)))
        pm.goto(BASE, timeout=30000)
        pm.wait_for_selector("text=今日数据", timeout=25000)
        # 复合流程竞态兜底：首次加载服务端数据未就绪时 reload 一次（二次加载命中缓存）
        try:
            pm.wait_for_selector('input[placeholder*="输入名称或代码"]', timeout=6000)
        except Exception:  # noqa: BLE001
            pm.reload()
            pm.wait_for_selector("text=今日数据", timeout=25000)
        # R11-R1：聊天移入首页 Tab——先切换到「分析师聊天」页签，再选分析师（阿狼）
        pm.click(".home-tab-btn:has-text('分析师聊天')", timeout=10000)
        pm.wait_for_selector(".pick-chip", timeout=10000)
        pm.click(".pick-chip", timeout=5000)
        try:
            pm.wait_for_selector('input[placeholder*="输入股票代码或名称"]', timeout=10000)
        except Exception:  # noqa: BLE001 - 诊断：打印搜索框真实状态
            diag = pm.evaluate(
                """() => {
                    const el = document.querySelector('input[placeholder*="输入股票代码或名称"]');
                    if (!el) return "NO_INPUT";
                    const r = el.getBoundingClientRect();
                    const st = getComputedStyle(el);
                    return JSON.stringify({top: r.top, bottom: r.bottom, w: r.width, h: r.height,
                        display: st.display, vis: st.visibility, opacity: st.opacity,
                        bodyScrollY: window.scrollY});
                }"""
            )
            raise TimeoutError(f"搜索框不可见，诊断={diag}，JS错误={errors[-3:]}") from None
        pm.fill('input[placeholder*="输入股票代码或名称"]', "600519")
        pm.wait_for_selector("button:has-text('进入聊天')", timeout=15000)
        pm.click("button:has-text('进入聊天')")
        try:
            pm.wait_for_selector(".chat-input", timeout=20000)
        except Exception as exc:  # noqa: BLE001 - 诊断后重试一次
            diag = pm.evaluate(
                """() => {
                    const inputs = [...document.querySelectorAll("input, textarea")].map(e =>
                        ({ph: e.placeholder || "", cls: e.className, vis: !!e.offsetParent}));
                    const chatView = document.querySelector(".chat-layout, .chat-view, .chat-page");
                    const errBox = document.querySelector(".upload-error, .error-box, .error");
                    return JSON.stringify({
                        inputs: inputs.slice(0, 10),
                        chatView: chatView ? chatView.className : null,
                        errText: errBox ? (errBox.textContent || "").slice(0, 120) : null,
                        appChildren: (document.querySelector(".app") || {}).children
                            ? document.querySelector(".app").children.length : null,
                    });
                }"""
            )
            print("CHAT-DIAG inputs:", diag)
            print("CHAT-DIAG body:", (pm.text_content("body") or "")[:200].replace("\n", " "))
            print("CHAT-DIAG errors:", errors[-3:])
            raise TimeoutError(f"进入聊天后 .chat-input 未出现（{exc}）") from None
        hidden = pm.eval_on_selector_all(".chat-sessions", "els => els.every(e => getComputedStyle(e).display === 'none')")
        check("浏览器·手机会话列表隐藏", bool(hidden))
        pm.click(".menu-btn")
        pm.wait_for_selector(".sidebar.mobile-open", timeout=10000)
        check("浏览器·手机抽屉含对话记录", pm.locator("text=对话记录").count() > 0)
        pm.click(".sidebar-backdrop")
        pm.wait_for_selector(".chat-input", timeout=10000)
        pm.click("button:has-text('详情')")
        pm.wait_for_selector(".stock-detail .detail-price", timeout=45000)
        check("浏览器·聊天页→详情", True)
        check("浏览器·无页面 JS 错误", len(errors) == 0, "; ".join(errors[:2]))
        br.close()
        browser.stop()
    except Exception as exc:  # noqa: BLE001
        check("无头浏览器冒烟", False, str(exc)[:120])

    fails = [r for r in results if not r[1]]
    print("")
    print(f"回测完成：共 {len(results)} 项，失败 {len(fails)} 项")
    for name, ok, extra in fails:
        print("  FAIL", name, extra)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
