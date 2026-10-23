"""P6: 机构持仓跟踪

解析 SEC EDGAR 13-F 季度报告，追踪顶级机构持仓变动，
检测连续增持信号，辅助判断大资本方向。
"""

import sys
from pathlib import Path

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.data.thirteen_f_storage import ThirteenFStorage
from core.data.thirteen_f_fetcher import ThirteenFFetcher

# ── 页面配置 ──────────────────────────────────────────────────
st.set_page_config(
    page_title="机构持仓 - Market Pulse",
    page_icon=" ",
    layout="wide",
)

CATEGORY_LABELS = {
    "hedge_fund": {"label": "对冲基金", "icon": " ", "color": "#7b1fa2"},
    "asset_mgmt": {"label": "资产管理", "icon": " ", "color": "#1565c0"},
    "other": {"label": "其他", "icon": " ", "color": "#555"},
}


# ── 缓存 ──────────────────────────────────────────────────────
@st.cache_resource
def get_storage():
    return ThirteenFStorage()


@st.cache_resource
def get_fetcher():
    return ThirteenFFetcher()


# ── 工具函数 ──────────────────────────────────────────────────

def format_value(v):
    """格式化金额（美元）"""
    if v >= 1e9:
        return f"${v / 1e9:.1f}B"
    elif v >= 1e6:
        return f"${v / 1e6:.1f}M"
    elif v >= 1e3:
        return f"${v / 1e3:.0f}K"
    return f"${v:,.0f}"


def format_shares(s):
    """格式化股数"""
    if s >= 1e6:
        return f"{s / 1e6:.1f}M"
    elif s >= 1e3:
        return f"{s / 1e3:.0f}K"
    return f"{s:,}"


# ── Tab 1: 持仓概览 ──────────────────────────────────────────

