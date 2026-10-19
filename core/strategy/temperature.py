"""市场温度指标与趋势状态机

七档温度：冻-寒-凉-平-温-热-沸
温度为 0-100 连续分，由趋势质量/趋势结构/动量加速/新高贴近度四维
固定阈值映射后加权合成（不用滚动百分位，避免成熟趋势被自身历史排名降温）。

纯函数，无实例状态。持有段元数据一律从价格历史重放推导，
以保证 generate_weights 在任意 current_date 可独立重算。
"""

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


# ============================================================
# 常量
# ============================================================

# 七档状态（低温 → 高温）
TEMP_STATES = ["冻", "寒", "凉", "平", "温", "热", "沸"]

# 档位上界（含），对应 冻/寒/凉/平/温/热；沸为剩余区间
TEMP_BUCKET_BOUNDS = [10.0, 25.0, 40.0, 55.0, 70.0, 85.0]

TEMP_COLORS = {
    "冻": "#1a237e",
    "寒": "#1565c0",
    "凉": "#42a5f5",
    "平": "#9e9e9e",
    "温": "#ffb74d",
    "热": "#ef6c00",
    "沸": "#c62828",
}

# 状态 → 新开仓 / 已持有 的目标size（占单票上限前的热度系数）
# V1：开仓只在 热/沸；V2 额外允许上位温区（warm_entry_t）进场，修「转热踩空」
TEMP_NEW_ENTRY_SIZE = {
    "冻": 0.0, "寒": 0.0, "凉": 0.0, "平": 0.0,
    "温": 0.0,   # 温区默认不抢跑
    "热": 0.85,
    "沸": 0.50,  # 泡沫区减仓进 + 收紧止损
}

TEMP_WARM_ENTRY_SIZE = 0.40  # 上位温区进场热度

TEMP_HOLD_SIZE = {
    "冻": 0.0, "寒": 0.0, "凉": 0.0, "平": 0.0,
    "温": 0.40,
    "热": 0.85,
    "沸": 0.50,  # 泡沫区减半
}

# 可持仓状态（已持有时）
HOLDABLE_STATES = {"温", "热", "沸"}

# 温度公式维度权重
WEIGHT_ER = 0.30        # 趋势效率
WEIGHT_TREND = 0.30     # 趋势结构
WEIGHT_MOM = 0.25       # 动量+加速
WEIGHT_HIGH = 0.15      # 新高贴近度

# 滚动百分位窗口
PCT_WINDOW = 252
PCT_MIN_PERIODS = 126

# 止损参数
# 持仓周期 2 个月-3 年，k=3 对日线太紧（3% 回撤即离场）。
# k=4 ≈ 峰值回撤 6-8% 才触发，匹配中长线趋势持有。
CHANDALIER_K_BASE = 4.0
CHANDALIER_K_TIGHT = 2.5
CHANDALIER_K_BOIL = 3.0
CHANDALIER_ATR_PERIOD = 20
TIGHTEN_GAIN = 0.15
HARD_STOP_PCT = -0.08

# 状态机
CONFIRM_LOOKBACK = 3
CONFIRM_MIN_COUNT = 2
COOLDOWN_DAYS = 5
MIN_HOLD_DAYS = 5          # 状态信号退出最短持有（止损不受限）
COMPOSITE_DD_HALVE = -0.09   # 组合 DD 减半（缓冲点，名义 -10%）
COMPOSITE_DD_FLATTEN = -0.14  # 组合 DD 清仓（缓冲点，名义 -15%）


# ============================================================
# 基础计算
# ============================================================


def rolling_pct_rank(
    series: pd.Series,
    window: int = PCT_WINDOW,
    min_periods: int = PCT_MIN_PERIODS,
) -> pd.Series:
    """值在滚动历史窗口内的百分位 [0, 100]

    当前值相对过去 window 根 K 线的位置。数据不足返回 NaN。
    """
    values = series.to_numpy(dtype=float)
    n = len(values)
    out = np.full(n, np.nan)
    for i in range(n):
        start = max(0, i - window + 1)
        w = values[start : i + 1]
        valid = w[~np.isnan(w)]
        if len(valid) < min_periods or np.isnan(values[i]):
            continue
        out[i] = float(np.mean(valid <= values[i]) * 100.0)
    return pd.Series(out, index=series.index)


def calc_efficiency_ratio(close: pd.Series, window: int = 63) -> pd.Series:
    """Kaufman 效率比：方向性位移 / 路径总长度

    趋势越顺畅越接近 1，震荡越接近 0。无方向。
    """
    direction = (close - close.shift(window)).abs()
    path = close.diff().abs().rolling(window).sum()
    er = direction / path.replace(0.0, np.nan)
    return er


