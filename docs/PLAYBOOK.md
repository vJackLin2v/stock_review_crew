# PLAYBOOK — 项目踩坑与解法手册

> 所有 agent（Codex/Claude）开工前必读。遇到环境/工具/权限/网络/业务逻辑问题时**先查这里**；
> 解决问题后**按文末模板追加新条目**，方便后续所有 agent。
> 来源：继承 synalysis_crew 的 PLAYBOOK（2026-08-03），并按本项目适配。

## 1. 网络与代理（Windows 本机）
| 问题 | 现象 | 原因 | 解决 |
|---|---|---|---|
| github.com 直连不通 | git push/curl 443 超时 | 本机网络对 github.com 不稳定 | 走代理：`git -c http.proxy=http://127.0.0.1:7890 push`（或写入仓库级 config）；curl 加 `-x http://127.0.0.1:7890` |
| api.github.com 可用 | 与 github.com 不通但 api 通 | 网络分层 | GitHub API 操作（issue/文件）可用 urllib/curl 直连 api.github.com |
| 代理客户端未启动 | 7890 端口无监听，直连/代理全部 000 | Clash 等未运行 | **请用户启动代理客户端**；启动后 `curl -x http://127.0.0.1:7890 https://api.github.com` 验证 |
| 下载 GitHub releases 失败 | curl 直连超时 | 同上 | 加 `-x http://127.0.0.1:7890` 重试 |
| **沙箱注入假代理**（本项目实测） | curl/PowerShell/git 全部连不上，`$env:HTTP_PROXY=http://127.0.0.1:9` | Codex 沙箱用假代理隔离网络 | 显式覆盖：git 用 `-c http.proxy=http://127.0.0.1:7890`；Python 用 `urllib.request.ProxyHandler({'https':'http://127.0.0.1:7890'})`；命令开头 `$env:HTTP_PROXY='http://127.0.0.1:7890'` |

## 2. GitHub Issue / 仓库操作
| 问题 | 现象 | 原因 | 解决 |
|---|---|---|---|
| 插件建 issue 404 | `Resource not found` | GitHub 插件 App 看不到你的私有仓库 | 仓库改 public，或把插件账号加为协作者（用户手动） |
| 插件建 issue 403 | `Resource not accessible by integration` | App 未安装/未授权到该账号仓库 | 插件无解 → 改用**用户的 fine-grained PAT**（仓库 + Issues 读写）走 api.github.com；网络不稳要加重试（3 次 + 退避） |
| 创建仓库 | 插件无建仓工具 | 能力缺失 | 用户手动创建，或用 PAT `POST /user/repos` 创建（private/public 均可） |
| 远端仓库未创建 | GET /repos 404 | 用户消息发错线程未真正建仓 | 用 PAT 创建或用户手动创建；创建后 `git remote add origin` + push |

## 3. 沙箱与提权（workspace-write 环境）
| 问题 | 现象 | 原因 | 解决 |
|---|---|---|---|
| git 写操作被拒 | `index.lock: Permission denied` / config 锁失败 | `.git` 目录在沙箱只读 | git 命令（add/commit/push）用 `require_escalated`，可申请 prefix `["git"]` |
| 前端构建失败 | esbuild `EPERM` spawn 子进程 | 沙箱拦截 | `npm run build` 用 `require_escalated` |
| pip/npm install 失败 | 网络/权限 | 沙箱网络受限 | 提权安装；依赖尽量一次性装好，agent 禁止自行安装 |
| 后台服务 | 需要常驻进程 | — | `Start-Process -WindowStyle Hidden`；健康检查用 `Invoke-RestMethod http://127.0.0.1:PORT/api/health` |
| pytest tmp_path 报错 | WinError 5 拒绝访问 | Windows 沙箱下 pytest 0o700 目录 ACL 问题 | 测试模块覆盖 tmp_path fixture，用 0o777 显式创建并自清理 |
| PowerShell 管道中文乱码 | python 收到 `??` | PS 按 GBK 编码管道 | 脚本用纯 ASCII（中文用 `\uXXXX`），或 `$env:PYTHONIOENCODING='utf-8'` |

## 4. 业务逻辑纪律（血泪教训）
1. **数据字典先行**：每个字段写死定义（公式/来源/边界/单位/None 语义），见 `docs/requirements.md` §六；代码与前端严格对照。
2. **口径先和用户确认再实现**（synalysis 教训）：收益率 TWR、胜率按完整交易闭环、翻倍禁止低点口径、腰斩递进计数、亏损榜升序、最大回撤正值。
3. **前端↔后端契约写进文档**：接口返回结构（含嵌套字段）完整记录，前端 normalize 层对照实现——字段不匹配会导致整页失效。
4. 比率一律小数存储（0.5=50%），前端负责 ×100；`None` 显示「—」不显示 0。
5. 长任务必须：后台 + 进度轮询 + fetch 超时（30s）+ 中文错误；控件支持重选同一文件。
6. 降级链：数据源失败 / LLM 无 Key / 历史缺失 → 明确降级并标注，绝不静默错报。
7. 测试：每个指标至少一个**手算抽查用例**；断言容差不可过大；真实数据冒烟。
8. 免责声明程序级强制追加；红涨绿跌（正红负绿）；全中文展示。

## 5. Agent 协作
- spawn_agent 并发 ≤5；每个 agent 给**文件所有权表**（只写自己名下文件）与只读契约；禁止 git/真实数据入库/改他人文件。
- 完成通知会自动推送；可用 wait_agent 主动查询状态。
- **空闲 agent 会被系统回收**（"保留待命"不等于永存）：重要任务开始前先确认负责人还在，不在就新开并把上下文写全。
- 模块负责人登记表在 `docs/PROGRESS.md`。
- 测试隔离：`STOCK_REVIEW_DATA_DIR` 指向 `.tmp/`，不要写仓库 `data/`。

## 6. 部署（可选 Cloudflare Tunnel）
- cloudflared 下载：GitHub releases（走代理 127.0.0.1:7890），放 `.tmp\cloudflared.exe`。
- 快速隧道：`cloudflared tunnel --url http://127.0.0.1:8501 --no-autoupdate`；一键脚本 `scripts\start_tunnel.ps1`。
- 快速隧道地址每次重启变化；电脑关机服务停止；公网 URL 不要公开分享。

## 7. 新问题记录模板
```markdown
## [YYYY-MM-DD] 问题一句话
- 现象：……
- 原因：……
- 解决：……
- 备注/复现：……
```

## 8. 本项目已记录问题
### [2026-08-07] 401 反复出现（已更新 .env 仍报 Invalid API key）——override 根治
- 现象：用户更新 .env 的 DEEPSEEK_API_KEY 后聊天仍报 401；新进程直测 LLM 正常，
  但运行中的服务（含守护自动重启的实例）持续 401。
- 原因：**服务进程继承了含过期 DEEPSEEK_API_KEY 的环境变量**（用户 shell/Codex
  环境残留），config `load_dotenv(override=False)` 不覆盖已存在的环境变量 →
  .env 新值永远不生效；守护脚本（start_backend.ps1 常驻）重启的 uvicorn 同样继承
  守护进程的旧 env。
- 解决（根治）：config.py 改 `load_dotenv(override=True)`——**.env 为凭据唯一真源**，
  任何入口（守护/手动/测试）都强制用 .env 值；start_backend.ps1 启动前
  Remove-Item Env:DEEPSEEK_API_KEY 等（双保险，BOM+CRLF 已保持）；
  test_data_config.test_env_values_loaded 同步新契约（env 不再覆盖 .env）。
- 备注：判断口诀——**新进程直测 LLM OK 但服务 401 = 进程级 env 污染**，
  override=True 后该问题从机制上消除；本次实测守护自动重启实例聊天正常（无 401）。

### [2026-08-07] 聊天报「401 Invalid API key」——服务进程仍用旧 Key（.env 已更新未重启）
- 现象：聊天/复盘 parse 报 `Error code: 401 - Invalid API key`，多次出现。
- 原因：用户已更新 `.env` 的 DEEPSEEK_API_KEY，但**运行中的服务进程在启动时已加载
  旧 Key**（config.load_dotenv override=False：环境变量残留旧值或进程未重启）；
  直接新起 Python 进程测 LLM 正常 → 判断为「进程级旧配置」。
- 解决：**重启服务**（杀 8502 监听进程 → 重新启动，加载新 .env）；
  启动时显式移除 shell 残留的 DEEPSEEK_API_KEY/DEEPSEEK_BASE_URL（env.pop）避免
  override=False 被旧环境变量占位。
- 加固：chat.py parse 失败对 401 输出中文指引（「LLM 凭据无效或已过期，请在 .env
  更新 DEEPSEEK_API_KEY 后重启服务」），不再透传英文原文。
- 排查口诀：**先新进程直测 LLM（.venv python）→ OK 则问题在运行中服务的进程配置，
  重启服务即可**；服务 health 正常不代表 LLM 配置正确（health 不调 LLM）。

### [2026-08-06] 「服务都启动不了」——.ps1 编码被 apply_patch 破坏（PS 5.1 按 ANSI 读）
- 现象：双击 启动服务.bat 后窗口一闪而过/无服务，或「启动不了」；start_backend.ps1
  报「源文本中包含无法识别的标记」「字符串缺少终止符」等语法错误（乱码）。
- 原因：**apply_patch 重写文件为 UTF-8 无 BOM**——Windows PowerShell 5.1 对无 BOM
  .ps1 按系统 ANSI（GBK）解码 → 中文乱码 → 引号/括号错乱 → 解析失败。
- 解决：.ps1 必须 **UTF-8 带 BOM + CRLF**（PS 5.1 识别 BOM 后按 UTF-8 解码）；
  用 Python（glob 定位避免管道中文问题）重写：`b"\xef\xbb\xbf" + text.encode("utf-8")`；
  本次修复 start_backend.ps1 / start_tunnel.ps1；后续**凡 apply_patch 改过 .ps1，
  交付前必须重加 BOM 并 `powershell -File` 冒烟**。
- 备注：.bat 保持无 BOM（首行 BOM 会破坏 @echo off）+ CRLF + chcp 65001（cmd 流式
  解码 OK）；PLAYBOOK 2026-08-04「bat 必须 CRLF」同源问题。

### [2026-08-06] Start-Process 报「已添加项。字典中的关键字:Path/PATH」（多环境注入）
- 现象：start_backend.ps1 的 Start-Process 抛 ArgumentException（Path/PATH 两个
  大小写变体键）→ $proc 为 Null → WaitForExit 报错 → uvicorn 没拉起。
- 原因：部分环境（沙箱/代理注入）进程环境变量表同时存在 Path 与 PATH。
- 解决：ps1 在 Start-Process 前合并去重 PATH 键（Get-Item 遍历 → Unique → 重建），
  并对 $proc 空值保护（5 秒后重试，不崩循环）；实测沙箱与用户环境均可拉起。

### [2026-08-06] Langfuse 捕获不到复盘/聊天 LLM trace（三个根因连环）
- 现象：Langfuse 面板只有 08-05 前的 ChatOpenAI trace，之后复盘/聊天 LLM 调用全无；
  日志提示 "No Langfuse client with public key ... has been initialized. Skipping tracing..."
