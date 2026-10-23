#!/usr/bin/env python3
"""月初飞书月报 — 成长动量·个股

结构：结论 → 本月怎么调（含原因）→ 何时执行 → 近 6 个月持仓 → 历史对照（vs 满仓 QQQ/SPY）→ 策略逻辑 → 风险

用法:
    python3 scripts/monthly_report.py            # 生成并推送飞书
    python3 scripts/monthly_report.py --dry-run  # 只打印，不推送
    python3 scripts/monthly_report.py --force    # 忽略本月已推送标记

月初由 daily_sync 自动触发（每自然月第一次运行）。
报告口径：简单易懂，只给该买/该卖/仓位占比/为什么/何时动手，不讲策略黑话。
合规：金融从业人员，境内只推荐 ETF/场外基金，不出现 A 股个股。
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).parent))

from core.data.config import CASH_PROXY
from core.data.notifier import FeishuNotifier
from export_target_weights import build_monthly_allocation, write_target_weights

MARKER_PATH = PROJECT_ROOT / "data" / ".last_monthly_report"

# 黄金单独一桶（对冲属性，不并进权益也不并进债/油）
GOLD_SYMBOL = "GLD"
# 债/油不是权益，也不是现金
DEFENSIVE_SYMBOLS = {"TLT", "CL"}

BRAKE_LABEL = {
    "none": "正常配置",
    "halve": "半仓防守",
    "flatten": "空仓防守",
}

BRAKE_HINT = {
    "none": "按下面比例配置即可",
    "halve": "回撤触发减半，**不要抄底加仓**",
    "flatten": "回撤触发清仓，**全部进现金/货基**",
}

# 策略逻辑说明（固定文案，口径要简单）
STRATEGY_LOGIC_LINES = [
    "- **选什么**：每月从 55 只美股大盘股（科技/消费/医药/金融/能源）里挑最强的 8 只",
    "- **能不能买**：过去 3 个月涨幅 **为正** 才买。跌的不碰",
    "- **买多少**：8 只按**涨幅加权**（涨得越多买越多），单票最多 20%",
    "- **什么时候换**：每月看一次。跌出前 8 名或 3 个月涨幅转负就换掉",
    "- **什么时候躲**：**不自动躲**。回撤预算 **−40%**（历史最大约 −32%），深回撤人工决策",
    "- **为什么是个股**：个股横截面动量比 ETF 轮动弹性更大，目标 3 年 1 倍量级；代价是回撤深、要扛得住",
]


def yearly_dd_map(equity_curve) -> dict:
    """从模型净值按自然年切最大回撤（收益对照走 bench_yearly）。"""
    if equity_curve is None or len(equity_curve) < 2:
        return {}
    eq = equity_curve.copy()
    eq.index = pd.to_datetime(eq.index)
    out = {}
    for year in sorted(set(eq.index.year)):
        seg = eq[eq.index.year == year]
        if len(seg) < 2:
            continue
        peak = seg.cummax()
        out[year] = float((seg / peak - 1.0).min())
    return out


def fmt_pct(x) -> str:
    return "—" if x is None else f"{x * 100:+.1f}%"


def fmt_pp(x) -> str:
    return "—" if x is None else f"{x * 100:+.1f}pp"


def md_table(headers: list, rows: list) -> str:
    """飞书 markdown 表格。rows 为 str 列表的列表。"""
    head = "| " + " | ".join(headers) + " |"
    sep = "| " + " | ".join("---" for _ in headers) + " |"
    body = ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join([head, sep] + body)


def hbar(value: float, max_abs: float, width: int = 8, positive: bool = True) -> str:
    """Unicode 横条。value/max_abs 决定格数，负向画在左侧用 ░，正向用 █。"""
    if max_abs <= 0 or value is None:
        return ""
    ratio = max(-1.0, min(1.0, value / max_abs))
    n = int(round(abs(ratio) * width))
    if n == 0:
        return "·"
    if value >= 0:
        return "█" * n if positive else "░" * n
    return "░" * n


def wbar(weight: float, width: int = 8, cap: float = 0.20) -> str:
    """仓位条：以单票顶 20% 为满格。"""
    return hbar(weight, cap, width=width, positive=True)


def change_reason(action: str, symbol: str, mom_map: dict) -> str:
    """用 3M 动量解释为什么进/出/调。"""
    mom = mom_map.get(symbol)
    if mom is None:
        return ""
    m = f"{mom * 100:+.0f}%"
    if action == "新进":
        return f"3 个月涨幅 {m}，冲进前 8"
    if action == "清仓":
        return f"3 个月涨幅 {m}，转负不买" if mom <= 0 else f"3 个月涨幅 {m}，跌出前 8"
    if action == "加仓":
        return f"3 个月涨幅 {m}，动量权重上调"
    return f"3 个月涨幅 {m}，动量权重下调"


def split_buckets(weights: dict) -> dict:
    """权益 / 黄金 / 债油 / 现金 四桶。现金=SHY，黄金=GLD。"""
    equity = 0.0
    gold = 0.0
    defensive = 0.0
    cash = 0.0
    for s, w in weights.items():
        if s == CASH_PROXY:
            cash += w
        elif s == GOLD_SYMBOL:
            gold += w
        elif s in DEFENSIVE_SYMBOLS:
            defensive += w
        else:
            equity += w
    return {"equity": equity, "gold": gold, "defensive": defensive, "cash": cash}


def display_symbol(symbol: str, name_map: dict) -> str:
    """标的显示名：日韩在老虎用区域 ETF，标出来避免买错。"""
    name = name_map.get(symbol, symbol)
    proxy = {"日经225": "EWJ", "KOSPI": "EWY"}.get(symbol)
    if proxy:
        return f"{name}（老虎 {proxy}）"
    if name == symbol:
        return symbol
    return f"{symbol} {name}"


def yearly_bench_lines(bench_yearly: list, bench_total: dict, equity_curve) -> list:
    """分年对照表：策略 vs 满仓 QQQ / 满仓 SPY，并给跑赢次数。"""
    dd_map = yearly_dd_map(equity_curve)
    beat_qqq = beat_spy = n_cmp = 0
    rows = []
    for r in bench_yearly or []:
        y = r["year"]
        dd = dd_map.get(y)
        s = r.get("strategy")
        q = r.get("qqq")
        p = r.get("spy")
        ex_q = r.get("ex_qqq")
        ex_p = r.get("ex_spy")
        if ex_q is not None:
            n_cmp += 1
            if ex_q > 0:
                beat_qqq += 1
        if ex_p is not None:
            if ex_p > 0:
                beat_spy += 1
        rows.append([
            str(y),
            f"**{fmt_pct(s)}**",
            fmt_pct(q),
            fmt_pct(p),
            f"**{fmt_pp(ex_q)}**" if ex_q is not None else "—",
            fmt_pp(ex_p),
            f"{dd * 100:.1f}%" if dd is not None else "—",
        ])

    lines = []
    if rows:
        lines.append(
            md_table(
                ["年份", "策略", "满仓QQQ", "满仓SPY", "vs QQQ", "vs SPY", "策略回撤"],
                rows,
            )
        )

    # 全期汇总（起点=策略净值首日，与基准对齐）
    eq = equity_curve
    if eq is not None and len(eq) > 1:
        eq2 = eq.copy()
        eq2.index = pd.to_datetime(eq2.index)
        years = (eq2.index[-1] - eq2.index[0]).days / 365.25
        total = float(eq2.iloc[-1] / eq2.iloc[0] - 1.0)
        cagr = (1.0 + total) ** (1 / years) - 1 if years > 0 else float("nan")
        max_dd = float((eq2 / eq2.cummax() - 1.0).min())
        q = (bench_total or {}).get("QQQ") or {}
        p = (bench_total or {}).get("SPY") or {}
        start_label = eq2.index[0].strftime("%Y-%m")
        total_rows = [
            [
                f"**策略**",
                f"**{total * 100:+.1f}%**",
                f"**{cagr * 100:.1f}%**",
                f"**{max_dd * 100:.1f}%**",
            ]
        ]
        if q:
            total_rows.append([
                "满仓QQQ",
                fmt_pct(q.get("total")),
                f"{q.get('cagr', float('nan')) * 100:.1f}%",
                f"{q.get('max_dd', 0) * 100:.1f}%",
            ])
        if p:
            total_rows.append([
                "满仓SPY",
                fmt_pct(p.get("total")),
                f"{p.get('cagr', float('nan')) * 100:.1f}%",
                f"{p.get('max_dd', 0) * 100:.1f}%",
            ])
        lines.append("")
        lines.append(f"**全期 {start_label} 至今**")
        lines.append(md_table(["标的", "累计", "年化", "最大回撤"], total_rows))

    if n_cmp:
        lines.append("")
        lines.append(
            f"**跑赢次数**：vs 满仓QQQ **{beat_qqq}/{n_cmp} 年**，"
            f"vs 满仓SPY **{beat_spy}/{n_cmp} 年**"
        )
        lines.append(
            "口径：基准=买入持有不折腾；策略含佣金 0.1%。超额是结果不是承诺"
        )
    if not lines:
        lines = ["（净值历史不足，暂无对照）"]
    return lines


def history_rows(weight_history: pd.DataFrame, current: dict, current_date, months: int = 12):
    """近 N 个月持仓快照：历史再平衡行 + 本月估算。"""
    rows = []
    if weight_history is not None and not weight_history.empty:
        hist = weight_history.copy()
        hist["date"] = pd.to_datetime(hist["date"])
        # 排除与本月同日的行，避免重复
        hist = hist[hist["date"].dt.strftime("%Y-%m") != pd.Timestamp(current_date).strftime("%Y-%m")]
        for _, row in hist.tail(months - 1).iterrows():
            w = {
                c: float(row[c])
                for c in hist.columns
                if c != "date" and pd.notna(row[c]) and float(row[c]) > 1e-4
            }
            rows.append({
                "label": pd.Timestamp(row["date"]).strftime("%y-%m"),
                "weights": w,
            })
    rows.append({
        "label": pd.Timestamp(current_date).strftime("%y-%m") + "（本月）",
        "weights": current,
    })
    return rows[-months:]


def prev_month_weights(weight_history: pd.DataFrame, current_date) -> dict:
    """上月再平衡权重（用于对比变动）。"""
    if weight_history is None or weight_history.empty:
        return {}
    hist = weight_history.copy()
    hist["date"] = pd.to_datetime(hist["date"])
    cur_m = pd.Timestamp(current_date).strftime("%Y-%m")
    hist = hist[hist["date"].dt.strftime("%Y-%m") != cur_m]
    if hist.empty:
        return {}
    row = hist.iloc[-1]
    return {
        c: float(row[c])
        for c in hist.columns
        if c != "date" and pd.notna(row[c]) and float(row[c]) > 1e-4
    }


def diff_holdings(prev: dict, curr: dict) -> list:
    """生成 买/卖 变动列表，按变动幅度排序。"""
    symbols = set(prev) | set(curr)
    diffs = []
    for s in symbols:
        if s in (CASH_PROXY, GOLD_SYMBOL):
            continue
        p = prev.get(s, 0.0)
        c = curr.get(s, 0.0)
        delta = c - p
        if abs(delta) < 0.005:
            continue
        if p <= 1e-4 and c > 1e-4:
            action = "新进"
        elif c <= 1e-4 and p > 1e-4:
            action = "清仓"
        elif delta > 0:
            action = "加仓"
        else:
            action = "减仓"
        diffs.append({
            "symbol": s,
            "action": action,
            "delta": delta,
            "curr": c,
            "prev": p,
        })
    diffs.sort(key=lambda x: -abs(x["delta"]))
    return diffs


def build_report(alloc: dict) -> dict:
    """构建飞书月报内容（纯文本结构，便于 dry-run 检查）。

    结构：结论 → 怎么调（带原因）→ 何时执行 → 近月持仓 → 历史对照 → 策略逻辑 → 风险
    """
    weights = alloc["weights"]
    brake = alloc["brake_action"]
    current_dd = alloc["current_dd"]
    as_of = alloc["as_of"]
    name_map = alloc["name_map"]
    mom_map = alloc.get("mom_map") or {}
    buckets = split_buckets(weights)

    prev_w = prev_month_weights(alloc["weight_history"], as_of)
    diffs = diff_holdings(prev_w, weights)
    hist = history_rows(alloc["weight_history"], weights, as_of, months=6)

    # 一句话结论 — 标题/结论都钉死「成长动量·个股」
    strategy = alloc["strategy"]
    strategy_name = strategy.name
    if strategy_name != "成长动量·个股":
        raise RuntimeError(
            f"生产策略不是成长动量·个股（当前 {strategy_name}），拒绝出月报"
        )
    conclusion = (
        f"生产策略 **成长动量·个股**（3 个月动量 top8 涨幅加权）　"
        f"**{BRAKE_LABEL.get(brake, brake)}**　"
        f"模型回撤 {current_dd * 100:.1f}%　回撤预算 −40%"
    )
    hint = BRAKE_HINT.get(brake, "")

    # 仓位：权益 / 黄金 / 现金 必展示；债/油有才展示
    bucket_rows = [
        ["权益", f"**{buckets['equity']:.0%}**", wbar(buckets["equity"], cap=1.0)],
        ["现金（SHY 1-3年美债ETF）", f"{buckets['cash']:.0%}", wbar(buckets["cash"], cap=1.0)],
    ]
    if buckets["gold"] > 0.005:
        bucket_rows.append(["黄金（GLD）", f"{buckets['gold']:.0%}", wbar(buckets["gold"], cap=1.0)])
    if buckets["defensive"] > 0.005:
        bucket_rows.append(["债/油", f"{buckets['defensive']:.0%}", wbar(buckets["defensive"], cap=1.0)])
    pos_block = md_table(["仓位桶", "占比", "图示"], bucket_rows)

    # 本月完整持仓表（权重 + 条形）
    hold_items = sorted(
        ((s, wt) for s, wt in weights.items() if wt > 1e-4),
        key=lambda x: -x[1],
    )
    hold_rows = []
    for s, wt in hold_items:
        hold_rows.append([display_symbol(s, name_map), f"{wt:.0%}", wbar(wt)])
    pos_block += "\n\n本月完整持仓：\n" + md_table(["标的", "权重", "图示"], hold_rows)

    # 黄金单独一条建议（有无仓位都要说清）
    gold_w = weights.get(GOLD_SYMBOL, 0.0)
    gold_prev = prev_w.get(GOLD_SYMBOL, 0.0)
    if gold_w > 1e-4 and gold_prev <= 1e-4:
        gold_line = f"**新进** GLD（黄金ETF）→ {gold_w:.0%}（分散风险，跟权益对冲）"
    elif gold_w > 1e-4 and gold_prev > 1e-4:
        if abs(gold_w - gold_prev) >= 0.005:
            direction = "加仓" if gold_w > gold_prev else "减仓"
            gold_line = (
                f"**{direction}** GLD（黄金ETF）→ {gold_w:.0%}"
                f"（{gold_prev:.0%} → {gold_w:.0%}）"
            )
        else:
            gold_line = f"**持有** GLD（黄金ETF）{gold_w:.0%}（与上月持平，不动）"
    elif gold_prev > 1e-4:
        gold_line = f"**清仓** GLD（黄金ETF）（上月 {gold_prev:.0%} → 0%）"
    else:
        gold_line = "不配黄金（本策略为美股个股，不含 GLD（黄金ETF））"

    # 本月调仓表：动作 + 上月/本月 + 原因（3M 动量）
    if not diffs:
        change_block = "本月持仓与上月基本一致，不用动手。\n\n" + gold_line
    else:
        change_rows = []
        for d in diffs[:12]:
            sign = "+" if d["delta"] > 0 else "−"
            reason = change_reason(d["action"], d["symbol"], mom_map) or "—"
            change_rows.append([
                f"**{d['action']}**",
                display_symbol(d["symbol"], name_map),
                f"{d['prev']:.0%}",
                f"{d['curr']:.0%}",
                f"{sign}{abs(d['delta']):.0%}",
                reason,
            ])
        change_block = (
            f"共 **{len(diffs)}** 项变动，按幅度排序：\n"
            + md_table(
                ["动作", "标的", "上月", "本月", "变动", "原因（3M 动量）"],
                change_rows,
            )
        )
        if len(diffs) > 12:
            change_block += f"\n…另有 {len(diffs) - 12} 项小变动"
        change_block += (
            "\n\n原因口径：只看过去 3 个月涨幅。涨得越多权重越高；转负或跌出前 8 就换。\n"
            + gold_line
        )

    # 何时执行
    tiger = alloc["tiger_w"]
    domestic = alloc["domestic_w"]
    exec_lines = [
        "- **时点**：今晚美股开盘后一次调完（北京时间约 21:30，夏令时 21:30 / 冬令时 22:30）",
        "- **顺序**：先卖后买——先减/清的票卖出回笼，再按目标权重买进",
        "- **账户**：本策略持仓全为美股个股 → **老虎账户**；境内账户通常空仓（保持货基）",
    ]
    if domestic:
        names = "、".join(
            f"{display_symbol(s, name_map)} {w:.0%}"
            for s, w in sorted(domestic.items(), key=lambda x: -x[1])
        )
        exec_lines.append(f"- 境内（次日开盘前）：{names}")
    else:
        exec_lines.append("- 境内（次日开盘前）：无目标仓位，**保持货基/现金**")
    if tiger:
        risk = {s: w for s, w in tiger.items() if s != CASH_PROXY}
        if risk:
            names = "、".join(
                f"{s} {w:.0%}" for s, w in sorted(risk.items(), key=lambda x: -x[1])
            )
            exec_lines.append(f"- 老虎买入：{names}")
        if tiger.get(CASH_PROXY, 0) > 0.005:
            exec_lines.append(f"- 老虎闲钱：SHY（1-3年美债ETF）{tiger[CASH_PROXY]:.0%}")
    else:
        exec_lines.append("- 老虎：无风险仓位，闲钱进 SHY（1-3年美债ETF）")
    if gold_w > 1e-4:
        exec_lines.append(f"- 黄金：老虎买 **GLD（黄金ETF）{gold_w:.0%}**（或境内黄金 ETF 同步）")
    else:
        exec_lines.append("- 黄金：本月 **0%**，不买 GLD（黄金ETF）")
    exec_lines += [
        "- **不要拖到月中**：权重是月初信号，拖久了动量已变，等于用旧指令交易",
        "- 单票 ≤20%；成交记到 data/live_trades.csv",
    ]

    # 近月持仓矩阵：行=标的，列=月份，一眼看出轮动
    hist_block = ""
    if hist:
        labels = [row["label"] for row in hist]
        # 只留出现过的标的，按近月权重降序
        symbols = []
        seen = set()
        for row in reversed(hist):
            for s, wt in sorted(row["weights"].items(), key=lambda x: -x[1]):
                if wt > 1e-4 and s not in seen:
                    seen.add(s)
                    symbols.append(s)
        matrix_rows = []
        for s in symbols:
            cells = []
            for row in hist:
                wt = row["weights"].get(s, 0.0)
                cells.append(f"{wt:.0%}" if wt > 1e-4 else "·")
            matrix_rows.append([s] + cells)
        # 末行加权益/现金合计
        eq_cells, cash_cells = [], []
        for row in hist:
            b = split_buckets(row["weights"])
            eq_cells.append(f"{b['equity']:.0%}")
            cash_cells.append(f"{b['cash']:.0%}")
        matrix_rows.append(["**权益**"] + [f"**{c}**" for c in eq_cells])
        matrix_rows.append(["**现金**"] + [f"**{c}**" for c in cash_cells])
        hist_block = (
            md_table(["标的"] + labels, matrix_rows)
            + "\n\n口径：每月首个交易日收盘后的目标权重。「·」=当月不持有；未配满进 SHY。"
        )
    else:
        hist_block = "（暂无历史权重）"

    # 历史对照：分年 vs 满仓 QQQ / SPY
    bench_block = "\n".join(
        yearly_bench_lines(
            alloc.get("bench_yearly"),
            alloc.get("bench_total"),
            alloc.get("equity_curve"),
        )
    )

    # 风险（客观，不粉饰）——回撤进度条 + 要点
    dd_gauge = wbar(abs(current_dd), cap=0.40, width=10)
    risk_block = (
        f"回撤进度：{dd_gauge}  **{current_dd * 100:.1f}%** / 预算 −40%\n\n"
        "- 贴近 −40% 时人工评估，**无自动减仓**\n"
        "- 历史最大回撤约 **−32%**（引擎实测 2019–2025）。个股集中 top8，跌起来比指数狠，要扛得住\n"
        "- 股票池是 **2019 年静态快照**，新一代赢家不在池内时收益会滑向指数；按触发条件人工评审换股，不自动换血\n"
        "- 3 年滚动做到 ≥26% 的窗口约 **48%**——「经常够」不是「稳稳 3 年 1 倍」\n"
        "- 历史回测 ≠ 未来。超额来自池内恰好有 NVDA/礼来 级赢家，不是机制永续"
    )

    return {
        "title": (
            f"  成长动量·个股 月报 · {as_of.strftime('%Y-%m')} "
            f"｜{BRAKE_LABEL.get(brake, brake)}"
        ),
        "conclusion": conclusion,
        "hint": hint,
        "pos_block": pos_block,
        "change_block": change_block,
        "exec_lines": exec_lines,
        "hist_block": hist_block,
        "bench_block": bench_block,
        "strategy_lines": STRATEGY_LOGIC_LINES,
        "risk_block": risk_block,
        "severity": "warning" if brake != "none" else "info",
    }


def to_feishu_elements(report: dict) -> list:
    """拼飞书卡片 elements。不用 collapsible（飞书不支持）；表格/条形用 markdown。"""
    parts = [report["conclusion"]]
    if report["hint"]:
        parts.append(report["hint"])
    return [
        {"tag": "markdown", "content": "\n".join(parts)},
        {"tag": "hr"},
        {"tag": "markdown", "content": f"**本月持仓与仓位**\n{report['pos_block']}"},
        {"tag": "hr"},
        {"tag": "markdown", "content": f"**本月怎么调（含原因）**\n{report['change_block']}"},
        {"tag": "hr"},
        {"tag": "markdown", "content": "**何时执行**\n" + "\n".join(report["exec_lines"])},
        {"tag": "hr"},
        {"tag": "markdown", "content": f"**近 6 个月持仓（轮动矩阵）**\n{report['hist_block']}"},
        {"tag": "hr"},
        {"tag": "markdown", "content": f"**历史对照（vs 满仓 QQQ / SPY）**\n{report['bench_block']}"},
        {"tag": "hr"},
        {"tag": "markdown", "content": "**策略逻辑（成长动量·个股）**\n" + "\n".join(report["strategy_lines"])},
        {"tag": "hr"},
        {"tag": "markdown", "content": f"**风险（客观）**\n{report['risk_block']}"},
    ]


def format_plain(report: dict) -> str:
    lines = [
        report["title"],
        "",
        report["conclusion"],
    ]
    if report["hint"]:
        lines.append(report["hint"])
    lines += [
        "",
        "【本月持仓与仓位】",
        report["pos_block"],
        "",
        "【本月怎么调（含原因）】",
        report["change_block"],
        "",
        "【何时执行】",
        *report["exec_lines"],
        "",
        "【近 6 个月持仓（轮动矩阵）】",
        report["hist_block"],
        "",
        "【历史对照（vs 满仓 QQQ / SPY）】",
        report["bench_block"],
        "",
        "【策略逻辑（成长动量·个股）】",
        *report["strategy_lines"],
        "",
        "【风险（客观）】",
        report["risk_block"],
    ]
    return "\n".join(lines)


def already_sent_this_month() -> bool:
    if not MARKER_PATH.exists():
        return False
    return MARKER_PATH.read_text(encoding="utf-8").strip() == datetime.now().strftime("%Y-%m")


def mark_sent() -> None:
    MARKER_PATH.parent.mkdir(parents=True, exist_ok=True)
    MARKER_PATH.write_text(datetime.now().strftime("%Y-%m"), encoding="utf-8")


def send_monthly_report(dry_run: bool = False, force: bool = False) -> bool:
    if not force and already_sent_this_month():
        print(f"本月已推送过（{MARKER_PATH}），跳过。用 --force 重发")
        return False

    print("计算本月权重（回放历史，约需 1 分钟）...")
    alloc = build_monthly_allocation()
    out_dir = write_target_weights(alloc)
    report = build_report(alloc)
    text = format_plain(report)
    print(text)
    print(f"\n调仓单已写入: {out_dir}")

    if dry_run:
        print("\n[dry-run] 不推送飞书")
        return True

    notifier = FeishuNotifier()
    if not notifier.is_configured():
        print("FEISHU_WEBHOOK_URL 未配置，只打印不推送")
        return False

    ok = notifier.send_card(
        title=report["title"],
        elements=to_feishu_elements(report),
        severity=report["severity"],
    )
    if ok:
        mark_sent()
        print("飞书月报已推送")
    else:
        print("飞书推送失败")
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description="月初飞书月报")
    parser.add_argument("--dry-run", action="store_true", help="只打印不推送")
    parser.add_argument("--force", action="store_true", help="忽略本月已推送标记")
    args = parser.parse_args()
    send_monthly_report(dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":
    main()
