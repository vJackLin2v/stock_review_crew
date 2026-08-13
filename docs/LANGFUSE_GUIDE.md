# Langfuse 打分机制使用指南（stock_review_crew）

> 适用范围：本项目 R10 Q4「每次复盘/聊天都产出质量评估（Langfuse 评分）」。
> 读完本文你可以：在面板上找到任意一次复盘/聊天的 trace 与 score、看懂本项目自动打的
> 确定性 rubric 分数、学会用四种方式给任何一条 trace 打分、搭一个质量看板长期监控，
> 以及在 trace 不出现时按清单自查。

---

## 1. Langfuse 是什么

Langfuse 是开源的 LLM 可观测性平台（SaaS 面板在 `cloud.langfuse.com`，也可自托管），
核心对象只有两个：

| 对象 | 含义 | 在本项目里的样子 |
|---|---|---|
| **Trace** | 一次完整请求（一次复盘、一轮聊天） | `review:close`、`chat:parse` |
| **Score** | 挂在 trace 上的评分（数值/布尔/分类/标签） | `review_quality`、`chat_quality` |

它帮你回答三个问题：模型实际输出了什么（prompt/输出/token 数/延迟）、输出质量如何
（分数）、质量随时间怎么变化（看板/趋势）。打分机制 = 给每条 trace 打上可比较、
可回溯的分数。

## 2. 本项目的接入点

所有接入都在既有模块里，不需要你改代码：

| 接入点 | 位置 | 说明 |
|---|---|---|
| Langfuse 凭据 | `.env`：`LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_BASE_URL`（兼容 `LANGFUSE_HOST`） | 缺失或 `LANGFUSE_ENABLED=0` 时全部 no-op |
| LLM 调用监控 | `src/stock_review_crew/config.py` 的 `langfuse_callback()` 进程级单例 | `graph._callbacks()` 复用它；**4.x 签名只收 `public_key`**（见 PLAYBOOK 2026-08-06 条目），secret/host 由 SDK 从环境变量读取 |
| 聊天 trace | `quality.trace_chat(stage, payload)` | name=`chat:{stage}`，自动带上 source/engine/stage |
| 复盘 trace | `quality.trace_review(stage, payload, metadata)` | name=`review:{stage}`，返回 trace id，metadata 含 source/engine/stage + 调用方传入的 mode/date/降级清单 |
| 复盘打分 | `quality.score_review(report, structured, meta, trace_id)` | 确定性 1-5 分，写回 name=`review_quality` |
| 聊天打分 | `quality.score_chat(messages, structured, meta, trace_id)` | 确定性 1-5 分，写回 name=`chat_quality` |
| 状态检查 | `GET /api/health` 的 `langfuse` 字段 | `{enabled, callback_ok, host, mode}`；`callback_ok=true` 才算监控真正打开 |

### 开关：脚本跑不上报、页面跑上报（2026-08-12）

默认行为：**前端页面提交的复盘/聊天全部上报 Langfuse**（凭据齐全即上报）。
批量脚本（如历史回补）不想刷屏面板时，按调用方式二选一：

| 调用方式 | 关闭方法 | 说明 |
|---|---|---|
| 走 API 的脚本（`POST /api/reviews`） | 请求体加 `trace: false` | job 级开关：只跳过本次复盘的 `review:done` trace 与 `review_quality` score；其余任务不受影响。`trace` 必须为布尔值，非布尔返回 400；不传默认 `true` |
| 直连引擎的脚本（进程内调 graph/quality） | 进程环境变量 `LANGFUSE_ENABLED=0`（或 `false/off/no`） | 全局总开关：该进程内 trace/score/LLM 回调全部 no-op，凭据存在也不生效 |

示例（走 API 的批量脚本）：

```python
body = {"date": d, "mode": "close", "trace": False}
req = urllib.request.Request(url, json.dumps(body).encode(), headers={"Content-Type": "application/json"})
```

> 说明：`trace=false` 只关 Langfuse 上报，**不影响** rubric 质量分本身——复盘结果里的
> `quality` 摘要照常计算（产品展示用），只是不写回 Langfuse。

### LLM 级回调（全流程 span）开关（2026-08-12）

**前端页面发起的复盘默认开启 LLM 级 Langfuse 回调**——面板里每条 `review:done` trace
下会出现完整的子 span 树（5 位分析师的 `ChatOpenAI` 调用、取数工具、辩论、报告等），
可见每次 LLM 的 prompt/输出/token 数。脚本批量复盘（`trace: false`）默认关闭。

