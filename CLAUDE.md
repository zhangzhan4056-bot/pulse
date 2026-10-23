# 市场脉搏观测网站 (Market Pulse)

A股 + 美股投资监控与决策辅助系统。

## 投资框架

双重动量 + 核心卫星策略，持仓周期 2个月-3年，保本优先。

## 项目结构

```
market-pulse/
├── 0_主页.py               # Streamlit 主入口（首页/仪表盘）
├── app.py                  # Cloud 入口兼容层（Main file path 仍是 app.py，exec 0_主页.py）
├── strategies_guide.html   # 策略学习文档（自包含 HTML，SVG 图表）
├── 策略研究学习手册.html     # 策略研究入门手册（8章，13策略，12指标）
├── 反脆弱策略学习指南.md     # 反脆弱策略深度学习指南
├── app/                    # 前端组件
│   └── components/
│       ├── charts.py       # Plotly 图表组件
│       ├── macro_charts.py # 宏观观测图表（散点图+时间线）
│       └── tooltips.py     # 指标说明 tooltip 组件（CSS hover 弹窗）
├── pages/                  # Streamlit 页面（必须在根目录）
│   ├── 1_市场全景.py        # P1: 市场全景（盯盘）
│   ├── 2_机会扫描.py        # P2: 全市场机会扫描（门槛8% 口径，结论先行）
│   ├── 3_策略回测.py        # P3: 策略回测（策略发现）
│   ├── 4_复盘回顾.py        # P4: 复盘回顾（P2+P3 双轨，结论先行）
│   ├── 5_政治信号.py        # P5: 政治信号跟踪（特朗普家族信号链，结论先行）
│   └── 6_机构持仓.py        # P6: 机构持仓跟踪（13-F 季报，结论先行）
├── core/                   # 核心逻辑
│   ├── data/               # 数据获取层
│   │   ├── config.py       # API key 管理、资产配置
│   │   ├── base.py         # 基础获取器（重试、限流）
│   │   ├── twelvedata.py   # Twelve Data API 封装
│   │   ├── akshare.py      # AkShare 封装（新浪源）
│   │   ├── storage.py      # SQLite 存储层
│   │   ├── manager.py      # DataManager 统一接口
│   │   ├── macro_fetcher.py  # 宏观数据获取器（AkShare）
│   │   ├── macro_storage.py  # 宏观数据存储（macro_daily 表）
│   │   ├── macro_analyzer.py # 宏观相似度分析（百分位排名+欧氏距离+PCA）
│   │   ├── signal_storage.py # 政治信号存储（political_signals 表）
│   │   ├── signal_fetcher.py # 政治信号自动获取器（SEC EDGAR + White House PTR OCR）
│   │   ├── thirteen_f_storage.py # 13-F 机构持仓存储（thirteen_f_holdings 表）
│   │   ├── thirteen_f_fetcher.py # 13-F 数据获取器（SEC EDGAR submissions API + XML 解析）
│   │   ├── event_calendar.py # 宏观事件日历（FOMC/NFP/CPI/PPI/三巫日/财报）
│   │   ├── alert_storage.py  # 告警存储（alerts 表）
│   │   ├── alert_evaluator.py # 告警评估器（汇总所有风险源）
│   │   └── notifier.py       # 飞书 Webhook 推送
│   ├── strategy/           # 策略引擎
│   │   ├── indicators.py   # 技术指标计算
│   │   ├── strategies.py   # 策略类定义（P3 注册 20 种；退役类仍保留定义）
│   │   ├── trend_follow.py # 趋势跟随信号（TSMOM/Donchian/吊灯，纯函数）
│   │   ├── kelly.py        # 分数凯利仓位配比（μ/σ²）
│   │   └── regime_config.py # 市场体制判定阈值配置
│   ├── scanner/            # 全市场扫描器
│   │   ├── scorer.py       # 门槛8%扫描（12M>8% 过门槛/门槛附近/不碰）
│   │   ├── sector_alert.py # 板块异常检测（动量骤降/波动率飙升/相对走弱）
│   │   ├── history.py      # 扫描历史持久化（ScanHistory → scan_history 表）
│   │   └── renderer.py     # 扫描结果渲染组件
│   ├── backtest/           # 回测引擎
│   │   └── engine.py       # BacktestEngine + BacktestResult
├── data/                   # 本地数据缓存 (SQLite)
├── scripts/                # 工具脚本
│   ├── daily_sync.py       # 每日数据同步（价格+宏观+基金+扫描+13-F+告警+飞书推送+月初月报）
│   ├── export_target_weights.py # 月度调仓单（老虎/境内双账户，A2 回撤刹车）
│   ├── monthly_report.py   # 月初飞书月报（持仓变动+近一年结构+权益/现金仓位）
│   ├── sweep_antifragile.py # 反脆弱策略参数扫描
│   ├── report_growth_rotation.py # 成长动量 vs 满仓QQQ/SPY长持 vs 凯利·门槛8% 对照报告
│   ├── com.market-pulse.daily-sync.plist # macOS launchd 定时任务配置
│   └── test_data.py        # 数据层验证脚本
├── docs/                   # 项目文档
└── tests/                  # 单元测试（test_kelly / test_trend_follow）
```

