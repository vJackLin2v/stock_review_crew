<!-- 已合并进 requirements.md，本文档保留供追溯，勿再新增改动（2026-08-13） -->
# R10 需求定稿 — 市场天气/情绪温度 + 纳指 + 板块温度榜 + 额外指数 + 风格坐标 + 设置页

> 2026-08-06 用户逐条确认（Q1/Q2）。前置：T1/T2 复盘降级修复 + Langfuse 启动打开已完成
> （docs/PROGRESS.md 2026-08-06 段）。本文件为 R10 唯一契约，冲突以本文件为准。

## 〇、用户确认记录

| # | 结论 |
|---|---|
| Q1-1 | 去掉基本面评分（F7）与公司资料卡（F8）——短线为主，基本面影响不大 |
| Q1-2 | 美股只做纳斯达克（综合 + 纳指100），不做罗素/道指/费半/VIX 全景 |
| Q1-3 | 市场天气/情绪温度（F3）升级为 **P0**（短线选手核心） |
| Q1-4 | P2 全要：平均股价/微盘/高股息额外指数（F2）、风格坐标（F4）、设置页（F12）；大神转载（F10）不做 |
| Q2 | 前端样式参考 satori5.icu 的卡片布局与信息密度，优化现有「今日数据」区 |
| Q3 | AKShare 探测与接入按主编排方案执行 |
| Q4 | **必须**：每次复盘/聊天都产出质量评估（Langfuse 评分），并交付《Langfuse 打分机制使用指南》 |

范围：F3（P0）、F6-纳指（P0）、F5 板块温度榜（P1）、F9 观察列表（P1）、F2（P2）、F4（P2）、F12（P2）。
不做：F7 基本面评分、F8 公司资料卡、F10 转载、F11 趣味（后续可加）。

### Q4 质量评估与 Langfuse 打分机制（硬需求）
- **复盘链路**：每次复盘完成后自动：① 上报 Langfuse trace（name=`review:{stage}`，
  metadata 含 date/mode/降级清单）；② 确定性 rubric 打分（1-5，含结构完整性/免责声明/
  数字核验/降级标注可见性，证据为 JSON 明细）写回 Langfuse score（name=`review_quality`）；
  ③ job result 增加 `quality` 摘要字段（score、明细、trace_id），前端结果页展示「本次复盘质量」。
- **聊天链路**：现有 quality.trace_chat 保持；新增 `score_chat` 确定性评分（结构/免责声明/
  数字核验），name=`chat_quality`，会话消息响应附 quality 摘要（可选展示）。
- **教学文档**：`docs/LANGFUSE_GUIDE.md`——Langfuse 面板怎么看 trace/score、如何建立打分机制
  （手动 annotation / API scores / LLM-as-judge / dashboard 质量看板）、本项目 rubric 与扩展方法。
- 无凭据时全部 no-op（现有测试语义保持）。

## 一、功能定义

### F3 市场天气/情绪温度（P0）
首页「今日数据」区新增一张综合卡：
- **市场天气**：晴 / 晴间多云 / 多云 / 偏冷有雨（一个词概括当日市场状态）；
- **情绪温度**：0-100 分 + 标签（火热/偏暖/中性/偏冷/冰点）+ 依据条目（每条中文一句话）；
- 数据来自现有数据块（涨停/跌停/炸板率/连板高度/涨跌家数/昨日涨停红盘率），不新增数据源；
- 手算规则冻结（见 §二 数据字典），测试必须手算抽查。

### F6 美股·纳斯达克（P0）
首页「今日数据」区新增「纳斯达克」卡：
- 纳斯达克综合（.IXIC）+ 纳指100（NDX）两项：最新价、涨跌幅（小数）、近 60 交易日走势（点数数组）；
- 数据源：AKShare `index_us_stock_sina`（主）→ `index_global_hist_em`（备）→ 缓存 → 标注缺失；
- 历史补做（复盘日期非当日）时走势仍取最近 60 交易日并标注「非当日」。

### F5 板块温度榜（P1）
「今日数据」板块卡升级：涨幅榜 top5（hot）+ 跌幅榜 top5（cold），每项含名称/涨跌幅（小数）/领涨股；
数据源：东财行业板块实时（现有 sectors 链）→ 腾讯行业备用。

### F9 观察列表（P1）
- 侧栏/首页「我的观察」：自选股列表（代码/名称/最新价/涨跌幅），支持添加（代码或搜索）、删除、置顶排序；
- 存储：`data/watchlist.json`（本地文件，不涉及账号体系）；
- 只读消费现有行情接口（/api/data/quote），不新增行情源。

