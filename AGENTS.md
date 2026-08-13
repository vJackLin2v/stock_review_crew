# AGENTS.md（所有 agent 开工前必读）

## 项目速览
- stock_review_crew：A 股多智能体复盘系统。FastAPI 后端 + React 前端 + LangGraph 分析师流程 + 6 时间模式。
- 需求与**数据字典（字段唯一真源）**：`docs/requirements.md`（§六）
- 进度存档与 agent 登记：`docs/PROGRESS.md`
- **环境/工具/业务逻辑踩坑与解法：`docs/PLAYBOOK.md`（必读）**

## 铁律
1. 开工前先读 `docs/PLAYBOOK.md` 与 `docs/requirements.md`；
2. 遇到环境/工具/权限/网络问题，先查 PLAYBOOK，解决后**按模板追加新条目**；
3. 禁止：`git commit/push`、真实数据写入仓库、修改自己写权限之外的文件、自行安装依赖；
4. 测试用系统 `python -m pytest`（3.13，已装 pytest/fastapi/akshare；tushare 需 mock）；
   前端构建 `npm run build` 需提权（esbuild 沙箱 EPERM）；
5. 所有面向用户的投资内容必须带「仅供参考，不构成投资建议」；
6. 比率一律小数存储，前端 ×100；`None` 显示「—」；全中文展示；
7. 数据源降级链：主源→备用→缓存→标注「数据缺失/估算」，绝不静默错报。
8. **改动后必须跑一键回测**：`scripts/regression.py`（全量 pytest + 前端 build + API 契约 +
   聊天链路 + 无头浏览器桌面/手机冒烟），全绿方可交付；禁止「只跑单测就交付」。

## 服务（重构后）
- 本地：`python -m uvicorn backend.main:app --host 127.0.0.1 --port 8501`
