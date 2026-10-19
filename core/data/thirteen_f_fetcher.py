"""SEC EDGAR 13-F 机构持仓数据获取器"""

import logging
import re
import time
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

EDGAR_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
EDGAR_ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"
EDGAR_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
EDGAR_USER_AGENT = "MarketPulse research@marketpulse.com"
REQUEST_DELAY = 0.12  # SEC 限制 10 req/sec


class ThirteenFFetcher:
    """SEC EDGAR 13-F 数据获取器"""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": EDGAR_USER_AGENT})

    def _get(self, url: str, timeout: int = 30) -> Optional[requests.Response]:
        """带限速和重试的 GET 请求"""
        for attempt in range(3):
            time.sleep(REQUEST_DELAY)
            try:
                resp = self.session.get(url, timeout=timeout)
                if resp.status_code == 429:
                    wait = 2 ** (attempt + 1)
                    logger.warning(f"429 rate limit, waiting {wait}s...")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp
            except requests.RequestException as e:
                if attempt == 2:
                    logger.error(f"Request failed after 3 attempts: {url} - {e}")
                    return None
                time.sleep(2 ** (attempt + 1))
        return None

    # ── SEC API 交互 ──────────────────────────────────────────

    def fetch_submissions(self, cik: str) -> Optional[Dict]:
        """获取机构的全部提交历史"""
        cik_padded = cik.zfill(10)
        url = EDGAR_SUBMISSIONS_URL.format(cik=cik_padded)
        resp = self._get(url)
        if not resp:
            return None
        try:
            return resp.json()
        except Exception as e:
            logger.error(f"Failed to parse submissions JSON for CIK {cik}: {e}")
            return None

    def find_latest_13f(self, submissions: Dict) -> Optional[Dict]:
        """从提交历史中找到最新的 13F-HR 文件"""
        recent = submissions.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        for i, form in enumerate(forms):
            if form in ("13F-HR", "13F-HR/A"):
                return {
                    "accession_number": recent["accessionNumber"][i],
                    "filing_date": recent["filingDate"][i],
                    "report_date": recent.get("reportDate", [None] * len(forms))[i],
                    "primary_doc": recent.get("primaryDocument", [""] * len(forms))[i],
                    "form": form,
                }
        return None

    def find_all_13f(self, submissions: Dict) -> List[Dict]:
        """从提交历史中找到所有 13F-HR 文件（用于历史回填）"""
        recent = submissions.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        results = []
        for i, form in enumerate(forms):
            if form in ("13F-HR", "13F-HR/A"):
                results.append({
                    "accession_number": recent["accessionNumber"][i],
                    "filing_date": recent["filingDate"][i],
                    "report_date": recent.get("reportDate", [None] * len(forms))[i],
                    "primary_doc": recent.get("primaryDocument", [""] * len(forms))[i],
                    "form": form,
                })
        return results

    def _get_info_table_url(self, cik: str, accession_number: str) -> Optional[str]:
        """找到 infoTable XML 文件的 URL

        策略：解析 filing index 页面找到所有 XML 文件，
        选择非 primary_doc 的 XML（通常是 infoTable）
        """
        cik_int = str(int(cik))
        acc_nodash = accession_number.replace("-", "")
        index_url = f"{EDGAR_ARCHIVES_BASE}/{cik_int}/{acc_nodash}/{accession_number}-index.htm"

        resp = self._get(index_url)
        if not resp:
            return None

        # 从 index 页面提取 XML 链接
        xml_links = re.findall(r'href="([^"]+\.xml)"', resp.text, re.IGNORECASE)
        base_url = f"{EDGAR_ARCHIVES_BASE}/{cik_int}/{acc_nodash}"

        # 优先找非 primary_doc 的 XML（infoTable）
        for link in xml_links:
            if "primary_doc" not in link and "xslForm13F" not in link:
                # 转为绝对 URL
                if link.startswith("/"):
                    return f"https://www.sec.gov{link}"
                else:
                    return f"{base_url}/{link}"

        # 备选：尝试 common info table filenames
        for link in xml_links:
            if "primary_doc" not in link:
                if link.startswith("/"):
                    return f"https://www.sec.gov{link}"
                elif "xslForm13F" not in link:
                    return f"{base_url}/{link}"

        return None

    def fetch_info_table(self, cik: str, accession_number: str) -> Optional[bytes]:
        """下载 infoTable XML"""
        url = self._get_info_table_url(cik, accession_number)
        if not url:
            return None
        resp = self._get(url)
        return resp.content if resp else None

    def fetch_primary_doc_metadata(self, cik: str, accession_number: str,
                                    primary_doc: str) -> Dict:
        """从 primary_doc.xml 提取元数据（manager name, period of report）"""
        cik_int = str(int(cik))
        acc_nodash = accession_number.replace("-", "")

        # primary_doc 路径可能是 xslForm13F_X02/primary_doc.xml 或直接 primary_doc.xml
        for path in [primary_doc, "primary_doc.xml"]:
            url = f"{EDGAR_ARCHIVES_BASE}/{cik_int}/{acc_nodash}/{path}"
            resp = self._get(url)
            if not resp:
                continue
            try:
                root = ET.fromstring(resp.content)
                meta = {"manager_name": "", "period_of_report": ""}
                for elem in root.iter():
                    tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                    text = (elem.text or "").strip()
                    if tag == "name" and not meta["manager_name"]:
                        meta["manager_name"] = text
                    elif tag == "periodOfReport":
                        meta["period_of_report"] = text
                return meta
            except ET.ParseError:
                continue
        return {"manager_name": "", "period_of_report": ""}

    # ── XML 解析 ──────────────────────────────────────────────

    def parse_13f_xml(self, xml_content: bytes) -> List[Dict]:
        """解析 infoTable XML，提取持仓数据，按 CUSIP 聚合"""
        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError as e:
            logger.error(f"XML parse error: {e}")
            return []

        raw = []
        for elem in root.iter():
            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if tag == "infoTable":
                holding = self._parse_info_table_entry(elem)
                if holding and holding.get("cusip"):
                    raw.append(holding)

        # 同一 filing 中同一 CUSIP 可能出现多次（不同 otherManager），聚合
        aggregated = {}
        for h in raw:
            cusip = h["cusip"]
            if cusip not in aggregated:
                aggregated[cusip] = {
                    "name_of_issuer": h["name_of_issuer"],
                    "title_of_class": h.get("title_of_class", ""),
                    "cusip": cusip,
                    "value": 0,
                    "shares": 0,
                    "shares_type": h.get("shares_type", "SH"),
                }
            aggregated[cusip]["value"] += h.get("value", 0)
            aggregated[cusip]["shares"] += h.get("shares", 0)

        return list(aggregated.values())

    def _parse_info_table_entry(self, elem) -> Optional[Dict]:
        """解析单个 infoTable 条目"""
        holding = {}
        for child in elem.iter():
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            text = (child.text or "").strip()

            if tag == "nameOfIssuer":
                holding["name_of_issuer"] = text
            elif tag == "titleOfClass":
                holding["title_of_class"] = text
            elif tag == "cusip":
                holding["cusip"] = text
            elif tag == "value":
                try:
                    holding["value"] = int(text)  # 单位：美元
                except ValueError:
                    holding["value"] = 0
            elif tag == "sshPrnamt":
                try:
                    holding["shares"] = int(text)
                except ValueError:
                    holding["shares"] = 0
            elif tag == "sshPrnamtType":
                holding["shares_type"] = text

        return holding if holding.get("cusip") else None

    # ── 高层方法 ──────────────────────────────────────────────

    def fetch_latest_holdings(self, cik: str, manager_name: str) -> Tuple[List[Dict], Dict]:
        """获取某机构最新一期持仓

        Returns:
            (holdings_list, filing_metadata)
            filing_metadata 包含: filing_id, filing_date, period_end, manager_name
        """
        submissions = self.fetch_submissions(cik)
        if not submissions:
            return [], {}

        latest = self.find_latest_13f(submissions)
        if not latest:
            return [], {}

        # 获取元数据
        meta = self.fetch_primary_doc_metadata(
            cik, latest["accession_number"], latest["primary_doc"]
        )

        # 获取 infoTable
        xml_content = self.fetch_info_table(cik, latest["accession_number"])
        if not xml_content:
            return [], {}

        holdings = self.parse_13f_xml(xml_content)

        # 转换 report_date 格式 (YYYY-MM-DD -> YYYY-MM-DD, 已经是)
        period_end = latest.get("report_date") or latest["filing_date"]

        # 补充 filing 元数据到每条持仓
        for h in holdings:
            h["filing_id"] = latest["accession_number"]
            h["cik"] = cik.zfill(10)
            h["manager_name"] = meta.get("manager_name") or manager_name
            h["filing_date"] = latest["filing_date"]
            h["period_end"] = period_end

        filing_meta = {
            "filing_id": latest["accession_number"],
            "filing_date": latest["filing_date"],
            "period_end": period_end,
            "manager_name": meta.get("manager_name") or manager_name,
            "form": latest["form"],
        }

        return holdings, filing_meta

    def fetch_and_store_new(self, storage) -> Dict:
        """检查所有跟踪机构，获取并存储新报告

        Returns:
            {
                "new_filings": int,
                "new_holdings": int,
                "errors": int,
                "updated_managers": [
                    {"cik": str, "name": str, "period_end": str,
                     "holdings_count": int, "accession": str},
                    ...
                ]
            }
        """
        managers = storage.get_tracked_managers()
        if managers.empty:
            return {"new_filings": 0, "new_holdings": 0, "errors": 0,
                    "updated_managers": []}

        new_filings = 0
        new_holdings = 0
        errors = 0
        updated_managers = []

        for _, mgr in managers.iterrows():
            cik = mgr["cik"]
            name = mgr["name"]
            try:
                # 检查是否有新报告
                submissions = self.fetch_submissions(cik)
                if not submissions:
                    errors += 1
                    continue

                latest = self.find_latest_13f(submissions)
                if not latest:
                    logger.info(f"  {name}: 无 13F 文件")
                    continue

                # 与本地最新日期比较
                local_latest = storage.get_latest_filing_date(cik)
                if local_latest and latest["filing_date"] <= local_latest:
                    continue

                # 获取并存储
                holdings, meta = self.fetch_latest_holdings(cik, name)
                if holdings:
                    count = storage.save_holdings(holdings)
                    new_filings += 1
                    new_holdings += count
                    updated_managers.append({
                        "cik": cik.zfill(10),
                        "name": meta.get("manager_name") or name,
                        "period_end": meta.get("period_end", ""),
                        "holdings_count": len(holdings),
                        "accession": latest["accession_number"],
                    })
                    logger.info(
                        f"  {name}: {meta.get('period_end', '?')} "
                        f"{len(holdings)} 条持仓 (新增 {count})"
                    )
                else:
                    logger.warning(f"  {name}: 解析失败或无持仓数据")

            except Exception as e:
                logger.error(f"  {name} 失败: {e}")
                errors += 1

        return {
            "new_filings": new_filings,
            "new_holdings": new_holdings,
            "errors": errors,
            "updated_managers": updated_managers,
        }

    def fetch_holdings_for_filing(self, cik: str, filing: Dict,
                                   manager_name: str) -> Tuple[List[Dict], Dict]:
        """获取指定 filing 的持仓数据

        Args:
            cik: 机构 CIK
            filing: find_latest_13f/find_all_13f 返回的 filing dict
            manager_name: 机构名称

        Returns:
            (holdings_list, filing_metadata)
        """
        meta = self.fetch_primary_doc_metadata(
            cik, filing["accession_number"], filing["primary_doc"]
        )
        xml_content = self.fetch_info_table(cik, filing["accession_number"])
        if not xml_content:
            return [], {}

        holdings = self.parse_13f_xml(xml_content)
        period_end = filing.get("report_date") or filing["filing_date"]

        for h in holdings:
            h["filing_id"] = filing["accession_number"]
            h["cik"] = cik.zfill(10)
            h["manager_name"] = meta.get("manager_name") or manager_name
            h["filing_date"] = filing["filing_date"]
            h["period_end"] = period_end

        filing_meta = {
            "filing_id": filing["accession_number"],
            "filing_date": filing["filing_date"],
            "period_end": period_end,
            "manager_name": meta.get("manager_name") or manager_name,
            "form": filing["form"],
        }
        return holdings, filing_meta

    def fetch_and_store_history(self, storage, lookback: int = 4) -> Dict[str, int]:
        """回填所有跟踪机构的最近 N 期历史持仓

        Args:
            storage: ThirteenFStorage 实例
            lookback: 回溯的 filing 数量（默认 4，即约 1 年）

        Returns:
            {"new_filings": int, "new_holdings": int, "errors": int}
        """
        managers = storage.get_tracked_managers()
        if managers.empty:
            return {"new_filings": 0, "new_holdings": 0, "errors": 0}

        new_filings = 0
        new_holdings = 0
        errors = 0

        for _, mgr in managers.iterrows():
            cik = mgr["cik"]
            name = mgr["name"]
            try:
                submissions = self.fetch_submissions(cik)
                if not submissions:
                    errors += 1
                    continue

                all_13f = self.find_all_13f(submissions)
                if not all_13f:
                    logger.info(f"  {name}: 无 13F 文件")
                    continue

                # 只取最近 N 个不同 period_end 的 filing
                seen_periods = set()
                filings_to_fetch = []
                for f in all_13f:
                    period = f.get("report_date") or f["filing_date"]
                    if period not in seen_periods:
                        seen_periods.add(period)
                        filings_to_fetch.append(f)
                    if len(filings_to_fetch) >= lookback:
                        break

                for filing in filings_to_fetch:
                    holdings, meta = self.fetch_holdings_for_filing(cik, filing, name)
                    if holdings:
                        count = storage.save_holdings(holdings)
                        new_filings += 1
                        new_holdings += count
                        logger.info(
                            f"  {name}: {meta.get('period_end', '?')} "
                            f"{len(holdings)} 条 (新增 {count})"
                        )

            except Exception as e:
                logger.error(f"  {name} 失败: {e}")
                errors += 1

        return {
            "new_filings": new_filings,
            "new_holdings": new_holdings,
            "errors": errors,
        }

    def search_edgar_by_name(self, query: str) -> List[Dict]:
        """通过 EDGAR 搜索机构名（用于添加新跟踪机构）"""
        params = {
            "q": f'"{query}"',
            "forms": "13F-HR",
            "dateRange": "custom",
            "startdt": "2024-01-01",
            "enddt": "2026-12-31",
            "size": 10,
        }
        resp = self._get(f"{EDGAR_SEARCH_URL}?{'&'.join(f'{k}={v}' for k,v in params.items())}")
        if not resp:
            return []

        try:
            data = resp.json()
        except Exception:
            return []

        results = []
        seen_ciks = set()
        for hit in data.get("hits", {}).get("hits", []):
            src = hit.get("_source", {})
            display = src.get("display_names", [""])[0]
            # 解析 "NAME (CIK 000xxxxxxx)"
            match = re.search(r"(.+?)\s*\(CIK\s+(\d+)\)", display)
            if match:
                name = match.group(1).strip()
                cik = match.group(2)
                if cik not in seen_ciks:
                    seen_ciks.add(cik)
                    results.append({"name": name, "cik": cik})

        return results
