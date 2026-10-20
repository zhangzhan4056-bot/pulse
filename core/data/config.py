"""配置管理 - API key 和 symbol 映射"""

import os
from pathlib import Path


def get_api_key() -> str:
    """获取 Twelve Data API key

    优先级：
    1. Streamlit secrets (st.secrets["TWELVEDATA_API_KEY"])
    2. 环境变量 TWELVEDATA_API_KEY
    3. ~/.local/bin/finquote.conf
    """
    # Streamlit Cloud secrets
    try:
        import streamlit as st
        key = st.secrets.get("TWELVEDATA_API_KEY")
        if key:
            return key
    except (ImportError, AttributeError, FileNotFoundError):
        pass

    # 环境变量
    key = os.environ.get("TWELVEDATA_API_KEY")
    if key:
        return key

    # 配置文件（支持 key=value 格式和纯 key 格式）
    conf_path = Path.home() / ".local" / "bin" / "finquote.conf"
    if conf_path.exists():
        for line in conf_path.read_text().strip().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                if "=" in line:
                    return line.split("=", 1)[1].strip()
                return line

    raise ValueError(
        "未找到 Twelve Data API key。"
        "请在 Streamlit Cloud Settings → Secrets 中配置 TWELVEDATA_API_KEY"
    )


# 大类资产 symbol 映射
ASSETS = {
    # 美股大类资产（以美股为主）
    "us_equity": {
        "SPY": "标普500 ETF",
        "QQQ": "纳斯达克100 ETF",
    },
    "us_international": {
        "EFA": "MSCI发达市场 ETF",
        "EEM": "MSCI新兴市场 ETF",
    },
    "us_bond": {
        "TLT": "20年期国债 ETF（替代 TNX）",
        "SHY": "1-3年短债 ETF（现金代理）",
    },
    "us_oil": {
        "CL": "WTI原油期货",
    },
    "us_commodity": {
        "GLD": "黄金ETF",
    },
    # A股指数（受美股影响大，作为辅助观测）
    "cn_equity": {
        "399006": "创业板指",
        "000688": "科创50",
        "000300": "沪深300",
    },
    # 其他亚太指数
    "global_equity": {
        "日经225": "日经225",
        "KOSPI": "韩国KOSPI",
    },
    # 美股板块 ETF（用于赛道分析）
    "us_sectors": {
        "XLK": "科技",
        "XLF": "金融",
        "XLE": "能源",
        "XLV": "医疗",
        "XLI": "工业",
        "XLP": "消费必需",
        "XLU": "公用事业",
        "XLRE": "房地产",
        "XLB": "材料",
        "XLC": "通信",
    },
}

# 所有美股 symbol（含国际宽基与短债）
US_SYMBOLS = {
    sym: desc
    for category in ["us_equity", "us_international", "us_bond", "us_oil", "us_commodity"]
    for sym, desc in ASSETS[category].items()
}

# 所有 A股 symbol
CN_SYMBOLS = ASSETS["cn_equity"]

# 全球指数 symbol（日韩等）
GLOBAL_SYMBOLS = ASSETS["global_equity"]

# 美股板块 ETF symbol
US_SECTORS_SYMBOLS = ASSETS["us_sectors"]

# 策略可交易宇宙 = 大类 + 国际 + 板块（所有策略共用）
STRATEGY_UNIVERSE = {
    **US_SYMBOLS,
    **CN_SYMBOLS,
    **GLOBAL_SYMBOLS,
    **US_SECTORS_SYMBOLS,
}

# 现金代理：策略未配满的仓位买入短债，不再闲置 0% 收益
CASH_PROXY = "SHY"

# 关注持仓（用于风险预警的特别监控）
WATCHED_HOLDINGS = {
    # 美股板块
    "XLK": {"name": "科技ETF", "type": "sector", "threshold": "sensitive"},
    # A股场外基金
    "011369": {"name": "华商均衡成长混合A", "type": "fund"},
    "000979": {"name": "景顺长城沪港深精选", "type": "fund"},
    "012922": {"name": "易方达全球成长精选C", "type": "fund"},
    "457001": {"name": "国富亚洲机会股票A", "type": "fund"},
}

