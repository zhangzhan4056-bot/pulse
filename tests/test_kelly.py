"""凯利配比单元测试"""

import unittest

from core.strategy.kelly import (
    kelly_weight,
    kelly_weights,
    mu_from_momentum,
)


class TestKellyWeight(unittest.TestCase):
    def test_basic_formula(self):
        # f = k * mu / vol^2 = 0.25 * 0.10 / 0.15^2 = 1.111
        self.assertAlmostEqual(kelly_weight(0.10, 0.15, k=0.25), 0.25 * 0.10 / 0.0225)

    def test_negative_mu_zero(self):
        self.assertEqual(kelly_weight(-0.05, 0.15), 0.0)

    def test_invalid_vol_zero(self):
        self.assertEqual(kelly_weight(0.10, 0.0), 0.0)
        self.assertEqual(kelly_weight(0.10, -0.15), 0.0)
        self.assertEqual(kelly_weight(0.10, None), 0.0)


class TestKellyWeights(unittest.TestCase):
    def test_cap_and_total(self):
        mu = {"A": 0.20, "B": 0.05, "C": -0.10}
        vol = {"A": 0.15, "B": 0.20, "C": 0.15}
        w = kelly_weights(mu, vol, k=0.25, max_weight=0.20, max_total=1.0)
        self.assertNotIn("C", w)  # 负 mu 不下注
        self.assertLessEqual(w["A"], 0.20)
        self.assertLessEqual(w["B"], 0.20)
        self.assertLessEqual(sum(w.values()), 1.0 + 1e-9)

    def test_total_scale_down(self):
        # 四个高优势资产，clip 后总和 0.8，不应超过 max_total=0.5
        mu = {f"s{i}": 0.15 for i in range(4)}
        vol = {f"s{i}": 0.12 for i in range(4)}
        w = kelly_weights(mu, vol, k=0.25, max_weight=0.20, max_total=0.5)
        self.assertLessEqual(sum(w.values()), 0.5 + 1e-9)

    def test_empty(self):
        self.assertEqual(kelly_weights({}, {}), {})

    def test_low_vol_bigger_weight(self):
        mu = {"low_vol": 0.10, "high_vol": 0.10}
        vol = {"low_vol": 0.10, "high_vol": 0.30}
        w = kelly_weights(mu, vol, k=0.25, max_weight=1.0, max_total=10.0)
        self.assertGreater(w["low_vol"], w["high_vol"])


class TestMuHelpers(unittest.TestCase):
    def test_momentum_compression(self):
        self.assertAlmostEqual(mu_from_momentum(20), 0.10)   # 20% → 0.5*0.20
        self.assertAlmostEqual(mu_from_momentum(-20), -0.10)
        self.assertAlmostEqual(mu_from_momentum(100), 0.15)  # clip 30% → 0.5*0.30
        self.assertEqual(mu_from_momentum(None), 0.0)


if __name__ == "__main__":
    unittest.main()
