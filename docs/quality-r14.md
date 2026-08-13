# R14 质量基线（A5-quality）— rubric / 数字核验 / 免责声明 / 验收映射 / 集成闸门

> 2026-08-11 编写，供 R14 集成阶段 Eval 使用；与 `docs/requirements-r14.md` 配套。
> 本文件是 A5 任务唯一可写产物；其余文件只读（见 §五 所有权表）。
> 依据：`docs/requirements-r14.md`（§四 数据字典 / §五 API 契约 / §七 验收标准）、
> `docs/requirements-r12.md`（撮合口径冻结）、`src/stock_review_crew/quality.py`
> （既有 rubric 思路：1-5 分制、evidence 必填、失败无害、比率小数归一）。

## 〇、用途与适用范围

1. **评价对象**：R14 交付物——`plan.py` / `quotes.py` / `replay.py` / `review_batch.py` /
   `account_replay.py` / 测试改造（A4）/ 本文档之外的文档登记（PROGRESS、PLAYBOOK）。
2. **评价单位**：可对「R14 整体」打分，也可按任务 agent（A1-A5）分别打分；
   打回动作针对不达标的具体任务。
3. **与运行期质量分的关系**：`quality.py::score_review/score_chat` 是**运行期自动分**
   （等权 0.25、passed ≥ 3，写入 Langfuse）；本文档 rubric 是**交付期静态质量基线**
   （正确性权重最高、打回线 3.5）。两者互补：运行期分看自动化，交付期分看集成 Eval。
4. **打回语义**：加权总分 < 3.5 → 对应任务打回重做；任何一项为 1 分 → 直接打回
   （单项 1 分说明存在硬伤，不允许加权平均掩盖）。

## 一、Rubric 评分（1-5 分制，正确性权重最高，<3.5 打回）

### 1.1 维度与权重（冻结，总分 = Σ weight × score，保留 1 位小数）

| 维度 | 权重 | 说明 |
|---|---|---|
| 正确性（业务逻辑） | **0.35** | 权重最高：§七 验收硬门、手算样例、撮合/精度口径 |
| 契约符合 | 0.20 | §四 数据字典与 §五 API/CLI 契约逐字段一致 |
| 测试质量 | 0.20 | 手算用例、边界覆盖、断言强度、回归全绿 |
| 可维护 | 0.15 | 单一职责、计划唯一真源、无重复实现、所有权内改动 |
| 文档一致性 | 0.10 | 需求/代码/PROGRESS/PLAYBOOK/本文件不漂移 |

每项评分必须附 **evidence**（证据引用：文件:行号 / 测试用例名 / 命令输出摘要），
无 evidence 的项视为无效（沿用 `quality.py::evaluate_rubric` 的 evidence 必填规则）。

### 1.2 评分锚点（5=优秀 / 3=合格线 / 1=硬伤；4 与 2 按锚点插值）

#### 正确性（业务逻辑，权重 0.35）

| 分 | 锚点 |
|---|---|
| 5 | §七 1-6 全部可复现；手算样例逐位一致（10 万×3 成→2900 股→现金 70,992.46→卖出 101,419.318）；两次重放逐字节一致；离线全缓存命中；违规注入（超资金/T+1/裸卖空/涨跌停越界/非整百）全部被引擎拦截，引擎漏洞 0；边界（周末/节假日/空计划/资金不足/同价去重/规则引擎降级日）语义正确 |
| 3 | 主路径与主手算样例正确；部分边界未覆盖或降级标注不完整（如缺失代码以 None 而非「不在文件里」） |
| 1 | 核心口径错误：去重键失效重现重复 pending、现金精度漂移（float 累加）、确定性不成立、撮合区间判定错误 |

#### 契约符合（权重 0.20）

