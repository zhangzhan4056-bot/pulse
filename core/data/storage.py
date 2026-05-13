"""SQLite 存储层 - 数据持久化"""

import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Optional

import pandas as pd

# 数据库路径
DB_PATH = Path(__file__).parent.parent.parent / "data" / "market_pulse.db"


class DataStorage:
    """SQLite 数据存储"""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        self._ensure_db()

    def _ensure_db(self):
        """确保数据库和表存在"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS price_daily (
                    symbol TEXT NOT NULL,
                    date TEXT NOT NULL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume REAL,
                    source TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (symbol, date)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_symbol
                ON price_daily(symbol)
            """)

    def save(self, df: pd.DataFrame, symbol: str, source: str) -> int:
        """保存数据到数据库（upsert）

        Args:
            df: DataFrame，必须包含 date 列
            symbol: 资产 symbol
            source: 数据来源 (twelvedata/akshare)

        Returns:
            int: 写入/更新的行数
        """
        if df.empty:
            return 0

        # 准备数据
        records = df.copy()
        records["symbol"] = symbol
        records["source"] = source
        records["updated_at"] = datetime.now().isoformat()
        records["date"] = records["date"].dt.strftime("%Y-%m-%d")

        # upsert
        with sqlite3.connect(self.db_path) as conn:
            count = 0
            for _, row in records.iterrows():
                conn.execute("""
                    INSERT INTO price_daily (symbol, date, open, high, low, close, volume, source, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(symbol, date) DO UPDATE SET
                        open=excluded.open,
                        high=excluded.high,
                        low=excluded.low,
                        close=excluded.close,
                        volume=excluded.volume,
                        source=excluded.source,
                        updated_at=excluded.updated_at
                """, (
                    row["symbol"],
                    row["date"],
                    row.get("open"),
                    row.get("high"),
                    row.get("low"),
                    row.get("close"),
                    row.get("volume"),
                    row["source"],
                    row["updated_at"],
                ))
                count += 1
            return count

    def load(
        self,
        symbol: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> pd.DataFrame:
        """从数据库读取数据

        Args:
            symbol: 资产 symbol
            start_date: 开始日期 YYYY-MM-DD
            end_date: 结束日期 YYYY-MM-DD

        Returns:
            DataFrame
        """
        query = "SELECT date, open, high, low, close, volume FROM price_daily WHERE symbol = ?"
        params: list = [symbol]

        if start_date:
            query += " AND date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND date <= ?"
            params.append(end_date)

        query += " ORDER BY date"

        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql_query(query, conn, params=params)

        if not df.empty:
            df["date"] = pd.to_datetime(df["date"])

        return df

    def get_last_date(self, symbol: str) -> Optional[str]:
        """获取 symbol 的最新日期

        Returns:
            str: YYYY-MM-DD 格式，如果没有数据返回 None
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT MAX(date) FROM price_daily WHERE symbol = ?",
                (symbol,),
            )
            result = cursor.fetchone()
            return result[0] if result and result[0] else None

    def get_stats(self) -> pd.DataFrame:
        """获取数据库统计信息"""
        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql_query("""
                SELECT
                    symbol,
                    source,
                    COUNT(*) as rows,
                    MIN(date) as first_date,
                    MAX(date) as last_date
                FROM price_daily
                GROUP BY symbol, source
                ORDER BY symbol
            """, conn)
        return df
