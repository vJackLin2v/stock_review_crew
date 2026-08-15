# -*- coding: utf-8 -*-
"""数据存档 / 一键复原（复盘 + 模拟账户 + 行情缓存 + 交易 + 聊天）。

把 ``data/`` 整个目录打包成一份快照存档，需要时可一键复原到存档时刻。
- 存档目录：``backups/<名称>/data``（+ manifest.json 元信息）
- 复原前自动把「当前（可能已被污染的）data/」另存为 ``backups/pre_restore-<时间>/``，
  保证任何情况下数据都不会丢。

用法：:

    python scripts/backup_restore.py backup [--name NAME]   # 存档当前 data/
    python scripts/backup_restore.py list                   # 列出所有存档
    python scripts/backup_restore.py restore [--name NAME]  # 复原（默认最新正式存档）

只依赖标准库，兼容系统 python 与 .venv python。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA_ROOT = REPO / "data"
BACKUP_ROOT = REPO / "backups"
# 预恢复安全备份前缀（list/restore 默认跳过，不会被当作正式存档）
PRE_RESTORE_PREFIX = "pre_restore-"


def _ts() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _default_name() -> str:
    return f"snapshot-{_ts()}"


def _iter_backups(include_pre_restore: bool = False) -> list[Path]:
    if not BACKUP_ROOT.is_dir():
        return []
    out = []
    for p in sorted(BACKUP_ROOT.iterdir()):
        if not p.is_dir():
            continue
        if not include_pre_restore and p.name.startswith(PRE_RESTORE_PREFIX):
            continue
        if (p / "data").is_dir() or (p / "manifest.json").exists():
            out.append(p)
    return out


def _read_manifest(backup_dir: Path) -> dict:
    mf = backup_dir / "manifest.json"
    try:
        if mf.exists():
            return json.loads(mf.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    return {}


def _count_files(root: Path) -> int:
    return sum(1 for _ in root.rglob("*") if _.is_file())


def cmd_backup(name: str) -> int:
    if not DATA_ROOT.is_dir():
        print(f"数据目录不存在：{DATA_ROOT}")
        return 2
    dest = BACKUP_ROOT / name
    if dest.exists():
        print(f"存档名已存在：{name}（请换 --name，或先手动处理 {dest}）")
        return 2
    dest_data = dest / "data"
    dest_data.mkdir(parents=True, exist_ok=False)
    shutil.copytree(DATA_ROOT, dest_data, dirs_exist_ok=True)
    subdirs = sorted(p.name for p in dest_data.iterdir())
    files = _count_files(dest_data)
    manifest = {
        "name": name,
        "created_at": _ts(),
        "source": str(DATA_ROOT),
        "subdirs": subdirs,
        "file_count": files,
    }
    (dest / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已存档：{dest}")
    print(f"  子目录：{', '.join(subdirs)}")
    print(f"  文件数：{files}")
    return 0


def cmd_list() -> int:
    backups = _iter_backups(include_pre_restore=True)
    if not backups:
        print("暂无存档。")
        return 0
    print(f"共 {len(backups)} 个存档：")
    for p in backups:
        mf = _read_manifest(p)
        tag = "[安全备份]" if p.name.startswith(PRE_RESTORE_PREFIX) else ""
        created = mf.get("created_at", p.name)
        files = mf.get("file_count", _count_files(p / "data"))
        print(f"  {p.name}  {tag}")
        print(f"      创建于 {created}，{files} 个文件")
    return 0


def _restore_from(src_data: Path) -> None:
    """把 src_data 的内容整体覆盖回 data/（先清空再复制，保证复原到位）。"""
    if not src_data.is_dir():
        raise FileNotFoundError(f"存档数据缺失：{src_data}")
    # 清空 data/（保留 data/ 目录本身）
    for child in DATA_ROOT.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    # 复制回
    for child in src_data.iterdir():
        dst = DATA_ROOT / child.name
        if child.is_dir():
            shutil.copytree(child, dst)
        else:
            shutil.copy2(child, dst)


def cmd_restore(name: str | None) -> int:
    backups = _iter_backups(include_pre_restore=False)
    if not backups:
        print("没有可复原的正式存档（可先运行 backup）。")
        return 2

    target: Path | None = None
    if name:
        target = next((p for p in backups if p.name == name), None)
        if target is None:
            print(f"找不到存档：{name}")
            return 2
    else:
        target = backups[-1]  # 最新正式存档

    # 安全预存当前状态
    pre = BACKUP_ROOT / f"{PRE_RESTORE_PREFIX}{_ts()}"
    pre_data = pre / "data"
    pre_data.mkdir(parents=True, exist_ok=False)
    shutil.copytree(DATA_ROOT, pre_data, dirs_exist_ok=True)
    (pre / "manifest.json").write_text(
        json.dumps({
            "name": pre.name,
            "created_at": _ts(),
            "purpose": "restore 前的自动安全备份（复原前的当前状态）",
            "file_count": _count_files(pre_data),
        }, ensure_ascii=False, indent=1), encoding="utf-8")

    try:
        _restore_from(target / "data")
    except Exception as exc:  # noqa: BLE001
        print(f"复原失败：{exc}（当前状态已安全预存到 {pre}，可手动处理）")
        return 1

    mf = _read_manifest(target)
    print(f"已复原到存档：{target.name}（创建于 {mf.get('created_at', '?')}）")
    print(f"复原前的当前状态已预存：{pre.name}")
    print("仅供参考，不构成投资建议。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="data/ 存档与一键复原")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_backup = sub.add_parser("backup", help="存档当前 data/")
    p_backup.add_argument("--name", default=None,
                          help="存档名（默认 snapshot-时间戳）")

    sub.add_parser("list", help="列出所有存档")

    p_restore = sub.add_parser("restore", help="复原（默认最新正式存档）")
    p_restore.add_argument("--name", default=None, help="指定存档名")

    args = parser.parse_args(argv)

    if args.cmd == "backup":
        return cmd_backup(args.name or _default_name())
    if args.cmd == "list":
        return cmd_list()
    if args.cmd == "restore":
        return cmd_restore(args.name)
    return 2


if __name__ == "__main__":
    sys.exit(main())
