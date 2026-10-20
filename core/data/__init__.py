"""数据获取层 - 大类资产数据获取与存储

符号常量（STRATEGY_UNIVERSE 等）从 config 同步导出，不拉取任何重依赖。
DataManager / 抓取器延迟导入，避免 akshare 等第三方库失败拖垮
`from core.data import STRATEGY_UNIVERSE`（Streamlit Cloud 曾因此整页 ImportError）。
"""

from .config import (
    ASSETS,
    US_SYMBOLS,
    CN_SYMBOLS,
    GLOBAL_SYMBOLS,
    US_SECTORS_SYMBOLS,
    STRATEGY_UNIVERSE,
    CASH_PROXY,
)

_LAZY_IMPORTS = {
    "DataManager": "manager",
    "DataStorage": "storage",
    "PoliticalSignalStorage": "signal_storage",
    "SignalFetcher": "signal_fetcher",
    "NewsFetcher": "signal_fetcher",
    "translate_to_cn": "signal_fetcher",
    "translate_batch": "signal_fetcher",
}

__all__ = [
    "ASSETS",
    "US_SYMBOLS",
    "CN_SYMBOLS",
    "GLOBAL_SYMBOLS",
    "US_SECTORS_SYMBOLS",
    "STRATEGY_UNIVERSE",
    "CASH_PROXY",
    "DataManager",
    "DataStorage",
    "PoliticalSignalStorage",
    "SignalFetcher",
    "NewsFetcher",
    "translate_to_cn",
    "translate_batch",
]


def __getattr__(name):
    if name in _LAZY_IMPORTS:
        import importlib

        module = importlib.import_module(f".{_LAZY_IMPORTS[name]}", __name__)
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(__all__)
