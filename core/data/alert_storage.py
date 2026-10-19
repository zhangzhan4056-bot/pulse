"""告警存储 — SQLite 持久化告警历史"""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

DB_PATH = Path(__file__).parent.parent.parent / "data" / "market_pulse.db"


class AlertStorage:
    """告警存储层"""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        self._ensure_db()

    def _ensure_db(self):
        """创建表（如果不存在）"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    alert_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    title TEXT NOT NULL,
                    message TEXT NOT NULL,
                    related_assets TEXT,
                    is_read INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_alert_date ON alerts(date)")

    def save(self, alert: Dict) -> int:
        """保存一条告警"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                INSERT INTO alerts (date, alert_type, severity, title, message, related_assets)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                alert.get("date", datetime.now().strftime("%Y-%m-%d")),
                alert["alert_type"],
                alert["severity"],
                alert["title"],
                alert["message"],
                alert.get("related_assets"),
            ))
            return cursor.lastrowid

    def save_dedup(self, alert: Dict, dedup_hours: int = 12) -> int:
        """保存告警（同类型+同标的去重，默认 12 小时内不重复）"""
        cutoff = (datetime.now() - timedelta(hours=dedup_hours)).strftime("%Y-%m-%d %H:%M:%S")
        related = alert.get("related_assets")
        with sqlite3.connect(self.db_path) as conn:
            # 检查是否已有相同类型的未读告警
            existing = conn.execute("""
                SELECT id FROM alerts
                WHERE alert_type = ? AND related_assets = ? AND is_read = 0 AND created_at >= ?
                LIMIT 1
            """, (alert["alert_type"], related, cutoff)).fetchone()
            if existing:
                return existing[0]  # 已存在，跳过
            cursor = conn.execute("""
                INSERT INTO alerts (date, alert_type, severity, title, message, related_assets)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                alert.get("date", datetime.now().strftime("%Y-%m-%d")),
                alert["alert_type"],
                alert["severity"],
                alert["title"],
                alert["message"],
                related,
            ))
            return cursor.lastrowid

    def load_recent(self, days: int = 7) -> pd.DataFrame:
        """加载最近 N 天的告警"""
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        with sqlite3.connect(self.db_path) as conn:
            return pd.read_sql_query(
                "SELECT * FROM alerts WHERE date >= ? ORDER BY created_at DESC",
                conn, params=(cutoff,)
            )

    def get_unread(self) -> pd.DataFrame:
        """获取未读告警"""
        with sqlite3.connect(self.db_path) as conn:
            return pd.read_sql_query(
                "SELECT * FROM alerts WHERE is_read = 0 ORDER BY created_at DESC",
                conn
            )

    def mark_read(self, alert_id: int) -> bool:
        """标记告警为已读"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE alerts SET is_read = 1 WHERE id = ?", (alert_id,))
            return True

    def mark_all_read(self) -> int:
        """标记所有告警为已读"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("UPDATE alerts SET is_read = 1 WHERE is_read = 0")
            return cursor.rowcount

    def get_stats(self) -> Dict:
        """获取告警统计"""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN is_read = 0 THEN 1 ELSE 0 END) as unread,
                    SUM(CASE WHEN severity = 'critical' THEN 1 ELSE 0 END) as critical,
                    SUM(CASE WHEN severity = 'warning' THEN 1 ELSE 0 END) as warning
                FROM alerts
                WHERE date >= date('now', '-7 days')
            """).fetchone()
            return {
                "total": row[0] or 0,
                "unread": row[1] or 0,
                "critical": row[2] or 0,
                "warning": row[3] or 0,
            }
