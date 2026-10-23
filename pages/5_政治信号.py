"""P5: 政治信号跟踪

跟踪特朗普家族公开喊话、持仓变动及催化剂事件，
按信号链串联，辅助判断内幕交易模式。
"""

import json
import sys
import tempfile
from pathlib import Path
from datetime import datetime, timedelta

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.data.signal_storage import PoliticalSignalStorage
from core.data.signal_fetcher import SignalFetcher
from core.data.storage import DataStorage

# ── 页面配置 ──────────────────────────────────────────────────
st.set_page_config(
    page_title="政治信号 - Market Pulse",
    page_icon=" ️",
    layout="wide",
)

# ── 常量 ──────────────────────────────────────────────────────
SPEAKERS = ["Donald Trump", "Don Jr.", "Eric Trump", "白宫", "五角大楼"]
CATEGORIES = {
    "trade": {"label": "持仓变动", "icon": " ", "color": "#FF9800"},
    "statement": {"label": "公开喊话", "icon": " ", "color": "#2196F3"},
    "event": {"label": "催化剂事件", "icon": " ", "color": "#9C27B0"},
}
DIRECTIONS = {
    "bullish": {"label": "看涨", "icon": " ", "color": "#ef5350"},
    "bearish": {"label": "看跌", "icon": " ", "color": "#26a69a"},
    "neutral": {"label": "中性", "icon": "⚪", "color": "#999"},
}
SOURCES = [
    "Truth Social", "SEC披露", "新闻发布会", "采访",
    "X (Twitter)", "行政令", "政府公告", "其他",
]
# 可追踪的资产列表（项目已有 + 加密 + 个股）
TRACKABLE_ASSETS = [
    "SPY", "QQQ", "DIA", "TLT", "IWM",
    "DELL", "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META", "GOOGL",
    "COIN", "MSTR",
    "BTC", "ETH",
    "XLK", "XLF", "XLE", "XLV", "XLI",
]


# ── 缓存 ──────────────────────────────────────────────────────
@st.cache_resource
def get_signal_storage():
    return PoliticalSignalStorage()


@st.cache_resource
def get_signal_fetcher():
    return SignalFetcher()


@st.cache_resource
def get_price_storage():
    return DataStorage()


# ── 辅助函数 ──────────────────────────────────────────────────
def format_assets(assets_json: str) -> str:
    """将 JSON 数组格式化为逗号分隔的标签"""
    try:
        assets = json.loads(assets_json) if isinstance(assets_json, str) else assets_json
        return ", ".join(assets)
    except (json.JSONDecodeError, TypeError):
        return str(assets_json)


def render_signal_card(row: pd.Series):
    """渲染单条信号卡片（纯 Streamlit 组件，避免 HTML/CSS 兼容问题）"""
    cat = CATEGORIES.get(row["signal_category"], CATEGORIES["statement"])
    direction = DIRECTIONS.get(row["signal_direction"], DIRECTIONS["neutral"])

    # 验证状态
    verification = row.get("verification", "unverified")
    if verification == "verified":
        v_label = "已验证"
    elif verification == "disputed":
        v_label = "有争议"
    else:
        v_label = "未验证"

    # 持仓变动详情
    trade_parts = []
    if row["signal_category"] == "trade" and row.get("trade_detail"):
        try:
            detail = json.loads(row["trade_detail"])
            if detail.get("amount"):
                trade_parts.append(detail["amount"])
            if detail.get("unsolicited"):
                trade_parts.append("非自主决策")
        except (json.JSONDecodeError, TypeError):
            pass

    # 来源链接
    source_url = row.get("source_url", "")
    source_text = f"[{row['source']}]({source_url})" if source_url else row["source"]

    with st.container(border=True):
        # 头部：日期 + 发言人 + 类型 + 来源 + 验证
        header = (
            f"**{row['date']}**　"
            f"**{row['speaker']}**　"
            f"`{cat['icon']} {cat['label']}`　"
            f"  {source_text}　"
            f"*{v_label}*"
        )
        st.markdown(header)

        # 英文内容
        st.markdown(f"> {row['content']}")

        # 中文翻译
        content_cn = row.get("content_cn") or ""
        if content_cn:
            st.caption(content_cn)

        # 持仓变动详情
        if trade_parts:
            st.caption(" · ".join(trade_parts))

        # 方向 + 影响说明
        line = f"**{direction['icon']} {direction['label']}**"
        if row.get("impact_note"):
            line += f"　  {row['impact_note']}"
        st.markdown(line)


