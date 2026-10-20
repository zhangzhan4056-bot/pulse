"""分数凯利仓位配比

连续收益版凯利：f* = μ / σ²
多资产：w_i = clip(k · μ̂_i / σ̂_i², 0, max_weight)

k ∈ [0.25, 0.5] 为分数系数（估计误差下的机构标准做法）。
结果再受单票硬顶和组合总仓位约束。

纯函数，无实例状态。
"""

from typing import Dict, Optional

import numpy as np


def kelly_weight(mu: float, vol: float, k: float = 0.25) -> float:
    """单资产分数凯利权重（未 clip）

    Args:
        mu: 年化预期收益，小数（0.10 = 10%）
        vol: 年化波动率，小数（0.15 = 15%）
        k: 分数系数，0.25 = 1/4 凯利

    Returns:
        建议仓位比例，可能 >1（调用方负责 clip）
    """
    if vol is None or vol <= 0 or mu is None:
        return 0.0
    if mu <= 0:
        return 0.0
    return k * (mu / (vol * vol))


def kelly_weights(
    mu_map: Dict[str, float],
    vol_map: Dict[str, float],
    k: float = 0.25,
    max_weight: float = 0.20,
    max_total: float = 1.0,
) -> Dict[str, float]:
    """多资产分数凯利配比

    1. 各资产 f_i = k · max(μ_i, 0) / σ_i²
    2. clip 到 [0, max_weight]
    3. 若总和 > max_total，按比例压缩
    4. 剔除权重为 0 的资产

    Args:
        mu_map: {symbol: 年化预期收益（小数）}
        vol_map: {symbol: 年化波动率（小数）}
        k: 分数系数，默认 1/4 凯利
        max_weight: 单票硬顶（默认 0.20，对应风控红线）
        max_total: 组合总仓位上限（默认 1.0）

    Returns:
        {symbol: weight}
    """
    raw: Dict[str, float] = {}
    for symbol in mu_map:
        mu = mu_map.get(symbol)
        vol = vol_map.get(symbol)
        if mu is None or vol is None:
            continue
        w = kelly_weight(float(mu), float(vol), k=k)
        if w > 0:
            raw[symbol] = w

    if not raw:
        return {}

    # 单票硬顶
    clipped = {s: min(w, max_weight) for s, w in raw.items()}
    total = sum(clipped.values())

    # 总仓位压缩
    if total > max_total > 0:
        scale = max_total / total
        clipped = {s: w * scale for s, w in clipped.items()}

    return {s: w for s, w in clipped.items() if w > 1e-6}


def mu_from_momentum(momentum_pct: float) -> float:
    """把动量收益（百分比）转成凯利用的 μ̂（小数）

    动量是无量纲排序信号，直接当年化 μ 会过于乐观。
    这里做温和压缩：clip(动量, -30%, 30%) × 0.5，当作半衰减的预期优势。
    """
    if momentum_pct is None or (isinstance(momentum_pct, float) and np.isnan(momentum_pct)):
        return 0.0
    clipped = max(-0.30, min(0.30, momentum_pct / 100.0))
    return clipped * 0.5