def calc_trend_quality(close: pd.Series, window: int = 63) -> pd.Series:
    """带方向的趋势质量分 [0, 100]

    50 + 50 · sign(ret_w) · ER · min(|ret_w|/15%, 1)
    平滑 +15% 上涨 → 100；平滑 -15% 下跌 → 0；震荡居中 50。
    """
    er = calc_efficiency_ratio(close, window)
    ret = close / close.shift(window) - 1.0
    mag = (ret.abs() / 0.15).clip(upper=1.0)
    score = 50.0 + 50.0 * np.sign(ret) * er.fillna(0.0) * mag
    return score.clip(0.0, 100.0)


def calc_trend_structure(close: pd.Series) -> pd.Series:
    """趋势结构原始值

    0.4·(c/MA200−1) + 0.3·(c/MA50−1) + 0.3·(MA50/MA50_{t−63}−1)
    典型区间约 [-0.20, 0.20]。
    """
    ma200 = close.rolling(200, min_periods=50).mean()
    ma50 = close.rolling(50, min_periods=20).mean()
    ma50_slope = ma50 / ma50.shift(63) - 1.0
    ts = 0.4 * (close / ma200 - 1.0) + 0.3 * (close / ma50 - 1.0) + 0.3 * ma50_slope
    return ts


def calc_trend_structure_score(close: pd.Series) -> pd.Series:
    """趋势结构分 [0, 100]，固定阈值 -0.20 ~ +0.20"""
    ts = calc_trend_structure(close)
    return ((ts + 0.20) / 0.40 * 100.0).clip(0.0, 100.0)


def calc_momentum_accel(close: pd.Series) -> pd.Series:
    """动量+加速度分 [0, 100]

    Wmom = 1M×0.2 + 3M×0.3 + 6M×0.3 + 12M×0.2（与 calc_momentum_score 同权重）
    再混入 3M−12M 加速度，映射 -15%~+15% → 0-100。
    """
    ret1m = (close / close.shift(21) - 1.0) * 100
    ret3m = (close / close.shift(63) - 1.0) * 100
    ret6m = (close / close.shift(126) - 1.0) * 100
    ret12m = (close / close.shift(252) - 1.0) * 100
    wmom = 0.2 * ret1m + 0.3 * ret3m + 0.3 * ret6m + 0.2 * ret12m
    accel = ret3m - ret12m
    mixed = 0.7 * wmom + 0.3 * accel
    return ((mixed + 15.0) / 30.0 * 100.0).clip(0.0, 100.0)


def calc_high_proximity(close: pd.Series, window: int = 252) -> pd.Series:
    """新高贴近度 [0, 1]：close / 252 日滚动最高"""
    rolling_max = close.rolling(window, min_periods=63).max()
    return close / rolling_max


def calc_high_proximity_score(close: pd.Series, window: int = 252) -> pd.Series:
    """新高贴近度分 [0, 100]：映射 [80%, 100%] → [0, 100]"""
    hp = calc_high_proximity(close, window)
    return ((hp - 0.80) / 0.20 * 100.0).clip(0.0, 100.0)


def calc_temperature(
    close: pd.Series,
    high: Optional[pd.Series] = None,
    low: Optional[pd.Series] = None,
) -> pd.Series:
    """综合温度 T ∈ [0, 100]，EWM(span=5) 平滑

    四维各先映射到 0-100 再加权（固定阈值而非滚动百分位——
    滚动百分位会让长期走强的成熟趋势被自身历史排名「降温」）。
    波动率百分位 >90 且新高贴近度 >0.98 时强制判沸。
    """
    tq = calc_trend_quality(close)
    ts = calc_trend_structure_score(close)
    ma = calc_momentum_accel(close)
    hp_raw = calc_high_proximity(close)
    hp = calc_high_proximity_score(close)

    t_raw = WEIGHT_ER * tq + WEIGHT_TREND * ts + WEIGHT_MOM * ma + WEIGHT_HIGH * hp
    t = t_raw.ewm(span=5, min_periods=1).mean()

    if high is not None and low is not None and len(close) >= 20:
        ret = close.pct_change()
        vol20 = ret.rolling(20, min_periods=10).std() * np.sqrt(252)
        vol_pct = rolling_pct_rank(vol20, window=PCT_WINDOW, min_periods=PCT_MIN_PERIODS)
        blowoff = (vol_pct > 90) & (hp_raw > 0.98)
        t = t.mask(blowoff.fillna(False), 100.0)

    return t.clip(0.0, 100.0)


def bucket_temperature(t: Optional[float]) -> str:
    """温度值 → 七档状态"""
    if t is None or (isinstance(t, float) and np.isnan(t)):
        return "平"
    for bound, state in zip(TEMP_BUCKET_BOUNDS, TEMP_STATES[:-1]):
        if t <= bound:
            return state
    return "沸"


