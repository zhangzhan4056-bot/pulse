"""趋势跟随信号与持有段重放（纯函数）

三种经典机构趋势跟随工具，互相独立、可对比：
1. TSMOM：时间序列动量，各资产看自己的 12 月收益符号
2. Donchian 突破：创 N 日新高进场，跌破 M 日低点出场（CTA/海龟）
3. 吊灯止损：最高价 − k×ATR，只上移不下移

持有段一律从价格历史无状态重放，禁止把 entry/peak/stop 挂在实例上。
"""

import numpy as np
import pandas as pd


# ============================================================
# 常量
# ============================================================

TSMOM_LOOKBACK = 252          # 12 个月
DONCHIAN_ENTRY = 55           # 突破进场窗口（交易日）
DONCHIAN_EXIT = 20            # 通道退出窗口
CHAN_K_BASE = 3.0             # 吊灯基础系数（CTA 中短线）
CHAN_K_SLOW = 6.0             # 吊灯慢速系数（12M 动量持有，过紧会与长持矛盾）
CHAN_ATR_PERIOD = 20
COOLDOWN_DAYS = 5             # 止损后冷却，防接飞刀
COOLDOWN_SLOW = 10            # 慢信号冷却
HARD_STOP_PCT = -0.08         # 单票硬止损（用户红线）
TIGHTEN_GAIN = 0.15           # 浮盈超过 15% 后收紧吊灯
CHAN_K_TIGHT = 2.5


# ============================================================
# 信号
# ============================================================

def calc_tsmom_signal(close: pd.Series, lookback: int = TSMOM_LOOKBACK) -> bool:
    """时间序列动量：12 月收益符号。

    只看方向不看幅度。数据不足时返回 False（空仓）。
    """
    if len(close) <= lookback:
        return False
    ret = close.iloc[-1] / close.iloc[-1 - lookback] - 1.0
    return bool(ret > 0 and np.isfinite(ret))


# ============================================================
# 无状态持有段重放
# ============================================================

def _atr_series(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = CHAN_ATR_PERIOD,
) -> np.ndarray:
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.rolling(window=period).mean().to_numpy(dtype=float)


def _chan_k(gain: float, base_k: float) -> float:
    if gain >= TIGHTEN_GAIN:
        return CHAN_K_TIGHT
    return base_k


def simulate_tsmom_chandelier_positions(
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    lookback: int = TSMOM_LOOKBACK,
    base_k: float = CHAN_K_SLOW,
    cooldown_days: int = COOLDOWN_DAYS,
) -> pd.DataFrame:
    """12 月动量符号 + 吊灯止损。

    进场条件：12 月收益 > 0；
    离场条件：12 月收益符号翻负，或跌破吊灯/硬止损。
    符号负责「要不要持有」，吊灯负责「何时离场」。
    """
    n = len(close)
    cols = ["in_position", "entry_price", "peak", "stop", "signal"]
    if n == 0:
        return pd.DataFrame(columns=cols)

    atr = _atr_series(high, low, close)
    c = close.to_numpy(dtype=float)
    h = high.to_numpy(dtype=float)

    # 12 月符号（截至当日）
    signal = np.zeros(n, dtype=bool)
    for i in range(n):
        if i >= lookback:
            prev = c[i - lookback]
            if prev > 0 and np.isfinite(prev) and np.isfinite(c[i]):
                signal[i] = (c[i] / prev - 1.0) > 0

    in_pos = np.zeros(n, dtype=bool)
    entry_arr = np.full(n, np.nan)
    peak_arr = np.full(n, np.nan)
    stop_arr = np.full(n, np.nan)

    in_position = False
    entry_price = np.nan
    peak = np.nan
    stop = np.nan
    cooldown_until = -1

    for i in range(n):
        px = c[i]
        if np.isnan(px):
            continue
        hi = h[i] if not np.isnan(h[i]) else px
        sig = signal[i]

        if in_position:
            peak = max(peak, hi)
            gain = peak / entry_price - 1.0 if entry_price > 0 else 0.0
            atr_i = atr[i]
            if not np.isnan(atr_i) and atr_i > 0:
                candidate = peak - _chan_k(gain, base_k) * atr_i
                if np.isnan(stop) or candidate > stop:
                    stop = candidate
            hard_level = entry_price * (1.0 + HARD_STOP_PCT)
            stop_hit = (not np.isnan(stop)) and px < stop
            hard_hit = px <= hard_level
            sign_exit = not sig

            if stop_hit or hard_hit or sign_exit:
                in_position = False
                cooldown_until = i + cooldown_days
                entry_price = np.nan
                peak = np.nan
                stop = np.nan
            else:
                in_pos[i] = True
                entry_arr[i] = entry_price
                peak_arr[i] = peak
                stop_arr[i] = stop
        else:
            if sig and i > cooldown_until:
                in_position = True
                entry_price = px
                peak = hi
                stop = np.nan
                in_pos[i] = True
                entry_arr[i] = entry_price
                peak_arr[i] = peak

    return pd.DataFrame(
        {
            "in_position": in_pos,
            "entry_price": entry_arr,
            "peak": peak_arr,
            "stop": stop_arr,
            "signal": signal,
        }
    )


