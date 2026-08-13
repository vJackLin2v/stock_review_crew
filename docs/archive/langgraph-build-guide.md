# 从零构建 LangGraph 项目指南

以本项目的演进过程为例，讲解 LangGraph 项目的核心概念和构建方法。

## 一、LangGraph 是什么

LangGraph 是一个**有状态、有环的图编排框架**。它不帮你做任何决策，只提供一张白纸让你画流程图。

核心三要素：**State（状态）→ Node（节点）→ Edge（边）**

```
State:  所有节点共享的数据（TypedDict + reducer）
Node:   图上每个框框 = 一个 Python 函数（可以调 LLM、调 API、处理数据）
Edge:   节点之间的连线（普通边、条件边、入口/出口）
```

## 二、项目搭建

```bash
uv init
uv add langgraph langchain langchain-openai
```

三个核心依赖：
- `langgraph` — 图编排引擎
- `langchain` — ChatModel、Tool、Prompt 等组件
- `langchain-openai` — OpenAI 兼容的 LLM 适配器（接 DeepSeek、Qwen 等）

## 三、State 设计（最重要的一步）

State 是整个项目的数据模型。设计原则：**所有节点需要的数据都在 State 里**。

```python
from typing import Annotated, TypedDict
import operator

class ReviewState(TypedDict):
    # 标量字段：直接赋值，后覆盖前
    date: str
    market_data: str
    final_report: str

    # 列表字段：用 Annotated + reducer 实现追加
    analyses: Annotated[list[dict], operator.add]  # ← 每个 Node 返回的 list 会合并
    debate_history: Annotated[list[dict], operator.add]
```

**关键坑**：普通 `list` 字段会被覆盖，用 `Annotated[list, operator.add]` 才会追加。

## 四、图结构设计

先画流程图，再写代码。本项目最终图：

```
fetch_data → news_analyst → trend_analysts → sentiment_analysts → host → (debate ⇄ host) → report → END
```

对应代码：

```python
from langgraph.graph import StateGraph, END

graph = StateGraph(ReviewState)

# 注册节点
graph.add_node("fetch_data", fetch_data_node)
graph.add_node("news_analyst", news_analyst_node)
graph.add_node("trend_analysts", trend_analysts_node)
graph.add_node("sentiment_analysts", sentiment_analysts_node)
graph.add_node("host", host_node)
graph.add_node("debate", debate_node)
graph.add_node("report", report_node)

# 连线
graph.set_entry_point("fetch_data")
graph.add_edge("fetch_data", "news_analyst")
graph.add_edge("news_analyst", "trend_analysts")
graph.add_edge("trend_analysts", "sentiment_analysts")
graph.add_edge("sentiment_analysts", "host")

# 条件边（循环的核心）→ host 决定继续辩论还是出报告
graph.add_conditional_edges("host", router, {
    "report": "report",
    "debate": "debate",
})
graph.add_edge("debate", "host")   # 辩论完回到主持人
graph.add_edge("report", END)

app = graph.compile()
```

## 五、Node 怎么写

Node 就是一个 Python 函数，签名固定：

```python
def my_node(state: ReviewState) -> dict:
    # 1. 从 state 读取输入
    data = state["market_data"]

    # 2. 处理（调 LLM、调 API、纯计算...）
    result = llm.invoke(data)

    # 3. 返回要更新的字段（dict 会 merge 到全局 state）
    return {"final_report": result.content}
```

Node 里可以干任何事：
- 调 LLM：`llm.invoke(messages)`
- 调 Tool：`tool.invoke(args)`
- 纯数据处理
- 什么都不干，直接 return

**Node 不是 Agent，不是 Task，只是图上的一个步骡。**

## 六、条件边（循环的关键）

条件边让图能**回到前面的节点**，形成循环：

```python
def router(state: ReviewState) -> str:
    """返回目标节点的名称"""
    if state.get("discussion_done"):
        return "report"
    if state.get("round_count", 0) >= state.get("max_rounds", 3):
        return "report"
    return "debate"

graph.add_conditional_edges("host", router, {
    "report": "report",   # router 返回 "report" → 去 report
    "debate": "debate",   # router 返回 "debate" → 去 debate
})
```

`router` 函数只返回字符串，LangGraph 根据映射表找到目标节点。**这就是 LangGraph 相比 CrewAI 最核心的优势——原生支持循环。**

## 七、执行方式

两种执行方式，各有用途：

```python
# 方式一：invoke — 一次性返回完整结果
final_state = app.invoke(initial_state)
result = final_state.get("final_report")

# 方式二：stream — 逐节点返回，中间过程可见
for chunk in app.stream(initial_state):
    node_name = list(chunk.keys())[0]
    update = chunk[node_name]
    print(f"[{node_name}] 完成")  # 实时输出
```

**选择**：CLI/日志用 `stream`（看中间过程），API/前端用 `invoke`（拿最终结果）。

## 八、Tool 与 LLM 集成