实现：`backend/main.py` 的 `_run_review_job` 用 `config.langfuse_callbacks_scope(trace)`
包住引擎调用——job 级 ContextVar 开关，随分析师线程池 `copy_context` 传播，多 job
并发互不干扰；`graph._callbacks()`/`trades.analyst._callbacks()` 在调用方线程求值。

环境变量 `LANGFUSE_CALLBACKS` 可全局强制覆盖：

| 值 | 效果 |
|---|---|
| 未设置 | 跟随 job 开关（页面复盘开、脚本关） |
| `1`/`true`/`on`/`yes` | 强制开启（所有 LLM 调用都打 span，含脚本/聊天） |
| `0`/`false`/`off`/`no` | 强制关闭（页面复盘也不打 LLM 级 span，只剩复盘级 trace） |

> 性能提醒：LLM 级回调同步 OTLP 导出实测把单次 LLM 调用拖 4~10 倍，这是 2026-08-10
> 默认关闭的原因；现在只对前端页面复盘开启，脚本批量不受影响。聊天链路默认仍关闭
> LLM 级回调（只有 `chat:*` 阶段 trace）。

健康检查法（PLAYBOOK 验证命令）：

```bash
cd H:\stock_review_crew
.venv\Scripts\python.exe -c "from stock_review_crew import config; print(config.langfuse_status())"
# 期望：{'enabled': True, 'callback_ok': True, 'host': 'https://cloud.langfuse.com', 'mode': 'auto'}
```

> 注意：系统 Python（3.13）没装 langfuse 包，测试环境里 `callback_ok` 为 false 属预期；
> 服务进程必须用 `.venv` 启动。

## 3. 面板怎么看（traces / scores）

1. 打开 <https://cloud.langfuse.com>，用 **`.env` 里的 `LANGFUSE_PUBLIC_KEY` 作为登录
   key**（项目页签名后即可进入对应项目）。
2. 左侧 **Traces** 页列出所有请求。每条 trace 有 name、输入/输出摘要、token 数、耗时、
   时间戳。用右上搜索/筛选框输入 `review:` 或 `chat:` 前缀即可只看复盘/聊天。
   - 复盘 trace 形如 `review:close`、`review:fetch`、`review:report`（stage 对应流水线阶段）；
   - 聊天 trace 形如 `chat:parse`、`chat:analyze`。
3. 点开任意一条 trace：
   - **Metadata**：看 `source=stock_review_crew`、`engine=review/chat`、`stage`、`mode`、`date`、
     降级清单；
   - **Input/Output**：复盘 payload（数据快照）与分析报告；聊天是消息序列；
   - **Token / 延迟**：右上角用量与耗时（LLM 调用的 generation 观测项里更细）；
   - **Scores**：看挂在这条 trace 上的评分（如 `review_quality`）。

## 4. 如何打分（四种方式）

### 4.1 面板手动 Annotation（最快上手）

1. Traces 页选中一条 trace → 右侧 **Scores** 区 → **Add Score**；
2. 选类型：
   - **NUMERIC**：1-5 分（对齐本项目的 rubric 量纲）；
   - **BOOLEAN**：如「含免责声明 true/false」；
   - **CATEGORICAL**：如 优秀/合格/不合格；
   - **TAG**：自由标签，如 `幻觉`、`降级正常`。
3. 填 name（建议与自动分同名，如 `review_quality`，方便看板合并）、value、comment 后保存。

适合抽样人工复核：每天抽 3-5 条 trace 手动打一遍，与自动分对照。

### 4.2 API 打分（本项目已自动，也可手动补）

复盘/聊天完成时，主编排调用 `score_review`/`score_chat` 已自动写回
`review_quality`/`chat_quality`（有凭据且传入 trace_id 时）。手动补分示例：

```bash
# 1) 生成 Basic 头（Windows PowerShell）
$pair = "$env:LANGFUSE_PUBLIC_KEY`:$env:LANGFUSE_SECRET_KEY"
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($pair))

# 2) 给指定 trace 补一个 4 分
curl.exe -X POST "https://cloud.langfuse.com/api/public/scores" `
  -H "Authorization: Basic $b64" -H "Content-Type: application/json" `
  -d '{\"name\":\"review_quality\",\"value\":4,\"traceId\":\"<trace_id>\",\"comment\":\"人工复核补分\"}'
