"""政治信号自动获取器

数据源：
1. SEC EDGAR - 特朗普家族 Form 4 (insider trading) 披露
2. OGE 278e - 总统财务披露（PDF解析）
3. White House PTR - 定期交易报告（OCR解析）
4. NewsAPI / 新闻搜索 - 公开喊话和事件
"""

import json
import re
import sqlite3
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import requests

# SEC EDGAR 配置
EDGAR_BASE = "https://efts.sec.gov/LATEST/search-index"
EDGAR_USER_AGENT = "MarketPulse research@marketpulse.com"  # SEC 要求

# 特朗普家族 CIK 编号
TRUMP_FAMILY_CIKS = {
    "0002016181": "Don Jr.",
    # Trump 本人的个人交易通过信托进行，CIK 不直接关联
}

# Trump 相关公司
TRUMP_COMPANIES = {
    "0001849635": {"name": "Trump Media & Technology Group", "ticker": "DJT"},
}

# 关注的关联人物（特朗普政治盟友）
ALLIED_FIGURES = {
    "0002015674": "Kashyap Patel",  # FBI Director
    "0001593763": "Linda McMahon",  # Education Secretary
    "0002015803": "Robert Lighthizer",  # Trade Rep
    "0002083205": "David Bernhardt",  # Interior Secretary
}

# ── OGE 278e 财务披露解析 ──────────────────────────────────────
# OGE Form 278e 是总统/高级官员的年度财务披露
# 包含：资产、交易、负债、礼物等
# 文件来源：白宫网站 PDF 或 OGE 电子披露系统

# OGE 电子披露系统
OGE_EFILING_BASE = "https://efiling.oge.gov/eo278/public"

# 常见股票名称到 ticker 的映射
OGE_TICKER_MAP = {
    "DELL": "DELL",
    "DELL TECHNOLOGIES": "DELL",
    "APPLE": "AAPL",
    "APPLE INC": "AAPL",
    "MICROSOFT": "MSFT",
    "MICROSOFT CORP": "MSFT",
    "AMAZON": "AMZN",
    "AMAZON.COM": "AMZN",
    "ALPHABET": "GOOGL",
    "GOOGLE": "GOOGL",
    "NVIDIA": "NVDA",
    "NVIDIA CORP": "NVDA",
    "TESLA": "TSLA",
    "TESLA INC": "TSLA",
    "META": "META",
    "META PLATFORMS": "META",
    "FACEBOOK": "META",
    "JOHNSON & JOHNSON": "JNJ",
    "JPMORGAN": "JPM",
    "JPMORGAN CHASE": "JPM",
    "VISA": "V",
    "MASTERCARD": "MA",
    "BERKSHIRE HATHAWAY": "BRK.B",
    "WALMART": "WMT",
    "PROCTER & GAMBLE": "PG",
    "COCA-COLA": "KO",
    "DISNEY": "DIS",
    "WALT DISNEY": "DIS",
    "NETFLIX": "NFLX",
    "INTEL": "INTC",
    "INTEL CORP": "INTC",
    "AMD": "AMD",
    "ADVANCED MICRO DEVICES": "AMD",
    "SALESFORCE": "CRM",
    "ORACLE": "ORCL",
    "ORACLE CORP": "ORCL",
    "CISCO": "CSCO",
    "CISCO SYSTEMS": "CSCO",
    "IBM": "IBM",
    "BOEING": "BA",
    "BOEING CO": "BA",
    "LOCKHEED MARTIN": "LMT",
    "RAYTHEON": "RTX",
    "GENERAL MOTORS": "GM",
    "FORD": "F",
    "FORD MOTOR": "F",
    "EXXON MOBIL": "XOM",
    "CHEVRON": "CVX",
    "PFIZER": "PFE",
    "MODERNA": "MRNA",
    "UNITEDHEALTH": "UNH",
    "UNITEDHEALTH GROUP": "UNH",
    "TRUMP MEDIA": "DJT",
    "TRUMP MEDIA & TECHNOLOGY": "DJT",
    "TMTG": "DJT",
    "DIGITAL WORLD": "DWAC",
    "COINBASE": "COIN",
    "COINBASE GLOBAL": "COIN",
    "MICROSTRATEGY": "MSTR",
    "ROBINHOOD": "HOOD",
    "SOFI": "SOFI",
    "GRABAGUN": "PEW",
    "PSQ HOLDINGS": "PSQH",
    "PSQ": "PSQH",
    "WORLD LIBERTY": "WLFI",
}


def guess_ticker_from_name(name: str) -> str:
    """从公司名称猜测 ticker"""
    name_upper = name.upper().strip()
    # 精确匹配
    if name_upper in OGE_TICKER_MAP:
        return OGE_TICKER_MAP[name_upper]
    # 模糊匹配
    for key, ticker in OGE_TICKER_MAP.items():
        if key in name_upper or name_upper in key:
            return ticker
    return ""


