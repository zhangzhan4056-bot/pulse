"""全市场扫描器

对齐扫描口径「门槛8%」（12M 涨幅 >8% 才买），与生产策略解耦。
score_all_assets / AssetScore 延迟导入，避免拖垮 `from core.scanner.scorer import x`。
"""

_LAZY_IMPORTS = {
    "score_all_assets": "scorer",
    "AssetScore": "scorer",
}


def __getattr__(name):
    if name in _LAZY_IMPORTS:
        import importlib

        module = importlib.import_module(f".{_LAZY_IMPORTS[name]}", __name__)
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(_LAZY_IMPORTS)
