"""交割单分析 API（R11-R1，T4）。

APIRouter（prefix="/api/trades"），由主 Agent 在 backend/main.py include_router
注册（本文件不修改 main.py）。契约（requirements-r11 第四节 + 前端 api.js）：

- ``POST /api/trades/upload``（multipart file）→ ``{trade_id, summary, records, metrics}``
  解析 → 存 ``data/trades/{trade_id}/records.json``（STOCK_REVIEW_DATA_DIR 可覆盖，
  测试隔离）→ 计算指标并缓存；``summary`` 含条数/区间/收益摘要；
- ``GET /api/trades/{trade_id}`` → ``{records, metrics}``（metrics 计算并缓存 JSON）；
- ``POST /api/trades/{trade_id}/analyze`` → ``{report, disclaimer}``
  （同步计算 + LLM，前端 loading；LLM 不可用自动规则引擎降级，绝不 500）；
- 错误一律中文：400 解析/参数失败、404 交易单不存在；文件大小 ≤ 10MB；
  扩展名白名单 .xlsx/.xls/.csv/.txt。

说明：
- 指标/分析全部同步执行（LLM 超时 120s 由 config 的 http_client 保证）；
- 上传时以 price_timeout=5 快速计算指标（行情拉取失败按成本兜底，标注来源），
  与 GET 读缓存的 metrics.json 完全一致；
- records.json 存储的是 TradeRecord.to_dict() 序列（contract_no 已脱敏，见 parser）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stock_review_crew.trades import (  # noqa: E402
    DISCLAIMER,
    MetricsResult,
    ParseError,
    analyze,
    compute_metrics,
    parse_trades,
)

logger = logging.getLogger("stock_review_crew.backend.trades")

router = APIRouter(prefix="/api/trades", tags=["trades"])


@router.get("")
def list_trades() -> dict[str, Any]:
    """R12 历史交割单列表（按上传时间倒序）：trade_id/时间/记录数/是否有分析报告/摘要。
    供交割单分析页历史列表展示；单目录损坏跳过并标注，绝不抛异常。"""
    root = trades_root()
    items = []
    if root.is_dir():
        for d in sorted(
            (p for p in root.iterdir() if p.is_dir() and _TRADE_ID_RE.fullmatch(p.name)),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        ):
            trade_id = d.name
            try:
                records = _load_records(trade_id)
                metrics = _load_or_compute_metrics(trade_id, records)
                items.append({
                    "trade_id": trade_id,
                    "created_at": _dir_mtime_iso(d),
                    "records_count": len(records),
                    "has_analysis": (d / "analysis.json").exists(),
                    "summary": _build_summary(records, metrics),
                })
            except Exception as exc:  # noqa: BLE001 - 单目录损坏跳过
                items.append({
                    "trade_id": trade_id,
                    "created_at": _dir_mtime_iso(d),
                    "records_count": 0,
                    "has_analysis": False,
                    "error": str(exc)[:80],
                })
    return {"trades": items, "disclaimer": DISCLAIMER}


def _dir_mtime_iso(d: Path) -> str:
    """目录最近修改时间 → ISO 字符串（用于列表排序展示）。"""
    try:
        import datetime

        return datetime.datetime.fromtimestamp(d.stat().st_mtime).isoformat(timespec="seconds")
    except Exception:  # noqa: BLE001
        return ""

MAX_FILE_BYTES = 10 * 1024 * 1024  # 10MB
ALLOWED_SUFFIXES = {".xlsx", ".xls", ".csv", ".txt"}
_TRADE_ID_RE = re.compile(r"^[0-9a-f]{12}$")


def trades_root() -> Path:
    """交割单存储根目录；环境变量 STOCK_REVIEW_DATA_DIR 可覆盖（测试隔离）。

    与 storage/reviews.py 的 reviews_root() 同约定：默认 <项目根>/data/trades。
    """
    override = os.environ.get("STOCK_REVIEW_DATA_DIR", "").strip()
    if override:
        return Path(override) / "trades"
    return ROOT / "data" / "trades"


def _ensure_trade_dir(trade_id: str) -> Path:
    directory = trades_root() / trade_id
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _valid_trade_id(trade_id: str) -> str:
    """校验 trade_id 格式（防目录穿越）；非法抛 404（对外不区分不存在）。"""
    value = str(trade_id or "").strip()
    if not _TRADE_ID_RE.fullmatch(value):
        raise HTTPException(status_code=404, detail="交割单不存在")
    return value


def _trade_dir(trade_id: str) -> Path:
    directory = trades_root() / _valid_trade_id(trade_id)
    if not directory.is_dir():
        raise HTTPException(status_code=404, detail="交割单不存在")
    return directory


def _save_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail="交割单数据文件损坏，请重新上传") from exc


def _load_records(trade_id: str) -> list[dict]:
    return _load_json(_trade_dir(trade_id) / "records.json")


def _load_or_compute_metrics(
    trade_id: str, records: list[dict], price_timeout: float = 15.0
) -> dict:
    """读缓存的 metrics.json；不存在则计算并缓存（保证 upload/GET 结果一致）。"""
    directory = _trade_dir(trade_id)
    metrics_file = directory / "metrics.json"
    if metrics_file.is_file():
        return _load_json(metrics_file)
    try:
        result = compute_metrics(records, price_timeout=price_timeout)
        payload = result.to_dict() if isinstance(result, MetricsResult) else dict(result)
        _save_json(metrics_file, payload)
        return payload
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - 指标计算任何异常 500 中文
        logger.exception("交割单指标计算失败：%s", trade_id)
        raise HTTPException(status_code=500, detail=f"指标计算失败：{exc}") from exc


def _build_summary(records: list[dict], metrics: dict) -> dict:
    """上传摘要：条数 / 区间 / 收益（供前端首屏展示）。"""
    account = metrics.get("account") or {}
    meta = metrics.get("meta") or {}
    return {
        "trade_count": len(records),
        "start_date": meta.get("start_date"),
        "end_date": meta.get("end_date"),
        "total_return_rate": account.get("total_return_rate"),
        "realized_pnl": account.get("realized_pnl"),
        "win_rate": (metrics.get("pnl") or {}).get("win_rate"),
        "is_partial": meta.get("is_partial"),
        "balance_source": meta.get("balance_source"),
    }


@router.post("/upload")
def upload_trades(file: UploadFile = File(...)) -> dict[str, Any]:
    """上传交割单：解析 → 落盘 → 指标缓存 → 摘要 + 记录 + 指标。"""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型：{suffix or '（空）'}（仅支持 {'、'.join(sorted(ALLOWED_SUFFIXES))}）",
        )
    try:
        content = file.file.read()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"文件读取失败：{exc}") from exc
    finally:
        try:
            file.file.close()
        except Exception:
            pass
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(status_code=400, detail="文件超过 10MB 大小限制，请拆分后上传")
    if not content:
        raise HTTPException(status_code=400, detail="文件内容为空")

    # 临时文件解析（保留原始扩展名，解析器按扩展名走对应路径）
    import tempfile

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
            handle.write(content)
            temp_path = Path(handle.name)
        try:
            records = parse_trades(temp_path)
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
    except ParseError as exc:
        raise HTTPException(status_code=400, detail=f"交割单解析失败：{exc}") from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"交割单解析失败：{exc}") from exc

    if not records:
        raise HTTPException(status_code=400, detail="未解析到任何成交记录，请检查文件内容")

    trade_id = uuid.uuid4().hex[:12]
    directory = _ensure_trade_dir(trade_id)
    records_payload = [record.to_dict() for record in records]
    _save_json(directory / "records.json", records_payload)
    metrics = _load_or_compute_metrics(trade_id, records_payload, price_timeout=5.0)
    return {
        "trade_id": trade_id,
        "summary": _build_summary(records_payload, metrics),
        "records": records_payload,
        "metrics": metrics,
    }


@router.get("/{trade_id}")
def get_trades(trade_id: str) -> dict[str, Any]:
    """查询交割单：记录 + 指标（指标首次计算并缓存 JSON）。"""
    records = _load_records(trade_id)
    metrics = _load_or_compute_metrics(trade_id, records)
    # R12：已生成的分析报告从 analysis.json 回读（历史列表回看，不重新生成）
    analysis = None
    analysis_path = _trade_dir(trade_id) / "analysis.json"
    if analysis_path.exists():
        try:
            analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - 损坏视同无分析
            analysis = None
    return {"trade_id": trade_id, "records": records, "metrics": metrics, "analysis": analysis}


@router.post("/{trade_id}/analyze")
def analyze_trades(trade_id: str) -> dict[str, Any]:
    """生成 AI 分析报告（同步：指标 + LLM，超时 120s；LLM 不可用规则引擎降级）。"""
    records = _load_records(trade_id)
    metrics = _load_or_compute_metrics(trade_id, records)
    try:
        result = analyze(records, metrics, max_rounds=2)
    except Exception as exc:  # noqa: BLE001 - analyze 自身不抛，这里兜底防御
        logger.exception("交割单 AI 分析失败：%s", trade_id)
        raise HTTPException(status_code=500, detail=f"分析失败：{exc}") from exc

    report = str(result.get("final_report") or "").strip()
    if not report:
        raise HTTPException(status_code=500, detail="分析报告生成为空，请重试")
    analysis_payload = {
        "final_report": report,
        "analysts": result.get("analysts") or [],
        "debate_history": result.get("debate_history") or [],
        "overall_tags": result.get("overall_tags") or [],
        "disclaimer": result.get("disclaimer") or DISCLAIMER,
        "degraded": bool(result.get("degraded")),
        "round_count": int(result.get("round_count") or 0),
    }
    try:
        _save_json(_trade_dir(trade_id) / "analysis.json", analysis_payload)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - 落盘失败不阻塞返回
        logger.warning("分析结果落盘失败（不阻塞返回）：%s", exc)
    return {"report": report, "disclaimer": analysis_payload["disclaimer"]}
