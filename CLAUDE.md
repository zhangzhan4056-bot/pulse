# 市场脉搏观测网站 (Market Pulse)

A股 + 美股投资监控与决策辅助系统。

## 投资框架

双重动量 + 核心卫星策略，持仓周期 2个月-3年，保本优先。

## 项目结构

```
market-pulse/
├── app.py                  # Streamlit 主入口（首页）
├── app/                    # 前端组件
│   └── components/
│       └── charts.py       # Plotly 图表组件
├── pages/                  # Streamlit 页面（必须在根目录）
│   ├── 1_市场全景.py        # P1: 市场全景（盯盘）
│   └── 2_机会扫描.py        # P2: 机会扫描（分析）
├── core/                   # 核心逻辑
│   ├── data/               # 数据获取层（已完成）
│   │   ├── config.py       # API key 管理、资产配置
│   │   ├── base.py         # 基础获取器（重试、限流）
│   │   ├── twelvedata.py   # Twelve Data API 封装
│   │   ├── akshare.py      # AkShare 封装（新浪源）
│   │   ├── storage.py      # SQLite 存储层
│   │   └── manager.py      # DataManager 统一接口
│   └── strategy/           # 策略引擎
│       └── indicators.py   # 技术指标计算
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
  - NKY (日经225), KOSPI (韩国KOSPI)

## 开发状态

### 已完成
- [x] 数据获取层 (core/data/)
- [x] P1 市场全景页面 (pages/1_市场全景.py)
- [x] 策略引擎 - 技术指标 (core/strategy/indicators.py)
- [x] P2 机会扫描页面 (pages/2_机会扫描.py) — 动量排名+核心卫星+轮动信号

### 待开发
- [ ] P3 操作建议页面
- [ ] P4 复盘回顾页面
- [ ] 回测引擎 (core/backtest/)
- [ ] 告警引擎 (core/alert/)

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