def parse_oge278e_text(text: str) -> List[Dict]:
    """解析 OGE 278e 文本内容，提取交易记录

    OGE 278e 的交易部分格式通常是：
    - 资产名称
    - 交易类型 (Purchase/Sale/Exchange)
    - 交易日期
    - 金额范围

    Args:
        text: OGE 278e 的纯文本内容

    Returns:
        list of signal dicts
    """
    signals = []
    lines = text.split("\n")

    # 状态机：查找交易部分
    in_transactions = False
    current_asset = ""
    current_date = ""
    current_type = ""

    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue

        # 检测交易部分开始
        if any(kw in line.lower() for kw in [
            "transactions", "schedule b", "part 1b",
            "assets and transactions", "income and assets"
        ]):
            in_transactions = True
            continue

        # 检测交易部分结束
        if in_transactions and any(kw in line.lower() for kw in [
            "schedule c", "schedule d", "part 2", "liabilities",
            "gifts", "reimbursements", "agreements"
        ]):
            in_transactions = False
            continue

        if not in_transactions:
            continue

        # 尝试解析交易行
        # 常见格式：
        # "Dell Technologies Inc. - Common Stock"
        # "Purchase  02/10/2026  $1,001 - $15,000"
        # 或者：
        # "DELL  Purchase  02/10/2026  $1,001 - $15,000"

        # 检测日期格式
        import re
        date_match = re.search(r'(\d{2}/\d{2}/\d{4})', line)
        if date_match:
            date_str = date_match.group(1)
            # 转换为 YYYY-MM-DD
            try:
                parts = date_str.split("/")
                current_date = f"{parts[2]}-{parts[0]}-{parts[1]}"
            except (IndexError, ValueError):
                current_date = ""

            # 检测交易类型
            line_lower = line.lower()
            if "purchase" in line_lower or "buy" in line_lower:
                current_type = "purchase"
            elif "sale" in line_lower or "sell" in line_lower:
                current_type = "sale"
            elif "exchange" in line_lower:
                current_type = "exchange"
            else:
                current_type = "unknown"

            # 检测资产名称（日期前的部分）
            before_date = line[:date_match.start()].strip()
            if before_date:
                current_asset = before_date

            # 如果有有效数据，创建信号
            if current_date and current_asset:
                ticker = guess_ticker_from_name(current_asset)
                if ticker:
                    direction = "bullish" if current_type == "purchase" else "bearish" if current_type == "sale" else "neutral"
                    signals.append({
                        "chain_id": f"{ticker}-OGE-{current_date[:4]}",
                        "date": current_date,
                        "speaker": "Donald Trump",
                        "signal_category": "trade",
                        "source": "OGE 278e 财务披露",
                        "source_url": "",
                        "content": f"OGE 278e: Trump {current_type} {current_asset}",
                        "content_cn": f"OGE 财务披露：特朗普{'买入' if current_type == 'purchase' else '卖出' if current_type == 'sale' else '交易'}{current_asset}",
                        "signal_direction": direction,
                        "affected_assets": json.dumps([ticker]),
                        "trade_detail": json.dumps({
                            "action": current_type,
                            "source": "OGE 278e",
                        }),
                        "impact_note": "OGE年度财务披露",
                        "verification": "verified",
                    })

    return signals