| 分 | 锚点 |
|---|---|
| 5 | plan.json 字段/None 语义/枚举与 §4.1 逐项一致；去重键 `(rec_date, skill_id, code, side, target_price)`；行情缓存缺失代码**不在文件里**；账户产物沿用 R12 结构；CLI 退出码 0/1/2 语义正确；无新增 HTTP API |
| 3 | 主契约符合，个别 None 语义或枚举边界漂移（如 source 枚举漏值） |
| 1 | 契约字段缺失/改名，或与 requirements-r14.md 直接矛盾 |

#### 测试质量（权重 0.20）

| 分 | 锚点 |
|---|---|
| 5 | 手算用例先行；确定性断言（hash/逐字节）；mock 完整降级链（含行情缺失标注）；断言容差收紧；违规注入用例齐全；全量 pytest ≥ 534 passed；一键回测 17/17 全绿 |
| 3 | 主用例齐全，边界不全或断言容差过大（如不校验精确金额） |
| 1 | 无手算样例、断言缺失、或回归未全绿即交付 |

#### 可维护（权重 0.15）

| 分 | 锚点 |
|---|---|
| 5 | registry + finalize 单点落盘；计划唯一真源（交易计划只写 plan.json，无 4 路写入漂移）；吸收 backtest_accounts 无重复实现；模块只在自己所有权内改动 |
| 3 | 存在轻微重复或耦合，但不影响迭代 |
| 1 | 多路写入漂移复现、重复实现并存、越界改动他人文件 |

#### 文档一致性（权重 0.10）

| 分 | 锚点 |
|---|---|
| 5 | requirements-r14.md 与实现一致；PROGRESS.md 登记；PLAYBOOK 按模板追加新坑；本文件验收映射不漂移；删除 backtest_accounts 后无残留文档引用其生产路径 |
| 3 | 个别文档滞后（如未登记 PROGRESS） |
| 1 | 文档与实现矛盾并误导后续开发 |

### 1.3 打回规则

- 加权总分 < 3.5 → 打回；
- 任一维度 = 1 分 → 直接打回（硬伤不可平均掩盖）；
- 打回后修复重评，重评须引用修复证据（diff / 测试输出）。

## 二、数字核验清单（目标价 / 成数 / 股数 / 现金精度；比率小数存储）

核验口径与 `quality.py` 数字核验器同源：比率字段小数存储（0.5=50%），文本 `50%` 与
数据 `0.5` 视为一致；金额按元；`None` 语义严格区分「字段缺失」与「值为 None」。

| # | 核验项 | 冻结口径 | 核验点 / 手算样例 |
|---|---|---|---|
| 1 | **目标价 target_price** | 元，>0，Decimal 两位精度（ROUND_HALF_UP） | 撮合：当日 [low, high] 覆盖 target 才成交；target ≥ 涨停价 → 买单拒绝；target ≤ 跌停价 → 卖单拒绝（主板 ±10%、创业板/科创板 ±20%，按前收盘×系数四舍五入 0.01）。plan.json 中 target_price 与快照/报告逐位一致 |
| 2 | **成数 position_cheng** | 整数 1-10；工具传 0 = 未用成数（改用 qty） | 折算公式：`floor(可用现金 × 成数/10 ÷ (target × (1+佣金率)))` 向下取整到 100 整数倍。手算：10 万 × 3 成、target=10.00 → **2900 股**，买入后现金 **70,992.46**（佣金 7.25 元等费用扣除） |
| 3 | **股数 qty** | >0 整数、100 整数倍 | 卖出 ≤ 可卖持仓（总持仓 − 当日买入冻结，T+1）；裸卖空拒绝。非整百、超持仓、超资金均拒绝并完整记录 reject_reason（中文） |
| 4 | **现金精度** | 全程 Decimal，禁止 float 累加 | 佣金 万2.5 最低 5 元（买卖双向）、印花税 卖出 0.05%、过户费 0.001%（双向）；`balance_after ≥ 0`。手算冻结：次日卖出 2900×10.50 → 现金 **101,419.318**（R12 验收冻结数字，逐位对齐） |
| 5 | **比率小数存储** | 涨跌幅/收益率/胜率/换手率等一律小数（0.5=50%），前端 ×100 | 与 `quality.py::_RATIO_FIELDS` / `_normalize_entity_value` 口径一致；`None` 显示「—」（不显示 0）；全中文、红涨绿跌 |
| 6 | **行情缓存** | `data/quotes_cache/{date}.json`：quotes[code] 缺失代码**不在文件里**（而非 None）；degraded 标注降级/缺失 | 重放全部命中缓存才算「离线可用」；任何缺失必须经 degraded 如实标注（铁律 7，绝不静默错报） |
| 7 | **计划去重** | 去重键 `(rec_date, skill_id, code, side, target_price)`；重跑整文件覆盖 | 同日重跑后无残留旧计划；不同 target_price 视为不同推荐（保留） |
| 8 | **确定性** | 相同 plan.json + 相同行情缓存 → 两次全量重放**逐字节一致** | 对比 trades/snapshots/account/replay_report 全部产物 hash |
| 9 | **输入指纹** | replay_meta.json 记录 plan.json 哈希、行情缺失标注、未结算 pending 数 | 指纹变化必须对应输入变化，用于审计重放来源 |

