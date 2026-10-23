"""P4: 复盘回顾

双轨复盘：机会扫描（P2）+ 策略回测（P3）的历史决策质量评估。
回答"P2 的分类准不准"和"P3 推荐的策略跑赢了吗"两个核心问题。
当前市场体制与策略推荐在 P2 顶部 / 主页，本页不重复展示。
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import streamlit as st
import numpy as np
import pandas as pd
import plotly.graph_objects as go

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.data.config import STRATEGY_UNIVERSE, GROWTH_UNIVERSE
from core.data.manager import DataManager
from core.scanner.scorer import (
    score_all_assets,
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
    TimeSeriesMomentumStrategy,
    CTABreakoutStrategy,
    MomentumChandelierStrategy,
    RiskParityStrategy,
    MinCorrelationStrategy,
    DualMomentumStrategy,
    DualMomentumKellyStrategy,
    DualMomentumKellyFilterStrategy,
    StockMomentumGrowthStrategy,
    QQQBuyHoldStrategy,
    SPYBuyHoldStrategy,
    DualMomentumKellyCTASleeveStrategy,
    DualMomentumKellyHedgeSleeveStrategy,
    AntifragileStrategy,
    AntifragileAggressiveStrategy,
    AntifragileWeeklyStrategy,
    AntifragileDailyStrategy,
    TailRiskParityStrategy,
    DrawdownConstraintStrategy,
)
from core.strategy.indicators import detect_market_regime, calc_regime_score, calc_drawdown
from core.strategy.regime_config import REGIME_COLORS
from app.components.tooltips import (
    HIT_RATE, REGRET_RATE,
    PROFIT_LOSS_RATIO, STRATEGY_HIT_RATE,
    ANNUAL_RETURN, TOTAL_RETURN, MAX_DRAWDOWN, SHARPE_RATIO,
    VOLATILITY, WIN_RATE,
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

# 生产策略 = P3/月报/调仓单实际在用的那一个。P4 的「推荐」一律指它，
# 不能拿夏普榜第一当推荐——低波底仓（风险平价）会系统性霸榜。
PRODUCTION_STRATEGY_NAME = "成长动量·个股"

# 角色标注：让「谁更好」不再被夏普误导
_STRATEGY_ROLES = {
    "成长动量·个股": "生产主策略",
    "满仓QQQ长期持有": "基准",
    "满仓SPY长期持有": "基准",
    "凯利·门槛8%": "前生产策略",
    "双动量×凯利": "主策略同源",
    "凯利·70/30 CTA": "主+卫星拼盘",
    "凯利·70/30 反脆弱": "主+对冲拼盘",
    "风险平价策略": "底仓",
    "低相关性组合策略": "底仓",
    "尾部风险平价": "底仓+尾部对冲",
    "TSMOM 趋势跟随": "趋势底仓",
    "趋势跟踪策略": "趋势底仓",
    "CTA 突破跟随": "卫星",
    "动量吊灯跟随": "卫星",
    "双动量策略": "对照（等权）",
    "回撤约束优化": "对照（约束版）",
    "反脆弱策略": "对冲实验",
    "反脆弱激进版": "对冲实验",
    "反脆弱周频版": "对冲实验",
    "反脆弱日频版": "对冲实验",
    "SPY 买入持有": "基准",
}


def _strategy_role(name: str) -> str:
    return _STRATEGY_ROLES.get(name, "其他")

OPP_CONFIG = {
    OPPORTUNITY_TREND_BUY: {"label": "过门槛", "icon": " ", "color": "#2e7d32", "bg": "#e8f5e9", "border": "#4caf50"},
    OPPORTUNITY_TREND_WATCH: {"label": "门槛附近", "icon": " ", "color": "#1565c0", "bg": "#e3f2fd", "border": "#42a5f5"},
    OPPORTUNITY_MEAN_REVERSION: {"label": "旧·均值回归", "icon": " ️", "color": "#6a1b9a", "bg": "#f3e5f5", "border": "#ab47bc"},
    OPPORTUNITY_AVOID: {"label": "不碰", "icon": " ", "color": "#c62828", "bg": "#ffebee", "border": "#ef5350"},
    OPPORTUNITY_NEUTRAL: {"label": "数据不足", "icon": "—", "color": "#757575", "bg": "#fafafa", "border": "#bdbdbd"},
}


# ============================================================
# 工具函数
# ============================================================

def _load_all_assets(dm: DataManager, days: int = 365) -> Dict[str, pd.DataFrame]:
    """加载全部资产数据"""
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    all_symbols = dict(STRATEGY_UNIVERSE)
    all_symbols.update(GROWTH_UNIVERSE)
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
    lookback_days: int = 730,
) -> List[Dict]:
    """基于历史数据模拟 P2 扫描，评估机会分类准确性

    Returns:
        List[Dict] 每个采样点的结果:
        {date, regime, composite, scores: [{symbol, name, opportunity, forward_return, correct}]}
    """
    all_symbols = STRATEGY_UNIVERSE

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
    """获取在产策略实例（退役策略不进复盘排名）"""
    return [
        TrendFollowingStrategy(),
        TimeSeriesMomentumStrategy(),
        CTABreakoutStrategy(),
        MomentumChandelierStrategy(),
        DualMomentumStrategy(),
        DualMomentumKellyStrategy(),
        DualMomentumKellyFilterStrategy(),
        StockMomentumGrowthStrategy(),
        QQQBuyHoldStrategy(),
        SPYBuyHoldStrategy(),
        DualMomentumKellyCTASleeveStrategy(),
        DualMomentumKellyHedgeSleeveStrategy(),
        RiskParityStrategy(),
        MinCorrelationStrategy(),
        TailRiskParityStrategy(),
        DrawdownConstraintStrategy(),
        AntifragileStrategy(),
        AntifragileAggressiveStrategy(),
        AntifragileWeeklyStrategy(),
        AntifragileDailyStrategy(),
    ]


def _spy_benchmark_result(spy_df: pd.DataFrame, start_date: str,
                          end_date: str) -> Optional[BacktestResult]:
    """构造「SPY 买入持有」基准结果，让跑赢对比有对手。

    full_results 里没有这一行时，页面只能显示「样本不足」。
    """
    if spy_df is None or spy_df.empty:
        return None
    df = spy_df.copy()
    df = df[(df["date"] >= start_date) & (df["date"] <= end_date)]
    if len(df) < 2:
        return None
    df = df.sort_values("date").reset_index(drop=True)
    equity = df.set_index("date")["close"].astype(float)
    equity = equity / equity.iloc[0]
    daily_returns = equity.pct_change().dropna()
    drawdown_curve = calc_drawdown(equity)

    total_return = (equity.iloc[-1] / equity.iloc[0] - 1) * 100
    years = len(daily_returns) / 252
    if years > 0 and equity.iloc[0] > 0:
        annualized = ((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1) * 100
    else:
        annualized = 0.0
    annual_vol = float(daily_returns.std() * np.sqrt(252) * 100) if len(daily_returns) else 0.0
    sharpe = ((annualized / 100 - 0.03) / (annual_vol / 100)) if annual_vol > 0 else 0.0
    monthly = daily_returns.resample("ME").apply(lambda x: (1 + x).prod() - 1)
    win_rate = float((monthly > 0).sum() / len(monthly) * 100) if len(monthly) else 0.0

    return BacktestResult(
        strategy_name="SPY 买入持有",
        equity_curve=equity,
        drawdown_curve=drawdown_curve,
        daily_returns=daily_returns,
        total_return=round(float(total_return), 2),
        annualized_return=round(float(annualized), 2),
        max_drawdown=round(float(drawdown_curve.min() * 100), 2),
        sharpe_ratio=round(float(sharpe), 2),
        volatility=round(annual_vol, 2),
        win_rate=round(win_rate, 1),
        num_rebalances=0,
        weight_history=pd.DataFrame(),
    )


def backtest_strategy_rankings(
    dm: DataManager,
    horizon_days: int = 30,
    lookback_days: int = 730,
    sample_interval: int = 30,
) -> Dict:
    """运行全部策略回测 + 在历史采样点评估排名

    Returns:
        Dict:
        - full_results: List[BacktestResult] 全量回测结果（用于 1 年表现总览）
        - sample_results: List[Dict] 每个采样点的排名评估
    """
    all_symbols = dict(STRATEGY_UNIVERSE)
    all_symbols.update(GROWTH_UNIVERSE)

    # 加载数据（多取 400 天给策略回望）
    end_date = datetime.now()
    data_start = (end_date - timedelta(days=lookback_days + 400)).strftime("%Y-%m-%d")
    data_end = end_date.strftime("%Y-%m-%d")

    all_data = {}
    missing = []
    for symbol in all_symbols:
        df = dm.load(symbol, data_start, data_end)
        if not df.empty:
            all_data[symbol] = df
        else:
            missing.append(symbol)

    if not all_data:
        return {
            "full_results": [], "sample_results": [],
            "data_error": "no_data",
            "missing_symbols": missing,
        }
    if "SPY" not in all_data:
        return {
            "full_results": [], "sample_results": [],
            "data_error": "no_spy",
            "missing_symbols": missing,
        }

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

    # 把「SPY 买入持有」塞进对比列表，否则跑赢结论没有对手
    spy_bench = _spy_benchmark_result(all_data["SPY"], data_start, data_end)
    if spy_bench is not None:
        results_list.append(spy_bench)

    progress_bar.progress(0.5, text="分析策略排名...")

    # 构建 SPY 基准净值曲线
    spy_df = all_data["SPY"].copy()
    spy_df = spy_df.sort_values("date").reset_index(drop=True)
    spy_equity = spy_df.set_index("date")["close"]
    spy_equity = spy_equity / spy_equity.iloc[0]  # 归一化

    # 对每个采样点评估
    # 预取 SPY/TLT/CL 数据用于体制检测
    spy_raw = all_data.get("SPY", pd.DataFrame())
    tlt_raw = all_data.get("TLT", pd.DataFrame())
    cl_raw = all_data.get("CL", pd.DataFrame())

    sample_results = []
    for sample_date in sample_dates:
        # 计算该采样点的市场体制
        regime_name = "中性"
        composite = 50.0
        spy_close_slice = spy_raw[spy_raw["date"] <= sample_date]["close"] if not spy_raw.empty else pd.Series()
        if len(spy_close_slice) >= 60:
            rs = calc_regime_score(spy_close_slice)
            composite = rs.get("composite", 50)
            tlt_close_slice = tlt_raw[tlt_raw["date"] <= sample_date]["close"] if not tlt_raw.empty else pd.Series()
            cl_close_slice = cl_raw[cl_raw["date"] <= sample_date]["close"] if not cl_raw.empty else pd.Series()
            if len(tlt_close_slice) >= 60 and len(cl_close_slice) >= 60:
                regime_info = detect_market_regime(spy_close_slice, tlt_close_slice, cl_close_slice)
                regime_name = regime_info.get("regime", "中性")

        rankings = []
        for r in results_list:
            # SPY 只做基准，不进推荐榜
            if "SPY" in r.strategy_name:
                continue
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
        # 「推荐」= 生产策略，不是夏普第一。夏普第一常被低波底仓（风险平价）占走。
        prod_entry = next(
            (x for x in rankings if x["name"] == PRODUCTION_STRATEGY_NAME), None
        )
        recommended = PRODUCTION_STRATEGY_NAME if prod_entry else rankings[0]["name"]
        rec_entry = prod_entry or rankings[0]

        sample_results.append({
            "date": sample_date,
            "regime": regime_name,
            "composite": composite,
            "recommended": recommended,
            "recommended_entry": rec_entry,
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

    # 按评分区间分桶（标签须与 _bucket_hit 查询键一致）
    def bucket(score, bins=(30, 50, 70, 90)):
        labels = ["<30", "30-50", "50-70", "70-90", ">=90"]
        for i, b in enumerate(bins):
            if score < b:
                return labels[i]
        return labels[-1]

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

    # 分析 trend_score 区间对过门槛命中率的影响（生产口径 12M>8%）
    trend_data = agg.get(OPPORTUNITY_TREND_BUY, {}).get("trend_score", {})
    if trend_data:
        low_hit = _bucket_hit(trend_data, "30-50")
        mid_hit = _bucket_hit(trend_data, "50-70")
        high_hit = _bucket_hit(trend_data, "70-90")
        if low_hit is not None and high_hit is not None and high_hit - low_hit > 15:
            suggestions.append({
                "parameter": "过门槛 trend_score 阈值",
                "priority": "高",
                "summary": f"高评分(≥70)命中率 {high_hit:.0f}% 远高于低评分(30-50)的 {low_hit:.0f}%",
                "problem": "过门槛候选里混入了低评分资产，拉低整体命中率",
                "suggestion": "在 12M>8% 之上再加 trend_score≥60 确认，过滤弱趋势",
                "reason": f"评分区间差异 {high_hit - low_hit:.0f}pp，阈值提升可显著提高信号质量",
            })
        elif low_hit is not None and mid_hit is not None and low_hit > mid_hit:
            suggestions.append({
                "parameter": "过门槛 trend_score 阈值",
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

    # 旧·均值回归只做历史对照，不给生产优化建议（不在机会轴）

    priority_order = {"高": 0, "中": 1, "低": 2}
    suggestions.sort(key=lambda s: priority_order.get(s["priority"], 9))
    return suggestions


def _render_hit_rate_explainer(horizon_days: int = 30):
    """白话解释命中率怎么算 — 贴在指标卡上方，不埋进 expander。"""
    horizon_label = next(
        (k for k, v in HORIZON_OPTIONS.items() if v == horizon_days),
        f"{horizon_days} 日",
    )
    st.markdown("**命中率怎么读**")
    cols = st.columns(3)
    rules = [
        (cols[0], "说过门槛（买）", "后来涨了 = 说对", "买对了几次 / 一共说了几次买", "#2e7d32", "#e8f5e9", "#4caf50"),
        (cols[1], "说门槛附近（等）", f"后来没跌超 5% = 说对", "等对了几次 / 一共说了几次等", "#1565c0", "#e3f2fd", "#42a5f5"),
        (cols[2], "说不碰（躲）", "后来跑输 SPY = 说对", "躲对了几次 / 一共说了几次躲", "#c62828", "#ffebee", "#ef5350"),
    ]
    for col, title, rule, formula, color, bg, border in rules:
        with col:
            st.markdown(
                f'<div style="padding:0.75rem 0.9rem;background:{bg};border-radius:0.45rem;'
                f'border-top:3px solid {border};height:100%;">'
                f'<div style="font-weight:700;color:{color};font-size:0.95rem;">{title}</div>'
                f'<div style="font-size:1.05rem;font-weight:700;color:#222;margin:0.3rem 0;">{rule}</div>'
                f'<div style="font-size:0.82rem;color:#666;">{formula}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
    st.caption(
        f"一句话：**说对的比例就是命中率**。每条信号看 {horizon_label} 后的实际表现；"
        "「不碰」的对照物是同期 SPY（大盘），不是绝对涨跌。"
        "随机瞎猜大约 50%——过门槛持续高于 55% 才算真有效。"
    )


def render_opportunity_review(results: List[Dict], horizon_days: int = 30):
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

    # ---- 白话口径（先解释怎么读，再给数字）----
    _render_hit_rate_explainer(horizon_days)

    # ---- 一句话结论 ----
    buy = by_opp.get(OPPORTUNITY_TREND_BUY)
    near = by_opp.get(OPPORTUNITY_TREND_WATCH)
    avoid = by_opp.get(OPPORTUNITY_AVOID)
    buy_hit = (buy["correct"] / buy["total"] * 100) if buy and buy["total"] else None
    avoid_hit = (avoid["correct"] / avoid["total"] * 100) if avoid and avoid["total"] else None
    parts = []
    if buy_hit is not None:
        parts.append(f"说「买」的 {buy_hit:.0f}% 说对了（{buy['total']} 条）")
    if near and near["total"]:
        near_hit = near["correct"] / near["total"] * 100
        parts.append(f"说「等」的 {near_hit:.0f}% 说对了")
    if avoid_hit is not None:
        parts.append(f"说「躲」的 {avoid_hit:.0f}% 说对了（{avoid['total']} 条）")
    body = "　".join(parts)
    if buy_hit is not None and buy_hit >= 55:
        st.success(f"扫描**准**：{body}。买对的比例明显高于瞎猜（50%），有正期望。")
    elif buy_hit is not None and buy_hit < 45:
        st.warning(f"扫描**不准**：{body}。买对的比例低于瞎猜，需收紧门槛或拉长确认。")
    else:
        st.info(f"扫描**一般**：{body}。接近瞎猜水平，参考价值有限。")

    # ---- 3 个主桶指标卡（生产口径）+ 旧均值回归折叠 ----
    card_sub = {
        OPPORTUNITY_TREND_BUY: "买对了几次 / 说了几次买",
        OPPORTUNITY_TREND_WATCH: "等对了几次 / 说了几次等",
        OPPORTUNITY_AVOID: "躲对了几次 / 说了几次躲",
    }
    cols = st.columns(3)
    for col, opp_type in zip(cols, [
        OPPORTUNITY_TREND_BUY, OPPORTUNITY_TREND_WATCH, OPPORTUNITY_AVOID
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
                    f'<div style="font-size:0.82rem;color:#444;">说对 {data["correct"]}/{data["total"]} 条</div>'
                    f'<div style="font-size:0.78rem;color:#666;">{card_sub[opp_type]} · 后来均{ret_text}</div>'
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
                         OPPORTUNITY_AVOID, OPPORTUNITY_MEAN_REVERSION]:
            data = by_opp.get(opp_type)
            if not data or data["total"] == 0:
                continue
            cfg = OPP_CONFIG[opp_type]
            hit = data["correct"] / data["total"] * 100
            avg_ret = sum(data["returns"]) / len(data["returns"])
            regret_rate = data["regret"] / data["total"] * 100 if data["total"] > 0 else 0
            winning = [r for r in data["returns"] if r > 0]
            losing = [r for r in data["returns"] if r < 0]
            avg_win = sum(winning) / len(winning) if winning else 0
            avg_lose = sum(losing) / len(losing) if losing else 0
            pnl_ratio = avg_win / abs(avg_lose) if avg_lose != 0 else float('inf')
            rows.append({
                "类型": cfg["label"],
                "信号数": data["total"],
                "命中率": f"{hit:.0f}%",
                "平均收益": f"+{avg_ret:.1f}%" if avg_ret > 0 else f"{avg_ret:.1f}%",
                "后悔率": f"{regret_rate:.0f}%",
                "盈亏比": f"{pnl_ratio:.2f}" if pnl_ratio != float('inf') else "∞",
            })
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True,
                         column_config={
                             "命中率": st.column_config.TextColumn(help=HIT_RATE),
                             "后悔率": st.column_config.TextColumn(help=REGRET_RATE),
                             "盈亏比": st.column_config.TextColumn(help=PROFIT_LOSS_RATIO),
                         })

    with col_chart:
        st.markdown("**命中率对比**")
        chart_rows = []
        for opp_type in [OPPORTUNITY_TREND_BUY, OPPORTUNITY_TREND_WATCH,
                         OPPORTUNITY_AVOID, OPPORTUNITY_MEAN_REVERSION]:
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

    # ---- 按市场体制分析 ----
    st.markdown("**  按市场体制分析**")
    st.caption("不同市场体制下各机会类型的命中率差异，用于判断策略适用环境")

    by_regime = {}  # {opp_type: {regime: {correct, total, returns}}}
    for r in results:
        regime = r.get("regime", "中性")
        for s in r["scores"]:
            opp = s["opportunity"]
            if opp == OPPORTUNITY_NEUTRAL:
                continue
            if opp not in by_regime:
                by_regime[opp] = {}
            if regime not in by_regime[opp]:
                by_regime[opp][regime] = {"correct": 0, "total": 0, "returns": []}
            by_regime[opp][regime]["total"] += 1
            if s["forward_return"] is not None:
                by_regime[opp][regime]["returns"].append(s["forward_return"])
                if s["correct"]:
                    by_regime[opp][regime]["correct"] += 1

    all_regimes = ["风险偏好", "避险", "危机", "滞胀担忧", "震荡", "中性"]
    # 只显示有数据的体制
    regime_order = [r for r in all_regimes if any(
        by_regime.get(opp, {}).get(r, {}).get("total", 0) > 0
        for opp in by_regime
    )]
    if not regime_order:
        st.info("暂无体制数据")
        return

    regime_chart, regime_table = st.columns([1, 1])

    with regime_chart:
        fig = go.Figure()
        for opp_type in [OPPORTUNITY_TREND_BUY, OPPORTUNITY_TREND_WATCH,
                         OPPORTUNITY_AVOID, OPPORTUNITY_MEAN_REVERSION]:
            cfg = OPP_CONFIG[opp_type]
            hit_rates = []
            for reg in regime_order:
                d = by_regime.get(opp_type, {}).get(reg)
                if d and d["total"] > 0:
                    hit_rates.append(d["correct"] / d["total"] * 100)
                else:
                    hit_rates.append(None)
            fig.add_trace(go.Bar(
                x=regime_order, y=hit_rates, name=cfg["label"],
                marker_color=cfg["color"], opacity=0.8,
            ))
        fig.add_hline(y=50, line_dash="dash", line_color="#999", line_width=1)
        fig.update_layout(
            barmode="group", yaxis_title="命中率 (%)",
            yaxis=dict(range=[0, 100]), height=350,
            template="plotly_white",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )
        st.plotly_chart(fig, use_container_width=True)

    with regime_table:
        st.markdown("**命中率明细**")
        table_rows = []
        for opp_type in [OPPORTUNITY_TREND_BUY, OPPORTUNITY_TREND_WATCH,
                         OPPORTUNITY_AVOID, OPPORTUNITY_MEAN_REVERSION]:
            cfg = OPP_CONFIG[opp_type]
            row = {"类型": cfg["label"]}
            for reg in regime_order:
                d = by_regime.get(opp_type, {}).get(reg)
                if d and d["total"] > 0:
                    hit = d["correct"] / d["total"] * 100
                    row[reg] = f"{hit:.0f}% ({d['total']}条)"
                else:
                    row[reg] = "—"
            table_rows.append(row)
        if table_rows:
            st.dataframe(pd.DataFrame(table_rows), use_container_width=True, hide_index=True)

    # 自动高亮最显著的体制差异
    insights = []
    for opp_type in [OPPORTUNITY_TREND_BUY, OPPORTUNITY_MEAN_REVERSION]:
        cfg = OPP_CONFIG[opp_type]
        regime_hits = {}
        for reg in regime_order:
            d = by_regime.get(opp_type, {}).get(reg)
            if d and d["total"] >= 3:
                regime_hits[reg] = d["correct"] / d["total"] * 100
        if len(regime_hits) >= 2:
            best_reg = max(regime_hits, key=regime_hits.get)
            worst_reg = min(regime_hits, key=regime_hits.get)
            spread = regime_hits[best_reg] - regime_hits[worst_reg]
            if spread > 15:
                insights.append(
                    f"{cfg['label']}在{best_reg}体制下命中率最高({regime_hits[best_reg]:.0f}%)，"
                    f"在{worst_reg}体制下最低({regime_hits[worst_reg]:.0f}%)，差距{spread:.0f}pp"
                )
    if insights:
        st.info(" · ".join(insights))

    st.divider()

    # ---- 评分区间命中率分析 ----
    st.markdown("**  评分区间命中率分析**")
    st.caption("不同评分区间的信号质量差异，用于判断阈值是否需要调整")

    def bucket(score, bins=(30, 50, 70, 90)):
        labels = ["<30", "30-50", "50-70", "70-90", ">=90"]
        for i, b in enumerate(bins):
            if score < b:
                return labels[i]
        return labels[-1]

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
        all_symbols = STRATEGY_UNIVERSE
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
    with st.expander("  指标含义（补充）", expanded=False):
        st.markdown(f"""
