"""策略引擎模块

指标函数从 indicators 延迟导出，避免 `from core.strategy.indicators import x`
被 __init__ 的20 个转发名拖垮（任一名字失配 = 整页 ImportError）。
"""

_LAZY_FROM = "indicators"

_LAZY_IMPORTS = {
    name: _LAZY_FROM
    for name in (
        "calc_returns",
        "calc_momentum_score",
        "calc_sma",
        "calc_ema",
        "calc_ma_alignment",
        "calc_rsi",
        "calc_rsi_status",
        "calc_macd",
        "calc_macd_signal",
        "calc_drawdown",
        "calc_max_drawdown",
        "calc_current_drawdown",
        "calc_volatility",
        "calc_atr",
        "calc_correlation_matrix",
        "detect_market_regime",
        "calc_core_satellite_allocation",
        "generate_rotation_signals",
        "CORE_ASSETS",
        "SATELLITE_ASSETS",
    )
}


def __getattr__(name):
    if name in _LAZY_IMPORTS:
        import importlib

        module = importlib.import_module(f".{_LAZY_IMPORTS[name]}", __name__)
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(_LAZY_IMPORTS)
