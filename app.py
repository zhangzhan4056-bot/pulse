"""Market Pulse - 市场脉搏观测网站

A股 + 美股投资监控与决策辅助系统
"""

import streamlit as st

st.set_page_config(
    page_title="首页 - Market Pulse",
    page_icon=" ",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("Market Pulse")
st.caption("市场脉搏观测 - 大类资产走势监控")

st.markdown("""
## 欢迎使用 Market Pulse

这是一个投资监控与决策辅助系统，帮助你追踪大类资产走势。

### 主要功能

- **市场全景**：实时查看美股和A股大类资产行情
- **机会扫描**：分析市场趋势，发现投资机会
- **操作建议**：基于策略生成交易建议
- **复盘回顾**：回顾历史交易，优化策略

### 数据源

- 美股/ETF：Twelve Data API（大类资产 + 板块 ETF）
- A股指数：AkShare 新浪源
- 全球指数：AkShare 新浪源（日经225、KOSPI）

---

  请从左侧导航栏选择功能页面。
""")