def parse_oge278e_pdf(pdf_path: str) -> List[Dict]:
    """解析 OGE 278e PDF 文件

    Args:
        pdf_path: PDF 文件路径

    Returns:
        list of signal dicts
    """
    try:
        import subprocess
        # 使用 pdftotext 提取文本（macOS: brew install poppler）
        result = subprocess.run(
            ["pdftotext", "-layout", pdf_path, "-"],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            return parse_oge278e_text(result.stdout)
    except FileNotFoundError:
        pass

    # 备选：尝试使用 PyPDF2
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(pdf_path)
        text = ""
        for page in reader.pages:
            text += page.extract_text() + "\n"
        return parse_oge278e_text(text)
    except ImportError:
        pass

    return []


def fetch_oge_from_web() -> Optional[str]:
    """尝试从 OGE 电子披露系统获取特朗普的财务披露

    Returns:
        文本内容，或 None
    """
    # 尝试 OGE 电子披露系统
    # Trump 的 OGE ID 通常是 10022221（总统）
    urls = [
        f"{OGE_EFILING_BASE}/financial-detail/2026/10022221",
        f"{OGE_EFILING_BASE}/financial-detail/2025/10022221",
    ]

    for url in urls:
        try:
            resp = requests.get(url, headers={"User-Agent": EDGAR_USER_AGENT}, timeout=15)
            if resp.status_code == 200 and len(resp.text) > 1000:
                return resp.text
        except Exception:
            continue

    return None


# ── White House PTR (Periodic Transaction Report) ──────────────────
# PTR 是总统的定期交易报告，比 OGE 278e 更频繁
# 来源：白宫网站 disclosures 页面

WHITE_HOUSE_DISCLOSURES_URL = "https://www.whitehouse.gov/disclosures/"

# PTR PDF 已知 URL 模式
PTR_URLS = [
    "https://www.whitehouse.gov/wp-content/uploads/2026/05/President-Donald-J.-Trump-Periodic-Transaction-Report-05.08.26-1.pdf",
    "https://www.whitehouse.gov/wp-content/uploads/2026/05/President-Donald-J.-Trump-Periodic-Transaction-Report-05.08.26-2.pdf",
    "https://www.whitehouse.gov/wp-content/uploads/2026/04/President-Donald-J.-Trump-Periodic-Transaction-Report-4.20.26.pdf",
    "https://www.whitehouse.gov/wp-content/uploads/2026/03/President-Donald-J.-Trump-Periodic-Transaction-Report-2.26.26-1.pdf",
    "https://www.whitehouse.gov/wp-content/uploads/2026/03/President-Donald-J.-Trump-Periodic-Transaction-Report-2.26.26-2.pdf",
    "https://www.whitehouse.gov/wp-content/uploads/2026/01/President-Donald-J.-Trump-Periodic-Transaction-Report-1.14.2026-.pdf",
    "https://www.whitehouse.gov/wp-content/uploads/2026/01/President-Donald-J.-Trump-Periodic-Transaction-Report-Amendment-1.14.26.pdf",
    "https://www.whitehouse.gov/wp-content/uploads/2025/12/President-Donald-J.-Trump-Periodic-Transaction-Report-12.18.25.pdf",
    "https://www.whitehouse.gov/wp-content/uploads/2025/11/President-Donald-J.-Trump-Periodic-Transaction-Report-11.14.25.pdf",
]

# 公司名 → ticker 映射（PTR OCR 用）
PTR_COMPANY_TICKER_MAP = {
    "DELL TECHNOLOGIES": "DELL",
    "META PLATFORMS": "META",
    "APPLE": "AAPL",
    "APPLE INC": "AAPL",
    "AMAZON": "AMZN",
    "AMAZON.COM": "AMZN",
    "MICROSOFT": "MSFT",
    "ALPHABET": "GOOGL",
    "NVIDIA": "NVDA",
    "TESLA": "TSLA",
    "COINBASE": "COIN",
    "MICROSTRATEGY": "MSTR",
    "VISA": "V",
    "MASTERCARD": "MA",
    "JPMORGAN": "JPM",
    "GOLDMAN": "GS",
    "GOLDMAN SACHS": "GS",
    "BOEING": "BA",
    "INTEL": "INTC",
    "ADVANCED MICRO": "AMD",
    "DISNEY": "DIS",
    "NETFLIX": "NFLX",
    "ORACLE": "ORCL",
    "CISCO": "CSCO",
    "IBM": "IBM",
    "EXXON": "XOM",
    "CHEVRON": "CVX",
    "PFIZER": "PFE",
    "UNITEDHEALTH": "UNH",
    "WALMART": "WMT",
    "COSTCO": "COST",
    "BERKSHIRE": "BRK",
    "UBER": "UBER",
    "PAYPAL": "PYPL",
    "CROWDSTRIKE": "CRWD",
    "SERVICENOW": "NOW",
    "BOOKING": "BKNG",
    "MCDONALD": "MCD",
    "VERIZON": "VZ",
    "RTX": "RTX",
    "LOCKHEED": "LMT",
    "GENERAL MOTORS": "GM",
    "FORD": "F",
    "PROGRESSIVE": "PGR",
    "BROADCOM": "AVGO",
    "ADOBE": "ADBE",
    "WORKDAY": "WDAY",
    "ABBVIE": "ABBV",
    "REGENERON": "REGN",
    "MARVELL": "MRVL",
    "ANALOG DEVICES": "ADI",
    "INTUITIVE SURGICAL": "ISRG",
    "AUTOMATIC DATA": "ADP",
    "T-MOBILE": "TMUS",
    "STRYKER": "SYK",
    "FEDEX": "FDX",
    "ALLSTATE": "ALL",
    "PEPSICO": "PEP",
    "GILEAD": "GILD",
    "SCHWAB": "SCHW",
    "COREWEAVE": "CRWV",
    "STELLANTIS": "STLA",
    "EATON": "ETN",
    "TRANSDIGM": "TDG",
    "AXON": "AXON",
    "FLUTTER": "FLUT",
    "GARTNER": "IT",
    "MONSTER": "MNST",
    "CARVANA": "CVNA",
    "VEEVA": "VEEV",
    "HILTON": "HLT",
    "APOLLO": "APO",
    "ARES": "ARES",
    "GE VERNOVA": "GEV",
    "SUPER MICRO": "SMCI",
    "PNC FINL": "PNC",
    "ROBINHOOD": "HOOD",
    "JABIL": "JBL",
    "MOTOROLA": "MSI",
    "TEXAS INSTR": "TXN",
    "FIDELITY NATL": "FIS",
    "OCCIDENTAL": "OXY",
    "SALESFORCE": "CRM",
}


def discover_ptr_urls() -> List[str]:
    """从白宫 disclosures 页面发现 PTR PDF 链接

    Returns:
        list of PTR PDF URLs
    """
    try:
        resp = requests.get(
            WHITE_HOUSE_DISCLOSURES_URL,
            headers={"User-Agent": EDGAR_USER_AGENT},
            timeout=30,
        )
        resp.raise_for_status()
    except Exception as e:
        print(f"白宫 disclosures 页面获取失败: {e}")
        return PTR_URLS  # 回退到已知 URL 列表

    # 从 HTML 中提取 PTR PDF 链接
    urls = []
    import re
    pattern = r'href="([^"]*Trump[^"]*Periodic[^"]*Transaction[^"]*\.pdf[^"]*)"'
    matches = re.findall(pattern, resp.text, re.IGNORECASE)
    for match in matches:
        if not match.startswith("http"):
            match = "https://www.whitehouse.gov" + match
        urls.append(match)

    return urls if urls else PTR_URLS


def parse_ptr_ocr_text(text: str) -> List[Dict]:
    """解析 PTR OCR 文本，提取交易记录

    Args:
        text: OCR 输出的纯文本

    Returns:
        list of signal dicts
    """
    signals = []
    lines = text.split("\n")

    # 日期模式
    date_patterns = [
        r'(\d{1,2})/(\d{1,2})/(\d{4})',  # M/D/YYYY
    ]

    # 金额模式
    amount_patterns = [
        (r'\$1,000,001\s*-\s*\$5,000,000', "$1,000,001 - $5,000,000"),
        (r'\$500,001\s*-\s*\$1,000,000', "$500,001 - $1,000,000"),
        (r'\$250,001\s*-\s*\$500,000', "$250,001 - $500,000"),
        (r'\$100,001\s*-\s*\$250,000', "$100,001 - $250,000"),
        (r'\$50,001\s*-\s*\$100,000', "$50,001 - $100,000"),
        (r'\$15,001\s*-\s*\$50,000', "$15,001 - $50,000"),
        (r'\$1,001\s*-\s*\$15,000', "$1,001 - $15,000"),
    ]

    for line in lines:
        line = line.strip()
        if not line or len(line) < 10:
            continue

        # 查找公司名
        company = ""
        ticker = ""
        for pattern, tick in PTR_COMPANY_TICKER_MAP.items():
            if pattern in line.upper():
                company = pattern
                ticker = tick
                break

        if not ticker:
            continue

        # 查找日期
        date_str = ""
        for dp in date_patterns:
            dm = re.search(dp, line)
            if dm:
                m, d, y = int(dm.group(1)), int(dm.group(2)), int(dm.group(3))
                if 1 <= m <= 12 and 1 <= d <= 31 and 2025 <= y <= 2026:
                    date_str = f"{y}-{str(m).zfill(2)}-{str(d).zfill(2)}"
                    break

        if not date_str:
            continue

        # 交易类型
        action = "purchase"
        if re.search(r'\bsale\b|\bsold\b|\bsell\b', line, re.IGNORECASE):
            action = "sale"
        elif re.search(r'\bpurchase\b|\bpurchas\b|\bbuy\b', line, re.IGNORECASE):
            action = "purchase"

        # 金额
        amount = ""
        for pat, val in amount_patterns:
            if re.search(pat, line):
                amount = val
                break

        unsolicited = bool(re.search(r'UNSOL|UNSOUC', line, re.IGNORECASE))

        direction = "bullish" if action == "purchase" else "bearish" if action == "sale" else "neutral"

        signals.append({
            "chain_id": f"{ticker}-PTR-{date_str[:7]}",
            "date": date_str,
            "speaker": "Donald Trump",
            "signal_category": "trade",
            "source": "OGE PTR (白宫)",
            "source_url": "",
            "content": f"PTR: Trump {action} {company} ({ticker}) {amount}",
            "content_cn": f"PTR交易报告：特朗普{'买入' if action == 'purchase' else '卖出'}{company}({ticker}) {amount}",
            "signal_direction": direction,
            "affected_assets": json.dumps([ticker]),
            "trade_detail": json.dumps({
                "action": action,
                "amount": amount,
                "unsolicited": unsolicited,
                "source": "OGE PTR",
            }),
            "impact_note": f"白宫PTR披露{'（非自主决策）' if unsolicited else ''}",
            "verification": "verified",
        })

    return signals


def parse_ptr_pdf(pdf_path: str) -> List[Dict]:
    """解析 PTR PDF 文件（OCR 方式）

    Args:
        pdf_path: PDF 文件路径

    Returns:
        list of signal dicts
    """
    try:
        from pdf2image import convert_from_path
        import pytesseract
    except ImportError:
        print("PTR 解析需要 pdf2image 和 pytesseract: pip install pdf2image pytesseract")
        return []

    try:
        images = convert_from_path(pdf_path, dpi=200)
    except Exception as e:
        print(f"PDF 转换失败: {e}")
        return []

    all_text = []
    for i, img in enumerate(images):
        text = pytesseract.image_to_string(img)
        all_text.append(text)

    full_text = "\n".join(all_text)
    return parse_ptr_ocr_text(full_text)


def fetch_and_parse_latest_ptr() -> List[Dict]:
    """获取并解析最新的 PTR PDF

    Returns:
        list of signal dicts
    """
    urls = discover_ptr_urls()
    if not urls:
        return []

    # 只处理最新的一个 PTR
    latest_url = urls[0]
    print(f"正在下载 PTR: {latest_url[:80]}...")

    try:
        resp = requests.get(
            latest_url,
            headers={"User-Agent": EDGAR_USER_AGENT},
            timeout=120,
        )
        resp.raise_for_status()
    except Exception as e:
        print(f"PTR 下载失败: {e}")
        return []

    # 保存到临时文件
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(resp.content)
        tmp_path = tmp.name

    print(f"正在 OCR 解析 PTR ({len(resp.content) // 1024 // 1024}MB)...")
    signals = parse_ptr_pdf(tmp_path)

    # 清理临时文件
    try:
        Path(tmp_path).unlink()
    except Exception:
        pass

    return signals


def fetch_sec_form4_filings(
    start_date: str = None,
    end_date: str = None,
    query: str = "Trump",
) -> List[Dict]:
    """从 SEC EDGAR 获取 Form 4 披露

    Args:
        start_date: 开始日期 YYYY-MM-DD
        end_date: 结束日期 YYYY-MM-DD
        query: 搜索关键词

    Returns:
        list of dict, 每条包含 date, person, company, ticker, adsh
    """
    if not start_date:
        start_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    if not end_date:
        end_date = datetime.now().strftime("%Y-%m-%d")

    params = {
        "q": f'"{query}"',
        "forms": "4",
        "dateRange": "custom",
        "startdt": start_date,
        "enddt": end_date,
        "from": 0,
        "size": 100,
    }

    try:
        resp = requests.get(
            EDGAR_BASE,
            params=params,
            headers={"User-Agent": EDGAR_USER_AGENT},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"SEC EDGAR 请求失败: {e}")
        return []

    hits = data.get("hits", {}).get("hits", [])
    results = []

    for hit in hits:
        src = hit.get("_source", {})
        names = src.get("display_names", [])
        adsh = src.get("adsh", "")
        period = src.get("period_ending", "")

        # 分离人员和公司：公司名含 Corp/Inc/Group/Holdings/Ltd
        person = ""
        company = ""
        ticker = ""
        company_keywords = {"Corp", "Inc", "Group", "Holdings", "Ltd", "LLC", "Co."}
        for name in names:
            clean = name.split("(")[0].strip()
            if any(kw in name for kw in company_keywords):
                company = clean
                # 从公司名推断 ticker
                if "Trump Media" in company:
                    ticker = "DJT"
                elif "PSQ" in company:
                    ticker = "PSQH"
                elif "GrabAGun" in company:
                    ticker = "PEW"
            else:
                person = clean

        # 人名清理：去掉多余空格
        if person:
            person = " ".join(person.split())
        else:
            person = query

        results.append({
            "date": period,
            "person": person,
            "company": company,
            "ticker": ticker,
            "adsh": adsh,
            "filing_url": f"https://www.sec.gov/Archives/edgar/data/{adsh.split('-')[0]}/{adsh.replace('-', '')}/{adsh}-index.htm",
        })

    return results


def fetch_form4_detail(adsh: str) -> Optional[Dict]:
    """获取单个 Form 4 的交易详情

    Args:
        adsh: SEC accession number (如 0001474506-25-000155)

    Returns:
        dict with transaction details, or None
    """
    # 构造 XML URL
    adsh_path = adsh.replace("-", "")
    adsh_prefix = adsh.split("-")[0]
    url = f"https://www.sec.gov/Archives/edgar/data/{adsh_prefix}/{adsh_path}/primary_doc.xml"

    try:
        resp = requests.get(
            url,
            headers={"User-Agent": EDGAR_USER_AGENT},
            timeout=15,
        )
        resp.raise_for_status()
    except Exception:
        return None

    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(resp.content)
    except Exception:
        return None

    # 解析 XML
    result = {
        "period": "",
        "person": "",
        "company": "",
        "ticker": "",
        "is_director": False,
        "is_officer": False,
        "is_ten_percent_owner": False,
        "shares": 0,
        "transaction_code": "",
        "security_type": "",
        "footnotes": [],
    }

    for elem in root.iter():
        tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        text = (elem.text or "").strip()

        if tag == "periodOfReport":
            result["period"] = text
        elif tag == "rptOwnerName":
            result["person"] = text
        elif tag == "issuerName":
            result["company"] = text
        elif tag == "issuerTradingSymbol":
            result["ticker"] = text
        elif tag == "isDirector":
            result["is_director"] = text == "1"
        elif tag == "isOfficer":
            result["is_officer"] = text == "1"
        elif tag == "isTenPercentOwner":
            result["is_ten_percent_owner"] = text == "1"
        elif tag == "transactionCode":
            result["transaction_code"] = text
        elif tag == "footnote" and text:
            result["footnotes"].append(text[:200])

    return result


def convert_sec_to_signals(filing: Dict, detail: Optional[Dict] = None) -> Dict:
    """将 SEC Form 4 转换为信号格式

    Args:
        filing: fetch_sec_form4_filings 返回的 dict
        detail: fetch_form4_detail 返回的 dict

    Returns:
        dict 适配 PoliticalSignalStorage.save()
    """
    person = filing.get("person", "Unknown")
    ticker = filing.get("ticker", "")
    company = filing.get("company", "")

    # 判断信号方向
    direction = "bullish"  # Form 4 买入/授予通常是看涨信号
    trade_action = "buy"
    if detail:
        code = detail.get("transaction_code", "")
        if code == "S":  # 卖出
            direction = "bearish"
            trade_action = "sell"
        elif code == "A":  # 授予/新增
            direction = "bullish"
            trade_action = "grant"
        elif code == "D":  # 处置
            direction = "neutral"
            trade_action = "dispose"

    # 构造 chain_id
    chain_id = f"{ticker or 'UNKNOWN'}-SEC-{filing['date'][:4]}"

    # 构造内容描述
    if detail:
        code_desc = {
            "A": "received/granted",
            "S": "sold",
            "D": "disposed of",
            "P": "purchased",
            "M": "exercised",
        }.get(detail.get("transaction_code", ""), "transacted in")

        content = f"SEC Form 4: {person} {code_desc} {company} ({ticker}) securities"
        content_cn = f"SEC Form 4: {person}{'获得' if trade_action == 'grant' else '卖出' if trade_action == 'sell' else '交易'}{company}({ticker})证券"
    else:
        content = f"SEC Form 4: {person} - {company} ({ticker})"
        content_cn = f"SEC Form 4: {person} - {company}({ticker})"

    return {
        "chain_id": chain_id,
        "date": filing["date"],
        "speaker": person,
        "signal_category": "trade",
        "source": "SEC Form 4",
        "source_url": filing.get("filing_url", ""),
        "content": content,
        "content_cn": content_cn,
        "signal_direction": direction,
        "affected_assets": json.dumps([ticker] if ticker else ["DJT"]),
        "trade_detail": json.dumps({
            "action": trade_action,
            "transaction_code": detail.get("transaction_code", "") if detail else "",
            "is_director": detail.get("is_director", False) if detail else False,
            "is_ten_percent_owner": detail.get("is_ten_percent_owner", False) if detail else False,
        }),
        "impact_note": f"SEC披露{'（10%以上股东）' if detail and detail.get('is_ten_percent_owner') else ''}",
        "verification": "verified",
    }


# ── 新闻抓取（Google News RSS + 白宫 RSS）───────────────────────

# Google News RSS 查询模板
GOOGLE_NEWS_QUERIES = [
    "Trump+stock+market",
    "Trump+tariff",
    "Trump+Dell+OR+Apple+OR+Tesla+OR+Amazon",
    "Trump+buy+sell+invest",
    "Trump+crypto+Bitcoin",
    "Trump+Nvidia+OR+Meta+OR+Google",
]

# AI/半导体板块叙事监控关键词（不要求含 Trump）
# signal_type: "precursor" = 前兆信号（即使 direction 不明确也推送），"direct" = 直接信号
SECTOR_NEWS_QUERIES = {
    "AI_SEMI": {
        "queries": [
            "AI+capex+spending+cut",
            "semiconductor+demand+slowdown",
            "data+center+investment+reduce",
            "AI+bubble+overcapacity",
            "chip+inventory+glut",
        ],
        "signal_type": "direct",
        "sectors": ["XLK"],
        "assets": ["NVDA", "AMD", "AVGO", "MU", "MRVL", "QCOM"],
    },
    "CLOUD": {
        "queries": [
            "cloud+spending+cut+reduce",
            "Meta+capex+rent+out",
            "Microsoft+Azure+slowdown",
            "Amazon+AWS+demand",
            "Google+cloud+investment",
        ],
        "signal_type": "direct",
        "sectors": ["XLK", "XLC"],
        "assets": ["META", "MSFT", "AMZN", "GOOGL"],
    },
    # ── 前兆信号：算力供需失衡（比 capex cut 早 1-3 个月）──
    "SUPPLY_DEMAND": {
        "queries": [
            "GPU+rental+price+drop",
            "data+center+sublease+capacity",
            "GPU+capacity+idle+unused",
            "cloud+instance+discount+pricing",
            "GPU+cloud+oversupply",
            "AI+chip+excess+inventory",
        ],
        "signal_type": "precursor",
        "sectors": ["XLK"],
        "assets": ["NVDA", "AMD", "AVGO", "MU", "MRVL", "META", "MSFT", "AMZN", "GOOGL"],
    },
    # ── 前兆信号：供应链砍单（比 capex cut 早 2-4 个季度）──
    "SUPPLY_CHAIN": {
        "queries": [
            "TSMC+order+cut+reduce",
            "ASML+booking+decline+weak",
            "memory+demand+weak+slowdown",
            "semiconductor+inventory+high+buildup",
            "chip+equipment+order+decline",
            "HBM+inventory+excess",
        ],
        "signal_type": "precursor",
        "sectors": ["XLK"],
        "assets": ["NVDA", "AMD", "AVGO", "MU", "MRVL", "QCOM", "TSM"],
    },
    # ── 前兆信号：建设放缓（比 capex cut 早 1-6 个月）──
    "CONSTRUCTION": {
        "queries": [
            "data+center+construction+delay",
            "data+center+lease+cancellation",
            "data+center+power+demand+revised",
            "hyperscaler+expansion+postpone",
            "data+center+permit+cancel",
        ],
        "signal_type": "precursor",
        "sectors": ["XLK"],
        "assets": ["META", "MSFT", "AMZN", "GOOGL"],
    },
    # ── 前兆信号：管理层措辞/开支审查 ──
    "MANAGEMENT": {
        "queries": [
            "AI+spending+review+efficiency",
            "cloud+growth+slowdown+cautious",
            "hyperscaler+capex+optimization",
            "data+center+hiring+freeze",
            "tech+layoffs+cloud+division",
        ],
        "signal_type": "precursor",
        "sectors": ["XLK", "XLC"],
        "assets": ["META", "MSFT", "AMZN", "GOOGL"],
    },
}

# 白宫 RSS 源
WHITEHOUSE_RSS_FEEDS = [
    "https://www.whitehouse.gov/presidential-actions/feed/",
]

# 新闻标题 → 信号方向的关键词
BULLISH_KEYWORDS = {"buy", "purchase", "invest", "endorse", "praise", "support", "great", "boost", "rally", "expand", "surge", "record", "accelerate"}
BEARISH_KEYWORDS = {
    "sell", "tariff", "restrict", "ban", "sanction", "penalty", "fine", "block", "decline", "crash",
    # 前兆信号相关 bearish 关键词
    "reduce", "cut", "slow", "slowdown", "delay", "scale back", "pullback", "lower", "downgrade",
    "weak", "disappointing", "excess", "oversupply", "idle", "sublease", "cancel", "freeze",
    "drop", "fall", "decrease", "revised down", "warning", "concern", "risk", "fear",
}


def translate_to_cn(text: str) -> str:
    """用免费 Google Translate API 将英文翻译为中文"""
    try:
        r = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": "en", "tl": "zh-CN", "dt": "t", "q": text},
            timeout=10,
        )
        if r.ok:
            return "".join(seg[0] for seg in r.json()[0])
    except Exception:
        pass
    return text  # 翻译失败返回原文