- 原因（三层）：
  1. **get_client 单例未注册**：langfuse 4.x 的 `CallbackHandler(public_key=...)` 内部走
     `get_client(public_key)`，只查找**已注册实例**——从未用同 public_key 显式构造
     `Langfuse(...)` 时返回 disabled client（不追踪）。修复：先 `Langfuse(public_key,
     secret_key, host)` 注册再建 handler；
  2. **OTLP 导出读环境/系统代理**：exporter 每次请求读 HTTP_PROXY/ALL_PROXY（含
     大小写变体），沙箱假代理 9 / 用户 7890 均导致静默失败。修复：构造期间清理全部
     代理变量 + **持久化 NO_PROXY=cloud.langfuse.com**（异步导出线程仍绕过代理）；
  3. **未 flush 不导出**：`start_observation` 创建的 span 不 end/flush 时异步导出
     不可见（手动 `Langfuse(...)` + flush 可导出）。修复：trace_review/score 写回后
     调 `client.flush()`（4.x 无 .trace/.score 方法，用 start_observation/create_score）。
- 解决：config.langfuse_callback 与 quality._get_langfuse_client 统一以上三点；
  实测：服务复盘 13 条 ChatOpenAI trace + review_quality score 均出现在面板。
- 备注：手动验证链路用 `Langfuse(...) + start_observation + end + flush`；
  排查顺序：先看面板是否有 ChatOpenAI trace（handler 是否生效），再看 review:done
  trace 与 scores。

### [2026-08-06] 复盘指数只显示上证/创业板/科创50（用户口径）+ market_data 渲染截断
- 现象：报告指数表格只有上证+深证（LLM 未写创业板/科创50）；另「| 指数 | 收盘 |...」
  表格带「+0.57%（0.0057）」双格式括号小数，成交量/成交额缺失时被 LLM 自行标注
  「量级异常」。
- 原因：① 数据层 INDEX_CODES 含深证成指，LLM 可选用；② market_data 的 index 块
  `_block_md` 默认 cap=40 行，3 指数 × 多字段超过后被截断 → 科创50 等显示「无数据」；
  ③ 报告 prompt 未约束涨跌幅格式。
- 解决：① INDEX_CODES 去掉 shenzhen（数据/报告/快照统一三指数）；② index 块渲染
  cap 提升到 160；③ build_report_prompt 冻结指数表格规则（只列三指数、涨跌幅
  +0.57% 格式、缺失「无数据」）。

### [2026-08-06] 聊天分析师「近5日日K缺失」——stock_info 上下文截断
- 现象：分析师聊天说「近5日日K和板块归属缺失」，但个股详情接口能拿到日K。
- 原因：聊天 stock_info 取数正常（fetch_stock_info 返回 recent5），但上下文组装
  `data[:1600]` 截断——recent5 位于 JSON 尾部被切掉。
- 解决：_build_context 对 stock_info 特化——compact 提取 code/close/ma5/recent5
  等关键字段再截断（recent5 完整保留）；DATA_KEY_WHITELIST 描述注明含近5日日K。

### [2026-08-06] 市场天气评分天天 100 分 → 加分项减半严格化
- 现象：强市场景 score 轻易 clamp 100（50+15+20+10+10+10=115），无法区分强弱。
- 解决：五档加分幅度减半 + 涨停-跌停差上限 ±20→±12（50+8+12+6+6+6=88 上限）；
  实测 2026-08-06 强市 79 分（晴/偏暖），弱市 10 分（冰点），差异明显；
  同步更新 requirements-r10.md §二 与 test_data_market_extra.py 手算样例。

### [2026-08-06] 前端 intent effect 依赖内联回调 → 创建会话竞态卡「正在加载会话消息…」
- 现象：首页聊天卡片点「进入聊天」后，ChatView 永久停在「正在加载会话消息…」，
  无输入框（桌面/手机均可能触发）；接口层 createChatSession 实际已 200 成功。
- 原因：ChatView 的 intent effect 依赖数组含 `onIntentConsumed/onSessionCreated`，
  而 App.jsx 传 `onIntentConsumed={() => setChatIntent(null)}` 是**内联箭头函数**
  （每次渲染新引用）——创建会话的 await 期间一次无关渲染（如会话列表刷新）就会
  触发 effect cleanup（cancelled=true），create 完成后 `if (cancelled) return` 跳过
  onSessionCreated → activeSessionId 未设置 → UI 卡 loading（会话已落盘，下次刷新可见）。
- 解决：effect 只依赖 `[intent]`，回调用 `useRef` latest-ref 模式读取
  （ChatView.jsx `intentCallbacksRef`）；Playwright 复测 0.8s 出输入框。
- 备注/复现：R10 加「今日数据/观察列表」后 App 渲染更频繁，竞态触发率大增；
  此类「回调引用稳定性」问题排查法：前端加 console.log 看 effect 的 run/cleanup 序列，
  或监听 network 看 create 200 后是否发 get/{id}。

### [2026-08-06] quality.verify_numbers 对长报告/表格误报 → rubric 置信度自检
- 现象：复盘质量分 numbers=1/5，mismatch 238→209 条；人工审查报告业务逻辑正确，
  核验器把「0.018616」（小数位）、「# 2026-08-05 A股收盘复盘报告」标题里的
  「2026」与「收盘」（别名「收盘」匹配「收盘复盘」）、表格数字（家数/MA 值）全部
  错误归因到首个引用实体的字段。
- 解决：① `_CODE_RE` 排除 `0.xxxxxx`/`4,806,544` 数字片段（`(?<![\d.,])(\d{6})(?![\d,])`）；
  ② 实体提取时比率字段归一（涨跌幅 10.01→0.1001，市盈率/市净率排除）；
  ③ **rubric 置信度自检**：幻觉代码≥3 且数值不一致极少→按代码缺失重罚；
  数值不一致≥20 且 verified<30% checked→核验器置信度不足给中性 3 分并注明，
  避免启发式误报把质量分打成 1 分。
- 备注：数字核验是启发式，报告/表格场景误报率天然高；评分必须带置信度信号，
  人工审查仍是最终手段（R10 实测报告数字与快照一致，仅 1 处成交额笔误由提示词
  单位铁律约束，见 prompts.py build_report_prompt）。

### [2026-08-06] 降级断言测试环境耦合（直连可用）→ 补 mock + regression 假代理
- 现象：提权/真实网络下全量 pytest 12 个「数据源全失败→降级」用例失败
  （沙箱假代理下全绿）；根因是数据层「代理失败自动直连」兜底让降级不触发，
  而测试 mock 未覆盖全部链上源（如 `_sentiment_from_realtime`、`_zt_pool_from_ths`、
  东财/腾讯日K）。
- 解决：① 补全测试 mock（离线确定性，不依赖网络状态）；② regression.py 的
  pytest 子进程显式注入 `HTTP_PROXY=http://127.0.0.1:9`（假代理），与沙箱行为一致。
- 备注：新增数据链（market_extra 等）的降级测试同样必须 mock 完整降级链各级源，
  并断言「绝不抛异常」。

### [2026-08-06] 复盘「一直降级 + 直接把提示词显示出来」（三个根因）
- 现象：用户反馈复盘结果直接显示角色提示词原文（如「你是阿狼（狼大），NGA论坛……」），
  且整场一直处于降级模式。
- 原因：
  1. **规则引擎把 skill.prompt 原文写进分析**：`graph._rule_analyst` 用
     `persona = skill.prompt[:120]` 填充「今日判断」章节——LLM 不可用时，报告直接
     泄漏角色提示词（修复：改展示 `skill.role` 人设一句话，禁止输出 prompt 原文）；
  2. **langfuse 回调故障拖垮 LLM**：`graph._callbacks()` 每次 LLM 调用新建
     `CallbackHandler`，Langfuse 网络/配置故障时 Langchain 会把回调异常传播给
     invoke → 整个 LLM 调用失败 → 全部走规则引擎降级（修复：config 进程级单例 +
     `_invoke_llm` 先带回调调用、回调失败去掉回调重试一次，监控故障与 LLM 本体隔离）；
  3. **无 Key 时 config 模块导入崩溃**：`make_llm()` 在 `DEEPSEEK_API_KEY` 为空时
     `ChatOpenAI(api_key="")` 抛 OpenAIError → config 导入失败 → 任何依赖 config 的
     模块（含 /api/health）直接报错（修复：空 Key 返回 None，引擎按 `_llm_ready()=False`
     走规则引擎，符合铁律 7 的降级链）。
- 解决：见上；测试新增 health langfuse 契约断言；全量 288 passed。
- 备注/复现：系统 Python（3.13）无 langfuse 包属预期（服务用 .venv）；数据源全线
  降级 `ProxyError 127.0.0.1:9` 是沙箱假代理环境所致（PLAYBOOK §1），用户环境
  （启动脚本设 7890 / 直连兜底）不受影响。

### [2026-08-06] Langfuse 4.x CallbackHandler 签名变化（只收 public_key）
- 现象：`CallbackHandler(public_key=..., secret_key=..., host=...)` 抛
  `TypeError: unexpected keyword argument 'secret_key'`，Langfuse 监控静默关闭
  （health 的 `langfuse.callback_ok=false`）。
- 原因：langfuse 4.14 的 `LangchainCallbackHandler.__init__(*, public_key, trace_context)`
  只接受 public_key；secret_key/host 由 SDK 内部 `get_client()` 从环境变量
  （LANGFUSE_SECRET_KEY / LANGFUSE_BASE_URL）读取。
- 解决：`CallbackHandler(public_key=config.LANGFUSE_PUBLIC_KEY)`，其余靠
  config.load_dotenv 注入的 env；用 `inspect.signature` 核对后再传参。
- 备注/复现：`.venv` 实测；验证命令 `python -c "from stock_review_crew import config; print(config.langfuse_status())"`。

### [2026-08-06] 沙箱 PowerShell Start-Process 报「Item has already been added. Key 'Path'/'PATH'」
- 现象：`Start-Process` 启动后台服务报 `ArgumentException: Item has already been
  added. Key in dictionary: 'Path' Key being added: 'PATH'`，无法拉起 uvicorn。
- 原因：沙箱/代理注入导致进程环境变量表同时存在 `Path` 与 `PATH` 两个大小写变体键，
  Start-Process 复制环境变量到子进程时字典冲突；`Get-Item Env:Path` 同样报错。
- 解决：改用 `Start-Job -ScriptBlock { & .venv\Scripts\python.exe -m uvicorn ... }`
  启动后台服务（不经过 Start-Process 的参数绑定）；日志重定向到 `.tmp\logs\*.job.log`。
- 备注/复现：用户本机正常环境无此问题；仅沙箱内验证/提权环境需要。

### [2026-08-03] GitHub 建 issue 403（本项目）
- 现象：GitHub 连接器 create_issue 全部 403 `Resource not accessible by integration`。
- 原因：GitHub App 未授权到新仓库 xiaoLin12130/stock_crew。
- 解决：用户已提供 fine-grained PAT（仓库 + Issues 读写）；待网络可用后走
  `POST https://api.github.com/repos/xiaoLin12130/stock_crew/issues` 补建（载荷在 .tmp/gh/）。

