"""宏观数据获取器 - FRED (Federal Reserve Economic Data) 封装

数据来源: https://fred.stlouisfed.org
- 免费，无需 API key（CSV 直接下载）
- 所有指标从 1990 年起，月度频率
- CPI/PPI 是指数水平，需自行计算同比变化率
"""

import logging
from typing import Dict

import pandas as pd

from .config import MACRO_INDICATORS, MACRO_DISPLAY_ORDER

logger = logging.getLogger(__name__)

# FRED CSV 下载 URL 模板
FRED_CSV_URL = (
    "https://fred.stlouisfed.org/graph/fredgraph.csv"
    "?id={series_id}&cosd=1990-01-01&coed=2026-12-31"
)


class MacroFetcher:
    """美国宏观数据获取器（基于 FRED CSV 下载）"""

    def _fetch_fred_csv(self, series_id: str) -> pd.DataFrame:
        """从 FRED 下载 CSV 数据

        Args:
            series_id: FRED 系列 ID，如 'CPIAUCSL', 'FEDFUNDS'

        Returns:
            DataFrame: (date, value) 按日期升序
        """
        url = FRED_CSV_URL.format(series_id=series_id)
        df = pd.read_csv(url)

        if df.empty:
            return pd.DataFrame(columns=["date", "value"])

        # FRED CSV 格式: observation_date, {series_id}
        date_col = df.columns[0]  # observation_date
        value_col = df.columns[1]  # series_id

        df = df.rename(columns={date_col: "date", value_col: "value"})
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df = df.dropna(subset=["date", "value"])
        df = df.sort_values("date").reset_index(drop=True)

        return df

    def _fetch_indicator(self, indicator: str) -> pd.DataFrame:
        """获取单个宏观指标原始数据

        CPI/PPI 返回的是指数水平，需要计算同比变化率。
        其他指标直接返回原始值。

        Args:
            indicator: 指标 key

        Returns:
            DataFrame: (date, value) 月度频率
        """
        config = MACRO_INDICATORS[indicator]
        series_id = config["fred_series"]

        df = self._fetch_fred_csv(series_id)
        if df.empty:
            return df

        # CPI/PPI 需要计算同比变化率
        if config.get("is_index"):
            df = df.set_index("date")
            df["value"] = df["value"].pct_change(12) * 100  # 12 个月同比
            df = df.dropna().reset_index()

        return df

    def fetch_all(self) -> Dict[str, pd.DataFrame]:
        """获取全部宏观指标

        Returns:
            dict: {indicator: DataFrame(date, value)}
        """
        results = {}
        for indicator in MACRO_DISPLAY_ORDER:
            try:
                logger.info(f"获取宏观指标 {indicator}...")
                df = self._fetch_indicator(indicator)
                results[indicator] = df
                logger.info(f"  {indicator}: {len(df)} 条月度数据")
            except Exception as e:
                logger.error(f"获取 {indicator} 失败: {e}")
                results[indicator] = pd.DataFrame()

        return results