def translate_batch(texts: List[str]) -> List[str]:
    """批量翻译（逐条，避免单次请求过大）"""
    return [translate_to_cn(t) for t in texts]


def _match_assets_from_text(text: str) -> List[str]:
    """从文本中匹配已知资产 ticker"""
    text_upper = text.upper()
    matched = set()
    for name, ticker in OGE_TICKER_MAP.items():
        if name in text_upper:
            matched.add(ticker)
    # 额外匹配常见简称
    extra = {
        "APPLE": "AAPL", "IPHONE": "AAPL",
        "TESLA": "TSLA", "ELON": "TSLA",
        "AMAZON": "AMZN", "AWS": "AMZN",
        "GOOGLE": "GOOGL", "YOUTUBE": "GOOGL",
        "FACEBOOK": "META", "INSTAGRAM": "META",
        "NVIDIA": "NVDA", "GPU": "NVDA",
        "BITCOIN": "BTC", "CRYPTO": "BTC",
        "ETHEREUM": "ETH",
        "DELL": "DELL",
    }
    for keyword, ticker in extra.items():
        if keyword in text_upper:
            matched.add(ticker)
    return list(matched)


def _guess_direction(text: str, query: str = "", signal_type: str = "direct") -> str:
    """从文本猜测信号方向

    Args:
        text: 新闻标题
        query: 搜索查询词（用于前兆信号的语义判断）
        signal_type: "precursor" = 前兆信号，"direct" = 直接信号
    """
    text_lower = text.lower()
    query_lower = query.lower().replace("+", " ")

    bullish_score = sum(1 for kw in BULLISH_KEYWORDS if kw in text_lower)
    bearish_score = sum(1 for kw in BEARISH_KEYWORDS if kw in text_lower)

    # 前兆信号加权：查询词本身含 bearish 语义时，给标题额外 bearish 权重
    if signal_type == "precursor":
        precursor_bearish = {"drop", "idle", "sublease", "cancel", "delay", "freeze",
                             "excess", "oversupply", "weak", "decline", "cut", "reduce",
                             "slow", "slowdown", "revised", "warning", "concern", "risk"}
        if any(kw in query_lower for kw in precursor_bearish):
            bearish_score += 2  # 前兆查询词本身带 bearish 语义，加 2 分

    if bullish_score > bearish_score:
        return "bullish"
    elif bearish_score > bullish_score:
        return "bearish"
    return "neutral"


