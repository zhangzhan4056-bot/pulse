#!/usr/bin/env python3
"""Market Pulse 每日数据同步脚本

拉取全量数据 → 运行扫描 → 导出 snapshot DB → 供 git 追踪

Usage:
    python scripts/daily_sync.py              # 完整同步
    python scripts/daily_sync.py --scan-only  # 只跑扫描（数据已是最新）
    python scripts/daily_sync.py --export-only # 只导出 snapshot

Cron (macOS):
    30 18 * * 1-5 cd /Users/zzzzzz/code_playground/market-pulse && /usr/bin/python3 scripts/daily_sync.py >> /tmp/mp-sync.log 2>&1
"""

import argparse
import logging
import re
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.data.config import US_SYMBOLS, CN_SYMBOLS, GLOBAL_SYMBOLS, US_SECTORS_SYMBOLS
from core.data.manager import DataManager
from core.data.macro_fetcher import MacroFetcher
from core.data.macro_storage import MacroStorage
from core.scanner.scorer import score_all_assets
from core.scanner.history import ScanHistory
from core.strategy.indicators import detect_market_regime, calc_regime_score

DB_PATH = PROJECT_ROOT / "data" / "market_pulse.db"
SNAPSHOT_PATH = PROJECT_ROOT / "data" / "market_pulse_snapshot.db"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def fetch_price_data(dm: DataManager) -> dict:
    """拉取所有价格数据"""
    logger.info("[1/4] 拉取价格数据...")

    results = {}

    # 美股大类资产（Twelve Data，限速 8 次/分钟）
    logger.info("  美股大类资产...")
    us_results = dm.fetch_us_assets(outputsize=30)
    results["us"] = us_results
    for sym, count in us_results.items():
        if isinstance(count, int):
            logger.info(f"    {sym}: {count} 条")

    # 美股板块 ETF
    logger.info("  美股板块 ETF...")
    sector_results = dm.fetch_us_sectors(outputsize=30)
    results["sectors"] = sector_results

    # A股指数（AkShare，无限速）
    logger.info("  A股指数...")
    cn_results = dm.fetch_cn_assets()
    results["cn"] = cn_results

    # 全球指数
    logger.info("  全球指数...")
    global_results = dm.fetch_global_assets()
    results["global"] = global_results

    return results


def fetch_macro_data() -> dict:
    """拉取宏观数据"""
    logger.info("[2/4] 拉取宏观数据...")
    macro_fetcher = MacroFetcher()
    macro_storage = MacroStorage()
    macro_data = macro_fetcher.fetch_all()
    results = macro_storage.save_all(macro_data)
    for indicator, count in results.items():
        if isinstance(count, int):
            logger.info(f"    {indicator}: {count} 条")
    return results


def run_scan(dm: DataManager) -> dict:
    """运行市场扫描并保存历史"""
    logger.info("[3/4] 运行市场扫描...")

    # 加载全部资产数据
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    all_symbols = {**US_SYMBOLS, **CN_SYMBOLS, **GLOBAL_SYMBOLS}
    data = {}
    for symbol in all_symbols:
        df = dm.load(symbol, start_date, end_date)
        if not df.empty:
            data[symbol] = df

    if not data:
        logger.warning("  无数据可扫描")
        return {}

    # 评分
    scores = score_all_assets(data)

    # 计算市场体制
    spy_close = data.get("SPY", __import__("pandas").DataFrame()).get("close", __import__("pandas").Series())
    regime_name = "中性"
    composite = 50.0
    if not spy_close.empty and len(spy_close) >= 60:
        rs = calc_regime_score(spy_close)
        composite = rs.get("composite", 50)
        tlt_close = data.get("TLT", __import__("pandas").DataFrame()).get("close", __import__("pandas").Series())
        cl_close = data.get("CL", __import__("pandas").DataFrame()).get("close", __import__("pandas").Series())
        if not tlt_close.empty and not cl_close.empty:
            regime_info = detect_market_regime(spy_close, tlt_close, cl_close)
            regime_name = regime_info.get("regime", "中性")

    # 保存扫描快照
    scan_history = ScanHistory()
    scan_history.save_snapshot(scores, regime_name, composite)

    # 统计
    opp_counts = {}
    for s in scores:
        opp_counts[s.opportunity] = opp_counts.get(s.opportunity, 0) + 1

    logger.info(f"  体制: {regime_name}, 综合评分: {composite:.0f}")
    logger.info(f"  趋势买入: {opp_counts.get('trend_buy', 0)}, "
                f"均值回归: {opp_counts.get('mean_reversion', 0)}, "
                f"规避: {opp_counts.get('avoid', 0)}")

    return {"regime": regime_name, "composite": composite, "scores_count": len(scores)}


