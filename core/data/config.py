"""配置管理 - API key 和 symbol 映射"""

import os
from pathlib import Path


def get_api_key() -> str:
    """获取 Twelve Data API key

    优先级：
    1. 环境变量 TWELVEDATA_API_KEY
    2. ~/.local/bin/finquote.conf
    """
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
        "请设置环境变量 TWELVEDATA_API_KEY 或创建 ~/.local/bin/finquote.conf"
    )


# 大类资产 symbol 映射
ASSETS = {
    # 美股大类资产（以美股为主）
    "us_equity": {
        "SPY": "标普500 ETF",
        "QQQ": "纳斯达克100 ETF",
    },
    "us_bond": {
        "TLT": "20年期国债 ETF（替代 TNX）",
    },
    "us_oil": {
        "CL": "WTI原油期货",
    },
    # A股指数（受美股影响大，作为辅助观测）
    "cn_equity": {
        "399006": "创业板指",
        "000688": "科创50",
        "000300": "沪深300",
    },
    # 其他亚太指数
    "global_equity": {
        "NKY": "日经225",
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

# 所有美股 symbol
US_SYMBOLS = {
    sym: desc
    for category in ["us_equity", "us_bond", "us_oil"]
    for sym, desc in ASSETS[category].items()
}

# 所有 A股 symbol
CN_SYMBOLS = ASSETS["cn_equity"]

# 全球指数 symbol（日韩等）
GLOBAL_SYMBOLS = ASSETS["global_equity"]

# 美股板块 ETF symbol
US_SECTORS_SYMBOLS = ASSETS["us_sectors"]

# Twelve Data API 配置
TWELVEDATA_BASE_URL = "https://api.twelvedata.com"
TWELVEDATA_RATE_LIMIT = 8  # 每分钟请求次数（免费计划）