## 三、免责声明检查项（所有面向用户投资内容）

统一口径字符串（与 `quality.py::DISCLAIMER` 同源，禁止分叉）：
**「仅供参考，不构成投资建议」**

| # | 检查点（面向用户的投资内容） | 检查方式 |
|---|---|---|
| 1 | 复盘报告 Markdown（6 模式正文） | 组装层程序级强制追加；`score_review` structure/disclaimer 项（含=5，缺=1） |
| 2 | 聊天回复（末条回复） | `score_chat` structure 项：末条回复必须含免责声明 |
| 3 | 交割单分析报告 | 报告生成层强制；trades 测试断言（R11 既有） |
| 4 | 账户重放报告 `replay_report.md`（.tmp/，若展示给用户） | 生成层强制追加；新测试断言包含 DISCLAIMER |
| 5 | 前端页面文案（报告页/聊天/模拟账户页） | 快照与接口返回中携带的免责声明文本必须完整，不可截断 |

抽查命令（人工复核，集成阶段执行）：

```powershell
# 最新复盘报告含免责声明
Get-ChildItem data\reviews -Directory | Sort-Object Name -Descending | Select-Object -First 1 |
  ForEach-Object { Select-String -Path (Join-Path $_.FullName '*.md') -Pattern '仅供参考，不构成投资建议' }
# 重放报告含免责声明
Select-String -Path .tmp\account_replay\replay_report.md -Pattern '仅供参考，不构成投资建议'
```

违规处理：任一面向用户的产物缺失免责声明 → rubric 正确性/契约符合两维各降一档，
修复后重跑全量 pytest 方可交付。

## 四、验收用例清单（对照 requirements-r14.md §七 7 条逐条映射）

映射约定：A4 为测试先行任务，`tests/test_account_plan.py`、`test_quotes_cache.py`、
`test_account_replay.py` 为**计划新建文件**（本文档为验收基线，集成阶段校验落盘）；
`tests/test_r12_backtest.py`、`tests/test_pending_dedup.py` 为**改造文件**（现网基线用例
已存在，见下方「现网基线」列）。所有 pytest 命令统一 `-p no:cacheprovider`（PLAYBOOK）。

