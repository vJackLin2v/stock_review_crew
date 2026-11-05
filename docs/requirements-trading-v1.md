# 交易体系 v1：个股止盈止损 + 条件单引擎（需求文档）

> 状态：已与用户确认，规格冻结。数据字典唯一真源仍是 `docs/requirements.md` §六。
> 本文描述「分析师提示词 + 计划字段 + 撮合引擎」三层如何落地一套可执行的个股止盈止损体系，
> 解决当前「分析师胜率低、买高卖低、无止损纪律」的问题。

---

## 一、背景与目标

- 现状：分析师（如阿狼）只有买入价+仓位，没有止盈止损；卖出全靠次日临场拍脑袋，导致
  5/8 卖出亏损、近半计划因「买入价挂太低」未成交（拒绝率 49%）。
- 目标：给每个买入计划绑定「止损价 / 止盈价 / 最大持有天数」，由**引擎机械执行**（条件单），
  并让分析师提示词输出这些字段（A 层），彻底消除「无纪律卖出」。

## 二、交易规则（v1 统一固定规则，已确认）

> v1 所有买入统一用一套规则；「核心仓 60 日线移动止损 + 13/34 日线分批止盈」留到 v2。

### 1. 买入价（限价单，现有语义不变）
- `target_price` = 分析师愿意成交的**限价单价格**，参考**今日**收盘价附近设定（软约束）。
- 系统按**次日** [low, high] 撮合：次日触及即成交；未触及则不成交，属正常（不代表看错）。
- **严禁任何「必成交 / 保证成交 / ±X%以内」等未来函数表述**（分析师预测次日，无法预知次日区间）。

### 2. 止损（持仓期每日检查）
- 止损价 `stop_loss` = 买入价 × 0.95（默认，可显式覆盖）。
- 触发：当日**盘中最低价 < stop_loss**。
- 成交价：
  - 开盘价 ≥ stop_loss → 按 **stop_loss** 成交；
  - 开盘价 < stop_loss（跳空打穿）→ 按**开盘价**成交。即 `成交价 = min(开盘价, stop_loss)`。
- **一字跌停**（开盘即封死跌停、全天无法成交）→ **无限顺延到开板日**，开板即卖。

### 3. 止盈（持仓期每日检查）
- 止盈价 `take_profit` = 买入价 × 1.10（默认，可显式覆盖）。
- 触发：当日**盘中最高价 ≥ take_profit**。
- 成交价：开盘价 ≤ take_profit → 按 take_profit；开盘价 > take_profit（跳空高开）→ 按开盘价。
  即 `成交价 = max(开盘价, take_profit)`。

### 4. 持有超时（打野仓专用 = v1 所有仓位）
- `max_hold_days` = 13（默认）。
- 买入日算第 1 天；持有到**第 13 个交易日收盘**仍未触发止损/止盈 → **收盘价直接卖出**。

### 5. 单日检查优先级
对每个已持有（非当日买入）的持仓，按顺序：① 一字跌停则顺延；② 止损；③ 止盈；④ 超时。

## 三、字段契约

### plan.json 买入计划新增字段
```json
{ "code":"002031", "side":"buy", "target_price":6.42, "position_cheng":1,
  "stop_loss":6.10, "take_profit":7.06, "max_hold_days":13 }
```
- `stop_loss` / `take_profit` / `max_hold_days` 均可缺省，缺省时引擎按
  `买入价×0.95` / `买入价×1.10` / `13` 计算。
- 卖出计划不变（不新增字段）。

### 分析师推荐输出格式（A 层提示词）
买入：`买入 代码：买入价XX.XX元，仓位X成，止损XX.XX元，止盈XX.XX元，持有13天，原因是XXX`
- 止损/止盈/持有天数为**可选**（缺省走引擎默认），但提示词应引导分析师显式思考并输出。
- 禁止未来函数表述；止损价/止盈价必须 > 0 且 止损 < 买入价 < 止盈。

## 四、改动范围

### A 层（提示词，主 AI 负责）
- 5 位分析师的 `skills/{id}/skill.json`（`prompt` + `style_anchor`）：
  删除未来函数表述、补充「个股止盈止损 + 仓位纪律」、引导输出止损/止盈/持有天数。

### B 层（引擎，派子代理）
1. `src/stock_review_crew/accounts/quotes.py`：行情加 `open` 字段（跳空成交需要）。
2. `src/stock_review_crew/accounts/plan_extractor.py` + `plan.py`：解析/校验新增三字段，
   缺省用默认值。
3. `src/stock_review_crew/accounts/engine.py`：
   - 买入成交时把 `stop_loss/take_profit/max_hold_days/buy_date` 挂到持仓；
   - 新增条件单检查：每日对非当日买入的持仓按 §二.5 顺序执行止损/止盈/超时，生成卖出
     交易记录（含 realized、费用按现有口径），处理跳空/一字跌停。
4. `src/stock_review_crew/accounts/replay.py`（`settle_plans` + `run_replay`）：每个结算日
   在匹配新计划前，先对存量持仓跑条件单检查。

### 数据缺口（B 层一并补）
- 行情缓存/取数补 `open`。
- 持仓期每个交易日的 OHLC（settle_plans 已按日抓持仓股行情，复用即可，仅需补 open）。

## 五、验收标准（逐条可验证）
1. 买入计划带 stop_loss/take_profit/max_hold_days（或正确走默认 0.95/1.10/13）。
2. 止损：盘中最低价 < 止损价 → 当日成交价 = min(开盘价, 止损价)；一字跌停顺延到开板。
3. 止盈：盘中最高价 ≥ 止盈价 → 成交价 = max(开盘价, 止盈价)。
4. 超时：第 13 个交易日收盘仍未触发 → 收盘价卖出。
5. 卖出产生的交易记录费用/realized 口径与现有引擎一致，现金自洽、T+1 不破坏。
6. 全量 pytest（系统 Python `C:\Python313\python.exe -m pytest tests/ -p no:cacheprovider`）
   与既有账户回放校验通过；新增专项测试覆盖上述 1~5（含跳空、一字跌停、13 日超时）。