### [2026-08-03] 远端仓库可能未创建
- 现象：用户消息「仓库已经建好」发错到 synalysis 线程且被「无视」；本地 git 无 remote。
- 解决：网络恢复后先用 PAT `GET /repos/xiaoLin12130/stock_crew` 验证；404 则
  `POST /user/repos` 创建（默认 private）或请用户手动创建。

### [2026-08-03] 代理客户端未启动
- 现象：127.0.0.1:7890 无监听，直连与代理全部 000。
- 解决：请用户启动 Clash 等代理客户端；系统代理已指向 127.0.0.1:7890。

### [2026-08-07] 降级原因透传英文异常原文（time data 'i' / 502 Bad Gateway for url）→ 中文化 + 冒烟补盲区
- 现象：首页「数据缺失/降级」横幅出现「东财板块: 东财HTTP请求失败：502 Server Error: Bad Gateway for
  url: http://80.push2his...」「通达信指数: time data 'i' does not match format '%Y-%m-%d'」。
- 原因：① BK 板块代码（BK1158/BK1641）降级链错误调用 akshare 通达信日线
  （stock_zh_index_daily_tx 对无效板块代码解析日期抛 ValueError）；② 兜底链
  `errors.append(f"...: {exc}")` 把英文异常原文直接拼进 degraded_reason 直达前端。
- 解决：BK 系删除 akshare 通达信兜底（单源中文标注）；新增 `_zh_source_error`（DataSourceError
  透传/中文消息保留/含连续英文归一为「XX数据源异常」）；_em_http_json 只报
  「东财数据源请求失败（HTTP {code}）」；regression.py 新增 2 项断言（API degraded_reason 与
  浏览器 body 无英文特征：time data/Bad Gateway/Traceback/url:）。
- 备注/复现：验证修复必须确认 8502 实例加载新代码（curl /api/data/extra-indices 看降级原因是否
  中文）；旧实例未重启会 10048 抢占端口导致新代码实例绑定失败——一律用
  `scripts/start_backend.ps1` 守护脚本启动（自动清理旧实例）。

### [2026-08-03] pytest 全绿但进程退出挂起（约 2 分钟）
- 现象：190 项测试全过，解释器在退出阶段死锁，主线程卡 `threading._shutdown`。
- 原因：akshare 依赖 py_mini_racer（内嵌 V8 JS 引擎），其后台 asyncio 事件循环线程
  在 Windows 上用 IOCP 完成端口阻塞，与解释器退出形成死锁（构造 MiniRacer 本身
  就可能永久阻塞）；posthog 遥测线程的 atexit join 是次要因素。
- 解决：在 `config.py` 与 `tests/conftest.py` 导入 akshare 之前把
  `py_mini_racer.MiniRacer` 替换为抛错 stub（本项目数据链路不需要 JS 引擎），
  并在 conftest 里 `posthog.disabled = True`；数据层遇相关接口自动降级标注。

### [2026-08-03] PowerShell 空字符串赋环境变量会删除变量
- 现象：`$env:DEEPSEEK_API_KEY=''` 后配置层仍读到 .env 的真实 Key（测试走真实 LLM）。
- 原因：PowerShell 空串赋值 = 删除该环境变量，而非置空。
- 解决：在 Python 内 `os.environ['DEEPSEEK_API_KEY']=''` 或用 `Remove-Item Env:`。

### [2026-08-04] 同花顺页面 JS 签名（v Cookie）与浏览器爬虫
- 现象：q.10jqka.com.cn 行业板块页无签名请求返回 401；akshare 依赖 py_mini_racer
  执行 ths.js（Windows IOCP 退出死锁，已禁用）。
- 解决（**浏览器解析 HTML 方案**）：Playwright（.venv 已装）驱动系统 Edge 无头，
  打开页面让 JS 自动生成 v Cookie；再**页面内 fetch**（同源 + `X-Requested-With:
  XMLHttpRequest` 头）取 ajax HTML 表格（独立 ctx.request 会 403，必须页面内 fetch）。
  实现：`src/stock_review_crew/tools/browser_crawler.py`，已接入板块降级链
  （同花顺 → 同花顺(浏览器) → 东财 → 缓存）。
- 备注：playwright 仅装在 .venv（系统 Python 未装，模块内 available() 判定降级）；
  浏览器启动约 2-4s，仅作备用源；沙箱内运行需提权。

### [2026-08-04] bat 启动脚本必须 CRLF 换行
- 现象：apply_patch 生成的 .bat（LF 换行）在 cmd 下解析错乱
  （echo 被拆行、中文行报「不是内部或外部命令」）。
- 解决：批处理文件写完后统一转 CRLF（PowerShell/脚本处理），并实测 `cmd /c` 运行。

### [2026-08-04] dist 不提交 git → 部署可能用陈旧前端
- 现象：源码/提交均有「股票名称搜索」，但线上 bundle 缺失（grep 不到
  api/data/search），用户功能「没做到」。
- 原因：frontend/dist 被 .gitignore 排除，某次前端改动后没有重新 `npm run build`，
  静态托管一直服务旧产物。
- 解决：前端改动后必须重新 build，并用 node 校验 bundle 关键字符串
  （如 `api/data/search`）再部署；PLAYBOOK 新增检查项。

### [2026-08-04] 东财 push2 全系 502（代理节点问题）
- 现象：clist/stock/get 等 push2 接口持续 502，个股行情与板块同时挂。
- 解决：fetch_stock_quote 增加**腾讯 qt.gtimg.cn 备用**（实测可用）；
  板块已有 腾讯行业/浏览器 兜底；指数已有 东财 push2his/腾讯日K 兜底。

### [2026-08-04] pytest 缓存/临时目录 ACL 异常（沙箱）
- 现象：仓库根目录出现 `pip-build-tracker-*` / `pip-download-*` / `pytest-cache-files-*`
  目录后，pytest 收集阶段报 `PermissionError: [WinError 5]`，全量测试无法运行；
  `git status` 同步报「could not open directory」警告。
- 原因：提权/沙箱进程创建的临时目录 ACL 与当前用户不匹配（Windows 0o700 类问题）；
  pytest 缓存写回退目录被污染后互相阻塞。
- 解决：删除根目录这些临时目录（需提权 Remove-Item，先确认路径在仓库根内）；
  **测试命令统一加 `-p no:cacheprovider`**，避免再生成 pytest 缓存临时目录。
- 备注/复现：沙箱内 `python -m pytest -q`（不带 `-p no:cacheprovider`）必现缓存写失败；
  全量/验收命令统一用 `python -m pytest -p no:cacheprovider`。

### [2026-08-04] 旧服务实例未重启 → 用户仍看到旧行为（多端口多实例并存）
- 现象：R8 聊天 6 步流水线改造完成后，用户聊天仍返回**旧格式回复**（assistant 消息无
  `parse/needs/stage` 字段，分析师定性回答 +「具体数据暂缺，结论为定性参考」）；
  前端另偶发「无法连接后端服务（网络错误）」。
- 原因：本机同时运行两个后端实例——8501 为新代码（16:46 启动），8502 为旧代码
  （15:53 启动，早于 chat.py 16:32 改造）；用户浏览器打开 8502，请求全被旧服务处理。
  `frontend/dist` 静态文件两服务共用，前端是新版、后端是旧版，行为不一致很难察觉。
- 解决：
  1. **停旧实例再以新代码重启同一端口**：`netstat -ano | findstr :8502` 定位 PID →
     `Stop-Process` → `.venv\Scripts\python.exe -m uvicorn backend.main:app --port 8502`；
  2. `/api/health` 只能证明进程活着，必须用**行为探针**验证版本：调一次
     `POST /api/chat/sessions/{id}/messages/stream`，新引擎必须出现 `stage` 事件且
     assistant 消息含 `parse/needs/stage` 字段（旧引擎没有）；
  3. 「网络错误」先查 `netstat` 监听 + `GET /api/health` + 代理 127.0.0.1:7890 是否在，
     多实例并存时最容易误判为代码问题。
- 备注/复现：改动后端代码后，核对监听端口的进程启动时间是否 ≥ 代码 mtime；
  启动脚本（启动服务.bat / scripts/start_tunnel.ps1）应**先杀同端口旧实例再启动**，
  杜绝多实例并存；本问题实测复现于 2026-08-04（8502 旧实例 15:53 启动）。

### [2026-08-04] 隧道 HTTP 502 / uvicorn 进程崩溃（pyarrow arrow.dll 段错误）
- 现象：通过 Cloudflare 隧道访问出现 HTTP 502；`.tmp/tunnel.err` 持续报
  `Unable to reach the origin service ... connection refused`；本地 8502 端口无监听。
- 原因：uvicorn 单进程被 **pyarrow（`.venv\Lib\site-packages\pyarrow\arrow.dll`）原生
  访问冲突 0xc0000005** 打崩（Windows 事件查看器 Application Error Id 1000，时间戳与
  故障一致）——akshare 数据下载/读取走 pyarrow C 扩展时偶发段错误，整个服务进程退出。
- 解决：
  1. 隧道 502 先看 `.tmp/tunnel.err`：`connection refused` = 源站已死，不是隧道问题；
  2. `netstat -ano | findstr :8502` 无监听 → 进程崩溃；用事件日志 Id 1000 确认崩溃模块；
  3. 重启：`powershell -File scripts\start_backend.ps1`（守护脚本，崩溃 3 秒后自动拉起），
     或双击 启动服务.bat（已改为调守护脚本）。
- 备注/复现：pyarrow 段错误偶发且发生在数据下载中，Python 层无法 try/except 捕获；
  已用守护脚本缓解（进程级自愈）；根治方向是让 akshare 避开 pyarrow 读取路径
  （如 pandas read_csv 引擎改 C）。守护日志：`.tmp/logs/uvicorn_watch.*.log`。

### [2026-08-04] LLM 端点不可达 → 聊天「问题解析失败：Connection error」
- 现象：聊天/复盘一进 parse 步骤就报 `问题解析失败：Connection error`（error 事件 → 终止）；
  数据源与本地服务均正常。
- 原因：DEEPSEEK_BASE_URL 指向的 opencode.ai 经代理 127.0.0.1:7890 出现
  `SSL: UNEXPECTED_EOF_WHILE_READING`；直连被 403（需代理）；属代理节点/端点瞬态故障。
- 解决：**无需改代码**——换 Clash 节点或等待恢复后重试；恢复判定：
  `curl -x http://127.0.0.1:7890 <DEEPSEEK_BASE_URL>` 返回非 5xx/非 SSL EOF。
- 备注/复现：2026-08-04 17:38-17:42 探针正常，17:53 起持续 5 分钟以上不可达后自行关注；
  此类故障下聊天按 Q5 终止并如实说明，是设计内行为，不要误判为代码回归。

### [2026-08-04] 通达信主站认证升级 → 老协议库拿哑数据；解法=可用主站清单（tdxpy）
- 现象：pytdx 1.72 / tdxpy 0.2.7 直连通达信主站，部分主站返回**哑数据**——
  行情快照空、分时 price=0.01×240 条、日K解析报 struct.error；误判为库失效。