## 数据源

- **美股/ETF**: Twelve Data API (免费计划，每分钟 8 次限制)
  - 大类资产：SPY (标普500), QQQ (纳指100), EFA (发达市场), EEM (新兴市场), TLT (20年国债), SHY (1-3年短债/现金代理), CL (WTI原油), GLD (黄金ETF)
  - 板块 ETF：XLK(科技), XLF(金融), XLE(能源), XLV(医疗), XLI(工业), XLP(消费必需), XLY(可选消费), XLU(公用事业), XLRE(房地产), XLB(材料), XLC(通信)
  - 策略宇宙 `STRATEGY_UNIVERSE`（31 标的）= 大类+国际+A股行业+美股板块，ETF 策略共用；未配满仓位自动买入 `CASH_PROXY=SHY`
  - 个股宇宙 `US_STOCK_UNIVERSE`（55 只，2019 年市值前列+落后股）— 仅「成长动量·个股」使用，**不进 STRATEGY_UNIVERSE**；引擎按 `get_all_symbols()` 过滤，避免个股污染 ETF 策略
  - API key: 环境变量 `TWELVEDATA_API_KEY` 或 `~/.local/bin/finquote.conf`
- **A股指数**: AkShare 新浪源（东方财富被墙）
  - 399006 (创业板指), 000688 (科创50), 000300 (沪深300)
  - 行业：399813 (国证半导体), 399986 (中证银行), 399989 (中证医疗), 000932 (中证消费), 399396 (国证有色), 399809 (煤炭), 399393 (国证地产)
- **全球指数**: AkShare 新浪源
  - 日经225, KOSPI (韩国KOSPI)
- **美国宏观数据**: FRED CSV（免费，无限速）
  - CPI 同比 (`CPIAUCSL`): 月度，1990 起
  - PPI (`WPSFD4131`): 月度，1990 起
  - 10Y 国债收益率 (`GS10`): 月度，1990 起
  - 联邦基金利率 (`FEDFUNDS`): 月度，1990 起
  - 失业率 (`UNRATE`): 月度，1990 起
  - VIX 恐慌指数 (`VIXCLS`): 日度，1990 起
- **A股场外基金**: AkShare 东方财富源
  - 011369 (中际旭创混合), 000979, 012922, 457001 — 光模块/AI 半导体相关基金
  - 函数: `fund_open_fund_info_em(symbol, indicator='单位净值走势')`
- **政治信号数据**: SEC EDGAR + White House PTR + Google News RSS
  - SEC EDGAR Form 4: 特朗普家族内幕交易披露（API 自动获取）
  - White House PTR: 总统定期交易报告（PDF OCR 解析，较慢）
  - OGE 278e: 年度财务披露（PDF 上传解析）
  - Google News RSS: Trump 相关新闻自动抓取（无 API key，覆盖主流媒体）
  - 白宫 RSS: 行政令和官方声明（presidential-actions/feed）
- **机构持仓数据**: SEC EDGAR 13F-HR 季度报告
  - 20 家顶级机构（Berkshire、Bridgewater、Citadel、Point72 等）
  - CIK-based submissions API 获取最新报告
  - XML 解析 infoTable 提取持仓明细（按 CUSIP 聚合子账户）
  - 增持信号：连续季度 shares+weight 双增
  - 季度趋势对比：`get_quarterly_signals()` 计算两个独立季度变动（Q-2→Q-1 vs Q-1→Q0），用于判断资金流向趋势和速度
  - 板块分类：`_classify_sector()` 按 issuer 名匹配 10 个板块（科技/半导体、金融、医疗、能源等），支持词边界正则
  - 飞书通知：资金流向分析（双季度净金额对比）+ AI 产业链重点信号 + 价值型机构增减持明细

