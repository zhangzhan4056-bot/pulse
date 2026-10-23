"""数据管理器 - 统一接口"""

import logging
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

from .config import ASSETS, US_SYMBOLS, CN_SYMBOLS, GLOBAL_SYMBOLS, US_SECTORS_SYMBOLS, US_STOCK_UNIVERSE
from .storage import DataStorage, DB_PATH

logger = logging.getLogger(__name__)

# Snapshot 路径（git 追踪的便携式数据库副本）
SNAPSHOT_PATH = DB_PATH.parent / "market_pulse_snapshot.db"


class DataManager:
    """数据管理器 - 协调所有数据源"""

    def __init__(self):
        # 抓取器延迟导入：import manager 本身不应依赖 akshare 是否可用
        from .twelvedata import TwelveDataFetcher
        from .akshare import AkShareFetcher

        self._bootstrap_if_needed()
        self.twelvedata = TwelveDataFetcher()
        self.akshare = AkShareFetcher()
        self.storage = DataStorage()

    @staticmethod
    def _bootstrap_if_needed():
        """Cloud 冷启动时从 snapshot 恢复数据"""
        if not SNAPSHOT_PATH.exists():
            return
        if DB_PATH.exists():
            try:
                with sqlite3.connect(DB_PATH) as conn:
                    count = conn.execute("SELECT COUNT(*) FROM price_daily").fetchone()[0]
                    if count > 0:
                        return
            except Exception:
                pass
        logger.info("从 snapshot 恢复数据...")
        shutil.copy2(SNAPSHOT_PATH, DB_PATH)

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

    def us_stocks_need_refresh(
        self,
        required_start: str,
        required_end: str,
        tail_lag_days: int = 7,
        symbols: Optional[list] = None,
    ) -> list:
        """返回需要 API 补数的美股个股（缺数 / 尾部过旧 / 头部可再延）。

        Args:
            required_start: 调用方真正需要的最早日期（预热请由调用方先扣好）
            required_end: 需要覆盖的最后日期
            symbols: 只检查指定 symbol；默认 US_STOCK_UNIVERSE

        已有足够覆盖时返回空列表。免费计划 outputsize 有限，
        已有约 8 年历史时头部再拉也延不了，不再重复请求。
        """
        if symbols is None:
            symbols = list(US_STOCK_UNIVERSE.keys())
        earliest = self.storage.get_earliest_dates(symbols)
        today = datetime.now().strftime("%Y-%m-%d")
        end_dt = datetime.strptime(min(required_end, today), "%Y-%m-%d")
        start_dt = datetime.strptime(required_start, "%Y-%m-%d")

        need = []
        for sym in symbols:
            first = earliest.get(sym)
            last = self.storage.get_last_date(sym)
            if first is None or last is None:
                need.append(sym)
                continue
            last_dt = datetime.strptime(last, "%Y-%m-%d")
            first_dt = datetime.strptime(first, "%Y-%m-%d")
            # 尾部过旧（周末/假日/未开盘允许滞后）
            if (end_dt - last_dt).days > tail_lag_days:
                need.append(sym)
                continue
            # 头部不足：仅当已有跨度明显短于需求才补
            #（~2000 个交易日 ≈ 8 年日历天，免费计划单次窗口上限附近）
            if first_dt > start_dt and (last_dt - first_dt).days < 2000:
                need.append(sym)
        return need

    def fetch_us_stocks(
        self,
        outputsize: int = 250,
        sleep_sec: float = 8.0,
        symbols: Optional[list] = None,
    ) -> Dict[str, int]:
        """获取美股个股数据（成长动量策略专用，免费计划 8 次/分钟）

        Args:
            outputsize: 获取最近 N 根 K 线
            sleep_sec: 请求间隔，默认 8 秒（贴合 8 次/分钟）
            symbols: 只拉指定 symbol；默认全部 US_STOCK_UNIVERSE
        """
        import time
        results = {}
        if symbols is None:
            symbols = list(US_STOCK_UNIVERSE.keys())
        for i, symbol in enumerate(symbols):
            if i > 0 and sleep_sec > 0:
                time.sleep(sleep_sec)
            try:
                df = self.twelvedata.get_time_series(symbol, outputsize=outputsize)
                count = self.storage.save(df, symbol, "twelvedata")
                results[symbol] = count
                logger.info(f"获取个股 {symbol} 成功，写入 {count} 条")
            except Exception as e:
                logger.error(f"获取个股 {symbol} 失败: {e}")
                results[symbol] = 0
        return results

    def fetch_cn_assets(self, symbols: Optional[list] = None) -> Dict[str, int]:
        """获取 A 股指数数据

        Args:
            symbols: 只拉指定 symbol；默认全部 CN_SYMBOLS

        Returns:
            dict: {symbol: 写入行数}
        """
        results = {}
        # 获取最近 1 年数据
        start_date = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")
        end_date = datetime.now().strftime("%Y%m%d")

        for symbol in (symbols if symbols is not None else list(CN_SYMBOLS.keys())):
            try:
                df = self.akshare.get_index_hist(symbol, start_date, end_date)
                count = self.storage.save(df, symbol, "akshare")
                results[symbol] = count
                logger.info(f"获取 {symbol} 成功，写入 {count} 条")
            except Exception as e:
                logger.error(f"获取 {symbol} 失败: {e}")
                results[symbol] = 0
        return results

    def fetch_global_assets(self, symbols: Optional[list] = None) -> Dict[str, int]:
        """获取全球指数数据（日韩等）

        Args:
            symbols: 只拉指定 symbol；默认全部 GLOBAL_SYMBOLS

        Returns:
            dict: {symbol: 写入行数}
        """
        results = {}
        for symbol in (symbols if symbols is not None else list(GLOBAL_SYMBOLS.keys())):
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

    def fetch_market_data_parallel(self) -> Dict:
        """并行获取所有市场数据（Twelve Data + AkShare + FRED）

        Twelve Data 限速串行执行，AkShare 和 FRED 并行运行。
        总耗时约等于 Twelve Data 单独耗时（~105s），而非三者之和（~140s）。

        Returns:
            dict: {"market": {...}, "akshare": {...}, "macro": {...}}
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        results = {}
        today = datetime.now().strftime("%Y-%m-%d")

        def fetch_twelvedata():
            td_results = {}
            all_symbols = list(US_SYMBOLS.keys()) + list(US_SECTORS_SYMBOLS.keys())
            for symbol in all_symbols:
                last = self.storage.get_last_date(symbol)
                if last == today:
                    td_results[symbol] = 0
                    continue
                try:
                    df = self.twelvedata.get_time_series(symbol, outputsize=2000)
                    count = self.storage.save(df, symbol, "twelvedata")
                    td_results[symbol] = count
                except Exception as e:
                    td_results[symbol] = f"错误: {e}"
            return td_results

        def fetch_akshare():
            cn = self.fetch_cn_assets()
            global_ = self.fetch_global_assets()
            return {"cn": cn, "global": global_}

        def fetch_fred():
            from .macro_fetcher import MacroFetcher
            from .macro_storage import MacroStorage
            fetcher = MacroFetcher()
            storage = MacroStorage()
            data = fetcher.fetch_all()
            return storage.save_all(data)

        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {
                pool.submit(fetch_twelvedata): "market",
                pool.submit(fetch_akshare): "akshare",
                pool.submit(fetch_fred): "macro",
            }
            for future in as_completed(futures):
                key = futures[future]
                try:
                    results[key] = future.result()
                except Exception as e:
                    results[key] = {"error": str(e)}

        return results

    def prefetch_for_backtest(self, min_days: int) -> None:
        """预取回测所需的历史数据（按需补充）

        检查数据库中各 symbol 的最早日期，不足则从 API 补充。
        - 美股/ETF: Twelve Data（限速 8 次/分钟，按日期范围获取）
        - A股/全球指数: AkShare（返回全部历史，无限速）

        Args:
            min_days: 回测需要的最少历史天数（含预热期）
        """
        from datetime import datetime, timedelta

        cutoff = (datetime.now() - timedelta(days=min_days)).strftime("%Y-%m-%d")

        # 检查所有符号的数据覆盖情况
        us_symbols = list(US_SYMBOLS.keys()) + list(US_SECTORS_SYMBOLS.keys())
        cn_symbols = list(CN_SYMBOLS.keys())
        global_symbols = list(GLOBAL_SYMBOLS.keys())
        all_symbols = us_symbols + cn_symbols + global_symbols
        earliest = self.storage.get_earliest_dates(all_symbols)

        # 只补「真正缺数」的 symbol：起点晚但已有 ≥1 年（上市晚/源数据起点晚）不再追
        def _need_history(symbol: str, first: Optional[str]) -> bool:
            if first is None:
                return True
            if first <= cutoff:
                return False
            last = self.storage.get_last_date(symbol)
            if last is None:
                return True
            span_days = (
                datetime.strptime(last, "%Y-%m-%d") - datetime.strptime(first, "%Y-%m-%d")
            ).days
            return span_days < 365

        missing_us = [s for s in us_symbols if _need_history(s, earliest.get(s))]
        missing_cn = [s for s in cn_symbols if _need_history(s, earliest.get(s))]
        missing_global = [s for s in global_symbols if _need_history(s, earliest.get(s))]

        if not missing_us and not missing_cn and not missing_global:
            logger.info("数据已满足回测要求，无需预取")
            return

        # 1. 预取 A 股指数（只拉缺的）
        if missing_cn:
            logger.info(f"需预取 {len(missing_cn)} 个 A 股 symbol: {missing_cn}")
            try:
                cn_results = self.fetch_cn_assets(symbols=missing_cn)
                for sym, count in cn_results.items():
                    if isinstance(count, int) and count > 0:
                        logger.info(f"预取 A 股 {sym} 完成，写入 {count} 条")
            except Exception as e:
                logger.error(f"预取 A 股失败: {e}")

        # 2. 预取全球指数（只拉缺的）
        if missing_global:
            logger.info(f"需预取 {len(missing_global)} 个全球 symbol: {missing_global}")
            try:
                global_results = self.fetch_global_assets(symbols=missing_global)
                for sym, count in global_results.items():
                    if isinstance(count, int) and count > 0:
                        logger.info(f"预取全球 {sym} 完成，写入 {count} 条")
            except Exception as e:
                logger.error(f"预取全球指数失败: {e}")

        # 3. 预取美股/ETF（Twelve Data，限速，用 outputsize 获取更多历史）
        # 注意：Twelve Data 免费计划用 start_date/end_date 只返回约 2 年数据，
        # 用 outputsize 可以返回更多历史。min_days 换算成交易日。
        if missing_us:
            trading_days_needed = max(250, int(min_days * 252 / 365))
            logger.info(f"需预取 {len(missing_us)} 个 US symbol，目标 {trading_days_needed} 个交易日")

            for i, symbol in enumerate(missing_us):
                if i > 0:
                    import time
                    time.sleep(8)  # 免费计划 8 次/分钟
                try:
                    df = self.twelvedata.get_time_series(
                        symbol, outputsize=trading_days_needed
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

    def fetch_fund_data(self, fund_codes: Optional[list] = None) -> Dict[str, int]:
        """获取场外基金净值数据（AkShare）

        Args:
            fund_codes: 基金代码列表，为 None 时从 WATCHED_HOLDINGS 中获取 type=fund 的

        Returns:
            {fund_code: row_count}
        """
        from .config import WATCHED_HOLDINGS

        if fund_codes is None:
            fund_codes = [k for k, v in WATCHED_HOLDINGS.items() if v.get("type") == "fund"]

        results = {}
        for code in fund_codes:
            try:
                import akshare as ak
                df = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
                if df.empty:
                    results[code] = "空数据"
                    continue

                # 转换为标准格式
                df = df.rename(columns={
                    "净值日期": "date",
                    "单位净值": "close",
                    "日增长率": "change_pct",
                })
                # date 列可能是 datetime.date 对象或字符串，统一转 datetime
                df["date"] = pd.to_datetime(df["date"])
                df["open"] = df["close"]  # 基金只有收盘净值
                df["high"] = df["close"]
                df["low"] = df["close"]
                df["volume"] = 0
                df = df[["date", "open", "high", "low", "close", "volume"]].sort_values("date")

                count = self.storage.save(df, code, "akshare_fund")
                results[code] = count
                logger.info(f"基金 {code}: 写入 {count} 条")
            except Exception as e:
                results[code] = f"错误: {e}"
                logger.error(f"基金 {code} 获取失败: {e}")

        return results

    def get_stats(self) -> pd.DataFrame:
        """获取数据库统计"""
        return self.storage.get_stats()
