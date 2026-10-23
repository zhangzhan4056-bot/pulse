#!/usr/bin/env python3
"""月初飞书月报 — 成长动量·个股 持仓变动 + 策略逻辑 + 分年回测 + 权益/现金仓位

用法:
    python3 scripts/monthly_report.py            # 生成并推送飞书
    python3 scripts/monthly_report.py --dry-run  # 只打印，不推送
    python3 scripts/monthly_report.py --force    # 忽略本月已推送标记

月初由 daily_sync 自动触发（每自然月第一次运行）。
报告口径：简单易懂，只给该买/该卖/仓位占比，不讲策略黑话。
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
    "- **选什么**：每月从 55 只美股大盘股（科技/消费/医药/金融/能源）里挑最强的 12 只",
    "- **能不能买**：过去 3 个月涨幅 **为正** 才买。跌的不碰",
    "- **买多少**：12 只等权，每只约 **8%**，单票最多 20%",
    "- **什么时候换**：每月看一次。跌出前 12 名或 3 个月涨幅转负就换掉",
    "- **什么时候躲**：**不自动躲**。回撤预算 **−40%**（历史最大约 −35%），深回撤人工决策",
    "- **为什么是个股**：个股横截面动量比 ETF 轮动弹性更大，目标 3 年 1 倍量级；代价是回撤深、要扛得住",
]


def yearly_backtest_rows(equity_curve) -> list:
    """从模型净值按自然年切收益/最大回撤。佣金 0.1%，含回撤刹车。"""
    if equity_curve is None or len(equity_curve) < 2:
        return []
    eq = equity_curve.copy()
    eq.index = pd.to_datetime(eq.index)
    rows = []
    for year in sorted(set(eq.index.year)):
        seg = eq[eq.index.year == year]
        if len(seg) < 2:
            continue
        prev = eq[eq.index.year < year]
        start = float(prev.iloc[-1]) if len(prev) else 1.0
        ret = float(seg.iloc[-1] / start - 1.0)
        peak = seg.cummax()
        dd = float((seg / peak - 1.0).min())
        rows.append({
            "year": year,
            "return": ret,
            "max_dd": dd,
            "end_nav": float(seg.iloc[-1]),
        })
    return rows


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


def top_holdings(weights: dict, n: int = 3) -> str:
    items = [
        (s, w) for s, w in weights.items()
        if s != CASH_PROXY and w > 1e-4
    ]
    items.sort(key=lambda x: -x[1])
    if not items:
        return "现金"
    return " / ".join(f"{s}{w:.0%}" for s, w in items[:n])


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
    """构建飞书月报内容（纯文本结构，便于 dry-run 检查）。"""
    weights = alloc["weights"]
    brake = alloc["brake_action"]
    current_dd = alloc["current_dd"]
    as_of = alloc["as_of"]
    name_map = alloc["name_map"]
    buckets = split_buckets(weights)

    prev_w = prev_month_weights(alloc["weight_history"], as_of)
    diffs = diff_holdings(prev_w, weights)
    hist = history_rows(alloc["weight_history"], weights, as_of, months=12)

    # 一句话结论 — 标题/结论都钉死「成长动量·个股」
    strategy = alloc["strategy"]
    strategy_name = strategy.name
    if strategy_name != "成长动量·个股":
        raise RuntimeError(
            f"生产策略不是成长动量·个股（当前 {strategy_name}），拒绝出月报"
        )
    conclusion = (
        f"生产策略 **成长动量·个股**（3 个月动量 top12 满仓）　"
        f"**{BRAKE_LABEL.get(brake, brake)}**　"
        f"模型回撤 {current_dd * 100:.1f}%　回撤预算 −40%"
    )
    hint = BRAKE_HINT.get(brake, "")

    # 分年回测
    yearly = yearly_backtest_rows(alloc.get("equity_curve"))
    yearly_lines = []
    for r in yearly:
        yearly_lines.append(
            f"- {r['year']} 年　收益 **{r['return'] * 100:+.1f}%**　"
            f"最大回撤 {r['max_dd'] * 100:.1f}%"
        )
    if yearly:
        eq_all = alloc.get("equity_curve")
        if eq_all is not None and len(eq_all) > 1:
            total = float(eq_all.iloc[-1] / eq_all.iloc[0] - 1.0)
            max_dd_all = float((eq_all / eq_all.cummax() - 1.0).min())
            y0 = yearly[0]["year"]
            yearly_lines.append(
                f"- **{y0} 至今累计 {total * 100:+.1f}%**　最大回撤 {max_dd_all * 100:.1f}%"
            )
    if not yearly_lines:
        yearly_lines = ["（净值历史不足，暂无分年结果）"]

    # 仓位：权益 / 黄金 / 现金 必展示；债/油有才展示
    pos_line = (
        f"权益仓位 **{buckets['equity']:.0%}**　"
        f"黄金仓位 **{buckets['gold']:.0%}**　"
        f"现金仓位 **{buckets['cash']:.0%}**"
    )
    if buckets["defensive"] > 0.005:
        pos_line += f"　债/油 **{buckets['defensive']:.0%}**"

    # 黄金单独一条建议（有无仓位都要说清）
    gold_w = weights.get(GOLD_SYMBOL, 0.0)
    gold_prev = prev_w.get(GOLD_SYMBOL, 0.0)
    if gold_w > 1e-4 and gold_prev <= 1e-4:
        gold_line = f"- **新进** GLD 黄金ETF → {gold_w:.0%}（分散风险，跟权益对冲）"
    elif gold_w > 1e-4 and gold_prev > 1e-4:
        if abs(gold_w - gold_prev) >= 0.005:
            direction = "加仓" if gold_w > gold_prev else "减仓"
            gold_line = (
                f"- **{direction}** GLD 黄金ETF → {gold_w:.0%}"
                f"（{gold_prev:.0%} → {gold_w:.0%}）"
            )
        else:
            gold_line = f"- **持有** GLD 黄金ETF {gold_w:.0%}（与上月持平，不动）"
    elif gold_prev > 1e-4:
        gold_line = f"- **清仓** GLD 黄金ETF（上月 {gold_prev:.0%} → 0%）"
    else:
        gold_line = "- **不配黄金**（本策略为美股个股，不含 GLD）"

    # 本月调仓
    if not diffs:
        change_lines = ["本月持仓与上月基本一致，不用动手。"]
    else:
        change_lines = []
        for d in diffs[:8]:
            sign = "+" if d["delta"] > 0 else "−"
            change_lines.append(
                f"- **{d['action']}** {display_symbol(d['symbol'], name_map)} "
                f"→ {d['curr']:.0%}（{sign}{abs(d['delta']):.0%}）"
            )
        if len(diffs) > 8:
            change_lines.append(f"- …另有 {len(diffs) - 8} 项小变动")
    change_lines.append(gold_line)

    # 近一年结构（黄金单独一列）
    hist_lines = []
    for row in hist:
        b = split_buckets(row["weights"])
        hist_lines.append(
            f"- {row['label']}　权益 {b['equity']:.0%} / 黄金 {b['gold']:.0%} / "
            f"现金 {b['cash']:.0%}　{top_holdings(row['weights'])}"
        )

    # 执行
    tiger = alloc["tiger_w"]
    domestic = alloc["domestic_w"]
    exec_lines = []
    if domestic:
        names = "、".join(
            f"{display_symbol(s, name_map)} {w:.0%}"
            for s, w in sorted(domestic.items(), key=lambda x: -x[1])
        )
        exec_lines.append(f"- 境内（次日开盘前）：{names}")
    else:
        exec_lines.append("- 境内：无目标仓位，**保持货基/现金**")
    if tiger:
        risk = {s: w for s, w in tiger.items() if s != CASH_PROXY}
        if risk:
            names = "、".join(
                f"{s} {w:.0%}" for s, w in sorted(risk.items(), key=lambda x: -x[1])
            )
            exec_lines.append(f"- 老虎（今晚开盘后）：{names}")
        if tiger.get(CASH_PROXY, 0) > 0.005:
            exec_lines.append(f"- 老虎闲钱：SHY {tiger[CASH_PROXY]:.0%}")
    else:
        exec_lines.append("- 老虎：无风险仓位，闲钱进 SHY")
    if gold_w > 1e-4:
        exec_lines.append(f"- 黄金：老虎买 **GLD {gold_w:.0%}**（或境内黄金 ETF 同步）")
    else:
        exec_lines.append("- 黄金：本月 **0%**，不买 GLD/黄金 ETF")
    exec_lines.append("- 合规：境内只买 **ETF/场外基金**，不碰个股")
    exec_lines.append("- 单票 ≤20%；成交记到 data/live_trades.csv")

    return {
        "title": (
            f"  成长动量·个股 月报 · {as_of.strftime('%Y-%m')} "
            f"｜{BRAKE_LABEL.get(brake, brake)}"
        ),
        "conclusion": conclusion,
        "hint": hint,
        "pos_line": pos_line,
        "change_lines": change_lines,
        "strategy_lines": STRATEGY_LOGIC_LINES,
        "yearly_lines": yearly_lines,
        "hist_lines": hist_lines,
        "exec_lines": exec_lines,
        "severity": "warning" if brake != "none" else "info",
    }


def to_feishu_elements(report: dict) -> list:
    """拼飞书卡片 elements。不用 collapsible（飞书不支持）。"""
    parts = [report["conclusion"]]
    if report["hint"]:
        parts.append(report["hint"])
    return [
        {"tag": "markdown", "content": "\n".join(parts)},
        {"tag": "hr"},
        {"tag": "markdown", "content": f"**仓位占比**\n{report['pos_line']}"},
        {"tag": "hr"},
        {"tag": "markdown", "content": "**本月怎么调**\n" + "\n".join(report["change_lines"])},
        {"tag": "hr"},
        {"tag": "markdown", "content": "**策略逻辑（成长动量·个股）**\n" + "\n".join(report["strategy_lines"])},
        {"tag": "hr"},
        {"tag": "markdown", "content": "**历史每年回测**\n" + "\n".join(report["yearly_lines"])},
        {"tag": "hr"},
        {"tag": "markdown", "content": "**近一年持仓结构**\n" + "\n".join(report["hist_lines"])},
        {"tag": "hr"},
        {"tag": "markdown", "content": "**怎么执行**\n" + "\n".join(report["exec_lines"])},
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
        "【仓位占比】",
        report["pos_line"],
        "",
        "【本月怎么调】",
        *report["change_lines"],
        "",
        "【策略逻辑（成长动量·个股）】",
        *report["strategy_lines"],
        "",
        "【历史每年回测】",
        *report["yearly_lines"],
        "",
        "【近一年持仓结构】",
        *report["hist_lines"],
        "",
        "【怎么执行】",
        *report["exec_lines"],
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