### 8.1 定义 Tool

```python
from langchain_core.tools import tool

@tool
def get_stock_info(code: str, date: str) -> str:
    """查询个股数据。参数: code=股票代码, date=日期"""
    # ... 调 AKShare/Tushare ...
    return json.dumps(result)
```

### 8.2 绑定到 LLM

```python
llm_tools = llm.bind_tools([get_stock_info, search_history])

response = llm_tools.invoke(messages)

# 处理工具调用
if response.tool_calls:
    for tc in response.tool_calls:
        result = get_stock_info.invoke(tc["args"])
        messages.append(ToolMessage(content=result, tool_call_id=tc["id"]))
    response = llm_tools.invoke(messages)
```

**注意**：不是所有模型都支持 tool calling。本项目用 DeepSeek V4 Flash，实测支持但有日期幻觉问题——解决方法是在 prompt 里明确指定 date 参数的值。

## 九、并行执行

LangGraph 支持两种并行：

### 9.1 图级并行（多个 Node 同时跑）
```python
graph.add_edge("fetch_data", "analyst_a")  # A 和 B 同时从 fetch_data 出发
graph.add_edge("fetch_data", "analyst_b")
```

### 9.2 节点内并行（Python 线程池）
```python
from concurrent.futures import ThreadPoolExecutor

with ThreadPoolExecutor(max_workers=len(skills)) as pool:
    futures = {pool.submit(invoke_one, s): s["name"] for s in skills}
    for f in as_completed(futures):
        results.append(f.result())
```

本项目用节点内并行：趋势派阿狼和冰川同时调 LLM，省一半时间。

## 十、项目演进路径

本项目是如何一步步搭建的：

| 阶段 | 做了什么 | LangGraph 对应 |
|------|---------|---------------|
| 1 | 定义 State，写死一个 Node | State + 1 Node |
| 2 | 加数据拉取 Node，连成线 | 2 Node + 普通边 |
| 3 | 加分析师 Node（串行） | 多 Node 串联 |
| 4 | 加主持人 + 条件边 | 条件边，形成循环 |
| 5 | 拆派别（趋势/情绪） | 拆 Node，趋势→情绪 |
| 6 | 趋势派内并行 | ThreadPoolExecutor |
| 7 | 加资讯分析 Node | 插入新 Node 到图中间 |
| 8 | 加 Tool 调用 | bind_tools + tool_calls 处理 |
| 9 | 加知识库检索 | 新 Tool，LLM 主动调用 |
| 10 | 加前端 + 日志 | stream 输出 |

**核心经验**：先跑通串行链路，再加条件边、并行、Tool。不要一开始就设计复杂图。

## 十一、常见坑

| 坑 | 表现 | 解决 |
|----|------|------|
| **list 字段不追加** | Node 返回的列表覆盖旧数据 | 用 `Annotated[list, operator.add]` |
| **条件边死循环** | 图一直在循环，停不下来 | 加 `round_count` 和 `max_rounds` 限制 |
| **stream 最后一个 chunk 不全** | `final_state` 缺少数据 | 用 `app.invoke()` 获取最终结果 |
| **LLM 不支持 tool calling** | `bind_tools` 后不调工具 | 用 `try/except` 回退到普通 `llm.invoke()` |
| **LLM 编造 tool args** | 日期参数被编造成 2025-05-22 | 在 prompt 里明确写死参数值 |
| **Node 函数签名不对** | LangGraph 报错 | 签名必须是 `(state) -> dict` |
| **State 字段拼错** | `state.get("discussion_topic")` vs `"dicussion_topic"` | 多写测试，检查每个 Node 的返回值 |

## 十二、和 CrewAI 的思维对比

| 概念 | CrewAI | LangGraph |
|------|--------|-----------|
| 共享数据 | `self.state.xxx` | `state["xxx"]` |
| 顺序执行 | `Process.sequential` | `add_edge("A", "B")` |
| 条件路由 | Flow 的 `@router` | `add_conditional_edges` + 路由函数 |
| 反馈循环 | Flow 里 `while` 循环 | 条件边回到前面的 Node |
| Agent | 有 role/goal/backstory 的实体 | 不存在，Node 是函数 |
| Task | Agent 执行的任务 | 不存在，Node 里自己调 LLM |
| Tool | 挂载在 Agent 上 | `bind_tools()` 或直接在 Node 里调 |
| 记忆 | `memory=True` | `checkpointer` 或自己实现 |

## 十三、总结

LangGraph 的本质：**一张有状态的流程图，每个框框是一个 Python 函数，连线决定执行顺序，条件边实现循环。**

比 CrewAI 灵活但代码量多 30%。适合：
- 需要精硡控制流程的场景
- 多 Agent 需要反复讨论/辩论
- 需要在特定步骤暂停等待人工输入

不适合：
- 简单线性任务（一个 Chain 就够）
- 没有循环需求的场景（CrewAI 更省代码）