def _extract_primary_asset(affected_assets_json: str) -> str:
    """从 affected_assets JSON 中提取第一个资产作为主资产"""
    try:
        assets = json.loads(affected_assets_json) if isinstance(affected_assets_json, str) else affected_assets_json
        if isinstance(assets, list) and assets:
            return assets[0]
    except (json.JSONDecodeError, TypeError):
        pass
    return "OTHER"


def render_asset_group(asset: str, signals_df: pd.DataFrame):
    """按资产分组渲染信号（可展开）"""
    # 筛选该资产的信号
    asset_signals = []
    for _, row in signals_df.iterrows():
        try:
            assets = json.loads(row["affected_assets"]) if isinstance(row["affected_assets"], str) else row["affected_assets"]
            if asset in assets:
                asset_signals.append(row)
        except (json.JSONDecodeError, TypeError):
            pass

    if not asset_signals:
        return

    asset_df = pd.DataFrame(asset_signals).sort_values("date", ascending=False)
    count = len(asset_df)

    # 统计
    trade_count = len(asset_df[asset_df["signal_category"] == "trade"])
    stmt_count = len(asset_df[asset_df["signal_category"] == "statement"])
    event_count = len(asset_df[asset_df["signal_category"] == "event"])

    # 最新信号的方向
    latest = asset_df.iloc[0]
    direction = DIRECTIONS.get(latest["signal_direction"], DIRECTIONS["neutral"])

    # 日期范围
    first_date = asset_df["date"].min()
    last_date = asset_df["date"].max()

    with st.expander(
        f"{direction['icon']} **{asset}** — {count} 条信号 | "
        f"  {trade_count}   {stmt_count}   {event_count} | "
        f"{first_date} → {last_date}",
        expanded=False,
    ):
        for _, sig in asset_df.iterrows():
            render_signal_card(sig)


def render_add_form(storage: PoliticalSignalStorage):
    """添加新信号的表单"""
    with st.form("add_signal", clear_on_submit=True):
        st.markdown("**添加新信号**")
        col1, col2, col3 = st.columns(3)
        with col1:
            date = st.date_input("日期", value=datetime.now())
        with col2:
            speaker = st.selectbox("发言人", SPEAKERS)
        with col3:
            signal_category = st.selectbox(
                "信号类型",
                options=list(CATEGORIES.keys()),
                format_func=lambda k: f"{CATEGORIES[k]['icon']} {CATEGORIES[k]['label']}",
            )

        col4, col5 = st.columns(2)
        with col4:
            source = st.selectbox("信息来源", SOURCES)
        with col5:
            source_url = st.text_input("来源链接（可选）", placeholder="https://...")

        content = st.text_area("原文内容", placeholder="英文原文或摘要")
        content_cn = st.text_area("中文翻译（可选）", placeholder="中文翻译或补充说明")

        col6, col7 = st.columns(2)
        with col6:
            signal_direction = st.selectbox(
                "信号方向",
                options=list(DIRECTIONS.keys()),
                format_func=lambda k: f"{DIRECTIONS[k]['icon']} {DIRECTIONS[k]['label']}",
            )
        with col7:
            affected_assets = st.multiselect("关联资产", TRACKABLE_ASSETS, default=[])

        col8, col9 = st.columns(2)
        with col8:
            chain_id = st.text_input("信号链ID（可选）", placeholder="如 DELL-2025，留空自动归类")
        with col9:
            verification = st.selectbox("验证状态", ["unverified", "verified", "disputed"])

        # 持仓变动详情（仅 trade 类型）
        trade_detail = ""
        if signal_category == "trade":
            trade_detail = st.text_input(
                "持仓变动详情（JSON）",
                placeholder='{"action":"buy","shares":"1000-5000","price_range":"$120-$130"}',
            )

        impact_note = st.text_input("影响说明（可选）", placeholder="简要说明影响或背景")

        submitted = st.form_submit_button("添加信号", use_container_width=True)

        if submitted:
            if not content:
                st.error("请填写原文内容")
                return
            if not affected_assets:
                st.error("请选择至少一个关联资产")
                return

            signal = {
                "chain_id": chain_id or None,
                "date": date.strftime("%Y-%m-%d"),
                "speaker": speaker,
                "signal_category": signal_category,
                "source": source,
                "source_url": source_url,
                "content": content,
                "content_cn": content_cn or None,
                "signal_direction": signal_direction,
                "affected_assets": json.dumps(affected_assets),
                "trade_detail": trade_detail if trade_detail else None,
                "impact_note": impact_note or None,
                "verification": verification,
            }
            storage.save(signal)
            st.success(f"信号已添加: {date} {speaker} {CATEGORIES[signal_category]['label']}")
            st.rerun()