def render_overview(storage):
    """持仓概览 — 先共识结论，再各机构明细"""
    managers = storage.get_tracked_managers()
    if managers.empty:
        st.info("暂无持仓数据，请先点击侧边栏「更新最新持仓」")
        return

    # 筛选器
    names = managers["name"].tolist()
    selected = st.multiselect(
        "选择机构",
        names,
        default=names[:5] if len(names) > 5 else names,
        key="overview_managers",
    )

    if not selected:
        st.warning("请至少选择一家机构")
        return

    # ── 汇总：全部被选机构的 Top 持仓 ──
    all_top = []
    for _, mgr in managers[managers["name"].isin(selected)].iterrows():
        cik = mgr["cik"]
        name = mgr["name"]
        df = storage.load_latest_holdings(cik)
        if df.empty:
            continue
        total_value = df["value"].sum()
        top5 = df.head(5).copy()
        top5["weight"] = (top5["value"] / total_value * 100).round(1)
        top5["manager"] = name
        all_top.append(top5[["manager", "name_of_issuer", "value", "weight"]])

    if all_top:
        merged = pd.concat(all_top)
        # 按标的汇总：哪些标的被多家机构重仓
        issuer_summary = (
            merged.groupby("name_of_issuer")
            .agg(
                count=("manager", "nunique"),
                total_value=("value", "sum"),
                managers=("manager", lambda x: ", ".join(x.unique())),
            )
            .sort_values("total_value", ascending=False)
            .head(15)
        )

        # 先给结论
        if not issuer_summary.empty:
            high = issuer_summary[issuer_summary["count"] >= 3]
            top3 = issuer_summary.head(3)
            top3_str = "、".join(
                f"{idx}（{row['count']}家）" for idx, row in top3.iterrows()
            )
            if not high.empty:
                st.success(
                    f"**共识结论**：{len(selected)} 家机构 Top5 交叉出 {len(issuer_summary)} 个重仓标的，"
                    f"其中 **{len(high)} 个高共识（≥3 家）**。最重：{top3_str}"
                )
            else:
                st.info(
                    f"**共识结论**：{len(selected)} 家机构 Top5 交叉出 {len(issuer_summary)} 个重仓标的，"
                    f"暂无 ≥3 家高共识。最重：{top3_str}"
                )

        st.markdown("#### 共识重仓标的（多家机构 Top 5 交叉）")
        if not issuer_summary.empty:
            for issuer, row in issuer_summary.iterrows():
                badge = ""
                if row["count"] >= 3:
                    badge = " **高共识**"
                elif row["count"] >= 2:
                    badge = " 共识"
                st.markdown(
                    f"- **{issuer}** — {format_value(row['total_value'])} "
                    f"({row['count']}家机构){badge} — {row['managers']}"
                )
        st.divider()

    # ── 各机构详情（折叠） ──
    for idx, (_, mgr) in enumerate(managers[managers["name"].isin(selected)].iterrows()):
        cik = mgr["cik"]
        name = mgr["name"]
        cat = CATEGORY_LABELS.get(mgr["category"], CATEGORY_LABELS["other"])

        df = storage.load_latest_holdings(cik)
        if df.empty:
            continue

        total_value = df["value"].sum()
        periods = storage.load_periods(cik)
        latest_period = periods[0] if periods else "未知"

        with st.expander(
            f"{cat['icon']} **{name}** — {cat['label']} | "
            f"持仓 {len(df)} 只 | 总市值 {format_value(total_value)} | "
            f"报告期 {latest_period}",
            expanded=False,
        ):
            # Top 10 持仓 treemap
            top10 = df.head(10).copy()
            if not top10.empty:
                fig = go.Figure(go.Treemap(
                    labels=top10["name_of_issuer"],
                    parents=[""] * len(top10),
                    values=top10["value"],
                    textinfo="label+value+percent parent",
                    texttemplate="<b>%{label}</b><br>%{value:$,.0f}<br>%{percentParent:.1%}",
                    marker=dict(
                        colors=top10["value"],
                        colorscale="Greens",
                    ),
                    hovertemplate=(
                        "<b>%{label}</b><br>"
                        "市值: %{value:$,.0f}<br>"
                        "占比: %{percentParent:.1%}<extra></extra>"
                    ),
                ))
                fig.update_layout(
                    height=350,
                    margin=dict(t=10, b=10, l=10, r=10),
                    template="plotly_white",
                )
                st.plotly_chart(fig, use_container_width=True,
                                config={"displayModeBar": False},
                                key=f"treemap_{cik}_{idx}")

            # 持仓明细表
            display_df = df[["name_of_issuer", "cusip", "title_of_class", "value", "shares"]].copy()
            display_df["weight"] = (display_df["value"] / total_value * 100).round(2)
            display_df["value_fmt"] = display_df["value"].apply(format_value)
            display_df["shares_fmt"] = display_df["shares"].apply(format_shares)
            display_df["weight_fmt"] = display_df["weight"].apply(lambda x: f"{x:.2f}%")

            st.dataframe(
                display_df[["name_of_issuer", "cusip", "title_of_class",
                            "value_fmt", "shares_fmt", "weight_fmt"]].rename(columns={
                    "name_of_issuer": "标的",
                    "cusip": "CUSIP",
                    "title_of_class": "证券类型",
                    "value_fmt": "市值",
                    "shares_fmt": "持股数",
                    "weight_fmt": "仓位占比",
                }),
                use_container_width=True,
                hide_index=True,
                key=f"table_{cik}_{idx}",
            )


# ── Tab 2: 增持信号 ──────────────────────────────────────────