## 开发状态

### 已完成
- [x] 数据获取层 (core/data/)
- [x] P1 市场全景页面 (pages/1_市场全景.py)
- [x] 策略引擎 - 技术指标 (core/strategy/indicators.py)
- [x] P2 全市场机会扫描 (pages/2_机会扫描.py) — 门槛8% 口径：过门槛候选/门槛附近/不碰，结论先行，扫描结果自动持久化到 scan_history 表
- [x] 全市场扫描器 (core/scanner/) — 12M>8% 门槛分组 + 凯利参考仓位（扫描口径独立于生产策略「成长动量·个股」）
- [x] 回测引擎 (core/backtest/engine.py) — 月度再平衡，按日计算组合收益
- [x] 策略类 (core/strategy/strategies.py) — P3 注册 21 种，分四类：趋势跟随（TSMOM/CTA突破/动量吊灯/均线基线）、趋势动量（双动量系列+凯利+成长动量+满仓长持基准）、风险配置、反脆弱
- [x] 趋势跟随框架 (core/strategy/trend_follow.py) — TSMOM 12月符号 / Donchian 55-20 突破 / 吊灯移动止损，无状态重放，与双动量（横截面选强）角色分工
- [x] 分数凯利配比 (core/strategy/kelly.py) — w=clip(k·μ̂/σ², 0, 20%)，策略用 k=0.5（1/2 凯利），单票硬顶+总仓位压缩；闲钱由引擎买入 SHY
- [x] P3 策略回测页面 (pages/3_策略回测.py) — 策略对比与历史回测
- [x] P4 复盘回顾页面 (pages/4_复盘回顾.py) — 双轨复盘：机会扫描分类准确性评估+生产策略跑赢率评估（vs SPY），含按体制分组、评分区间命中率分析和参数优化建议；当前市场体制/策略推荐只在 P2 顶部与主页，P4 不重复
- [x] P5 政治信号跟踪页面 (pages/5_政治信号.py) — 特朗普家族信号链跟踪：持仓变动/公开喊话/催化剂事件，近 30 天结论横幅 + 信号链视图 + 全部时间线 + 走势图叠加。数据源：SEC EDGAR + White House PTR + Google News RSS + 白宫RSS，增量更新
- [x] P6 机构持仓跟踪 (pages/6_机构持仓.py) — 13-F 季度持仓跟踪：20 家顶级机构，页级结论 + 持仓概览（共识结论 + 各机构 Top10 treemap）+增持信号（结论 + 板块分布 + 高共识 + Top 5 详情）+跨机构对比+管理人管理。数据源：SEC EDGAR 13F-HR，CIK submissions API，每日检查
- [x] P1 宏观观测模块 — CPI/PPI/10Y国债/联邦基金利率/失业率/VIX，百分位排名+欧氏距离相似度匹配，散点图+时间线可视化
- [x] 风险预警系统 — 五层防线：宏观事件日历+VIX监控+板块异常检测+AI叙事新闻监控+飞书推送
  - 宏观事件日历 (core/data/event_calendar.py) — FOMC/NFP/CPI/PPI/三巫日/重要财报，风险窗口标记
  - VIX 恐慌指标 — FRED VIXCLS 日频数据，颜色编码（<15平静/15-20正常/20-25警惕/>25恐慌）
  - 板块异常检测 (core/scanner/sector_alert.py) — 动量骤降/波动率飙升/相对走弱/排名跳水
  - AI叙事新闻监控 — SECTOR_NEWS_QUERIES 扩展 Google News RSS，监控 capex/demand/slowdown 关键词
  - 告警引擎 (core/data/alert_evaluator.py) — 汇总所有信号源生成告警
  - 飞书 Webhook 推送 (core/data/notifier.py) — 环境变量 FEISHU_WEBHOOK_URL
  - 持仓关注 — A股场外基金(011369/000979/012922/457001)净值监控，AkShare fund_open_fund_info_em
  - 首页风险预警横幅 (0_主页.py) — 未读告警颜色编码显示
  - P1 风险面板 — VIX卡片+事件日历+持仓关注+板块异常信号
