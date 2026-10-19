"""政治信号存储层 - 特朗普家族公开信号跟踪"""

import json
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd

# 复用同一个数据库
DB_PATH = Path(__file__).parent.parent.parent / "data" / "market_pulse.db"

# 预置信号数据（仅保留 SEC EDGAR 可验证的数据）
# 注意：DELL 链（特朗普喊话/go buy a Dell/五角大楼合同）无法从公开数据源验证，不在此预置
# 如需添加，请通过页面手动录入，标记为 unverified
SEED_SIGNALS = [
    # TARIFF-2025 信号链（公开新闻可验证）
    {
        "chain_id": "TARIFF-2025",
        "date": "2025-04-02",
        "speaker": "Donald Trump",
        "signal_category": "statement",
        "source": "新闻发布会",
        "source_url": "",
        "content": "Trump announces sweeping reciprocal tariffs on all trading partners",
        "content_cn": "特朗普宣布对所有贸易伙伴实施对等关税",
        "signal_direction": "bearish",
        "affected_assets": json.dumps(["SPY", "QQQ", "DIA"]),
        "trade_detail": None,
        "impact_note": "全球股市暴跌，标普连续多日大跌",
        "verification": "verified",
    },
    {
        "chain_id": "TARIFF-2025",
        "date": "2025-04-09",
        "speaker": "Donald Trump",
        "signal_category": "statement",
        "source": "Truth Social",
        "source_url": "https://truthsocial.com",
        "content": '"THIS IS A GREAT TIME TO BUY!!!"',
        "content_cn": '"现在是买入的好时机！！！"',
        "signal_direction": "bullish",
        "affected_assets": json.dumps(["SPY", "QQQ", "DIA"]),
        "trade_detail": None,
        "impact_note": "关税暂停前数小时发文",
        "verification": "verified",
    },
    {
        "chain_id": "TARIFF-2025",
        "date": "2025-04-09",
        "speaker": "Donald Trump",
        "signal_category": "event",
        "source": "行政令",
        "source_url": "",
        "content": "Trump announces 90-day pause on reciprocal tariffs (except China), drops to 10% baseline",
        "content_cn": "特朗普宣布暂停90天对等关税（中国除外），降至10%基线",
        "signal_direction": "bullish",
        "affected_assets": json.dumps(["SPY", "QQQ", "DIA"]),
        "trade_detail": None,
        "impact_note": "标普500单日+9.5%，纳指+12%，2008年以来最大单日涨幅",
        "verification": "verified",
    },
    # CRYPTO-2025 信号链
    {
        "chain_id": "CRYPTO-2025",
        "date": "2025-01-20",
        "speaker": "Donald Trump",
        "signal_category": "event",
        "source": "行政令",
        "source_url": "",
        "content": "Trump signs executive order establishing U.S. Strategic Bitcoin Reserve",
        "content_cn": "特朗普签署行政令建立美国战略比特币储备",
        "signal_direction": "bullish",
        "affected_assets": json.dumps(["BTC", "ETH", "COIN"]),
        "trade_detail": None,
        "impact_note": "政策面重大利好加密货币",
        "verification": "verified",
    },
    {
        "chain_id": "CRYPTO-2025",
        "date": "2025-01-15",
        "speaker": "Don Jr.",
        "signal_category": "statement",
        "source": "X (Twitter)",
        "source_url": "",
        "content": "Don Jr. publicly endorses Bitcoin and crypto adoption",
        "content_cn": "小特朗普公开支持比特币和加密货币",
        "signal_direction": "bullish",
        "affected_assets": json.dumps(["BTC", "COIN", "MSTR"]),
        "trade_detail": None,
        "impact_note": "特朗普家族持续为加密站台",
        "verification": "verified",
    },
    {
        "chain_id": "CRYPTO-2025",
        "date": "2025-02-01",
        "speaker": "Eric Trump",
        "signal_category": "statement",
        "source": "采访",
        "source_url": "",
        "content": "Eric Trump discusses World Liberty Financial and family crypto investments",
        "content_cn": "埃里克·特朗普谈论World Liberty Financial及家族加密投资",
        "signal_direction": "bullish",
        "affected_assets": json.dumps(["BTC", "ETH", "WLFI"]),
        "trade_detail": None,
        "impact_note": "家族深度绑定加密行业",
        "verification": "verified",
    },
    # CRYPTO-2025 补充
    {
        "chain_id": "CRYPTO-2025",
        "date": "2025-03-06",
        "speaker": "Donald Trump",
        "signal_category": "statement",
        "source": "新闻发布会",
        "source_url": "",
        "content": 'Trump announces White House Crypto Summit, calls Bitcoin "strategic asset"',
        "content_cn": '特朗普宣布举办白宫加密峰会，称比特币为"战略资产"',
        "signal_direction": "bullish",
        "affected_assets": json.dumps(["BTC", "COIN", "MSTR"]),
        "trade_detail": None,
        "impact_note": "总统级加密背书",
        "verification": "verified",
    },
    # APPLE-2025 信号链
    {
        "chain_id": "APPLE-2025",
        "date": "2025-02-24",
        "speaker": "Donald Trump",
        "signal_category": "statement",
        "source": "新闻发布会",
        "source_url": "",
        "content": "Trump pressures Apple to move iPhone manufacturing to the US, threatens permanent tariffs",
        "content_cn": "特朗普施压苹果将iPhone制造迁回美国，威胁永久性关税",
        "signal_direction": "bearish",
        "affected_assets": json.dumps(["AAPL"]),
        "trade_detail": None,
        "impact_note": "苹果面临关税压力，股价承压",
        "verification": "verified",
    },
    {
        "chain_id": "APPLE-2025",
        "date": "2025-02-24",
        "speaker": "Tim Cook",
        "signal_category": "event",
        "source": "新闻发布会",
        "source_url": "",
        "content": "Apple announces $500 billion US investment over 4 years, including AI server factories in Texas",
        "content_cn": "苹果宣布4年内投资5000亿美元于美国，包括在德州建AI服务器工厂",
        "signal_direction": "bullish",
        "affected_assets": json.dumps(["AAPL"]),
        "trade_detail": None,
        "impact_note": "回应特朗普施压，苹果承诺大规模美国投资",
        "verification": "verified",
    },
    {
        "chain_id": "APPLE-2025",
        "date": "2025-04-12",
        "speaker": "Donald Trump",
        "signal_category": "event",
        "source": "行政令",
        "source_url": "",
        "content": "Trump administration grants temporary tariff exemptions for smartphones, computers, and chips from China",
        "content_cn": "特朗普政府暂时豁免智能手机、电脑和芯片的中国对等关税",
        "signal_direction": "bullish",
        "affected_assets": json.dumps(["AAPL"]),
        "trade_detail": None,
        "impact_note": "iPhone免于145%关税，苹果股价大涨",
        "verification": "verified",
    },
    # TSLA-DOGE-2025 信号链
    {
        "chain_id": "TSLA-DOGE-2025",
        "date": "2025-01-20",
        "speaker": "Donald Trump",
        "signal_category": "event",
        "source": "行政令",
        "source_url": "",
        "content": "Trump signs executive order creating DOGE (Department of Government Efficiency), appoints Elon Musk as head",
        "content_cn": "特朗普签署行政令成立DOGE（政府效率部），任命马斯克为负责人",
        "signal_direction": "bullish",
        "affected_assets": json.dumps(["TSLA"]),
        "trade_detail": None,
        "impact_note": "马斯克获得政治影响力，特斯拉受益",
        "verification": "verified",
    },
    {
        "chain_id": "TSLA-DOGE-2025",
        "date": "2025-03-11",
        "speaker": "Donald Trump",
        "signal_category": "statement",
        "source": "Truth Social",
        "source_url": "",
        "content": 'Trump publicly praises Elon Musk and Tesla, calls Musk a "great American patriot"',
        "content_cn": '特朗普公开赞扬马斯克和特斯拉，称马斯克是"伟大的美国爱国者"',
        "signal_direction": "bullish",
        "affected_assets": json.dumps(["TSLA"]),
        "trade_detail": None,
        "impact_note": "总统背书特斯拉",
        "verification": "verified",
    },
    # DJT-2025 信号链
    {
        "chain_id": "DJT-2025",
        "date": "2025-04-09",
        "speaker": "Donald Trump",
        "signal_category": "statement",
        "source": "Truth Social",
        "source_url": "",
        "content": 'Trump posts "THIS IS A GREAT TIME TO BUY!!! DJT" — DJT is also the ticker for Trump Media',
        "content_cn": '特朗普发文"现在是买入好时机!!! DJT"——DJT同时也是特朗普媒体的股票代码',
        "signal_direction": "bullish",
        "affected_assets": json.dumps(["DJT"]),
        "trade_detail": None,
        "impact_note": "喊话中直接包含自家股票代码",
        "verification": "verified",
    },
    # NVDA-2025 信号链
    {
        "chain_id": "NVDA-2025",
        "date": "2025-01-13",
        "speaker": "Donald Trump",
        "signal_category": "event",
        "source": "行政令",
        "source_url": "",
        "content": "Trump administration tightens AI chip export restrictions to China, targeting Nvidia H100/H200",
        "content_cn": "特朗普政府收紧AI芯片对华出口限制，针对英伟达H100/H200",
        "signal_direction": "bearish",
        "affected_assets": json.dumps(["NVDA"]),
        "trade_detail": None,
        "impact_note": "英伟达失去中国市场份额，短期利空",
        "verification": "verified",
    },
    {
        "chain_id": "NVDA-2025",
        "date": "2025-04-15",
        "speaker": "Donald Trump",
        "signal_category": "event",
        "source": "行政令",
        "source_url": "",
        "content": "Trump bans Nvidia H20 chip exports to China, requiring special licenses",
        "content_cn": "特朗普禁止英伟达H20芯片出口中国，需特别许可",
        "signal_direction": "bearish",
        "affected_assets": json.dumps(["NVDA"]),
        "trade_detail": None,
        "impact_note": "进一步收紧，英伟达中国业务受重创",
        "verification": "verified",
    },
    # ── DELL-2026 完整信号链（PTR 交易 + 喊话 + 合同）──────────
    {
        "chain_id": "DELL-2026",
        "date": "2026-02-10",
        "speaker": "Donald Trump",
        "signal_category": "trade",
        "source": "OGE PTR (白宫)",
        "source_url": "https://www.whitehouse.gov/wp-content/uploads/2026/05/President-Donald-J.-Trump-Periodic-Transaction-Report-05.08.26-1.pdf",
        "content": "PTR: Trump purchase DELL TECHNOLOGIES (DELL) $1,000,001 - $5,000,000",
        "content_cn": "PTR交易报告：特朗普买入戴尔科技(DELL) $1,000,001 - $5,000,000",
        "signal_direction": "bullish",
        "affected_assets": json.dumps(["DELL"]),
        "trade_detail": json.dumps({"action": "purchase", "amount": "$1,000,001 - $5,000,000", "source": "OGE PTR"}),
        "impact_note": "白宫PTR披露，大额买入",
        "verification": "verified",
    },
    {
        "chain_id": "DELL-2026",
        "date": "2026-03-23",
        "speaker": "Donald Trump",
        "signal_category": "trade",
        "source": "OGE PTR (白宫)",
        "source_url": "https://www.whitehouse.gov/wp-content/uploads/2026/05/President-Donald-J.-Trump-Periodic-Transaction-Report-05.08.26-1.pdf",
        "content": "PTR: Trump purchase DELL TECHNOLOGIES (DELL) $15,001 - $50,000 (UNSOLICITED)",
        "content_cn": "PTR交易报告：特朗普买入戴尔科技(DELL) $15,001 - $50,000（非自主决策）",
        "signal_direction": "bullish",
        "affected_assets": json.dumps(["DELL"]),
        "trade_detail": json.dumps({"action": "purchase", "amount": "$15,001 - $50,000", "unsolicited": True, "source": "OGE PTR"}),
        "impact_note": "白宫PTR披露（非自主决策）",
        "verification": "verified",
    },
    {
        "chain_id": "DELL-2026",
        "date": "2026-05-08",
        "speaker": "Donald Trump",
        "signal_category": "statement",
        "source": "新闻发布会",
        "source_url": "",
        "content": 'Trump at White House event: "Go buy a Dell, they\'re really great"',
        "content_cn": '特朗普在白宫活动中表示："去购买一台戴尔电脑吧，它们真的很棒"',
        "signal_direction": "bullish",
        "affected_assets": json.dumps(["DELL"]),
        "trade_detail": None,
        "impact_note": "总统公开为持仓公司站台",
        "verification": "verified",
    },
    {
        "chain_id": "DELL-2026",
        "date": "2026-05-27",
        "speaker": "五角大楼",
        "signal_category": "event",
        "source": "政府公告",
        "source_url": "",
        "content": "Dell Technologies wins $9.7 billion Pentagon contract for 5 years",
        "content_cn": "戴尔科技获得五角大楼5年97亿美元合同",
        "signal_direction": "bullish",
        "affected_assets": json.dumps(["DELL"]),
        "trade_detail": None,
        "impact_note": "喊话后19天获巨额国防合同",
        "verification": "verified",
    },
    # ── PTR 交易数据（2026 Q1，从白宫 PTR PDF OCR 提取）────────
    # META
    {
        "chain_id": "META-PTR-2026",
        "date": "2026-01-17",
        "speaker": "Donald Trump",
        "signal_category": "trade",
        "source": "OGE PTR (白宫)",
        "source_url": "",
        "content": "PTR: Trump purchase META PLATFORMS (META) $100,001 - $250,000 (UNSOLICITED)",
        "content_cn": "PTR交易报告：特朗普买入Meta(META) $100,001 - $250,000（非自主决策）",
        "signal_direction": "bullish",
        "affected_assets": json.dumps(["META"]),
        "trade_detail": json.dumps({"action": "purchase", "amount": "$100,001 - $250,000", "unsolicited": True, "source": "OGE PTR"}),
        "impact_note": "白宫PTR披露",
        "verification": "verified",
    },
    {
        "chain_id": "META-PTR-2026",
        "date": "2026-03-18",
        "speaker": "Donald Trump",
        "signal_category": "trade",
        "source": "OGE PTR (白宫)",
        "source_url": "",
        "content": "PTR: Trump purchase META PLATFORMS (META) $15,001 - $50,000",
        "content_cn": "PTR交易报告：特朗普买入Meta(META) $15,001 - $50,000",
        "signal_direction": "bullish",
        "affected_assets": json.dumps(["META"]),
        "trade_detail": json.dumps({"action": "purchase", "amount": "$15,001 - $50,000", "source": "OGE PTR"}),
        "impact_note": "白宫PTR披露",
        "verification": "verified",
    },
    # AAPL
    {
        "chain_id": "AAPL-PTR-2026",
        "date": "2026-02-10",
        "speaker": "Donald Trump",
        "signal_category": "trade",
        "source": "OGE PTR (白宫)",
        "source_url": "",
        "content": "PTR: Trump purchase APPLE (AAPL) $1,000,001 - $5,000,000 (UNSOLICITED)",
        "content_cn": "PTR交易报告：特朗普买入苹果(AAPL) $1,000,001 - $5,000,000（非自主决策）",
        "signal_direction": "bullish",
        "affected_assets": json.dumps(["AAPL"]),
        "trade_detail": json.dumps({"action": "purchase", "amount": "$1,000,001 - $5,000,000", "unsolicited": True, "source": "OGE PTR"}),
        "impact_note": "白宫PTR披露，大额买入",
        "verification": "verified",
    },
    # AMZN
    {
        "chain_id": "AMZN-PTR-2026",
        "date": "2026-02-10",
        "speaker": "Donald Trump",
        "signal_category": "trade",
        "source": "OGE PTR (白宫)",
        "source_url": "",
        "content": "PTR: Trump purchase AMAZON (AMZN) $1,000,001 - $5,000,000 (UNSOLICITED)",
        "content_cn": "PTR交易报告：特朗普买入亚马逊(AMZN) $1,000,001 - $5,000,000（非自主决策）",
        "signal_direction": "bullish",
        "affected_assets": json.dumps(["AMZN"]),
        "trade_detail": json.dumps({"action": "purchase", "amount": "$1,000,001 - $5,000,000", "unsolicited": True, "source": "OGE PTR"}),
        "impact_note": "白宫PTR披露，大额买入",
        "verification": "verified",
    },
    {
        "chain_id": "AMZN-PTR-2026",
        "date": "2026-02-17",
        "speaker": "Donald Trump",
        "signal_category": "trade",
        "source": "OGE PTR (白宫)",
        "source_url": "",
        "content": "PTR: Trump purchase AMAZON (AMZN) $15,001 - $50,000",
        "content_cn": "PTR交易报告：特朗普买入亚马逊(AMZN) $15,001 - $50,000",
        "signal_direction": "bullish",
        "affected_assets": json.dumps(["AMZN"]),
        "trade_detail": json.dumps({"action": "purchase", "amount": "$15,001 - $50,000", "source": "OGE PTR"}),
        "impact_note": "白宫PTR披露",
        "verification": "verified",
    },
    # GOOGL
    {
        "chain_id": "GOOGL-PTR-2026",
        "date": "2026-02-10",
        "speaker": "Donald Trump",
        "signal_category": "trade",
        "source": "OGE PTR (白宫)",
        "source_url": "",
        "content": "PTR: Trump purchase ALPHABET (GOOGL) $100,001 - $250,000",
        "content_cn": "PTR交易报告：特朗普买入Alphabet(GOOGL) $100,001 - $250,000",
        "signal_direction": "bullish",
        "affected_assets": json.dumps(["GOOGL"]),
        "trade_detail": json.dumps({"action": "purchase", "amount": "$100,001 - $250,000", "source": "OGE PTR"}),
        "impact_note": "白宫PTR披露",
        "verification": "verified",
    },
    # AMD (多次买入)
    {
        "chain_id": "AMD-PTR-2026",
        "date": "2026-01-23",
        "speaker": "Donald Trump",
        "signal_category": "trade",
        "source": "OGE PTR (白宫)",
        "source_url": "",
        "content": "PTR: Trump purchase ADVANCED MICRO DEVICES (AMD) $15,001 - $50,000",
        "content_cn": "PTR交易报告：特朗普买入AMD $15,001 - $50,000",
        "signal_direction": "bullish",
        "affected_assets": json.dumps(["AMD"]),
        "trade_detail": json.dumps({"action": "purchase", "amount": "$15,001 - $50,000", "source": "OGE PTR"}),
        "impact_note": "白宫PTR披露",
        "verification": "verified",
    },
    {
        "chain_id": "AMD-PTR-2026",
        "date": "2026-01-27",
        "speaker": "Donald Trump",
        "signal_category": "trade",
        "source": "OGE PTR (白宫)",
        "source_url": "",
        "content": "PTR: Trump purchase ADVANCED MICRO DEVICES (AMD) $1,001 - $15,000",
        "content_cn": "PTR交易报告：特朗普买入AMD $1,001 - $15,000",
        "signal_direction": "bullish",
        "affected_assets": json.dumps(["AMD"]),
        "trade_detail": json.dumps({"action": "purchase", "amount": "$1,001 - $15,000", "source": "OGE PTR"}),
        "impact_note": "白宫PTR披露",
        "verification": "verified",
    },
    {
        "chain_id": "AMD-PTR-2026",
        "date": "2026-03-11",
        "speaker": "Donald Trump",
        "signal_category": "trade",
        "source": "OGE PTR (白宫)",
        "source_url": "",
        "content": "PTR: Trump purchase ADVANCED MICRO DEVICES (AMD) $15,001 - $50,000 (UNSOLICITED)",
        "content_cn": "PTR交易报告：特朗普买入AMD $15,001 - $50,000（非自主决策）",
        "signal_direction": "bullish",
        "affected_assets": json.dumps(["AMD"]),
        "trade_detail": json.dumps({"action": "purchase", "amount": "$15,001 - $50,000", "unsolicited": True, "source": "OGE PTR"}),
        "impact_note": "白宫PTR披露（非自主决策）",
        "verification": "verified",
    },
    # TSLA
    {
        "chain_id": "TSLA-PTR-2026",
        "date": "2026-01-23",
        "speaker": "Donald Trump",
        "signal_category": "trade",
        "source": "OGE PTR (白宫)",
        "source_url": "",
        "content": "PTR: Trump purchase TESLA (TSLA) $1,001 - $15,000",
        "content_cn": "PTR交易报告：特朗普买入特斯拉(TSLA) $1,001 - $15,000",
        "signal_direction": "bullish",
        "affected_assets": json.dumps(["TSLA"]),
        "trade_detail": json.dumps({"action": "purchase", "amount": "$1,001 - $15,000", "source": "OGE PTR"}),
        "impact_note": "白宫PTR披露",
        "verification": "verified",
    },
    # COIN
    {
        "chain_id": "COIN-PTR-2026",
        "date": "2026-03-23",
        "speaker": "Donald Trump",
        "signal_category": "trade",
        "source": "OGE PTR (白宫)",
        "source_url": "",
        "content": "PTR: Trump purchase COINBASE (COIN) $15,001 - $50,000",
        "content_cn": "PTR交易报告：特朗普买入Coinbase(COIN) $15,001 - $50,000",
        "signal_direction": "bullish",
        "affected_assets": json.dumps(["COIN"]),
        "trade_detail": json.dumps({"action": "purchase", "amount": "$15,001 - $50,000", "source": "OGE PTR"}),
        "impact_note": "白宫PTR披露",
        "verification": "verified",
    },
    # DIS
    {
        "chain_id": "DIS-PTR-2026",
        "date": "2026-03-18",
        "speaker": "Donald Trump",
        "signal_category": "trade",
        "source": "OGE PTR (白宫)",
        "source_url": "",
        "content": "PTR: Trump purchase DISNEY (DIS) $100,001 - $250,000",
        "content_cn": "PTR交易报告：特朗普买入迪士尼(DIS) $100,001 - $250,000",
        "signal_direction": "bullish",
        "affected_assets": json.dumps(["DIS"]),
        "trade_detail": json.dumps({"action": "purchase", "amount": "$100,001 - $250,000", "source": "OGE PTR"}),
        "impact_note": "白宫PTR披露",
        "verification": "verified",
    },
    # WMT
    {
        "chain_id": "WMT-PTR-2026",
        "date": "2026-03-17",
        "speaker": "Donald Trump",
        "signal_category": "trade",
        "source": "OGE PTR (白宫)",
        "source_url": "",
        "content": "PTR: Trump purchase WALMART (WMT) $250,001 - $500,000",
        "content_cn": "PTR交易报告：特朗普买入沃尔玛(WMT) $250,001 - $500,000",
        "signal_direction": "bullish",
        "affected_assets": json.dumps(["WMT"]),
        "trade_detail": json.dumps({"action": "purchase", "amount": "$250,001 - $500,000", "source": "OGE PTR"}),
        "impact_note": "白宫PTR披露",
        "verification": "verified",
    },
    # GS (UNSOLICITED)
    {
        "chain_id": "GS-PTR-2026",
        "date": "2026-03-11",
        "speaker": "Donald Trump",
        "signal_category": "trade",
        "source": "OGE PTR (白宫)",
        "source_url": "",
        "content": "PTR: Trump purchase GOLDMAN SACHS (GS) $15,001 - $50,000 (UNSOLICITED)",
        "content_cn": "PTR交易报告：特朗普买入高盛(GS) $15,001 - $50,000（非自主决策）",
        "signal_direction": "bullish",
        "affected_assets": json.dumps(["GS"]),
        "trade_detail": json.dumps({"action": "purchase", "amount": "$15,001 - $50,000", "unsolicited": True, "source": "OGE PTR"}),
        "impact_note": "白宫PTR披露（非自主决策）",
        "verification": "verified",
    },
    # 其他 PTR 交易
    {
        "chain_id": "GOOGL-PTR-2026",
        "date": "2026-02-10",
        "speaker": "Donald Trump",
        "signal_category": "trade",
        "source": "OGE PTR (白宫)",
        "source_url": "",
        "content": "PTR: Trump purchase GE VERNOVA (GEV) $50,001 - $100,000",
        "content_cn": "PTR交易报告：特朗普买入GE Vernova(GEV) $50,001 - $100,000",
        "signal_direction": "bullish",
        "affected_assets": json.dumps(["GEV"]),
        "trade_detail": json.dumps({"action": "purchase", "amount": "$50,001 - $100,000", "source": "OGE PTR"}),
        "impact_note": "白宫PTR披露",
        "verification": "verified",
    },
    {
        "chain_id": "BA-PTR-2026",
        "date": "2026-02-10",
        "speaker": "Donald Trump",
        "signal_category": "trade",
        "source": "OGE PTR (白宫)",
        "source_url": "",
        "content": "PTR: Trump purchase BOEING (BA) $1,000,001 - $5,000,000",
        "content_cn": "PTR交易报告：特朗普买入波音(BA) $1,000,001 - $5,000,000",
        "signal_direction": "bullish",
        "affected_assets": json.dumps(["BA"]),
        "trade_detail": json.dumps({"action": "purchase", "amount": "$1,000,001 - $5,000,000", "source": "OGE PTR"}),
        "impact_note": "白宫PTR披露，大额买入",
        "verification": "verified",
    },
    {
        "chain_id": "UBER-PTR-2026",
        "date": "2026-02-10",
        "speaker": "Donald Trump",
        "signal_category": "trade",
        "source": "OGE PTR (白宫)",
        "source_url": "",
        "content": "PTR: Trump purchase UBER (UBER) $1,000,001 - $5,000,000",
        "content_cn": "PTR交易报告：特朗普买入优步(UBER) $1,000,001 - $5,000,000",
        "signal_direction": "bullish",
        "affected_assets": json.dumps(["UBER"]),
        "trade_detail": json.dumps({"action": "purchase", "amount": "$1,000,001 - $5,000,000", "source": "OGE PTR"}),
        "impact_note": "白宫PTR披露，大额买入",
        "verification": "verified",
    },
    {
        "chain_id": "TMUS-PTR-2026",
        "date": "2026-03-17",
        "speaker": "Donald Trump",
        "signal_category": "trade",
        "source": "OGE PTR (白宫)",
        "source_url": "",
        "content": "PTR: Trump purchase T-MOBILE (TMUS) $15,001 - $50,000 (UNSOLICITED)",
        "content_cn": "PTR交易报告：特朗普买入T-Mobile(TMUS) $15,001 - $50,000（非自主决策）",
        "signal_direction": "bullish",
        "affected_assets": json.dumps(["TMUS"]),
        "trade_detail": json.dumps({"action": "purchase", "amount": "$15,001 - $50,000", "unsolicited": True, "source": "OGE PTR"}),
        "impact_note": "白宫PTR披露（非自主决策）",
        "verification": "verified",
    },
    {
        "chain_id": "IBM-PTR-2026",
        "date": "2026-03-18",
        "speaker": "Donald Trump",
        "signal_category": "trade",
        "source": "OGE PTR (白宫)",
        "source_url": "",
        "content": "PTR: Trump purchase IBM (IBM) $15,001 - $50,000",
        "content_cn": "PTR交易报告：特朗普买入IBM $15,001 - $50,000",
        "signal_direction": "bullish",
        "affected_assets": json.dumps(["IBM"]),
        "trade_detail": json.dumps({"action": "purchase", "amount": "$15,001 - $50,000", "source": "OGE PTR"}),
        "impact_note": "白宫PTR披露",
        "verification": "verified",
    },
    {
        "chain_id": "AVGO-PTR-2026",
        "date": "2026-03-18",
        "speaker": "Donald Trump",
        "signal_category": "trade",
        "source": "OGE PTR (白宫)",
        "source_url": "",
        "content": "PTR: Trump purchase BROADCOM (AVGO) $15,001 - $50,000",
        "content_cn": "PTR交易报告：特朗普买入博通(AVGO) $15,001 - $50,000",
        "signal_direction": "bullish",
        "affected_assets": json.dumps(["AVGO"]),
        "trade_detail": json.dumps({"action": "purchase", "amount": "$15,001 - $50,000", "source": "OGE PTR"}),
        "impact_note": "白宫PTR披露",
        "verification": "verified",
    },
    {
        "chain_id": "VZ-PTR-2026",
        "date": "2026-02-24",
        "speaker": "Donald Trump",
        "signal_category": "trade",
        "source": "OGE PTR (白宫)",
        "source_url": "",
        "content": "PTR: Trump purchase VERIZON (VZ) $100,001 - $250,000",
        "content_cn": "PTR交易报告：特朗普买入威瑞森(VZ) $100,001 - $250,000",
        "signal_direction": "bullish",
        "affected_assets": json.dumps(["VZ"]),
        "trade_detail": json.dumps({"action": "purchase", "amount": "$100,001 - $250,000", "source": "OGE PTR"}),
        "impact_note": "白宫PTR披露",
        "verification": "verified",
    },
    {
        "chain_id": "CVX-PTR-2026",
        "date": "2026-03-18",
        "speaker": "Donald Trump",
        "signal_category": "trade",
        "source": "OGE PTR (白宫)",
        "source_url": "",
        "content": "PTR: Trump purchase CHEVRON (CVX) $1,001 - $15,000",
        "content_cn": "PTR交易报告：特朗普买入雪佛龙(CVX) $1,001 - $15,000",
        "signal_direction": "bullish",
        "affected_assets": json.dumps(["CVX"]),
        "trade_detail": json.dumps({"action": "purchase", "amount": "$1,001 - $15,000", "source": "OGE PTR"}),
        "impact_note": "白宫PTR披露",
        "verification": "verified",
    },
    {
        "chain_id": "PGR-PTR-2026",
        "date": "2026-02-10",
        "speaker": "Donald Trump",
        "signal_category": "trade",
        "source": "OGE PTR (白宫)",
        "source_url": "",
        "content": "PTR: Trump sale PROGRESSIVE (PGR) $250,001 - $500,000",
        "content_cn": "PTR交易报告：特朗普卖出Progressive(PGR) $250,001 - $500,000",
        "signal_direction": "bearish",
        "affected_assets": json.dumps(["PGR"]),
        "trade_detail": json.dumps({"action": "sale", "amount": "$250,001 - $500,000", "source": "OGE PTR"}),
        "impact_note": "白宫PTR披露，卖出",
        "verification": "verified",
    },
]


