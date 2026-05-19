"""宏观相似度分析引擎

算法：百分位排名 + 欧氏距离
1. 构建 (月份 x 指标) 矩阵
2. 每个指标计算百分位排名（0-100%）
3. 当前月与历史月在百分位空间计算欧氏距离
4. 取 TOP-K 最相似月份，查询后续 SPY/QQQ 收益率
"""

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .config import MACRO_INDICATORS, MACRO_DISPLAY_ORDER


def build_macro_matrix(
    data: Dict[str, pd.DataFrame],
    min_coverage: float = 0.6,
) -> pd.DataFrame:
    """构建宏观指标矩阵

    Args:
        data: {indicator: DataFrame(date, value)}
        min_coverage: 月份必须有至少这个比例的指标才有值

    Returns:
        DataFrame: index=月份(DatetimeIndex), columns=指标名, values=原始值
    """
    series_dict = {}
    for indicator in MACRO_DISPLAY_ORDER:
        df = data.get(indicator)
        if df is not None and not df.empty:
            s = df.set_index("date")["value"]
            s.name = indicator
            series_dict[indicator] = s

    if not series_dict:
        return pd.DataFrame()

    matrix = pd.DataFrame(series_dict)
    matrix = matrix.resample("ME").last()

    n_indicators = len(series_dict)
    min_count = max(1, int(n_indicators * min_coverage))
    valid_mask = matrix.notna().sum(axis=1) >= min_count
    matrix = matrix[valid_mask]

    return matrix


def percentile_rank(matrix: pd.DataFrame) -> pd.DataFrame:
    """百分位排名标准化

    对每个指标，计算当前值在历史分布中的百分位位置（0-100%）。
    比 Z-score 更适合非平稳的宏观数据：不受历史均值/标准差漂移影响。

    Returns:
        DataFrame: 与 matrix 同维度，值为 0-100 的百分位
    """
    return matrix.rank(pct=True) * 100


def find_similar_months(
    ranked: pd.DataFrame,
    target_month: pd.Timestamp,
    top_k: int = 5,
    exclude_window: int = 6,
) -> pd.DataFrame:
    """查找与目标月份最相似的历史月份

    Args:
        ranked: 百分位排名矩阵
        target_month: 目标月份
        top_k: 返回前 K 个最相似月份
        exclude_window: 排除目标月份前后 N 个月

    Returns:
        DataFrame: (month, distance, rank) 按距离升序
    """
    if target_month not in ranked.index:
        raise ValueError(f"目标月份 {target_month} 不在数据范围内")

    target_vec = ranked.loc[target_month].fillna(50).values  # NaN 填中位数
    target_idx = ranked.index.get_loc(target_month)

    distances = []
    for i, month in enumerate(ranked.index):
        if abs(i - target_idx) <= exclude_window:
            continue
        row = ranked.loc[month]
        if row.notna().sum() < 3:
            continue
        row_filled = row.fillna(50).values
        dist = np.linalg.norm(target_vec - row_filled)
        distances.append({"month": month, "distance": dist})

    if not distances:
        return pd.DataFrame(columns=["month", "distance", "rank"])

    result = pd.DataFrame(distances)
    result = result.sort_values("distance").head(top_k).reset_index(drop=True)
    result["rank"] = range(1, len(result) + 1)

    return result


def calc_forward_returns(
    spy_df: pd.DataFrame,
    qqq_df: pd.DataFrame,
    months: List[pd.Timestamp],
    forward_periods: Optional[List[int]] = None,
) -> pd.DataFrame:
    """计算指定月份之后的资产收益率"""
    if forward_periods is None:
        forward_periods = [6, 12]

    spy_monthly = _to_monthly_close(spy_df)
    qqq_monthly = _to_monthly_close(qqq_df)

    results = []
    for month in months:
        row = {"month": month}
        for asset_name, monthly_prices in [("spy", spy_monthly), ("qqq", qqq_monthly)]:
            if monthly_prices.empty:
                for p in forward_periods:
                    row[f"{asset_name}_{p}m"] = None
                continue

            start_prices = monthly_prices[
                (monthly_prices.index >= month) &
                (monthly_prices.index <= month + pd.DateOffset(months=3))
            ]
            if start_prices.empty:
                for p in forward_periods:
                    row[f"{asset_name}_{p}m"] = None
                continue
            start_price = start_prices.iloc[0]

            for p in forward_periods:
                target_date = month + pd.DateOffset(months=p)
                future_prices = monthly_prices[monthly_prices.index >= target_date]
                if future_prices.empty:
                    row[f"{asset_name}_{p}m"] = None
                else:
                    end_price = future_prices.iloc[0]
                    ret = (end_price / start_price - 1) * 100
                    row[f"{asset_name}_{p}m"] = round(ret, 2)

        results.append(row)

    return pd.DataFrame(results)