- 原因：通达信新版主站对未认证客户端返回占位数据（部分主站仍兼容老协议）。
- 解决：
  1. **扫描主站**：对 167 台主站（connect.cfg 38 + pytdx 内置 + tdxpy 内置）并行探测，
     实测 **22 台返回真实数据**（清单固化在 `tools/tdx_remote.py::_SERVERS`）；
  2. 用 **tdxpy**（2024 维护中）替代 pytdx（2020 停更）；已 `uv add tdxpy` 入项目依赖；
  3. 接入降级链：个股实时行情（东财→腾讯→通达信协议）、个股日K（Tushare→东财→协议→本地）、
     分钟线（东财→腾讯→协议→本地）、指数日K（akshare→东财→腾讯→协议→本地）。
- 备注/复现：协议源实测能力——实时五档✅ 日K✅ 指数K（含涨跌家数）✅
  1/5/15/30/60分钟K（约 6 个交易日历史）✅；**分时曲线/历史分时/逐笔 ❌**
  （新版主站协议格式变更，tdxpy/pytdx 均解析失败，竞价段暂无法从该路线获取）。
  指数 market 判定：000 系→沪(1)、399 系→深(0)，不能按个股代码前缀判。
  系统 Python（测试）未装 tdxpy 时 `available()=False`，接入点自动降级跳过。

### [2026-08-04] ETF（5xx）行情缺失：市场前缀漏 5xx + 通达信 ETF 价格 ×10
- 现象：聊天查 ETF（如 588170）报「腾讯行情字段不足」「分时数据缺失」，整轮终止。
- 原因：
  1. `_tx_symbol/_secid/_em_secid` 市场前缀只认 6/9（股票），5xx 沪市基金/ETF 被误判为
     sz/0. → 腾讯返回字段不足、东财 stock/get 返回空 JSON；
  2. 通达信协议对 ETF 快照价格按 ×10 返回（0.865→8.65），K线价格则是真实元值。
- 解决：
  1. 三处前缀映射统一加 5→sh/1.（realtime._tx_symbol/_secid、stock_data._tx_symbol/_em_secid）；
  2. `tdx_remote.fetch_quote` 对 5xx 价格类字段 ÷10（K线不缩放，实测确认）；
  3. `realtime.fetch_stock_quote` 错误消息组装改为 `tx_note` 无条件初始化（避免 UnboundLocalError）。
- 备注/复现：东财 stock/get 对 ETF 仍返回空 JSON（实测），ETF 行情由 腾讯/通达信协议 覆盖；
  588170 修复后：腾讯实时 0.865/+4.34%/五档、分时 242 根、TDX 日K/1分钟K 均正常。

### [2026-08-04] uv sync 会清掉 venv 手工安装的依赖（playwright 被移除）
- 现象：浏览器兜底源（browser_crawler）静默失效 `available()=False`；无头冒烟
  `ModuleNotFoundError: No module named 'playwright'`。
- 原因：`uv sync` 按 uv.lock **修剪 venv 中未登记的手工安装包**；playwright 此前是
  venv 直装、未进 pyproject，在 tdxpy 接入的 uv sync 中被移除。
- 解决：运行期需要的包必须 `uv add` 入 pyproject/uv.lock（playwright 已加入；
  浏览器用系统 Edge channel，无需下载浏览器内核）；禁止「只装不进锁」。
- 备注/复现：2026-08-04 R9 无头冒烟时发现；同类还有 tdxpy（已入依赖）。
  附带教训：**前端改动验收必须走真实浏览器点击冒烟**（Playwright 无头），
  仅测后端接口会漏掉 props 接线类错误（如 R9 首页「查看详情」未传 onOpenStockDetail）。

### [2026-08-04] 一键回测机制（scripts/regression.py）
- 现象：修复一个 bug 引入新 bug（R9 首页详情漏传 prop、uv sync 清掉 playwright），
  后端 pytest 全绿但浏览器点击路径坏。
- 原因：变更后只跑后端单测，未覆盖前端构建/浏览器点击/依赖驻留。
- 解决：`scripts/regression.py` 一键全量：全量 pytest + npm build + API 契约抽查 +
  聊天链路（LLM 不可用时校验「设计内中文终止」而非报错）+ Playwright 桌面/手机点击冒烟；
  AGENTS 铁律 8：改动后必跑、全绿方可交付。
- 备注/复现：运行需 .venv Python 且 8501/8502 服务在跑（esbuild/浏览器需提权）；
  聊天链路对 LLM 端点（opencode.ai，需代理）不可用不判失败，只校验降级说明符合契约。

### [2026-08-11] 数据根路径 parents[4] 解析到仓库外（H:\data）→ 统一 parents[3]
- 现象：R14 新增 accounts/quotes.py、replay.py 复用了 storage.py 的
  `Path(__file__).resolve().parents[4] / "data"` 写法，解析到 **H:\data**（仓库外）——
  与 reviews/plan 的 parents[3]（仓库 data/）分裂；CLI 被迫用 STOCK_REVIEW_DATA_DIR
  打补丁，路径语义混乱。
- 原因：storage.py 是 R12 遗留（当初为「真实交易数据不进仓库」刻意放 H:\data），
  新模块照抄导致不一致；.gitignore 已忽略 data/，仓库内 data/ 实际安全。
- 解决：三个模块统一 `parents[3] / "data"`（quotes.py:40 / replay.py:63 /
  storage.py:18），删除 CLI 变通；reviews_root/accounts_root/quotes_root/
  replay reviews_root 全部解析到 H:\stock_review_crew\data\*。
- 备注/复现：写新模块时先确认「数据根回退路径与既有 reviews 存储一致」；
  遗留 H:\data\accounts 为历史产物保留不迁移（R14 全量重放会重建仓库内账户）。

### [2026-08-11] normalizeStockDetail 丢弃 ma 对象 → 详情页 JS 崩溃
- 现象：浏览器冒烟「个股详情」45s 超时；页面错误边界显示
  「Cannot read properties of undefined (reading 'ma5')」。
- 原因：后端与 mock 均返回 `ma:{ma5,ma10,ma20}` 对象，但 normalize.js 的
  normalizeStockDetail 只映射顶层 ma5/ma10/ma20 并**丢掉 ma**；
  StockDetail.jsx `detail.ma.ma5` → detail.ma 为 undefined → 崩溃。
- 解决：normalize 补 `ma: {ma5, ma10, ma20}`（`(x.ma && x.ma.ma5) ?? x.ma5`
  兼容两种形状），同时保留顶层字段。
- 备注/复现：该函数为未提交工作区（R10-R13 遗留）新增，从未被浏览器点击验证过——
  **前端 normalize 改动必须跑 regression 浏览器详情项**；「另一线程」留下的半成品
  是这类隐性破坏的高发区，集成闸门阶段务必逐个还原对照。

### [2026-08-11] PowerShell heredoc 传中文给 python 会被编码破坏（复现脚本假阴性）
- 现象：用 `@'...'@ | python -` 内联传 Playwright 复现脚本，脚本内中文选择器
  （`text=今日数据`）被 GBK 编码破坏成 `????`，`wait_for_selector` 永远超时，
  误导排查方向（以为是页面没渲染，实际渲染正常）。
- 原因：sandbox PowerShell 把 heredoc 内容按系统 ANSI 传给 python stdin；
  早期脚本能显示中文是因 print 输出走了 reconfigure，但**字符串字面量在传入时已损坏**。
