"""告警评估器 — 汇总所有风险源，生成告警列表"""

import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List

import pandas as pd

from core.data.event_calendar import get_current_risk_level
from core.data.config import US_SECTORS_SYMBOLS, WATCHED_HOLDINGS

logger = logging.getLogger(__name__)

# 持仓主题分组 — 同主题基金合并为一条告警
FUND_THEMES = {
    "光模块/AI半导体": ["011369", "000979", "012922", "457001"],
}
# 反向映射: symbol → theme
_SYMBOL_TO_THEME = {}
for _theme, _symbols in FUND_THEMES.items():
    for _s in _symbols:
        _SYMBOL_TO_THEME[_s] = _theme


class AlertEvaluator:
    """告警评估器 — 汇总宏观事件、VIX、板块异常、新闻信号"""

    def evaluate(self, dm) -> List[Dict]:
        """评估当前所有风险源，返回告警列表

        Args:
            dm: DataManager 实例

        Returns:
            告警字典列表
        """
        alerts = []
        today = datetime.now().strftime("%Y-%m-%d")

        # 1. 宏观事件窗口
        try:
            alerts += self._check_macro_events(today)
        except Exception as e:
            logger.warning(f"宏观事件检查失败: {e}")

        # 2. VIX 水位
        try:
            alerts += self._check_vix(dm, today)
        except Exception as e:
            logger.warning(f"VIX 检查失败: {e}")

        # 3. 板块异常
        try:
            alerts += self._check_sector_alerts(dm, today)
        except Exception as e:
            logger.warning(f"板块异常检查失败: {e}")

        # 4. 新闻信号
        try:
            alerts += self._check_news_signals()
        except Exception as e:
            logger.warning(f"新闻信号检查失败: {e}")

        return alerts

    def _check_macro_events(self, today: str) -> List[Dict]:
        """检查宏观事件风险窗口"""
        alerts = []
        level, reasons = get_current_risk_level()

        if level == "high":
            alerts.append({
                "date": today,
                "alert_type": "macro_event",
                "severity": "warning",
                "title": "  宏观事件高风险窗口",
                "message": "未来 3 天内有重要宏观事件：\n" + "\n".join(reasons[:5]),
                "related_assets": json.dumps(["SPY", "QQQ", "TLT"]),
            })
        elif level == "medium":
            alerts.append({
                "date": today,
                "alert_type": "macro_event",
                "severity": "info",
                "title": "  宏观事件中风险窗口",
                "message": "未来 7 天内有宏观事件：\n" + "\n".join(reasons[:3]),
                "related_assets": json.dumps(["SPY"]),
            })

        return alerts

    def _check_vix(self, dm, today: str) -> List[Dict]:
        """检查 VIX 恐慌指数"""
        alerts = []
        try:
            from core.data.macro_storage import MacroStorage
            storage = MacroStorage()
            vix_df = storage.load("vix")

            if vix_df.empty:
                return alerts

            latest = vix_df.iloc[-1]
            vix_value = latest["value"]

            # VIX > 25: 恐慌
            if vix_value > 25:
                alerts.append({
                    "date": today,
                    "alert_type": "vix",
                    "severity": "critical",
                    "title": "  VIX 恐慌指数飙升",
                    "message": f"VIX 当前 {vix_value:.1f}，处于恐慌区间（>25）。市场波动率显著放大，建议减仓或对冲。",
                    "related_assets": json.dumps(["SPY", "QQQ"]),
                })
            elif vix_value > 20:
                alerts.append({
                    "date": today,
                    "alert_type": "vix",
                    "severity": "warning",
                    "title": "  VIX 警戒线",
                    "message": f"VIX 当前 {vix_value:.1f}，处于警戒区间（20-25）。市场紧张情绪上升。",
                    "related_assets": json.dumps(["SPY"]),
                })

            # VIX 5 日涨幅 > 50%
            if len(vix_df) >= 5:
                vix_5d_ago = vix_df.iloc[-5]["value"]
                if vix_5d_ago > 0:
                    change = (vix_value - vix_5d_ago) / vix_5d_ago * 100
                    if change > 50:
                        alerts.append({
                            "date": today,
                            "alert_type": "vix",
                            "severity": "critical",
                            "title": "  VIX 急速飙升",
                            "message": f"VIX 5 日涨幅 {change:.0f}%（{vix_5d_ago:.1f} → {vix_value:.1f}），恐慌情绪快速蔓延。",
                            "related_assets": json.dumps(["SPY", "QQQ", "TLT"]),
                        })
        except Exception:
            pass

        return alerts

    def _check_sector_alerts(self, dm, today: str) -> List[Dict]:
        """检查板块异常（含 A 股基金，按主题去重）"""
        alerts = []

        try:
            from core.scanner.sector_alert import detect_sector_alerts

            end_date = today
            start_date = (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d")

            # 加载板块数据
            sector_data = {}
            for symbol in US_SECTORS_SYMBOLS:
                df = dm.load(symbol, start_date, end_date)
                if not df.empty:
                    sector_data[symbol] = df

            # 加载关注基金数据
            for code, info in WATCHED_HOLDINGS.items():
                if info.get("type") == "fund":
                    df = dm.load(code, start_date, end_date)
                    if not df.empty:
                        sector_data[code] = df

            # 加载 SPY 基准
            spy_df = dm.load("SPY", start_date, end_date)

            if not sector_data or spy_df.empty:
                return alerts

            # 运行原始检测
            raw_alerts = detect_sector_alerts(sector_data, spy_df)

            # 分离板块 ETF 和基金
            etf_alerts = [a for a in raw_alerts if a.sector in US_SECTORS_SYMBOLS]
            fund_alerts = [a for a in raw_alerts if a.sector not in US_SECTORS_SYMBOLS]

            # 板块 ETF：每个 symbol 独立告警，同 symbol 合并多条规则
            merged_by_symbol: Dict[str, List] = {}
            for a in etf_alerts:
                merged_by_symbol.setdefault(a.sector, []).append(a)
            for symbol, symbol_alerts in merged_by_symbol.items():
                alerts.append(self._merge_alerts(symbol_alerts, today))

            # 基金：按主题分组，同主题合并为一条
            theme_alerts: Dict[str, List] = {}
            for a in fund_alerts:
                theme = _SYMBOL_TO_THEME.get(a.sector, a.sector_name)
                theme_alerts.setdefault(theme, []).append(a)
            for theme, theme_list in theme_alerts.items():
                alerts.append(self._merge_theme_alerts(theme, theme_list, today))

        except Exception as e:
            logger.warning(f"板块异常检测失败: {e}")

        return alerts

    def _merge_alerts(self, alerts: list, today: str) -> Dict:
        """合并同一 symbol 的多条告警为一条"""
        if len(alerts) == 1:
            a = alerts[0]
            return {
                "date": today, "alert_type": "sector", "severity": a.severity,
                "title": f"  板块异常: {a.sector_name}",
                "message": a.message,
                "related_assets": json.dumps([a.sector]),
            }
        # 多条：取最严重的 severity，合并 message
        severity_order = {"critical": 0, "warning": 1}
        alerts.sort(key=lambda x: severity_order.get(x.severity, 2))
        worst = alerts[0]
        messages = [a.message for a in alerts]
        return {
            "date": today, "alert_type": "sector", "severity": worst.severity,
            "title": f"  板块异常: {worst.sector_name}",
            "message": " | ".join(messages),
            "related_assets": json.dumps([worst.sector]),
        }

    def _merge_theme_alerts(self, theme: str, alerts: list, today: str) -> Dict:
        """合并同主题多只基金的告警为一条"""
        severity_order = {"critical": 0, "warning": 1}
        alerts.sort(key=lambda x: severity_order.get(x.severity, 2))
        worst = alerts[0]

        # 收集所有相关 symbol
        symbols = list(set(a.sector for a in alerts))
        # 取代表标的（最差的那个）
        rep_name = worst.sector_name
        count = len(symbols)

        # 合并 message：取各类型最差的
        by_type: Dict[str, list] = {}
        for a in alerts:
            by_type.setdefault(a.alert_type, []).append(a)
        parts = []
        for atype, type_alerts in by_type.items():
            # 同类型取最差
            type_alerts.sort(key=lambda x: severity_order.get(x.severity, 2))
            parts.append(type_alerts[0].message)

        suffix = f"（{count} 只基金同主题）" if count > 1 else ""
        return {
            "date": today, "alert_type": "sector", "severity": worst.severity,
            "title": f"  板块异常: {theme}{suffix}",
            "message": " | ".join(parts),
            "related_assets": json.dumps(symbols),
        }

    def _check_news_signals(self) -> List[Dict]:
        """检查最近的行业新闻信号（含前兆信号）"""
        alerts = []
        try:
            from core.data.signal_storage import PoliticalSignalStorage
            storage = PoliticalSignalStorage()

            # 获取最近 3 天的信号（前兆信号值得更长窗口）
            cutoff = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
            all_signals = storage.load_all()

            if all_signals.empty:
                return alerts

            # 过滤最近的行业新闻
            recent_news = all_signals[
                (all_signals["date"] >= cutoff) &
                (all_signals["speaker"] == "行业新闻")
            ]

            if recent_news.empty:
                return alerts

            # 分离前兆信号和直接信号
            # 前兆信号：不要求 direction == bearish，只要有信号就推送
            precursor_mask = recent_news["impact_note"].str.contains(
                "\\[precursor\\]", regex=True, na=False
            )
            precursors = recent_news[precursor_mask]
            # 直接信号：要求 direction == bearish
            direct_bearish = recent_news[
                ~precursor_mask & (recent_news["signal_direction"] == "bearish")
            ]

            # 合并要推送的信号
            to_alert = pd.concat([precursors, direct_bearish]).drop_duplicates()

            if to_alert.empty:
                return alerts

            count = len(to_alert)
            precursor_count = len(precursors)
            headlines = to_alert.head(5)["content"].tolist()
            msg = f"最近 3 天有 {count} 条行业信号"
            if precursor_count > 0:
                msg += f"（其中 {precursor_count} 条为前兆信号）"
            msg += "：\n"
            msg += "\n".join(f"- {h[:80]}" for h in headlines)

            # 提取相关资产
            all_assets = set()
            for assets_str in to_alert["affected_assets"]:
                try:
                    all_assets.update(json.loads(assets_str))
                except Exception:
                    pass

            # 有前兆信号时提高 severity
            severity = "warning"
            if precursor_count >= 3:
                severity = "critical"
            elif count < 2:
                severity = "info"

            alerts.append({
                "date": datetime.now().strftime("%Y-%m-%d"),
                "alert_type": "news",
                "severity": severity,
                "title": "  AI/半导体叙事信号" if precursor_count > 0 else "  AI/半导体负面叙事",
                "message": msg,
                "related_assets": json.dumps(list(all_assets)[:5]),
            })
        except Exception:
            pass

        return alerts
