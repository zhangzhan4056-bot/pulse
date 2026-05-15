"""AkShare 封装 - A股指数数据获取"""

import time
import logging
from typing import Optional, Dict

import akshare as ak
import pandas as pd

logger = logging.getLogger(__name__)

# 指数代码映射（纯数字 -> 带前缀）
INDEX_CODE_MAP = {
    "000001": "sh000001",  # 上证综指
    "399001": "sz399001",  # 深证成指
    "399006": "sz399006",  # 创业板指
    "000300": "sh000300",  # 沪深300
    "000688": "sh000688",  # 科创50
    "000905": "sh000905",  # 中证500
    "000016": "sh000016",  # 上证50
}

# 全球指数代码映射（symbol -> AkShare 中文名称）
GLOBAL_INDEX_MAP = {
    "日经225": "日经225指数",
    "KOSPI": "首尔综合指数",
}


class AkShareFetcher:
    """AkShare A股指数数据获取器"""

    def get_index_hist(
        self,
        symbol: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> pd.DataFrame:
        """获取指数历史数据（使用新浪源）

        Args:
            symbol: 指数代码（纯数字），如 000001, 399001, 000300
            start_date: 开始日期 YYYYMMDD（暂不支持，返回全部历史）
            end_date: 结束日期 YYYYMMDD（暂不支持，返回全部历史）

        Returns:
            DataFrame: 标准化列名 (date, open, high, low, close, volume)
        """
        # 转换为带前缀的代码
        prefixed_symbol = INDEX_CODE_MAP.get(symbol)
        if not prefixed_symbol:
            raise ValueError(f"未知的指数代码: {symbol}")

        try:
            df = ak.stock_zh_index_daily(symbol=prefixed_symbol)
        except Exception as e:
            logger.error(f"获取 A 股指数 {symbol} 失败: {e}")
            raise

        if df.empty:
            return pd.DataFrame()

        # 新浪源已经返回英文列名，直接使用
        df["date"] = pd.to_datetime(df["date"])

        # 按日期升序
        df = df.sort_values("date").reset_index(drop=True)

        return df[["date", "open", "high", "low", "close", "volume"]]

    def get_global_index_hist(
        self,
        symbol: str,
    ) -> pd.DataFrame:
        """获取全球指数历史数据（使用新浪源）

        Args:
            symbol: 指数代码，如 日经225, KOSPI (韩国)

        Returns:
            DataFrame: 标准化列名 (date, open, high, low, close, volume)
        """
        cn_name = GLOBAL_INDEX_MAP.get(symbol)
        if not cn_name:
            raise ValueError(f"未知的全球指数代码: {symbol}，支持: {list(GLOBAL_INDEX_MAP.keys())}")

        try:
            df = ak.index_global_hist_sina(symbol=cn_name)
        except Exception as e:
            logger.error(f"获取全球指数 {symbol} 失败: {e}")
            raise

        if df.empty:
            return pd.DataFrame()

        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)

        return df[["date", "open", "high", "low", "close", "volume"]]

    def get_index_spot(self) -> pd.DataFrame:
        """获取指数实时行情（使用新浪源）

        Returns:
            DataFrame: 实时行情数据
        """
        try:
            df = ak.stock_zh_index_spot_sina()
        except Exception as e:
            logger.error(f"获取 A 股实时行情失败: {e}")
            raise

        if df.empty:
            return pd.DataFrame()

        # 标准化列名
        column_map = {
            "代码": "code",
            "名称": "name",
            "最新价": "price",
            "涨跌幅": "change_pct",
            "涨跌额": "change",
            "成交量": "volume",
            "成交额": "amount",
            "振幅": "amplitude",
            "最高": "high",
            "最低": "low",
            "今开": "open",
            "昨收": "prev_close",
        }
        df = df.rename(columns=column_map)

        return df

    def batch_get_hist(
        self,
        symbols: list,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        sleep_sec: float = 1.0,
    ) -> Dict[str, pd.DataFrame]:
        """批量获取多个指数的历史数据

        Args:
            symbols: 指数代码列表
            start_date: 开始日期
            end_date: 结束日期
            sleep_sec: 请求间隔秒数（避免被封）

        Returns:
            dict: {symbol: DataFrame}
        """
        result = {}
        for i, symbol in enumerate(symbols):
            if i > 0:
                time.sleep(sleep_sec)
            try:
                df = self.get_index_hist(symbol, start_date, end_date)
                result[symbol] = df
                logger.info(f"获取 {symbol} 成功，{len(df)} 条数据")
            except Exception as e:
                logger.error(f"获取 {symbol} 失败: {e}")
                result[symbol] = pd.DataFrame()
        return result
