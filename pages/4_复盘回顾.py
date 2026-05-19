"""P4: 复盘回顾

双轨复盘：机会扫描（P2）+ 策略回测（P3）的历史决策质量评估。
回答"P2 的分类准不准"和"P3 推荐的策略跑赢了吗"两个核心问题。
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.data import DataManager, US_SYMBOLS, CN_SYMBOLS, GLOBAL_SYMBOLS
from core.scanner.scorer import (
    score_all_assets,
    AssetScore,
    OPPORTUNITY_TREND_BUY,
    OPPORTUNITY_TREND_WATCH,
    OPPORTUNITY_MEAN_REVERSION,
    OPPORTUNITY_AVOID,
    OPPORTUNITY_NEUTRAL,
)
from core.scanner.history import ScanHistory
from core.backtest.engine import BacktestEngine, BacktestResult
from core.strategy.strategies import (
    TrendFollowingStrategy,
    RiskParityStrategy,
    MeanReversionStrategy,
    MinCorrelationStrategy,
    DualMomentumStrategy,
    MomentumVolFilterStrategy,
    DrawdownControlStrategy,
    AntifragileStrategy,
    TailRiskParityStrategy,
    DrawdownConstraintStrategy,
    STRATEGY_CATEGORIES,
)


# ============================================================
# 页面配置
# ============================================================
st.set_page_config(
    page_title="复盘回顾 - Market Pulse",
    page_icon=" ",
    layout="wide",
)


@st.cache_resource
def get_data_manager():
    return DataManager()


# ============================================================
# 常量
# ============================================================

HORIZON_OPTIONS = {
    "1 周": 7,
    "2 周": 14,
    "1 个月": 30,
    "3 个月": 90,
}

OPP_CONFIG = {
    OPPORTUNITY_TREND_BUY: {"label": "趋势买入", "icon": " ", "color": "#2e7d32", "bg": "#e8f5e9", "border": "#4caf50"},
    OPPORTUNITY_TREND_WATCH: {"label": "趋势关注", "icon": " ", "color": "#1565c0", "bg": "#e3f2fd", "border": "#42a5f5"},
    OPPORTUNITY_MEAN_REVERSION: {"label": "均值回归", "icon": " ", "color": "#6a1b9a", "bg": "#f3e5f5", "border": "#ab47bc"},
    OPPORTUNITY_AVOID: {"label": "建议规避", "icon": " ", "color": "#c62828", "bg": "#ffebee", "border": "#ef5350"},
    OPPORTUNITY_NEUTRAL: {"label": "中性", "icon": "—", "color": "#757575", "bg": "#fafafa", "border": "#bdbdbd"},
}


# ============================================================
# 工具函数
# ============================================================

def _load_all_assets(dm: DataManager, days: int = 365) -> Dict[str, pd.DataFrame]:
    """加载全部资产数据"""
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    all_symbols = {**US_SYMBOLS, **CN_SYMBOLS, **GLOBAL_SYMBOLS}
    data = {}
    for symbol in all_symbols:
        df = dm.load(symbol, start_date, end_date)
        if not df.empty:
            data[symbol] = df
    return data


def _forward_return_from_data(
    df: Optional[pd.DataFrame],
    signal_date: str,
    horizon_days: int,
) -> Optional[float]:
    """从已加载的数据中计算前向收益"""
    if df is None or df.empty:
        return None

    mask = df["date"] >= signal_date
    sub = df[mask]
    if len(sub) < 2:
        return None

    close = sub["close"].reset_index(drop=True)
    end_idx = min(horizon_days, len(close) - 1)
    if end_idx < 1:
        return None

    entry = close.iloc[0]
    exit_price = close.iloc[end_idx]
    if entry == 0:
        return None

    return (exit_price / entry - 1) * 100


def _forward_return_from_equity(
    equity: pd.Series,
    signal_date: str,
    horizon_days: int,
) -> Optional[float]:
    """从净值曲线计算前向收益"""
    dates_after = equity.index[equity.index >= signal_date]
    if len(dates_after) < 2:
        return None

    entry_val = equity.loc[dates_after[0]]
    end_idx = min(horizon_days, len(dates_after) - 1)
    exit_val = equity.loc[dates_after[end_idx]]

    if entry_val == 0:
        return None

    return (exit_val / entry_val - 1) * 100


def _opp_correctness(opp_type: str, forward_return: float,
                     benchmark_return: Optional[float] = None) -> Optional[bool]:
    """判定机会分类是否正确

    - trend_buy: 前向收益 > 0
    - trend_watch: 前向收益 > -5%
    - mean_reversion: 前向收益 > 0
    - avoid: 前向收益 < 基准收益（或 < 同期中位数）
    """
    if forward_return is None:
        return None

    if opp_type == OPPORTUNITY_TREND_BUY:
        return forward_return > 0
    elif opp_type == OPPORTUNITY_TREND_WATCH:
        return forward_return > -5
    elif opp_type == OPPORTUNITY_MEAN_REVERSION:
        return forward_return > 0
    elif opp_type == OPPORTUNITY_AVOID:
        if benchmark_return is not None:
            return forward_return < benchmark_return
        return forward_return < 0
    return None


# ============================================================
# P2 机会扫描复盘引擎
# ============================================================

def backtest_opportunities(
    dm: DataManager,
    horizon_days: int = 30,
    lookback_days: int = 365,
) -> List[Dict]:
    """基于历史数据模拟 P2 扫描，评估机会分类准确性

    Returns:
        List[Dict] 每个采样点的结果:
        {date, regime, composite, scores: [{symbol, name, opportunity, forward_return, correct}]}
    """
    all_symbols = {**US_SYMBOLS, **CN_SYMBOLS, **GLOBAL_SYMBOLS}

    # 加载全部数据（一次性）
    end_date = datetime.now()
    data_start = (end_date - timedelta(days=lookback_days + 400)).strftime("%Y-%m-%d")
    data_end = end_date.strftime("%Y-%m-%d")

    all_data = {}
    for symbol in all_symbols:
        df = dm.load(symbol, data_start, data_end)
        if not df.empty:
            all_data[symbol] = df

    if not all_data:
        return []

    # 生成采样日期（排除最近 horizon_days 天）
    latest_data_date = max(df["date"].iloc[-1] for df in all_data.values())
    if hasattr(latest_data_date, 'strftime'):
        latest_str = latest_data_date.strftime("%Y-%m-%d")
    else:
        latest_str = str(latest_data_date)[:10]

    cutoff_date = datetime.strptime(latest_str, "%Y-%m-%d") - timedelta(days=horizon_days)
    start_date = datetime.strptime(latest_str, "%Y-%m-%d") - timedelta(days=lookback_days)

    trading_dates = set()
    for df in all_data.values():
        for d in df["date"]:
            ds = d.strftime("%Y-%m-%d") if hasattr(d, 'strftime') else str(d)[:10]
            trading_dates.add(ds)

    sample_dates = sorted([
        d for d in trading_dates
        if start_date.strftime("%Y-%m-%d") <= d <= cutoff_date.strftime("%Y-%m-%d")
    ])

    if not sample_dates:
        return []

    # 逐日回测
    from core.strategy.indicators import detect_market_regime, calc_regime_score

    progress_bar = st.progress(0, text="机会扫描回测中...")
    results = []
    total = len(sample_dates)

    for idx, sample_date in enumerate(sample_dates):
        if idx % 20 == 0 or idx == total - 1:
            progress_bar.progress(
                (idx + 1) / total,
                text=f"机会扫描回测中... {idx + 1}/{total} ({sample_date})"
            )

        # 截取数据到采样日
        sliced = {}
        for sym, df in all_data.items():
            mask = df["date"] <= sample_date
            sub = df[mask].copy()
            if len(sub) >= 60:
                sliced[sym] = sub

        if len(sliced) < 3:
            continue

        # 运行 P2 评分
        scores = score_all_assets(sliced)

        # 计算市场体制
        spy_close = sliced.get("SPY", pd.DataFrame()).get("close", pd.Series())
        regime_name = "中性"
        composite = 50.0
        if not spy_close.empty and len(spy_close) >= 60:
            rs = calc_regime_score(spy_close)
            composite = rs.get("composite", 50)
            tlt_close = sliced.get("TLT", pd.DataFrame()).get("close", pd.Series())
            cl_close = sliced.get("CL", pd.DataFrame()).get("close", pd.Series())
            if not tlt_close.empty and not cl_close.empty:
                regime_info = detect_market_regime(spy_close, tlt_close, cl_close)
                regime_name = regime_info.get("regime", "中性")

        # 计算 SPY 基准收益（用于 avoid 判定）
        spy_fwd = _forward_return_from_data(
            all_data.get("SPY"), sample_date, horizon_days
        )

        # 评估每个资产
        evaluated = []
        for s in scores:
            if s.opportunity == OPPORTUNITY_NEUTRAL:
                continue
            fwd_ret = _forward_return_from_data(
                all_data.get(s.symbol), sample_date, horizon_days
            )
            correct = _opp_correctness(s.opportunity, fwd_ret, spy_fwd)
            evaluated.append({
                "symbol": s.symbol,
                "name": s.name,
                "opportunity": s.opportunity,
                "trend_score": s.trend_score,
                "reversion_score": s.reversion_score,
                "risk_score": s.risk_score,
                "forward_return": fwd_ret,
                "correct": correct,
                "strategies": s.strategies,
                "summary": s.summary,
            })

        results.append({
            "date": sample_date,
            "regime": regime_name,
            "composite": composite,
            "scores": evaluated,
        })

    progress_bar.empty()
    return results


# ============================================================
# P3 策略回测复盘引擎
# ============================================================

def _get_all_strategies():
    """获取全部策略实例"""
    return [
        TrendFollowingStrategy(),
        RiskParityStrategy(),
        MeanReversionStrategy(),
        MinCorrelationStrategy(),
        DualMomentumStrategy(),
        MomentumVolFilterStrategy(),
        DrawdownControlStrategy(),
        AntifragileStrategy(),
        TailRiskParityStrategy(),
        DrawdownConstraintStrategy(),
    ]


def backtest_strategy_rankings(
    dm: DataManager,
    horizon_days: int = 30,
    lookback_days: int = 365,
    sample_interval: int = 30,
) -> Dict:
    """运行全部策略回测 + 在历史采样点评估排名

    Returns:
        Dict:
        - full_results: List[BacktestResult] 全量回测结果（用于 1 年表现总览）
        - sample_results: List[Dict] 每个采样点的排名评估
    """
    all_symbols = {**US_SYMBOLS, **CN_SYMBOLS, **GLOBAL_SYMBOLS}

    # 加载数据（多取 400 天给策略回望）
    end_date = datetime.now()
    data_start = (end_date - timedelta(days=lookback_days + 400)).strftime("%Y-%m-%d")
    data_end = end_date.strftime("%Y-%m-%d")

    all_data = {}
    for symbol in all_symbols:
        df = dm.load(symbol, data_start, data_end)
        if not df.empty:
            all_data[symbol] = df

    if not all_data or "SPY" not in all_data:
        return {"full_results": [], "sample_results": []}

    # 获取最新数据日期
    latest_str = max(
        (df["date"].iloc[-1].strftime("%Y-%m-%d")
         if hasattr(df["date"].iloc[-1], 'strftime')
         else str(df["date"].iloc[-1])[:10])
        for df in all_data.values()
    )

    cutoff_date = datetime.strptime(latest_str, "%Y-%m-%d") - timedelta(days=horizon_days)
    start_date = datetime.strptime(latest_str, "%Y-%m-%d") - timedelta(days=lookback_days)

    # 生成采样日期
    trading_dates = set()
    for df in all_data.values():
        for d in df["date"]:
            ds = d.strftime("%Y-%m-%d") if hasattr(d, 'strftime') else str(d)[:10]
            trading_dates.add(ds)

    all_sorted = sorted(trading_dates)
    sample_dates = [
        d for d in all_sorted
        if start_date.strftime("%Y-%m-%d") <= d <= cutoff_date.strftime("%Y-%m-%d")
    ]
    # 按间隔采样
    if len(sample_dates) > sample_interval:
        step = len(sample_dates) // sample_interval
        sample_dates = sample_dates[::step]

    # 运行全部策略回测（一次性，用全量数据）
    strategies = _get_all_strategies()
    engine = BacktestEngine(risk_free_rate=0.03, commission_rate=0.001, warmup_days=0)

    progress_bar = st.progress(0, text="策略回测中...")

    results_list = engine.run_comparison(strategies, all_data, data_start, data_end)

    progress_bar.progress(0.5, text="分析策略排名...")

    # 构建 SPY 基准净值曲线
    spy_df = all_data["SPY"].copy()
    spy_df = spy_df.sort_values("date").reset_index(drop=True)
    spy_equity = spy_df.set_index("date")["close"]
    spy_equity = spy_equity / spy_equity.iloc[0]  # 归一化

    # 对每个采样点评估
    sample_results = []
    for sample_date in sample_dates:
        rankings = []
        for r in results_list:
            equity = r.equity_curve
            dates_before = equity.index[equity.index <= sample_date]
            if len(dates_before) < 21:
                continue

            recent_dates = dates_before[-min(90, len(dates_before)):]
            recent_equity = equity.loc[recent_dates]
            if len(recent_equity) < 2:
                continue
            daily_ret = recent_equity.pct_change().dropna()
            if len(daily_ret) < 5:
                continue
            sharpe_est = (daily_ret.mean() / daily_ret.std() * (252 ** 0.5)) if daily_ret.std() > 0 else 0

            fwd_ret = _forward_return_from_equity(equity, sample_date, horizon_days)
            spy_fwd = _forward_return_from_equity(spy_equity, sample_date, horizon_days)

            beat_spy = None
            if fwd_ret is not None and spy_fwd is not None:
                beat_spy = fwd_ret > spy_fwd

            rankings.append({
                "name": r.strategy_name,
                "sharpe_est": round(sharpe_est, 2),
                "total_return": r.total_return,
                "max_drawdown": r.max_drawdown,
                "forward_return": fwd_ret,
                "spy_forward_return": spy_fwd,
                "beat_spy": beat_spy,
            })

        if not rankings:
            continue

        rankings.sort(key=lambda x: x["sharpe_est"], reverse=True)
        recommended = rankings[0]["name"]

        sample_results.append({
            "date": sample_date,
            "recommended": recommended,
            "rankings": rankings,
        })

    progress_bar.empty()
    return {"full_results": results_list, "sample_results": sample_results}


# ============================================================
# 侧边栏
# ============================================================

def render_sidebar():
    """侧边栏控制"""
    with st.sidebar:
        st.header("  复盘设置")

        horizon_label = st.selectbox(
            "评估周期",
            options=list(HORIZON_OPTIONS.keys()),
            index=2,
        )

        st.divider()

        # 数据诊断
        scan_history = ScanHistory()
        scan_snapshots = scan_history.get_all_snapshots()
        with st.expander("  数据诊断", expanded=False):
            st.metric("扫描历史快照", len(scan_snapshots))
            if scan_snapshots:
                st.caption(f"最早: {scan_snapshots[0]['date']}")
                st.caption(f"最新: {scan_snapshots[-1]['date']}")

    horizon_days = HORIZON_OPTIONS[horizon_label]
    return horizon_days


# ============================================================
# 机会扫描复盘渲染
# ============================================================

def _bucket_hit(bucket_data: Dict, bucket_key: str) -> Optional[float]:
    """从分桶数据中提取命中率"""
    data = bucket_data.get(bucket_key)
    if data and data["total"] > 0:
        return data["correct"] / data["total"] * 100
    return None


def _generate_opportunity_suggestions(results: List[Dict]) -> List[Dict]:
    """基于评分区间命中率分析，生成 P2 参数优化建议"""

    # 按评分区间分桶
    def bucket(score, bins=(30, 50, 70, 90)):
        for i, b in enumerate(bins):
            if score < b:
                return f"<{b}"
        return f">={bins[-1]}"

    # 按机会类型 + 评分维度聚合
    # 结构: {opp_type: {dim: {bucket: {correct, total, returns}}}}
    agg = {}
    for r in results:
        for s in r["scores"]:
            opp = s["opportunity"]
            if opp == OPPORTUNITY_NEUTRAL:
                continue
            if opp not in agg:
                agg[opp] = {}
            for dim in ["trend_score", "reversion_score", "risk_score"]:
                if dim not in agg[opp]:
                    agg[opp][dim] = {}
                b = bucket(s[dim])
                if b not in agg[opp][dim]:
                    agg[opp][dim][b] = {"correct": 0, "total": 0, "returns": []}
                agg[opp][dim][b]["total"] += 1
                if s["forward_return"] is not None:
                    agg[opp][dim][b]["returns"].append(s["forward_return"])
                    if s["correct"]:
                        agg[opp][dim][b]["correct"] += 1

    suggestions = []

    # 分析 trend_score 区间对趋势买入命中率的影响
    trend_data = agg.get(OPPORTUNITY_TREND_BUY, {}).get("trend_score", {})
    if trend_data:
        low_hit = _bucket_hit(trend_data, "<50")
        mid_hit = _bucket_hit(trend_data, "50-70")
        high_hit = _bucket_hit(trend_data, "70-90")
        if low_hit is not None and high_hit is not None and high_hit - low_hit > 15:
            suggestions.append({
                "parameter": "趋势买入 trend_score 阈值",
                "priority": "高",
                "summary": f"高评分(≥70)命中率 {high_hit:.0f}% 远高于低评分(<50)的 {low_hit:.0f}%",
                "problem": "当前趋势买入可能纳入了低评分资产，拉低整体命中率",
                "suggestion": "提高趋势买入的 trend_score 最低阈值到 60-70，过滤掉弱趋势信号",
                "reason": f"评分区间差异 {high_hit - low_hit:.0f}pp，阈值提升可显著提高信号质量",
            })
        elif low_hit is not None and mid_hit is not None and low_hit > mid_hit:
            suggestions.append({
                "parameter": "趋势买入 trend_score 阈值",
                "priority": "低",
                "summary": "评分与命中率关系不显著，当前阈值可能已合理",
                "problem": f"低评分命中率 {low_hit:.0f}% 反而高于中评分 {mid_hit:.0f}%",
                "suggestion": "暂不调整，可能与样本量不足或市场环境有关",
                "reason": "评分有效性需要更多数据验证",
            })

    # 分析 risk_score 对规避信号的影响
    avoid_data = agg.get(OPPORTUNITY_AVOID, {}).get("risk_score", {})
    if avoid_data:
        high_risk_hit = _bucket_hit(avoid_data, ">=90")
        mid_risk_hit = _bucket_hit(avoid_data, "70-90")
        if high_risk_hit is not None and mid_risk_hit is not None:
            if high_risk_hit > mid_risk_hit + 10:
                suggestions.append({
                    "parameter": "规避 risk_score 阈值",
                    "priority": "中",
                    "summary": f"高风险(≥90)规避准确率 {high_risk_hit:.0f}%，中风险(70-90)仅 {mid_risk_hit:.0f}%",
                    "problem": "中等风险资产被误判为规避，可能错过了机会",
                    "suggestion": "收紧规避条件：risk_score 阈值从当前值提高到 80+，减少误判",
                    "reason": "只有高风险资产才应触发规避，中等风险应降级为中性或关注",
                })

    # 分析 reversion_score 对均值回归的影响
    mr_data = agg.get(OPPORTUNITY_MEAN_REVERSION, {}).get("reversion_score", {})
    if mr_data:
        high_rev = _bucket_hit(mr_data, ">=90")
        mid_rev = _bucket_hit(mr_data, "70-90")
        low_rev = _bucket_hit(mr_data, "<50")
        if high_rev is not None and mid_rev is not None and high_rev > mid_rev + 10:
            suggestions.append({
                "parameter": "均值回归 reversion_score 阈值",
                "priority": "中",
                "summary": f"高回归分(≥90)命中率 {high_rev:.0f}% 远高于中分(70-90)的 {mid_rev:.0f}%",
                "problem": "低分均值回归信号可能是噪音",
                "suggestion": "提高均值回归触发阈值到 80+，只在明确超卖时才触发",
                "reason": "均值回归需要更极端的超卖才有统计优势",
            })

    priority_order = {"高": 0, "中": 1, "低": 2}
    suggestions.sort(key=lambda s: priority_order.get(s["priority"], 9))
    return suggestions


def render_opportunity_review(results: List[Dict]):
    """渲染机会扫描复盘结果"""
    st.subheader("  机会扫描复盘")
    st.caption("P2 说的趋势买入，后来真的涨了吗？规避的后来跌了吗？")

    if not results:
        st.warning("数据不足，无法进行机会扫描回测")
        return

    # 汇总统计
    by_opp = {}
    for r in results:
        for s in r["scores"]:
            opp = s["opportunity"]
            if opp == OPPORTUNITY_NEUTRAL:
                continue
            if opp not in by_opp:
                by_opp[opp] = {"correct": 0, "total": 0, "returns": [], "regret": 0}
            by_opp[opp]["total"] += 1
            if s["forward_return"] is not None:
                by_opp[opp]["returns"].append(s["forward_return"])
                if s["correct"]:
                    by_opp[opp]["correct"] += 1
                # 后悔率：买入类信号亏损 > 5%
                if opp in (OPPORTUNITY_TREND_BUY, OPPORTUNITY_TREND_WATCH) and s["forward_return"] < -5:
                    by_opp[opp]["regret"] += 1

    total_signals = sum(v["total"] for v in by_opp.values())
    if total_signals == 0:
        st.info("无非中性信号可评估")
        return

    # ---- 4 个指标卡 ----
    cols = st.columns(4)
    for col, opp_type in zip(cols, [
        OPPORTUNITY_TREND_BUY, OPPORTUNITY_MEAN_REVERSION,
        OPPORTUNITY_TREND_WATCH, OPPORTUNITY_AVOID
    ]):
        cfg = OPP_CONFIG[opp_type]
        data = by_opp.get(opp_type)
        with col:
            if data and data["total"] > 0:
                hit = data["correct"] / data["total"] * 100
                avg_ret = sum(data["returns"]) / len(data["returns"]) if data["returns"] else 0
                ret_text = f"+{avg_ret:.1f}%" if avg_ret > 0 else f"{avg_ret:.1f}%"
                hit_color = "#2e7d32" if hit >= 60 else "#c62828" if hit < 40 else cfg["color"]
                st.markdown(
                    f'<div style="text-align:center;padding:0.8rem;background:{cfg["bg"]};'
                    f'border-radius:0.5rem;border-bottom:3px solid {cfg["border"]};">'
                    f'<div style="font-size:0.85rem;color:{cfg["color"]};">{cfg["icon"]} {cfg["label"]}</div>'
                    f'<div style="font-size:2rem;font-weight:bold;color:{hit_color};">{hit:.0f}%</div>'
                    f'<div style="font-size:0.8rem;color:#666;">{data["total"]}条 · 均{ret_text}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f'<div style="text-align:center;padding:0.8rem;background:{cfg["bg"]};'
                    f'border-radius:0.5rem;border-bottom:3px solid {cfg["border"]};">'
                    f'<div style="font-size:0.85rem;color:{cfg["color"]};">{cfg["icon"]} {cfg["label"]}</div>'
                    f'<div style="font-size:2rem;font-weight:bold;color:#bdbdbd;">—</div>'
                    f'<div style="font-size:0.8rem;color:#666;">无数据</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

    st.divider()

    # ---- 详细统计表 ----
    col_table, col_chart = st.columns([1, 1])

    with col_table:
        st.markdown("**按机会类型统计**")
        rows = []
        for opp_type in [OPPORTUNITY_TREND_BUY, OPPORTUNITY_TREND_WATCH,
                         OPPORTUNITY_MEAN_REVERSION, OPPORTUNITY_AVOID]:
            data = by_opp.get(opp_type)
            if not data or data["total"] == 0:
                continue
            cfg = OPP_CONFIG[opp_type]
            hit = data["correct"] / data["total"] * 100
            avg_ret = sum(data["returns"]) / len(data["returns"])
            regret_rate = data["regret"] / data["total"] * 100 if data["total"] > 0 else 0
            rows.append({
                "类型": cfg["label"],
                "信号数": data["total"],
                "命中率": f"{hit:.0f}%",
                "平均收益": f"+{avg_ret:.1f}%" if avg_ret > 0 else f"{avg_ret:.1f}%",
                "后悔率": f"{regret_rate:.0f}%",
            })
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    with col_chart:
        st.markdown("**命中率对比**")
        chart_rows = []
        for opp_type in [OPPORTUNITY_TREND_BUY, OPPORTUNITY_MEAN_REVERSION,
                         OPPORTUNITY_TREND_WATCH, OPPORTUNITY_AVOID]:
            data = by_opp.get(opp_type)
            if data and data["total"] > 0:
                cfg = OPP_CONFIG[opp_type]
                hit = data["correct"] / data["total"] * 100
                chart_rows.append({"类型": cfg["label"], "命中率": hit, "color": cfg["color"]})

        if chart_rows:
            cdf = pd.DataFrame(chart_rows)
            fig = go.Figure(go.Bar(
                x=cdf["类型"],
                y=cdf["命中率"],
                marker_color=cdf["color"].tolist(),
                text=[f"{h:.0f}%" for h in cdf["命中率"]],
                textposition="outside",
            ))
            fig.add_hline(y=50, line_dash="dash", line_color="#999", line_width=1)
            fig.update_layout(
                yaxis_title="命中率 (%)",
                yaxis=dict(range=[0, 100]),
                height=300,
                template="plotly_white",
                showlegend=False,
            )
            st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # ---- 评分区间命中率分析 ----
    st.markdown("**  评分区间命中率分析**")
    st.caption("不同评分区间的信号质量差异，用于判断阈值是否需要调整")

    def bucket(score, bins=(30, 50, 70, 90)):
        for i, b in enumerate(bins):
            if score < b:
                return f"<{b}"
        return f">={bins[-1]}"

    bucket_order = ["<30", "30-50", "50-70", "70-90", ">=90"]
    dim_config = [
        ("trend_score", "趋势评分", OPPORTUNITY_TREND_BUY, "#2e7d32"),
        ("reversion_score", "回归评分", OPPORTUNITY_MEAN_REVERSION, "#6a1b9a"),
        ("risk_score", "风险评分", OPPORTUNITY_AVOID, "#c62828"),
    ]

    # 聚合数据
    bucket_agg = {}
    for dim, _, _, _ in dim_config:
        bucket_agg[dim] = {}
    for r in results:
        for s in r["scores"]:
            opp = s["opportunity"]
            if opp == OPPORTUNITY_NEUTRAL:
                continue
            for dim, _, target_opp, _ in dim_config:
                if opp != target_opp:
                    continue
                b = bucket(s[dim])
                if b not in bucket_agg[dim]:
                    bucket_agg[dim][b] = {"correct": 0, "total": 0, "returns": []}
                bucket_agg[dim][b]["total"] += 1
                if s["forward_return"] is not None:
                    bucket_agg[dim][b]["returns"].append(s["forward_return"])
                    if s["correct"]:
                        bucket_agg[dim][b]["correct"] += 1

    # 渲染三个维度的区间对比图
    chart_cols = st.columns(3)
    for col_idx, (dim, label, _, color) in enumerate(dim_config):
        with chart_cols[col_idx]:
            rows = []
            for b in bucket_order:
                d = bucket_agg[dim].get(b)
                if d and d["total"] > 0:
                    hit = d["correct"] / d["total"] * 100
                    rows.append({"区间": b, "命中率": hit, "信号数": d["total"]})
            if rows:
                rdf = pd.DataFrame(rows)
                fig = go.Figure(go.Bar(
                    x=rdf["区间"], y=rdf["命中率"],
                    marker_color=color, opacity=0.8,
                    text=[f"{h:.0f}%({n}条)" for h, n in zip(rdf["命中率"], rdf["信号数"])],
                    textposition="outside", textfont=dict(size=10),
                ))
                fig.add_hline(y=50, line_dash="dash", line_color="#999", line_width=1)
                fig.update_layout(
                    title=label, yaxis_title="命中率(%)",
                    yaxis=dict(range=[0, 105]), height=280,
                    template="plotly_white", showlegend=False,
                )
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.caption(f"{label}: 数据不足")

    st.divider()

    # ---- 参数优化建议 ----
    st.markdown("**  P2 参数优化方向**")
    st.caption("基于评分区间分析，识别 P2 评分模型中值得调整的阈值参数")

    suggestions = _generate_opportunity_suggestions(results)
    if suggestions:
        for s in suggestions:
            icon = " " if s["priority"] == "高" else " " if s["priority"] == "中" else " "
            with st.expander(f"{icon} {s['parameter']} — {s['summary']}", expanded=s["priority"] == "高"):
                st.markdown(f"**问题**: {s['problem']}")
                st.markdown(f"**建议**: {s['suggestion']}")
                st.markdown(f"**理由**: {s['reason']}")
    else:
        st.info("评分区间命中率分布均匀，当前阈值参数暂无明显优化空间")

    st.divider()

    # ---- 按资产细分 ----
    with st.expander("  按资产细分", expanded=False):
        all_symbols = {**US_SYMBOLS, **CN_SYMBOLS, **GLOBAL_SYMBOLS}
        asset_stats = {}
        for r in results:
            for s in r["scores"]:
                sym = s["symbol"]
                if sym not in asset_stats:
                    asset_stats[sym] = {"correct": 0, "total": 0, "returns": []}
                asset_stats[sym]["total"] += 1
                if s["forward_return"] is not None:
                    asset_stats[sym]["returns"].append(s["forward_return"])
                    if s["correct"]:
                        asset_stats[sym]["correct"] += 1

        asset_rows = []
        for sym, data in sorted(asset_stats.items(), key=lambda x: -x[1]["total"]):
            if data["total"] == 0:
                continue
            hit = data["correct"] / data["total"] * 100
            avg_ret = sum(data["returns"]) / len(data["returns"]) if data["returns"] else 0
            asset_rows.append({
                "资产": all_symbols.get(sym, sym),
                "代码": sym,
                "信号数": data["total"],
                "命中率": f"{hit:.0f}%",
                "平均收益": f"+{avg_ret:.1f}%" if avg_ret > 0 else f"{avg_ret:.1f}%",
            })
        if asset_rows:
            st.dataframe(pd.DataFrame(asset_rows), use_container_width=True, hide_index=True)

    # ---- 命中率走势 ----
    with st.expander("  命中率走势", expanded=False):
        weekly_data = []
        for r in results:
            evaluable = [s for s in r["scores"] if s["correct"] is not None]
            if evaluable:
                hit = sum(1 for s in evaluable if s["correct"]) / len(evaluable) * 100
                weekly_data.append({"date": r["date"], "hit_rate": hit, "count": len(evaluable)})

        if weekly_data:
            wdf = pd.DataFrame(weekly_data)
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=wdf["date"],
                y=wdf["hit_rate"],
                mode="lines+markers",
                name="命中率",
                line=dict(width=2.5, color="#2196f3"),
                marker=dict(size=6),
                text=[f"{c}条" for c in wdf["count"]],
                hovertemplate="%{x}<br>命中率: %{y:.0f}%<br>信号数: %{text}<extra></extra>",
            ))
            fig.add_hline(y=50, line_dash="dash", line_color="#999", line_width=1)
            fig.update_layout(
                yaxis_title="命中率 (%)",
                yaxis=dict(range=[0, 100]),
                height=350,
                template="plotly_white",
                showlegend=False,
            )
            st.plotly_chart(fig, use_container_width=True)

    # ---- 指标说明 ----
    with st.expander("  指标含义", expanded=False):
        st.markdown("""
