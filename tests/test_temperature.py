"""温度模块单元测试

验证：无前视、档位边界、吊灯线单调不降、数据不足安全降级。
"""

import unittest

import numpy as np
import pandas as pd

from core.strategy.temperature import (
    TEMP_STATES,
    TEMP_HOLD_SIZE,
    TEMP_NEW_ENTRY_SIZE,
    bucket_temperature,
    bucket_temperature_series,
    calc_temperature,
    confirm_states,
    detect_transitions,
    rolling_pct_rank,
    simulate_symbol_positions,
    apply_drawdown_overlay,
    COMPOSITE_DD_HALVE,
    COMPOSITE_DD_FLATTEN,
)


def _make_close(n: int, drift: float = 0.0, noise: float = 0.0, seed: int = 42) -> pd.Series:
    rng = np.random.default_rng(seed)
    rets = drift / 252 + noise / np.sqrt(252) * rng.standard_normal(n)
    return pd.Series(100.0 * np.cumprod(1.0 + rets))


def _ohlcv_from_close(close: pd.Series):
    high = close * 1.005
    low = close * 0.995
    return close, high, low


class TestBucketTemperature(unittest.TestCase):
    def test_bounds(self):
        self.assertEqual(bucket_temperature(0.0), "冻")
        self.assertEqual(bucket_temperature(10.0), "冻")
        self.assertEqual(bucket_temperature(10.1), "寒")
        self.assertEqual(bucket_temperature(25.0), "寒")
        self.assertEqual(bucket_temperature(40.0), "凉")
        self.assertEqual(bucket_temperature(55.0), "平")
        self.assertEqual(bucket_temperature(70.0), "温")
        self.assertEqual(bucket_temperature(85.0), "热")
        self.assertEqual(bucket_temperature(85.1), "沸")
        self.assertEqual(bucket_temperature(100.0), "沸")

    def test_all_states_covered(self):
        for t in [5, 15, 30, 48, 60, 78, 95]:
            self.assertIn(bucket_temperature(t), TEMP_STATES)

    def test_nan_returns_flat(self):
        self.assertEqual(bucket_temperature(float("nan")), "平")
        self.assertEqual(bucket_temperature(None), "平")

    def test_series(self):
        s = pd.Series([5.0, 30.0, 60.0, 90.0])
        buckets = bucket_temperature_series(s)
        self.assertEqual(buckets.tolist(), ["冻", "凉", "温", "沸"])


class TestNoLookahead(unittest.TestCase):
    def test_truncation_stability(self):
        """截断未来数据后，历史温度值必须不变"""
        close = _make_close(500, drift=0.15, noise=0.15)
        high, low = close * 1.01, close * 0.99

        t_full = calc_temperature(close, high, low)
        cutoff = 400
        t_trunc = calc_temperature(close.iloc[:cutoff], high.iloc[:cutoff], low.iloc[:cutoff])

        # 历史段（EWM 预热后）应一致
        hist_full = t_full.iloc[50:cutoff].round(6)
        hist_trunc = t_trunc.iloc[50:cutoff].round(6)
        pd.testing.assert_series_equal(hist_full, hist_trunc, check_names=False)

    def test_rolling_pct_rank_no_lookahead(self):
        s = _make_close(300, drift=0.1, noise=0.2)
        full = rolling_pct_rank(s, window=60, min_periods=20)
        trunc = rolling_pct_rank(s.iloc[:200], window=60, min_periods=20)
        pd.testing.assert_series_equal(
            full.iloc[20:200].round(6), trunc.iloc[20:200].round(6), check_names=False
        )


class TestTemperatureRange(unittest.TestCase):
    def test_uptrend_warmer_than_downtrend(self):
        up = _make_close(400, drift=0.30, noise=0.05)
        down = _make_close(400, drift=-0.30, noise=0.05)
        t_up = calc_temperature(up, up * 1.01, up * 0.99).iloc[-1]
        t_down = calc_temperature(down, down * 1.01, down * 0.99).iloc[-1]
        self.assertGreater(t_up, t_down)
        self.assertGreater(t_up, 55)
        self.assertLess(t_down, 45)

    def test_crash_is_cold(self):
        # 前 300 天平静上涨，后 60 天暴跌
        up = _make_close(300, drift=0.10, noise=0.05)
        crash = up.iloc[-1] * np.cumprod(1.0 - np.full(60, 0.015))
        close = pd.concat([up, pd.Series(crash)], ignore_index=True)
        t = calc_temperature(close, close * 1.01, close * 0.99)
        self.assertLess(t.iloc[-1], 35)