- [x] 指标说明 Tooltip 系统 (app/components/tooltips.py) — 全页面 hover 弹窗，覆盖综合评分/市场体制/动量/趋势/回归/风险/技术指标/回测指标/复盘指标/宏观指标共 30 个常量
- [x] 告警去重与主题分组 — 同板块多基金合并为一条告警（FUND_THEMES 映射），存储层 12 小时时间窗口去重（save_dedup）
- [x] 月初飞书月报 (scripts/monthly_report.py) — 成长动量·个股持仓变动+近一年结构+权益/现金仓位；daily_sync 每自然月首次自动推送；权重与 export_target_weights 共用 build_monthly_allocation
- [x] 生产策略切换为「成长动量·个股」(2026-10) — StockMomentumGrowthStrategy，55 股 3M 动量 top12 满仓，无 A2，回撤预算 −40%；月报/调仓单只输出这一套。前生产「凯利·门槛8%」保留为对照
- [x] 成长动量·个股 (2026-10) — StockMomentumGrowthStrategy，55 股 3M 动量 top12 满仓。引擎实测 2019-2025：CAGR 27.9% / DD −34.8% / Calmar 0.80（QQQ 21.8% / −35.6% / 0.61）。**3 年滚动 ≥26% 仅 29% 窗口**，是「经常够」不是「稳稳」
- [x] 基准策略 (2026-10) — 满仓QQQ长期持有 / 满仓SPY长期持有，对照主动策略是否值得折腾

### 待开发（按优先级排序）
1. [ ] 温度趋势轮动策略（Temperature Trend Rotation）— 七档温度状态机 + 吊灯止损（设计已定稿待实现）

## 运行方式

```bash
# 安装依赖
pip3 install -r requirements.txt

# 设置 API key
export TWELVEDATA_API_KEY="your_key"

# 设置飞书 Webhook（可选，用于告警推送）
export FEISHU_WEBHOOK_URL="https://open.feishu.cn/open-apis/bot/v2/hook/xxx"

# 启动应用
streamlit run 0_主页.py

# 每日数据同步（含告警评估+飞书推送）
python scripts/daily_sync.py
```

## 开发规范

- Python 3.9+（不支持 `str | None`，用 `Optional[str]`）
- 前端: Streamlit（pages 目录必须在项目根目录）
- 图表: Plotly
- 数据库: SQLite
- 版本控制: git，功能分支开发后合并
- 提交信息格式: `feat: xxx` / `fix: xxx` / `docs: xxx`
- **页面版式：结论先行、明细往后**——顶部先给一句话结论/共识/该看谁，再展开图表和明细表

## 已知限制

- Twelve Data 免费计划：每分钟 8 次请求，页面只能从数据库读取
- AkShare 东方财富源被墙，使用新浪源
- plotly 导出图片需要 kaleido 包
- White House PTR PDF 是扫描件，OCR 质量有限，部分日期/金额可能解析不完整
- Google News RSS 自动抓取的新闻信号方向（bullish/bearish）是关键词猜测，标记为 unverified，需人工确认

## 风控红线

- 个股止损线: -8%（趋势类策略默认无紧止损，见已知陷阱）
- 组合回撤: **−40%**（2026-10 放宽，接受成长动量量级回撤；无自动刹车，深回撤人工决策）
- 单票仓位: 不超过总仓位 20%
- 合规: 金融从业人员，**不买 A 股个股**，境内只用 ETF/场外基金

## 已知陷阱