| §七 # | 验收标准 | 测试文件（A4 新建/改造） | 关键用例建议 | 执行命令 |
|---|---|---|---|---|
| 1 | plan.json 生成；同日重跑**整文件覆盖**，无残留旧计划 | tests/test_account_plan.py（新）；test_pending_dedup.py（改造，去重指向 plan） | `test_finalize_writes_plan_json`；`test_rerun_same_day_overwrites_no_stale`；`test_dedup_key_four_fields`（现网基线：test_place_order_dedup_same_rec / test_place_order_keeps_diff_price） | `python -m pytest tests\test_account_plan.py tests\test_pending_dedup.py -p no:cacheprovider -q` |
| 2 | 相同 plan.json + 相同行情 → 两次全量重放**逐字节一致** | tests/test_account_replay.py（新） | `test_replay_deterministic_byte_identical`（两次产物 hash 一致）；`test_replay_meta_fingerprint`（输入指纹稳定） | `python -m pytest tests\test_account_replay.py -p no:cacheprovider -q` |
| 3 | 补单日复盘闭环：D 重跑 → plan.json 更新 → D+1 起账户正确 | tests/test_account_replay.py（新）；tests/test_r12_backtest.py（改造指向 replay） | `test_backfill_single_day_chain`；手算断言复用 test_r12_backtest.py::test_engine_adapter_q1_signature（2900 股 / 101,419.318 冻结数字）+ test_accounts_engine.py 手算 13 项基线 | 同上两个文件分别跑，或 `python -m pytest tests\test_account_replay.py tests\test_r12_backtest.py -p no:cacheprovider -q` |
| 4 | 重放**离线可用**（行情全命中缓存）；缺失如实标注，绝不静默 | tests/test_quotes_cache.py（新）；tests/test_account_replay.py（新） | `test_replay_offline_all_cache_hit`；`test_missing_quote_annotated_not_silent`（degraded 标注、不抛错、如实说明） | `python -m pytest tests\test_quotes_cache.py -p no:cacheprovider -q` |
| 5 | scripts/backtest_accounts.py 移除后全量 pytest 仍绿；**违规注入仍被断言抓到** | tests/test_r12_backtest.py（改造：断言保留、指向 replay） | `test_assertion_catches_overdraft` / `test_assertion_catches_t1_sell_violation` / `test_assertion_catches_bare_short_sell` / `test_assertion_catches_price_out_of_limit` / `test_assertion_catches_non_lot100_and_negative_fee` / `test_assertion_passes_clean_day`；退出码：`test_run_backtest_exit_1_on_engine_violation`（违规→1）、`test_run_backtest_exit_2_when_engine_unavailable`（数据不可用→2） | `python -m pytest -p no:cacheprovider -q`（全量，≥ 534 passed） |
| 6 | validate 子命令正确标记 可用/需重跑（报告缺失/LLM 降级/计划为空） | tests/test_account_plan.py（新）；A3 CLI 冒烟 | `test_validate_flags_missing_report`；`test_validate_flags_llm_degraded`；`test_validate_flags_empty_plan`；输出含 `{date, ok, reasons[]}` | 单测：`python -m pytest tests\test_account_plan.py -p no:cacheprovider -q`；CLI：`python work\review_batch.py validate --start 2026-08-01 --end 2026-08-07` |
| 7 | 全量 pytest ≥ 534 passed + **一键回测 17/17 全绿** | scripts/regression.py（17 项：后端健康/全量 pytest/npm build/今日数据 indices 4 行/个股详情 600519/ETF 588170/额外指数降级中文/自动复盘 status/聊天链路/浏览器 9 项冒烟） | R14 无新增 HTTP API，断言不增；批跑 CLI 不破坏既有页面 | `powershell -ExecutionPolicy Bypass -Command ".venv\Scripts\python.exe scripts\regression.py"`（需提权 + 8501 服务在跑） |

集成阶段补充：`tests/test_r12_acceptance.py` 52 项验收用例（既有 skip）在本轮对齐启用后，
必须全绿且计入全量 pytest 基数。

## 五、集成闸门检查脚本建议（git diff 对照任务所有权，防越界）

### 5.1 所有权允许路径（来自 requirements-r14.md §六，冻结）