def _parse_rss_date(date_str: str) -> str:
    """解析 RSS 日期格式为 YYYY-MM-DD"""
    from email.utils import parsedate_to_datetime
    try:
        dt = parsedate_to_datetime(date_str)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d")


class NewsFetcher:
    """公开喊话和新闻自动抓取（Google News RSS + 白宫 RSS）"""

    def __init__(self):
        self.user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"

    def fetch_statements(self, days_back: int = 7) -> List[Dict]:
        """抓取最近 N 天的公开喊话

        Args:
            days_back: 回溯天数

        Returns:
            list of signal dicts
        """
        signals = []
        signals.extend(self._fetch_google_news(days_back))
        signals.extend(self._fetch_sector_news(days_back))
        signals.extend(self._fetch_whitehouse_rss())
        return signals

    def _fetch_google_news(self, days_back: int) -> List[Dict]:
        """从 Google News RSS 抓取 Trump 相关新闻"""
        import xml.etree.ElementTree as ET

        signals = []
        cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

        for query in GOOGLE_NEWS_QUERIES:
            url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
            try:
                resp = requests.get(url, headers={"User-Agent": self.user_agent}, timeout=15)
                if resp.status_code != 200:
                    continue
                root = ET.fromstring(resp.text)
            except Exception:
                continue

            for item in root.findall(".//item"):
                title_el = item.find("title")
                link_el = item.find("link")
                pub_el = item.find("pubDate")
                source_el = item.find("source")

                if title_el is None or title_el.text is None:
                    continue

                title = title_el.text.strip()
                link = link_el.text.strip() if link_el is not None and link_el.text else ""
                pub_date = _parse_rss_date(pub_el.text) if pub_el is not None and pub_el.text else ""
                source_name = source_el.text.strip() if source_el is not None and source_el.text else "Google News"

                # 过滤：必须含 Trump
                if "trump" not in title.lower():
                    continue

                # 过滤日期
                if pub_date and pub_date < cutoff:
                    continue

                # 匹配资产
                assets = _match_assets_from_text(title)
                if not assets:
                    continue

                direction = _guess_direction(title)

                signals.append({
                    "chain_id": f"{assets[0]}-NEWS-{pub_date[:7]}",
                    "date": pub_date,
                    "speaker": "Donald Trump",
                    "signal_category": "statement",
                    "source": f"新闻 ({source_name})",
                    "source_url": link,
                    "content": title,
                    "content_cn": translate_to_cn(title),
                    "signal_direction": direction,
                    "affected_assets": json.dumps(assets),
                    "trade_detail": None,
                    "impact_note": "Google News 自动抓取",
                    "verification": "unverified",
                })

        return signals

    def _fetch_sector_news(self, days_back: int = 7) -> List[Dict]:
        """抓取 AI/半导体板块叙事新闻（不要求含 Trump）"""
        import xml.etree.ElementTree as ET

        signals = []
        cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

        for sector_key, config in SECTOR_NEWS_QUERIES.items():
            signal_type = config.get("signal_type", "direct")
            for query in config["queries"]:
                url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
                try:
                    resp = requests.get(url, headers={"User-Agent": self.user_agent}, timeout=15)
                    if resp.status_code != 200:
                        continue
                    root = ET.fromstring(resp.text)
                except Exception:
                    continue

                for item in root.findall(".//item"):
                    title_el = item.find("title")
                    link_el = item.find("link")
                    pub_el = item.find("pubDate")
                    source_el = item.find("source")

                    if title_el is None or title_el.text is None:
                        continue

                    title = title_el.text.strip()
                    link = link_el.text.strip() if link_el is not None and link_el.text else ""
                    pub_date = _parse_rss_date(pub_el.text) if pub_el is not None and pub_el.text else ""
                    source_name = source_el.text.strip() if source_el is not None and source_el.text else "Google News"

                    # 过滤日期
                    if pub_date and pub_date < cutoff:
                        continue

                    # 匹配资产（从文本 + 配置中合并）
                    assets = _match_assets_from_text(title)
                    # 补充配置中的关联资产
                    for a in config.get("assets", []):
                        if a not in assets:
                            assets.append(a)

                    if not assets:
                        assets = config.get("assets", [])[:3]  # 取前 3 个作为默认

                    direction = _guess_direction(title, query, signal_type)

                    signals.append({
                        "chain_id": f"SECTOR-{sector_key}-{pub_date[:7]}",
                        "date": pub_date,
                        "speaker": "行业新闻",
                        "signal_category": "event",
                        "source": f"行业新闻 ({source_name})",
                        "source_url": link,
                        "content": title,
                        "content_cn": translate_to_cn(title),
                        "signal_direction": direction,
                        "affected_assets": json.dumps(assets),
                        "trade_detail": None,
                        "impact_note": f"板块叙事监控: {sector_key} [{signal_type}]",
                        "verification": "unverified",
                        "signal_type": signal_type,
                    })

        return signals

    def _fetch_whitehouse_rss(self) -> List[Dict]:
        """从白宫 RSS 抓取行政令和声明"""
        import xml.etree.ElementTree as ET

        signals = []

        for feed_url in WHITEHOUSE_RSS_FEEDS:
            try:
                resp = requests.get(feed_url, headers={"User-Agent": self.user_agent}, timeout=15)
                if resp.status_code != 200:
                    continue
                root = ET.fromstring(resp.text)
            except Exception:
                continue

            for item in root.findall(".//item"):
                title_el = item.find("title")
                link_el = item.find("link")
                pub_el = item.find("pubDate")
                desc_el = item.find("description")

                if title_el is None or title_el.text is None:
                    continue

                title = title_el.text.strip()
                link = link_el.text.strip() if link_el is not None and link_el.text else ""
                pub_date = _parse_rss_date(pub_el.text) if pub_el is not None and pub_el.text else ""
                desc = desc_el.text.strip() if desc_el is not None and desc_el.text else ""

                # 匹配资产（从标题+描述）
                full_text = f"{title} {desc}"
                assets = _match_assets_from_text(full_text)

                # 即使没匹配到资产，行政令也可能很重要
                if not assets:
                    # 检查是否是经济/市场相关的行政令
                    market_keywords = {"tariff", "trade", "economy", "financial", "tax", "regulation",
                                       "import", "export", "sanction", "executive order"}
                    if not any(kw in title.lower() for kw in market_keywords):
                        continue
                    assets = ["SPY"]  # 宽基影响

                direction = _guess_direction(title)

                # 判断类型
                category = "event"
                if "executive order" in title.lower():
                    category = "event"
                elif "statement" in title.lower() or "remarks" in title.lower():
                    category = "statement"

                signals.append({
                    "chain_id": f"WH-{pub_date[:7]}",
                    "date": pub_date,
                    "speaker": "白宫",
                    "signal_category": category,
                    "source": "白宫官方",
                    "source_url": link,
                    "content": title,
                    "content_cn": translate_to_cn(title),
                    "signal_direction": direction,
                    "affected_assets": json.dumps(assets),
                    "trade_detail": None,
                    "impact_note": "白宫RSS自动抓取",
                    "verification": "verified",
                })

        return signals