def render_price_overlay(storage: DataStorage):
    """信号叠加走势图"""
    st.subheader("  信号 vs 市场走势")

    col1, col2, col3 = st.columns(3)
    with col1:
        symbol = st.selectbox("选择资产", TRACKABLE_ASSETS, key="overlay_symbol")
    with col2:
        days = st.selectbox("时间范围", [30, 60, 90, 180, 365], index=2, key="overlay_days")
    with col3:
        st.write("")  # 占位

    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    df = storage.load(symbol, start_date, end_date)
    if df.empty:
        st.warning(f"{symbol} 暂无价格数据，请先在 P1 市场全景中获取数据")
        return

    # 获取该资产的信号
    signal_storage = get_signal_storage()
    all_signals = signal_storage.load_all()

    # 解析 affected_assets 筛选包含该 symbol 的信号
    asset_signals = []
    for _, row in all_signals.iterrows():
        try:
            assets = json.loads(row["affected_assets"]) if isinstance(row["affected_assets"], str) else row["affected_assets"]
            if symbol in assets:
                asset_signals.append(row)
        except (json.JSONDecodeError, TypeError):
            pass

    if not asset_signals:
        st.info(f"暂无与 {symbol} 相关的信号记录")

    # 画图
    fig = go.Figure()

    # 价格线
    fig.add_trace(go.Scatter(
        x=df["date"],
        y=df["close"],
        mode="lines",
        name=symbol,
        line=dict(color="#1976D2", width=2),
    ))

    # 信号标记
    for sig in asset_signals:
        direction = DIRECTIONS.get(sig["signal_direction"], DIRECTIONS["neutral"])
        cat = CATEGORIES.get(sig["signal_category"], CATEGORIES["statement"])

        # 找到信号日期对应的价格
        sig_date = pd.to_datetime(sig["date"])
        price_row = df[df["date"] == sig_date]
        if price_row.empty:
            # 找最近的交易日
            nearest = df[df["date"] >= sig_date].head(1)
            if nearest.empty:
                continue
            price_row = nearest

        price_val = price_row["close"].iloc[0]

        fig.add_trace(go.Scatter(
            x=[sig_date],
            y=[price_val],
            mode="markers",
            marker=dict(
                size=14,
                color=direction["color"],
                symbol="triangle-up" if sig["signal_direction"] == "bullish" else "triangle-down",
                line=dict(width=2, color="white"),
            ),
            name=f"{sig['date']} {cat['label']}",
            hovertext=f"{sig['speaker']}: {sig['content'][:50]}",
            showlegend=False,
        ))

        # 垂直虚线
        fig.add_vline(
            x=sig_date,
            line_dash="dot",
            line_color=direction["color"],
            opacity=0.4,
        )

    fig.update_layout(
        height=450,
        margin=dict(l=0, r=0, t=30, b=0),
        xaxis_title="",
        yaxis_title="价格",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )

    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def main():
    storage = get_signal_storage()
    price_storage = get_price_storage()

    # 确保种子数据完整（增量补充，不阻塞）
    added = storage.ensure_seed_data()
    if added > 0:
        st.toast(f"补充 {added} 条种子数据", icon=" ")

    st.title(" ️ 政治信号跟踪")
    st.caption("跟踪特朗普家族公开喊话、持仓变动及催化剂事件，按信号链串联分析")

    # ── 侧边栏筛选 ──────────────────────────────────────────
    with st.sidebar:
        st.subheader("数据更新")
        if st.button("  获取最新数据", use_container_width=True):
            fetcher = get_signal_fetcher()
            with st.status("正在更新数据...", expanded=True) as status:
                st.write("  获取 SEC EDGAR 最新披露...")
                result = fetcher.fetch_incremental(storage, days_back=30)
                st.write(f"  SEC EDGAR: 新增 {result['sec']} 条")
                st.write(f"  公开喊话: 新增 {result['news']} 条")
                total = sum(result.values())
                if total > 0:
                    status.update(label=f"更新完成! 新增 {total} 条", state="complete")
                else:
                    status.update(label="已是最新", state="complete")
            if total > 0:
                st.rerun()

        st.divider()
        st.subheader("OGE 财务披露导入")
        st.caption("总统年度 OGE 278e 披露（含数千笔交易）")
        oge_file = st.file_uploader(
            "上传 OGE 278e PDF",
            type=["pdf"],
            key="oge_upload",
            help="从白宫网站下载的 OGE 278e 财务披露 PDF",
        )
        if oge_file is not None:
            # 保存到临时文件
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(oge_file.read())
                tmp_path = tmp.name

            if st.button("解析并导入 OGE", use_container_width=True):
                fetcher = get_signal_fetcher()
                with st.spinner("正在解析 OGE PDF..."):
                    signals = fetcher.import_oge_pdf(tmp_path)
                if signals:
                    new_count = 0
                    for sig in signals:
                        storage.save(sig)
                        new_count += 1
                    st.success(f"导入 {new_count} 笔 OGE 交易")
                    st.rerun()
                else:
                    st.warning("未解析到交易记录，请确认 PDF 格式")

        st.divider()
        st.subheader("PTR 定期交易报告导入")
        st.caption("总统 PTR 披露（更频繁的交易报告，OCR 解析）")
        ptr_file = st.file_uploader(
            "上传 PTR PDF",
            type=["pdf"],
            key="ptr_upload",
            help="从白宫网站下载的 Periodic Transaction Report PDF（OCR 解析，较慢）",
        )
        if ptr_file is not None:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(ptr_file.read())
                ptr_path = tmp.name

            if st.button("解析并导入 PTR", use_container_width=True):
                fetcher = get_signal_fetcher()
                with st.spinner("正在 OCR 解析 PTR PDF（可能需要1-2分钟）..."):
                    signals = fetcher.import_ptr_pdf(ptr_path)
                if signals:
                    new_count = 0
                    for sig in signals:
                        storage.save(sig)
                        new_count += 1
                    st.success(f"导入 {new_count} 笔 PTR 交易")
                    st.rerun()
                else:
                    st.warning("未解析到交易记录，请确认 PDF 格式")

        st.divider()
        st.subheader("筛选条件")
        filter_speaker = st.selectbox("发言人", ["全部"] + SPEAKERS, key="filter_speaker")
        filter_category = st.selectbox(
            "信号类型",
            ["全部"] + list(CATEGORIES.keys()),
            format_func=lambda k: "全部" if k == "全部" else f"{CATEGORIES[k]['icon']} {CATEGORIES[k]['label']}",
            key="filter_category",
        )
        filter_direction = st.selectbox(
            "信号方向",
            ["全部"] + list(DIRECTIONS.keys()),
            format_func=lambda k: "全部" if k == "全部" else f"{DIRECTIONS[k]['icon']} {DIRECTIONS[k]['label']}",
            key="filter_direction",
        )
        filter_start = st.date_input("开始日期", value=None, key="filter_start")
        filter_end = st.date_input("结束日期", value=None, key="filter_end")

    # ── 一句话结论（近 30 天）——结论先行 ─────────────────
    month_ago = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    recent = storage.load_filtered(start_date=month_ago)

    if recent is not None and not recent.empty:
        n = len(recent)
        dirs = recent["signal_direction"].value_counts().to_dict()
        bull = dirs.get("bullish", 0)
        bear = dirs.get("bearish", 0)
        # 主资产
        asset_counts = {}
        for _, row in recent.iterrows():
            asset = _extract_primary_asset(row["affected_assets"])
            if asset and asset != "OTHER":
                asset_counts[asset] = asset_counts.get(asset, 0) + 1
        top_assets = sorted(asset_counts.items(), key=lambda x: -x[1])[:3]
        asset_str = "、".join(f"{a}({c})" for a, c in top_assets) if top_assets else "—"
        latest = recent.sort_values("date", ascending=False).iloc[0]
        latest_dir = DIRECTIONS.get(latest["signal_direction"], DIRECTIONS["neutral"])

        if bull > bear * 2:
            tone, bg, border, color = "偏多", "#e8f5e9", "#4caf50", "#2e7d32"
        elif bear > bull * 2:
            tone, bg, border, color = "偏空", "#ffebee", "#ef5350", "#c62828"
        else:
            tone, bg, border, color = "多空交织", "#f5f5f5", "#9e9e9e", "#555"

        st.markdown(
            f'<div style="padding:0.85rem 1.1rem;background:{bg};border-left:5px solid {border};'
            f'border-radius:0 0.45rem 0.45rem 0;margin:0.5rem 0 0.8rem 0;">'
            f'<div style="font-size:1.15rem;font-weight:700;color:{color};">'
            f'近 30 天 {n} 条信号 · {tone}（看涨 {bull} / 看跌 {bear}）</div>'
            f'<div style="color:#555;margin-top:0.35rem;font-size:0.92rem;">'
            f'主资产：{asset_str}　|　最新：{latest["date"]} {latest["speaker"]} '
            f'{latest_dir["icon"]} {latest_dir["label"]}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    else:
        st.info("近 30 天无新信号。可从侧边栏拉取 SEC EDGAR / 导入 PTR。")

    # ── 统计概览（明细）───────────────────────────────────────
    stats = storage.get_stats()
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("总信号数", stats["total"])
    col2.metric("  持仓变动", stats["trades"])
    col3.metric("  公开喊话", stats["statements"])
    col4.metric("  催化剂", stats["events"])
    col5.metric("  信号链", stats["chains"])

    st.divider()

    # ── 获取筛选后的数据 ──────────────────────────────────────
    filtered = storage.load_filtered(
        speaker=filter_speaker,
        signal_category=filter_category,
        signal_direction=filter_direction,
        start_date=filter_start.strftime("%Y-%m-%d") if filter_start else None,
        end_date=filter_end.strftime("%Y-%m-%d") if filter_end else None,
    )

    # ── 标签页：信号链 / 全部信号 / 添加 / 走势图 ──────────
    tab1, tab2, tab3, tab4 = st.tabs([
        "  信号链视图",
        "  全部信号",
        " ➕ 添加信号",
        "  信号 vs 走势",
    ])

    with tab1:
        # 按资产分组视图
        if filtered.empty:
            st.info("筛选条件下无信号记录")
        else:
            # 提取每个信号的主资产，按资产分组
            asset_groups = {}
            for _, row in filtered.iterrows():
                asset = _extract_primary_asset(row["affected_assets"])
                if asset not in asset_groups:
                    asset_groups[asset] = []
                asset_groups[asset].append(row)

            # 按信号数量排序（多的在前）
            sorted_assets = sorted(asset_groups.keys(), key=lambda a: len(asset_groups[a]), reverse=True)

            for asset in sorted_assets:
                asset_df = pd.DataFrame(asset_groups[asset])
                render_asset_group(asset, asset_df)

    with tab2:
        # 全部信号时间线
        if filtered.empty:
            st.info("筛选条件下无信号记录")
        else:
            for _, row in filtered.iterrows():
                render_signal_card(row)

    with tab3:
        render_add_form(storage)

    with tab4:
        render_price_overlay(price_storage)


if __name__ == "__main__":
    main()