def render_accumulation(storage):
    """增持信号检测 — 先结论，后明细"""
    st.caption("连续季度持股数↑ 且 仓位占比↑ 的资产，表明机构在主动加仓")

    col1, col2 = st.columns(2)
    with col1:
        lookback = st.slider("回溯季度数", 2, 8, 4, key="acc_lookback")
    with col2:
        min_managers = st.slider("最少机构数", 1, 5, 1, key="acc_min_mgr")

    signals = storage.get_accumulation_signals(lookback_quarters=lookback)
    if signals.empty:
        st.info("暂无增持信号（需要至少 2 个季度的数据）")
        return

    # 过滤
    signals = signals[signals["manager_count"] >= min_managers]

    if signals.empty:
        st.info(f"无满足条件（≥{min_managers} 家机构）的增持信号")
        return

    # ── 结论先行：一句话 + 板块 + 高共识 ──
    from scripts.daily_sync import _classify_sector
    sector_counts = {}
    for _, row in signals.iterrows():
        sec = _classify_sector(row["name_of_issuer"])
        if sec:
            sector_counts[sec] = sector_counts.get(sec, 0) + 1
    sorted_secs = sorted(sector_counts.items(), key=lambda x: x[1], reverse=True)
    secs_str = "、".join(f"{s}({c})" for s, c in sorted_secs[:5]) if sorted_secs else "—"

    high_consensus = signals[signals["manager_count"] >= 3]
    top_names = "、".join(signals.head(3)["name_of_issuer"].tolist())

    if not high_consensus.empty:
        st.success(
            f"**结论**：最近 {lookback} 个季度 **{len(signals)} 个资产被连续增持**"
            f"（≥{min_managers} 家），其中 **{len(high_consensus)} 个 ≥3 家高共识**。"
            f"重点看 **{top_names}**。板块分布：{secs_str}"
        )
    else:
        st.info(
            f"**结论**：最近 {lookback} 个季度 **{len(signals)} 个资产被连续增持**"
            f"（≥{min_managers} 家），暂无 ≥3 家高共识。最重：**{top_names}**。"
            f"板块分布：{secs_str}"
        )

    if not high_consensus.empty:
        st.markdown("**高共识标的（≥3 家）**")
        for _, row in high_consensus.head(8).iterrows():
            st.markdown(
                f"- **{row['name_of_issuer']}** — {row['manager_count']}家 "
                f"({row['accumulating_managers'][:60]}{'...' if len(row['accumulating_managers']) > 60 else ''})"
            )

    st.divider()

    # ── Top 信号详情（最多展示 5 个） ──
    st.markdown("#### Top 增持信号详情")
    top_signals = signals.head(5)

    for i, (_, row) in enumerate(top_signals.iterrows()):
        color = "#2e7d32" if row["manager_count"] >= 3 else "#1565c0"
        badge = f'<span style="background:{color};color:white;padding:2px 8px;border-radius:12px;font-size:0.8rem;">{row["manager_count"]} 家</span>'

        st.markdown(
            f"**{row['name_of_issuer']}** {badge} — "
            f"CUSIP `{row['cusip']}` | 报告期 {row['latest_period']}"
        )
        st.caption(f"增持机构: {row['accumulating_managers']}")

        # 展示各机构的季度持仓变化
        comparison = storage.compare_holdings_across_managers(row["cusip"])
        if not comparison.empty:
            fig = go.Figure()
            for mgr_name, grp in comparison.groupby("manager_name"):
                grp = grp.sort_values("period_end")
                fig.add_trace(go.Bar(
                    name=mgr_name,
                    x=grp["period_end"],
                    y=grp["shares"],
                    text=grp["shares"].apply(format_shares),
                    textposition="auto",
                ))
            fig.update_layout(
                barmode="group",
                height=300,
                template="plotly_white",
                legend=dict(orientation="h", yanchor="bottom", y=1.02),
                xaxis_title="报告期",
                yaxis_title="持股数",
                margin=dict(t=10, b=10),
            )
            st.plotly_chart(fig, use_container_width=True,
                            config={"displayModeBar": False},
                            key=f"acc_{i}_{row['cusip']}")

        if i < len(top_signals) - 1:
            st.divider()

    # 提示更多
    if len(signals) > 5:
        st.caption(f"还有 {len(signals) - 5} 个增持信号，可在侧边栏调整筛选条件查看")


# ── Tab 3: 机构对比 ──────────────────────────────────────────

