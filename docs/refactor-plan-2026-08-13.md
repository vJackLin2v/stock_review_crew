# 重构设计 v1（2026-08-13，聚焦 P0 复盘 + 分析师模拟下单）

> 本文为项目负责人（我）的设计思路，落地交给 deepseek-v4-flash 子代理。
> 待 `docs/audit-features-2026-08-13.md`（审计子代理）出稿后据此修订 v2。
> 优先级：P0 复盘 / 模拟下单 > P1 数据展示 / 交割单 > P2 聊天。

## 〇、已确认的死代码 / 遗留（grep 实锤，待用户确认后清理）

| 项 | 位置 | 证据 | 处置 |
|---|---|---|---|
| 拆分失败残留死文件 | `src/stock_review_crew/tools/_base.py` + `_index_daily.py` / `_zt_pool.py` / `_auction.py` / `_breadth.py` / `_lhb.py` / `_minute.py` / `_sectors.py` / `_macro_news.py` / `_sentiment.py` / `_stock_info.py`（共 11 个） | 仅互相 `from ._base import …` 自引用；`tools/__init__.py` 与 `graph.py` 均从 `stock_data.py` 单文件导入，无任何生产/测试 import 它们 | 归档到 `work/archive/split_leftover/`（不硬删，先归档） |
| 拆分恢复脚本 | `work/graph_orig.py`、`graph_head_v.py`、`graph_uncomp.py`、`fix_graph_head.py`、`rebuild_graph.py`、`check_*.py`、`probe_*.py`、`test_rec_parser.py`、`inspect_recommends.py`、`smoke_accounts.js` 及 `*.json` 结果 | 均为 08-13 拆分失败+`.pyc` 恢复的中间产物 | 归档到 `work/archive/`（保留 `review_batch.py`、`account_replay.py`、`run_sectors_july.py` 等仍在用的 ops 脚本） |
| calc_qty 工具残留 | `accounts/engine.py::calc_buy_qty` / `calc_sell_qty` | 生产代码零调用（`calc_qty` 工具已于 08-12 移除）；仅 `calc_buy_qty` 有一个测试引用，`calc_sell_qty` 完全零引用 | 删除两函数 + 对应测试 `test_calc_buy_qty_kechuang_*` |

## 一、自动下单解析归一（P0 模拟下单核心，最高性价比）

**现状**：`graph.py::_llm_analyst` 内叠了三套互相补丁的正则（约 110 行）：
1. qty 式（`买入 代码 [名]：买入价X元 买入N股` / 卖出同理）；
2. 成数式（`代码 [名]：买入价X元 仓位N成`，带后顾断言排除动词开头）；
3. 结构化成数式（`买入 代码 [名]：买入价X元 仓位N成`）。

三套各自重复「卖出无持仓校验 + plan.add + tool_log」逻辑。

**设计**：抽取 `accounts/plan_extractor.py`，单一入口：

```python
extract_plans(text, rec_date, skill_id, skill_name, held_codes) -> list[PlanRec]
```

内部用**一套正交正则**同时匹配 `买入/卖出 + 6位代码 + [可选名称] + 目标价 + (股数 | 成数)`，统一走 `plan.add`；卖出前统一做 `held_codes` 校验（无持仓 → 跳过 + 记录 `sell_skipped_no_position`）。返回的 `PlanRec` 由 `graph` 转成 tool_log。

**收益**：三套正则 → 一套；去重/持仓校验单点化；新增 `tests/test_plan_extractor.py` 单测（qty式/成数式/带前缀/无名称/无持仓卖出/一行多只，各一手算断言）。

## 二、graph.py 瘦身（P0 复盘引擎，函数级抽取）

**现状**：1830 行单文件，混装 LangGraph 节点 + 规则引擎 + 渲染 + 下单解析 + 进度钩子。

**设计**（**函数级搬迁，严禁按行号切片**——PLAYBOOK 08-13 教训）：

1. 规则引擎 → `rule_engine.py`：`_rule_facts` / `_facts_sentence` / `_direction_sentence` / `_summary_sentence` / `_rule_analyst` / `_rule_host` / `_rule_report` / `_rule_tags` / `_RULE_PLAN` / `_RULE_DEBATE` / `_sign`（约 250 行）。
2. Markdown 渲染 → `rendering.py`：`_fmt_value` / `_dict_to_md` / `_block_md` / `_render_market_markdown`（约 150 行）。
3. 下单解析 → `accounts/plan_extractor.py`（见 §一）。
4. `graph.py` 保留：进度钩子 + 6 个 LangGraph 节点 + 图构建 + `build_result`（约 300 行）。

**执行纪律**（交付给子代理的硬约束）：
- 改动前 `git show HEAD:src/stock_review_crew/graph.py > work/archive/graph_backup.py` 备份；
- 每次只搬迁一个模块 → 立即 `python -m pytest tests/test_graph.py tests/test_analysts_merge.py -p no:cacheprovider -q` 全绿 → 再搬下一个；
- 只动 `import` 与函数体迁移，不改任何函数逻辑；拆分前后行为必须等价（回归 17/17 全绿）。

## 三、分析师提示词（已完成设计，落地中）

- style_anchor 风格锚点 + 规则合并去重（本轮已派子代理实现，见 §任务2）。

## 四、backend/main.py 测试兼容 re-export 清理（低优先，P1）

**现状**：`backend/main.py` 末尾 `from backend.jobs import (...)` / `from backend.snapshot import (...)` / `from backend.routes_data import (...)` 共约 30 个内部符号的 re-export，是拆路由后为兼容「测试直接 monkeypatch `backend.main.XXX`」打的补丁。

**设计**：把 `tests/test_backend*.py` 中对 `backend.main.XXX` 的 monkeypatch 目标改为直接 import `backend.jobs` / `backend.snapshot` / `backend.routes_data` 的新家，然后删除 main.py 的 re-export 块。收益：消除「宿主模块实时读取（`_host`）」这一层间接，降低心智负担。

## 五、待审计报告补充项（v2 再定）

- 数据层 `tools/stock_data.py`（约 3000 行）是否需要进一步拆分（PLAYBOOK 教训：大文件拆分风险高，除非收益明确否则维持单文件）；
- 聊天（P2）是否有可下线/精简的入口；
- 前端 `frontend/src/` 的组件健康度与死样式。

## 附：清理/重构的验证基线

- 系统 python 3.13：`python -m pytest -p no:cacheprovider -q`（基线 715 passed / 52 skipped）；
- 前端：`npm run build`（需提权）；
- 终验：`scripts/regression.py` 一键回测 17/17 全绿（AGENTS 铁律 8）。

> 本文所有内容仅供项目重构决策参考，不构成投资建议。