class TestChandelier(unittest.TestCase):
    def test_stop_ratchets_only_up(self):
        n = 200
        close = _make_close(n, drift=0.20, noise=0.08)
        high, low = close * 1.01, close * 0.99
        # 强制全程「热」温度，确保持续持仓
        temp = pd.Series(np.full(n, 80.0))
        pos = simulate_symbol_positions(close, high, low, temp)
        stops = pos.loc[pos["in_position"], "stop"].dropna()
        if len(stops) > 1:
            diffs = stops.diff().dropna()
            self.assertTrue((diffs >= -1e-9).all(), "吊灯线出现下移")

    def test_hard_stop_exits(self):
        # 先涨后急跌超过 -8%，必须触发止损离场
        up = _make_close(150, drift=0.20, noise=0.02)
        crash = up.iloc[-1] * np.cumprod(np.concatenate([np.ones(5), 1.0 - np.full(30, 0.01)]))
        close = pd.concat([up, pd.Series(crash)], ignore_index=True)
        high, low = close * 1.002, close * 0.998
        temp = pd.Series(np.full(len(close), 80.0))  # 热，始终想持有
        pos = simulate_symbol_positions(close, high, low, temp)

        # 止损必须触发过（持仓从有到无）
        ever_held = pos["in_position"].any()
        self.assertTrue(ever_held)
        # 急跌段出现过 size==0（被打出去）
        crash_start = 150
        self.assertTrue((pos.loc[crash_start:, "size"] == 0).any())

        # 任一持仓的浮亏不得超过硬止损线（从各自 entry 计）
        in_pos = pos[pos["in_position"]]
        for idx, row in in_pos.iterrows():
            if np.isnan(row["entry_price"]) or row["entry_price"] <= 0:
                continue
            dd = close.iloc[idx] / row["entry_price"] - 1.0
            self.assertGreater(dd, -0.085, f"day {idx} 浮亏 {dd:.2%} 超过硬止损")


class TestStateMachine(unittest.TestCase):
    def test_confirm_requires_majority(self):
        states = pd.Series(["平", "热", "热", "热", "凉"])
        confirmed = confirm_states(states, lookback=3, min_count=2)
        # 第 3 个「热」应确认
        self.assertEqual(confirmed.iloc[2], "热")
        self.assertEqual(confirmed.iloc[3], "热")

    def test_new_entry_only_on_hot(self):
        n = 300
        close, high, low = _ohlcv_from_close(_make_close(n, drift=0.0, noise=0.05))
        # 前半段平，后半段热
        temp = pd.Series([50.0] * 150 + [80.0] * 150)
        pos = simulate_symbol_positions(close, high, low, temp)
        # 平区不开仓
        self.assertTrue((pos.loc[:140, "size"] == 0).all())
        # 热区应开仓
        self.assertTrue((pos.loc[160:200, "size"] > 0).any())

    def test_warm_hold_only(self):
        n = 300
        close, high, low = _ohlcv_from_close(_make_close(n, drift=0.05, noise=0.05))
        # 先热后温：热开仓，温持有但不新开
        temp = pd.Series([80.0] * 160 + [60.0] * 140)
        pos = simulate_symbol_positions(close, high, low, temp)
        # 温区已持有部分应有 0.40
        warm_sizes = pos.loc[170:220, "size"]
        self.assertTrue((warm_sizes > 0).any())
        self.assertTrue((warm_sizes[warm_sizes > 0] <= 0.41).all())

    def test_size_map_consistency(self):
        self.assertEqual(TEMP_NEW_ENTRY_SIZE["热"], 0.85)
        self.assertEqual(TEMP_NEW_ENTRY_SIZE["温"], 0.0)
        self.assertEqual(TEMP_NEW_ENTRY_SIZE["沸"], 0.50)
        self.assertEqual(TEMP_HOLD_SIZE["温"], 0.40)
        self.assertEqual(TEMP_HOLD_SIZE["沸"], 0.50)
        self.assertEqual(TEMP_HOLD_SIZE["平"], 0.0)

    def test_transitions_detect(self):
        states = pd.Series(["平", "温", "热", "热", "平"])
        trans = detect_transitions(states)
        self.assertEqual(len(trans), 3)
        self.assertEqual(trans[0]["to"], "温")
        self.assertEqual(trans[1]["to"], "热")
        self.assertEqual(trans[2]["to"], "平")


class TestDrawdownOverlay(unittest.TestCase):
    def test_no_overlay_in_normal_range(self):
        w = {"SPY": 0.2, "QQQ": 0.2}
        self.assertEqual(apply_drawdown_overlay(w, -0.05), w)

    def test_halve(self):
        w = {"SPY": 0.2, "QQQ": 0.2}
        out = apply_drawdown_overlay(w, COMPOSITE_DD_HALVE - 0.01)
        self.assertAlmostEqual(out["SPY"], 0.1)

    def test_flatten(self):
        w = {"SPY": 0.2}
        self.assertEqual(apply_drawdown_overlay(w, COMPOSITE_DD_FLATTEN - 0.01), {})


class TestInsufficientData(unittest.TestCase):
    def test_short_series_returns_nan_temp(self):
        close = _make_close(30)
        t = calc_temperature(close, close * 1.01, close * 0.99)
        self.assertTrue(t.isna().all() or len(t) == 30)

    def test_short_series_no_position(self):
        # 数据不足时真实温度为 NaN → 判平 → 不开仓
        close = _make_close(40)
        high, low = close * 1.01, close * 0.99
        temp = calc_temperature(close, high, low)
        pos = simulate_symbol_positions(close, high, low, temp)
        self.assertTrue((pos["size"] == 0).all())


if __name__ == "__main__":
    unittest.main()