def export_snapshot():
    """导出 DB 为 snapshot（VACUUM + 复制）"""
    logger.info("[4/4] 导出 DB snapshot...")

    if not DB_PATH.exists():
        logger.warning("  DB 文件不存在，跳过导出")
        return

    # VACUUM 压缩
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("VACUUM")
    except Exception as e:
        logger.warning(f"  VACUUM 失败: {e}")

    # 复制
    shutil.copy2(DB_PATH, SNAPSHOT_PATH)
    size_mb = SNAPSHOT_PATH.stat().st_size / (1024 * 1024)
    logger.info(f"  已导出: {SNAPSHOT_PATH} ({size_mb:.1f} MB)")


def parse_args():
    parser = argparse.ArgumentParser(description="Market Pulse 每日数据同步")
    parser.add_argument("--scan-only", action="store_true", help="只运行扫描，不拉取数据")
    parser.add_argument("--export-only", action="store_true", help="只导出 snapshot")
    return parser.parse_args()


def fetch_fund_data(dm: DataManager) -> dict:
    """拉取关注基金净值数据"""
    logger.info("[新增] 拉取基金净值数据...")
    try:
        results = dm.fetch_fund_data()
        for code, count in results.items():
            if isinstance(count, int):
                logger.info(f"    {code}: {count} 条")
            else:
                logger.warning(f"    {code}: {count}")
        return results
    except Exception as e:
        logger.error(f"基金数据拉取失败: {e}")
        return {}


def fetch_news_signals() -> int:
    """抓取行业新闻信号（含前兆信号）并存入数据库"""
    logger.info("[新增] 抓取行业新闻信号...")
    try:
        from core.data.signal_fetcher import NewsFetcher
        from core.data.signal_storage import PoliticalSignalStorage

        storage = PoliticalSignalStorage()
        fetcher = NewsFetcher()
        signals = fetcher.fetch_statements(days_back=3)

        if not signals:
            logger.info("  无新信号")
            return 0

        # 去重保存
        existing = storage.load_all()
        existing_keys = set()
        if not existing.empty:
            for _, row in existing.iterrows():
                key = (row["date"], row["speaker"], row.get("content", "")[:50])
                existing_keys.add(key)

        count = 0
        for signal in signals:
            key = (signal["date"], signal["speaker"], signal["content"][:50])
            if key not in existing_keys:
                storage.save(signal)
                count += 1

        precursor_count = sum(1 for s in signals if s.get("signal_type") == "precursor")
        logger.info(f"  新增 {count} 条信号（含 {precursor_count} 条前兆信号）")
        return count
    except Exception as e:
        logger.error(f"新闻信号抓取失败: {e}")
        return 0


def fetch_13f_data() -> dict:
    """检查 13-F 机构持仓更新"""
    logger.info("[新增] 检查 13-F 机构持仓更新...")
    try:
        from core.data.thirteen_f_storage import ThirteenFStorage
        from core.data.thirteen_f_fetcher import ThirteenFFetcher

        storage = ThirteenFStorage()
        storage.seed_default_managers()

        fetcher = ThirteenFFetcher()
        result = fetcher.fetch_and_store_new(storage)

        logger.info(
            f"  13-F: {result['new_filings']} 份新报告, "
            f"{result['new_holdings']} 条持仓, "
            f"{result['errors']} 个错误"
        )

        # 有新报告时飞书通知（含增持信号）
        if result.get("new_filings", 0) > 0:
            _send_13f_notification(storage, result)

        return result
    except Exception as e:
        logger.error(f"13-F 数据获取失败: {e}")
        return {"new_filings": 0, "new_holdings": 0, "errors": 1}


