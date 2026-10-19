"""宏观事件日历 — 重要经济数据/会议/财报日期跟踪

用于风险窗口评估：事件前 N 天标记为高/中风险窗口。
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple


@dataclass
class MacroEvent:
    """单个宏观事件"""
    date: str           # YYYY-MM-DD
    name: str           # "FOMC 利率决议"
    category: str       # "fomc" / "nfp" / "cpi" / "ppi" / "triple_witch" / "earnings"
    risk_level: str     # "high" / "medium"
    description: str    # 说明


# ── FOMC 2025-2026 会议日期（美联储公布） ─────────────────────
FOMC_DATES = {
    2025: [
        ("2025-01-29", "2025-01-30"), ("2025-03-18", "2025-03-19"),
        ("2025-05-06", "2025-05-07"), ("2025-06-17", "2025-06-18"),
        ("2025-07-29", "2025-07-30"), ("2025-09-16", "2025-09-17"),
        ("2025-10-28", "2025-10-29"), ("2025-12-09", "2025-12-10"),
    ],
    2026: [
        ("2026-01-27", "2026-01-28"), ("2026-03-17", "2026-03-18"),
        ("2026-04-28", "2026-04-29"), ("2026-06-16", "2026-06-17"),
        ("2026-07-28", "2026-07-29"), ("2026-09-15", "2026-09-16"),
        ("2026-10-27", "2026-10-28"), ("2026-12-08", "2026-12-09"),
    ],
}

# ── 重要财报日期（硬编码 2026 年，季度） ─────────────────────
EARNINGS_DATES_2026 = [
    # Q1 财报 (4-5月)
    ("2026-04-22", "NVDA 英伟达 Q1 财报", "high"),
    ("2026-04-29", "MSFT 微软 Q1 财报", "high"),
    ("2026-04-29", "GOOGL 谷歌 Q1 财报", "high"),
    ("2026-04-30", "META Q1 财报", "high"),
    ("2026-05-01", "AMZN 亚马逊 Q1 财报", "high"),
    ("2026-04-16", "TSMC 台积电 Q1 财报", "high"),
    # Q2 财报 (7-8月)
    ("2026-07-22", "NVDA 英伟达 Q2 财报", "high"),
    ("2026-07-28", "MSFT 微软 Q2 财报", "high"),
    ("2026-07-28", "GOOGL 谷歌 Q2 财报", "high"),
    ("2026-07-29", "META Q2 财报", "high"),
    ("2026-07-30", "AMZN 亚马逊 Q2 财报", "high"),
    ("2026-07-16", "TSMC 台积电 Q2 财报", "high"),
    # Q3 财报 (10-11月)
    ("2026-10-21", "NVDA 英伟达 Q3 财报", "high"),
    ("2026-10-27", "MSFT 微软 Q3 财报", "high"),
    ("2026-10-27", "GOOGL 谷歌 Q3 财报", "high"),
    ("2026-10-28", "META Q3 财报", "high"),
    ("2026-10-29", "AMZN 亚马逊 Q3 财报", "high"),
    ("2026-10-15", "TSMC 台积电 Q3 财报", "high"),
]

# 风险窗口配置
RISK_WINDOW_HIGH = 3    # 事件前 3 天 = 高风险
RISK_WINDOW_MEDIUM = 7  # 事件前 7 天 = 中风险


def _first_friday(year: int, month: int) -> date:
    """计算某月第一个周五"""
    d = date(year, month, 1)
    # weekday(): 0=Mon, 4=Fri
    days_until_fri = (4 - d.weekday()) % 7
    return d + timedelta(days=days_until_fri)


def _third_friday(year: int, month: int) -> date:
    """计算某月第三个周五"""
    d = date(year, month, 1)
    days_until_fri = (4 - d.weekday()) % 7
    first_fri = d + timedelta(days=days_until_fri)
    return first_fri + timedelta(weeks=2)


def _generate_nfp_events(year: int) -> List[MacroEvent]:
    """生成非农就业数据日期（每月第一个周五）"""
    events = []
    for month in range(1, 13):
        d = _first_friday(year, month)
        events.append(MacroEvent(
            date=d.strftime("%Y-%m-%d"),
            name=f"非农就业数据 ({month}月)",
            category="nfp",
            risk_level="high",
            description="美国非农就业报告，通常引发市场大幅波动",
        ))
    return events


def _generate_cpi_events(year: int) -> List[MacroEvent]:
    """生成 CPI 发布日期（每月 10-13 号之间的工作日）"""
    events = []
    for month in range(1, 13):
        # CPI 通常在每月 10-13 号发布，取 12 号作为近似
        d = date(year, month, 12)
        # 如果是周末，移到下一个周一
        if d.weekday() == 5:
            d += timedelta(days=2)
        elif d.weekday() == 6:
            d += timedelta(days=1)
        events.append(MacroEvent(
            date=d.strftime("%Y-%m-%d"),
            name=f"CPI 消费者物价指数 ({month}月)",
            category="cpi",
            risk_level="high",
            description="通胀核心指标，影响美联储利率决策",
        ))
    return events


def _generate_ppi_events(year: int) -> List[MacroEvent]:
    """生成 PPI 发布日期（每月 13-15 号之间的工作日）"""
    events = []
    for month in range(1, 13):
        d = date(year, month, 14)
        if d.weekday() == 5:
            d += timedelta(days=2)
        elif d.weekday() == 6:
            d += timedelta(days=1)
        events.append(MacroEvent(
            date=d.strftime("%Y-%m-%d"),
            name=f"PPI 生产者物价指数 ({month}月)",
            category="ppi",
            risk_level="medium",
            description="上游通胀指标，CPI 的领先信号",
        ))
    return events


def _generate_triple_witch_events(year: int) -> List[MacroEvent]:
    """生成三巫日（3/6/9/12 月第三个周五）"""
    events = []
    for month in [3, 6, 9, 12]:
        d = _third_friday(year, month)
        events.append(MacroEvent(
            date=d.strftime("%Y-%m-%d"),
            name=f"三巫日 ({month}月)",
            category="triple_witch",
            risk_level="high",
            description="股指期货/期权/个股期权同时到期，通常引发成交量和波动率飙升",
        ))
    return events


def _generate_fomc_events() -> List[MacroEvent]:
    """生成 FOMC 会议日期"""
    events = []
    for year, dates in FOMC_DATES.items():
        for start, end in dates:
            # 利率决议在第二天公布
            events.append(MacroEvent(
                date=end,
                name="FOMC 利率决议",
                category="fomc",
                risk_level="high",
                description=f"美联储议息会议结果公布（{start}~{end}）",
            ))
    return events


def _generate_earnings_events() -> List[MacroEvent]:
    """生成重要财报日期"""
    events = []
    for d_str, name, risk in EARNINGS_DATES_2026:
        events.append(MacroEvent(
            date=d_str,
            name=name,
            category="earnings",
            risk_level=risk,
            description=f"AI/半导体核心公司财报",
        ))
    return events


def generate_all_events(years: Optional[List[int]] = None) -> List[MacroEvent]:
    """生成所有宏观事件"""
    if years is None:
        years = [2025, 2026]

    events = []
    for year in years:
        events.extend(_generate_nfp_events(year))
        events.extend(_generate_cpi_events(year))
        events.extend(_generate_ppi_events(year))
        events.extend(_generate_triple_witch_events(year))

    events.extend(_generate_fomc_events())
    events.extend(_generate_earnings_events())

    # 按日期排序
    events.sort(key=lambda e: e.date)
    return events


def get_upcoming_events(days: int = 14, today: Optional[date] = None) -> List[MacroEvent]:
    """获取未来 N 天内的事件（含风险窗口标记）"""
    if today is None:
        today = date.today()

    all_events = generate_all_events()
    end_date = today + timedelta(days=days)

    upcoming = []
    for event in all_events:
        event_date = date.fromisoformat(event.date)
        if today <= event_date <= end_date:
            upcoming.append(event)

    return upcoming


def get_current_risk_level(today: Optional[date] = None) -> Tuple[str, List[str]]:
    """获取当前日期的风险等级

    Returns:
        (risk_level, reasons): risk_level 为 "high" / "medium" / "low"
        reasons 为触发风险的事件描述列表
    """
    if today is None:
        today = date.today()

    all_events = generate_all_events()
    reasons = []
    max_risk = "low"

    for event in all_events:
        event_date = date.fromisoformat(event.date)
        days_until = (event_date - today).days

        if days_until < 0:
            continue  # 已过期

        if days_until <= RISK_WINDOW_HIGH:
            reasons.append(f"  {event.name}（{event.date}，{days_until}天后）")
            max_risk = "high"
        elif days_until <= RISK_WINDOW_MEDIUM:
            reasons.append(f"  {event.name}（{event.date}，{days_until}天后）")
            if max_risk != "high":
                max_risk = "medium"

    return max_risk, reasons


def get_risk_calendar(year: int, month: int) -> List[Dict]:
    """获取某月的风险日历（用于 P1 展示）

    Returns:
        每天一条记录: {"date": "YYYY-MM-DD", "risk": "high"/"medium"/"none", "events": [...]}
    """
    all_events = generate_all_events()

    # 构建当月每天的日历
    import calendar
    _, days_in_month = calendar.monthrange(year, month)

    calendar_data = []
    for day in range(1, days_in_month + 1):
        d = date(year, month, day)
        d_str = d.strftime("%Y-%m-%d")

        # 找到当天的事件
        day_events = [e for e in all_events if e.date == d_str]

        # 找到风险窗口覆盖当天的事件
        window_events = []
        for event in all_events:
            event_date = date.fromisoformat(event.date)
            days_until = (event_date - d).days
            if 0 < days_until <= RISK_WINDOW_HIGH:
                window_events.append(f"距{event.name}还有{days_until}天")
            elif RISK_WINDOW_HIGH < days_until <= RISK_WINDOW_MEDIUM:
                window_events.append(f"距{event.name}还有{days_until}天")

        # 确定风险等级
        if day_events:
            risk = max((e.risk_level for e in day_events), key=lambda r: 0 if r == "high" else 1)
        elif window_events:
            risk = "medium"
        else:
            risk = "none"

        calendar_data.append({
            "date": d_str,
            "day": day,
            "weekday": ["一", "二", "三", "四", "五", "六", "日"][d.weekday()],
            "risk": risk,
            "events": [e.name for e in day_events],
            "windows": window_events,
        })

    return calendar_data