- 解决：**含中文的调试/复现脚本一律写成文件再运行**（.tmp/*.py，UTF-8），
  不要走内联 heredoc；用 ASCII 模式做布尔检查（`in` 判中文会被同样破坏）。
- 备注/复现：2026-08-11 浏览器冒烟排查浪费 2 轮；最终用 .tmp/browser_repro.py
  文件方式一次定位到 normalize 崩溃。回归脚本（regression.py）本身是文件，不受影响。

### [2026-08-11] 「LLM 不可用直接终止复盘」契约（2026-08-10 变更，测试需同步）
- 现象：全量 pytest 约 20 个用例失败——test_backend/test_graph/test_r11 期望
  「LLM 不可用 → 规则引擎降级出报告」，实际 job 变 error。
- 原因：2026-08-10 未提交变更（graph.py report_node）把 LLM 不可用从「降级生成
  规则报告」改为「抛 DataValidationError 终止」（用户已确认语义：LLM 都不可用时
  复盘无意义）；autouse offline fixture 把 _llm_ready 打成 False，测试契约未同步。
- 解决（用户拍板方案 A）：同步测试契约——「LLM 不可用 → 任务 error + 中文提示」
  单独断言；要测流水线逻辑的用例 mock LLM 可用（_llm_ready→True + 拦截 _invoke_llm
  返回固定 AIMessage 或 patch report_node），保证 fetch/辩论/进度钩子仍被覆盖。
- 备注/复现：排查口诀——测试单独跑 vs 全量跑结果不一致 = 顺序污染；
  确定性失败先看 autouse fixture 的 monkeypatch 面；「HEAD 基线对照」用
  `git show HEAD:path` 比对 uncommitted 变更。

### [2026-08-11] 模块内 os.environ.pop 删代理变量不恢复 → 污染后续测试（顺序耦合）
- 现象：全量 pytest 中 test_backend_chat 3 个用例失败（assistant 内容变成「无法获取数据」），
  单独跑 test_backend_chat.py 全过；二分定位污染源为 test_account_replay.py
  （其真实抓取路径触发）。
- 原因：accounts/quotes.py 的 fetch_stock_name/_fetch_tx_quote 为直连腾讯，执行
  `os.environ.pop(HTTP_PROXY/HTTPS_PROXY/...)` **永久删除代理变量且不恢复**——
  后续 chat 取数（东财链）失去代理被迫直连 → 被出口 IP 限流 → 取数失败终止。
- 解决：`requests.Session(trust_env=False)` 已足够绕过代理，**删除 pop 逻辑**
  （不动进程环境变量）；同类坏习惯在 work/archive/settle_pending.py（已归档）。
- 备注/复现：全量失败/单跑通过 = 顺序污染，优先搜「删环境变量不恢复」类副作用；
  排查口诀同 2026-08-11「LLM 不可用终止」条目。

### [2026-08-12] Langfuse 复盘上报开关（脚本跑不上报、页面跑上报）
- 现象：批量历史回补脚本（走 `POST /api/reviews`）每次复盘都往 Langfuse 刷
  `review:done` trace + `review_quality` score，面板被脚本数据淹没，页面跑的真实复盘
  反而难找；用户要求「脚本跑不写 Langfuse，前端页面跑才写」。
- 原因：此前 quality 上报只看凭据（无请求级/进程级区分），批量与页面同链路无法区分。
- 解决（双层开关）：
  1. **API job 级**：`POST /api/reviews` 请求体支持可选 `trace`（布尔，默认 true）——
     脚本传 `trace: false` 时 `_run_review_job` 跳过 `quality.trace_review`/
     `score_review`（job 内 `_trace` 私有字段记录，不进轮询契约）；rubric 质量分
     照常计算（产品展示用），只是不写回 Langfuse；前端页面不传该字段，行为不变；
  2. **进程级总开关**：`quality._langfuse_configured()` 对齐 config——环境变量
     `LANGFUSE_ENABLED=0/false/off/no` 时凭据齐全也全链路 no-op（直连引擎的脚本可用）。
- 备注/复现：测试 `test_review_trace_false_skips_langfuse` /
  `test_review_trace_default_true` / `test_langfuse_enabled_0_global_kill_switch`；
  文档 `docs/LANGFUSE_GUIDE.md` §2「开关：脚本跑不上报、页面跑上报」。

### [2026-08-12] LLM 级 Langfuse 回调改 job 级开关（前端复盘要见全流程）
- 现象：用户要求「前端页面发起的复盘必须在 Langfuse 看到全流程（LLM 调用/取数等子
  span）」；此前 `graph._callbacks()` 只看环境变量 `LANGFUSE_CALLBACKS`（默认关），
  页面与脚本无法区分，且 `_invoke_llm` 在内部超时线程里才求值回调——即使设了 job
  标志 ContextVar 也传不进（线程不 copy_context）。
- 解决（三层）：
  1. **config.py 新增 job 级开关**：`langfuse_callbacks_scope(flag)` ContextVar 上下文
     管理器 + `langfuse_callbacks_enabled()`；`_run_review_job` 用
     `with config.langfuse_callbacks_scope(trace): state = compiled.invoke(initial)`；
  2. **调用方线程求值**：`graph._invoke_llm` 改为先 `callbacks = _callbacks()` 再传入
     内部线程（否则 job 开关在超时线程里读不到）；直接 `llm.invoke(..., callbacks)`
     的调用点（host/debate）天然在拷贝上下文的线程里，不受影响；
  3. **线程池 copy_context**：`trades/analyst.py` 两处 ThreadPool
     （`submit(_run_analyst)` 与 `pool.map(run_one)`）改 `contextvars.copy_context().run`
     包裹，交易分析 LLM 调用也能读到 job 开关（对齐 graph.py 1217 行既有做法）；
  4. **开关优先级**：`LANGFUSE_CALLBACKS=1/true/on/yes` 强制开、`0/false/off/no`
     强制关、未设置跟随 job（前端复盘开、脚本 trace=false 关）；
     `trades/analyst._callbacks()` 同步此策略（此前它无条件带回调，脚本跑也会刷）。
- 备注/复现：测试在 `tests/test_langfuse_callbacks.py`（**不能放 test_graph.py**——
  其 autouse offline fixture 把 `graph._callbacks` monkeypatch 成恒 `[]`，会假阴性）；
  backend 接线测试 `test_review_job_langfuse_scope_flag`（job 内开关 True/False + 复位）；
  全量 626 passed；面板验证：页面跑一次复盘后 Traces 点开 `review:done` 应看到
  ChatOpenAI 子 span（脚本 `trace:false` 的复盘只有叶子 span）。

### [2026-08-12] 复盘进度偶发倒挂（sentiment 40→35）——并行线程 clamp 状态不共享
- 现象：`test_progress_stages_monotonic` 偶发失败（全量跑通过/回测跑挂，假代理环境下
  复现率更高）：events 出现 sentiment 30→40→35；前端进度条也会后退。
- 原因：08-10 分析师线程池改「每任务独立 copy_context」后，`_emit` 的单调 clamp 读
  `_last_pct_var`（ContextVar）——每个并行线程持有**独立副本**，各自从拷贝时的值起步，
  完成顺序不同时高 pct 线程先发、低 pct 线程后发 → 回调收到非单调序列；clamp 形同虚设。
- 解决：`_last_pct_var` 改存 **job 内共享 dict**（`{"last": 0.0}`，copy_context 只复制
  引用不复制对象）+ 模块级 `_progress_lock`，`_emit` 在锁内读改写——同 job 所有线程
  共享同一 clamp 状态，跨线程严格单调；不同 job 仍各自 set 独立 dict，并发隔离不变。
- 备注/复现：修复前 10 次复测该测试偶发失败；修复后 10/10 过 + 全量 626 passed；
  排查口诀：并行线程改共享状态时，ContextVar 拷贝的是「引用」，可变对象天然共享，
  「值类型」才隔离——进度/计数器类状态要么存共享可变对象加锁，要么在主线程统一发射。

### [2026-08-12] 资讯屏蔽开关（NEWS_ENABLED，默认关闭）
- 现象：用户要求「先屏蔽掉资讯这块的内容，做个开关」——复盘报告里的财经资讯
  （财新/央视）、聊天资讯工具、首页「最新快讯」（新浪7x24）都不再需要。
- 解决：config 新增 `news_enabled()`（动态读 env，`NEWS_ENABLED=1/true/on/yes` 开，
  默认关）；接入点共 4 处统一判断：
  1. **复盘引擎**（graph.py）：`fetch_data_node` 取数后剥离 news 块（snapshot/market_data
     无资讯）；`_render_market_markdown` 关闭时跳过 news 块（既不渲染内容也不渲染
     「数据缺失」占位——否则会输出「今日财经资讯（数据缺失，禁止编造）」）；`_legacy_fetch`
     不调 get_news_headlines；`news_analyst_node` 直接跳过（报告无「资讯分析」章节，
     进度阶段 news 保留，前端 stages 不变）；
  2. **聊天**（chat.py）：`_fetch_data` 对 news/news_headlines/realtime_news 三键关闭时
     返回 `available=False` + 「资讯已关闭（NEWS_ENABLED=0）」，不调工具；
  3. **实时快讯**（tools/realtime.py）：`fetch_realtime_snapshot._news` 关闭时返回
     `{"news": [], "source": "closed"}`，不抓新浪7x24（前端 DataTab 条件渲染自动隐藏）；
  4. **测试**：tests/conftest.py `setdefault("NEWS_ENABLED", "1")`——生产默认关、测试
     显式开保持既有资讯链路用例；专项测试 `tests/test_news_switch.py`（8 用例）。
- 备注/复现：不改前端（后端空数据驱动隐藏）；「联网搜索简报」（用户问题相关才请求）
  不在屏蔽范围；全量 634 passed；`.env` 设 `NEWS_ENABLED=1` 并重启服务即恢复资讯。

### [2026-08-12] 指数成交额恒无数据/错值——akshare 通达信指数日线 amount 列是成交量（手）
- 现象：上证指数成交额一直「无数据」（用户环境）；冒烟环境反而显示错值（创业板 1.9 亿
  「成交额」、科创50 1183 万）。
- 原因（实测 2026-08-12，三源打点定位）：
  1. 主源 `ak.stock_zh_index_daily_tx` 返回列 `date/open/close/high/low/amount`——
     **amount 列实为成交量（手）**（腾讯日K接口第 6 列是 volume，akshare 误命名 amount；
     实测 sz399006 该列 190755076 ≈ 当日成交量 189412654 手，真实成交额 5647 亿）；
  2. 旧 `_records_from_tx` 把该列当成交额读入 → 主源「成功」时错值直达报告，且
     amount 非 None 导致回填被跳过；用户环境主源失败走腾讯（amount=None）→ 显示无数据——
     同一 bug 两种表象，环境不同结论相反；
  3. 腾讯日K备用源 6 列无成交额（第 6 列 volume）；**东财 push2his 第 7 列与通达信协议
     fetch_daily(is_index=True) 的 amount 才是真实成交额（元）**。
- 解决：① `_records_from_tx` 修正映射——`volume ← amount 列（成交量）`、`amount 置 None`；
  ② `_backfill_index_amount(indices)` 对 amount=None 的指数用 东财 push2his → 通达信协议
  回填当日成交额（按日期精确匹配、只写 amount 字段、全失败静默保持 None）；主源/备用源
  各 return 前统一调用；③ 测试 `test_index_amount.py`（手算：逐字段对比仅 amount 变更）。
- 备注/复现：排查口诀——指数成交额异常先打点三个源函数（em/tx_kline/tdx_remote）确认
  值从哪个源来；**akshare 接口列名可能语义错位，拿真实值对比量级再信**（成交量≈亿级手、
  成交额≈千亿级元）；创业板/科创50 真实成交额量级：创业板约 5000-6000 亿、科创50 约 1000 亿。

### [2026-08-12] test_watchlist_full_flow 偶发 409→200（重复添加不查重）
- 现象：全量 pytest 偶发失败（`POST /api/watchlist` 重复 600519 预期 409 实际 200）；
  单独复跑 5/5 绿（复现率 0）。
- 原因：watchlist_add 路由在写盘前调 `_resolve_stock_name(code)`（fetch_stock_quote 真实
  网络降级链）——无代理环境单次 POST 可阻塞 15-30s，全量跑时网络时序拉长窗口；
  add_watchlist 查重与名称解析无逻辑耦合，属**环境时序放大**而非逻辑缺陷（判定逻辑正确）。
- 解决：全量/回归统一带假代理 `HTTP_PROXY=http://127.0.0.1:9`（与 regression.py 一致），
  名称解析快速失败返回 None，测试确定性恢复（680 passed 全绿）。
- 备注/复现：网络相关测试在全量环境必须先注入假代理；偶发失败先单独复跑 5 次区分
  「真 bug」与「环境时序放大」，再决定是否修代码。

### [2026-08-12] 分析师工具循环后 LLM 空回复（阿狼/爱在冰川 8-10/8-12 复盘）
- 现象：个别分析师（阿狼、爱在冰川等）分析条目变成「（工具查询完成，LLM 最终回复为
  空，以下为工具摘要）get_positions×1；search×1；?×6；calc_qty×2」；同场其他分析师
  正常、辩论阶段也正常（辩论不绑工具）。
- 原因：工具循环（上限 3 轮）内 LLM 每轮都继续调用工具（get_stock_info 等，tool_log
  里 get_stock_info 缺 type 键显示为「?」），**第 3 轮结束时的回复只有 tool_calls 没有
  content**——循环在 LLM 想总结前被轮数上限截断，content 为空 → 走「LLM 回复为空」
  兜底占位。非 LLM 故障（辩论无工具正常），非空话检测误伤。
- 解决：① 工具循环退出后若 content 为空且有过工具调用 → **追加一次强制总结调用**
  （无工具 runner + system 提示「工具已全部完成，直接输出最终分析，不要再调用工具」）；
  仍为空才走兜底；② 兜底文本实质化——改用规则引擎观点（基于快照）+ 工具摘要附录，
  不再输出「LLM 回复为空」占位；③ tool_log 的 get_stock_info 补 type 键（诊断可读）。
- 备注/复现：取证 `data/reviews/2026-08-10|08-12/report.json` analyses[].tool_calls；
  单测 `test_forced_summary_after_tool_loop_empty`（script 3 段：tc → 空 → 强制总结正文）。

### [2026-08-12] 报告推荐行格式失控（「弘信电子（002379）21.34元，2成」一行多股）
- 现象：8-12 报告正文把分析师推荐转述成「光迅科技（002281）参考买入价183.50-185.00元，
  2-3成；弘信电子（002379）21.34元，2成」——名称(代码)、区间价、一行多股混排，
  系统无法解析、用户无法分辨。
- 原因：`build_report_prompt` 只有宽泛的格式描述（无冻结模板、无反例、约束未置尾）；
  分析师原文格式是对的（`002379 弘信电子：买入价21.34元，仓位2成`），是报告生成环节
  LLM 转述时自由改写。
- 解决（依据 DeepSeek 官方 JSON Output/结构化输出指南：固定 schema + 程序校验）：
  ① ANALYST_BASE_TEMPLATE sec3 与 build_report_prompt 各加「推荐格式冻结」章节——
  每行一只、格式唯一 `代码 名称：买入价XX.XX元，仓位X成`、✅正例 ❌反例（括号代码/
  一行多只/区间价）、约束置尾；② 程序校验 `_recommendation_format_ok(report)`：推荐行
  含「名称（代码）」或一行 ≥2 代码 → 违规 → `report_node` 重试一次（prompt 附违规提示），
  仍违规接受（不无限重试）；描述行（「光迅科技（002281）今日+0.52%...」）不误伤。
- 备注/复现：官方文档 api-docs.deepseek.com/guides/prompting（JSON Output 章节）——
  结构化约束 + 输出后程序校验是官方推荐做法；本项目报告为 Markdown 长文不适合整体
  JSON，故对关键行做「冻结格式 + 正反例 + 校验重试」。

### [2026-08-12] 复盘降级标注过滤（用户口径：除非全链路缺失否则不标注）
- 现象：每次复盘都带「**本次复盘存在降级标注**」横幅：sectors/minute/news/index_trend
  主源失败降级备用源的正常降级链信息刷屏。
- 解决：`graph._filter_usable_degraded(notes, blocks)`——按 note 前缀匹配数据块，
  该块数据可用（`_block_usable`）即丢弃该条标注（降级链正常工作不算问题）；
  只有全链路失败（块 source=数据缺失）才保留标注；匹配不到块的保守保留。
- 备注/复现：语义变更影响 test_graph 旧断言（`开盘啦不可用` 不再进 degraded_notes，
  state.degraded 变 False——已同步）；快照/报告/meta 三处同步干净；quality 的
  degraded 评分项语义不变（真缺失时仍要求标注可见）。

### [2026-08-12] 分析师只输出股票代码（根除代码-名称不匹配）+ 复盘不获取财经新闻
- 现象：8-12 报告出现「宏桥控股(002379) 弘信电子：买入价21.34元，仓位2成」——LLM 给出
  的代码与名称不匹配（Langfuse 取证：分析师输出代码正确但名称错位）；复盘降级标注里
  news 相关条目（央视无数据/经济日历超时）持续出现。
- 原因：① 旧格式「代码 名称：买入价」让 LLM 自由生成名称 → 代码-名称对不上；② 资讯
  开关只做了**展示层剥离**（fetch_data_node pop news 块），取数层仍在请求 news 块 →
  降级 note 产生且过滤逻辑匹配不到（块已被剥离）→ 泄漏到标注。
- 解决：
  1. **只输出代码**：ANALYST_BASE_TEMPLATE sec3 / build_account_block 输出结构 /
     build_report_prompt 推荐冻结——格式统一 `买入 代码：买入价XX.XX元，仓位X成`，
     正反例明确禁止写名称；`_recommendation_format_ok` 升级：推荐行出现
     「代码+中文名」或「名称（代码）」即违规 → 报告重试一次；
  2. **自动下单正则兼容**：三处 regex 名称组改可选
     `(\d{6})\s*(?:([^\s：:，。]{1,12})\s*)?[：:]`（新格式无名称、旧格式仍兼容，
     name 缺失 plan 落盘用 code）；
  3. **前端撤销名称转回**（用户改口）：MarkdownView/ChatView 移除 withStockNames/
     getStockNames 调用（format.js/api.js 函数保留无害）；
  4. **取数层不获取新闻**：`_parallel_blocks` 在 NEWS_ENABLED=0 时剔除 news 键
     （lambda 惰性未执行，零网络调用）；`_filter_usable_degraded` 对 news 前缀
     标注在开关关闭时直接丢弃。
- 备注/复现：全量 683 passed + 回归 17/17；用户口径记录在 requirements-r15.md
  （R1 修订：LLM 输入输出均只代码，UI 不再转名称——之前 R5 转回已撤销）。

### [2026-08-12] 下单链路收敛：移除 calc_qty/place_order 工具（用户拍板）
- 背景：分析师输出只代码后（上一条目），工具链仍残留「LLM 调 calc_qty 算股数 →
  自然语言总结不输出格式行」的漂移风险（8-03 拔小弦案例）；place_order 工具与
  「程序解析文本自动记录」双路写 plan 语义重复。
- 用户确认（2026-08-12）：① calc_qty 移除；② place_order 移除——下单统一由程序
  读取分析师报告文本自动操作；③ 8-03 修复靠重试（不自动补齐）；④ 三路写 plan
  收敛为一路（文本正则 regex_*）。
- 落地：
  1. graph.py 删除 `_make_place_order_tool` 与 `_make_account_tools` 的 calc_qty；
     `_llm_analyst` 工具只剩 get_stock_info / search_history / get_positions；
  2. 工具分发删 place_order/calc_qty 分支；tool_qty_fallback 补齐逻辑删除；
     推荐行缺失 → 纯重试一次（明示按冻结格式输出，无工具 runner）；
  3. prompts（build_account_block）删 calc_qty/place_order 引导，改为
     「按格式输出文本，系统自动读取并记录计划；卖出前用 get_positions 确认可卖」；
  4. 写 plan 唯一入口 = 自动下单正则（regex_qty/regex_cheng）；
     `engine.place_order`/`calc_buy_qty`（撮合引擎）不受影响，保留。
- 备注/复现：测试 test_pending_dedup 两个用例改测 plan.add 去重（正则入口等价语义）；
  新增 test_natural_language_summary_retried_to_format（8-03 案例：总结→重试出格式行）；
  全量 684 passed + 回归 17/17；撮合折算股数在 replay/engine 层（成数×现金，见
  engine.place_order 口径），分析师不需要算手数。

### [2026-08-12] 逐日交错模拟流程（撮合前日计划 → 跑当日复盘）+ qty 式卖出成数 0 兜底
- 背景：用户指出「跑完全部复盘再模拟下单，分析师不知道自己的持仓」——正确流程必须
  逐日交错：**撮合 day-1 的计划（用 day 完整行情）→ 跑 day 复盘**（分析师 get_positions
  读到撮合后持仓，含 T+1 冻结）。
- 落地：
  1. `replay.settle_plans(rec_date, settle_date)`（新增）：增量撮合——加载已有账户
     （非从零），撮合 rec_date 计划用 settle_date 行情，快照 + 落盘；
     与 run_replay（全量从零重放，事后校验用）并存；
  2. 流程脚本 `.tmp/run_aug_sim.py`：重置账户 → for day: settle_plans(prev, day) →
     复盘(day)（backend._run_review_job）；
  3. **qty 式卖出兜底**（8-06 养家「卖出 000595：卖出价6.26元，卖出3000股」被拒根因）：
     qty 式正则无成数信息 → position_cheng=0 → engine.place_order 校验
     「仓位成数无效」拒绝（即使显式 qty 已给出）。修复：**显式 qty 时成数不参与计算，
     放宽校验（0<cheng≤10 仅对无 qty 的成数折算生效）**；
  4. 矫正方式：不重跑复盘，用修复后引擎 `replay --start/--end` 全量重放即重建账户。
- 备注/复现：重放后养家 000595 卖出 3000 股成交、铁锤 003001 8-06 卖出成交、
  8-07 重复卖被「无持仓」正确拒绝（连续两日卖同一只属分析师重复，引擎拦截合规）；
  全量 685 passed；交错模拟 5 天（8-03~8-07）撮合统计：成交 14 / 拒绝 19 / 未结算 10
  （8-07 计划未到结算日）+ 违规 0；行情有 data/quotes_cache 缓存，重放秒级。

### [2026-08-12] 重复卖出兜底：卖出计划无持仓 → 程序跳过 + prompt 约束
- 现象（承上条目）：铁锤连续两日计划卖出同一只 003001，第一日成交后第二日被引擎
  「无持仓」拒绝——分析师重复卖出属 LLM 行为问题，仅靠引擎拦截不优雅。
- 落地（用户确认一并处理）：
  1. **程序兜底**（graph.py 自动下单解析）：三处正则入口（qty 式/成数式/结构化）解析
     卖出计划时校验当前账户持仓（`_held_codes`），无持仓 → 不写 plan、tool_log 记录
     `sell_skipped_no_position`；
  2. **prompt 约束**（build_account_block）：只能卖出 get_positions 查询结果中存在的
     持仓；查询无该股/无持仓禁止输出卖出计划；同标的同时段只允许一个卖出计划。
- 备注/复现：单测 `test_sell_plan_skipped_when_no_position`（600519 有持仓正常记录、
  003001 无持仓跳过）；全量 686 passed；服务已重启（账户/撮合相关接口加载新代码）。

### [2026-08-12] 收益率曲线与累计收益率对不上（口径统一为收盘快照）
- 现象：模拟账户页「收益率曲线」终点与「累计收益率」卡片不一致。
- 原因：曲线用**每日收盘快照**（engine.snapshot 的 total_assets，收盘估值）；
  概览 total_return_rate 用**实时价估值**（fetch_stock_quote 当前价）——实时接口
  失败时回落**成本价**（不含浮动盈亏），两套口径不同源 → 数字对不上。
- 同花顺口径（行业公认定义，用户要求先查证）：累计收益率 = (当日总资产 − 期初资金)
  / 期初资金，总资产 = 现金 + 持仓市值（**按当日收盘价估值**）；曲线每日一点（收盘
  口径），**曲线最后一点 = 当前累计收益率**（必须一致）。
- 修复：`accounts_routes._summary` 的 total_assets / total_return_rate 统一改用
  **最近收盘快照**（与曲线终点同源，恒一致）；实时价仅保留在持仓行「现价/市值」列。
- 备注/复现：实测 yangjia API=-0.0118 与快照终点=-0.0118002 同源（显示层四舍五入一致）；
  全量 686 passed；模拟盘为收盘后复盘场景，快照即最新口径，实时估值不再参与收益率。

### [2026-08-13] fastapi≥0.140 include_router 懒加载：app.routes 不扁平化（契约探针失效）
- 现象：backend/main.py 拆分路由（APIRouter + include_router）后，`.venv`（fastapi 0.141.1）
  下 `test_r15_acceptance.test_r5_stock_names_contract` 失败——`app.routes` 遍历不到
  `/api/data/stock-names`；系统 python（fastapi 0.115.12）却正常（40 条扁平路由）。
- 原因：fastapi 0.140+ 的 `APIRouter.include_router` 改为追加懒加载 `_IncludedRouter`
  （starlette 1.3.x），`app.routes` 只含 `_IncludedRouter` 包装，路径在**首次请求匹配时**
  才物化；0.115 及更早版本直接扁平化。`app.routes` 是 `self.router.routes` 属性代理。
- 解决：装配阶段调用 `backend.main._flatten_included_routers(app)`——把 `_IncludedRouter`
  经 `effective_candidates()` 展开为普通路由（本项目 include 均无 prefix/tags/dependencies，
  物化后与原路由等价）；旧版本自动空操作。`backend/main.py` 已内置，勿删。
- 备注/复现：两环境（.venv 0.141.1 / 系统 0.115.12）`len(app.routes)` 现均为 40；
  只遍历 `app.routes` 的契约探针必须用物化后的 app，不能依赖请求时展开。

### [2026-08-13] 子代理机械拆分 3100 行 stock_data.py 失败 → 未提交改动丢失 → .pyc 字节码恢复
- 现象：派子代理（deepseek-v4-flash）按行号切片拆分 `tools/stock_data.py` 到多个子模块，
  执行中把文件写乱（`_base.py` 出现孤儿缩进 import 列表 + 门面内容错位、各子模块只剩
  import 头），导致「最近几天未提交的改动」被覆盖：10 个函数（fetch_stock_detail/
  _backfill_index_amount/_ths_sector_hist_line/_parallel_blocks 等）+ 6 个模块级变量
  全部「丢失」——`git log -S` 无记录（从未 commit），`git checkout` 只能回到 HEAD 旧版。
- 原因：flash 模型对大文件「机械切片 + import 重排」不可靠，行号假设与实际文件状态漂移，
  中间态把「定义」和「import 列表」写串；且子代理无「先备份 + 分步验证」的护栏。
- 解决（恢复三步）：
  1. `git show HEAD:... > stock_data.py` 恢复到干净基线（HEAD 2673 行完整版，只缺未提交改动）；
  2. 从 `__pycache__/stock_data.cpython-313.pyc`（拆分前编译产物）用 `marshal.load` +
     `dis.get_instructions` 逐条反汇编，还原 10 个函数 + 6 个变量的源码（docstring/常量用
     `repr(co_consts)` 直接提取，避免 GBK 乱码）；
  3. 补回「接线」（父函数里的调用点）：`_fetch_index_trend_impl` 的 _index_required_ok/
     _backfill_index_amount/_records_from_tdx_remote、`_sectors_from_hist` 的
     _ths_sector_hist_line、6 模式聚合的 _parallel_blocks、`_records_from_tx` 的
     amount→volume 映射、INDEX_CODES 移除深证成指等。
- 备注/复现：**教训**——大文件拆分这类「机械重构」不要交给 flash 子代理一次做完；
  要么不拆（职责单一的大文件优于拆分风险），要么「先备份原文件 + 分块提交 + 每块跑测试」。
  反汇编恢复的关键前提是 `.pyc` 存在（同版本 Python 编译产物）；若连 .pyc 都没有，
  未提交改动即不可恢复。测试基线必须用「系统 python 3.13」（AGENTS.md 铁律 4），
  `.venv`（3.12 装了 tdxpy）会导致 `_records_from_tdx_remote` 真实连通达信主站，
  使「全源失败」类测试（test_data_modes/test_index_amount）环境性失败。

### [2026-08-14] 逐日交错撮合跑通：三处根因链（批跑不撮合 → 缓存挡补抓 → 只推进有计划账户）
- 背景：用户报「分批复盘 7-02/7-03 分析师看不到前一日持仓」。逐日交错是正确流程：
  **撮合 day-1 计划（用 day 完整行情）→ 跑 day 复盘**，让分析师 get_positions 读到
  撮合后持仓（含 T+1 冻结）。但把「交错撮合」接进 `work/review_batch.py` 后跑 3 天，
  连续暴露三处根因：
- 根因①：批跑脚本 `cmd_run` 只提交复盘、从不结算前一日计划（交错没接上）。
  → 修：循环里加 `prev_date` 跟踪，每个非跳过日在复盘前先 `settle_plans(prev, day)`
    再 `run_one_date(day)`（新增 `settle_prev_day` 助手，失败不阻断复盘）。
- 根因②：修①后跑 3 天仍 **0 成交**（3 拒绝 + 7 行情缺失）。行情缺失的根因是
  `quotes.py get_day_quotes`「缓存文件已存在 → 权威直接返回、绝不联网/重抓」——而
  `data/quotes_cache/{date}.json` 是**部分快照**（只含复盘时取数过的股票），计划股
  不在其中就被挡在缓存外判「行情缺失」。
  → 修：缓存**部分命中**时，已命中的代码仍以缓存为权威（不重抓、保持确定性），
    缺失代码逐 code 补抓并**合并落盘**（不再整体早返回）。历史日期主源失败仍拒绝
    腾讯实时价兜底（原语义保留）。测试 `test_settle_plans_no_plan_missing_quote_...`
    改 mock 主源失败来模拟缺失（旧「空缓存阻断抓取」机制已过时）。
- 根因③：修②后成交正常，但 `settle_plans` 的 `sids` 只取「当天有计划」的分析师 →
  7-03 撮合后仅 bingchuan（当天唯一有计划的）推进到 7-03，其余持有 7-02 买入仓位的
  账户 current_date 停在 7-02、T+1 冻结不解除。
  → 修：`sids = 计划技能 ∪ 存量账户目录`（`skill_ids` 显式传入时仍只按传入过滤），
    无论当天有无计划都推进所有存量账户（`_settle_day` 幂等，同日期早返回）。
- 备注/复现：settle-only 验证（免 LLM，plan.json 在盘）：7-01→7-02 成交 3/拒绝 6/
  行情缺失 0；7-02→7-03 成交 1/拒绝 0/缺失 0。撮合后全账户 date=2026-07-03，7-02 买入
  （000620/001269/600446）frozen=0（已解冻）、7-03 买入（000536）frozen=2400（冻结）。
  另修 `test_breadth_realtime`：R16 加「total>=3000 完整性守卫」后旧 mock 仅 3 只触发
  降级，改 mock 3000 只+边界样本。全量 733 passed / 52 skipped。

### [2026-08-14] 批跑「不允许跳过」：fail-fast + 增强重试 + py_mini_racer 修复 + 东财限流
- 现象：晚间（20:37 起）跑 7-29~8-14 逐日批跑，出现「单日失败被静默跳过」——7-29
  `LLM 降级`、7-30/8-06/8-07 `index_trend 数据缺失`，批跑脚本原设计「单日失败不中断
  批次」继续跑后续日期，导致数据链断裂（7-30 缺失但 7-31/8-03 已生成）。
- 根因（三连环，外部服务晚间抖动 + 一个环境坑）：
  1. **`.venv` py_mini_racer 缺 `__init__.py`**（`site-packages/py_mini_racer/` 里只有
     `_mini_racer.py` 等，无 `__init__.py`）→ `from py_mini_racer import MiniRacer` 报
     `cannot import name ... unknown location` → `import akshare` 整体失败 → 指数主源
     `stock_zh_index_daily_tx` 不可用。7-29 能成功是「当时」靠东财备用源；东财被限流后
     主源+备用源全挂 → `index_trend 数据缺失`。**修复**：手动补 `__init__.py`
     （`from ._mini_racer import MiniRacer`），.venv 无 pip 无法重装。（.venv 文件不入
     git，换环境需重建。）
  2. **东财数据源限流**（备用源）：直连 `push2his/push2.eastmoney.com` 均
     `RemoteDisconnected`，走代理 502。主源（腾讯指数）恢复后不再依赖东财。
  3. **外部服务间歇抖动**：腾讯指数（14-18s，偶发 >30s 超时）+ opencode LLM（150s 超时）
     晚间时好时坏，3s 间隔 × 3 次重试不足以跨越抖动窗口。
- 修复（`work/review_batch.py`）：
  1. **fail-fast**：`cmd_run` 单日失败立即 `return 1` 中止批次，绝不静默跳过后续日期
     （重跑前需先 `settle_plans` 逐日重建账户到上一个 done 日期，因 settle 已把账户推进
     到失败日期）。
  2. **增强重试**：`MAX_ATTEMPTS` 3→5、`RETRY_SLEEP` 3s→45s，跨越抖动窗口。
- 备注/复现：修复后 8-06~8-14 共 7 天全部成功（exit 0），8-07/8-11/8-12/8-14 靠 45s
  重试跨越抖动；7-01~8-14 共 33 个交易日逐日交错撮合跑通，总成交 135 / 拒绝 97 /
  行情缺失 0。教训：**批跑绝不能「单日失败静默跳过」**；外部服务抖动要 fail-fast +
  拉长重试间隔，而不是靠事后补跑。

### [2026-08-15] 给 place_order 加可选 keyword 参数会破坏 monkeypatch 假引擎（测试签名适配）
- 现象：为条件单给 `engine.place_order` 加 `*, conditional=None` 后，`run_replay`/`settle_plans`
  对买入计划传 `conditional=`，凡 monkeypatch 替换 `place_order` 的测试（test_account_replay.py
  的 `_fake_engine`、test_r12_backtest.py 的 `_fake_place_order`、`test_run_replay_*_rejections`）
  全部 `TypeError: got an unexpected keyword argument 'conditional'`。
- 原因：这些假引擎签名 `fake(account, code, name, side, target_price, position, day)` 未收新
  kwarg，而撮合入口真实传参带 conditional。
- 解决：三个测试文件的所有假 `place_order` 签名统一改 `(..., day, **kwargs)`，内部兜底调用
  `orig(..., **kwargs)` 转发。
- 备注/复现：**给引擎/API 加可选参数前，先 grep 测试里 monkeypatch 该函数签名的地方**；
  假实现接收 `**kwargs` 是常态防破手段。全量 762 passed / 53 skipped（R12 验收 skip 为既有态）。

### [2026-08-15] 条件单持有天数要用持仓内计数器，勿用自然日差
- 现象/设计：需求「超时持有到第 13 个交易日收盘」——若用
  `(day_date - buy_date).days`（自然日）跨周末/节假日会**多计天数**，导致提前超时清仓。
- 解决：持仓挂 `hold_days`（买入日=1）+ `hold_check_date`，`check_conditional_orders` 在
  **每个结算日**只对 `hold_check_date != day` 的持仓 +1（幂等），按结算日序列累计到第 13 天。
- 备注/复现：`settle_plans`（增量加载落盘）与 `run_replay`（全量内存）都能正确持久该计数，
  保证两种回放确定性一致。

### [2026-08-15] 历史复盘离线回放 + 数据预热（完整行情快照落盘）
- 背景：复盘落盘时 snapshot.json 只保留前端展示 6 键（丢 index 日K/龙虎榜等），完整取数
  结果从未落盘 → 每次重跑历史复盘都实时调东财/腾讯/akshare（502 抖动 + A/B 回测输入不一致）。
- 落地：
  1. `reviews.py` 新增 `save_market_data/load_market_data/has_market_data`——把喂给 LLM 的
     完整结果（blocks + 渲染 market_data 文本 + market_weather + emotion_cycle +
     degraded_notes + source + saved_at）落盘到 `data/reviews/{date}/market_data.json`
     （尊重 STOCK_REVIEW_DATA_DIR 隔离）；`fetch_data_node` 实时取数后自动落盘（失败不阻塞）；
  2. `fetch_data_node` 开头：若该日期 market_data.json 已存在且 mode 匹配且未强制实时 → 直接
     离线回放（跳过 fetch_mode_data）；source 标「离线回放」，降级说明加
     「离线回放：复用已缓存行情快照，未实时取数」，返回契约与实时取数完全一致；
  3. 强制实时开关：`REVIEW_FORCE_FETCH=1/true/on/yes` 或 state.force_fetch 真值；
  4. `scripts/preheat_reviews.py`：批量预热历史日期（--start/--end/--dates/--force/--fail-fast/
     --mode/--cache），断点续跑（已有跳过），单日失败打印中文原因默认继续。
- 坑（测试隔离）：`fetch_data_node` 自动落盘是**进程副作用**——test_graph 各用例共用
  STOCK_REVIEW_DATA_DIR 时，前一个用例落盘的 date=2026-08-03 market_data.json 会被后续用例
  离线回放复用，导致 `test_fetch_emotion_cycle_injected`/`degraded` 断言失败（顺序污染）。
  → conftest.py 新增**函数级 autouse** 数据根隔离 fixture（每测试独立临时目录 + 清理），
  根治「落盘副作用」类顺序污染；test_graph 的 offline fixture 设 `REVIEW_FORCE_FETCH=1`
  维持「每次实时取数」的流水线测试语义（离线回放由 test_offline_replay.py 专项覆盖）。
- 备注/复现：新增 `tests/test_offline_replay.py`（12 用例：存储 roundtrip/回放跳过实时/
  整图回放/无缓存兼容/强制刷新/确定性/模式不符回退/预热脚本）；全量 744 passed / 53 skipped。
  教训：**给已有测试逻辑加「读取已落盘产物」的旁路时，必须同步考虑该产物在本进程的重现性**，
  用 per-test 数据根隔离根除「跨用例副作用」，不要靠临时禁用缓存打补丁。

### [2026-08-16] 闭包 nonlocal 漏声明 + 进度回调新增「分析师子事件」污染 job.stage（回测耗时采集）
- 现象：把 `backend/jobs.py::_run_review_job` 抽成可复用 `run_review_day(date, mode, max_rounds,
  trace, progress_cb, timing_cb)`（含阶段/分析师耗时采集）后，单日复盘 job 终态 `stage` 不回归
  「done」（停在 fetch）；`_new_timing_tracker` 的 `track()` 对阶段事件静默抛异常
  （UnboundLocalError），导致 `stage_timings` 恒空、`progress_cb` 收不到阶段事件（被 `_emit`
  的 try/except 吞掉）。
- 原因（两个坑叠加）：
  1. **闭包 nonlocal 漏声明**：`track` 内既 `stage != _active` 读又 `_active = stage` 写 →
     Python 当局部变量 → 读取报 UnboundLocalError；`nonlocal` 只在 `_close` 里声明了，
     `track` 里漏了。**凡内层函数既读又写外层变量，必须在每个写它的函数里都 nonlocal。**
  2. **进度「子事件」污染 job.stage**：graph 新增「分析师 started/finished」子事件（stage
     用 `analyst`、meta 带 skill_id）后，单日 job 的 `_progress_cb(job_id)` 把
     `JOBS[job_id]["stage"]` 覆盖成 analyst；且该子事件以 `_emit(..., meta=...)` 关键字传参，
     `test_r2_analysts_total_5_and_progress` 直接 monkeypatch `graph._emit` 为 3 参 lambda
     → 4 参调用 TypeError（非 `_emit` 内部，无法被吞）直接失败。
- 解决：
  1. `track` 加 `nonlocal _active, _active_start`；
  2. `_progress_cb` 对 `meta.type == "analyst"` 子事件直接忽略（单日 job 不采集、不污染 stage）；
  3. **分析师子事件默认关（opt-in）**：graph 新增 `_analyst_events_var` ContextVar +
     `set_progress_callback(callback, analyst_events=False)`；`_run_analysts` 仅在
     `analyst_events=True`（回测编排器 / run_review_day 显式开启）时发射该子事件——
     既有 3 参回调、直接用 3 参 lambda 替换 `_emit` 的测试零改动。
- 备注/复现：`test_review_full_chain` 从 stage=analyst 到 stage=done；
  `run_review_day` 的 stage_timings 从空到 `{fetch,news,analysts,host,report}`；
  `test_r2_analysts_total_5_and_progress` 恢复全绿。教训：**给共享进度回调加「子事件」维度时，
  要么让不关心的调用方显式跳过（_progress_cb ignore），要么默认关闭、需要方 opt-in
  （analyst_events 开关），避免新事件破坏既有 3 参回调/替换 _emit 的测试。

### [2026-08-16] 回测实时诊断面板：断言口径 + 并行代理工作区 + 历史数据不回填
- 现象/需求：回测页看不到「卡在哪/为什么/第几次/多久」→ current 契约增强（8 字段：
  date/stage/message/pct/attempt/attempts/last_error/elapsed_ms，GET 实时算）+ 重试可见 +
  统计时间含全部重试（review.elapsed_ms 累计所有尝试墙钟含 RETRY_SLEEP，不再只算末次成功）。
- 坑 1（测试断言口径）：建议 `review.elapsed_ms >= 400` 被实证否定——注入 mock
  `make_fake_day()` 的 stage_timings 固定 100×4=400，但 **mock 并不真 sleep**，真实累计墙钟
  只有 3~6ms，`>=400` 必然误红（测试 bug）。教训：**对「真实墙钟」类字段的断言必须基于
  可测语义（非负 int + 口径相等 `elapsed == review + settle` + 慢重试 mock 的下界），
  不要拿固定 stage 求和当下界**——那是旧语义的产物，与新语义无关联。
- 坑 2（并行 TEST/IMPL 共享工作区）：TEST 写「预期红」测试时，IMPL 已把未提交改动落入同一
  工作区 → 测试直接全绿，无法证明「测试能卡住未实现契约」。解法：断言设计上让「旧实现必然红」
  （如 A1 八字段检查、A4 attempts 恒等），并明确区分「实现未就绪红」vs「已合并实现绿」；
  另外 **send_message 可唤醒已 finished 的 subagent 继续新 turn**（排队消息在 agent 退出后 parked）。
- 坑 3（历史数据不回填）：修复 `day["attempts"]=attempt` bug 后，**旧 progress.json 里已是
  旧值**（如 attempts=1/3），新代码不回填——验收时历史回测的 attempts 仍显示旧语义
  （前端「重试 N-1 次」徽标对旧数据 attempts=3 显示「重试 2 次」，恰好与新契约兼容，但
  要与「新回测才写 attempts=3」区分）。教训：**契约变更只影响新数据，验收历史回测时
  先确认数据产生于旧代码还是新代码。**
- 备注/复现：`tests/test_backtest_live_diag.py` 8 用例（A1-A7）；`test_backtest.py` 两处
  旧断言同步（attempts==1→3、review==400→非负+口径相等）；前端 build + msedge headless
  冒烟（`.tmp/smoke_bt.py`：历史回测打开、重试徽标、终态不误显「正在重试」）。**

### [2026-08-16] LLM 超时调优（150→200s）+ 失败类型分类重试（TimeoutError→2 次 fail-fast）
- 现象/需求：回测分析师/report 阶段 8-06/8-07 重试 2 次浪费——单次 LLM 硬超时
  `llm_runtime._INVOKE_TIMEOUT=150s`，opencode 慢响应 >150s 被判 TimeoutError → 整日按
  MAX_ATTEMPTS=3 重跑。调优：① `_INVOKE_TIMEOUT` 150→200；② `backend/backtest.py`
  失败分类 fail-fast——`LLM_MAX_ATTEMPTS=2` + `_is_llm_failure(exc)=isinstance(exc,
  TimeoutError)`；`_run_one_day` 动态上限 `max_attempts`：data 类（RuntimeError/
  DataValidationError 等）保持 3，LLM 类首败后降 2 即 fail-fast；`day.attempts`/
  `current.attempts` 用实际生效上限。
- 坑（共享工作区并发合并）：TEST 写 E1/E2/E3 测试时首次跑 E2 红（3 次）证明旧实现被卡住，
  随后 IMPL 合入同一工作区 → 第二次跑全绿（2 次 fail-fast）。凡是「预期先红后绿」的契约
  测试，断言必须让旧实现必然红——E2 用「次数==2 且不抛第 3 次 + day.attempts==2 +
  attempts_log 长度 2 全失败且 reason 含『超时』 + current.stage=error」确定性区分。
- 备注/复现：验收新增 test_e1_llm_invoke_timeout_200 / test_e2_llm_timeout_failfast_
  two_attempts / test_e3_data_failure_keeps_three_attempts；tests/test_backtest_live_diag.py
  15 全绿。

### [2026-08-16] 坏快照事故链：根语义不一致 + 指数取数不稳定 + lite 快速模式
- 现象 1（坏快照）：7-1 回测失败「index_trend 数据缺失」——8-15 14:03 一次失败运行
  （指数取数超时 >45s）把好快照覆盖成坏版；离线回放命中坏快照 → validate_data_node 失败
  （错误消息误导「检查网络」）。修复：删坏快照 + 批量预热（7 个日期 7/7 OK）+ 错误消息
  区分「实时失败 vs 快照缺失」（graph.validate_data_node 按 snapshot.source=="离线回放"）。
- 现象 2（根语义不一致）：预热脚本设 `STOCK_REVIEW_DATA_DIR` 后好快照写到 `data/{date}/`
  （少 `reviews/` 一级）——`reviews_root()` 的 env override 分支 `return Path(override)`
  （把 env 当 reviews 根），而 backtest/accounts/quotes 等全把 env 当**数据根**再拼子目录。
  修复：统一 `Path(override)/"reviews"` + 同步 5 处旧语义 fixture + `scripts/preheat_reviews.py`。
  教训：**同一 env 的语义必须全仓一致；scripts/ 是「用 env 隔离」的高危路径，改根逻辑必须
  同步排查 scripts/**。
- 现象 3（lite 快速模式两个坑）：
  ① `default_initial_state` 加 `lite` 键会被 LangGraph 丢弃（`ReviewState` 未声明该字段，
  invoke 忽略额外键）→ host/report 仍调 LLM。修复：`_lite_var` ContextVar +
  `set_lite_context(flag)` 上下文管理器包裹 invoke（host/report 读
  `_lite_active() or state.get("lite")` 双保险）。
  ② **前端 `api.js::startBacktest` 构造固定 body 会丢弃新增参数**（lite）——加新参数必须
  同步改 api.js 请求体 + normalize.js params 映射，否则「checkbox 勾了但后端收不到」。
- 备注/复现：全量 818 passed / 53 skipped；build + 冒烟（`.tmp/smoke_final.py`）全过；
  失败历史回测清理（删 error/cancelled 目录，history 从 manifest 重建）；lite 验收 F1/F2/F3。

### [2026-08-16] 回测断点续跑（resume）实现要点与并行 TEST 跑测污染
- 现象：本实现代理（只改实现，TEST 并行改 tests）跑合并后的 test_backtest_live_diag.py 时，
  test_a3_retry 偶发失败（calls 混入另一天的日期，5 次 vs 期望 3）；单独复跑该用例、以及
  三文件合跑均全绿（70 passed）。属并行 pytest 进程 / 遗留后台线程的跨测试污染，非逻辑 bug。
- 原因：resume 新用例（H1/H3）会启动后台 _run_loop 线程并 sleep 拉长 running 窗；若与
  其他测试/另一 pytest 进程共享数据隔离时序，可能串扰静态函数（_start 返回不同区间）。
- 解决：三文件合跑确定性通过；单独复跑区分「真 bug」与「环境时序放大」（PLAYBOOK 第 2026-08-12
  watchlist 条目同款口诀）。resume 单 running 互斥、不重置账户、保留已 done 天产物是语义核心。
- 备注/复现：resume 验收用 POST /api/backtest/{id}/resume；delete_review keep_snapshot 是
  _run_one_day 产物校验失败清理改用的防复发路径（H4/H6）。