def _classify_sector(name: str) -> str:
    """根据 issuer 名称推断行业板块"""
    name_lower = name.lower()
    rules = [
        ("科技/半导体", [
            "apple", "microsoft", "nvidia", "amd", "intel", "qualcomm",
            "broadcom", "micron", "applied", "lam research", "asml",
            "synopsys", "cadence", "arm holdings", "tsmc", "taiwan semi",
            "meta", "alphabet", "google", "amazon", "salesforce", "adobe",
            "servicenow", "intuit", "palantir", "snowflake", "datadog",
            "crowdstrike", "palo alto", "fortinet", "zynga", "paypal",
            "stripe", "shopify", "cloudflare", "uber", "lyft", "airbnb",
            "spotify", "snap", "pinterest", "robinhood", "coinbase",
            "microchip", "on semiconductor", "marvell", "western digital",
            "seagate", "netapp", "pure storage", "mongodb", "elastic",
            "semiconductor", "chip", "nxp", "infineon", "texas instrument",
            "analog device", "analog devices", "maxim", "xilinx", "altera",
        ]),
        ("金融/银行", [
            "bank", "jpmorgan", "jp morgan", "goldman", "morgan stanley",
            "citigroup", "wells fargo", "bank of america", "berkshire",
            "visa", "mastercard", "american express", "charles schwab",
            "blackrock", "state street", "vanguard", "fidelity",
            "morgan", "goldman sachs", "ubs", "credit suisse", "barclays",
            "hsbc", "deutsche bank", "ing", "aig", "metlife", "prudential",
            "allstate", "progressive", "travelers", "loews", "aon", "marsh",
            "willis", "chubb", "axis capital", "renaissance re", "everest",
        ]),
        ("医疗/生物", [
            "pharma", "pfizer", "johnson", "merck", "abbvie", "amgen",
            "gilead", "regeneron", "vertex", "biogen", "moderna", "biontech",
            "illumina", "intuitive", "edwards", "medtronic", "abbott",
            "boston scientific", "stryker", "zimmer", "biomet", "danaher",
            "thermo fisher", "agilent", "waters", "perkinelmer", "bio-rad",
            "bio techne", "seattle genetics", "alkermes", "biomarin",
            "incyte", "alexion", "ultragenyx", "sarepta", "bluebird",
            "health", "medical", "hospital", "clinic", "unitedhealth",
            "anthem", "cigna", "humana", "centene", "molina",
        ]),
        ("能源", [
            "exxon", "chevron", "conocophillips", "schlumberger", "halliburton",
            "baker hughes", "occidental", "pioneer", "devon", "marathon",
            "valero", "phillips 66", "hess", "coterra", "diamondback",
            "energy", "oil", "gas", "petroleum", "solar", "wind", "renewable",
            "first solar", "sunpower", "enphase", "solar edge",
        ]),
        ("消费", [
            "walmart", "costco", "target", "home depot", "lowe", "mcdonald",
            "starbucks", "nike", "lululemon", "tjx", "ross stores", "kohl",
            "macy", "nordstrom", "gap", "ralph lauren", "pvh", "tapestry",
            "capri", "estee lauder", "procter", "gamble", "coca cola",
            "pepsi", "monster", "keurig", "general mills", "kellogg",
            "kraft", "mondelez", "hershey", "tyson", "archer daniels",
            "bunge", "deere", "caterpillar", "consumer", "retail",
        ]),
        ("工业/制造", [
            "boeing", "lockheed", "raytheon", "northrop", "general electric",
            "honeywell", "3m", "illinois tool", "parker hannifin", "eaton",
            "emerson", "rockwell", "ametek", "fortive", "roper", "dover",
            "ingredion", "graco", "nordson", "idex", "xylem", "pentair",
            "industrial", "aerospace", "defense", "manufacturing",
        ]),
        ("通信/媒体", [
            "disney", "netflix", "comcast", "at&t", "verizon", "t-mobile",
            "tmus", "charter", "viacom", "paramount", "warner", "discovery",
            "fox", "news corp", "new york times", "gannett", "telecom",
            "communication", "media",
        ]),
        ("地产/REIT", [
            "real estate", "reit", "simon property", "american tower",
            "crown castle", "prologis", "equinix", "digital realty",
            "public storage", "extra space", "avidity", "welltower",
            "ventas", "healthpeak", "essex", "avalonbay", "equity residential",
            "udr", "camden", "mid-america apartment", "essex property",
        ]),
        ("材料/化工", [
            "dupont", "dow", "lyondellbasell", "air products", "linde",
            "ecolab", "sherwin", "ppg", "eastman", "celanese", "albemarle",
            "fmc", "chemours", "avient", "material", "chemical", "mining",
        ]),
        ("公用事业", [
            "nextera", "dominion", "duke energy", "southern company",
            "american electric", "xcel energy", "wec energy", "eversource",
            "avangrid", "utility", "electric", "power",
        ]),
    ]
    for sector, keywords in rules:
        if any(re.search(r'\b' + re.escape(kw) + r'\b', name_lower) for kw in keywords):
            return sector
    return ""