def simulate_donchian_positions(
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    entry_lookback: int = DONCHIAN_ENTRY,
    exit_lookback: int = DONCHIAN_EXIT,
    base_k: float = CHAN_K_BASE,
    cooldown_days: int = COOLDOWN_DAYS,
) -> pd.DataFrame:
    """CTA 突破：Donchian 通道 + 吊灯。

    进场：收盘价突破前 entry_lookback 日最高价；
    离场：跌破前 exit_lookback 日最低价，或跌破吊灯线。
    通道用 shift(1)，不含当日，避免前视。
    """
    n = len(close)
    cols = ["in_position", "entry_price", "peak", "stop", "entry_ch", "exit_ch"]
    if n == 0:
        return pd.DataFrame(columns=cols)

    atr = _atr_series(high, low, close)
    # 前 N 日通道（不含当日）
    entry_ch = high.rolling(entry_lookback).max().shift(1).to_numpy(dtype=float)
    exit_ch = low.rolling(exit_lookback).min().shift(1).to_numpy(dtype=float)

    c = close.to_numpy(dtype=float)
    h = high.to_numpy(dtype=float)

    in_pos = np.zeros(n, dtype=bool)
    entry_arr = np.full(n, np.nan)
    peak_arr = np.full(n, np.nan)
    stop_arr = np.full(n, np.nan)

    in_position = False
    entry_price = np.nan
    peak = np.nan
    stop = np.nan
    cooldown_until = -1

    for i in range(n):
        px = c[i]
        if np.isnan(px):
            continue
        hi = h[i] if not np.isnan(h[i]) else px
        ech = entry_ch[i]
        xch = exit_ch[i]

        if in_position:
            peak = max(peak, hi)
            gain = peak / entry_price - 1.0 if entry_price > 0 else 0.0
            atr_i = atr[i]
            if not np.isnan(atr_i) and atr_i > 0:
                candidate = peak - _chan_k(gain, base_k) * atr_i
                if np.isnan(stop) or candidate > stop:
                    stop = candidate
            hard_level = entry_price * (1.0 + HARD_STOP_PCT)
            stop_hit = (not np.isnan(stop)) and px < stop
            hard_hit = px <= hard_level
            channel_exit = (not np.isnan(xch)) and px < xch

            if stop_hit or hard_hit or channel_exit:
                in_position = False
                cooldown_until = i + cooldown_days
                entry_price = np.nan
                peak = np.nan
                stop = np.nan
            else:
                in_pos[i] = True
                entry_arr[i] = entry_price
                peak_arr[i] = peak
                stop_arr[i] = stop
        else:
            breakout = (not np.isnan(ech)) and px > ech
            if breakout and i > cooldown_until:
                in_position = True
                entry_price = px
                peak = hi
                stop = np.nan
                in_pos[i] = True
                entry_arr[i] = entry_price
                peak_arr[i] = peak

    return pd.DataFrame(
        {
            "in_position": in_pos,
            "entry_price": entry_arr,
            "peak": peak_arr,
            "stop": stop_arr,
            "entry_ch": entry_ch,
            "exit_ch": exit_ch,
        }
    )


def inverse_vol_weights(
    closes: dict,
    selected: list,
    per_name_cap: float = 0.20,
) -> dict:
    """反波动率配权 + 单票硬顶，剩余现金。

    波动率越低权重越高（等风险）；超过 cap 的部分不回补，保本优先。
    """
    invs = {}
    for s in selected:
        close = closes[s]
        rets = close.pct_change().dropna()
        if len(rets) < 20:
            invs[s] = 1.0
            continue
        vol = rets.tail(60).std()
        invs[s] = 1.0 / vol if vol and vol > 0 else 1.0

    total = sum(invs.values())
    if total <= 0:
        return {}
    weights = {}
    for s in selected:
        w = invs[s] / total
        w = min(per_name_cap, w)
        if w > 0:
            weights[s] = w
    return weights
