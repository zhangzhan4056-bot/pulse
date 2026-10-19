"""SEC EDGAR 13-F 机构持仓存储层"""

import sqlite3
import json
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

DB_PATH = Path(__file__).parent.parent.parent / "data" / "market_pulse.db"


class ThirteenFStorage:
    """13-F 机构持仓 SQLite 存储"""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        self._ensure_db()

    def _ensure_db(self):
        """确保数据库和表存在"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS thirteen_f_managers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cik TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    category TEXT DEFAULT 'other',
                    is_default INTEGER DEFAULT 0,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_13f_mgr_cik
                ON thirteen_f_managers(cik)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS thirteen_f_holdings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filing_id TEXT NOT NULL,
                    cik TEXT NOT NULL,
                    manager_name TEXT NOT NULL,
                    filing_date TEXT NOT NULL,
                    period_end TEXT NOT NULL,
                    cusip TEXT NOT NULL,
                    name_of_issuer TEXT NOT NULL,
                    title_of_class TEXT,
                    value INTEGER,
                    shares INTEGER,
                    shares_type TEXT DEFAULT 'SH',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_13f_cik
                ON thirteen_f_holdings(cik)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_13f_cusip
                ON thirteen_f_holdings(cusip)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_13f_period
                ON thirteen_f_holdings(period_end)
            """)
            conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_13f_unique
                ON thirteen_f_holdings(filing_id, cusip)
            """)

    # ── 机构管理 ──────────────────────────────────────────────

    def seed_default_managers(self) -> int:
        """播入默认跟踪机构，返回新增数量"""
        from core.data.config import THIRTEEN_F_MANAGERS
        count = 0
        with sqlite3.connect(self.db_path) as conn:
            for cik, info in THIRTEEN_F_MANAGERS.items():
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO thirteen_f_managers (cik, name, category, is_default) "
                        "VALUES (?, ?, ?, 1)",
                        (cik, info["name"], info["category"]),
                    )
                    count += conn.total_changes
                except sqlite3.IntegrityError:
                    pass
        return count

    def add_manager(self, cik: str, name: str, category: str = "other") -> bool:
        """添加自定义跟踪机构，返回是否成功"""
        cik = cik.zfill(10)
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT INTO thirteen_f_managers (cik, name, category, is_default) "
                    "VALUES (?, ?, ?, 0)",
                    (cik, name, category),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def remove_manager(self, cik: str) -> bool:
        """移除非默认机构，返回是否成功"""
        cik = cik.zfill(10)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT is_default FROM thirteen_f_managers WHERE cik = ?", (cik,)
            )
            row = cursor.fetchone()
            if not row or row[0] == 1:
                return False
            conn.execute("DELETE FROM thirteen_f_managers WHERE cik = ?", (cik,))
        return True

    def get_tracked_managers(self) -> pd.DataFrame:
        """返回所有跟踪机构"""
        with sqlite3.connect(self.db_path) as conn:
            return pd.read_sql_query(
                "SELECT * FROM thirteen_f_managers ORDER BY is_default DESC, name",
                conn,
            )

    # ── 持仓数据 ──────────────────────────────────────────────

    def save_holdings(self, holdings: List[Dict]) -> int:
        """批量保存持仓数据，返回新增条数"""
        if not holdings:
            return 0
        count = 0
        with sqlite3.connect(self.db_path) as conn:
            for h in holdings:
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO thirteen_f_holdings "
                        "(filing_id, cik, manager_name, filing_date, period_end, "
                        "cusip, name_of_issuer, title_of_class, value, shares, shares_type) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            h["filing_id"], h["cik"], h["manager_name"],
                            h["filing_date"], h["period_end"],
                            h["cusip"], h["name_of_issuer"],
                            h.get("title_of_class", ""),
                            h.get("value", 0), h.get("shares", 0),
                            h.get("shares_type", "SH"),
                        ),
                    )
                    count += conn.total_changes
                except sqlite3.IntegrityError:
                    pass
        return count

    def get_latest_filing_date(self, cik: str) -> Optional[str]:
        """获取某机构最新 filing_date"""
        cik = cik.zfill(10)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT MAX(filing_date) FROM thirteen_f_holdings WHERE cik = ?",
                (cik,),
            )
            row = cursor.fetchone()
            return row[0] if row and row[0] else None

    def load_latest_holdings(self, cik: str) -> pd.DataFrame:
        """获取某机构最近一期持仓"""
        cik = cik.zfill(10)
        with sqlite3.connect(self.db_path) as conn:
            return pd.read_sql_query(
                "SELECT * FROM thirteen_f_holdings "
                "WHERE cik = ? AND period_end = "
                "(SELECT MAX(period_end) FROM thirteen_f_holdings WHERE cik = ?) "
                "ORDER BY value DESC",
                conn,
                params=(cik, cik),
            )

    def load_all_holdings(self) -> pd.DataFrame:
        """获取全部持仓数据"""
        with sqlite3.connect(self.db_path) as conn:
            return pd.read_sql_query(
                "SELECT * FROM thirteen_f_holdings ORDER BY period_end DESC, value DESC",
                conn,
            )

    def load_periods(self, cik: str) -> List[str]:
        """获取某机构所有可用季度"""
        cik = cik.zfill(10)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT DISTINCT period_end FROM thirteen_f_holdings "
                "WHERE cik = ? ORDER BY period_end DESC",
                (cik,),
            )
            return [row[0] for row in cursor.fetchall()]

    # ── 增持信号检测 ──────────────────────────────────────────

    def get_accumulation_signals(self, lookback_quarters: int = 4) -> pd.DataFrame:
        """检测连续增持信号：shares↑ AND weight↑（连续季度）

        Returns:
            DataFrame with columns: cusip, name_of_issuer, accumulating_managers,
            manager_count, latest_period
        """
        all_holdings = self.load_all_holdings()
        if all_holdings.empty:
            return pd.DataFrame()

        # 取最近 N 个季度
        periods = sorted(all_holdings["period_end"].unique(), reverse=True)
        recent_periods = periods[:lookback_quarters]
        if len(recent_periods) < 2:
            return pd.DataFrame()

        df = all_holdings[all_holdings["period_end"].isin(recent_periods)].copy()

        # 计算每家机构每季度的总组合价值
        portfolio_totals = (
            df.groupby(["cik", "period_end"])["value"]
            .sum()
            .reset_index()
            .rename(columns={"value": "total_value"})
        )
        df = df.merge(portfolio_totals, on=["cik", "period_end"])
        df["weight"] = df["value"] / df["total_value"].replace(0, float("nan"))

        # 按 (cik, cusip) 检查连续季度
        results = []
        for (cik, cusip), group in df.groupby(["cik", "cusip"]):
            group = group.sort_values("period_end")
            if len(group) < 2:
                continue
            all_accumulating = True
            for i in range(1, len(group)):
                prev = group.iloc[i - 1]
                curr = group.iloc[i]
                if not (
                    curr["shares"] > prev["shares"]
                    and curr["weight"] > prev["weight"]
                ):
                    all_accumulating = False
                    break
            if all_accumulating:
                results.append(
                    {
                        "cusip": cusip,
                        "name_of_issuer": group.iloc[-1]["name_of_issuer"],
                        "cik": cik,
                        "manager_name": group.iloc[-1]["manager_name"],
                        "quarters_accumulating": len(group),
                        "latest_period": group.iloc[-1]["period_end"],
                        "shares_change": int(
                            group.iloc[-1]["shares"] - group.iloc[0]["shares"]
                        ),
                        "value_change": int(
                            group.iloc[-1]["value"] - group.iloc[0]["value"]
                        ),
                        "weight_change": float(
                            group.iloc[-1]["weight"] - group.iloc[0]["weight"]
                        ),
                    }
                )

        if not results:
            return pd.DataFrame()

        result_df = pd.DataFrame(results)
        # 按 CUSIP 聚合：哪些机构在增持
        aggregated = (
            result_df.groupby(["cusip", "name_of_issuer"])
            .agg(
                accumulating_managers=("manager_name", lambda x: ", ".join(x)),
                manager_count=("cik", "nunique"),
                latest_period=("latest_period", "max"),
                total_shares_change=("shares_change", "sum"),
                total_value_change=("value_change", "sum"),
                avg_weight_change=("weight_change", "mean"),
            )
            .reset_index()
        )
        return aggregated.sort_values("manager_count", ascending=False)

    def get_reduction_signals(self, lookback_quarters: int = 4) -> pd.DataFrame:
        """检测连续减持信号：shares↓ AND weight↓（连续季度）

        Returns:
            DataFrame with columns: cusip, name_of_issuer, reducing_managers,
            manager_count, latest_period, total_shares_change, avg_weight_change
        """
        all_holdings = self.load_all_holdings()
        if all_holdings.empty:
            return pd.DataFrame()

        periods = sorted(all_holdings["period_end"].unique(), reverse=True)
        recent_periods = periods[:lookback_quarters]
        if len(recent_periods) < 2:
            return pd.DataFrame()

        df = all_holdings[all_holdings["period_end"].isin(recent_periods)].copy()

        portfolio_totals = (
            df.groupby(["cik", "period_end"])["value"]
            .sum()
            .reset_index()
            .rename(columns={"value": "total_value"})
        )
        df = df.merge(portfolio_totals, on=["cik", "period_end"])
        df["weight"] = df["value"] / df["total_value"].replace(0, float("nan"))

        results = []
        for (cik, cusip), group in df.groupby(["cik", "cusip"]):
            group = group.sort_values("period_end")
            if len(group) < 2:
                continue
            all_reducing = True
            for i in range(1, len(group)):
                prev = group.iloc[i - 1]
                curr = group.iloc[i]
                if not (
                    curr["shares"] < prev["shares"]
                    and curr["weight"] < prev["weight"]
                ):
                    all_reducing = False
                    break
            if all_reducing:
                results.append(
                    {
                        "cusip": cusip,
                        "name_of_issuer": group.iloc[-1]["name_of_issuer"],
                        "cik": cik,
                        "manager_name": group.iloc[-1]["manager_name"],
                        "quarters_reducing": len(group),
                        "latest_period": group.iloc[-1]["period_end"],
                        "shares_change": int(
                            group.iloc[-1]["shares"] - group.iloc[0]["shares"]
                        ),
                        "value_change": int(
                            group.iloc[-1]["value"] - group.iloc[0]["value"]
                        ),
                        "weight_change": float(
                            group.iloc[-1]["weight"] - group.iloc[0]["weight"]
                        ),
                    }
                )

        if not results:
            return pd.DataFrame()

        result_df = pd.DataFrame(results)
        aggregated = (
            result_df.groupby(["cusip", "name_of_issuer"])
            .agg(
                reducing_managers=("manager_name", lambda x: ", ".join(x)),
                manager_count=("cik", "nunique"),
                latest_period=("latest_period", "max"),
                total_shares_change=("shares_change", "sum"),
                total_value_change=("value_change", "sum"),
                avg_weight_change=("weight_change", "mean"),
            )
            .reset_index()
        )
        return aggregated.sort_values("manager_count", ascending=False)

    # ── 季度趋势对比 ──────────────────────────────────────────

    def get_quarterly_signals(self) -> Dict[str, pd.DataFrame]:
        """计算两个独立季度变动的增减持信号，用于趋势对比

        prev = Q-2→Q-1 的单季度变动（上季度 vs 上上季度）
        curr = Q-1→Q0 的单季度变动（本季度 vs 上季度）
        对比两者判断资金流向趋势和速度变化。

        Returns:
            {
                "prev_acc": DataFrame,  # Q-2→Q-1 增持信号
                "prev_red": DataFrame,  # Q-2→Q-1 减持信号
                "curr_acc": DataFrame,  # Q-1→Q0 增持信号
                "curr_red": DataFrame,  # Q-1→Q0 减持信号
                "prev_to": str,         # Q-1 period_end (如 "2025-12-31")
                "curr_to": str,         # Q0 period_end (如 "2026-03-31")
            }
        """
        all_holdings = self.load_all_holdings()
        if all_holdings.empty:
            return {}

        periods = sorted(all_holdings["period_end"].unique(), reverse=True)
        if len(periods) < 3:
            return {}

        # Q0 = 最新, Q-1 = 上季度, Q-2 = 上上季度
        q0, q_1, q_2 = periods[0], periods[1], periods[2]

        def _compute_single_quarter_signals(from_period, to_period):
            """计算两个季度之间的单季度增减持信号"""
            df = all_holdings[
                all_holdings["period_end"].isin([from_period, to_period])
            ].copy()

            totals = (
                df.groupby(["cik", "period_end"])["value"]
                .sum()
                .reset_index()
                .rename(columns={"value": "total_value"})
            )
            df = df.merge(totals, on=["cik", "period_end"])
            df["weight"] = df["value"] / df["total_value"].replace(0, float("nan"))

            acc_results = []
            red_results = []

            for (cik, cusip), group in df.groupby(["cik", "cusip"]):
                group = group.sort_values("period_end")
                if len(group) < 2:
                    continue
                prev = group.iloc[0]
                curr = group.iloc[1]
                if prev["period_end"] != from_period or curr["period_end"] != to_period:
                    continue

                shares_delta = curr["shares"] - prev["shares"]
                value_delta = curr["value"] - prev["value"]
                weight_delta = curr["weight"] - prev["weight"]

                entry = {
                    "cusip": cusip,
                    "name_of_issuer": curr["name_of_issuer"],
                    "cik": cik,
                    "manager_name": curr["manager_name"],
                    "shares_change": int(shares_delta),
                    "value_change": int(value_delta),
                    "weight_change": float(weight_delta),
                }

                if shares_delta > 0 and weight_delta > 0:
                    acc_results.append(entry)
                elif shares_delta < 0 and weight_delta < 0:
                    red_results.append(entry)

            def _aggregate(results, manager_col):
                if not results:
                    return pd.DataFrame()
                rdf = pd.DataFrame(results)
                return (
                    rdf.groupby(["cusip", "name_of_issuer"])
                    .agg(
                        **{manager_col: ("manager_name", lambda x: ", ".join(x))},
                        manager_count=("cik", "nunique"),
                        total_shares_change=("shares_change", "sum"),
                        total_value_change=("value_change", "sum"),
                        avg_weight_change=("weight_change", "mean"),
                    )
                    .reset_index()
                    .sort_values("manager_count", ascending=False)
                )

            return (_aggregate(acc_results, "accumulating_managers"),
                    _aggregate(red_results, "reducing_managers"))

        # Q-2→Q-1（上季度变动）
        prev_acc, prev_red = _compute_single_quarter_signals(q_2, q_1)
        # Q-1→Q0（本季度变动）
        curr_acc, curr_red = _compute_single_quarter_signals(q_1, q0)

        return {
            "prev_acc": prev_acc,
            "prev_red": prev_red,
            "curr_acc": curr_acc,
            "curr_red": curr_red,
            "prev_to": q_1,
            "curr_to": q0,
        }

    # ── 机构对比 ──────────────────────────────────────────────

    def compare_holdings_across_managers(self, cusip: str) -> pd.DataFrame:
        """跨机构对比某资产的持仓变化"""
        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql_query(
                "SELECT manager_name, cik, period_end, shares, value "
                "FROM thirteen_f_holdings WHERE cusip = ? "
                "ORDER BY period_end, manager_name",
                conn,
                params=(cusip,),
            )
        if df.empty:
            return df

        # 计算每家机构每季度的总组合价值和权重
        totals = (
            df.groupby(["cik", "period_end"])["value"]
            .sum()
            .reset_index()
            .rename(columns={"value": "total_value"})
        )
        df = df.merge(totals, on=["cik", "period_end"])
        df["weight"] = (df["value"] / df["total_value"].replace(0, float("nan")) * 100).round(2)
        return df

    def search_holdings_by_issuer(self, keyword: str) -> pd.DataFrame:
        """按 issuer 名搜索持仓"""
        with sqlite3.connect(self.db_path) as conn:
            return pd.read_sql_query(
                "SELECT DISTINCT cusip, name_of_issuer, "
                "COUNT(DISTINCT cik) as manager_count, "
                "MAX(period_end) as latest_period "
                "FROM thirteen_f_holdings "
                "WHERE name_of_issuer LIKE ? "
                "GROUP BY cusip, name_of_issuer "
                "ORDER BY manager_count DESC",
                conn,
                params=(f"%{keyword}%",),
            )

    def get_manager_stats(self) -> Dict:
        """返回统计概览"""
        with sqlite3.connect(self.db_path) as conn:
            managers = conn.execute(
                "SELECT COUNT(*) FROM thirteen_f_managers"
            ).fetchone()[0]
            holdings = conn.execute(
                "SELECT COUNT(*) FROM thirteen_f_holdings"
            ).fetchone()[0]
            filings = conn.execute(
                "SELECT COUNT(DISTINCT filing_id) FROM thirteen_f_holdings"
            ).fetchone()[0]
            date_range = conn.execute(
                "SELECT MIN(period_end), MAX(period_end) FROM thirteen_f_holdings"
            ).fetchone()
        return {
            "managers": managers,
            "holdings": holdings,
            "filings": filings,
            "earliest_period": date_range[0] if date_range else None,
            "latest_period": date_range[1] if date_range else None,
        }