def _send_13f_notification(storage, result: dict):
    """发送 13-F 更新通知到飞书（结构化卡片 + SEC 链接）"""
    from core.data.notifier import FeishuNotifier
    notifier = FeishuNotifier()
    if not notifier.is_configured():
        return

    elements = []

    # ── 1. 概览区 ──
    summary_lines = [
        f"**新报告:** {result['new_filings']} 份 | "
        f"**新增持仓:** {result['new_holdings']} 条",
    ]
    for m in result.get("updated_managers", []):
        cik = m["cik"].lstrip("0")
        sec_url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=13F-HR"
        summary_lines.append(
            f"- [{m['name']}]({sec_url}) — "
            f"{m['period_end']} · {m['holdings_count']} 只持仓"
        )
    elements.append({"tag": "markdown", "content": "\n".join(summary_lines)})

    # ── 统一数据源：季度信号 ──
    quarterly = storage.get_quarterly_signals()
    # curr_acc/red = 本季度变动（Q-1→Q0，最新一份13F vs 上一份）
    # prev_acc/red = 上季度变动（Q-2→Q-1，上一份 vs 上上一份）
    curr_acc = quarterly.get("curr_acc", pd.DataFrame())
    curr_red = quarterly.get("curr_red", pd.DataFrame())
    prev_acc = quarterly.get("prev_acc", pd.DataFrame())
    prev_red = quarterly.get("prev_red", pd.DataFrame())
    curr_to = quarterly.get("curr_to", "")  # 本季度 period_end
    prev_to = quarterly.get("prev_to", "")  # 上季度 period_end

    # 价值型机构关键词
    value_kw = [
        "Berkshire", "Bridgewater", "Pershing", "Tiger Global",
        "Coatue", "Third Point", "Baupost", "Lone Pine",
        "Appaloosa", "Soros", "Viking", "Icahn", "Elliott",
    ]

    def is_value_manager(mgr_str):
        return any(kw.lower() in mgr_str.lower() for kw in value_kw)

    # ── 2. 资金流向分析 ──
    try:
        if quarterly:

            def _fmt_usd(val):
                """格式化美元金额：$1.2B / $350M / -$50M"""
                if val == 0:
                    return "$0"
                sign = "+" if val > 0 else "-"
                av = abs(val)
                if av >= 1e9:
                    return f"{sign}${av / 1e9:.1f}B"
                if av >= 1e6:
                    return f"{sign}${av / 1e6:.0f}M"
                return f"{sign}${av / 1e3:.0f}K"

            def _build_sector_stats(acc_df, red_df):
                """从信号 DataFrame 构建板块统计"""
                stats = {}
                def _ensure(s):
                    if s not in stats:
                        stats[s] = {
                            "acc_count": 0, "red_count": 0,
                            "acc_value": 0, "red_value": 0,
                            "vm_acc": 0, "vm_red": 0,
                            "acc_issuers": set(), "red_issuers": set(),
                        }
                if not acc_df.empty:
                    for _, r in acc_df.iterrows():
                        sec = _classify_sector(r["name_of_issuer"])
                        if not sec:
                            continue
                        _ensure(sec)
                        d = stats[sec]
                        d["acc_count"] += 1
                        d["acc_value"] += r.get("total_value_change", 0)
                        d["acc_issuers"].add(r["name_of_issuer"])
                        if is_value_manager(r["accumulating_managers"]):
                            d["vm_acc"] += 1
                if not red_df.empty:
                    for _, r in red_df.iterrows():
                        sec = _classify_sector(r["name_of_issuer"])
                        if not sec:
                            continue
                        _ensure(sec)
                        d = stats[sec]
                        d["red_count"] += 1
                        d["red_value"] += abs(r.get("total_value_change", 0))
                        d["red_issuers"].add(r["name_of_issuer"])
                        if is_value_manager(r.get("reducing_managers", "")):
                            d["vm_red"] += 1
                return stats

            # 本季度变动（Q-1→Q0）
            curr_stats = _build_sector_stats(curr_acc, curr_red)
            # 上季度变动（Q-2→Q-1）
            prev_stats = _build_sector_stats(prev_acc, prev_red)

            if curr_stats:

                def _net_val(d):
                    return d["acc_value"] - d["red_value"]

                sorted_sectors = sorted(
                    curr_stats.items(),
                    key=lambda x: abs(_net_val(x[1])),
                    reverse=True,
                )

                flow_lines = [
                    f"**  资金流向分析**"
                ]

                # ── 1. 板块明细表（本季度 + 上季度净金额） ──
                table_lines = [
                    f"板块 | 本季度({curr_to}) | 上季度({prev_to})"
                ]
                table_lines.append(
                    "---- | ---- | ----"
                )
                hedging_sectors = set()
                for sector, d in sorted_sectors:
                    net = d["acc_count"] - d["red_count"]
                    max_c = max(d["acc_count"], d["red_count"])
                    net_ratio = abs(net) / max_c if max_c > 0 else 0
                    vm_diverge = (d["vm_acc"] > 0 and d["vm_red"] > 0
                                  and abs(d["vm_acc"] - d["vm_red"]) >= 3)
                    if abs(net) < 3 and net_ratio < 0.25 and not vm_diverge:
                        hedging_sectors.add(sector)
                        continue

                    curr_nv = _net_val(d)
                    p = prev_stats.get(sector)
                    prev_nv = _net_val(p) if p else 0

                    table_lines.append(
                        f"{sector} | {_fmt_usd(curr_nv)} | {_fmt_usd(prev_nv)}"
                    )
                if len(table_lines) > 2:
                    flow_lines.append("\n".join(table_lines))
                if hedging_sectors:
                    flow_lines.append(
                        f"  对冲板块（增持≈减持，忽略）: {', '.join(hedging_sectors)}"
                    )

                # ── 2. 价值型机构共识判断 ──
                vm_consensus = []
                vm_diverge_list = []
                for sector, d in curr_stats.items():
                    if d["vm_acc"] == 0 and d["vm_red"] == 0:
                        continue
                    net_vm = d["vm_acc"] - d["vm_red"]
                    net_all = d["acc_count"] - d["red_count"]
                    net_val = _net_val(d)
                    if net_vm == 0:
                        continue
                    vm_dir = "增持" if net_vm > 0 else "减持"
                    all_dir = "增持" if net_all > 0 else "减持" if net_all < 0 else "平衡"
                    if all_dir == "平衡":
                        vm_diverge_list.append((sector, vm_dir, "平衡", abs(net_vm), net_val))
                    elif (vm_dir == "增持" and all_dir == "增持") or \
                         (vm_dir == "减持" and all_dir == "减持"):
                        vm_consensus.append((sector, vm_dir, abs(net_vm), net_val))
                    else:
                        vm_diverge_list.append((sector, vm_dir, all_dir, abs(net_vm), net_val))

                if vm_consensus:
                    vm_consensus.sort(key=lambda x: x[2], reverse=True)
                    parts = [
                        f"{s}({d}，{_fmt_usd(v)})"
                        for s, d, _, v in vm_consensus[:5]
                    ]
                    flow_lines.append(
                        f"**价值型机构共识（与全市场同向）:** {', '.join(parts)}"
                    )

                if vm_diverge_list:
                    vm_diverge_list.sort(key=lambda x: x[3], reverse=True)
                    parts = [
                        f"{s}(价值型{vm}，全市场{all}，{_fmt_usd(v)})"
                        for s, vm, all, _, v in vm_diverge_list[:5]
                    ]
                    flow_lines.append(
                        f"**⚠️ 价值型与市场分歧（重点关注）:** {', '.join(parts)}"
                    )

                # ── 3. 集中度判断 ──
                inflow_sectors = [
                    (s, d) for s, d in sorted_sectors
                    if s not in hedging_sectors and _net_val(d) > 0
                ]
                concentration_notes = []
                for sector, d in inflow_sectors[:3]:
                    net = d["acc_count"] - d["red_count"]
                    n_issuers = len(d["acc_issuers"])
                    if net >= 3 and n_issuers <= net * 0.5:
                        top_names = sorted(d["acc_issuers"])[:3]
                        concentration_notes.append(
                            f"{sector}: {n_issuers}个标的获{d['acc_count']}次增持，"
                            f"集中在 {', '.join(top_names)}"
                        )
                    elif net >= 5 and n_issuers > net * 0.8:
                        concentration_notes.append(
                            f"{sector}: {n_issuers}个标的分散增持，板块级看多信号"
                        )
                if concentration_notes:
                    flow_lines.append(
                        "**集中度:** " + "；".join(concentration_notes)
                    )

                # ── 4. 整体趋势叙述 ──
                real_inflows = [
                    (s, d) for s, d in sorted_sectors
                    if s not in hedging_sectors and _net_val(d) > 0
                ]
                real_outflows = [
                    (s, d) for s, d in sorted_sectors
                    if s not in hedging_sectors and _net_val(d) < 0
                ]
                if real_inflows and real_outflows:
                    top_in_s, top_in_d = real_inflows[0]
                    top_out_s, top_out_d = real_outflows[0]
                    flow_lines.append(
                        f"**整体趋势:** 资金从{top_out_s}"
                        f"({_fmt_usd(_net_val(top_out_d))}) "
                        f"流向{top_in_s}({_fmt_usd(_net_val(top_in_d))})"
                    )
                elif real_inflows:
                    top_s, top_d = real_inflows[0]
                    flow_lines.append(
                        f"**整体趋势:** 多板块净流入，"
                        f"{top_s}({_fmt_usd(_net_val(top_d))})最显著"
                    )

                elements.append({"tag": "markdown", "content": "\n".join(flow_lines)})

    except Exception as e:
        logger.error(f"资金流向分析失败: {e}")

    # ── 3. AI 产业链重点信号 ──
    try:
        # 短关键词用词边界，长关键词用子串匹配
        ai_keywords_long = [
            "nvidia", "broadcom", "micron", "marvell",
            "applied materials",
            "lam research", "asml", "synopsys", "arm holdings",
            "tsmc", "taiwan semi", "on semiconductor", "microchip",
            "qualcomm", "western digital", "seagate", "netapp", "pure storage",
            "coherent", "lumentum", "fabrinet", "innolight",
            "arista", "juniper networks", "ciena", "mellanox",
            "super micro", "hewlett packard",
            "snowflake", "datadog", "palantir", "mongodb",
            "crowdstrike", "palo alto", "cloudflare",
            "servicenow", "intuit", "salesforce", "adobe",
            "cadence design", "cadence sys", "keysight",
        ]
        ai_keywords_short = [
            "amd", "intel", "smci", "dell", "ansys", "elastic",
        ]

        def is_ai_chain(name):
            n = name.lower()
            if any(kw in n for kw in ai_keywords_long):
                return True
            return any(re.search(r'\b' + re.escape(kw) + r'\b', n) for kw in ai_keywords_short)

        ai_acc = curr_acc[curr_acc["name_of_issuer"].apply(is_ai_chain)] if not curr_acc.empty else pd.DataFrame()
        ai_red = curr_red[curr_red["name_of_issuer"].apply(is_ai_chain)] if not curr_red.empty else pd.DataFrame()

        if not ai_acc.empty or not ai_red.empty:
            elements.append({"tag": "hr"})
            ai_lines = [
                f"**  AI 产业链重点信号** "
                f"(增持 {len(ai_acc)} / 减持 {len(ai_red)})"
            ]

            # AI 增持
            if not ai_acc.empty:
                ai_lines.append("**⬆️ 增持:**")
                for _, row in ai_acc.head(15).iterrows():
                    sec_url = (
                        f"https://www.sec.gov/cgi-bin/browse-edgar"
                        f"?action=getcompany&CIK=&type=13F-HR"
                        f"&company={row['name_of_issuer'].replace(' ', '+')}"
                    )
                    mgrs = row["accumulating_managers"]
                    if len(mgrs) > 50:
                        mgrs = mgrs[:50] + "..."
                    ai_lines.append(
                        f"- [{row['name_of_issuer']}]({sec_url}) — "
                        f"**{row['manager_count']}家**增持 · {mgrs}"
                    )
                if len(ai_acc) > 15:
                    ai_lines.append(f"- ...及其他 {len(ai_acc) - 15} 个")

            # AI 减持
            if not ai_red.empty:
                ai_lines.append("**⬇️ 减持:**")
                for _, row in ai_red.head(15).iterrows():
                    sec_url = (
                        f"https://www.sec.gov/cgi-bin/browse-edgar"
                        f"?action=getcompany&CIK=&type=13F-HR"
                        f"&company={row['name_of_issuer'].replace(' ', '+')}"
                    )
                    mgrs = row["reducing_managers"]
                    if len(mgrs) > 50:
                        mgrs = mgrs[:50] + "..."
                    ai_lines.append(
                        f"- [{row['name_of_issuer']}]({sec_url}) — "
                        f"**{row['manager_count']}家**减持 · {mgrs}"
                    )
                if len(ai_red) > 15:
                    ai_lines.append(f"- ...及其他 {len(ai_red) - 15} 个")

            elements.append({
                "tag": "markdown",
                "content": "\n".join(ai_lines),
            })
    except Exception as e:
        logger.error(f"AI 产业链信号加载失败: {e}")

    # ── 4. 增持信号明细 ──
    try:
        if not curr_acc.empty:
            # 筛选价值型机构信号
            value_mask = curr_acc["accumulating_managers"].apply(is_value_manager)
            value_signals = curr_acc[value_mask]

            if not value_signals.empty:
                elements.append({"tag": "hr"})
                v_lines = [
                    f"**  价值型机构增持信号** ({len(value_signals)} 个)"
                ]
                for _, row in value_signals.head(10).iterrows():
                    sec_url = (
                        f"https://www.sec.gov/cgi-bin/browse-edgar"
                        f"?action=getcompany&CIK=&type=13F-HR"
                        f"&company={row['name_of_issuer'].replace(' ', '+')}"
                    )
                    mgrs = row["accumulating_managers"]
                    if len(mgrs) > 60:
                        mgrs = mgrs[:60] + "..."
                    v_lines.append(
                        f"- [{row['name_of_issuer']}]({sec_url}) — "
                        f"**{row['manager_count']}家**增持 · {mgrs}"
                    )
                if len(value_signals) > 10:
                    v_lines.append(
                        f"- ...及其他 {len(value_signals) - 10} 个资产"
                    )
                elements.append({
                    "tag": "markdown",
                    "content": "\n".join(v_lines),
                })

            # 全量增持信号
            if len(curr_acc) > len(value_signals):
                elements.append({"tag": "hr"})
                a_lines = [
                    f"**  全部增持信号** ({len(curr_acc)} 个，含量化基金)"
                ]
                for _, row in curr_acc.head(10).iterrows():
                    a_lines.append(
                        f"- {row['name_of_issuer']} · "
                        f"{row['manager_count']}家增持"
                    )
                if len(curr_acc) > 10:
                    a_lines.append(
                        f"- ...及其他 {len(curr_acc) - 10} 个"
                    )
                elements.append({
                    "tag": "markdown",
                    "content": "\n".join(a_lines),
                })
    except Exception as e:
        logger.error(f"获取增持信号失败: {e}")
        elements.append({"tag": "hr"})
        elements.append({
            "tag": "markdown",
            "content": f"⚠️ 增持信号加载失败: {e}",
        })

    # ── 5. 减持信号明细 ──
    try:
        if not curr_red.empty:
            red_value_mask = curr_red["reducing_managers"].apply(is_value_manager)
            red_value_signals = curr_red[red_value_mask]

            if not red_value_signals.empty:
                elements.append({"tag": "hr"})
                rv_lines = [
                    f"**  价值型机构减持信号** ({len(red_value_signals)} 个)"
                ]
                for _, row in red_value_signals.head(10).iterrows():
                    sec_url = (
                        f"https://www.sec.gov/cgi-bin/browse-edgar"
                        f"?action=getcompany&CIK=&type=13F-HR"
                        f"&company={row['name_of_issuer'].replace(' ', '+')}"
                    )
                    mgrs = row["reducing_managers"]
                    if len(mgrs) > 60:
                        mgrs = mgrs[:60] + "..."
                    rv_lines.append(
                        f"- [{row['name_of_issuer']}]({sec_url}) — "
                        f"**{row['manager_count']}家**减持 · {mgrs}"
                    )
                if len(red_value_signals) > 10:
                    rv_lines.append(
                        f"- ...及其他 {len(red_value_signals) - 10} 个资产"
                    )
                elements.append({
                    "tag": "markdown",
                    "content": "\n".join(rv_lines),
                })

            # 全量减持信号
            if len(curr_red) > len(red_value_signals):
                elements.append({"tag": "hr"})
                ra_lines = [
                    f"**  全部减持信号** ({len(curr_red)} 个，含量化基金)"
                ]
                for _, row in curr_red.head(10).iterrows():
                    ra_lines.append(
                        f"- {row['name_of_issuer']} · "
                        f"{row['manager_count']}家减持"
                    )
                if len(curr_red) > 10:
                    ra_lines.append(
                        f"- ...及其他 {len(curr_red) - 10} 个"
                    )
                elements.append({
                    "tag": "markdown",
                    "content": "\n".join(ra_lines),
                })
    except Exception as e:
        logger.error(f"获取减持信号失败: {e}")

    # ── 5. 底部 ──
    elements.append({"tag": "hr"})
    elements.append({
        "tag": "markdown",
        "content": "打开本地 Market Pulse → 机构持仓页查看详情",
    })

    # ── 发送 ──
    ok = notifier.send_card(
        title=f"  13-F 更新：{result['new_filings']} 家机构提交新报告",
        elements=elements,
        severity="info",
    )
    if ok:
        logger.info("  飞书通知已发送")
    else:
        logger.warning("  飞书通知发送失败")