- **12 月动量的吊灯止损必须月频评估**：周频会被正常回撤反复打出去（实测 +4% vs 月频 +64%）。12M 趋势要扛 10-15% 回踩，周内噪音不该触发离场。CTA 突破换手低，周频没问题
- **不要删根目录 `app.py`**：Streamlit Cloud 的 Main file path 仍是 `app.py`，删掉会整站 "Oh no, error running app"。`app.py` 只是 exec `0_主页.py` 的兼容层。Python 里 `app/` 包优先于同名模块，`from app.components...` 不受 `app.py` 影响
- **`core/data/__init__.py` 不许同步 import manager/signal_fetcher**：重依赖（akshare）一挂，`from core.data import STRATEGY_UNIVERSE` 整包 ImportError，页面报错行号会指到无关的 import。符号常量只从 `config` 导出；DataManager/抓取器用 PEP 562 延迟加载；`manager.__init__` 内再 import 抓取器。页面用 `from core.data.config import STRATEGY_UNIVERSE`
- **周频换手是收益杀手**：高频再平衡的成本拖累可达 15%+。趋势类信号（12M 符号/吊灯）必须月频评估；CTA 突破换手本身低，周频才可接受
- **吊灯止损 k 要匹配持仓周期**：k=3 对 2 个月-3 年持有偏紧，容易被 3% 级别回踩打出去。长持用更宽的 k（趋势动量吊灯用 k=6）
- **凯利在长仓无杠杆下经常 >100%**：必须 clip 单票 20% + 总仓位 1.0，真正生效的是相对配比不是绝对仓位
- **等权双动量 top3 = 33%/票，破 20% 红线**：要守红线用「双动量×凯利」（max_weight=0.20）；等权版回测收益更高但单票超限，不能直接上实盘
- **扩宇宙的主要收益来源是「填满 5 个 20% 名额」**：10 标的时常只有 2-3 个过绝对动量，20% 顶格下最多 40-60% 仓位。23 标的后平均仓位接近满仓，2021 从 +0% 修到 +5%。扩行业指数前先算相关，>0.85 是假分散（如军工~半导体 0.93）
- **单策略的组合 DD 重放用基础权重路径**：generate_weights 每次独立重放历史，禁止把 entry/peak/stop 挂在 self 上（引擎 except 时会失步）
- **不要把组合回撤红线做成自动日频/月频刹车**：对双动量×凯利实测，收益从 +81% 打到 +41~56%，回撤只少 2–3pp，夏普变差。慢熊里自动减仓=卖在下跌途中。**生产策略 = 成长动量·个股**（3M 动量 top12 满仓，`portfolio_dd_brake=False`，回撤预算 −40% 人工扛）；月报/调仓单只输出这一套。前生产「凯利·门槛8%」的 A2 月频 −15%/−20% 仅作对照保留
- **清仓后不要用「DD 收窄」当恢复仓位条件**：净值冻结后 `DD=NAV/峰值−1` 不会自己修复，会死锁在空仓（实测总收益掉到 +0.9%）。恢复条件要么 None（再平衡按当前 DD 重定档），要么用时间/信号，不能用 DD 修复
- **组合峰值必须逐日更新**：只在再平衡日 `peak=max(peak,nav)` 会丢掉月内高点，刹车几乎不触发
- **双账户实盘走「信号统一、执行拆分」**：没有零售平台能一个策略同时驱动老虎+境内券商。每月 `scripts/export_target_weights.py` 出 tiger.json + domestic.csv；A 股次日开盘前、美股当晚执行，两账户各自 20% 顶
- **月度调仓单必须按出单日 DD 定档，不能贴上月 weight_history**：月频刹车最多滞后一个月，月末满仓单在月中深回撤时会给出错误满仓。`export_target_weights.py` 用 `generate_weights(today)` + 当前模型 DD 重算 A2 档位
- **回测/调仓不要每次全量拉个股**：免费计划 8 次/分钟，55 只连打 = 转圈 7 分钟+。先 `us_stocks_need_refresh()` 判覆盖，只补缺数/过旧的；`fetch_us_stocks(symbols=...)` 定向拉。已有约 8 年历史（outputsize 上限）时再拉也延不了头部。P3 的 `GROWTH_UNIVERSE` 只在选中成长动量时加载

