# A 股数据获取方案调研（2026-08-04）

## 结论摘要
1. **主流免费方案**：akshare（聚合，已用）/ efinance（东财纯 HTTP）/ adata（东财+新浪）/
   easyquotation（新浪/腾讯批量实时）/ pytdx-mootdx（通达信协议直连）/ baostock（日线）。
2. **盘中实时**：新浪 hq.sinajs.cn 与腾讯 qt.gtimg.cn 均为**批量实时**（无需 Cookie），
   是全市场/股票池行情的可靠源；东财 push2（HTTP 改写后）为行情/板块主源。
3. **需要 JS 签名/登录**的页面（同花顺 q.10jqka.com.cn、开盘啦 App）：
   浏览器方案（Playwright）或社区 Python 签名实现；本仓库已落地浏览器方案。
4. **通达信**：本地文件（陈旧）之外，pytdx/mootdx 可直接连行情服务器拿盘中
   tick/分钟/涨跌家数，无需 Cookie——作为后续增强（需验证代理连通性）。
5. 参考实现（已克隆到 .tmp/research/）：pytdx（archive，2018）、easyquotation（新浪/腾讯）。

## 落地映射
| 数据 | 本仓库源链 |
|---|---|
| 指数盘中 | 东财 ulist → 腾讯 qt → 通达信日K（含 push2his 盘中当日 bar） |
| 个股/批量行情 | 东财 stock/get → 腾讯 qt 批量 → 新浪 hq.sinajs.cn 批量 |
| 昨日涨停今日表现（盘中） | 东财 push2ex 昨日池 + 新浪/腾讯批量实时 |
| 涨跌家数（盘中） | 东财 clist 全市场统计（→ 后续 mootdx/浏览器） |
| 板块 | 东财 → 腾讯行业 → 浏览器同花顺 |
| 同花顺页面（竞价/家数） | 浏览器方案（Playwright，待办） |