class SignalFetcher:
    """政治信号自动获取器"""

    def __init__(self):
        self.last_fetch_time: Optional[datetime] = None

    def fetch_all(self, days_back: int = 365, include_ptr: bool = False) -> List[Dict]:
        """获取所有可用信号

        Args:
            days_back: 回溯天数
            include_ptr: 是否获取 PTR（OCR 解析较慢，默认关闭）

        Returns:
            list of signal dicts ready for storage
        """
        start_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        end_date = datetime.now().strftime("%Y-%m-%d")

        signals = []

        # 1. SEC EDGAR Form 4
        print("正在获取 SEC EDGAR 数据...")
        sec_signals = self._fetch_sec_signals(start_date, end_date)
        signals.extend(sec_signals)
        print(f"  SEC: 获取到 {len(sec_signals)} 条")

        # 2. OGE 电子披露系统
        print("正在获取 OGE 财务披露...")
        oge_signals = self._fetch_oge_signals()
        signals.extend(oge_signals)
        print(f"  OGE: 获取到 {len(oge_signals)} 条")

        # 3. White House PTR（可选，OCR 较慢）
        if include_ptr:
            print("正在获取 White House PTR...")
            ptr_signals = self._fetch_ptr_signals()
            signals.extend(ptr_signals)
            print(f"  PTR: 获取到 {len(ptr_signals)} 条")

        self.last_fetch_time = datetime.now()
        return signals

    def _fetch_oge_signals(self) -> List[Dict]:
        """获取 OGE 财务披露信号"""
        text = fetch_oge_from_web()
        if text:
            return parse_oge278e_text(text)
        return []

    def _fetch_ptr_signals(self) -> List[Dict]:
        """获取 White House PTR 信号（OCR 解析）"""
        return fetch_and_parse_latest_ptr()

    def import_oge_pdf(self, pdf_path: str) -> List[Dict]:
        """导入 OGE 278e PDF 文件

        Args:
            pdf_path: PDF 文件路径

        Returns:
            list of signal dicts
        """
        return parse_oge278e_pdf(pdf_path)

    def import_ptr_pdf(self, pdf_path: str) -> List[Dict]:
        """导入 PTR PDF 文件（OCR 解析）

        Args:
            pdf_path: PDF 文件路径

        Returns:
            list of signal dicts
        """
        return parse_ptr_pdf(pdf_path)

    def _fetch_sec_signals(self, start_date: str, end_date: str) -> List[Dict]:
        """获取 SEC EDGAR 信号"""
        signals = []

        # 搜索 Trump 相关的 Form 4
        filings = fetch_sec_form4_filings(start_date, end_date, "Trump")

        for filing in filings:
            # 获取详情
            detail = None
            if filing.get("adsh"):
                detail = fetch_form4_detail(filing["adsh"])
                time.sleep(0.15)  # SEC 限速：10 req/sec

            signal = convert_sec_to_signals(filing, detail)
            signals.append(signal)

        return signals

    def fetch_and_store(self, storage, days_back: int = 365) -> int:
        """获取信号并存储到数据库

        Args:
            storage: PoliticalSignalStorage 实例
            days_back: 回溯天数

        Returns:
            新增的信号数量
        """
        signals = self.fetch_all(days_back)
        return self._save_new(storage, signals)

    def fetch_incremental(self, storage, days_back: int = 30) -> Dict[str, int]:
        """增量获取：只取最近 N 天，跳过已存在记录

        Args:
            storage: PoliticalSignalStorage 实例
            days_back: SEC 回溯天数（新闻固定 7 天）

        Returns:
            dict with keys "sec", "news" → 新增条数
        """
        result = {"sec": 0, "news": 0}

        # 1. SEC EDGAR（最近 N 天）
        start_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        end_date = datetime.now().strftime("%Y-%m-%d")
        sec_signals = self._fetch_sec_signals(start_date, end_date)
        result["sec"] = self._save_new(storage, sec_signals)

        # 2. 新闻（最近 7 天）
        news_fetcher = NewsFetcher()
        news_signals = news_fetcher.fetch_statements(days_back=7)
        result["news"] = self._save_new(storage, news_signals)

        return result

    def _save_new(self, storage, signals: List[Dict]) -> int:
        """保存新信号（自动去重）

        Args:
            storage: PoliticalSignalStorage 实例
            signals: 信号列表

        Returns:
            新增条数
        """
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
        return count