- **AkShare 用新浪源**：东方财富源（`*_em` 函数）被墙，一律用 `stock_zh_index_daily`、`index_global_hist_sina`
- **Twelve Data 免费计划**：每分钟 8 次，`quote` 端点支持的 symbol 比 `time_series` 少，测试新 symbol 用 `time_series`
- **国际指数用中文名查询**：`index_global_hist_sina` 的 symbol 是中文名（"日经225指数"），不是代码（"N225"）
- **Python 3.9 兼容**：不支持 `str | None`，用 `Optional[str]`
- **Streamlit pages 目录必须在项目根目录**：不能嵌套在 app/ 下
- **API key 配置文件格式**：`finquote.conf` 是 `key=value` 带注释，不能直接读整行当 key
- **Twelve Data `start_date/end_date` 参数有隐藏限制**：免费计划用日期范围参数只返回约 2 年数据，必须用 `outputsize` 参数才能拿到更长历史（本地数据是用 `outputsize` 积累的，线上用 `start_date` 会截断）
- **Streamlit Cloud 数据库是临时的**：每次重启丢失，P1 的「一键获取」和 P3 的 `prefetch_for_backtest()` 都必须覆盖所有数据源（美股+A股+全球），不能只处理一种
- **改完代码必须本地相关页面都跑一遍再 push**：不能只跑 P1 就认为没问题，P3 的 `st.columns(3)` 硬编码 bug 本地跑 P3 就能发现；策略/扫描改动至少跑 P2+P3+P4
- **P4 复盘只实例化在产策略**：`_get_all_strategies()` 必须与文件顶部 import 一致。退役类（DualMomentum6M/3M、MomentumVolFilter、DrawdownControl、RegimeSwitching）仍在 `strategies.py` 里，但 P4 引用未 import 的类会 NameError
- **反脆弱已全面切换 BS 公允价值定价**：`use_bs_cost=True`，`_bs_put_premium()` 动态算 OTM put 权利金，`_calc_option_payoff()` 只用到期日内在价值。标准版 cost=0.3%/月 + 5% OTM，激进版 0.5%/月 + 3% OTM，vol=20%。不要用旧线性公式 `excess_drop × leverage × cost`。买保险有成本，对冲收益低于纯双动量是正常的
- **Twelve Data 免费计划不支持宏观端点**：`/cpi`、`/ppi`、`/treasury_yield`、`/fedfunds`、`/unemployment` 返回 404，必须用 AkShare 的 `macro_usa_*` 和 `bond_zh_us_rate` 函数
- **QQQ AkShare 数据有 2005-2011 缺口**：`stock_us_daily(symbol='QQQ')` 缺少 2005~2011（QQQ 曾改名 QQQQ），前向收益率对 2008 年相似月份返回 NaN
- **AkShare 宏观函数偶尔 SSL 报错**：`datacenter-api.jin10.com` 偶发 SSLError，fetch_all 已有 try/except 容错

- **AkShare 基金净值日期是 datetime.date 对象**：`fund_open_fund_info_em` 返回的"净值日期"列是 `datetime.date` 不是字符串，`DataStorage.save()` 期望 datetime 列（用 `.dt.strftime`），需要先 `pd.to_datetime()` 转换
- **Streamlit `st.info()` 的 `icon` 参数只接受单个 emoji 字符**：不接受 shortcode（如 `:warning:`），不接受多字符字符串。用 `st.info("text", icon="⚠️")` 而不是 `icon=" "`
- **13-F XML 解析三件事**：命名空间前缀不一致（一律 `tag.split("}")[-1]`）；同一 CUSIP 可能多个 infoTable（按 CUSIP 聚合 value/shares）；`<value>` 单位是整美元。数据有季度延迟（季末后 45 天内提交），13F-HR/A 是修订版（find_latest_13f 取最新）
- **飞书 Webhook 不支持 `collapsible` 标签**：`send_card()` 的 elements 中不能用 `{"tag": "collapsible"}`，会返回 `unsupported type of block`。用多个 markdown 段 + `hr` 分割线代替

- **止损信号当日收盘确认，权重必须次日再生效**：用 `close[t]` 触发止损却把 `weight[t]` 清零 = 前视，回测虚高（实测假 55% CAGR）。信号在 t 收盘确认 → 权重从 t+1 起变
- **A2/DD 刹车不能加在个股动量上**：个股动量天然回撤 −30%+，月频 −15% 清仓后净值冻结、DD 不会自己修复，死锁空仓（实测 CAGR 33%→13%）。`StockMomentumGrowthStrategy.portfolio_dd_brake=False`
- **紧吊灯在个股趋势上是收益杀手**：k=3/4 会把 2023–2025 主升浪打飞（当年只剩 +6~15%）。引擎月频调权时盘中止损本就不生效，不要假装有
- **20% SPY/QQQ 防御腿几乎不降个股动量回撤**：−34.2% vs 满仓 −34.8%，却吃掉 2.8pp CAGR。默认 `stock_budget=1.0`
- **探针月末调仓 ≠ 引擎月初调仓**：2020–21 年收益可差 15pp，3 年滚动「≥26% 占比」会从 58% 掉到 29%。以引擎/`scripts/report_growth_rotation.py` 为准
