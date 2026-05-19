"""宏观数据 SQLite 存储"""

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

from .config import MACRO_INDICATORS

DB_PATH = Path(__file__).parent.parent.parent / "data" / "market_pulse.db"


class MacroStorage:
    """宏观数据存储（独立于 price_daily）"""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        self._ensure_table()

    def _ensure_table(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS macro_daily (
                    indicator TEXT NOT NULL,
                    date      TEXT NOT NULL,
                    value     REAL NOT NULL,
                    source    TEXT DEFAULT 'akshare',
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (indicator, date)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_macro_indicator
                ON macro_daily(indicator)
            """)

    def save(self, indicator: str, df: pd.DataFrame) -> int:
        """保存指标月度数据（upsert）"""
        if df.empty:
            return 0

        now = datetime.now().isoformat()
        count = 0

        with sqlite3.connect(self.db_path) as conn:
            for _, row in df.iterrows():
                conn.execute("""
                    INSERT INTO macro_daily (indicator, date, value, source, updated_at)
                    VALUES (?, ?, ?, 'fred', ?)
                    ON CONFLICT(indicator, date) DO UPDATE SET
                        value=excluded.value,
                        source=excluded.source,
                        updated_at=excluded.updated_at
                """, (indicator, row["date"].strftime("%Y-%m-%d"), float(row["value"]), now))
                count += 1

        return count

    def save_all(self, data: Dict[str, pd.DataFrame]) -> Dict[str, int]:
        """批量保存所有指标"""
        results = {}
        for indicator, df in data.items():
            results[indicator] = self.save(indicator, df)
        return results

    def load(
        self,
        indicator: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> pd.DataFrame:
        """读取指标数据"""
        query = "SELECT date, value FROM macro_daily WHERE indicator = ?"
        params = [indicator]

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

    def load_all(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Dict[str, pd.DataFrame]:
        """读取所有指标数据"""
        result = {}
        for indicator in MACRO_INDICATORS:
            result[indicator] = self.load(indicator, start_date, end_date)
        return result

    def get_stats(self) -> pd.DataFrame:
        """获取宏观数据统计"""
        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql_query("""
                SELECT
                    indicator,
                    COUNT(*) as rows,
                    MIN(date) as first_date,
                    MAX(date) as last_date
                FROM macro_daily
                GROUP BY indicator
                ORDER BY indicator
            """, conn)
        return df

    def has_data(self) -> bool:
        """检查是否有宏观数据"""
        stats = self.get_stats()
        return not stats.empty
