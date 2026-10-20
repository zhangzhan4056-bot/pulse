"""趋势跟随纯函数单元测试：无前视、吊灯单调、突破通道不含当日"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.strategy.trend_follow import (
    calc_tsmom_signal,
    simulate_tsmom_chandelier_positions,
    simulate_donchian_positions,
    inverse_vol_weights,
)


def _make_ohlcv(n=300, start=100.0, drift=0.001, seed=7):
    rng = np.random.default_rng(seed)
    rets = drift + 0.01 * rng.standard_normal(n)
    close = start * np.cumprod(1 + rets)
    close = pd.Series(close)
    high = close * 1.01
    low = close * 0.99
    return close, high, low


def test_tsmom_signal_direction():
    up, _, _ = _make_ohlcv(300, drift=0.002)
    down, _, _ = _make_ohlcv(300, drift=-0.002)
    assert calc_tsmom_signal(up) is True
    assert calc_tsmom_signal(down) is False
    short = up.head(50)
    assert calc_tsmom_signal(short) is False


def test_chandelier_stop_monotonic():
    close, high, low = _make_ohlcv(250)
    pos = simulate_tsmom_chandelier_positions(close, high, low)
    stops = pos["stop"].dropna()
    # 吊灯线只上移不下调（离场后 stop 重置为 NaN，比较连续段）
    in_pos = pos["in_position"].to_numpy()
    stop_arr = pos["stop"].to_numpy(dtype=float)
    prev = np.nan
    for i in range(len(stop_arr)):
        if in_pos[i]:
            if np.isfinite(prev) and np.isfinite(stop_arr[i]):
                assert stop_arr[i] >= prev - 1e-9
            prev = stop_arr[i]
        else:
            prev = np.nan


def test_no_lookahead_tsmom():
    close, high, low = _make_ohlcv(320)
    full = simulate_tsmom_chandelier_positions(close, high, low)
    cut = 250
    part = simulate_tsmom_chandelier_positions(close.iloc[:cut], high.iloc[:cut], low.iloc[:cut])
    # 截断输入后，历史 in_position 路径必须不变
    assert list(full["in_position"][:cut]) == list(part["in_position"][:cut])


def test_donchian_breakout_no_lookahead():
    close, high, low = _make_ohlcv(320)
    full = simulate_donchian_positions(close, high, low)
    cut = 250
    part = simulate_donchian_positions(close.iloc[:cut], high.iloc[:cut], low.iloc[:cut])
    assert list(full["in_position"][:cut]) == list(part["in_position"][:cut])


def test_donchian_enters_on_breakout():
    # 先跌后涨，后半段创新高应触发进场
    n = 120
    down = 100 - np.arange(60) * 0.5
    up = down[-1] + np.arange(1, 61) * 1.2
    close = pd.Series(np.concatenate([down, up]))
    high = close * 1.005
    low = close * 0.995
    pos = simulate_donchian_positions(close, high, low, entry_lookback=20, exit_lookback=10)
    assert bool(pos["in_position"].iloc[-1]) is True


def test_inverse_vol_cap():
    rng = np.random.default_rng(1)
    a = pd.Series(100 * np.cumprod(1 + 0.001 + 0.002 * rng.standard_normal(200)))
    b = pd.Series(100 * np.cumprod(1 + 0.001 + 0.02 * rng.standard_normal(200)))
    w = inverse_vol_weights({"A": a, "B": b}, ["A", "B"], per_name_cap=0.20)
    assert set(w) == {"A", "B"}
    assert all(x <= 0.20 + 1e-9 for x in w.values())
    # 低波 A 权重应不低于高波 B（cap 后可能都是 0.20）
    assert w["A"] >= w["B"] - 1e-9


if __name__ == "__main__":
    for _n, _f in list(globals().items()):
        if _n.startswith("test_") and callable(_f):
            _f()
            print("PASS", _n)
    print("all ok")
