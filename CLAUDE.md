# 市场脉搏观测网站 (Market Pulse)

A股 + 美股投资监控与决策辅助系统。

## 投资框架

双重动量 + 核心卫星策略，持仓周期 2个月-3年，保本优先。

## 项目结构

```
market-pulse/
├── app.py                  # Streamlit 主入口（首页）
├── strategies_guide.html   # 策略学习文档（自包含 HTML，SVG 图表）
├── 反脆弱策略学习指南.md     # 反脆弱策略深度学习指南
├── app/                    # 前端组件
│   └── components/
│       └── charts.py       # Plotly 图表组件
├── pages/                  # Streamlit 页面（必须在根目录）
│   ├── 1_市场全景.py        # P1: 市场全景（盯盘）
│   ├── 2_机会扫描.py        # P2: 全市场机会扫描（分析）
│   ├── 3_策略回测.py        # P3: 策略回测（策略发现）
│   └── 4_复盘回顾.py        # P4: 复盘回顾（P2+P3 双轨复盘）
├── core/                   # 核心逻辑
│   ├── data/               # 数据获取层
│   │   ├── config.py       # API key 管理、资产配置
│   │   ├── base.py         # 基础获取器（重试、限流）
│   │   ├── twelvedata.py   # Twelve Data API 封装
│   │   ├── akshare.py      # AkShare 封装（新浪源）
│   │   ├── storage.py      # SQLite 存储层
│   │   └── manager.py      # DataManager 统一接口
│   ├── strategy/           # 策略引擎
│   │   ├── indicators.py   # 技术指标计算
│   │   └── strategies.py   # 策略类定义（11种配置策略）
│   ├── scanner/            # 全市场扫描器
│   │   ├── scorer.py       # 多维评分（趋势/均值回归/风险）
│   │   ├── history.py      # 扫描历史持久化（ScanHistory → scan_history 表）
│   │   └── renderer.py     # 扫描结果渲染组件
│   ├── backtest/           # 回测引擎
│   │   └── engine.py       # BacktestEngine + BacktestResult
│   └── alert/              # 告警引擎（待开发）
├── data/                   # 本地数据缓存 (SQLite)
├── scripts/                # 工具脚本
│   └── test_data.py        # 数据层验证脚本
└── tests/                  # 测试（待实现）
```

## 数据源

- **美股/ETF**: Twelve Data API (免费计划，每分钟 8 次限制)
  - 大类资产：SPY (标普500), QQQ (纳指100), TLT (20年国债), CL (WTI原油)
  - 板块 ETF：XLK(科技), XLF(金融), XLE(能源), XLV(医疗), XLI(工业), XLP(消费必需), XLU(公用事业), XLRE(房地产), XLB(材料), XLC(通信)
  - API key: 环境变量 `TWELVEDATA_API_KEY` 或 `~/.local/bin/finquote.conf`
- **A股指数**: AkShare 新浪源（东方财富被墙）
  - 399006 (创业板指), 000688 (科创50), 000300 (沪深300)
- **全球指数**: AkShare 新浪源
  - 日经225, KOSPI (韩国KOSPI)

## 开发状态

### 已完成
- [x] 数据获取层 (core/data/)
- [x] P1 市场全景页面 (pages/1_市场全景.py)
- [x] 策略引擎 - 技术指标 (core/strategy/indicators.py)
- [x] P2 全市场机会扫描 (pages/2_机会扫描.py) — 多维评分扫描+机会发现+规避清单，扫描结果自动持久化到 scan_history 表
- [x] 全市场扫描器 (core/scanner/) — 趋势/均值回归/风险三维评分+ScanHistory持久化，对应 P3 的 11 种策略
- [x] 回测引擎 (core/backtest/engine.py) — 月度再平衡，按日计算组合收益
- [x] 策略类 (core/strategy/strategies.py) — 11种策略：趋势跟踪/双动量/动量+波动率过滤/回撤控制/均值回归/风险平价/低相关性组合/尾部风险平价/回撤约束优化/反脆弱/反脆弱激进版
- [x] P3 策略回测页面 (pages/3_策略回测.py) — 策略对比与历史回测
- [x] P4 复盘回顾页面 (pages/4_复盘回顾.py) — 双轨复盘：市场策略推荐+机会扫描分类准确性评估+策略推荐跑赢率评估，含评分区间命中率分析和参数优化建议

### 待开发（按优先级排序）
1. [ ] P4 增强 — 风险调整指标（盈亏比/regret_rate）、基准对比（vs SPY）、按体制分组分析
2. [ ] 告警引擎 (core/alert/) — 扫描结果变化主动推送（Telegram/邮件）

## 运行方式

```bash
# 安装依赖
pip3 install -r requirements.txt

# 设置 API key
export TWELVEDATA_API_KEY="your_key"

# 启动应用
streamlit run app.py
```

## 开发规范

- Python 3.9+（不支持 `str | None`，用 `Optional[str]`）
- 前端: Streamlit（pages 目录必须在项目根目录）
- 图表: Plotly
- 数据库: SQLite
- 版本控制: git，功能分支开发后合并
- 提交信息格式: `feat: xxx` / `fix: xxx` / `docs: xxx`

## 已知限制

- Twelve Data 免费计划：每分钟 8 次请求，页面只能从数据库读取
- AkShare 东方财富源被墙，使用新浪源
- plotly 导出图片需要 kaleido 包

## 风控红线

- 个股止损线: -8%
- 组合回撤: -10% 减半仓，-15% 清仓
- 单票仓位: 不超过总仓位 20%

## 已知陷阱

- **AkShare 用新浪源**：东方财富源（`*_em` 函数）被墙，一律用 `stock_zh_index_daily`、`index_global_hist_sina`
- **Twelve Data 免费计划**：每分钟 8 次，`quote` 端点支持的 symbol 比 `time_series` 少，测试新 symbol 用 `time_series`
- **国际指数用中文名查询**：`index_global_hist_sina` 的 symbol 是中文名（"日经225指数"），不是代码（"N225"）
- **Python 3.9 兼容**：不支持 `str | None`，用 `Optional[str]`
- **Streamlit pages 目录必须在项目根目录**：不能嵌套在 app/ 下
- **API key 配置文件格式**：`finquote.conf` 是 `key=value` 带注释，不能直接读整行当 key
- **Twelve Data `start_date/end_date` 参数有隐藏限制**：免费计划用日期范围参数只返回约 2 年数据，必须用 `outputsize` 参数才能拿到更长历史（本地数据是用 `outputsize` 积累的，线上用 `start_date` 会截断）
- **Streamlit Cloud 数据库是临时的**：每次重启丢失，P1 的「一键获取」和 P3 的 `prefetch_for_backtest()` 都必须覆盖所有数据源（美股+A股+全球），不能只处理一种
- **改完代码必须本地三个页面都跑一遍再 push**：不能只跑 P1 就认为没问题，P3 的 `st.columns(3)` 硬编码 bug 本地跑 P3 就能发现
- **反脆弱对冲公式已改为简化 Black-Scholes**：用月末收盘价计算 SPY 月跌幅，BS 公式计算 OTM 月 put 赔付。标准版 cost=0.3%/月 + 5% OTM，激进版 cost=0.5%/月 + 3% OTM（经参数扫描优化）。vol=20%。不要用旧的线性公式 `excess_drop × leverage × cost`
