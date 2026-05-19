"""扫描历史存储

将每日扫描评分快照持久化到 SQLite，用于 P4 复盘评估。
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from core.scanner.scorer import AssetScore

DB_PATH = Path(__file__).parent.parent.parent / "data" / "market_pulse.db"


class ScanHistory:
    """扫描历史管理"""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        self._ensure_table()

    def _ensure_table(self):
        """确保 scan_history 表存在"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS scan_history (
                    date TEXT NOT NULL PRIMARY KEY,
                    scores TEXT,
                    market_regime TEXT,
                    composite_score REAL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

    def save_snapshot(self, scores: List[AssetScore], market_regime: str = "",
                      composite_score: float = 50.0):
        """保存当日扫描快照

        Args:
            scores: score_all_assets() 返回的 AssetScore 列表
            market_regime: 市场体制
            composite_score: 综合评分
        """
        today = datetime.now().strftime("%Y-%m-%d")

        scores_data = [
            {
                "symbol": s.symbol,
                "name": s.name,
                "trend_score": s.trend_score,
                "reversion_score": s.reversion_score,
                "risk_score": s.risk_score,
                "opportunity": s.opportunity,
                "momentum": s.momentum,
                "ma_alignment": s.ma_alignment,
                "macd_signal": s.macd_signal,
                "rsi": s.rsi,
                "current_drawdown": s.current_drawdown,
                "max_drawdown": s.max_drawdown,
                "volatility": s.volatility,
                "ret_1m": s.ret_1m,
                "ret_3m": s.ret_3m,
                "strategies": s.strategies,
                "summary": s.summary,
            }
            for s in scores
        ]

        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO scan_history
                    (date, scores, market_regime, composite_score)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    scores=excluded.scores,
                    market_regime=excluded.market_regime,
                    composite_score=excluded.composite_score,
                    created_at=CURRENT_TIMESTAMP
            """, (
                today,
                json.dumps(scores_data, ensure_ascii=False),
                market_regime,
                composite_score,
            ))

    def get_all_snapshots(self) -> List[Dict]:
        """获取全部扫描快照（按日期升序）"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM scan_history
                ORDER BY date ASC
            """)
            rows = cursor.fetchall()

        return [
            {
                "date": row["date"],
                "scores": json.loads(row["scores"]) if row["scores"] else [],
                "market_regime": row["market_regime"],
                "composite_score": row["composite_score"],
            }
            for row in rows
        ]

    def get_snapshot(self, date: str) -> Optional[Dict]:
        """获取指定日期的扫描快照"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM scan_history WHERE date = ?
            """, (date,))
            row = cursor.fetchone()

        if row is None:
            return None

        return {
            "date": row["date"],
            "scores": json.loads(row["scores"]) if row["scores"] else [],
            "market_regime": row["market_regime"],
            "composite_score": row["composite_score"],
        }