# SEC EDGAR 13-F 跟踪机构列表（CIK 零填充 10 位，已验证）
THIRTEEN_F_MANAGERS = {
    "0001067983": {"name": "Berkshire Hathaway", "category": "other"},
    "0001350694": {"name": "Bridgewater Associates", "category": "hedge_fund"},
    "0001423053": {"name": "Citadel Advisors", "category": "hedge_fund"},
    "0001603466": {"name": "Point72 Asset Management", "category": "hedge_fund"},
    "0001037389": {"name": "Renaissance Technologies", "category": "hedge_fund"},
    "0001179392": {"name": "Two Sigma Investments", "category": "hedge_fund"},
    "0001273087": {"name": "Millennium Management", "category": "hedge_fund"},
    "0001009207": {"name": "D.E. Shaw & Co.", "category": "hedge_fund"},
    "0001048445": {"name": "Elliott Management", "category": "hedge_fund"},
    "0001029160": {"name": "Soros Fund Management", "category": "hedge_fund"},
    "0001167483": {"name": "Tiger Global Management", "category": "hedge_fund"},
    "0001135730": {"name": "Coatue Management", "category": "hedge_fund"},
    "0001040273": {"name": "Third Point", "category": "hedge_fund"},
    "0001336528": {"name": "Pershing Square Capital", "category": "hedge_fund"},
    "0001167557": {"name": "AQR Capital Management", "category": "hedge_fund"},
    "0001061768": {"name": "Baupost Group", "category": "hedge_fund"},
    "0001103804": {"name": "Viking Global Investors", "category": "hedge_fund"},
    "0001061165": {"name": "Lone Pine Capital", "category": "hedge_fund"},
    "0001656456": {"name": "Appaloosa Management", "category": "hedge_fund"},
    "0000921669": {"name": "Carl Icahn", "category": "other"},
}

# SEC EDGAR 配置
EDGAR_USER_AGENT = "MarketPulse research@marketpulse.com"
EDGAR_RATE_LIMIT = 0.12  # 请求间隔秒数（SEC 限制 10 req/sec）

# Twelve Data API 配置
TWELVEDATA_BASE_URL = "https://api.twelvedata.com"
TWELVEDATA_RATE_LIMIT = 8  # 每分钟请求次数（免费计划）

# 宏观指标配置（数据源：FRED - Federal Reserve Economic Data）
MACRO_INDICATORS = {
    "cpi_yoy": {
        "name": "CPI 同比",
        "unit": "%",
        "frequency": "monthly",
        "fred_series": "CPIAUCSL",  # CPI for All Urban Consumers (Index)
        "is_index": True,           # 需要计算同比变化率
    },
    "ppi": {
        "name": "PPI 同比",
        "unit": "%",
        "frequency": "monthly",
        "fred_series": "WPSFD4131", # PPI Final Demand Less Foods and Energy
        "is_index": True,
    },
    "treasury_10y": {
        "name": "10Y 国债收益率",
        "unit": "%",
        "frequency": "monthly",
        "fred_series": "GS10",      # Market Yield on U.S. Treasury Securities at 10-Year (Monthly)
    },
    "fedfunds": {
        "name": "联邦基金利率",
        "unit": "%",
        "frequency": "monthly",
        "fred_series": "FEDFUNDS",  # Federal Funds Effective Rate (Monthly)
    },
    "unemployment": {
        "name": "失业率",
        "unit": "%",
        "frequency": "monthly",
        "fred_series": "UNRATE",    # Unemployment Rate (Monthly, Seasonally Adjusted)
    },
    "vix": {
        "name": "VIX 恐慌指数",
        "unit": "点",
        "frequency": "daily",
        "fred_series": "VIXCLS",    # CBOE Volatility Index (Daily)
        "is_index": False,
    },
}

MACRO_DISPLAY_ORDER = ["cpi_yoy", "ppi", "treasury_10y", "fedfunds", "unemployment", "vix"]
MACRO_COLORS = {
    "cpi_yoy": "#ef5350",
    "ppi": "#ff9800",
    "treasury_10y": "#2196f3",
    "fedfunds": "#9c27b0",
    "unemployment": "#607d8b",
    "vix": "#d32f2f",
}