7. 提示词改动不引入未来函数表述，5 位分析师 skill.json 均含止盈止损纪律。

---

## 六、移动止盈 v1.1（爱在冰川）

> 状态：规格已与用户确认（2026-08 后补充），为 v1 个别分析师可用的增量止盈模式。
> 语义/字段实现见 `src/stock_review_crew/accounts/{engine,plan,plan_extractor}.py` 与专项测试。

### 1. 语义（唯一口径）

移动止盈在**固定止盈（§二.3）之外**并行可选，二者互斥：买入计划带
`trailing_drawdown` → 该持仓走移动止盈；否则走现有固定止盈。take_profit 仍是移动止盈的
**激活阈值**（= 买入价 × 1.10，不因启用移动止盈而改）。

| 要素 | 定义 |
|---|---|
| **激活** | 持有期内某日盘中最高价 `high >= take_profit`（涨幅达 +10%） |
| **峰值跟踪** | 激活后 `peak_high = max(peak_high, 当日 high)`；买入时初始化 `peak_high = None` |
| **移动止盈线** | 激活后每个结算日 `stop = peak_high − trailing_drawdown × avg_cost` |
| **触发卖出** | `day.low < stop` → 卖，成交价 `= min(open, stop)`；open 缺失 → stop；跳空低开按开盘价 |

- 激活后每一天都按 `stop` 检查（即便当日 `high < take_profit` 回落，也同样检查/跟踪峰值）。
- 激活与回落可**同日发生**（high 先达阈值、low 又跌穿 stop）→ 当日触发卖出。
- 同日触发优先级沿用 §二.5：一字跌停 → 止损 → 止盈（含移动）→ 超时。
- 移动止盈与固定止盈互斥；启用移动止盈的持仓不复用 `max(open, take_profit)` 固定口径。

例（买入价 100、take_profit=110、trailing_drawdown=0.05）：
- 峰值 115（+15%）→ stop = 115 − 5 = 110（+10%）；
- 峰值 117（+17%）→ stop = 117 − 5 = 112（+12%）。

### 2. 数据字段

- `PlanRec.trailing_drawdown: Optional[float] = None`：比率小数，0.05 = 5 个百分点。
  - 提供则须 `0 < x < 1`（校验失败 → 抛中文 ValueError）；
  - `to_dict` / `from_dict` / 序列化同步；`None` → JSON `null`。
- `plan_extractor`：买入行新增解析 `回落N%`（或 `回落N个百分点`）→ `trailing_drawdown = N/100`；
  解析不到 → `None`；卖出行不新增字段。
- `engine.place_order`：买入成交把 `trailing_drawdown` 挂到持仓，并初始化 `peak_high = None`；
- `engine.check_conditional_orders` ③止盈分支：计划带 `trailing_drawdown` → 移动止盈，
  否则固定止盈（既有行为不变）；
- `engine.apply_conditional_defaults` 不改默认（take_profit 仍为激活阈值）；
  `trailing_drawdown` 透传（None 默认）。

## 七、分析师专属交易模型（2026-08-16 最新回测打磨）

> 状态：已落地。每位分析师在 `skills/{id}/skill.json` 新增 `trading_model`（给 LLM 的中文规则）
> 与 `trading_params`（机器可读兜底参数）。当推荐行未显式给出止损/止盈/持有天数/回落幅度时，
> 重放/撮合层用 `engine.apply_analyst_conditional_defaults` 按 `skill_id` 取专属参数兜底；
> 显式字段始终优先。

| 分析师 | 定位 | 止损 | 止盈 | 持有 | 移动止盈 | 单票/总仓/只数 |
|---|---|---|---|---|---|---|
| alang 阿狼 | 趋势波段 | -5% | +10% | 13天 | 无 | ≤5成 / ≤10成 / ≤4只 |
| baxiaoxian 拔小弦 | 超短反包 | -4% | +6% | 3天 | 无 | ≤3成 / ≤7成 / ≤2只 |
| bingchuan 爱在冰川 | 逻辑低吸 | -5% | +8% | 5天 | 回落4% | ≤3成 / ≤8成 / ≤2只 |
| tiechui 铁锤 | 跟随确认 | -4% | +6% | 3天 | 无 | ≤3成 / ≤7成 / ≤2只 |
| yangjia 炒股养家 | 情绪博弈 | -5% | +8% | 3天 | 无 | ≤5成 / ≤9成 / ≤3只 |

- 通用买入价纪律（2026-08-16 修正）：买入价必须严格低于当日涨停价，禁止把目标价设成
  涨停价/高于涨停价；卖出价必须严格高于跌停价。这是针对回测中大量「目标价=涨停价被拒买」
  的修复。
- 通用回测纪律（2026-08-17 新增，prompts.py ANALYST_BASE_TEMPLATE 统一注入）：单日新建
  买入最多 2 只、同板块/同逻辑只选 1 只；买入价要贴近次日大概率触及的支撑位，禁止长期挂
  远低于现价的低价单；同一持仓同日只允许一个卖出计划，已清仓不重复卖出；上一笔止损后下一笔
  必须降仓或暂停。
- `ANALYST_TRADING_DEFAULTS` 位于 `src/stock_review_crew/accounts/engine.py`，
  `replay.py` 在构造买入条件单时调用 `apply_analyst_conditional_defaults`。