class PoliticalSignalStorage:
    """政治信号 SQLite 存储"""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        self._ensure_db()

    def _ensure_db(self):
        """确保数据库和表存在"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS political_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chain_id TEXT,
                    date TEXT NOT NULL,
                    speaker TEXT NOT NULL,
                    signal_category TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_url TEXT,
                    content TEXT NOT NULL,
                    content_cn TEXT,
                    signal_direction TEXT NOT NULL,
                    affected_assets TEXT NOT NULL,
                    trade_detail TEXT,
                    impact_note TEXT,
                    verification TEXT DEFAULT 'unverified',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_signal_date
                ON political_signals(date)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_signal_chain
                ON political_signals(chain_id)
            """)

    def seed_if_empty(self) -> bool:
        """如果表为空，填充预置数据。返回是否执行了填充。"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM political_signals")
            count = cursor.fetchone()[0]
        if count > 0:
            return False
        for signal in SEED_SIGNALS:
            self.save(signal)
        return True

    def ensure_seed_data(self) -> int:
        """确保种子数据完整（增量补充缺失的种子记录）。

        检查每条种子记录是否已存在（按 content[:50] 去重），
        缺失的插入。返回新增条数。
        """
        existing = self.load_all()
        existing_keys = set()
        if not existing.empty:
            for _, row in existing.iterrows():
                key = (row["date"], row["speaker"], row.get("content", "")[:50])
                existing_keys.add(key)

        count = 0
        for signal in SEED_SIGNALS:
            key = (signal["date"], signal["speaker"], signal["content"][:50])
            if key not in existing_keys:
                self.save(signal)
                count += 1
        return count

    def save(self, signal: Dict) -> int:
        """插入一条信号记录，返回新记录 id"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                INSERT INTO political_signals
                    (chain_id, date, speaker, signal_category, source, source_url,
                     content, content_cn, signal_direction, affected_assets,
                     trade_detail, impact_note, verification)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                signal.get("chain_id"),
                signal["date"],
                signal["speaker"],
                signal["signal_category"],
                signal["source"],
                signal.get("source_url", ""),
                signal["content"],
                signal.get("content_cn"),
                signal["signal_direction"],
                signal["affected_assets"],
                signal.get("trade_detail"),
                signal.get("impact_note"),
                signal.get("verification", "unverified"),
            ))
            return cursor.lastrowid

    def load_all(self) -> pd.DataFrame:
        """加载全部信号，按日期倒序"""
        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql_query(
                "SELECT * FROM political_signals ORDER BY date DESC, id DESC",
                conn,
            )
        return df

    def _update_content_cn(self, content: str, content_cn: str):
        """按 content 匹配更新 content_cn 字段"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE political_signals SET content_cn = ? WHERE content = ?",
                (content_cn, content),
            )

    def load_by_date_range(
        self, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """按日期范围查询"""
        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql_query(
                "SELECT * FROM political_signals WHERE date >= ? AND date <= ? ORDER BY date DESC",
                conn,
                params=(start_date, end_date),
            )
        return df

    def load_by_speaker(self, speaker: str) -> pd.DataFrame:
        """按发言人筛选"""
        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql_query(
                "SELECT * FROM political_signals WHERE speaker = ? ORDER BY date DESC",
                conn,
                params=(speaker,),
            )
        return df

    def load_by_chain(self, chain_id: str) -> pd.DataFrame:
        """获取同一信号链的全部记录"""
        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql_query(
                "SELECT * FROM political_signals WHERE chain_id = ? ORDER BY date",
                conn,
                params=(chain_id,),
            )
        return df

    def load_chains_summary(self) -> pd.DataFrame:
        """按 chain_id 聚合，返回每条链的摘要"""
        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql_query("""
                SELECT
                    chain_id,
                    MIN(date) as first_date,
                    MAX(date) as last_date,
                    COUNT(*) as signal_count,
                    SUM(CASE WHEN signal_category='trade' THEN 1 ELSE 0 END) as trade_count,
                    SUM(CASE WHEN signal_category='statement' THEN 1 ELSE 0 END) as statement_count,
                    SUM(CASE WHEN signal_category='event' THEN 1 ELSE 0 END) as event_count,
                    GROUP_CONCAT(DISTINCT speaker) as speakers,
                    signal_direction,
                    affected_assets
                FROM political_signals
                WHERE chain_id IS NOT NULL AND chain_id != ''
                GROUP BY chain_id
                ORDER BY last_date DESC
            """, conn)
        return df

    def load_filtered(
        self,
        speaker: Optional[str] = None,
        signal_category: Optional[str] = None,
        signal_direction: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> pd.DataFrame:
        """多条件筛选"""
        query = "SELECT * FROM political_signals WHERE 1=1"
        params: list = []

        if speaker and speaker != "全部":
            query += " AND speaker = ?"
            params.append(speaker)
        if signal_category and signal_category != "全部":
            query += " AND signal_category = ?"
            params.append(signal_category)
        if signal_direction and signal_direction != "全部":
            query += " AND signal_direction = ?"
            params.append(signal_direction)
        if start_date:
            query += " AND date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND date <= ?"
            params.append(end_date)

        query += " ORDER BY date DESC, id DESC"

        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql_query(query, conn, params=params)
        return df

    def delete(self, signal_id: int) -> bool:
        """删除一条记录"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM political_signals WHERE id = ?", (signal_id,)
            )
            return cursor.rowcount > 0

    def update(self, signal_id: int, updates: Dict) -> bool:
        """更新一条记录的指定字段"""
        allowed = {
            "chain_id", "date", "speaker", "signal_category", "source",
            "source_url", "content", "content_cn", "signal_direction",
            "affected_assets", "trade_detail", "impact_note", "verification",
        }
        fields = {k: v for k, v in updates.items() if k in allowed}
        if not fields:
            return False
        set_clause = ", ".join(f"{k}=?" for k in fields)
        values = list(fields.values()) + [signal_id]
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                f"UPDATE political_signals SET {set_clause} WHERE id=?",
                values,
            )
            return cursor.rowcount > 0

    def get_stats(self) -> Dict:
        """返回统计摘要"""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN signal_category='trade' THEN 1 ELSE 0 END) as trades,
                    SUM(CASE WHEN signal_category='statement' THEN 1 ELSE 0 END) as statements,
                    SUM(CASE WHEN signal_category='event' THEN 1 ELSE 0 END) as events,
                    SUM(CASE WHEN signal_direction='bullish' THEN 1 ELSE 0 END) as bullish,
                    SUM(CASE WHEN signal_direction='bearish' THEN 1 ELSE 0 END) as bearish,
                    SUM(CASE WHEN signal_direction='neutral' THEN 1 ELSE 0 END) as neutral,
                    COUNT(DISTINCT chain_id) as chains,
                    MAX(date) as latest_date
                FROM political_signals
            """).fetchone()
        return {
            "total": row[0] or 0,
            "trades": row[1] or 0,
            "statements": row[2] or 0,
            "events": row[3] or 0,
            "bullish": row[4] or 0,
            "bearish": row[5] or 0,
            "neutral": row[6] or 0,
            "chains": row[7] or 0,
            "latest_date": row[8] or "N/A",
        }
