"""P3: 策略回测（策略发现）

对比多种资产配置策略的历史表现，发现最优策略组合。
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List

import streamlit as st
import pandas as pd

# 添加项目根目录到 path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.data import DataManager, US_SYMBOLS, CN_SYMBOLS, GLOBAL_SYMBOLS
from core.strategy.strategies import (
    TrendFollowingStrategy,
    RiskParityStrategy,
    MeanReversionStrategy,
    MinCorrelationStrategy,
    DualMomentumStrategy,
    MomentumVolFilterStrategy,
    DrawdownControlStrategy,
    AntifragileStrategy,
    GEMRegimeOverlayStrategy,
    TailRiskParityStrategy,
    DrawdownConstraintStrategy,
    STRATEGY_CATEGORIES,
)
from core.backtest import BacktestEngine, BacktestResult
from app.components.charts import create_multi_line_chart, create_drawdown_chart

# 页面配置
st.set_page_config(
    page_title="策略回测 - Market Pulse",
    page_icon=" ",
    layout="wide",
)

# 策略注册表（按分类排序）
STRATEGY_REGISTRY = {
    "趋势跟踪策略": TrendFollowingStrategy,
    "双动量策略": DualMomentumStrategy,
    "动量+波动率过滤": MomentumVolFilterStrategy,
    "回撤控制策略": DrawdownControlStrategy,
    "均值回归策略": MeanReversionStrategy,
    "风险平价策略": RiskParityStrategy,
    "低相关性组合策略": MinCorrelationStrategy,
    "GEM宏观配置": GEMRegimeOverlayStrategy,
    "尾部风险平价": TailRiskParityStrategy,
    "回撤约束优化": DrawdownConstraintStrategy,
    "反脆弱策略": AntifragileStrategy,
}

# 按分类分组的策略顺序
CATEGORY_ORDER = ["趋势动量", "均值回归", "风险配置", "反脆弱"]


def get_data_manager():
    return DataManager()


def load_all_assets(dm: DataManager, start_date: str, end_date: str) -> Dict[str, pd.DataFrame]:
    """加载全部资产数据"""


    all_symbols = {**US_SYMBOLS, **CN_SYMBOLS, **GLOBAL_SYMBOLS}
    data = {}

    for symbol in all_symbols:
        df = dm.load(symbol, start_date, end_date)
        if not df.empty:
            data[symbol] = df

    return data


def run_backtests(
    strategy_names: List[str],
    start_date: str,
    end_date: str,
    risk_free_rate: float,
    commission_rate: float,
) -> List[BacktestResult]:
    """运行回测"""
    dm = get_data_manager()

    # 预取：确保数据库覆盖回测区间 + 预热期
    # 策略需要 ~252 个交易日回望，用 370 天日历天确保覆盖
    warmup_calendar_days = 370
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")

    # 数据加载起始 = 用户起始 - warmup（给策略足够的回望数据）
    load_start = (start_dt - timedelta(days=warmup_calendar_days)).strftime("%Y-%m-%d")
    days_needed = (end_dt - start_dt).days + warmup_calendar_days + 30
    dm.prefetch_for_backtest(days_needed)

    # 加载数据
    all_data = load_all_assets(dm, start_date=load_start, end_date=end_date)

    if not all_data:
        return []

    # 创建策略实例
    strategies = [STRATEGY_REGISTRY[name]() for name in strategy_names]

    # 运行回测：warmup=0，页面已通过 load_start 提供足够的历史数据
    engine = BacktestEngine(
        risk_free_rate=risk_free_rate,
        commission_rate=commission_rate,
        warmup_days=0,
    )
    results = engine.run_comparison(strategies, all_data, start_date, end_date)

    return results


def render_equity_chart(results: List[BacktestResult]):
    """渲染收益曲线对比图"""
    equity_data = {}
    for r in results:
        if r.equity_curve.empty:
            continue
        df = pd.DataFrame({
            "date": r.equity_curve.index,
            "close": r.equity_curve.values,
        })
        equity_data[r.strategy_name] = df

    if equity_data:
        fig = create_multi_line_chart(equity_data, title="策略收益曲线对比", normalize=False)
        st.plotly_chart(fig, use_container_width=True)


def render_drawdown_chart(results: List[BacktestResult]):
    """渲染回撤对比图"""
    dd_data = {}
    for r in results:
        if r.drawdown_curve.empty:
            continue
        dd_data[r.strategy_name] = r.drawdown_curve

    if dd_data:
        fig = create_drawdown_chart(dd_data, title="策略回撤对比")
        st.plotly_chart(fig, use_container_width=True)


def render_metrics_table(results: List[BacktestResult]):
    """渲染指标对比表格"""
    rows = []
    for r in results:
        strategy_cls = STRATEGY_REGISTRY.get(r.strategy_name)
        cat_label = ""
        if strategy_cls and strategy_cls.category in STRATEGY_CATEGORIES:
            cat_label = STRATEGY_CATEGORIES[strategy_cls.category]["label"]
        rows.append({
            "分类": cat_label,
            "策略": r.strategy_name,
            "年化收益": f"{r.annualized_return:+.2f}%",
            "总收益": f"{r.total_return:+.2f}%",
            "最大回撤": f"{r.max_drawdown:.2f}%",
            "夏普比率": f"{r.sharpe_ratio:.2f}",
            "年化波动率": f"{r.volatility:.2f}%",
            "月度胜率": f"{r.win_rate:.1f}%",
            "再平衡次数": r.num_rebalances,
        })

    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)


def render_strategy_card(result: BacktestResult):
    """渲染单个策略详情卡片"""
    strategy_cls = STRATEGY_REGISTRY.get(result.strategy_name)
    cat_label = ""
    if strategy_cls and strategy_cls.category in STRATEGY_CATEGORIES:
        cat_label = STRATEGY_CATEGORIES[strategy_cls.category]["label"]

    # 策略名称 + 分类标签（大字）
    st.markdown(f"### {result.strategy_name}")
    if cat_label:
        st.caption(cat_label)

    # 策略描述
    if strategy_cls:
        st.caption(strategy_cls.description)

    # 反脆弱策略：显示对冲参数
    if strategy_cls and hasattr(strategy_cls, "hedge_monthly_cost"):
        cost_pct = strategy_cls.hedge_monthly_cost * 100
        otm_pct = strategy_cls.hedge_otm_threshold * 100
        st.caption(
            f"对冲参数: 权利金 {cost_pct:.1f}%/月 · OTM {otm_pct:.0f}% · 杠杆 {strategy_cls.hedge_leverage:.0f}x "
            f"(固定模拟，非真实期权数据)"
        )

    st.divider()

    # 核心指标 — 用 HTML 控制字号
    metrics_html = f"""
    <div style="display:flex; gap:24px; flex-wrap:wrap; margin:8px 0;">
        <div style="flex:1; min-width:120px;">
            <div style="font-size:13px; color:#888;">年化收益</div>
            <div style="font-size:18px; font-weight:600; color:{'#2ecc71' if result.annualized_return >= 0 else '#e74c3c'};">
                {result.annualized_return:+.2f}%
            </div>
        </div>
        <div style="flex:1; min-width:120px;">
            <div style="font-size:13px; color:#888;">最大回撤</div>
            <div style="font-size:18px; font-weight:600; color:#e74c3c;">
                {result.max_drawdown:.2f}%
            </div>
        </div>
        <div style="flex:1; min-width:120px;">
            <div style="font-size:13px; color:#888;">夏普比率</div>
            <div style="font-size:18px; font-weight:600;">{result.sharpe_ratio:.2f}</div>
        </div>
        <div style="flex:1; min-width:120px;">
            <div style="font-size:13px; color:#888;">总收益</div>
            <div style="font-size:18px; font-weight:600; color:{'#2ecc71' if result.total_return >= 0 else '#e74c3c'};">
                {result.total_return:+.2f}%
            </div>
        </div>
        <div style="flex:1; min-width:120px;">
            <div style="font-size:13px; color:#888;">年化波动率</div>
            <div style="font-size:18px; font-weight:600;">{result.volatility:.2f}%</div>
        </div>
        <div style="flex:1; min-width:120px;">
            <div style="font-size:13px; color:#888;">月度胜率</div>
            <div style="font-size:18px; font-weight:600;">{result.win_rate:.1f}%</div>
        </div>
    </div>
    """
    st.markdown(metrics_html, unsafe_allow_html=True)

    # 权重历史
    if not result.weight_history.empty:
        with st.expander("查看持仓历史"):
            display_df = result.weight_history.copy()
            weight_cols = [c for c in display_df.columns if c != "date"]
            for col in weight_cols:
                display_df[col] = display_df[col].apply(
                    lambda x: f"{x:.1%}" if pd.notna(x) and x > 0 else ""
                )
            display_df["date"] = display_df["date"].dt.strftime("%Y-%m-%d")
            st.dataframe(display_df, use_container_width=True, hide_index=True)


def main():
    st.title("  策略回测")
    st.caption("策略发现与历史表现对比")

    dm = get_data_manager()

    # 侧边栏：回测参数
    with st.sidebar:
        st.subheader("回测参数")

        col_start, col_end = st.columns(2)
        with col_start:
            backtest_start = st.date_input(
                "起始日期",
                value=datetime(2020, 1, 1),
                min_value=datetime(2020, 1, 1),
                max_value=datetime.now(),
            )
        with col_end:
            backtest_end = st.date_input(
                "结束日期",
                value=datetime.now(),
                min_value=backtest_start,
                max_value=datetime.now(),
            )
        days_option = (backtest_end - backtest_start).days

        risk_free_rate = st.number_input(
            "无风险利率 (%)",
            min_value=0.0,
            max_value=10.0,
            value=3.0,
            step=0.5,
        ) / 100

        commission_rate = st.number_input(
            "交易佣金 (%)",
            min_value=0.0,
            max_value=1.0,
            value=0.1,
            step=0.05,
        ) / 100

        st.divider()

        st.subheader("策略选择")
        selected_strategies = []
        for cat_key in CATEGORY_ORDER:
            cat = STRATEGY_CATEGORIES[cat_key]
            st.markdown(f"**{cat['label']}** · {cat['subtitle']}")
            # 找出属于该分类的策略
            cat_strategies = [
                name for name, cls in STRATEGY_REGISTRY.items()
                if cls.category == cat_key
            ]
            for name in cat_strategies:
                if st.checkbox(name, value=True, key=f"cb_{name}"):
                    selected_strategies.append(name)
            st.caption(cat["description"])
            # 反脆弱策略数据来源提示
            if cat_key == "反脆弱":
                st.warning("期权权利金采用固定 0.5%/月 模拟，实际费用受 IV/期限等影响。真实回测需接入付费期权数据源（ORATS/CBOE 等）。", icon="⚠️")
            st.divider()

        run_clicked = st.button("  运行回测", use_container_width=True, type="primary")

        # 数据诊断
        with st.expander("  数据诊断"):
            stats = dm.get_stats()
            if stats.empty:
                st.warning("数据库为空，请先获取数据")
            else:
                for _, row in stats.iterrows():
                    st.caption(f"{row['symbol']}: {row['first_date']} ~ {row['last_date']} ({row['rows']} 条)")

    # 主区域
    if not selected_strategies:
        st.info("请在侧边栏选择至少一个策略")
        return

    if not run_clicked:
        st.info("点击侧边栏「运行回测」开始策略对比")

        # 展示分类知识卡片
        st.subheader("策略分类速查")
        cols = st.columns(len(CATEGORY_ORDER))
        for i, cat_key in enumerate(CATEGORY_ORDER):
            cat = STRATEGY_CATEGORIES[cat_key]
            with cols[i]:
                st.markdown(f"### {cat['label']}")
                st.markdown(f"**{cat['subtitle']}**")
                st.caption(cat["description"])
                st.markdown(f"**市场表现**: {cat['market_view']}")
                # 列出该分类下的策略
                cat_strategies = [
                    name for name, cls in STRATEGY_REGISTRY.items()
                    if cls.category == cat_key and name in selected_strategies
                ]
                for name in cat_strategies:
                    cls = STRATEGY_REGISTRY[name]
                    st.markdown(f"- {cls.name}")

        st.divider()

        # 展示选中策略说明
        st.subheader("选中策略说明")
        for name in selected_strategies:
            cls = STRATEGY_REGISTRY[name]
            cat = STRATEGY_CATEGORIES.get(cls.category, {})
            st.markdown(f"**{cls.name}** `[{cat.get('label', '')}]` — {cls.description}")
        return

    # 运行回测
    with st.spinner("正在运行回测，请稍候..."):
        results = run_backtests(
            selected_strategies,
            start_date=backtest_start.strftime("%Y-%m-%d"),
            end_date=backtest_end.strftime("%Y-%m-%d"),
            risk_free_rate=risk_free_rate,
            commission_rate=commission_rate,
        )

    if not results:
        st.error("回测失败：数据不足")
        return

    # 过滤有效结果
    valid_results = [r for r in results if not r.equity_curve.empty]
    if not valid_results:
        st.error("所有策略数据不足，无法生成回测结果")
        return

    # ============================================================
    # 策略表现对比
    # ============================================================
    st.subheader("  策略表现对比")

    # 反脆弱策略数据说明
    has_antifragile = any(r.strategy_name == "反脆弱策略" for r in valid_results)
    if has_antifragile:
        st.info(
            "  **反脆弱策略说明**: 期权权利金按固定 0.5%/月 模拟，"
            "实际成本受隐含波动率(IV)、剩余期限、行权价等因素影响。"
            "回测中 SPY 月跌 >5% 时产生对冲收益（杠杆系数 7x 估算）。"
            "如需精确回测，需接入付费期权数据源（ORATS $99/月、CBOE DataShop 等）。",
            icon="ℹ️",
        )

    render_equity_chart(valid_results)
    render_drawdown_chart(valid_results)

    st.divider()

    # ============================================================
    # 核心指标对比
    # ============================================================
    st.subheader("  核心指标对比")

    render_metrics_table(valid_results)

    # 最优策略提示
    if len(valid_results) > 1:
        best_sharpe = max(valid_results, key=lambda r: r.sharpe_ratio)
        best_return = max(valid_results, key=lambda r: r.annualized_return)
        lowest_dd = max(valid_results, key=lambda r: r.max_drawdown)  # 回撤是负值，max 即最小回撤

        col1, col2, col3 = st.columns(3)
        col1.info(f"**最高夏普**: {best_sharpe.strategy_name} ({best_sharpe.sharpe_ratio:.2f})")
        col2.info(f"**最高收益**: {best_return.strategy_name} ({best_return.annualized_return:+.2f}%)")
        col3.info(f"**最低回撤**: {lowest_dd.strategy_name} ({lowest_dd.max_drawdown:.2f}%)")

    st.divider()

    # ============================================================
    # 各策略详情
    # ============================================================
    st.subheader("  各策略详情")

    # 2 列布局
    cols = st.columns(2)
    for i, result in enumerate(valid_results):
        with cols[i % 2]:
            render_strategy_card(result)
            if i < len(valid_results) - 1:
                st.divider()


if __name__ == "__main__":
    main()
