# -*- coding: utf-8 -*-
"""R14 A3 账户重放 CLI 薄壳。

契约（docs/requirements-r14.md 第五节/第六节/第七节/第八节，唯一真源）：
- `python work/account_replay.py [--start D] [--end D] [--rebuild] [--fixture]`
- 只做参数解析与旧输出清理，重放逻辑转调
  `stock_review_crew.accounts.replay`（A2 全量重放入口）：
  结算日序列 = 已存在 plan.json 的日期集合，只消费 plan.json + 行情缓存，
  不调 LLM；产出 .tmp/account_replay/replay_report.md + replay_meta.json；
- 退出码（透传 replay 入口）：0=无违规，1=发现引擎漏洞/断言失败，
  2=数据不可用（无复盘产物）；
- --rebuild：默认即全量重放，该参数仅显式语义 + 清空旧输出
  （.tmp/account_replay）；不动仓库 data/ 与账户数据（由 replay 自身落盘覆盖）。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
OUT_DIR = REPO / ".tmp" / "account_replay"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="R14 账户全量重放 CLI（薄壳，转调 accounts.replay 重放入口）")
    parser.add_argument("--start", default=None,
                        help="起始结算日 YYYY-MM-DD（含，默认不限）")
    parser.add_argument("--end", default=None,
                        help="结束结算日 YYYY-MM-DD（含，默认不限）")
    parser.add_argument("--rebuild", action="store_true",
                        help="从零重放（默认即全量重放；显式语义 + 清空旧输出"
                             " .tmp/account_replay）")
    parser.add_argument("--fixture", action="store_true",
                        help="确定性样例行情（不读缓存/不取网络），供测试/回归")
    args = parser.parse_args(argv)

    if args.rebuild and OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)

    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    from stock_review_crew.accounts import replay  # noqa: PLC0415 - 入口内导入

    code = replay.run_replay(start=args.start, end=args.end,
                             report_dir=OUT_DIR, fixture=args.fixture)
    if code == 2:
        print("[account_replay] 数据不可用：data/reviews/ 下无 plan.json 复盘产物，"
              "请先运行复盘批跑（work/review_batch.py run）生成计划。",
              file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