def bucket_temperature_series(t: pd.Series) -> pd.Series:
    """温度序列 → 状态序列"""
    return t.map(bucket_temperature)


# ============================================================
# 状态机
# ============================================================


def confirm_states(
    states: pd.Series,
    lookback: int = CONFIRM_LOOKBACK,
    min_count: int = CONFIRM_MIN_COUNT,
) -> pd.Series:
    """近 lookback 日中至少 min_count 日同状态才确认

    无多数状态时沿用上一确认态（防抖）。
    """
    if states.empty:
        return states.copy()

    result: List[str] = []
    prev = states.iloc[0]
    values = states.tolist()
    for i in range(len(values)):
        window = values[max(0, i - lookback + 1) : i + 1]
        counts: Dict[str, int] = {}
        for s in window:
            counts[s] = counts.get(s, 0) + 1
        candidates = [s for s, c in counts.items() if c >= min_count]
        if candidates:
            # 取计数最高者；同票取更近出现的
            prev = max(candidates, key=lambda s: (counts[s], window[::-1].index(s)))
        result.append(prev)
    return pd.Series(result, index=states.index, dtype=object)


def detect_transitions(states: pd.Series) -> List[Dict[str, object]]:
    """检测状态转换点

    Returns:
        [{"date", "from", "to", "index"}, ...]
    """
    transitions = []
    for i in range(1, len(states)):
        if states.iloc[i] != states.iloc[i - 1]:
            transitions.append({
                "date": states.index[i] if isinstance(states.index, pd.DatetimeIndex) else i,
                "from": states.iloc[i - 1],
                "to": states.iloc[i],
                "index": i,
            })
    return transitions


# ============================================================
# 吊灯止损与持有段重放
# ============================================================


def calc_atr_series(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = CHANDALIER_ATR_PERIOD,
) -> pd.Series:
    """ATR 序列（与 indicators.calc_atr 相同，本地实现避免循环依赖）"""
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.rolling(window=period).mean()


def _chandelier_k(state: str, gain: float) -> float:
    """根据温度状态和浮盈选择止损系数（越小越紧）"""
    if gain >= TIGHTEN_GAIN:
        return CHANDALIER_K_TIGHT
    if state == "沸":
        return CHANDALIER_K_BOIL
    return CHANDALIER_K_BASE


def simulate_symbol_positions(
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    temp: pd.Series,
    min_hold_days: int = MIN_HOLD_DAYS,
    cooldown_days: int = COOLDOWN_DAYS,
    warm_entry_t: Optional[float] = None,
) -> pd.DataFrame:
    """逐日重放单标的状态机 + 吊灯止损，返回目标热度 size

    无实例状态，结果只取决于输入序列。size 为热度系数（0–0.85），
    组合层再乘单票上限。

    Args:
        warm_entry_t: 若设置（如 65），温度 ≥ 该值的温区允许以
            TEMP_WARM_ENTRY_SIZE 开仓，用于修复止损后「转热踩空」。

    Returns:
        DataFrame 列: size, in_position, entry_price, peak, stop, state
    """
    n = len(close)
    if n == 0:
        return pd.DataFrame(
            columns=["size", "in_position", "entry_price", "peak", "stop", "state"]
        )

    states = bucket_temperature_series(temp)
    confirmed = confirm_states(states)
    atr = calc_atr_series(high, low, close)

    c = close.to_numpy(dtype=float)
    h = high.to_numpy(dtype=float)
    a = atr.to_numpy(dtype=float)
    tvals = temp.to_numpy(dtype=float) if isinstance(temp, pd.Series) else np.asarray(temp, dtype=float)
    st = confirmed.tolist()

    size_arr = np.zeros(n)
    in_pos_arr = np.zeros(n, dtype=bool)
    entry_arr = np.full(n, np.nan)
    peak_arr = np.full(n, np.nan)
    stop_arr = np.full(n, np.nan)

    in_position = False
    entry_price = np.nan
    entry_idx = -1
    peak = np.nan
    stop = np.nan
    cooldown_until = -1

    for i in range(n):
        state = st[i]
        px = c[i]
        atr_i = a[i]
        if np.isnan(px):
            size_arr[i] = 0.0
            continue

        # --- 已持有：更新峰值/止损，检查退出 ---
        if in_position:
            hi = h[i] if not np.isnan(h[i]) else px
            peak = max(peak, hi)
            gain = peak / entry_price - 1.0 if entry_price > 0 else 0.0

            if not np.isnan(atr_i) and atr_i > 0:
                k = _chandelier_k(state, gain)
                candidate = peak - k * atr_i
                # 只上移不下调
                if np.isnan(stop) or candidate > stop:
                    stop = candidate

            hard_level = entry_price * (1.0 + HARD_STOP_PCT)

            stop_hit = (not np.isnan(stop)) and px < stop
            hard_hit = px <= hard_level
            days_held = i - entry_idx

            if stop_hit or hard_hit:
                # 止损离场，进入冷却
                in_position = False
                cooldown_until = i + cooldown_days
                entry_price = np.nan
                entry_idx = -1
                peak = np.nan
                stop = np.nan
                size_arr[i] = 0.0
            else:
                # 状态信号退出（温转平），受最短持有约束
                if state not in HOLDABLE_STATES and days_held >= min_hold_days:
                    in_position = False
                    entry_price = np.nan
                    entry_idx = -1
                    peak = np.nan
                    stop = np.nan
                    size_arr[i] = 0.0
                else:
                    size_arr[i] = TEMP_HOLD_SIZE.get(state, 0.0)

            in_pos_arr[i] = in_position
            entry_arr[i] = entry_price
            peak_arr[i] = peak
            stop_arr[i] = stop
            continue

        # --- 未持有：冷却期 / 新开仓 ---
        if i < cooldown_until:
            size_arr[i] = 0.0
            in_pos_arr[i] = False
            continue

        new_size = TEMP_NEW_ENTRY_SIZE.get(state, 0.0)
        # V2：上位温区可进场（温转热过程，修止损后踩空）
        if (
            new_size <= 0
            and warm_entry_t is not None
            and state == "温"
            and not np.isnan(tvals[i])
            and tvals[i] >= warm_entry_t
        ):
            new_size = TEMP_WARM_ENTRY_SIZE

        if new_size > 0 and not np.isnan(atr_i) and atr_i > 0:
            in_position = True
            entry_price = px
            entry_idx = i
            peak = px
            stop = px - CHANDALIER_K_BASE * atr_i
            size_arr[i] = new_size
            in_pos_arr[i] = True
            entry_arr[i] = entry_price
            peak_arr[i] = peak
            stop_arr[i] = stop
        else:
            size_arr[i] = 0.0
            in_pos_arr[i] = False

    return pd.DataFrame(
        {
            "size": size_arr,
            "in_position": in_pos_arr,
            "entry_price": entry_arr,
            "peak": peak_arr,
            "stop": stop_arr,
            "state": confirmed.values,
        },
        index=close.index,
    )