def render_comparison(storage):
    """跨机构持仓对比"""
    keyword = st.text_input("搜索标的（公司名或 CUSIP）", key="cmp_search",
                            placeholder="例: Apple、AAPL、037833100")

    if not keyword:
        st.info("输入关键词搜索标的，查看多家机构的持仓对比")
        return

    results = storage.search_holdings_by_issuer(keyword)
    if results.empty:
        st.warning(f"未找到匹配「{keyword}」的标的")
        return

    # 选择标的
    options = [
        f"{row['name_of_issuer']} ({row['cusip']}) — {row['manager_count']} 家机构"
        for _, row in results.iterrows()
    ]
    selected = st.selectbox("选择标的", options, key="cmp_select")

    if not selected:
        return

    # 提取 CUSIP
    cusip = selected.split("(")[1].split(")")[0]
    issuer_name = selected.split(" (")[0]

    comparison = storage.compare_holdings_across_managers(cusip)
    if comparison.empty:
        st.warning("无持仓数据")
        return

    st.subheader(f"  {issuer_name} ({cusip})")

    # 柱状图：各机构持股数变化
    fig = go.Figure()
    for mgr_name, grp in comparison.groupby("manager_name"):
        grp = grp.sort_values("period_end")
        fig.add_trace(go.Bar(
            name=mgr_name,
            x=grp["period_end"],
            y=grp["shares"],
            text=grp["shares"].apply(format_shares),
            textposition="auto",
        ))
    fig.update_layout(
        barmode="group",
        height=400,
        template="plotly_white",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        xaxis_title="报告期",
        yaxis_title="持股数",
    )
    st.plotly_chart(fig, use_container_width=True,
                    config={"displayModeBar": False},
                    key="cmp_bar_shares")

    # 权重变化图
    fig2 = go.Figure()
    for mgr_name, grp in comparison.groupby("manager_name"):
        grp = grp.sort_values("period_end")
        fig2.add_trace(go.Scatter(
            name=mgr_name,
            x=grp["period_end"],
            y=grp["weight"],
            mode="lines+markers",
            text=grp["weight"].apply(lambda x: f"{x:.2f}%"),
        ))
    fig2.update_layout(
        title="仓位占比变化",
        height=350,
        template="plotly_white",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        xaxis_title="报告期",
        yaxis_title="仓位占比 (%)",
    )
    st.plotly_chart(fig2, use_container_width=True,
                    config={"displayModeBar": False},
                    key="cmp_line_weight")

    # 明细表
    display = comparison[["manager_name", "period_end", "shares", "value", "weight"]].copy()
    display["value"] = display["value"].apply(format_value)
    display["shares"] = display["shares"].apply(format_shares)
    display["weight"] = display["weight"].apply(lambda x: f"{x:.2f}%")
    st.dataframe(
        display.rename(columns={
            "manager_name": "机构",
            "period_end": "报告期",
            "shares": "持股数",
            "value": "市值",
            "weight": "仓位占比",
        }),
        use_container_width=True,
        hide_index=True,
        key="cmp_table",
    )


# ── Tab 4: 管理人列表 ────────────────────────────────────────

def render_managers(storage, fetcher):
    """管理人列表管理"""
    managers = storage.get_tracked_managers()

    # 统计
    stats = storage.get_manager_stats()
    col1, col2, col3 = st.columns(3)
    col1.metric("跟踪机构数", stats["managers"])
    col2.metric("持仓记录数", stats["holdings"])
    col3.metric("报告份数", stats["filings"])

    if not managers.empty:
        display = managers[["name", "cik", "category", "is_default"]].copy()
        display["category"] = display["category"].map(
            lambda x: CATEGORY_LABELS.get(x, CATEGORY_LABELS["other"])["label"]
        )
        display["is_default"] = display["is_default"].map({1: "默认", 0: "自定义"})
        st.dataframe(
            display.rename(columns={
                "name": "机构名称",
                "cik": "CIK",
                "category": "类别",
                "is_default": "类型",
            }),
            use_container_width=True,
            hide_index=True,
        )

    st.divider()
    st.subheader("添加新机构")

    search_query = st.text_input("搜索 EDGAR", key="mgr_search",
                                  placeholder="输入机构名称关键词")

    if search_query and st.button("  搜索", key="btn_search"):
        with st.spinner("搜索 EDGAR..."):
            results = fetcher.search_edgar_by_name(search_query)

        if results:
            st.session_state["edgar_results"] = results
        else:
            st.warning("未找到匹配结果")

    if "edgar_results" in st.session_state and st.session_state["edgar_results"]:
        results = st.session_state["edgar_results"]
        for r in results:
            col_name, col_cik, col_btn = st.columns([3, 2, 1])
            col_name.write(f"**{r['name']}**")
            col_cik.write(f"`{r['cik']}`")
            if col_btn.button("添加", key=f"add_{r['cik']}"):
                success = storage.add_manager(r["cik"], r["name"])
                if success:
                    st.success(f"已添加 {r['name']}")
                    st.rerun()
                else:
                    st.warning("该机构已在跟踪列表中")

    # 移除非默认机构
    non_default = managers[managers["is_default"] == 0]
    if not non_default.empty:
        st.divider()
        st.subheader("移除自定义机构")
        to_remove = st.selectbox(
            "选择要移除的机构",
            non_default["name"].tolist(),
            key="mgr_remove",
        )
        if st.button("  移除", key="btn_remove"):
            cik = non_default[non_default["name"] == to_remove]["cik"].iloc[0]
            if storage.remove_manager(cik):
                st.success(f"已移除 {to_remove}")
                st.rerun()
            else:
                st.error("移除失败")


