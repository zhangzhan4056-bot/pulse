# 市场脉搏观测网站 (Market Pulse)

A股 + 美股投资监控与决策辅助系统。

## 投资框架

双重动量 + 核心卫星策略，持仓周期 2个月-3年，保本优先。

## 项目结构

```
market-pulse/
├── app/                    # Streamlit 前端
│   ├── pages/
│   │   ├── 1_market_overview.py   # P1: 市场全景（盯盘）
│   │   ├── 2_opportunity.py       # P2: 机会扫描（分析）
│   │   ├── 3_decision.py          # P3: 操作建议（决策）
│   │   └── 4_review.py            # P4: 复盘回顾（回顾）
│   └── components/               # 可复用组件
├── core/                   # 核心逻辑
│   ├── data/               # 数据获取层
│   ├── strategy/           # 策略引擎
│   ├── backtest/           # 回测引擎
│   └── alert/              # 告警引擎
├── data/                   # 本地数据缓存 (SQLite)
├── tests/                  # 测试
├── docs/                   # 文档
└── scripts/                # 工具脚本
```

## 数据源

- 美股/ETF 实时 + 历史: Twelve Data API (key 在 ~/.local/bin/finquote.conf)
- A股数据: AkShare (东方财富)
- 宏观数据: FRED API (美国利率、通胀等)

## 开发规范

- Python 3.9+
- 前端: Streamlit
- 图表: Plotly
- 数据库: SQLite (起步) → PostgreSQL (规模大了)
- 版本控制: git，每次功能完成提交
- 提交信息格式: `feat: xxx` / `fix: xxx` / `docs: xxx`
- 不在 main 分支直接开发，功能分支开发后合并

## 风控红线

- 个股止损线: -8%
- 组合回撤: -10% 减半仓，-15% 清仓
- 单票仓位: 不超过总仓位 20%
