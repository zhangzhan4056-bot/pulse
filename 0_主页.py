"""Market Pulse - 市场脉搏观测网站

A股 + 美股投资监控与决策辅助系统。首页仪表盘。
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

sys.path.insert(0, str(Path(__file__).parent))

from core.data import DataManager, US_SYMBOLS, CN_SYMBOLS, GLOBAL_SYMBOLS
from core.strategy.indicators import detect_market_regime, calc_regime_score
from core.strategy.regime_config import (
    REGIME_COLORS,
    REGIME_BACKGROUNDS,
    REGIME_LABELS,
    REGIME_RECOMMENDATIONS,
)
from core.scanner.history import ScanHistory
from core.scanner.scorer import (
    OPPORTUNITY_CANDIDATE,
    OPPORTUNITY_NEAR,
    OPPORTUNITY_AVOID,
    OPPORTUNITY_NEUTRAL,
    ABS_MOMENTUM_MIN_PCT,
)
from core.strategy.regime_config import PRODUCTION_STRATEGY
from app.components.tooltips import COMPOSITE_SCORE, VOL_REGIME, MARKET_REGIME, tip_icon

# ============================================================
# 页面配置
# ============================================================
st.set_page_config(
    page_title="Market Pulse",
    page_icon=" ",
    layout="wide",
    initial_sidebar_state="expanded",
)


@st.cache_resource
def get_data_manager():
    return DataManager()


# ============================================================
# 工具函数
# ============================================================

def _format_price(price: float, symbol: str) -> str:
    if symbol in CN_SYMBOLS or symbol in GLOBAL_SYMBOLS:
        return f"{price:,.2f}"
    return f"${price:,.2f}"


def _format_change(change_pct: float) -> str:
    sign = "+" if change_pct >= 0 else ""
    return f"{sign}{change_pct:.2f}%"


def _get_change_color(change_pct: float) -> str:
    return "#ef5350" if change_pct >= 0 else "#26a69a"


# ============================================================
# 区块 1: 决策面板（体制 + 行动摘要）
# ============================================================

OPP_CONFIG = {
    OPPORTUNITY_CANDIDATE: {"label": "过门槛", "icon": " ", "color": "#2e7d32", "bg": "#e8f5e9", "border": "#4caf50"},
    OPPORTUNITY_NEAR: {"label": "门槛附近", "icon": " ", "color": "#1565c0", "bg": "#e3f2fd", "border": "#42a5f5"},
    OPPORTUNITY_AVOID: {"label": "不碰", "icon": " ", "color": "#c62828", "bg": "#ffebee", "border": "#ef5350"},
    OPPORTUNITY_NEUTRAL: {"label": "数据不足", "icon": "—", "color": "#757575", "bg": "#fafafa", "border": "#bdbdbd"},
}


def _render_decision_panel(dm: DataManager):
    """渲染决策面板：体制 + 推荐策略 + 行动资产列表"""
    # ---- 检测体制 ----
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")

    spy_df = dm.load("SPY", start_date, end_date)
    tlt_df = dm.load("TLT", start_date, end_date)
    cl_df = dm.load("CL", start_date, end_date)

    if spy_df.empty or len(spy_df) < 60:
        st.info("数据不足，无法检测市场体制。请先在「市场全景」页面获取数据。")
        return

    spy_close = spy_df["close"]
    tlt_close = tlt_df["close"] if not tlt_df.empty else pd.Series()
    cl_close = cl_df["close"] if not cl_df.empty else pd.Series()

    regime = {"regime": "中性", "description": "数据不足"}
    composite = 50.0
    vol_regime = "正常"

    if not tlt_close.empty and not cl_close.empty:
        regime = detect_market_regime(spy_close, tlt_close, cl_close)
    rs = calc_regime_score(spy_close)
    composite = rs.get("composite", 50)
    vol_regime = rs.get("vol_regime", "正常")

    regime_name = regime["regime"]
    color = REGIME_COLORS.get(regime_name, "#757575")
    bg = REGIME_BACKGROUNDS.get(regime_name, "#f5f5f5")
    label = REGIME_LABELS.get(regime_name, "  中性")
    rec = REGIME_RECOMMENDATIONS.get(regime_name, REGIME_RECOMMENDATIONS["中性"])
    score_color = "#2e7d32" if composite >= 65 else "#c62828" if composite <= 35 else "#555"

    # ---- 加载扫描结果 ----
    scan_history = ScanHistory()
    snapshots = scan_history.get_all_snapshots()
    has_scan = len(snapshots) > 0

    scores_by_opp = {opp: [] for opp in OPP_CONFIG}
    scan_date = ""
    if has_scan:
        latest = snapshots[-1]
        scan_date = latest.get("date", "")
        for s in latest.get("scores", []):
            opp = s.get("opportunity", OPPORTUNITY_NEUTRAL)
            # 历史快照里的均值回归归到不碰
            if opp == "mean_reversion":
                opp = OPPORTUNITY_AVOID
            if opp in scores_by_opp:
                scores_by_opp[opp].append(s)

    # ---- 渲染 ----
    col_main, col_side = st.columns([2, 1])

    with col_main:
        # 一句话结论（先给结果）
        cand_n = len(scores_by_opp.get(OPPORTUNITY_CANDIDATE, []))
        near_n = len(scores_by_opp.get(OPPORTUNITY_NEAR, []))
        avoid_n = len(scores_by_opp.get(OPPORTUNITY_AVOID, []))
        top_cands = (scores_by_opp.get(OPPORTUNITY_CANDIDATE) or [])[:3]
        top_names = "、".join(a.get("name", "") for a in top_cands) if top_cands else "无"

        if cand_n > 0:
            conclusion = (
                f"**{PRODUCTION_STRATEGY}**（12M >{ABS_MOMENTUM_MIN_PCT:.0f}%）："
                f"{cand_n} 个过门槛，优先 **{top_names}**；其余进现金/SHY。"
            )
            st.success(conclusion)
        else:
            conclusion = (
                f"**{PRODUCTION_STRATEGY}**（12M >{ABS_MOMENTUM_MIN_PCT:.0f}%）："
                f"0 个过门槛，{near_n} 个在门槛附近，**先不买，拿现金/SHY**。"
            )
            st.info(conclusion)

        # 体制横幅
        st.markdown(
            f'<div style="padding:0.85rem 1.1rem;background:{bg};border-left:5px solid {color};'
            f'border-radius:0 0.5rem 0.5rem 0;margin-bottom:0.75rem;">'
            f'<div style="display:flex;align-items:center;gap:1rem;flex-wrap:wrap;">'
            f'<span style="font-size:1.35rem;font-weight:bold;color:{color};">{label}{tip_icon(MARKET_REGIME)}</span>'
            f'<span style="color:#555;">{regime["description"]}</span>'
            f'<span style="font-size:1.25rem;font-weight:600;color:{score_color};margin-left:auto;">{composite:.0f}</span>'
            f'<span style="color:#888;font-size:0.85rem;">综合评分{tip_icon(COMPOSITE_SCORE)} · 波动率{vol_regime}{tip_icon(VOL_REGIME)}</span>'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # 推荐策略（生产策略置顶）
        st.markdown(
            f'<div style="padding:0.55rem 1rem;background:#f8f9fa;border-radius:0.4rem;margin-bottom:0.75rem;">'
            f'<span style="font-weight:600;color:#333;">  推荐策略：</span>'
            f'<span style="color:{color};font-weight:600;">{rec["strategies"][0]["name"]}</span>'
            f'<span style="color:#666;margin-left:0.5rem;">— {rec["strategies"][0]["reason"]}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # 行动资产列表
        if not has_scan:
            st.info("暂无扫描数据，请先运行机会扫描")
            st.page_link("pages/2_机会扫描.py", label="  前往机会扫描", use_container_width=True)
        else:
            action_cols = st.columns(3)
            action_items = [
                (OPPORTUNITY_CANDIDATE, "过门槛"),
                (OPPORTUNITY_NEAR, "门槛附近"),
                (OPPORTUNITY_AVOID, "不碰"),
            ]
            for col, (opp_type, action_label) in zip(action_cols, action_items):
                cfg = OPP_CONFIG[opp_type]
                assets = scores_by_opp.get(opp_type, [])
                with col:
                    st.markdown(
                        f'<div style="padding:0.5rem;background:{cfg["bg"]};border-radius:0.4rem;'
                        f'border-bottom:3px solid {cfg["border"]};margin-bottom:0.3rem;">'
                        f'<span style="font-weight:600;color:{cfg["color"]};">{cfg["icon"]} {action_label}</span>'
                        f'<span style="color:#888;margin-left:0.3rem;">({len(assets)})</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                    if not assets:
                        st.caption("—")
                    else:
                        for a in assets[:3]:
                            summary = a.get("summary", "")
                            if len(summary) > 32:
                                summary = summary[:32] + "..."
                            st.markdown(
                                f'<div style="padding:0.3rem 0;border-bottom:1px solid #eee;">'
                                f'<span style="font-weight:500;">{a["name"]}</span>'
                                f'<span style="color:#888;font-size:0.8rem;margin-left:0.3rem;">{summary}</span>'
                                f'</div>',
                                unsafe_allow_html=True,
                            )
                        if len(assets) > 3:
                            st.caption(f"等 {len(assets)} 个")

    with col_side:
        # 快速操作
        st.markdown(
            '<div style="padding:0.8rem;background:#f8f9fa;border-radius:0.4rem;margin-bottom:0.8rem;">'
            '<div style="font-weight:600;color:#333;margin-bottom:0.5rem;">快速操作</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        st.page_link("pages/1_市场全景.py", label="  获取最新数据", use_container_width=True)
        st.page_link("pages/2_机会扫描.py", label="  运行完整扫描", use_container_width=True)
        st.page_link("pages/4_复盘回顾.py", label="  查看复盘分析", use_container_width=True)

        if scan_date:
            st.caption(f"扫描日期: {scan_date}")


# ============================================================
# 区块 2: 关键资产快照
# ============================================================

KEY_ASSETS = [
    ("SPY", "标普500"),
    ("QQQ", "纳指100"),
    ("TLT", "20Y国债"),
    ("CL", "WTI原油"),
    ("GLD", "黄金"),
    ("000300", "沪深300"),
    ("399813", "半导体"),
]


def _render_asset_snapshot(dm: DataManager):
    """渲染关键资产快照卡片"""
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")

    cols = st.columns(len(KEY_ASSETS))
    for col, (symbol, label) in zip(cols, KEY_ASSETS):
        df = dm.load(symbol, start_date, end_date)
        if df.empty:
            with col:
                st.warning(f"{label}: 暂无数据")
            continue

        latest = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else df.iloc[-1]
        price = latest["close"]
        change_pct = ((price - prev["close"]) / prev["close"]) * 100
        color = _get_change_color(change_pct)

        with col:
            # 价格卡片
            st.markdown(
                f'<div style="border-radius:0.5rem;border:1px solid #e0e0e0;background:white;'
                f'padding:0.8rem 1rem;text-align:center;">'
                f'<div style="font-size:0.85rem;color:#666;margin-bottom:0.3rem;">{label}</div>'
                f'<div style="font-size:1.3rem;font-weight:bold;margin-bottom:0.15rem;">{_format_price(price, symbol)}</div>'
                f'<div style="font-size:0.9rem;color:{color};font-weight:500;">{_format_change(change_pct)}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

            # 迷你走势图
            dates = df["date"].dt.strftime("%Y-%m-%d").tolist()
            closes = df["close"].tolist()
            base_price = closes[0]
            pct_changes = [(p / base_price - 1) * 100 for p in closes]

            y_min, y_max = min(pct_changes), max(pct_changes)
            y_pad = (y_max - y_min) * 0.05 if y_max != y_min else 1.0

            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=dates, y=pct_changes, mode="lines",
                line=dict(color=color, width=2), showlegend=False,
                customdata=closes,
                hovertemplate="%{x}<br>涨跌幅: %{y:.2f}%<br>价格: %{customdata:,.2f}<extra></extra>",
            ))
            fig.update_layout(
                height=180,
                margin=dict(l=30, r=5, t=5, b=20),
                xaxis=dict(showgrid=False, tickformat="%m/%d", tickfont=dict(size=12, color="#999")),
                yaxis=dict(
                    showgrid=True, gridcolor="rgba(0,0,0,0.05)",
                    tickfont=dict(size=12, color="#999"), ticksuffix="%",
                    range=[y_min - y_pad, y_max + y_pad], side="right",
                ),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                hovermode="x",
            )
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False}, key=f"spark_{symbol}")


# ============================================================
# 区块 3: 综合评分趋势
# ============================================================

def _render_score_trend():
    """渲染综合评分趋势图"""
    scan_history = ScanHistory()
    snapshots = scan_history.get_all_snapshots()

    if len(snapshots) < 2:
        return  # 历史不足，不显示

    rows = []
    for snap in snapshots:
        rows.append({
            "date": snap["date"],
            "composite": snap.get("composite_score", 50),
            "regime": snap.get("market_regime", "中性"),
        })

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])

    fig = go.Figure()

    # 按体制分段着色
    regime_order = ["风险偏好", "避险", "危机", "滞胀担忧", "震荡", "中性"]
    for regime_name in regime_order:
        mask = df["regime"] == regime_name
        if not mask.any():
            continue
        sub = df[mask]
        fig.add_trace(go.Scatter(
            x=sub["date"], y=sub["composite"],
            mode="markers+lines",
            name=regime_name,
            marker=dict(color=REGIME_COLORS.get(regime_name, "#999"), size=6),
            line=dict(color=REGIME_COLORS.get(regime_name, "#999"), width=2),
            hovertemplate="%{x|%Y-%m-%d}<br>评分: %{y:.0f}<br>体制: " + regime_name + "<extra></extra>",
        ))

    # 参考线
    fig.add_hline(y=70, line_dash="dash", line_color="#2e7d32", line_width=1, annotation_text="偏多")
    fig.add_hline(y=50, line_dash="dot", line_color="#999", line_width=1)
    fig.add_hline(y=30, line_dash="dash", line_color="#c62828", line_width=1, annotation_text="偏空")

    fig.update_layout(
        title="综合评分趋势",
        yaxis_title="评分 (0-100)",
        yaxis=dict(range=[0, 100]),
        height=300,
        template="plotly_white",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="x unified",
    )
    st.plotly_chart(fig, use_container_width=True)


# ============================================================
# 区块 5: 快速导航
# ============================================================

def _render_quick_links():
    """渲染快速导航卡片"""
    pages = [
        ("  市场全景", "大类资产实时行情、宏观观测、板块热度", "pages/1_市场全景.py"),
        ("  机会扫描", "门槛8% 扫描：过门槛候选 / 门槛附近 / 不碰", "pages/2_机会扫描.py"),
        ("  策略回测", "策略历史回测对比，生产=成长动量·个股", "pages/3_策略回测.py"),
        ("  复盘回顾", "双轨复盘：扫描准确性 + 策略跑赢率，按体制分组", "pages/4_复盘回顾.py"),
        ("  机构持仓", "13-F 季度持仓跟踪、增持信号检测、机构对比", "pages/6_机构持仓.py"),
    ]

    cols = st.columns(5)
    for col, (title, desc, page_path) in zip(cols, pages):
        with col:
            st.markdown(
                f'<div style="padding:1rem;background:#fafafa;border:1px solid #e0e0e0;'
                f'border-radius:0.5rem;min-height:100px;">'
                f'<div style="font-size:1rem;font-weight:600;margin-bottom:0.3rem;">{title}</div>'
                f'<div style="font-size:0.8rem;color:#666;">{desc}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
            st.page_link(page_path, label=title, use_container_width=True)


# ============================================================
# 启动自动刷新
# ============================================================

def _auto_refresh_data(dm: DataManager) -> bool:
    """启动时自动刷新全部数据。返回 True 表示执行了刷新（需要 rerun）。"""
    stats = dm.get_stats()
    if stats.empty:
        return False  # 数据库为空，P1 会处理

    last_date = stats["last_date"].max()
    days_old = (datetime.now() - datetime.strptime(last_date, "%Y-%m-%d")).days

    if days_old < 1:
        return False  # 数据新鲜

    if st.session_state.get("startup_refreshed"):
        return False  # 本次 session 已刷新

    st.session_state["startup_refreshed"] = True

    with st.spinner(f"正在自动刷新数据（{days_old} 天未更新）..."):
        # 并行获取市场数据
        dm.fetch_market_data_parallel()

        # 获取政治信号（增量）
        try:
            from core.data.signal_fetcher import SignalFetcher
            from core.data.signal_storage import PoliticalSignalStorage
            fetcher = SignalFetcher()
            signal_storage = PoliticalSignalStorage()
            signal_storage.ensure_seed_data()
            fetcher.fetch_incremental(signal_storage)
        except Exception:
            pass  # 信号获取失败不影响主流程

    st.toast("数据已自动刷新", icon="✅")
    return True


# ============================================================
# 风险预警横幅
# ============================================================

def _render_alert_banner():
    """渲染风险预警横幅"""
    try:
        from core.data.alert_storage import AlertStorage
        storage = AlertStorage()
        stats = storage.get_stats()

        if stats["unread"] == 0:
            return

        # 加载最近告警
        df = storage.load_recent(days=3)
        if df.empty:
            return

        # 按严重程度排序
        severity_order = {"critical": 0, "warning": 1, "info": 2}
        df["severity_rank"] = df["severity"].map(severity_order).fillna(2)
        df = df.sort_values("severity_rank")

        worst = df.iloc[0]["severity"]
        total = len(df)

        if worst == "critical":
            bg = "#ffebee"
            border = "#ef5350"
            color = "#c62828"
            icon = " "
        elif worst == "warning":
            bg = "#fff3e0"
            border = "#ff9800"
            color = "#e65100"
            icon = " "
        else:
            bg = "#e3f2fd"
            border = "#2196f3"
            color = "#1565c0"
            icon = " "

        # 横幅头部
        st.markdown(
            f'<div style="padding:0.8rem 1.2rem;background:{bg};border-left:5px solid {border};'
            f'border-radius:0 0.5rem 0.5rem 0;margin-bottom:0.5rem;">'
            f'<div style="display:flex;align-items:center;gap:0.5rem;">'
            f'<span style="font-size:1.2rem;">{icon}</span>'
            f'<span style="font-weight:bold;color:{color};">风险预警 ({total} 条未读)</span>'
            f'</div></div>',
            unsafe_allow_html=True,
        )

        # 前 3 条直接展示
        for _, row in df.head(3).iterrows():
            sev = row["severity"]
            sev_icon = " " if sev == "critical" else " " if sev == "warning" else " "
            st.markdown(f"{sev_icon} **{row['title']}** — {row['message'][:120]}")

        # 剩余条目可展开
        remaining = df.iloc[3:]
        if len(remaining) > 0:
            with st.expander(f"查看剩余 {len(remaining)} 条告警"):
                for _, row in remaining.iterrows():
                    sev = row["severity"]
                    sev_icon = " " if sev == "critical" else " " if sev == "warning" else " "
                    st.markdown(f"{sev_icon} **{row['title']}**")
                    st.caption(row["message"][:200])
                    st.divider()

        # 一键已读
        if st.button("标记全部已读", key="mark_alerts_read"):
            storage.mark_all_read()
            st.rerun()

    except Exception:
        pass  # 告警模块失败不影响主流程

    # 13-F 增持信号
    try:
        from core.data.thirteen_f_storage import ThirteenFStorage
        tf_storage = ThirteenFStorage()
        accumulation = tf_storage.get_accumulation_signals(lookback_quarters=4)
        if not accumulation.empty:
            top_signals = accumulation.head(5)
            signal_count = len(accumulation)

            st.markdown(
                f'<div style="padding:0.6rem 1rem;background:#e8f5e9;border-left:5px solid #4caf50;'
                f'border-radius:0 0.5rem 0.5rem 0;margin-bottom:0.5rem;">'
                f'<span style="font-weight:bold;color:#2e7d32;">  机构增持信号</span>'
                f'<span style="color:#555;margin-left:0.5rem;">'
                f'{signal_count} 个资产被多家机构连续增持</span>'
                f'</div>',
                unsafe_allow_html=True,
            )
            for _, row in top_signals.iterrows():
                managers_short = row['accumulating_managers']
                if len(managers_short) > 60:
                    managers_short = managers_short[:60] + "..."
                st.markdown(
                    f"  **{row['name_of_issuer']}** — "
                    f"{row['manager_count']} 家机构增持 ({managers_short})"
                )
    except Exception:
        pass  # 13-F 模块失败不影响主流程


# ============================================================
# 主函数
# ============================================================

def main():
    st.title("Market Pulse")
    st.caption("市场脉搏观测 — 体制判断 · 策略推荐 · 行动指导")

    dm = get_data_manager()

    # 启动自动刷新
    if _auto_refresh_data(dm):
        st.rerun()

    # 检查数据是否可用
    stats = dm.get_stats()
    db_empty = stats.empty

    if db_empty:
        st.warning("数据库为空，请先获取数据。")
        st.page_link("pages/1_市场全景.py", label="  前往市场全景获取数据", use_container_width=True)
        return

    # 数据陈旧检测
    last_date = stats["last_date"].max()
    days_old = (datetime.now() - datetime.strptime(last_date, "%Y-%m-%d")).days
    if days_old >= 7:
        st.error(f"  数据已 {days_old} 天未更新（最新: {last_date}），请在「市场全景」页面刷新")
    elif days_old >= 3:
        st.warning(f"  数据已 {days_old} 天未更新（最新: {last_date}），建议在「市场全景」页面刷新")

    # 风险预警横幅
    _render_alert_banner()

    # 区块 1: 决策面板（体制 + 推荐策略 + 行动资产）
    _render_decision_panel(dm)

    st.divider()

    # 区块 2: 关键资产快照
    st.subheader("  关键资产")
    _render_asset_snapshot(dm)

    st.divider()

    # 区块 3: 综合评分趋势
    _render_score_trend()

    st.divider()

    # 区块 4: 快速导航
    st.subheader("  功能导航")
    _render_quick_links()


if __name__ == "__main__":
    main()
