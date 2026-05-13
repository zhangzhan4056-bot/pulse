"""数据管理器 - 统一接口"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Optional

import pandas as pd

from .config import ASSETS, US_SYMBOLS, CN_SYMBOLS
from .twelvedata import TwelveDataFetcher
from .akshare import AkShareFetcher
from .storage import DataStorage

logger = logging.getLogger(__name__)


class DataManager:
    """数据管理器 - 协调所有数据源"""

    def __init__(self):
        self.twelvedata = TwelveDataFetcher()
        self.akshare = AkShareFetcher()
        self.storage = DataStorage()

    def fetch_us_assets(self, outputsize: int = 30) -> Dict[str, int]:
        """获取美股大类资产数据

        Args:
            outputsize: 获取最近 N 天数据

        Returns:
            dict: {symbol: 写入行数}
        """
        results = {}
        for symbol in US_SYMBOLS:
            try:
                df = self.twelvedata.get_time_series(symbol, outputsize=outputsize)
                count = self.storage.save(df, symbol, "twelvedata")
                results[symbol] = count
                logger.info(f"获取 {symbol} 成功，写入 {count} 条")
            except Exception as e:
                logger.error(f"获取 {symbol} 失败: {e}")
                results[symbol] = 0
        return results

    def fetch_cn_assets(self) -> Dict[str, int]:
        """获取 A 股指数数据

        Returns:
            dict: {symbol: 写入行数}
        """
        results = {}
        # 获取最近 1 年数据
        start_date = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")
        end_date = datetime.now().strftime("%Y%m%d")

        for symbol in CN_SYMBOLS:
            try:
                df = self.akshare.get_index_hist(symbol, start_date, end_date)
                count = self.storage.save(df, symbol, "akshare")
                results[symbol] = count
                logger.info(f"获取 {symbol} 成功，写入 {count} 条")
            except Exception as e:
                logger.error(f"获取 {symbol} 失败: {e}")
                results[symbol] = 0
        return results

    def fetch_all(self) -> Dict[str, Dict[str, int]]:
        """获取所有数据

        Returns:
            dict: {"us": {symbol: count}, "cn": {symbol: count}}
        """
        return {
            "us": self.fetch_us_assets(),
            "cn": self.fetch_cn_assets(),
        }

    def load(
        self,
        symbol: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> pd.DataFrame:
        """从缓存读取数据

        Args:
            symbol: 资产 symbol
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            DataFrame
        """
        return self.storage.load(symbol, start_date, end_date)

    def get_quote(self, symbol: str) -> dict:
        """获取美股实时报价"""
        return self.twelvedata.get_quote(symbol)

    def get_cn_realtime(self) -> pd.DataFrame:
        """获取 A 股实时行情"""
        return self.akshare.get_index_spot()

    def get_stats(self) -> pd.DataFrame:
        """获取数据库统计"""
        return self.storage.get_stats()
