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
│   ├── 2_机会扫描.py        # P2: 全市场机会扫描（分析）
│   ├── 3_策略回测.py        # P3: 策略回测（策略发现）
│   ├── 4_复盘回顾.py        # P4: 复盘回顾（P2+P3 双轨复盘）
│   ├── 5_政治信号.py        # P5: 政治信号跟踪（特朗普家族信号链）
│   └── 6_机构持仓.py        # P6: 机构持仓跟踪（13-F 季报分析）
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
│   │   ├── strategies.py   # 策略类定义（16 种配置策略）
│   │   ├── temperature.py  # 市场温度指标+七档状态机+吊灯止损
│   │   ├── kelly.py        # 分数凯利仓位配比（μ/σ²）
│   │   └── regime_config.py # 市场体制判定阈值配置
│   ├── scanner/            # 全市场扫描器
│   │   ├── scorer.py       # 多维评分（趋势/均值回归/风险）
│   │   ├── sector_alert.py # 板块异常检测（动量骤降/波动率飙升/相对走弱）
│   │   ├── history.py      # 扫描历史持久化（ScanHistory → scan_history 表）
│   │   └── renderer.py     # 扫描结果渲染组件
│   ├── backtest/           # 回测引擎
│   │   └── engine.py       # BacktestEngine + BacktestResult
├── data/                   # 本地数据缓存 (SQLite)
├── scripts/                # 工具脚本
│   ├── daily_sync.py       # 每日数据同步（价格+宏观+基金+扫描+13-F+告警+飞书推送）
│   ├── sweep_antifragile.py # 反脆弱策略参数扫描
│   ├── com.market-pulse.daily-sync.plist # macOS launchd 定时任务配置
│   └── test_data.py        # 数据层验证脚本
├── docs/                   # 项目文档
└── tests/                  # 测试（待实现）
```

## 数据源

- **美股/ETF**: Twelve Data API (免费计划，每分钟 8 次限制)
  - 大类资产：SPY (标普500), QQQ (纳指100), TLT (20年国债), CL (WTI原油), GLD (黄金ETF)
  - 板块 ETF：XLK(科技), XLF(金融), XLE(能源), XLV(医疗), XLI(工业), XLP(消费必需), XLU(公用事业), XLRE(房地产), XLB(材料), XLC(通信)
  - API key: 环境变量 `TWELVEDATA_API_KEY` 或 `~/.local/bin/finquote.conf`
- **A股指数**: AkShare 新浪源（东方财富被墙）
  - 399006 (创业板指), 000688 (科创50), 000300 (沪深300)
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
- [x] P2 全市场机会扫描 (pages/2_机会扫描.py) — 多维评分扫描+机会发现+规避清单，扫描结果自动持久化到 scan_history 表
- [x] 全市场扫描器 (core/scanner/) — 趋势/均值回归/风险三维评分+ScanHistory持久化，对应 P3 的 11 种策略
- [x] 回测引擎 (core/backtest/engine.py) — 月度再平衡，按日计算组合收益
- [x] 策略类 (core/strategy/strategies.py) — 16 种策略：趋势跟踪/双动量/动量+波动率过滤/回撤控制/温度趋势轮动/温度趋势轮动V2/双动量×凯利/动量波动率×凯利/温度轮动×凯利/均值回归/风险平价/低相关性组合/尾部风险平价/回撤约束优化/反脆弱/反脆弱激进版
- [x] 市场温度框架 (core/strategy/temperature.py) — 七档 冻-寒-凉-平-温-热-沸，四维固定阈值映射（趋势质量/趋势结构/动量加速/新高贴近度），状态机+吊灯止损+组合回撤红线
- [x] 分数凯利配比 (core/strategy/kelly.py) — w=clip(k·μ̂/σ², 0, 20%)，1/4 凯利默认，单票硬顶+总仓位压缩
- [x] P3 策略回测页面 (pages/3_策略回测.py) — 策略对比与历史回测
- [x] P4 复盘回顾页面 (pages/4_复盘回顾.py) — 双轨复盘：市场策略推荐+机会扫描分类准确性评估+策略推荐跑赢率评估，含评分区间命中率分析和参数优化建议
- [x] P5 政治信号跟踪页面 (pages/5_政治信号.py) — 特朗普家族信号链跟踪：持仓变动/公开喊话/催化剂事件，信号链视图+全部时间线+走势图叠加。数据源：SEC EDGAR + White House PTR + Google News RSS + 白宫RSS，增量更新
- [x] P6 机构持仓跟踪 (pages/6_机构持仓.py) — 13-F 季度持仓跟踪：20 家顶级机构，持仓概览（共识重仓标的汇总+各机构 Top10 treemap）+增持信号（板块分布概括+高共识标的+Top 5 详情图表）+跨机构对比+管理人管理。数据源：SEC EDGAR 13F-HR，CIK submissions API，每日检查
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

### 待开发（按优先级排序）
1. [ ] P4 增强 — 基准对比（vs SPY）、按体制分组分析（盈亏比/regret_rate 已实现）

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

## 已知限制

- Twelve Data 免费计划：每分钟 8 次请求，页面只能从数据库读取
- AkShare 东方财富源被墙，使用新浪源
- plotly 导出图片需要 kaleido 包
- White House PTR PDF 是扫描件，OCR 质量有限，部分日期/金额可能解析不完整
- Google News RSS 自动抓取的新闻信号方向（bullish/bearish）是关键词猜测，标记为 unverified，需人工确认

## 风控红线

- 个股止损线: -8%
- 组合回撤: -10% 减半仓，-15% 清仓
- 单票仓位: 不超过总仓位 20%

## 已知陷阱

- **不要删根目录 `app.py`**：Streamlit Cloud 的 Main file path 仍是 `app.py`，删掉会整站 "Oh no, error running app"。`app.py` 只是 exec `0_主页.py` 的兼容层。Python 里 `app/` 包优先于同名模块，`from app.components...` 不受 `app.py` 影响
- **温度公式不要用滚动百分位做主维度**：成熟趋势会被自身历史排名「降温」（2021 牛市误判平/寒）。四维用固定阈值映射，滚动百分位只用于波动率泡沫检测
- **温度策略周频换手是收益杀手**：314 次周频再平衡 ≈ 15%+ 成本拖累。V1/V2/凯利版都是控回撤有效但绝对收益为负，参数未扫完前不要上真钱
- **吊灯止损 k 要匹配持仓周期**：k=3 对 2 个月-3 年持有太紧（3% 回撤就打出去），已改 k=4.0（浮盈 15% 后 2.5，沸 3.0），ATR(20)
- **凯利在长仓无杠杆下经常 >100%**：必须 clip 单票 20% + 总仓位 1.0，真正生效的是相对配比不是绝对仓位
- **单策略的组合 DD 重放用基础权重路径**：generate_weights 每次独立重放历史，禁止把 entry/peak/stop 挂在 self 上（引擎 except 时会失步）

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
- **Twelve Data 免费计划不支持宏观端点**：`/cpi`、`/ppi`、`/treasury_yield`、`/fedfunds`、`/unemployment` 返回 404，必须用 AkShare 的 `macro_usa_*` 和 `bond_zh_us_rate` 函数
- **QQQ AkShare 数据有 2005-2011 缺口**：`stock_us_daily(symbol='QQQ')` 缺少 2005~2011 数据（QQQ 曾改名 QQQQ），前向收益率计算对 2008 年相似月份返回 NaN
- **AkShare 宏观函数偶尔 SSL 报错**：`datacenter-api.jin10.com` 偶发 SSLError，fetch_all 已有 try/except 容错，单个指标失败不影响其他
- **反脆弱策略已全面切换到 BS 公允价值定价**：所有策略用 `use_bs_cost=True`，引擎用 `_bs_put_premium()` 动态计算 OTM put 权利金。赔付函数 `_calc_option_payoff()` 只用到期日内在价值（无时间价值虚增）。买保险有成本，对冲策略收益低于纯双动量是正常的
- **AkShare 基金净值日期是 datetime.date 对象**：`fund_open_fund_info_em` 返回的"净值日期"列是 `datetime.date` 不是字符串，`DataStorage.save()` 期望 datetime 列（用 `.dt.strftime`），需要先 `pd.to_datetime()` 转换
- **Streamlit `st.info()` 的 `icon` 参数只接受单个 emoji 字符**：不接受 shortcode（如 `:warning:`），不接受多字符字符串。用 `st.info("text", icon="⚠️")` 而不是 `icon=" "`
- **13-F XML 命名空间不一致**：不同机构的 13F XML 使用不同命名空间前缀，解析时一律用 `tag.split("}")[-1]` 去掉命名空间
- **13-F 同一 CUSIP 可能出现多次**：同一 filing 中同一 CUSIP 可能有多个 infoTable 条目（不同 otherManager/子账户），必须按 CUSIP 聚合 value 和 shares 后再存储
- **13-F value 单位是美元**：XML 中 `<value>` 的单位是整美元（不是千美元），与某些旧文档描述不同
- **13-F 有季度延迟**：机构在季度结束后 45 天内提交，数据不是实时的
- **13F-HR/A 是修订版**：同一家机构可能提交多个 13F-HR/A 修订，find_latest_13f 优先取最新的
- **飞书 Webhook 不支持 `collapsible` 标签**：`send_card()` 的 elements 中不能用 `{"tag": "collapsible"}`，会返回 `unsupported type of block`。用多个 markdown 段 + `hr` 分割线代替