def _to_monthly_close(df: pd.DataFrame) -> pd.Series:
    """将日数据聚合为月末收盘价"""
    if df.empty:
        return pd.Series(dtype=float)
    s = df.set_index("date")["close"]
    return s.resample("ME").last().dropna()


def pca_reduce(ranked: pd.DataFrame, n_components: int = 2) -> pd.DataFrame:
    """PCA 降维"""
    clean = ranked.fillna(50)
    X = clean.values

    if X.shape[0] < n_components + 1:
        return pd.DataFrame(columns=["pc1", "pc2", "year", "month_label"])

    X_centered = X - X.mean(axis=0)
    cov = np.cov(X_centered, rowvar=False)
    eigenvalues, eigenvectors = np.linalg.eigh(cov)
    idx = np.argsort(eigenvalues)[::-1]
    eigenvectors = eigenvectors[:, idx]
    components = eigenvectors[:, :n_components]
    projected = X_centered @ components

    result = pd.DataFrame(
        projected,
        columns=[f"pc{i+1}" for i in range(n_components)],
        index=clean.index,
    )
    result["year"] = result.index.year
    result["month_label"] = result.index.strftime("%Y-%m")

    return result


def fetch_long_history_equity() -> Tuple[pd.DataFrame, pd.DataFrame]:
    """从 Twelve Data 获取 SPY/QQQ 月线长历史数据

    月线数据从上市日起可用：SPY 1993, QQQ 1999。
    """
    import requests
    from .config import get_api_key, TWELVEDATA_BASE_URL

    api_key = get_api_key()

    def _fetch_monthly(symbol: str) -> pd.DataFrame:
        url = (
            f"{TWELVEDATA_BASE_URL}/time_series"
            f"?symbol={symbol}&interval=1month&outputsize=5000&apikey={api_key}"
        )
        r = requests.get(url, timeout=15)
        data = r.json()
        if "values" not in data or not data["values"]:
            return pd.DataFrame(columns=["date", "close"])
        records = [
            {"date": pd.Timestamp(v["datetime"]), "close": float(v["close"])}
            for v in data["values"]
        ]
        df = pd.DataFrame(records).sort_values("date").reset_index(drop=True)
        return df

    spy_df = pd.DataFrame(columns=["date", "close"])
    qqq_df = pd.DataFrame(columns=["date", "close"])

    try:
        spy_df = _fetch_monthly("SPY")
    except Exception:
        pass

    try:
        qqq_df = _fetch_monthly("QQQ")
    except Exception:
        pass

    return spy_df, qqq_df


def compute_macro_analysis(
    macro_data: Dict[str, pd.DataFrame],
    spy_df: Optional[pd.DataFrame] = None,
    qqq_df: Optional[pd.DataFrame] = None,
    top_k: int = 5,
) -> Dict:
    """宏观相似度分析的完整入口

    Returns:
        dict: {
            "matrix": 原始宏观矩阵,
            "ranked": 百分位排名矩阵,
            "current_month": 当前月份,
            "current_values": 当前月各指标原始值,
            "current_ranks": 当前月各指标百分位,
            "similar": TOP-K 相似月份 DataFrame,
            "forward_returns": 前向收益率 DataFrame,
            "pca": PCA 降维结果,
        }
    """
    matrix = build_macro_matrix(macro_data)
    if matrix.empty:
        return {"error": "宏观数据不足，无法进行分析"}

    ranked = percentile_rank(matrix)

    current_month = ranked.index[-1]
    current_values = matrix.loc[current_month]
    current_ranks = ranked.loc[current_month]

    similar = find_similar_months(ranked, current_month, top_k=top_k)

    if spy_df is None or qqq_df is None:
        spy_df, qqq_df = fetch_long_history_equity()

    forward_returns = calc_forward_returns(
        spy_df, qqq_df, similar["month"].tolist()
    )

    pca_result = pca_reduce(ranked)

    spy_monthly = _to_monthly_close(spy_df)
    qqq_monthly = _to_monthly_close(qqq_df)

    return {
        "matrix": matrix,
        "ranked": ranked,
        "current_month": current_month,
        "current_values": current_values,
        "current_ranks": current_ranks,
        "similar": similar,
        "forward_returns": forward_returns,
        "pca": pca_result,
        "spy_monthly": spy_monthly,
        "qqq_monthly": qqq_monthly,
    }