**命中率（说对率）** — 说对条数 ÷ 总条数。随机瞎猜约 50%。

| 机会类型 | 说什么 | 什么算说对 | 通俗理解 |
|---------|--------|-----------|---------|
| 过门槛 | 买 | 前向收益 > 0 | 买对了 |
| 门槛附近 | 等 | 前向收益 > −5% | 等对了（没踩大坑） |
| 不碰 | 躲 | 前向收益 < SPY | 躲对了（跑输大盘） |
| 旧·均值回归 | （已退役） | 前向收益 > 0 | 历史口径，不在生产机会轴 |

**后悔率** — 说过门槛/等，但后来亏超 5% 的比例（说买却踩坑）。越低越好。

**盈亏比** — 平均盈利 ÷ 平均亏损绝对值。>1 盈大于亏。

**前向收益** — 信号日之后的实际涨幅，窗口 = 左侧选定的评估周期。
        """)


# ============================================================
# 策略回测复盘渲染
# ============================================================

def render_strategy_review(data: Dict, horizon_days: int = 30):
    """渲染策略回测复盘结果"""
    st.subheader("  策略回测复盘")
    st.caption("P3 推荐的策略，后来跑赢 SPY（标普500 买入持有）了吗？")

    full_results = data.get("full_results", [])
    sample_results = data.get("sample_results", [])
    data_error = data.get("data_error")

    if data_error == "no_spy":
        st.warning("缺少 SPY 历史价格，无法做跑赢对比。请先 `python scripts/daily_sync.py`。")
        return
    if data_error == "no_data":
        st.warning("策略宇宙行情未入库。请先 `python scripts/daily_sync.py`。")
        return
    if not full_results:
        st.warning("数据不足，无法进行策略回测复盘")
        return

    # ---- 一句话结论（生产策略 vs SPY）----
    spy_row = next((r for r in full_results if "SPY" in r.strategy_name), None)
    live = [r for r in full_results if "SPY" not in r.strategy_name]
    live_by_ret = sorted(live, key=lambda r: r.total_return, reverse=True)
    prod = next((r for r in live if r.strategy_name == PRODUCTION_STRATEGY_NAME), None)

    if prod is not None and spy_row is not None:
        excess = prod.total_return - spy_row.total_return
        if excess > 0:
            beat = (
                f"生产策略 **{prod.strategy_name}** 近 1 年 **{prod.total_return:+.1f}%**"
                f"（最大回撤 {prod.max_drawdown:.1f}%），"
                f"比 **SPY（标普500 买入持有，{spy_row.total_return:+.1f}%）**"
                f"**多赚 {excess:.1f} 个百分点**。"
            )
        elif excess < 0:
            beat = (
                f"生产策略 **{prod.strategy_name}** 近 1 年 **{prod.total_return:+.1f}%**"
                f"（最大回撤 {prod.max_drawdown:.1f}%），"
                f"比 **SPY（{spy_row.total_return:+.1f}%）**"
                f"**少赚 {abs(excess):.1f} 个百分点**。"
            )
        else:
            beat = (
                f"生产策略 **{prod.strategy_name}** 近 1 年 **{prod.total_return:+.1f}%**，"
                f"与 **SPY（{spy_row.total_return:+.1f}%）打平**。"
            )
        # 附注：若收益榜首不是生产策略，说明角色差异，别误读成「该换策略」
        if live_by_ret and live_by_ret[0].strategy_name != PRODUCTION_STRATEGY_NAME:
            top_ret = live_by_ret[0]
            role = _strategy_role(top_ret.strategy_name)
            beat += (
                f" 全场收益最高是「{top_ret.strategy_name}」"
                f"（{top_ret.total_return:+.1f}% / 回撤 {top_ret.max_drawdown:.1f}%，角色：{role}）"
                f"——角色不同，不直接替代生产策略。"
            )
        st.success(beat)
    elif prod is not None:
        st.success(
            f"生产策略 **{prod.strategy_name}** 近 1 年 **{prod.total_return:+.1f}%**"
            f"（最大回撤 {prod.max_drawdown:.1f}%）。SPY 基准缺失，无法比较超额。"
        )
    else:
        st.info("未找到生产策略回测结果，暂无跑赢结论。")

    # ============================================================
    # 全策略 1 年表现总览
    # ============================================================
    st.markdown("**  全策略近 1 年表现总览**")
    st.caption(
        "默认按总收益排序。**风险平价/低相关是底仓工具**（回撤小、收益弹性小），"
        "夏普容易排前面，但不能替代生产策略「成长动量·个股」。"
    )

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
            "角色": _strategy_role(r.strategy_name),
            "总收益%": r.total_return,
            "年化%": r.annualized_return,
            "最大回撤%": r.max_drawdown,
            "Sharpe": r.sharpe_ratio,
            "波动率%": r.volatility,
            "月胜率%": r.win_rate,
            "再平衡次数": r.num_rebalances,
        })

    perf_df = pd.DataFrame(perf_rows)
    # 按总收益排：夏普偏爱低波底仓，不适合当「谁更好」的主排序
    perf_df = perf_df.sort_values("总收益%", ascending=False).reset_index(drop=True)

    # 格式化显示
    display_df = perf_df.copy()
    for col in ["总收益%", "年化%", "波动率%"]:
        display_df[col] = display_df[col].apply(lambda x: f"+{x:.1f}%" if x > 0 else f"{x:.1f}%")
    display_df["最大回撤%"] = display_df["最大回撤%"].apply(lambda x: f"{x:.1f}%")
    display_df["Sharpe"] = display_df["Sharpe"].apply(lambda x: f"{x:.2f}")
    display_df["月胜率%"] = display_df["月胜率%"].apply(lambda x: f"{x:.0f}%")

    st.dataframe(display_df, use_container_width=True, hide_index=True,
                 column_config={
                     "总收益%": st.column_config.TextColumn(help=TOTAL_RETURN),
                     "年化%": st.column_config.TextColumn(help=ANNUAL_RETURN),
                     "最大回撤%": st.column_config.TextColumn(help=MAX_DRAWDOWN),
                     "Sharpe": st.column_config.TextColumn(help=SHARPE_RATIO),
                     "波动率%": st.column_config.TextColumn(help=VOLATILITY),
                     "月胜率%": st.column_config.TextColumn(help=WIN_RATE),
                 })

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

    st.markdown("**  生产策略跑赢率评估**")
    st.caption(
        f"「推荐」= 生产策略「{PRODUCTION_STRATEGY_NAME}」，不是夏普榜第一。"
        "夏普 = 收益/波动，低波底仓（风险平价）会系统性霸榜，不代表该当主策略。"
        "每个采样时点看生产策略往后一个评估周期有没有跑赢 SPY。"
    )

    # 汇总统计（评估对象固定为生产策略）
    total_evaluated = 0
    total_beat = 0
    all_excess = []

    for r in sample_results:
        top = r.get("recommended_entry") or (r["rankings"][0] if r["rankings"] else None)
        if top and top["beat_spy"] is not None:
            total_evaluated += 1
            if top["beat_spy"]:
                total_beat += 1
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
            f'border-radius:0.5rem;border-bottom:3px solid #42a5f5;" title="{STRATEGY_HIT_RATE}">'
            f'<div style="font-size:0.85rem;color:#1565c0;">跑赢 SPY 的比例 ⓘ</div>'
            f'<div style="font-size:2rem;font-weight:bold;color:{hit_color};">{hit_rate:.0f}%</div>'
            f'<div style="font-size:0.8rem;color:#666;">{total_beat}/{total_evaluated} 个推荐时点跑赢</div>'
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
        st.markdown(
            f'<div style="text-align:center;padding:0.8rem;background:#f3e5f5;'
            f'border-radius:0.5rem;border-bottom:3px solid #ab47bc;">'
            f'<div style="font-size:0.85rem;color:#6a1b9a;">评估对象（固定）</div>'
            f'<div style="font-size:1.05rem;font-weight:bold;color:#6a1b9a;">{PRODUCTION_STRATEGY_NAME}</div>'
            f'<div style="font-size:0.8rem;color:#666;">生产策略，非夏普轮换</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    with c4:
        st.metric("采样点数", len(sample_results))

    # 推荐策略 vs SPY
    dates = [r["date"] for r in sample_results]
    rec_returns = [
        (r.get("recommended_entry") or (r["rankings"][0] if r["rankings"] else {})).get("forward_return")
        for r in sample_results
    ]
    spy_returns = [
        (r.get("recommended_entry") or (r["rankings"][0] if r["rankings"] else {})).get("spy_forward_return")
        for r in sample_results
    ]

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
            top = r.get("recommended_entry") or (r["rankings"][0] if r["rankings"] else None)
            if not top:
                continue
            fwd = top["forward_return"]
            spy_fwd = top["spy_forward_return"]
            excess = (fwd - spy_fwd) if fwd is not None and spy_fwd is not None else None
            rows.append({
                "日期": r["date"],
                "体制": r.get("regime", "中性"),
                "推荐策略": r["recommended"],
                "Sharpe估计": top["sharpe_est"],
                "策略收益": f"+{fwd:.1f}%" if fwd and fwd > 0 else f"{fwd:.1f}%" if fwd else "N/A",
                "SPY收益": f"+{spy_fwd:.1f}%" if spy_fwd and spy_fwd > 0 else f"{spy_fwd:.1f}%" if spy_fwd else "N/A",
                "超额": f"+{excess:.1f}%" if excess and excess > 0 else f"{excess:.1f}%" if excess else "N/A",
                "跑赢": "✅" if top["beat_spy"] else "❌" if top["beat_spy"] is False else "—",
            })
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.divider()

    # ============================================================
    # 按体制分组分析
    # ============================================================
    st.markdown("**  按体制分组分析**")
    st.caption("不同市场体制下推荐策略的跑赢率和超额收益")

    by_regime = {}
    for r in sample_results:
        regime = r.get("regime", "中性")
        if regime not in by_regime:
            by_regime[regime] = {"recommended_counts": {}, "beat_count": 0, "total": 0, "excess_returns": []}

        rec = r["recommended"]
        by_regime[regime]["recommended_counts"][rec] = by_regime[regime]["recommended_counts"].get(rec, 0) + 1

        top = r.get("recommended_entry") or (r["rankings"][0] if r["rankings"] else None)
        if top and top["beat_spy"] is not None:
            by_regime[regime]["total"] += 1
            if top["beat_spy"]:
                by_regime[regime]["beat_count"] += 1
        if top and top["forward_return"] is not None and top["spy_forward_return"] is not None:
            by_regime[regime]["excess_returns"].append(top["forward_return"] - top["spy_forward_return"])

    if by_regime:
        regime_rows = []
        for regime, data in sorted(by_regime.items()):
            beat_rate = data["beat_count"] / data["total"] * 100 if data["total"] > 0 else 0
            avg_excess = sum(data["excess_returns"]) / len(data["excess_returns"]) if data["excess_returns"] else 0
            top_strategy = max(data["recommended_counts"].items(), key=lambda x: x[1])[0] if data["recommended_counts"] else "N/A"
            regime_rows.append({
                "体制": regime,
                "最常推荐": top_strategy,
                "推荐次数": data["recommended_counts"].get(top_strategy, 0),
                "跑赢率": f"{beat_rate:.0f}%",
                "平均超额": f"{avg_excess:+.1f}%",
                "样本数": data["total"],
            })

        regime_chart_col, regime_table_col = st.columns([1, 1])

        with regime_chart_col:
            fig = go.Figure(go.Bar(
                x=[r["体制"] for r in regime_rows],
                y=[float(r["跑赢率"].rstrip("%")) for r in regime_rows],
                marker_color=[REGIME_COLORS.get(r["体制"], "#999") for r in regime_rows],
                text=[f"{r['跑赢率']}<br>{r['最常推荐']}" for r in regime_rows],
                textposition="outside",
                textfont=dict(size=10),
            ))
            fig.add_hline(y=50, line_dash="dash", line_color="#999", line_width=1)
            fig.update_layout(
                yaxis_title="跑赢 SPY 率 (%)",
                yaxis=dict(range=[0, 105]),
                height=300,
                template="plotly_white",
                showlegend=False,
            )
            st.plotly_chart(fig, use_container_width=True)

        with regime_table_col:
            st.dataframe(pd.DataFrame(regime_rows), use_container_width=True, hide_index=True,
                         column_config={
                             "跑赢率": st.column_config.TextColumn(help=STRATEGY_HIT_RATE),
                             "平均超额": st.column_config.TextColumn(help="推荐策略相对 SPY 的平均超额收益"),
                         })

        # 高亮最佳/最差体制
        if len(regime_rows) >= 2:
            best = max(regime_rows, key=lambda r: float(r["跑赢率"].rstrip("%")))
            worst = min(regime_rows, key=lambda r: float(r["跑赢率"].rstrip("%")))
            st.info(
                f"策略系统在**{best['体制']}**体制下表现最好（跑赢率 {best['跑赢率']}），"
                f"在**{worst['体制']}**体制下表现最差（跑赢率 {worst['跑赢率']}）。"
            )
    else:
        st.info("采样点不足，无法进行体制分组分析")


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

        # ---- 成长动量·个股（生产）----
        if "成长动量" in name:
            if max_dd < -35:
                suggestions.append({
                    "strategy": name,
                    "priority": "中",
                    "summary": "生产策略回撤接近 −40% 预算，关注是否需要人工干预",
                    "problem": f"最大回撤 {max_dd:.1f}%，接近组合 −40% 回撤预算",
                    "current": "3M 动量 top12 满仓等权，单票 20%，无自动刹车",
                    "suggestion": "回撤贴近 −40% 时人工评估是否降仓；不要加自动 A2（会死锁空仓）",
                    "reason": "个股动量天然 −30%+ 回撤，自动刹车已证伪；−40% 是人工红线",
                })

        # ---- 凯利袖珍组合 ----
        if "70/30" in name:
            if max_dd < -12:
                suggestions.append({
                    "strategy": name,
                    "priority": "中",
                    "summary": "对冲/CTA 袖珍未压住回撤",
                    "problem": f"最大回撤 {max_dd:.1f}%，袖珍配比可能不够",
                    "current": "70% 主仓 + 30% CTA/反脆弱袖珍",
                    "suggestion": "提高袖珍到 35%，或袖珍内加吊灯止损",
                    "reason": "袖珍的作用是压回撤，压不住就要加码",
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

def _preflight_data(dm: DataManager) -> bool:
    """开跑前检查 SPY/核心数据是否入库，缺了直接给修复命令。"""
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    spy = dm.load("SPY", start, end)
    if spy.empty:
        st.error(
            "**本地数据库没有 SPY 近期价格**，P4 无法评估「跑赢 SPY 了吗」。\n\n"
            "修复：\n"
            "1. 终端执行 `python scripts/daily_sync.py`（拉全量行情+宏观）\n"
            "2. 或打开 **P3 策略回测** → 侧边栏「预取回测数据」\n"
            "3. 回到本页刷新"
        )
        return False

    universe = STRATEGY_UNIVERSE
    loaded = 0
    for sym in universe:
        if not dm.load(sym, start, end).empty:
            loaded += 1
    if loaded < max(3, len(universe) // 4):
        st.error(
            f"**行情覆盖不足**：策略宇宙 {len(universe)} 个标的里近 30 天只有 {loaded} 个有数据。\n\n"
            "修复：执行 `python scripts/daily_sync.py` 后刷新本页。"
        )
        return False
    return True


def _render_review_headline(opp_results: List[Dict], strat_results: Dict,
                            horizon_days: int = 30):
    """复盘总结论 — 直接回答两个问题，让没接触过的人一眼看懂。"""
    horizon_label = next(
        (k for k, v in HORIZON_OPTIONS.items() if v == horizon_days),
        f"{horizon_days} 日",
    )

    # ---- Q1: 扫描准不准？ ----
    by_opp = {}
    for r in opp_results or []:
        for s in r.get("scores", []):
            opp = s.get("opportunity")
            if opp == OPPORTUNITY_NEUTRAL:
                continue
            by_opp.setdefault(opp, {"correct": 0, "total": 0})
            by_opp[opp]["total"] += 1
            if s.get("forward_return") is not None and s.get("correct"):
                by_opp[opp]["correct"] += 1

    buy = by_opp.get(OPPORTUNITY_TREND_BUY)
    avoid = by_opp.get(OPPORTUNITY_AVOID)
    if buy and buy["total"]:
        buy_hit = buy["correct"] / buy["total"] * 100
        scan_verdict = "准" if buy_hit >= 55 else ("不准" if buy_hit < 45 else "一般")
        scan_color = "#2e7d32" if buy_hit >= 55 else ("#c62828" if buy_hit < 45 else "#e65100")
        scan_bg = "#e8f5e9" if buy_hit >= 55 else ("#ffebee" if buy_hit < 45 else "#fff3e0")
        scan_border = "#4caf50" if buy_hit >= 55 else ("#ef5350" if buy_hit < 45 else "#ff9800")
        scan_detail = (
            f'说「买」的后来 {buy_hit:.0f}% 真涨了'
            f'（{buy["total"]} 条信号，{horizon_label}窗口）'
        )
        if avoid and avoid["total"]:
            avoid_hit = avoid["correct"] / avoid["total"] * 100
            scan_detail += f'；说「别碰」的 {avoid_hit:.0f}% 真跑输了大盘'
    else:
        scan_verdict, scan_color, scan_bg, scan_border = "样本不足", "#666", "#f5f5f5", "#bbb"
        scan_detail = "历史信号太少，暂无法判断扫描准不准"

    # ---- Q2: 推荐跑赢了吗？和谁比、赢多少 ----
    data_error = (strat_results or {}).get("data_error")
    full = (strat_results or {}).get("full_results") or []
    live = [r for r in full if "SPY" not in r.strategy_name]
    spy_row = next((r for r in full if "SPY" in r.strategy_name), None)
    if data_error == "no_spy":
        strat_verdict, strat_color, strat_bg, strat_border = "缺 SPY 数据", "#c62828", "#ffebee", "#ef5350"
        strat_detail = (
            "本地库里没有 SPY 历史价格，无法比较。"
            "请先执行 <b>python scripts/daily_sync.py</b>，"
            "或在 P3 侧边栏点「预取回测数据」。"
        )
    elif data_error == "no_data":
        strat_verdict, strat_color, strat_bg, strat_border = "缺行情数据", "#c62828", "#ffebee", "#ef5350"
        strat_detail = (
            "策略宇宙的行情还没入库。"
            "请先执行 <b>python scripts/daily_sync.py</b>。"
        )
    elif live and spy_row is not None:
        prod = next((r for r in live if r.strategy_name == PRODUCTION_STRATEGY_NAME), None)
        focus = prod or max(live, key=lambda r: r.sharpe_ratio)
        label = "生产策略" if prod is not None else "收益最高策略"
        excess = focus.total_return - spy_row.total_return
        if excess > 0:
            strat_verdict = "跑赢了"
            strat_color, strat_bg, strat_border = "#2e7d32", "#e8f5e9", "#4caf50"
            win_word = "多赚"
        elif excess < 0:
            strat_verdict = "没跑赢"
            strat_color, strat_bg, strat_border = "#c62828", "#ffebee", "#ef5350"
            win_word = "少赚"
        else:
            strat_verdict = "打平"
            strat_color, strat_bg, strat_border = "#e65100", "#fff3e0", "#ff9800"
            win_word = "持平"
        strat_detail = (
            f'和 <b>SPY（标普500 买入持有）</b>比：'
            f'{label}「{focus.strategy_name}」近 1 年 '
            f'<b>{focus.total_return:+.1f}%</b> vs SPY {spy_row.total_return:+.1f}%，'
            f'<b>{win_word} {excess:+.1f} 个百分点</b>'
        )
    else:
        strat_verdict, strat_color, strat_bg, strat_border = "样本不足", "#666", "#f5f5f5", "#bbb"
        strat_detail = (
            "策略回测结果为空，或 SPY 基准行缺失，无法比较。"
            "先跑 daily_sync 补数据；若数据齐全仍看到本条，是代码问题。"
        )

    c1, c2 = st.columns(2)
    with c1:
        st.markdown(
            f'<div style="padding:0.75rem 1rem;background:{scan_bg};border-left:4px solid {scan_border};'
            f'border-radius:0 0.4rem 0.4rem 0;">'
            f'<div style="font-size:0.8rem;color:#666;">P2 扫描准不准</div>'
            f'<div style="font-size:1.25rem;font-weight:700;color:{scan_color};margin-top:0.15rem;">'
            f'{scan_verdict}</div>'
            f'<div style="font-size:0.92rem;color:#333;margin-top:0.35rem;line-height:1.45;">{scan_detail}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            f'<div style="padding:0.75rem 1rem;background:{strat_bg};border-left:4px solid {strat_border};'
            f'border-radius:0 0.4rem 0.4rem 0;">'
            f'<div style="font-size:0.8rem;color:#666;">P3 推荐跑赢了吗（vs SPY）</div>'
            f'<div style="font-size:1.25rem;font-weight:700;color:{strat_color};margin-top:0.15rem;">'
            f'{strat_verdict}</div>'
            f'<div style="font-size:0.92rem;color:#333;margin-top:0.35rem;line-height:1.45;">{strat_detail}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    st.caption(
        "结论在前，明细在后。扫描：过门槛 = 12M>8%（门槛8% 口径），"
        f"「说对」= {horizon_label}内涨/没跌超5%/跑输大盘。"
        "策略：比较基准固定为 SPY 标普500 买入持有。"
    )


def main():
    st.title("  复盘回顾")
    st.caption("双轨复盘：机会扫描分类准确性 + 策略推荐跑赢率 · 结论先行")

    dm = get_data_manager()
    horizon_days = render_sidebar()

    # 数据预检：缺 SPY/行情时先拦住，给修复命令，不空跑
    if not _preflight_data(dm):
        st.stop()

    # 先算两轨结果，才能给出总结论
    with st.spinner("运行机会扫描回测..."):
        opp_results = backtest_opportunities(dm, horizon_days=horizon_days)
    with st.spinner("运行策略回测复盘..."):
        strat_results = backtest_strategy_rankings(dm, horizon_days=horizon_days)

    # ============================================================
    # 区块 0: 复盘总结论
    # ============================================================
    _render_review_headline(opp_results, strat_results, horizon_days)

    st.divider()

    # 当前市场体制与策略推荐在 P2 顶部 / 主页，P4 只做历史复盘，不重复展示

    # ============================================================
    # 区块 1: 机会扫描复盘
    # ============================================================
    render_opportunity_review(opp_results, horizon_days)

    st.divider()

    # ============================================================
    # 区块 2: 策略回测复盘
    # ============================================================
    render_strategy_review(strat_results, horizon_days)


if __name__ == "__main__":
    main()
