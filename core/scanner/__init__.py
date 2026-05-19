"""全市场扫描器

对每个资产进行多维评分，发现趋势、均值回归、风险机会。
"""

from core.scanner.scorer import score_all_assets, AssetScore

__all__ = ["score_all_assets", "AssetScore"]