### F2 额外指数（P2）
首页指数卡下方新增：平均股价、微盘股、高股息股 三个指数（最新价/涨跌幅）；
数据源：AKShare 通达信指数（`stock_zh_index_daily_tx`/`index_zh_a_hist`）实测后冻结代码；
缺失时整卡降级标注（不阻塞首页）。

### F4 风格坐标（P2）
「今日数据」新增「风格坐标」卡：大盘 vs 小盘、价值 vs 成长 两个维度强弱（0-100 + 标签）；
实现：大盘=沪深300、小盘=中证1000（`index_zh_a_hist`），价值/成长先用全市场 PE 分档计算
（`stock_zh_a_spot_em` 已有）+ 申万风格指数（`index_analysis_daily_sw`，探测后定）；
任何维度数据缺失 → 该维度标注「数据缺失」，不整体降级。

### F12 设置页（P2）
- 深色/浅色主题切换（localStorage，立即生效）；
- 涨跌配色：红涨绿跌 / 绿涨红跌（全局生效，警示框不参与）；
- 首页默认图表周期（分时/日线）；
- 全站字号（较小/标准/较大/特大）；
- 纯前端本地设置（localStorage），无后端接口。

## 二、数据字典（字段唯一真源）

### 市场天气/情绪温度（F3）
输入（全部小数比率，None=无数据）：
| 字段 | 定义 | 来源 |
|---|---|---|
| zt_total | 涨停家数 | zt_pool.limit_up.count |
| dt_total | 跌停家数 | zt_pool.limit_down.count |
| zhaban_rate | 炸板率（小数） | zt_pool.zhaban_rate |
| lianban_height | 最高连板数（int） | zt_pool.tier 最大板数 |
| up / down | 涨/跌家数 | breadth |
| red_rate | 昨日涨停今日红盘率（小数） | sentiment.red_rate |

评分规则（**冻结，手算可验证**，`score = clamp(base + Σ加分, 0, 100)`）：
```
base = 50
涨跌家数比 r = up/(up+down)（分母为 0 时该条不加分）:
  r>0.6 → +8；0.5<r≤0.6 → +4；0.4≤r≤0.5 → 0；0.3≤r<0.4 → -8；r<0.3 → -14
涨停-跌停差 d = zt_total - dt_total：每 10 家 +1（上限 +12，d<0 时每 10 家 -1，下限 -12）
炸板率：<0.25 → +6；0.25~0.4 → 0；>0.4 → -12
连板高度：≥5 → +6；3~4 → +3；<3 → -4（缺失不加分）
红盘率：>0.6 → +6；<0.4 → -8（缺失不加分）
（2026-08-06 严格化：原加分幅度减半+上限收窄，强市不再轻易满分，评分差异更明显）
```
天气映射：score≥75 晴；60–74 晴间多云；40–59 多云；<40 偏冷有雨。
情绪标签：≥80 火热；65–79 偏暖；45–64 中性；30–44 偏冷；<30 冰点。
输出结构：
```json
{
  "date": "YYYY-MM-DD",
  "weather": "晴间多云",
  "emotion_score": 63,
  "emotion_label": "中性",
  "reasons": ["涨跌家数比 0.67：+15", "涨停 80 家、跌停 5 家：+15", "炸板率 0.20：+10", "连板高度 5：+10", "昨日涨停红盘率 0.68：+10"],
  "source": "规则引擎（基于现有行情快照）",
  "degraded": false
}
```
手算样例：up=3000/down=1500（r=0.67→+8）、zt=80/dt=5（d=75→+7）、炸板 0.20→+6、
连板 5→+6、红盘率 0.68→+6 ——
**验收用例：score=50+8+7+6+6+6=83 → 天气晴、情绪偏暖**。

### 纳斯达克（F6）
```json
{
  "items": [
    {"name": "纳斯达克综合", "symbol": ".IXIC", "close": 17689.36, "pct_change": 0.0123,
     "pre_close": 17474.6, "open": null, "high": null, "low": null,
     "points": [{"date": "2026-08-05", "close": 17689.36}], "is_today": false}
  ],
  "source": "新浪/东财",
  "degraded": false,
  "degraded_reason": []
}
```
比率小数；`is_today` 标记数据是否为当前交易日（历史补做时为 false，前端显示「非当日」）。

### 板块温度榜（F5）
```json
{"hot": [{"name": "半导体", "pct_change": 0.023, "leader": "XX科技"}],
 "cold": [{"name": "煤炭", "pct_change": -0.015, "leader": "XX煤业"}],
 "source": "东财行业", "degraded": false}
```
pct_change 小数；不足 5 条时按实际数量返回；整体缺失时 `degraded=true`。