**命中率** — 各类型信号中，方向判断正确的比例。越高越好。

| 机会类型 | 正确判定 | 含义 |
|---------|---------|------|
| 趋势买入 | 前向收益 > 0 | 说"买"，后来真的涨了 |
| 趋势关注 | 前向收益 > -5% | 说"关注"，后来没有暴跌 |
| 均值回归 | 前向收益 > 0 | 说"超卖会反弹"，后来真的弹了 |
| 建议规避 | 前向收益 < 基准 | 说"别碰"，后来跑输了大盘 |

**后悔率** — 买入/关注信号中，亏损超过 5% 的比例。越低越好。反映系统的资本保护能力。
        """)


# ============================================================
# 策略回测复盘渲染
# ============================================================

def render_strategy_review(data: Dict, horizon_days: int = 30):
    """渲染策略回测复盘结果"""
    st.subheader("  策略回测复盘")
    st.caption("P3 推荐的策略，后来跑赢 SPY 了吗？")

    full_results = data.get("full_results", [])
    sample_results = data.get("sample_results", [])

    if not full_results:
        st.warning("数据不足，无法进行策略回测复盘")
        return

    # ============================================================
    # 全策略 1 年表现总览
    # ============================================================
    st.markdown("**  全策略近 1 年表现总览**")

    # SPY 基准
    spy_total = None
    spy_sharpe = None
    spy_dd = None
    for r in full_results:
        if "SPY" in r.strategy_name:
            spy_total = r.total_return
            spy_sharpe = r.sharpe_ratio
            spy_dd = r.max_drawdown
            break

    perf_rows = []
    for r in full_results:
        perf_rows.append({
            "策略": r.strategy_name,
            "总收益%": r.total_return,
            "年化%": r.annualized_return,
            "最大回撤%": r.max_drawdown,
            "Sharpe": r.sharpe_ratio,
            "波动率%": r.volatility,
            "月胜率%": r.win_rate,
            "再平衡次数": r.num_rebalances,
        })

    perf_df = pd.DataFrame(perf_rows)
    perf_df = perf_df.sort_values("Sharpe", ascending=False).reset_index(drop=True)

    # 格式化显示
    display_df = perf_df.copy()
    for col in ["总收益%", "年化%", "波动率%"]:
        display_df[col] = display_df[col].apply(lambda x: f"+{x:.1f}%" if x > 0 else f"{x:.1f}%")
    display_df["最大回撤%"] = display_df["最大回撤%"].apply(lambda x: f"{x:.1f}%")
    display_df["Sharpe"] = display_df["Sharpe"].apply(lambda x: f"{x:.2f}")
    display_df["月胜率%"] = display_df["月胜率%"].apply(lambda x: f"{x:.0f}%")

    st.dataframe(display_df, use_container_width=True, hide_index=True)

    # 净值曲线对比
    fig = go.Figure()
    for r in full_results:
        equity = r.equity_curve
        fig.add_trace(go.Scatter(
            x=equity.index,
            y=equity.values,
            mode="lines",
            name=r.strategy_name,
            line=dict(width=1.5),
        ))
    fig.update_layout(
        title="全策略净值曲线",
        yaxis_title="净值",
        height=400,
        template="plotly_white",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="x unified",
    )
    st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # ============================================================
    # 参数优化建议
    # ============================================================
    st.markdown("**  策略参数优化方向**")
    st.caption("基于近 1 年回测表现，识别值得调整的参数")

    suggestions = _generate_strategy_suggestions(perf_df, full_results)

    if suggestions:
        for s in suggestions:
            icon = " " if s["priority"] == "高" else " " if s["priority"] == "中" else " "
            with st.expander(f"{icon} {s['strategy']} — {s['summary']}", expanded=s["priority"] == "高"):
                st.markdown(f"**问题**: {s['problem']}")
                st.markdown(f"**当前参数**: `{s['current']}`")
                st.markdown(f"**建议调整**: {s['suggestion']}")
                st.markdown(f"**理由**: {s['reason']}")
    else:
        st.info("各策略表现正常，暂无明显优化方向")

    st.divider()

    # ============================================================
    # 推荐策略评估（原有逻辑）
    # ============================================================
    if not sample_results:
        st.info("采样点不足，无法评估推荐策略")
        return

    st.markdown("**  推荐策略跑赢率评估**")

    # 汇总统计
    total_evaluated = 0
    total_beat = 0
    all_excess = []
    strategy_recommended_count = {}
    strategy_beat_count = {}

    for r in sample_results:
        rec = r["recommended"]
        strategy_recommended_count[rec] = strategy_recommended_count.get(rec, 0) + 1

        top = r["rankings"][0] if r["rankings"] else None
        if top and top["beat_spy"] is not None:
            total_evaluated += 1
            if top["beat_spy"]:
                total_beat += 1
                strategy_beat_count[rec] = strategy_beat_count.get(rec, 0) + 1
            if top["forward_return"] is not None and top["spy_forward_return"] is not None:
                all_excess.append(top["forward_return"] - top["spy_forward_return"])

    hit_rate = (total_beat / total_evaluated * 100) if total_evaluated > 0 else 0
    avg_excess = (sum(all_excess) / len(all_excess)) if all_excess else 0

    # 指标卡
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        hit_color = "#2e7d32" if hit_rate >= 60 else "#c62828" if hit_rate < 40 else "#f57c00"
        st.markdown(
            f'<div style="text-align:center;padding:0.8rem;background:#e3f2fd;'
            f'border-radius:0.5rem;border-bottom:3px solid #42a5f5;">'
            f'<div style="font-size:0.85rem;color:#1565c0;">推荐策略跑赢率</div>'
            f'<div style="font-size:2rem;font-weight:bold;color:{hit_color};">{hit_rate:.0f}%</div>'
            f'<div style="font-size:0.8rem;color:#666;">{total_beat}/{total_evaluated}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    with c2:
        excess_color = "#2e7d32" if avg_excess > 0 else "#c62828"
        st.markdown(
            f'<div style="text-align:center;padding:0.8rem;background:#e8f5e9;'
            f'border-radius:0.5rem;border-bottom:3px solid #4caf50;">'
            f'<div style="font-size:0.85rem;color:#2e7d32;">平均超额收益</div>'
            f'<div style="font-size:2rem;font-weight:bold;color:{excess_color};">{avg_excess:+.1f}%</div>'
            f'<div style="font-size:0.8rem;color:#666;">vs SPY</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    with c3:
        best_strategy = max(strategy_recommended_count.items(), key=lambda x: x[1])[0] if strategy_recommended_count else "N/A"
        st.markdown(
            f'<div style="text-align:center;padding:0.8rem;background:#f3e5f5;'
            f'border-radius:0.5rem;border-bottom:3px solid #ab47bc;">'
            f'<div style="font-size:0.85rem;color:#6a1b9a;">最常被推荐</div>'
            f'<div style="font-size:1.1rem;font-weight:bold;color:#6a1b9a;">{best_strategy}</div>'
            f'<div style="font-size:0.8rem;color:#666;">{strategy_recommended_count.get(best_strategy, 0)}次</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    with c4:
        st.metric("采样点数", len(sample_results))

    # 推荐策略 vs SPY
    dates = [r["date"] for r in sample_results]
    rec_returns = [r["rankings"][0]["forward_return"] if r["rankings"] else None for r in sample_results]
    spy_returns = [r["rankings"][0]["spy_forward_return"] if r["rankings"] else None for r in sample_results]

    fig = go.Figure()
    fig.add_trace(go.Bar(x=dates, y=rec_returns, name="推荐策略", marker_color="#2196f3", opacity=0.7))
    fig.add_trace(go.Bar(x=dates, y=spy_returns, name="SPY", marker_color="#999", opacity=0.5))
    fig.update_layout(
        yaxis_title="前向收益 (%)", height=300, template="plotly_white", barmode="group",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    fig.add_hline(y=0, line_dash="solid", line_color="#666", line_width=1)
    st.plotly_chart(fig, use_container_width=True)

    # 详细记录
    with st.expander("  详细记录", expanded=False):
        rows = []
        for r in sample_results:
            top = r["rankings"][0] if r["rankings"] else None
            if not top:
                continue
            fwd = top["forward_return"]
            spy_fwd = top["spy_forward_return"]
            excess = (fwd - spy_fwd) if fwd is not None and spy_fwd is not None else None
            rows.append({
                "日期": r["date"],
                "推荐策略": r["recommended"],
                "Sharpe估计": top["sharpe_est"],
                "策略收益": f"+{fwd:.1f}%" if fwd and fwd > 0 else f"{fwd:.1f}%" if fwd else "N/A",
                "SPY收益": f"+{spy_fwd:.1f}%" if spy_fwd and spy_fwd > 0 else f"{spy_fwd:.1f}%" if spy_fwd else "N/A",
                "超额": f"+{excess:.1f}%" if excess and excess > 0 else f"{excess:.1f}%" if excess else "N/A",
                "跑赢": "✅" if top["beat_spy"] else "❌" if top["beat_spy"] is False else "—",
            })
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _generate_strategy_suggestions(perf_df: pd.DataFrame, full_results: List[BacktestResult]) -> List[Dict]:
    """基于回测表现生成策略参数优化建议"""
    suggestions = []

    # SPY 基准
    spy_row = perf_df[perf_df["策略"].str.contains("SPY", case=False, na=False)]
    spy_sharpe = spy_row["Sharpe"].values[0] if not spy_row.empty else 0.5
    spy_return = perf_df.loc[perf_df["策略"].str.contains("SPY", case=False, na=False), "总收益%"].values[0] if not spy_row.empty else 0

    for _, row in perf_df.iterrows():
        name = row["策略"]
        sharpe = row["Sharpe"]
        total_ret = row["总收益%"]
        max_dd = row["最大回撤%"]
        vol = row["波动率%"]

        # ---- 趋势跟踪策略 ----
        if "趋势跟踪" in name:
            if sharpe < 0.3:
                suggestions.append({
                    "strategy": name,
                    "priority": "高",
                    "summary": "Sharpe 过低，信号过滤不足",
                    "problem": f"Sharpe {sharpe:.2f}，可能持有过多假突破信号",
                    "current": "MA 多头排列即持仓，MACD>0 为全仓",
                    "suggestion": "增加 MACD 确认要求（histogram > 阈值），或加入 RSI 过滤（RSI > 50 才持仓）",
                    "reason": "纯均线策略在震荡市频繁假突破，加入动量确认可减少无效交易",
                })

        # ---- 均值回归策略 ----
        if "均值回归" in name:
            if max_dd < -20:
                suggestions.append({
                    "strategy": name,
                    "priority": "高",
                    "summary": "最大回撤过大，抄底阈值过于激进",
                    "problem": f"最大回撤 {max_dd:.1f}%，RSI<30 就全仓买入风险过高",
                    "current": "RSI<30: 全仓, 30-50: 70%, 50-70: 30%, >=70: 不持仓",
                    "suggestion": "RSI<30 改为 80% 仓位，加入回撤过滤（资产回撤 > -25% 才抄底）",
                    "reason": "深度下跌时 RSI 可能长期低于 30，全仓抄底会承受巨大回撤",
                })

        # ---- 风险平价策略 ----
        if "风险平价" in name and "尾部" not in name:
            if sharpe < 0.2:
                suggestions.append({
                    "strategy": name,
                    "priority": "中",
                    "summary": "收益过低，波动率阈值可能过于宽松",
                    "problem": f"Sharpe {sharpe:.2f}，可能纳入了过多低质量资产",
                    "current": "min_volatility=0.5%",
                    "suggestion": "提高 min_volatility 到 1.0%，排除波动率过低的无效资产",
                    "reason": "波动率极低的资产往往收益也低，拖累组合表现",
                })

        # ---- 双动量策略 ----
        if "双动量" in name:
            if max_dd < -15:
                suggestions.append({
                    "strategy": name,
                    "priority": "中",
                    "summary": "回撤偏大，绝对动量过滤可能不够严格",
                    "problem": f"最大回撤 {max_dd:.1f}%，12 月动量>0 的过滤条件在拐点失效",
                    "current": "12 月绝对动量 > 0 才持仓",
                    "suggestion": "加入 6 月动量二次确认，或在 SPY 回撤 > -8% 时降仓",
                    "reason": "单一 12 月动量在市场转折时滞后，双时间框架可提前预警",
                })

        # ---- 动量+波动率过滤 ----
        if "动量+波动率" in name:
            if vol > 20:
                suggestions.append({
                    "strategy": name,
                    "priority": "中",
                    "summary": "组合波动率偏高，目标波动率可能设置过高",
                    "problem": f"波动率 {vol:.1f}%，target_vol=12% 可能未生效",
                    "current": "target_vol=12.0%, min_momentum=50",
                    "suggestion": "降低 target_vol 到 10%，或提高 min_momentum 到 60",
                    "reason": "高波动环境下仓位缩放不足，需要更严格的入场条件",
                })

        # ---- 回撤控制策略 ----
        if "回撤控制" in name:
            if max_dd < -12:
                suggestions.append({
                    "strategy": name,
                    "priority": "高",
                    "summary": "回撤阈值过于宽松，减仓不够及时",
                    "problem": f"最大回撤 {max_dd:.1f}%，但阈值在 -5% 才开始减仓",
                    "current": "DD<-5%: 满仓, <-8%: 半仓, <-10%: 20%, >-10%: 清仓",
                    "suggestion": "提前减仓：DD<-3%: 80%, <-5%: 50%, <-8%: 20%, >-10%: 清仓",
                    "reason": "用户保本优先，应在回撤初期就开始降仓",
                })

        # ---- 反脆弱策略 ----
        if "反脆弱" in name:
            if total_ret < -10:
                suggestions.append({
                    "strategy": name,
                    "priority": "中",
                    "summary": "对冲成本过高，非大跌年份净拖累严重",
                    "problem": f"总收益 {total_ret:+.1f}%，年化对冲成本 ~12% 在非危机年份是沉重负担",
                    "current": "hedge_monthly_cost=1.0%, hedge_otm_threshold=5%, hedge_leverage=35",
                    "suggestion": "降低 OTM 阈值到 3%（更频繁触发赔付），或将权利金预算降到 0.7%/月",
                    "reason": "反脆弱策略的代价是持续出血，如果大跌年份不够多，净收益会很差",
                })

        # ---- 尾部风险平价 ----
        if "尾部风险平价" in name:
            if sharpe < 0.3:
                suggestions.append({
                    "strategy": name,
                    "priority": "中",
                    "summary": "CVaR 过滤可能过于保守",
                    "problem": f"Sharpe {sharpe:.2f}，alpha=5% 的 CVaR 过滤可能排除了太多资产",
                    "current": "alpha=0.05, vol_scale_threshold=1.5, min_momentum=40",
                    "suggestion": "放宽 alpha 到 0.10（10% CVaR），或降低 vol_scale_threshold 到 1.2",
                    "reason": "过于保守的尾部过滤导致仓位长期偏低，错过上涨",
                })

        # ---- 回撤约束优化 ----
        if "回撤约束" in name:
            if max_dd < -10:
                suggestions.append({
                    "strategy": name,
                    "priority": "中",
                    "summary": "资产级回撤过滤可能不够严格",
                    "problem": f"最大回撤 {max_dd:.1f}%，asset_max_dd=-40% 过于宽松",
                    "current": "asset_max_dd=-40%, 组合 DD<-8%: 满仓, <-12%: 60%",
                    "suggestion": "收紧 asset_max_dd 到 -30%，组合阈值提前到 -6%/-10%/-14%",
                    "reason": "允许 -40% 回撤的资产在熊市中损失过大",
                })

    # 按优先级排序
    priority_order = {"高": 0, "中": 1, "低": 2}
    suggestions.sort(key=lambda s: priority_order.get(s["priority"], 9))
    return suggestions


# ============================================================
# 主函数
# ============================================================

def render_market_recommendation(dm: DataManager):
    """基于当前市场体制推荐最适合的策略"""
    from core.strategy.indicators import detect_market_regime, calc_regime_score, calc_momentum_score

    st.subheader("  当前市场策略推荐")
    st.caption("基于市场体制和近 1 年回测数据，推荐当前最适合的策略组合")

    # 加载数据
    end_date = datetime.now()
    data_start = (end_date - timedelta(days=400)).strftime("%Y-%m-%d")
    data_end = end_date.strftime("%Y-%m-%d")
    all_symbols = {**US_SYMBOLS, **CN_SYMBOLS, **GLOBAL_SYMBOLS}
    data = {}
    for symbol in all_symbols:
        df = dm.load(symbol, data_start, data_end)
        if not df.empty:
            data[symbol] = df

    spy_close = data.get("SPY", pd.DataFrame()).get("close", pd.Series())
    tlt_close = data.get("TLT", pd.DataFrame()).get("close", pd.Series())
    cl_close = data.get("CL", pd.DataFrame()).get("close", pd.Series())

    if spy_close.empty:
        st.warning("数据不足，无法检测市场体制")
        return

    # 检测市场体制
    regime = {"regime": "中性", "description": "数据不足"}
    if not tlt_close.empty and not cl_close.empty:
        regime = detect_market_regime(spy_close, tlt_close, cl_close)

    rs = calc_regime_score(spy_close)
    composite = rs.get("composite", 50)
    vol_regime = rs.get("vol_regime", "正常")

    # 体制 → 策略推荐规则
    # 每个体制推荐 3 个策略，优先不同类别以确保多样性
    REGIME_RECOMMENDATIONS = {
        "风险偏好": {
            "label": "  风险偏好",
            "color": "#2e7d32",
            "bg": "#e8f5e9",
            "advice": "趋势向上 + 股强于债，动量策略收割趋势收益",
            "strategies": [
                {"name": "双动量策略", "reason": "绝对动量过滤 + 相对动量选最强资产"},
                {"name": "动量+波动率过滤", "reason": "动量选股 + 波动率控仓，稳步上涨"},
                {"name": "趋势跟踪策略", "reason": "均线多头排列 + MACD 确认，顺势而为"},
            ],
        },
        "避险": {
            "label": " ️ 避险",
            "color": "#1565c0",
            "bg": "#e3f2fd",
            "advice": "趋势向下，减少权益暴露，转向风险配置和对冲",
            "strategies": [
                {"name": "风险平价策略", "reason": "波动率反比配置，自动降仓高波资产"},
                {"name": "低相关性组合策略", "reason": "最大分散化，降低单一资产风险"},
                {"name": "回撤控制策略", "reason": "回撤风控层自动减仓，保本优先"},
            ],
        },
        "危机": {
            "label": "  危机",
            "color": "#c62828",
            "bg": "#ffebee",
            "advice": "高波动 + 趋势崩塌，保护本金为第一优先",
            "strategies": [
                {"name": "反脆弱策略", "reason": "尾部对冲在暴跌时产生凸性收益"},
                {"name": "回撤约束优化", "reason": "资产级+组合级双重回撤硬约束"},
                {"name": "回撤控制策略", "reason": "跌破阈值自动减仓，严格风控"},
            ],
        },
        "滞胀担忧": {
            "label": "  滞胀担忧",
            "color": "#e65100",
            "bg": "#fff3e0",
            "advice": "趋势不明 + 高波动，降低仓位等待明朗",
            "strategies": [
                {"name": "均值回归策略", "reason": "震荡市中 RSI 超卖反弹获利"},
                {"name": "尾部风险平价", "reason": "CVaR+动量联合优化，避开高尾部风险资产"},
                {"name": "回撤控制策略", "reason": "回撤阈值自动降仓，控制下行风险"},
            ],
        },
        "震荡": {
            "label": "  震荡",
            "color": "#757575",
            "bg": "#f5f5f5",
            "advice": "方向不明，适合均值回归和分散化配置",
            "strategies": [
                {"name": "均值回归策略", "reason": "RSI 超卖超买交替，天然适合震荡"},
                {"name": "低相关性组合策略", "reason": "最大分散化降低震荡市的来回割"},
                {"name": "风险平价策略", "reason": "不判断方向，按波动率均衡分配"},
            ],
        },
        "中性": {
            "label": "  中性",
            "color": "#757575",
            "bg": "#f5f5f5",
            "advice": "无明确信号，均衡配置等待方向",
            "strategies": [
                {"name": "风险平价策略", "reason": "不判断方向，按波动率均衡分配"},
                {"name": "双动量策略", "reason": "绝对动量过滤，正收益资产才持有"},
                {"name": "低相关性组合策略", "reason": "分散化配置，降低整体波动"},
            ],
        },
    }

    rec = REGIME_RECOMMENDATIONS.get(regime["regime"], REGIME_RECOMMENDATIONS["中性"])

    # 渲染体制卡片
    st.markdown(
        f'<div style="padding:1rem 1.2rem;background:{rec["bg"]};border-left:5px solid {rec["color"]};'
        f'border-radius:0 0.5rem 0.5rem 0;margin-bottom:1rem;">'
        f'<div style="font-size:1.5rem;font-weight:bold;color:{rec["color"]};">{rec["label"]}</div>'
        f'<div style="color:#555;margin-top:0.3rem;">{regime["description"]}</div>'
        f'<div style="color:#333;margin-top:0.5rem;font-weight:500;">  {rec["advice"]}</div>'
        f'<div style="color:#888;margin-top:0.3rem;font-size:0.85rem;">综合评分 {composite:.0f} · 波动率 {vol_regime}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # 渲染推荐策略卡片
    cols = st.columns(3)
    for col, s in zip(cols, rec["strategies"]):
        with col:
            # 查找策略在回测中的 Sharpe
            sharpe_text = ""
            # 从 strategies.py 获取策略描述
            strategy_cls = None
            for cls in [TrendFollowingStrategy, RiskParityStrategy, MeanReversionStrategy,
                        MinCorrelationStrategy, DualMomentumStrategy, MomentumVolFilterStrategy,
                        DrawdownControlStrategy, AntifragileStrategy, TailRiskParityStrategy,
                        DrawdownConstraintStrategy]:
                if cls.name == s["name"]:
                    strategy_cls = cls
                    break

            desc = strategy_cls.description if strategy_cls else ""
            cat_label = ""
            if strategy_cls and strategy_cls.category in STRATEGY_CATEGORIES:
                cat_label = STRATEGY_CATEGORIES[strategy_cls.category]["label"]

            st.markdown(
                f'<div style="padding:0.8rem;background:#fff;border:1px solid #e0e0e0;border-radius:0.5rem;'
                f'border-top:3px solid {rec["color"]};height:160px;">'
                f'<div style="font-size:1rem;font-weight:600;">{s["name"]}</div>'
                f'<div style="font-size:0.75rem;color:#888;margin:0.2rem 0;">{cat_label}</div>'
                f'<div style="font-size:0.85rem;color:#555;margin-top:0.4rem;">{s["reason"]}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    st.divider()


def main():
    st.title("  复盘回顾")
    st.caption("双轨复盘：机会扫描分类准确性 + 策略推荐跑赢率")

    dm = get_data_manager()
    horizon_days = render_sidebar()

    # ============================================================
    # 区块 0: 市场策略推荐
    # ============================================================
    render_market_recommendation(dm)

    # ============================================================
    # 区块 A: 机会扫描复盘
    # ============================================================
    with st.spinner("运行机会扫描回测..."):
        opp_results = backtest_opportunities(dm, horizon_days=horizon_days)

    render_opportunity_review(opp_results)

    st.divider()

    # ============================================================
    # 区块 B: 策略回测复盘
    # ============================================================
    with st.spinner("运行策略回测复盘..."):
        strat_results = backtest_strategy_rankings(dm, horizon_days=horizon_days)

    render_strategy_review(strat_results, horizon_days)


if __name__ == "__main__":
    main()
