# A股短线/波段复盘 + 多智能体分析 + 模拟交易 竞品调研报告

> 日期：2026-08-13
> 调研对象：本项目（A 股复盘系统：FastAPI + React + LangGraph 多智能体；6 种时间模式复盘 + 5 位人格分析师 + 主持人辩论 + 模拟下单虚拟账户 + 数据展示 + 交割单分析 + 分析师聊天）
> 调研方法：web_search 桌面调研。**每一条结论均附来源 URL；无法从公开来源证实的细节标注「待验证」；未编造任何项目或链接。**
> 说明：本项目文档 `docs/requirements.md` §四 记录了我们实际使用的数据源与降级链（东财 push2、腾讯、通达信协议 tdxpy、开盘啦 Cookie 等），本文 §四 与其互相印证。

---

## 一、可比项目/产品清单

### 1.1 开源项目（GitHub）

| 名称 | 类型 | 核心能力 | 链接 | 与我们可比的点 |
|---|---|---|---|---|
| TradingAgents（TauricResearch） | 开源 | 多智能体 LLM 金融交易框架：研究员 → 多/空辩论 → 基金经理 → 交易员 → 风控；美/港股为主；据媒体报道约 71.4K star（「待验证」当前数） | https://github.com/TauricResearch/TradingAgents ；论文 https://arxiv.org/abs/2412.20138 ；报道 https://www.tmtpost.com/7977510.html | 多智能体「辩论 → 决策」机制与我们的「主持人 + 辩论」流程最直接可比 |
| TradingAgents-CN（wuwei891112 等） | 开源 | TradingAgents 中文增强版，适配 A 股 | https://github.com/wuwei891112/TradingAgents-CN | 把 TradingAgents 翻译/适配 A 股数据源的实践 |
| TradingAgents-AShare（KylinMountain） | 开源 | A 股多智能体投研系统，15 名 AI Agent 模拟机构协作与实时辩论对抗，全流程可视化，Docker 一键部署，支持 OpenClaw/Claude Code 集成 | https://github.com/KylinMountain/TradingAgents-AShare | 多分析师辩论 + 全流程可视化的工程实现参考 |
| TradingAgents-CN-lite（cy-Yin） | 开源 | 轻量级多智能体交易分析框架，支持 A 股/港股/美股 | https://github.com/cy-Yin/TradingAgents-CN-lite | 轻量部署、A 股数据适配参考 |
| ai-hedge-fund（virattt） | 开源 | 「传奇投资者」人格多智能体分析团队 + 回测 | https://github.com/virattt/ai-hedge-fund | 角色人格化 agent 提示词设计（巴菲特/木头姐等风格） |
| quantdash-ai-stock（rancy777） | 开源 | A 股看盘/复盘/盘前计划一体化工作台：情绪周期、板块轮动、策略选股、AI 复盘、MCP Server、飞书机器人 | https://github.com/rancy777/quantdash-ai-stock | **定位最接近**：复盘 + 情绪周期 + AI 生成内容，可对标其报告结构与情绪周期模块 |
| vibe-astock（simonlin1212） | 开源 | A 股短线复盘看板：涨停池·连板梯队·龙虎榜·板块资金一屏看完；赚钱效应/晋级率/梯队断层/情绪周期等派生指标**纯计算直出（不经过 AI）**，AI 只把数据串成盘面研判；全本地、免 API key | https://github.com/simonlin1212/vibe-astock | 短线情绪指标计算口径 + 「指标计算与 AI 写作分离」的防幻觉架构思想 |
| daben-review（WhiteWolf-js） | 开源 | 短线打板情绪 + 板块分析 + 盘前/盘中/盘后分析 | https://github.com/WhiteWolf-js/daben-review | 打板情绪复盘工具，与我们 6 种时间模式（盘前/盘中/收盘）思路一致 |
| skill-b6-limitup-pool（quantskills） | 开源 | 涨停池动态管理：首板/连板数/**炸板次数/回封时间**、题材分组/特殊形态、情绪面量化（**分层晋级率·赚钱效应**），输出多维表格 + HTML 看板 | https://github.com/quantskills/skill-b6-limitup-pool | 涨停梯队字段细化（炸板次数、回封时间、分层晋级率）与情绪量化口径 |
| replay（NNNightglow） | 开源 | 股票复盘系统：指数、板块、个股、情绪等模块，支持历史回溯、数据更新与**资金管理** | https://github.com/NNNightglow/replay | 历史回溯与资金管理维度 |
| akshare（akfamily） | 开源 | 财经数据接口库（涨停股池 stock_zt_pool_em、龙虎榜、板块资金、指数等） | https://github.com/akfamily/akshare ；涨停股池说明 https://zhuanlan.zhihu.com/p/374376371 | 我们数据层已依赖；其涨停池/龙虎榜接口口径可对照 |
| easy-stock（jundizhou） | 开源 | A 股 AI 行情分析与智能投研工作台（大模型驱动） | https://github.com/jundizhou/easy-stock | 投研工作台形态 |
| EasyQuant（HiRenyi） | 开源 | AI 优化并回测量化策略，搭建全免费数据源，本地 + 聚宽免费回测，**多智能体评估 A 股标的** | https://github.com/HiRenyi/EasyQuant | 多智能体选股评估 + 回测链路 |
| gupiao（dongxnb） | 开源 | 多智能体 A 股交易分析系统：技术分析、基本面分析、风险评估 | https://github.com/dongxnb/gupiao | 多智能体分工参考 |
| aiagents-stock（oficcejo） | 开源 | 复合多 AI 智能体股票团队分析盯盘：模拟证券分析师团队、**游资龙虎榜跟踪**、板块预警轮动、预留 miniqmt 接口 | https://github.com/oficcejo/aiagents-stock | 游资/龙虎榜跟踪 + 量化接口预留 |
| AlphaCouncil / AI77（164149043 / acuage） | 开源 | 多智能体股票分析决策系统（Gemini + DeepSeek + 聚合数据 API） | https://github.com/164149043/AlphaCouncil ；https://github.com/acuage/AI77 | 多智能体决策系统 |
| A-share-Ai（hyqibot 幻银量化） | 开源 | AI 自主驱动实盘交易机器（付费获取），并公开 **A 股 Ai 炒股大赛模拟基金排行/全市场排行** 报告页 | https://github.com/hyqibot/A-share-Ai | 模拟基金竞赛/排行榜机制与我们的分析师虚拟账户可比 |
| FinRobot / FinGPT（AI4Finance-Foundation） | 开源 | 金融大模型（FinGPT）与 LLM 金融分析 Agent 平台（FinRobot） | https://github.com/AI4Finance-Foundation/FinRobot ；https://github.com/AI4Finance-Foundation/FinGPT | 金融 agent 分层设计、记忆/工具组织 |
| vnpy_paperaccount（vnpy） | 开源 | VeighNa 框架的**本地仿真交易模块**（模拟撮合） | https://github.com/vnpy/vnpy_paperaccount ；文档 https://www.vnpy.com/docs/en/community/app/paper_account.html | 模拟交易撮合引擎实现参考（委托/成交/持仓/资金） |
| backtrader | 开源 | 回测框架：模拟 broker，含**滑点模型、手续费、订单撮合** | https://www.backtrader.com/docu/slippage/slippage/ ；中文文档 https://backtrader-zh.readthedocs.io/zh-cn/latest/user_guide/brokers.html | 撮合与费用模型参考 |
| leek-fund（韭菜盒子） | 开源 | VSCode 炒股插件：股票/基金实时数据、主力资金等 | https://github.com/cwdhf/leek-fund | 轻量行情展示形态 |
| iwencai-cli（shaw-baobao） | 开源 | 同花顺问财（iWenCai）命令行工具：自然语言选股返回结构化结果 | https://github.com/shaw-baobao/iwencai-cli | 自然语言选股交互实现 |
| mootdx / tdx（new-economy 等） | 开源 | 通达信协议解析、数据读取封装 | https://github.com/mootdx/mootdx ；https://github.com/new-economy/tdx | 与我们的 tdxpy 路线同族实现 |
| kaipanla（yueshushu0207） | 开源 | 开盘啦数据接口封装 | https://github.com/yueshushu0207/kaipanla | 开盘啦数据获取参考（Cookie 门槛下） |
| jiuyan-stock-sentiment-skill（Mingdi-hub） | 开源 | 通过韭研公社 API 抓取个股讨论帖（含正文），输出 JSON 供 AI 分析舆情 | https://github.com/Mingdi-hub/jiuyan-stock-sentiment-skill | 社区舆情接入（我们资讯/舆情能力默认关闭，可作为开关内能力） |

### 1.2 商业产品

| 名称 | 类型 | 核心能力 | 链接 | 与我们可比的点 |
|---|---|---|---|---|
| 同花顺 i问财 | 商业 | 自然语言选股/问股（「问财选股」），AI 投资助手 App，条件单 | https://baike.baidu.com/item/i问财/61779154 ；评测 https://www.aixq.cc/28961.html ；App https://apps.apple.com/sg/app/同花顺问财-ai投资助手/id1222204519 | 自然语言选股交互范本（我们分析师聊天可对标） |
| 东方财富 妙想 | 商业 | 金融大模型：AI 选股、政策解读、智能问答；已升级深度思考模式并向所有用户开放；实测约 29 个炒股 Skills | https://finance.eastmoney.com/a/202503243354138686.html ；https://stock.stockstar.com/IG2025031400044472.shtml ；29 Skills 测评 https://caifuhao.eastmoney.com/news/20260716131607449043950 | AI 能力商品化形态、Skill 化组织 |
| 问财 vs 妙想 对比 | 商业 | 投研时效「从小时到秒」（东吴证券报告观点） | https://www.sgpjbg.com/labelsyh/aizhinengtouguyingyong.html ；https://m.toutiao.com/article/7485272318172840475/ | 能力边界与差异化定位参考 |
| 开盘啦 | 商业 | 涨停复盘、**竞价**、**情绪周期**、龙虎榜 App/网站（「如何用开盘啦看懂每一轮行情」为官方教学文） | https://www.kaipanla.com/article/39810 ；App https://apps.apple.com/cn/app/开盘啦-选股炒股，股票龙虎榜/id1071188962 ；百科 https://baike.baidu.com/item/开盘啦 | 情绪周期产品化标杆；同时是**我们的竞价数据源**（Cookie 门槛，见 §四） |
| 选股宝 | 商业 | 题材/涨停复盘、盯盘辅助（题材颜色标注） | https://baijiahao.baidu.com/s?id=1617532478194750937 ；盯盘辅助插件 https://chromewebstore.google.com/detail/选股宝盯盘辅助/dkfbfbbglfofocbigbhmkgihckdedcbk | 题材/涨停原因标注展示 |
| 财联社 | 商业 | 7x24 电报快讯（资讯源） | https://apps.apple.com/ga/app/财联社-上海报业集团主管主办/id958017075 | 资讯层对标（我们 NEWS_ENABLED 开关默认关） |
| 韭研公社 | 商业 | 个股讨论社区、每日异动简报 | 社区 https://www.jiuyangongshe.com/u/2c8ec6de0de643d1ba7d1193a5816e84 ；异动简报 Skill https://www.jindage.com/en/skills/jiuyan-yidong-market | 社区舆情/异动简报（我们可做成可选数据块） |
| 淘股吧 | 商业 | 短线炒股论坛：复盘学、涨停原因讨论、情绪方法论（养家心法等） | 复盘讨论 https://www.tgb.cn/dialog/23yPnyZNGy5_71602980_1 ；复盘经验 https://m.taoguba.com.cn/Article/3226536/1 | 短线复盘方法论内容源（我们 5 位分析师人设即源自这类游资生态） |
| 雪球 组合（模拟盘） | 商业 | 组合调仓/跟投/收益率展示（组合小知识官方帖） | https://xueqiu.com/4226803442/181740768 ；跟单讨论 https://global.v2ex.co/t/1200668 | 模拟组合产品形态（调仓记录、可跟投） |
| 同花顺/东财/叩富 模拟炒股 | 商业 | 虚拟资金模拟交易（教程：5 步上手、零风险练习真实交易） | https://blog.csdn.net/2401_89926894/article/details/162012510 ；叩富模拟平台 https://licai.cofool.com/ | 模拟交易产品化（T+1/涨跌停约束的公开做法「待验证」细节） |
| QMT（迅投）/ PTrade（恒生） | 商业 | 券商量化交易终端：回测 + 模拟盘 + 实盘；**回测引擎在信号精度、滑点模拟上有差异** | https://zhuanlan.zhihu.com/p/2040007766316734266 ；https://licai.cofool.com/user/guide_view_3309068.html ；回测引擎对比 https://licai.cofool.com/user/guide_view_3354232.html | 模拟撮合引擎精度（滑点/信号精度）参照系 |
| 聚宽 JoinQuant | 商业 | 量化平台：回测 + 模拟交易（社区案例公开） | 平台对比 https://zhuanlan.zhihu.com/p/626005758 ；社区 https://www.joinquant.com/ | 模拟撮合/回测平台能力参考 |
| 大智慧量化工场 / DDE | 商业 | AI 选股/策略编写（双均线等）/DDE 决策预警（「对倒出货」等） | 出自大众媒体盘点 https://mbd.baidu.com/newspage/data/dtlandingsuper?nid=dt_4554663177109325204 （功能细节「待验证」） | AI 选股/预警工具 |
| 韭菜盒子 AI 版 | 商业 | VSCode 看盘插件的 AI 增强版 | https://zeeklog.com/vscodechao-gu-cha-jian-jiu-cai-he-zi-aiban/ | 轻量工具形态 |
| 2026 免费 AI 理财工具选型观察 | 商业 | 券商原生、行情终端、第三方智能体社区三类选型视角 | https://caifuhao.eastmoney.com/news/20260804144612062570310 | 竞品格局全景参考 |

---

## 二、关键差异与差距分析

### 2.1 我们相对同类的差异化优势（守住）
1. **「多智能体复盘报告」是稀缺形态**：开盘啦/选股宝/淘股吧是「数据看板 + 人工复盘/论坛内容」，同类开源多智能体项目（TradingAgents 系、ai-hedge-fund）以美股为主、做「个股决策」，而我们把「人格化分析师（阿狼/爱在冰川/铁锤/养家/拔小弦）+ 主持人 + 辩论 + 自动报告」用在 A 股**每日 6 时间模式复盘**上，这一组合在开源与商业产品中都未见完全同款（据本次调研「待验证」）。来源：https://github.com/TauricResearch/TradingAgents ；https://github.com/rancy777/quantdash-ai-stock ；https://github.com/simonlin1212/vibe-astock
2. **分析师虚拟账户 + 逐日撮合结算 + 交割单分析**：开源项目几乎没有「给 LLM 分析师配 10 万虚拟账户做 T+1 模拟交易并回看交割单」的完整闭环；接近的只有幻银量化 A 股 Ai 炒股大赛排行（实盘 AI 驱动）与 vnpy_paperaccount（撮合模块，无 LLM 人格）。来源：https://github.com/hyqibot/A-share-Ai ；https://github.com/vnpy/vnpy_paperaccount

### 2.2 我们缺什么（差距点）
1. **情绪派生指标的细化与“纯计算直出”**：vibe-astock 把赚钱效应/晋级率/梯队断层/情绪周期做成**本地纯计算指标，AI 只写叙事**（防幻觉）；skill-b6 把涨停池细化到首板/连板数/**炸板次数/回封时间**/题材分组/**分层晋级率·赚钱效应**。我们的情绪指标（市场天气评分、昨日涨停表现等）已有雏形，但炸板次数/回封时间/分层晋级率等字段与「指标计算与 AI 写作分离」的架构尚不完整。来源：https://github.com/simonlin1212/vibe-astock ；https://github.com/quantskills/skill-b6-limitup-pool
2. **辩论的证据锚定与分歧收敛机制**：中金研究报告与 TradingAgents/FinCom 均强调**每个观点必须引用数据、多空持证据辩论**（FinCom 用 Disagree-or-Commit 让分歧显式收敛）。我们的辩论是「主持人 + ≤3 轮」，但缺少「观点必须引用快照字段」的程序级约束与收敛判定。来源：https://finance.sina.com.cn/stock/stockzmt/2026-07-29/doc-inikmivr0110776.shtml ；https://arxiv.org/abs/2412.20138 ；https://arxiv.org/abs/2606.00939
3. **模拟撮合的“市场微观结构”细节**：QMT/PTrade 回测引擎把滑点模拟/信号精度当卖点对比；backtrader 有可配滑点（固定点数/百分比）+ 手续费 + 涨跌停无法成交；vnpy_paperaccount 有完整本地撮合。我们的撮合目前是逐日按收盘/成交价结算，缺滑点模型、涨跌停封单无法买入的成交细节、部分成交/撤单等「仿真感」能力。来源：https://licai.cofool.com/user/guide_view_3354232.html ；https://www.backtrader.com/docu/slippage/slippage/ ；https://github.com/vnpy/vnpy_paperaccount
4. **情绪周期「阶段定位」输出**：开盘啦把情绪周期做成核心产品（冰点/修复/发酵/高潮/退潮的教学体系），养家心法是内容源头。我们只有「市场天气」量化分，缺少「周期定位 + 次日推演」的报告章节。来源：https://www.kaipanla.com/article/39810 ；https://xueqiu.com/8832856558/258771195
5. **AI 产品化的交互入口**：问财/妙想把「自然语言问股/选股」做成大众入口，投研时效「从小时到秒」；我们的分析师聊天优先级最低，能力上落后于商业产品。来源：https://baike.baidu.com/item/i问财/61779154 ；https://www.sgpjbg.com/labelsyh/aizhinengtouguyingyong.html
6. **分析师记忆/人设一致性**：FinMem 用「分层记忆 + 角色设计」提升交易 agent 表现；我们分析师每日复盘独立，跨日一致性靠历史复盘注入，无显式长期记忆层。来源：https://ojs.aaai.org/index.php/AAAI-SS/article/view/31290

---

## 三、可直接借鉴的提升点

### P0 复盘质量（3-5 条）
1. **情绪派生指标纯计算直出、AI 只写叙事**：把赚钱效应/晋级率/梯队断层/情绪周期等做成确定性计算，AI 仅负责把指标串成研判，从机制上防幻觉（与我们的降级链纪律同构）。来源：https://github.com/simonlin1212/vibe-astock
2. **涨停池字段细化**：补「首板/连板数、炸板次数、回封时间、题材分组、分层晋级率、赚钱效应」等维度进入涨停梯队模块与报告。来源：https://github.com/quantskills/skill-b6-limitup-pool
3. **辩论加证据锚定与收敛机制**：要求每个分析师观点引用快照具体字段（价格/涨停数/晋级率），主持人按「分歧是否收敛」判定辩论是否继续（对齐 FinCom 的 Disagree-or-Commit；我们已有 ≤3 轮上限，可加收敛早停）。来源：https://finance.sina.com.cn/stock/stockzmt/2026-07-29/doc-inikmivr0110776.shtml ；https://arxiv.org/abs/2606.00939 ；https://arxiv.org/abs/2412.20138
4. **情绪周期阶段定位章节**：报告增加「情绪周期定位（冰点/修复/发酵/高潮/退潮）+ 次日推演」，方法学对齐养家心法体系与券商「情绪周期与赚钱效应」策论。来源：https://xueqiu.com/8832856558/258771195 ；https://stock.finance.sina.com.cn/stock/view/paper.php?symbol=sh000001&reportid=808927980313&autocallup=no&isfromsina=no
5. **分析师分层记忆/人设一致性**：为每位分析师维护轻量长期记忆（历史复盘结论摘要），参考 FinMem 的分层记忆 + 角色设计。来源：https://ojs.aaai.org/index.php/AAAI-SS/article/view/31290

### P1 模拟下单真实性（3-5 条）
1. **引入可配滑点模型**（固定点数/百分比/波动比例）+ 手续费明细，参考 backtrader 模拟 broker 的滑点实现。来源：https://www.backtrader.com/docu/slippage/slippage/ ；https://backtrader-zh.readthedocs.io/zh-cn/latest/user_guide/brokers.html
2. **涨跌停成交约束仿真化**：涨停封单时买单无法成交、跌停封单时卖单无法成交，并按「次日继续一字」顺延未成交订单（细节「待验证」，公开模拟炒股教程仅描述基本 T+1/涨跌停约束）。来源：https://blog.csdn.net/2401_89926894/article/details/162012510 ；https://licai.cofool.com/user/guide_view_3354232.html
3. **撮合引擎模块化参考 vnpy_paperaccount**：委托队列、部分成交、撤单、资金/持仓/历史委托分离，为后续交割单分析提供更细粒度流水。来源：https://github.com/vnpy/vnpy_paperaccount
4. **分析师模拟竞赛/排行榜**：把 5 位分析师虚拟账户做成可对比的排行（周/月收益率、胜率、最大回撤），参照幻银量化 A 股 Ai 炒股大赛排行机制。来源：https://github.com/hyqibot/A-share-Ai
5. **调仓记录/跟投展示**：借鉴雪球组合的调仓记录展示形态（每次调仓留痕、可回溯）。来源：https://xueqiu.com/4226803442/181740768

### P1 数据展示（3-5 条）
1. **复盘看板一屏化**：涨停池/连板梯队/龙虎榜/板块资金同屏，参照 vibe-astock 的看板布局。来源：https://github.com/simonlin1212/vibe-astock
2. **情绪指标明细化展示**：昨日涨停表现/红盘率/连板率/炸板率/赚钱效应逐项展示（我们已定义口径，可加多日趋势小图）。来源：https://caifuhao.eastmoney.com/news/20211006201601579083920 ；https://github.com/quantskills/skill-b6-limitup-pool
3. **全流程可视化**：15-agent 级联流程可视化（TradingAgents-AShare 卖点），我们已有任务进度条，可加「分析师观点来源」高亮。来源：https://github.com/KylinMountain/TradingAgents-AShare
4. **题材/涨停原因标注**：涨停股标注题材归属与涨停原因（选股宝盯盘辅助、淘股吧涨停原因讨论的做法）。来源：https://baijiahao.baidu.com/s?id=1617532478194750937 ；https://www.55188.com/search-涨停原因.html
5. **情绪周期可视化**：开盘啦式「情绪周期 + 关键转折点」图形化。来源：https://www.kaipanla.com/article/39810

### 体验（3-5 条）
1. **自然语言问股入口**：把分析师聊天做成「问财/妙想式」的自然语言入口（问个股/板块/复盘结论），对齐商业产品交互。来源：https://baike.baidu.com/item/i问财/61779154 ；https://www.aixq.cc/28961.html
2. **免 API Key / 全本地运行模式**：像 vibe-astock 一样支持「指标本地计算 + 仅写作环节可选用 AI」，降低使用门槛。来源：https://github.com/simonlin1212/vibe-astock
3. **IM 推送**：复盘完成推送到飞书/微信（quantdash-ai-stock 集成飞书机器人）。来源：https://github.com/rancy777/quantdash-ai-stock
4. **结构化报告输出 + 程序校验**：报告正文用冻结模板 + 结构化 JSON 约束（我们 PLAYBOOK 2026-08-12「推荐行格式失控」已采用同思路；公开可参考复盘 Skill 模板沉淀）。来源：https://clawhub.ai/laojun509/skills/a-share-daily-review
5. **舆情/异动简报开关内能力**：把韭研公社/见微数据式舆情抓取做成可选项（对应我们 NEWS_ENABLED 开关思路），需要时打开。来源：https://github.com/Mingdi-hub/jiuyan-stock-sentiment-skill ；https://www.jindage.com/en/skills/jiuyan-yidong-market

---

## 四、数据源现状结论

> 结合本次公开资料调研 + 本项目 PLAYBOOK/requirements 实测记录，结论分级：✅稳定可用 / ⚠️可用但有门槛或偶发不稳定 / ❌已失效或不可用。

| 数据源 | 现状 | 结论 | 来源 |
|---|---|---|---|
| 东财 push2 / push2his（行情/涨停池/龙虎榜/板块） | A 股免费数据主力源；akshare 大量接口（stock_zh_a_spot_em 等）背后就是东财，东财偶发接口失效/限流；本项目实测曾遇 push2 全系 502 | ✅ 主力源（需备用降级） | akshare issue https://github.com/akfamily/akshare/issues/6574 ；https://github.com/akfamily/akshare/issues/6106 ；本项目 PLAYBOOK「东财 push2 全系 502」条目 |
| akshare（聚合库） | 活跃维护但**接口高频失效**（stock_zh_a_hist、日线下载等 issue 持续出现），本质是上游源（东财/新浪/腾讯）的封装，上游一变就断 | ⚠️ 好用但必须接降级链；同花顺系接口依赖 py_mini_racer JS 引擎（本项目已 stub 掉） | https://github.com/akfamily/akshare/issues/6092 ；https://github.com/akfamily/akshare/issues/5769 ；https://github.com/akfamily/akshare/issues/5820 |
| tushare | 积分制：K 线类免费，多数高频/财务接口需积分与 token；token 入 .env（本项目已如此） | ⚠️ 稳定但权限门槛 | https://tushare.pro/ ；积分规则 https://blog.csdn.net/geniusChinaHN/article/details/148023510 |
| 腾讯 qt.gtimg.cn | 轻量实时行情备用源，字段有限（无成交额、历史深度浅）；本项目实测稳定可用 | ✅ 稳定备用 | 行情接口收集帖 https://app.jisilu.cn/question/449583 ；本项目 PLAYBOOK「腾讯备用」条目 |
| 新浪 hq.sinajs.cn | 需 referer 校验（Referer: finance.sina.com.cn），服务器/ECS 环境常被拒；新浪 7x24 快讯仍可用 | ⚠️ 受限（本项目已用新浪 7x24 快讯，行情不用） | https://developer.aliyun.com/ask/694287 ；https://cloud.tencent.cn/developer/article/2393955 |
| 通达信协议（tdxpy / pytdx / mootdx） | 新版主站对未认证客户端返回**哑数据**；本项目实测 167 台主站仅 22 台返回真实数据，已固化白名单；分时/逐笔/历史分时仍无法解析 | ⚠️ 可用但需主站白名单；适合日K/分钟K/五档兜底 | 本项目 PLAYBOOK「通达信主站认证升级」条目；同族实现 https://github.com/mootdx/mootdx ；https://github.com/new-economy/tdx |
| 开盘啦 kaipanla | App/网页接口需登录 Cookie（易过期）+ 签名；公开爬取研究多，但官方无公开 API | ⚠️ 竞价数据唯一较好来源（本项目 Q1 已按 Cookie 设计），Cookie 失效需重抓 | https://blog.csdn.net/wx_ai476118/article/details/137974489 ；https://www.52pojie.cn/forum.php?mod=viewthread&tid=1986719 ；https://github.com/yueshushu0207/kaipanla |
| 北向资金实时数据 | **2024-08-19 起沪深港通披露机制调整，北向资金不再实时/每日披露**（改为季度披露持股），实时指标已死 | ❌ 不能作为实时指标（本项目已停用，以涨跌家数/昨日涨停表现替代） | https://finance.eastmoney.com/a/202408183158724049.html ；https://finance.eastmoney.com/a/202408263165166650.html |
| 免费 API 整体风险 | 「A 股免费 API 最危险的地方：不是不能用，而是错了你不知道」——静默返回错值比断连更危险 | ⚠️ 必须多源交叉 + 量级校验（与本项目降级链纪律一致） | https://tickdb.ai/blog/general/a-股免费-api-最危险的地方不是不能用而是错了你不知道-mpxqdexw |

### 数据源策略小结（对项目负责人的建议）
1. **主源矩阵**：东财 push2 系（行情/涨停池/龙虎榜/板块资金）+ 腾讯（实时备用）+ 通达信协议白名单（日K/分钟K/五档兜底）+ akshare 聚合（指数/板块/竞价之外的长历史）——与本项目现有降级链一致，方向正确，需持续维护主站白名单与接口参数。
2. **竞价数据**：开盘啦 Cookie 仍是当前最可行的免费方案（门槛 = Cookie 易过期 + 可能风控），建议在 UI 上提示「Cookie 失效」并及时降级到同花顺竞价页/东财分时推算（本项目已有该降级链）。
3. **历史数据**：东财涨停池约 30 交易日限制（本项目已标注），更长历史需 tushare（积分）或自建存储逐日累积。
4. **风险提示**：任何免费源都可能静默错值（akshare 曾把成交量列当成交额——本项目 2026-08-12 实测踩坑），坚持「多源交叉 + 量级校验 + 明确标注降级」铁律。

---

## 附：调研说明与待验证清单
- 本次调研基于 2026-08-13 的 web_search 结果；各项目/产品的**当前维护状态、star 数、付费策略**等动态信息以链接页面为准，标注「待验证」的细节未逐一实测。
- 未编造任何项目或链接；表中每个条目均有来源 URL。
- 项目内部事实（数据源降级链、情绪指标口径、模拟撮合现状、NEWS_ENABLED 开关等）引用自 `docs/requirements.md` 与 `docs/PLAYBOOK.md`。