### 额外指数（F2）
```json
{"items": [{"name": "平均股价", "code": "880003", "close": 22.15, "pct_change": 0.008, "source": "通达信指数"}],
 "source": "AKShare", "degraded": false}
```
代码以实测为准（§四 探测任务），字段冻结如上。

### 风格坐标（F4）
```json
{"large_vs_small": {"score": 62, "label": "大盘占优", "desc": "沪深300 +0.8%，中证1000 -0.3%"},
 "value_vs_growth": {"score": 45, "label": "均衡", "desc": "价值/成长数据缺失或接近"},
 "source": "指数代理+全市场PE分档", "degraded": false}
```
维度规则：score = 50 + (大盘涨跌幅 - 小盘涨跌幅) × 100（clamp 0-100，label 按 ≥60 大盘占优 / ≤40 小盘占优 / 其他均衡）；
价值/成长同理（价值涨跌幅 - 成长涨跌幅）。任一输入缺失 → 该维度 score=null、label="数据缺失"。

## 三、API 契约（前端依赖，冻结）

```
GET /api/data/market-weather → §二 F3 结构（200；数据缺失时 weather="数据缺失", emotion_score=null）
GET /api/data/us-nasdaq    → §二 F6 结构（200；两指数独立降级）
GET /api/data/sector-heat  → §二 F5 结构
GET /api/data/extra-indices→ §二 F2 结构
GET /api/data/market-style → §二 F4 结构
GET /api/watchlist         → [{code, name, note, pinned, created_at}]
POST /api/watchlist        → Body {code, note?} → 200 {code, name, note, pinned, created_at}（重复代码 409 中文）
DELETE /api/watchlist/{code} → 204 / 404 中文
PUT  /api/watchlist/order  → Body {codes: [...]} → 200（重排序，缺失代码忽略）
```
响应恒含中文错误；比率一律小数；None 显示「—」。设置项（F12）纯前端无接口。

## 四、AKShare 探测任务（M1 首步，主编排提权执行）
1. `index_us_stock_sina(symbol=".IXIC"/".NDX")` 字段与数据日期；
2. `index_global_hist_em(symbol=".IXIC")` 备用可用性；
3. 平均股价/微盘股/高股息指数代码：`stock_zh_index_daily_tx`（880003 等）与 `index_zh_a_hist`（东财代码）实测；
4. `index_zh_a_hist(symbol="000300"/"000852")` 沪深300/中证1000 日线；
5. 申万风格 `index_analysis_daily_sw` 字段（价值/成长 proxy，不可用则用 PE 分档）。

## 五、模块拆分与文件所有权（并发 ≤5）
| 模块 | 唯一写权限 | 只读契约 |
|---|---|---|
| M1 数据层 | `src/stock_review_crew/tools/market_extra.py`（新）、`src/stock_review_crew/tools/__init__.py`（导出） | §二/§四 |
| M2 后端 | `backend/main.py`（新增 5 个 /api/data/* + watchlist 4 路由）、`src/stock_review_crew/storage/watchlist.py`（新） | §二/§三 |
| M3 前端 | `frontend/src/**`（今日数据卡片/观察列表/设置页/normalize/样式） | §二/§三（开发期 mock 对齐 satori 样式） |
| M4 测试 | `tests/test_data_market_extra.py`、`tests/test_backend_market_extra.py`、`tests/test_watchlist.py` | 仅 §二/§三（不看实现，验收用例先行，未完成标 skip） |
| 主编排 | 集成/全量回归/PLAYBOOK/PROGRESS | 全部 |

规则：禁止 git commit/push、禁止真实数据入库、禁止自行安装依赖（akshare 已有）；
测试用系统 `python -m pytest -p no:cacheprovider`；前端 `npm run build` 需提权；
完成交付三件套：代码 + 测试全绿 + 变更文件清单。

## 六、验收标准
1. F3 手算样例通过（§二）；6 模式复盘报告不回归（288 基线）；
2. F6/F5/F2/F4 各至少一条真实数据冒烟（用户网络环境；沙箱内降级标注合法）；
3. 观察列表增删改排全链路（含 409/404 中文）；
4. 设置页四项即时生效（浏览器冒烟）；
5. 前端 normalize 契约断言 + 无头浏览器点击冒烟（参考 regression.py 既有链路）；
6. 全量 pytest 全绿 + `npm run build` 成功 + Langfuse health `callback_ok=true`；
7. 所有投资内容带免责声明；全中文；None 显示「—」。
