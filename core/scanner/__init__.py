"""全市场扫描器

对齐生产策略「凯利·门槛8%」：12M 涨幅 >8% 才买，过门槛候选 / 门槛附近 / 不碰。
"""

from core.scanner.scorer import score_all_assets, AssetScore

__all__ = ["score_all_assets", "AssetScore"]
