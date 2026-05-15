"""数据管理器 - 统一接口"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Optional

import pandas as pd

from .config import ASSETS, US_SYMBOLS, CN_SYMBOLS, GLOBAL_SYMBOLS, US_SECTORS_SYMBOLS
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

    def fetch_global_assets(self) -> Dict[str, int]:
        """获取全球指数数据（日韩等）

        Returns:
            dict: {symbol: 写入行数}
        """
        results = {}
        for symbol in GLOBAL_SYMBOLS:
            try:
                df = self.akshare.get_global_index_hist(symbol)
                count = self.storage.save(df, symbol, "akshare_global")
                results[symbol] = count
                logger.info(f"获取 {symbol} 成功，写入 {count} 条")
            except Exception as e:
                logger.error(f"获取 {symbol} 失败: {e}")
                results[symbol] = 0
        return results

    def fetch_us_sectors(self, outputsize: int = 250) -> Dict[str, int]:
        """获取美股板块 ETF 数据

        Args:
            outputsize: 获取最近 N 天数据

        Returns:
            dict: {symbol: 写入行数}
        """
        results = {}
        for symbol in US_SECTORS_SYMBOLS:
            try:
                df = self.twelvedata.get_time_series(symbol, outputsize=outputsize)
                count = self.storage.save(df, symbol, "twelvedata")
                results[symbol] = count
                logger.info(f"获取板块 {symbol} 成功，写入 {count} 条")
            except Exception as e:
                logger.error(f"获取板块 {symbol} 失败: {e}")
                results[symbol] = 0
        return results

    def fetch_all(self) -> Dict[str, Dict[str, int]]:
        """获取所有数据

        Returns:
            dict: {"us": ..., "cn": ..., "global": ..., "sectors": ...}
        """
        return {
            "us": self.fetch_us_assets(),
            "cn": self.fetch_cn_assets(),
            "global": self.fetch_global_assets(),
            "sectors": self.fetch_us_sectors(),
        }

    def prefetch_for_backtest(self, min_days: int) -> None:
        """预取回测所需的历史数据（按需补充）

        检查数据库中各 symbol 的最早日期，不足则从 API 补充。
        仅补充 Twelve Data 源（美股/ETF），AkShare 源返回全部历史。

        Args:
            min_days: 回测需要的最少历史天数（含预热期）
        """
        from datetime import datetime, timedelta

        cutoff = (datetime.now() - timedelta(days=min_days)).strftime("%Y-%m-%d")

        # 检查 US 符号的数据覆盖情况
        all_symbols = list(US_SYMBOLS.keys()) + list(US_SECTORS_SYMBOLS.keys())
        earliest = self.storage.get_earliest_dates(all_symbols)

        # 找出缺失或不足的 symbol
        missing = []
        for symbol in all_symbols:
            first = earliest.get(symbol)
            if first is None or first > cutoff:
                missing.append(symbol)

        if not missing:
            logger.info("数据已满足回测要求，无需预取")
            return

        logger.info(f"需预取 {len(missing)} 个 US symbol，目标 {min_days} 天")

        # 用日期范围获取数据（比 outputsize 更可靠，能拿到更多历史）
        start = cutoff  # 从截止日期开始获取
        end = datetime.now().strftime("%Y-%m-%d")

        for i, symbol in enumerate(missing):
            if i > 0:
                import time
                time.sleep(1)  # 遵守速率限制
            try:
                df = self.twelvedata.get_time_series(
                    symbol, start_date=start, end_date=end
                )
                count = self.storage.save(df, symbol, "twelvedata")
                logger.info(f"预取 {symbol} 完成，写入 {count} 条")
            except Exception as e:
                logger.error(f"预取 {symbol} 失败: {e}")

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