def simulate_portfolio_drawdown(
    size_by_symbol: Dict[str, pd.Series],
    close_by_symbol: Dict[str, pd.Series],
    per_name_cap: float = 0.20,
) -> Tuple[float, float]:
    """用基础权重路径模拟模型净值，返回 (current_dd, min_dd)

    不计手续费，因此模型 DD 略优于引擎 DD，调用方应留缓冲。
    """
    if not size_by_symbol:
        return 0.0, 0.0

    # 对齐日期索引
    all_dates = None
    for s in close_by_symbol.values():
        idx = s.index
        all_dates = idx if all_dates is None else all_dates.union(idx)
    if all_dates is None or len(all_dates) == 0:
        return 0.0, 0.0

    all_dates = all_dates.sort_values()
    equity = 1.0
    peak_eq = 1.0
    min_dd = 0.0
    current_dd = 0.0

    # 逐日组合收益
    aligned_close = {}
    aligned_size = {}
    for sym in close_by_symbol:
        aligned_close[sym] = close_by_symbol[sym].reindex(all_dates).ffill()
        aligned_size[sym] = size_by_symbol[sym].reindex(all_dates).ffill().fillna(0.0)

    for i in range(1, len(all_dates)):
        daily_ret = 0.0
        for sym in aligned_close:
            # size 是热度系数（最大 0.85），映射到单票仓位上限
            w = min(per_name_cap, aligned_size[sym].iloc[i - 1] / 0.85 * per_name_cap)
            prev_c = aligned_close[sym].iloc[i - 1]
            curr_c = aligned_close[sym].iloc[i]
            if not np.isnan(prev_c) and not np.isnan(curr_c) and prev_c > 0:
                daily_ret += w * (curr_c / prev_c - 1.0)
        # 超额权重视为现金（收益 0），不惩罚
        equity *= 1.0 + daily_ret
        peak_eq = max(peak_eq, equity)
        dd = equity / peak_eq - 1.0
        current_dd = dd
        min_dd = min(min_dd, dd)

    return current_dd, min_dd


def apply_drawdown_overlay(
    weights: Dict[str, float],
    current_dd: float,
    halve_threshold: float = COMPOSITE_DD_HALVE,
    flatten_threshold: float = COMPOSITE_DD_FLATTEN,
) -> Dict[str, float]:
    """组合回撤红线：DD≤halve → ×0.5；DD≤flatten → 清零"""
    if current_dd <= flatten_threshold:
        return {}
    if current_dd <= halve_threshold:
        return {s: w * 0.5 for s, w in weights.items()}
    return dict(weights)