```

Python（.venv 的 langfuse 4.x 用 `create_score`；旧版 SDK 是 `client.score`，参数一致）：

```python
import json, os
from langfuse import Langfuse

client = Langfuse(
    public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
    secret_key=os.environ["LANGFUSE_SECRET_KEY"],
    host=os.environ.get("LANGFUSE_BASE_URL", "https://cloud.langfuse.com"),
)
client.create_score(
    name="review_quality", value=4, trace_id="<trace_id>",
    comment=json.dumps({"by": "manual", "note": "人工复核补分"}, ensure_ascii=False),
)
client.flush()  # 强制等异步导出完成，脚本场景建议调用
```

### 4.3 LLM-as-judge（批量自动化打分）

思路：用另一个模型按固定 rubric 给输出打分，替代人肉抽查。两条路线：

- **面板路线**：Langfuse 面板的 Evaluators/Config 里配置 judge 模板（prompt 里写明
  评分维度与 1-5 分档位），挂在 trace 上批量跑；
- **代码路线**：本项目已内置 rubric 聚合函数
  `quality.evaluate_rubric(items, pass_threshold=3.0)`——每项 `{name, score(1-5),
  evidence}` 校验后汇总 `{total, average, passed, items}`。外部脚本可让 judge 模型
  对每条 trace 输出 `{name, score, evidence}` 列表，再交给 `evaluate_rubric` 判通过。
  `score_review`/`score_chat` 的确定性四项（见 §5）也可作为 judge 的基准答案对照。

注意：LLM-as-judge 的分要和确定性 rubric 分用**不同 score name**（如
`review_quality_judge`），否则看板无法区分来源。

### 4.4 Dashboard 质量看板（长期监控）

面板左侧 **Dashboard** 新建看板，用 Scores 数据建图：

- **分数分布**：`name = review_quality` 的 value 直方图（看整体质量形态）；
- **趋势**：按天/按周的 value 平均值折线（看回归：发布新 prompt 后分数是否掉）；
- **按 trace name 分组**：按 `trace.name` 分组比较（`review:close` vs
  `review:pre_market` vs `chat:*`），定位哪个环节最弱；
- **降级影响**：给 `review_quality` 按 metadata 里 degraded 是否为空做分组对比，
  量化「降级损失了多少分」。

有了看板，打分机制就闭环了：自动分（4.2）兜底全量 → 人工/LLM 分（4.1/4.3）抽检校准 →
看板趋势发现异常 → 修 prompt/数据源 → 趋势回升验证修复。

## 5. 本项目质量信号说明

### 5.1 score_review 四项（等权 0.25，总分 = Σ 0.25×item 四舍五入 clamp 1-5）

| item | 满分条件 | 扣分规则 |
|---|---|---|
| `structure` 结构完整性 | 报告含该 mode 全部模式章节标题 | 按命中比例线性映射 `1 + 4×(命中/总数)` 取整；章节表冻结在 `quality.REVIEW_SECTIONS`（与 `prompts.MODE_INFO` 一致），调用方可用 `meta["sections"]` 覆盖避免漂移 |
| `disclaimer` 免责声明 | 含「仅供参考，不构成投资建议」 | 缺失则该项 1 分 |
| `numbers` 数字核验 | `verify_numbers` mismatch=0 | 每 1 条 mismatch 扣 1 分，下限 1 |
| `degraded` 降级标注可见 | `meta.degraded` 为空（不适用满分），或非空且报告含「降级」字样 | `meta.degraded` 非空但报告无「降级」→ 该项 1 分 |

`passed = score ≥ 3`。返回 `{score, breakdown:[{item, score, max, evidence}], passed}`，
`evidence` 是可直接复核的 JSON 明细（章节命中数、mismatch 数与首条原因、降级条数）。

### 5.2 score_chat 三项（等权 1/3）

| item | 满分条件 |
|---|---|
| `structure` | 末条回复含免责声明 |
| `numbers` | 消息文本数字核验 mismatch=0（规则同 5.1） |
| `analysts` | `meta.analysts` 名单逐位在消息的作者字段/文本中有回复；未提供名单则不适用满分 |

### 5.3 verify_numbers 原理（防幻觉底座）

- 6 位股票代码必须存在于结构化数据中，编造代码记为 mismatch；
- 带字段别名（涨幅/收盘价/成交额/换手率…）的数字声称必须与数据一致，字段不存在也算
  mismatch（无法溯源即扣分）；
- 比率统一口径：`0.5 = 50%`（数据存小数，文本两种写法都算一致）；
- 金额支持 千/万/亿/万亿；`None`=无数据，文本声称有值而数据为 None 记 mismatch；
- 宁可漏报不误报：无别名、无法映射的数字（轮次/日期）跳过不指控。

### 5.4 降级标注口径

`state.degraded_notes`（降级明细列表）→ `meta.degraded`。空列表/False = 未降级；
非空 = 已降级，此时**报告正文必须出现「降级」字样**（如「涨停数据源降级，已用缓存」），
这是铁律 7「绝不静默错报」在评分层的落地：降级但报告没写，就是扣分项。

## 6. 故障排查

### trace 在面板上没出现

按顺序自查：

1. **凭据**：`.env` 是否有 `LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY` +
   `LANGFUSE_BASE_URL`？`LANGFUSE_ENABLED` 是否被设成 `0/false`？缺任一 → 全链路 no-op；
2. **host 可达性**：`cloud.langfuse.com` 在本机网络是否通（本项目需代理
   `127.0.0.1:7890`；沙箱里是假代理 `127.0.0.1:9`，连不上属正常，见 PLAYBOOK §1）；
3. **`callback_ok=false` 检查法**：`GET /api/health` 看 `langfuse.callback_ok`；
   为 false 时用 §2 的 `.venv` 命令看是否报导入/构造错误；
4. **导入失败**：langfuse 包是否在服务所用 venv 里（系统 Python 无此包属预期）；
5. **异步延迟**：SDK 异步导出，面板通常几秒内出现；批量/网络抖动时可能更久，
   不是失败。脚本里需要即时确认可调 `client.flush()`；
6. **score 没出现但 trace 在**：`score_review`/`score_chat` 只在**有 trace_id** 时写回；
   检查调用方是否把 `trace_review` 的返回值传给了 score。

### 导出失败重试的含义

`create_score`/`trace` 调用是**尽力而为**：返回后由 SDK 后台线程异步上报，失败自动
退避重试，不会抛给主流程（本项目铁律：任何异常在 `quality` 模块内消化）。面板暂时
没有 = 可能还在重试，不代表丢了；只有长时间（分钟级）不出现才按上表排查。

### 常见误判

- 测试环境（系统 Python）`callback_ok=false` 是**预期**，不是故障；
- `quality.trace_chat` 在老 SDK 与 4.x 上的可用方法不同：代码已做兼容
  （先 `client.trace`/`client.score`，缺失时回退 4.x 的 `start_observation`/
  `create_score`），不要因为某个 SDK 版本缺方法就判定监控坏了；
- 沙箱/CI 里 `HTTP_PROXY=http://127.0.0.1:9` 是假代理隔离，任何网络失败都先怀疑它。

