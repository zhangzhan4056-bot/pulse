"""Twelve Data API 封装 - 美股/ETF 数据获取"""

from typing import Optional

import pandas as pd

from .base import BaseFetcher, rate_limit, check_response
from .config import get_api_key, TWELVEDATA_BASE_URL, TWELVEDATA_RATE_LIMIT


class TwelveDataFetcher(BaseFetcher):
    """Twelve Data API 数据获取器"""

    def __init__(self):
        super().__init__()
        self.api_key = get_api_key()
        self.base_url = TWELVEDATA_BASE_URL

    @rate_limit(TWELVEDATA_RATE_LIMIT)
    def get_quote(self, symbol: str) -> dict:
        """获取实时报价

        Args:
            symbol: 股票/ETF symbol，如 SPY, QQQ, TNX, CL

        Returns:
            dict: 包含 symbol, price, change, percent_change 等
        """
        resp = self.session.get(
            f"{self.base_url}/quote",
            params={"symbol": symbol, "apikey": self.api_key},
        )
        resp.raise_for_status()
        data = resp.json()
        check_response(data, f"quote/{symbol}")
        return data

    @rate_limit(TWELVEDATA_RATE_LIMIT)
    def get_time_series(
        self,
        symbol: str,
        interval: str = "1day",
        outputsize: int = 30,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> pd.DataFrame:
        """获取历史时间序列

        Args:
            symbol: 股票/ETF symbol
            interval: 时间间隔，1min/5min/15min/30min/1h/1day/1week/1month
            outputsize: 返回数据条数（与 start_date/end_date 二选一）
            start_date: 开始日期 YYYY-MM-DD
            end_date: 结束日期 YYYY-MM-DD

        Returns:
            DataFrame: 标准化列名 (date, open, high, low, close, volume)
        """
        params = {
            "symbol": symbol,
            "interval": interval,
            "apikey": self.api_key,
        }
        if start_date and end_date:
            params["start_date"] = start_date
            params["end_date"] = end_date
        else:
            params["outputsize"] = outputsize

        resp = self.session.get(f"{self.base_url}/time_series", params=params)
        resp.raise_for_status()
        data = resp.json()
        check_response(data, f"time_series/{symbol}")

        if "values" not in data:
            return pd.DataFrame()

        df = pd.DataFrame(data["values"])

        # 标准化列名
        df = df.rename(columns={"datetime": "date"})
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # 按日期升序
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)

        return df[["date", "open", "high", "low", "close", "volume"]]

    def get_latest_price(self, symbol: str) -> float:
        """获取最新价格（轻量接口）"""
        quote = self.get_quote(symbol)
        return float(quote["close"])