# ── 主函数 ────────────────────────────────────────────────────

def main():
    storage = get_storage()
    fetcher = get_fetcher()

    # 初始化默认机构
    storage.seed_default_managers()

    # ── 侧边栏 ──
    with st.sidebar:
        st.header("  数据更新")
        if st.button("  更新最新持仓", use_container_width=True):
            with st.status("正在从 SEC EDGAR 获取 13-F 数据...", expanded=True) as status:
                result = fetcher.fetch_and_store_new(storage)
                if result["new_filings"] > 0:
                    status.update(
                        label=f"  获取完成：{result['new_filings']} 份新报告，"
                              f"{result['new_holdings']} 条持仓",
                        state="complete",
                        expanded=False,
                    )
                elif result["errors"] > 0:
                    status.update(
                        label=f"⚠️ 完成但有 {result['errors']} 个错误",
                        state="error",
                    )
                else:
                    status.update(label="  暂无新报告", state="complete")
            st.rerun()

        stats = storage.get_manager_stats()
        st.metric("跟踪机构", stats["managers"])
        st.metric("持仓记录", stats["holdings"])

    # ── 标题 ──
    st.title("  机构持仓跟踪")
    st.caption("SEC EDGAR 13-F 季度报告解析 · 顶级机构持仓变动 · 连续增持信号检测")

    # ── 总览结论（tabs 之前）────────────────────────────────
    try:
        acc = storage.get_accumulation_signals(lookback_quarters=4)
        mgr_stats = storage.get_manager_stats()
        periods = []
        managers = storage.get_tracked_managers()
        for _, mgr in managers.head(8).iterrows():
            p = storage.load_periods(mgr["cik"])
            if p:
                periods.append(p[0])
        latest_period = max(periods) if periods else "未知"
        acc_n = len(acc) if acc is not None and not acc.empty else 0
        high_n = 0
        top_names = ""
        if acc is not None and not acc.empty:
            high = acc[acc["manager_count"] >= 3]
            high_n = len(high)
            top_names = "、".join(acc.head(3)["name_of_issuer"].tolist())

        if acc_n > 0:
            st.success(
                f"**结论**：最新报告期 **{latest_period}**，"
                f"跟踪 {mgr_stats['managers']} 家机构 / {mgr_stats['holdings']} 条持仓；"
                f"**{acc_n} 个标的被连续增持**（其中 {high_n} 个 ≥3 家高共识），"
                f"重点看 **{top_names}**。"
            )
        else:
            st.info(
                f"**结论**：最新报告期 **{latest_period}**，"
                f"跟踪 {mgr_stats['managers']} 家机构 / {mgr_stats['holdings']} 条持仓；"
                f"暂无多季度连续增持信号（需至少 2 个季度数据）。"
            )
    except Exception:
        st.caption("总览结论生成失败，可直接看下方明细")

    # ── Tabs ──
    tabs = st.tabs(["  持仓概览", "  增持信号", "  机构对比", "  管理人列表"])

    with tabs[0]:
        render_overview(storage)
    with tabs[1]:
        render_accumulation(storage)
    with tabs[2]:
        render_comparison(storage)
    with tabs[3]:
        render_managers(storage, fetcher)


if __name__ == "__main__":
    main()