def evaluate_alerts(dm: DataManager) -> list:
    """评估告警并推送"""
    logger.info("[新增] 评估风险告警...")
    try:
        from core.data.alert_evaluator import AlertEvaluator
        from core.data.alert_storage import AlertStorage
        from core.data.notifier import FeishuNotifier

        evaluator = AlertEvaluator()
        alerts = evaluator.evaluate(dm)

        if not alerts:
            logger.info("  无告警")
            return []

        # 保存到数据库（去重：同类型+同标的 12 小时内不重复）
        storage = AlertStorage()
        saved = 0
        for a in alerts:
            rid = storage.save_dedup(a, dedup_hours=12)
            if rid:
                saved += 1
        logger.info(f"  保存 {saved} 条告警（去重后）")

        # 飞书推送
        notifier = FeishuNotifier()
        if notifier.is_configured():
            sent = notifier.send_alert_batch(alerts)
            logger.info(f"  飞书推送 {sent} 条")
        else:
            logger.info("  飞书 Webhook 未配置，跳过推送")

        return alerts
    except Exception as e:
        logger.error(f"告警评估失败: {e}")
        return []


def main():
    args = parse_args()
    start_time = time.time()

    logger.info("=== Market Pulse 每日同步 ===")

    if args.export_only:
        export_snapshot()
        elapsed = time.time() - start_time
        logger.info(f"完成，耗时 {elapsed:.0f} 秒")
        return

    dm = DataManager()

    if not args.scan_only:
        # 拉取价格数据
        price_results = fetch_price_data(dm)
        # 拉取宏观数据
        macro_results = fetch_macro_data()
        # 拉取基金净值
        fund_results = fetch_fund_data(dm)

    # 运行扫描
    scan_results = run_scan(dm)

    # 抓取行业新闻信号（含前兆信号）
    news_count = fetch_news_signals()

    # 检查 13-F 机构持仓更新
    thirteen_f_results = fetch_13f_data()

    # 评估告警并推送
    alerts = evaluate_alerts(dm)

    # 导出 snapshot
    export_snapshot()

    elapsed = time.time() - start_time
    logger.info(f"=== 完成，耗时 {elapsed:.0f} 秒 ===")


if __name__ == "__main__":
    main()