## 7. 合规

- `.env` 里的 `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` **不得**出现在日志、
  截图、聊天窗、文档示例或任何提交内容里；public key 用于登录面板，secret key 用于
  API 鉴权，两者都属于敏感凭据；
- 打分的 comment/evidence 只写质量结论与数据口径，不要把完整对话原文/客户敏感信息
  塞进 score（面板默认按项目隔离，但仍遵守最小必要原则）；
- 面向用户的任何投资内容必须带「仅供参考，不构成投资建议」——这既是业务铁律，
  也是 `disclaimer` 评分项的满分条件。

## 8. 附录：手算样例（验证评分函数）

好报告（close 模式，7/7 章节 + 免责声明 + 数字全对 + 无降级）：

```
structure=5, disclaimer=5, numbers=5, degraded=5
总分 = (5+5+5+5)×0.25 = 5 → score=5, passed=True
```

缺免责声明（其余全对）：

```
(5+1+5+5)×0.25 = 4 → score=4
```

3 条数字 mismatch（其余全对）：

```
numbers = 5-3 = 2 → (5+5+2+5)×0.25 = 4.25 → 四舍五入 4
```

全部踩雷（无章节/无免责声明/≥4 mismatch/降级不可见）：

```
1×0.25×4 = 1 → clamp 到 1（rubric 下限 1，不会出现 0 分）
```