| 任务 | 允许改动（精确路径或目录前缀） |
|---|---|
| A1 计划层 | `src/stock_review_crew/accounts/plan.py`（新）、`src/stock_review_crew/graph.py`、`backend/main.py`（仅挂钩处） |
| A2 行情缓存+重放 | `src/stock_review_crew/accounts/quotes.py`（新）、`src/stock_review_crew/accounts/replay.py`（新） |
| A3 批跑脚本+并轨 | `work/review_batch.py`、`work/account_replay.py`（新）、`scripts/backtest_accounts.py`（**允许删除**）、`work/` 旧脚本（移入 `work/archive/`） |
| A4 测试先行 | `tests/`（新增 3 文件 + 改造 test_r12_backtest.py / test_pending_dedup.py） |
| A5 质量基线 | `docs/quality-r14.md`（本文件） |

### 5.2 建议脚本（集成阶段落盘 `scripts/gate_check.py`，主 Agent 执行）

```python
"""集成闸门：git diff --name-only 对照 R14 任务所有权表，防越界改动。"""
import subprocess, sys
from pathlib import Path

ROOT = Path(r"H:\stock_review_crew")
# 5.1 表转前缀白名单（目录前缀末尾带 os.sep；删除文件同样会出现在 diff 中）
ALLOWED = [
    "src/stock_review_crew/accounts/plan.py",
    "src/stock_review_crew/graph.py",
    "backend/main.py",
    "src/stock_review_crew/accounts/quotes.py",
    "src/stock_review_crew/accounts/replay.py",
    "work/review_batch.py",
    "work/account_replay.py",
    "scripts/backtest_accounts.py",      # 允许删除（A3）
    "work/archive/",
    "tests/",
    "docs/quality-r14.md",
]

def main() -> int:
    # git diff --name-only 同时覆盖 新增/修改/删除（HEAD vs 工作区）；-z 防文件名含换行
    out = subprocess.run(
        ["git", "diff", "--name-only", "-z", "HEAD"],
        cwd=ROOT, capture_output=True, text=True,
    ).stdout
    changed = [p for p in out.split("\0") if p.strip()]
    violations = []
    for p in changed:
        if not any(p == a or p.startswith(a.rstrip("/") + "/") for a in ALLOWED):
            violations.append(p)
    if violations:
        print("越界改动（不在 R14 所有权表内）：")
        for v in violations:
            print("  -", v)
        return 1
    print(f"闸门通过：{len(changed)} 个改动文件均在所有权表内")
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

### 5.3 附加闸门（建议与越界检查一起跑）

1. **产物存在性**：A1-A4 计划产物必须落盘——`src/stock_review_crew/accounts/plan.py`、
   `quotes.py`、`replay.py`、`work/review_batch.py`、`work/account_replay.py`、
   `tests/test_account_plan.py`、`tests/test_quotes_cache.py`、`tests/test_account_replay.py`；
2. **测试计数**：全量 pytest 输出 passed 数 ≥ 534，且 R14 新增用例全部 collected 非 skip
   （除 test_r12_acceptance.py 52 项按 §四 启用前仍计 skip 的既有例外）；
3. **一键回测**：scripts/regression.py 17/17 全绿；
4. **删除核验**：`scripts/backtest_accounts.py` 已移除，且全仓库无生产路径引用
   （`rg "backtest_accounts" --glob "!docs/*" --glob "!work/archive/*"` 无命中；
   `work/archive/` 与文档历史记录允许保留引用）；
5. **本文件验收自检**（A5 交付命令）：

```powershell
python -c "import pathlib; t=pathlib.Path('docs/quality-r14.md').read_text(encoding='utf-8'); assert 'rubric' in t.lower() or '评分' in t"
```

## 六、Eval 使用步骤（集成阶段）

1. 跑 §五 闸门（越界 + 产物 + 测试计数 + 回归）；任一失败 → 先修闸门再评分；
2. 对照 §四 逐条执行验收映射命令，记录每条 PASS/FAIL 与证据；
3. 按 §一 rubric 五维打分（每项附 evidence），加权总分 < 3.5 或任一项 1 分 → 打回；
4. 免责声明按 §三 抽查；数字精度按 §二 清单抽 3 条手算核对；
5. 全绿后在 PROGRESS.md 登记 R14 交付，并关闭本任务。
