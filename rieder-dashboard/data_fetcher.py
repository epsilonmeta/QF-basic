"""
Data fetcher – multi-source, scrape-first approach.

Yield curves
  US      → Treasury.gov XML (primary) → FRED → yfinance
  EU      → ECB SDMX REST API         → yfinance
  Japan   → MoF Japan CSV             → yfinance
  UK      → BoE IADB CSV              → yfinance
  Canada  → BoC Valet JSON            → yfinance

Corporate bonds
  Yields  → FRED via pandas-datareader (no key needed) → ETF proxies
  Spreads → FRED OAS series           → ETF spread proxy

Corporate defaults
  Events  → SEC EDGAR 8-K full-text search (free API)
  News    → Reuters / Bloomberg RSS with bankruptcy keywords

Macro / Sectors / News  (unchanged from v1)
"""

import os
import time
import logging
import xml.etree.ElementTree as ET
from io import StringIO
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import requests
import feedparser
import yfinance as yf
import pandas_datareader.data as web
from bs4 import BeautifulSoup

try:
    from fredapi import Fred as _FredLib
except ImportError:
    _FredLib = None

logger = logging.getLogger(__name__)

# ── Shared headers for polite scraping ───────────────────────────────────────
_HDRS = {
    "User-Agent": "GlobalMacroDashboard/2.0 (educational; epsilonright@gmail.com)",
    "Accept":     "application/json, text/html, text/csv, */*",
}

# ── US Treasury XML ───────────────────────────────────────────────────────────
_TREASURY_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "m":    "http://schemas.microsoft.com/ado/2007/08/dataservices/metadata",
    "d":    "http://schemas.microsoft.com/ado/2007/08/dataservices",
}
_TREASURY_FIELDS = {
    "BC_1MONTH":  "1M",  "BC_2MONTH":  "2M",  "BC_3MONTH":  "3M",
    "BC_6MONTH":  "6M",  "BC_1YEAR":   "1Y",  "BC_2YEAR":   "2Y",
    "BC_3YEAR":   "3Y",  "BC_5YEAR":   "5Y",  "BC_7YEAR":   "7Y",
    "BC_10YEAR":  "10Y", "BC_20YEAR":  "20Y", "BC_30YEAR":  "30Y",
}

# ── ECB yield curve tenors ────────────────────────────────────────────────────
_ECB_TENORS = ["SR_1Y","SR_2Y","SR_3Y","SR_5Y","SR_7Y","SR_10Y","SR_15Y","SR_20Y","SR_30Y"]
_ECB_LABELS = ["1Y",   "2Y",   "3Y",   "5Y",   "7Y",   "10Y",   "15Y",   "20Y",   "30Y"]

# ── Bank of Canada Valet series codes → labels ────────────────────────────────
_BOC_SERIES = {
    "V39051": "1M", "V39052": "3M", "V39053": "6M",
    "V39054": "1Y", "V39055": "2Y", "V39057": "3Y",
    "V39058": "5Y", "V39059": "7Y", "V39060": "10Y",
    "V39065": "30Y",
}

# ── BoE IADB par-yield series codes ───────────────────────────────────────────
_BOE_SERIES = {
    "IUMAJPQ": "1Y", "IUMAJPR": "2Y", "IUMAJPU": "5Y",
    "IUMAJPW": "10Y", "IUMAJPY": "20Y", "IUMAJQA": "30Y",
}

# ── Sector ETFs / macro / global rates (unchanged) ───────────────────────────
SECTOR_ETFS = {
    "Technology": "XLK", "Financials": "XLF", "Healthcare": "XLV",
    "Energy": "XLE", "Consumer Disc.": "XLY", "Consumer Staples": "XLP",
    "Industrials": "XLI", "Materials": "XLB", "Utilities": "XLU",
    "Real Estate": "XLRE", "Communication": "XLC",
}
GLOBAL_RATES_YF = {
    "US 10Y":  "^TNX",       "US 2Y":   "^IRX",
    "DE 10Y":  "^DE10YT=RR", "JP 10Y":  "^JP10YT=RR",
    "UK 10Y":  "^GB10YT=RR", "CN 10Y":  "^CN10YT=RR",
    "CA 10Y":  "^CA10YT=RR",
}
NEWS_RSS_FEEDS = [
    ("Reuters Markets",    "https://feeds.reuters.com/reuters/businessNews"),
    ("WSJ Markets",        "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"),
    ("Seeking Alpha",      "https://seekingalpha.com/feed.xml"),
    ("Yahoo Finance",      "https://finance.yahoo.com/news/rssindex"),
]

# ── FRED series for corporate bonds (accessible via pandas-datareader, no key) ─
CORP_YIELD_SERIES = {
    "AAA":  "BAMLC0A1CAAAEY",
    "AA":   "BAMLC0A2CAAEY",
    "A":    "BAMLC0A3CAEY",
    "BBB":  "BAMLC0A4CBBBEY",
    "HY":   "BAMLH0A0HYM2EY",
    "IG":   "BAMLC0A0CMEY",
}
CORP_OAS_SERIES = {
    "IG OAS":  "BAMLC0A0CMOAS",
    "HY OAS":  "BAMLH0A0HYM2OAS",
    "BBB OAS": "BAMLC0A4CBBBOAS",
    "BB OAS":  "BAMLH0A1HYBBOAS",
    "B OAS":   "BAMLH0A2HYBOAS",
}


def _get(url, timeout=12, **kwargs) -> requests.Response | None:
    """GET with shared headers; returns None on failure."""
    try:
        resp = requests.get(url, headers=_HDRS, timeout=timeout, **kwargs)
        resp.raise_for_status()
        return resp
    except Exception as e:
        logger.debug(f"GET {url} → {e}")
        return None


class DataFetcher:
    def __init__(self, fred_api_key: str = ""):
        self.fred_api_key = fred_api_key or os.getenv("FRED_API_KEY", "")
        self._fred = _FredLib(api_key=self.fred_api_key) if (self.fred_api_key and _FredLib) else None

    # ══════════════════════════════════════════════════════════════════════════
    # YIELD CURVES
    # ══════════════════════════════════════════════════════════════════════════

    # ── US Treasuries ─────────────────────────────────────────────────────────

    def get_us_yield_curve(self, lookback_days: int = 365) -> pd.DataFrame:
        """
        Historical US Treasury yield curve.
        Primary: Treasury.gov XML API  |  Fallback: FRED → yfinance
        """
        end   = datetime.today()
        start = end - timedelta(days=lookback_days)

        df = self._us_curve_treasury(start, end)
        if not df.empty:
            return df

        if self._fred:
            try:
                frames = {}
                for field, label in _TREASURY_FIELDS.items():
                    sid = field.replace("BC_", "DGS").replace("MONTH", "MO").replace("YEAR", "").replace("_", "")
                    # Map to correct FRED series IDs
                sid_map = {
                    "1M": "DGS1MO", "3M": "DGS3MO", "6M": "DGS6MO",
                    "1Y": "DGS1",   "2Y": "DGS2",   "3Y": "DGS3",
                    "5Y": "DGS5",   "7Y": "DGS7",   "10Y": "DGS10",
                    "20Y": "DGS20", "30Y": "DGS30",
                }
                for label, sid in sid_map.items():
                    try:
                        frames[label] = self._fred.get_series(sid, start, end)
                    except Exception:
                        pass
                if frames:
                    out = pd.DataFrame(frames)
                    out.index = pd.to_datetime(out.index)
                    return out.dropna(how="all")
            except Exception as e:
                logger.warning(f"FRED US curve: {e}")

        return self._us_curve_yf(start, end)

    def _us_curve_treasury(self, start: datetime, end: datetime) -> pd.DataFrame:
        years = list(range(start.year, end.year + 1))
        records = []
        for year in years:
            url = (
                "https://home.treasury.gov/resource-center/data-chart-center/"
                f"interest-rates/pages/xml?data=daily_treasury_yield_curve"
                f"&field_tdate_value={year}"
            )
            resp = _get(url, timeout=15)
            if resp is None:
                continue
            try:
                root = ET.fromstring(resp.content)
                for entry in root.findall(".//atom:entry", _TREASURY_NS):
                    content = entry.find("atom:content", _TREASURY_NS)
                    if content is None:
                        continue
                    props = content.find("m:properties", _TREASURY_NS)
                    if props is None:
                        continue
                    date_el = props.find("d:NEW_DATE", _TREASURY_NS)
                    if date_el is None or not date_el.text:
                        continue
                    date = pd.to_datetime(date_el.text[:10])
                    if date < pd.Timestamp(start) or date > pd.Timestamp(end):
                        continue
                    row: dict = {"date": date}
                    for field, label in _TREASURY_FIELDS.items():
                        el = props.find(f"d:{field}", _TREASURY_NS)
                        if el is not None and el.text:
                            try:
                                row[label] = float(el.text)
                            except ValueError:
                                pass
                    records.append(row)
            except Exception as e:
                logger.warning(f"Treasury XML parse {year}: {e}")

        if not records:
            return pd.DataFrame()
        df = pd.DataFrame(records).set_index("date").sort_index()
        # keep ordered tenors
        tenor_order = ["1M","2M","3M","6M","1Y","2Y","3Y","5Y","7Y","10Y","20Y","30Y"]
        cols = [c for c in tenor_order if c in df.columns]
        return df[cols].dropna(how="all")

    def _us_curve_yf(self, start, end) -> pd.DataFrame:
        tickers = {"3M": "^IRX", "5Y": "^FVX", "10Y": "^TNX", "30Y": "^TYX"}
        raw = yf.download(list(tickers.values()), start=start, end=end, progress=False)["Close"]
        if isinstance(raw, pd.Series):
            raw = raw.to_frame()
        raw.columns = list(tickers.keys())[:raw.shape[1]]
        return raw.dropna(how="all")

    # ── EU / ECB ──────────────────────────────────────────────────────────────

    def get_eu_yield_curve(self) -> pd.Series:
        """ECB AAA-rated euro area government bond spot yields (today's snapshot)."""
        records = {}
        for tenor_code, label in zip(_ECB_TENORS, _ECB_LABELS):
            series_key = f"YC/B.U2.EUR.4F.G_N_A.SV_C_YM.{tenor_code}"
            url = (
                f"https://data-api.ecb.europa.eu/service/data/{series_key}"
                "?lastNObservations=1&format=jsondata"
            )
            resp = _get(url, timeout=10)
            if resp is None:
                continue
            try:
                data = resp.json()
                series = data["dataSets"][0]["series"]
                for _, sv in series.items():
                    obs = sv.get("observations", {})
                    if obs:
                        latest = obs[max(obs.keys(), key=int)]
                        records[label] = float(latest[0])
                    break
            except Exception as e:
                logger.debug(f"ECB {tenor_code}: {e}")
            time.sleep(0.1)

        if records:
            return pd.Series(records, name="EU Bund").sort_index()

        # yfinance fallback
        logger.warning("ECB API unavailable – falling back to yfinance for EU curve")
        for label, tkr in {"2Y": "^DE2YT=RR", "5Y": "^DE5YT=RR",
                           "10Y": "^DE10YT=RR", "30Y": "^DE30YT=RR"}.items():
            try:
                h = yf.Ticker(tkr).history(period="5d")["Close"].dropna()
                if not h.empty:
                    records[label] = float(h.iloc[-1])
            except Exception:
                pass
        return pd.Series(records, name="EU Bund").sort_index()

    # ── Japan JGB (Ministry of Finance) ──────────────────────────────────────

    def get_japan_yield_curve(self) -> pd.Series:
        """Japan JGB yields from Ministry of Finance CSV."""
        url = ("https://www.mof.go.jp/english/policy/jgbs/reference/"
               "interest_rate/jgbcme.csv")
        resp = _get(url, timeout=12)
        if resp:
            try:
                text = resp.content.decode("utf-8", errors="replace")
                df = pd.read_csv(StringIO(text), skiprows=1, header=0)
                df.columns = [c.strip() for c in df.columns]
                last = df.dropna(how="all").iloc[-1]
                mapping = {
                    "1year": "1Y", "2year": "2Y", "3year": "3Y",
                    "5year": "5Y", "7year": "7Y", "10year": "10Y",
                    "15year": "15Y", "20year": "20Y", "30year": "30Y",
                    "40year": "40Y",
                }
                records = {}
                for col_raw, label in mapping.items():
                    # match case-insensitively
                    for c in df.columns:
                        if c.lower().strip() == col_raw.lower():
                            try:
                                records[label] = float(last[c])
                            except Exception:
                                pass
                            break
                if records:
                    return pd.Series(records, name="Japan JGB").sort_index()
            except Exception as e:
                logger.warning(f"MoF JGB parse: {e}")

        # yfinance fallback
        logger.warning("MoF CSV unavailable – falling back to yfinance for Japan")
        records = {}
        for label, tkr in {"2Y": "^JP2YT=RR", "5Y": "^JP5YT=RR",
                            "10Y": "^JP10YT=RR", "30Y": "^JP30YT=RR"}.items():
            try:
                h = yf.Ticker(tkr).history(period="5d")["Close"].dropna()
                if not h.empty:
                    records[label] = float(h.iloc[-1])
            except Exception:
                pass
        return pd.Series(records, name="Japan JGB").sort_index()

    # ── UK Gilts (Bank of England IADB) ──────────────────────────────────────

    def get_uk_yield_curve(self) -> pd.Series:
        """UK Gilt par yields from Bank of England IADB API."""
        codes = list(_BOE_SERIES.keys())
        c_params = "&".join(f"C={c}" for c in codes)
        today = datetime.today()
        url = (
            "https://www.bankofengland.co.uk/boeapps/database/fromshowcolumns.asp"
            f"?csv.x=yes&Datefrom=01%2FJan%2F{today.year}"
            f"&Dateto={today.day:02d}%2F{today.strftime('%b')}%2F{today.year}"
            f"&CSVF=TT&{c_params}"
        )
        resp = _get(url, timeout=14)
        if resp:
            try:
                lines = [l for l in resp.text.strip().split("\n") if l.strip()]
                # Find the header row
                header_idx = next(
                    (i for i, l in enumerate(lines) if "Date" in l or "date" in l), None
                )
                if header_idx is not None:
                    csv_text = "\n".join(lines[header_idx:])
                    df = pd.read_csv(StringIO(csv_text))
                    df.columns = [c.strip() for c in df.columns]
                    last = df.dropna(how="all").iloc[-1]
                    records = {}
                    for code, label in _BOE_SERIES.items():
                        for col in df.columns:
                            if code in col:
                                try:
                                    records[label] = float(last[col])
                                except Exception:
                                    pass
                                break
                    if records:
                        return pd.Series(records, name="UK Gilt").sort_index()
            except Exception as e:
                logger.warning(f"BoE IADB parse: {e}")

        # yfinance fallback
        logger.warning("BoE API unavailable – falling back to yfinance for UK")
        records = {}
        for label, tkr in {"2Y": "^GB2YT=RR", "5Y": "^GB5YT=RR",
                            "10Y": "^GB10YT=RR", "30Y": "^GB30YT=RR"}.items():
            try:
                h = yf.Ticker(tkr).history(period="5d")["Close"].dropna()
                if not h.empty:
                    records[label] = float(h.iloc[-1])
            except Exception:
                pass
        return pd.Series(records, name="UK Gilt").sort_index()

    # ── Canada (Bank of Canada Valet API) ────────────────────────────────────

    def get_canada_yield_curve(self) -> pd.Series:
        """Canadian government bond yields from Bank of Canada Valet API."""
        url = ("https://www.bankofcanada.ca/valet/observations/"
               "group/bond_yields_all/json?recent=5")
        resp = _get(url, timeout=12)
        if resp:
            try:
                data = resp.json()
                observations = data.get("observations", [])
                if observations:
                    latest = observations[-1]
                    records = {}
                    for code, label in _BOC_SERIES.items():
                        entry = latest.get(code, {})
                        val = entry.get("v") if isinstance(entry, dict) else entry
                        if val is not None:
                            try:
                                records[label] = float(val)
                            except Exception:
                                pass
                    if records:
                        return pd.Series(records, name="Canada").sort_index()
            except Exception as e:
                logger.warning(f"BoC Valet parse: {e}")

        # yfinance fallback
        logger.warning("BoC API unavailable – falling back to yfinance for Canada")
        records = {}
        for label, tkr in {"2Y": "^CA2YT=RR", "5Y": "^CA5YT=RR",
                            "10Y": "^CA10YT=RR", "30Y": "^CA30YT=RR"}.items():
            try:
                h = yf.Ticker(tkr).history(period="5d")["Close"].dropna()
                if not h.empty:
                    records[label] = float(h.iloc[-1])
            except Exception:
                pass
        return pd.Series(records, name="Canada").sort_index()

    def get_all_yield_curves(self) -> dict[str, pd.Series]:
        """Return all 5 yield curve snapshots as a dict of Series."""
        return {
            "US Treasury": self.get_us_yield_curve(lookback_days=5).iloc[-1].dropna() if not self.get_us_yield_curve(lookback_days=5).empty else pd.Series(dtype=float),
            "EU Bund":     self.get_eu_yield_curve(),
            "Japan JGB":   self.get_japan_yield_curve(),
            "UK Gilt":     self.get_uk_yield_curve(),
            "Canada":      self.get_canada_yield_curve(),
        }

    def get_yield_curve_history(self, region: str = "US",
                                lookback_days: int = 365) -> pd.DataFrame:
        """Historical yield curves for 2Y, 5Y, 10Y, 30Y."""
        end   = datetime.today()
        start = end - timedelta(days=lookback_days)

        if region == "US":
            df = self.get_us_yield_curve(lookback_days)
            cols = [c for c in ["2Y","5Y","10Y","30Y"] if c in df.columns]
            return df[cols].dropna(how="all") if cols else pd.DataFrame()

        # Other regions: yfinance only (historical scraping is complex)
        tickers_map = {
            "EU":     {"2Y":"^DE2YT=RR","5Y":"^DE5YT=RR","10Y":"^DE10YT=RR","30Y":"^DE30YT=RR"},
            "Japan":  {"2Y":"^JP2YT=RR","5Y":"^JP5YT=RR","10Y":"^JP10YT=RR","30Y":"^JP30YT=RR"},
            "UK":     {"2Y":"^GB2YT=RR","5Y":"^GB5YT=RR","10Y":"^GB10YT=RR","30Y":"^GB30YT=RR"},
            "Canada": {"2Y":"^CA2YT=RR","5Y":"^CA5YT=RR","10Y":"^CA10YT=RR","30Y":"^CA30YT=RR"},
        }
        tickers = tickers_map.get(region, tickers_map["EU"])
        labels  = list(tickers.keys())
        tkr_list = list(tickers.values())
        try:
            raw = yf.download(tkr_list, start=start, end=end, progress=False)["Close"]
            if isinstance(raw, pd.Series):
                raw = raw.to_frame()
                raw.columns = labels[:1]
            else:
                raw.columns = labels[:raw.shape[1]]
            return raw.dropna(how="all")
        except Exception:
            return pd.DataFrame()

    # ══════════════════════════════════════════════════════════════════════════
    # CORPORATE BONDS
    # ══════════════════════════════════════════════════════════════════════════

    def get_corporate_bond_yields(self, lookback_years: int = 3) -> pd.DataFrame:
        """
        Corporate bond effective yields by rating tier.
        Source: FRED ICE BofA indices via pandas-datareader (no key needed).
        Columns: AAA, AA, A, BBB, HY, IG
        """
        end   = datetime.today()
        start = end - timedelta(days=lookback_years * 365)
        frames = {}
        for label, sid in CORP_YIELD_SERIES.items():
            try:
                s = web.DataReader(sid, "fred", start, end)
                frames[label] = s.iloc[:, 0]
            except Exception as e:
                logger.debug(f"FRED corp yield {sid}: {e}")

        if not frames:
            # ETF proxy fallback
            logger.warning("FRED corp yields unavailable – using ETF proxies")
            return self._corp_yields_etf_proxy(start, end)

        df = pd.DataFrame(frames)
        df.index = pd.to_datetime(df.index)
        return df.dropna(how="all")

    def _corp_yields_etf_proxy(self, start, end) -> pd.DataFrame:
        """Approximate yields from ETF price history (not true yield data)."""
        etfs = {"IG": "LQD", "HY": "HYG", "Short IG": "VCSH"}
        frames = {}
        for label, tkr in etfs.items():
            try:
                raw = yf.download(tkr, start=start, end=end, progress=False)["Close"]
                frames[label] = raw
            except Exception:
                pass
        return pd.DataFrame(frames).dropna(how="all")

    def get_credit_spreads_history(self, lookback_years: int = 5) -> pd.DataFrame:
        """
        OAS (option-adjusted spreads) history by rating.
        Source: FRED ICE BofA OAS series (no key needed).
        """
        end   = datetime.today()
        start = end - timedelta(days=lookback_years * 365)
        frames = {}
        for label, sid in CORP_OAS_SERIES.items():
            try:
                s = web.DataReader(sid, "fred", start, end)
                frames[label] = s.iloc[:, 0]
            except Exception as e:
                logger.debug(f"FRED OAS {sid}: {e}")

        if not frames:
            return pd.DataFrame()
        df = pd.DataFrame(frames)
        df.index = pd.to_datetime(df.index)
        return df.dropna(how="all")

    def get_credit_spread_snapshot(self) -> dict:
        """Current OAS levels and their Z-scores (vs 5Y history)."""
        df = self.get_credit_spreads_history(lookback_years=5)
        if df.empty:
            return {}
        result = {}
        last = df.iloc[-1]
        for col in df.columns:
            val = last[col]
            mean = df[col].mean()
            std  = df[col].std()
            result[col] = {
                "value":   round(float(val), 0),
                "zscore":  round((float(val) - float(mean)) / float(std), 2) if std else 0.0,
                "pct":     round(float((df[col] <= val).mean() * 100), 0),
                "5y_avg":  round(float(mean), 0),
            }
        return result

    def get_credit_spread_vs_treasury(self) -> pd.DataFrame:
        """Corporate bond yields vs Treasury benchmark (spread = yield - treasury)."""
        end   = datetime.today()
        start = end - timedelta(days=365 * 3)
        corp  = self.get_corporate_bond_yields(3)
        try:
            t10 = web.DataReader("DGS10", "fred", start, end).iloc[:, 0]
            t10.index = pd.to_datetime(t10.index)
            if not corp.empty:
                spread_df = corp.subtract(t10, axis=0).dropna(how="all")
                spread_df.columns = [f"{c} Spread" for c in spread_df.columns]
                return spread_df
        except Exception as e:
            logger.warning(f"T-spread calc: {e}")
        return pd.DataFrame()

    # ══════════════════════════════════════════════════════════════════════════
    # CORPORATE DEFAULTS
    # ══════════════════════════════════════════════════════════════════════════

    def get_corporate_defaults(self, lookback_days: int = 365) -> pd.DataFrame:
        """
        Recent corporate bankruptcy filings.
        Primary: SEC EDGAR 8-K full-text search
        Secondary: Reuters/Bloomberg bankruptcy RSS
        """
        records = []
        end_date   = datetime.today()
        start_date = end_date - timedelta(days=lookback_days)

        # ── Source 1: SEC EDGAR ──────────────────────────────────────────────
        edgar_records = self._edgar_bankruptcies(
            start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")
        )
        records.extend(edgar_records)

        # ── Source 2: RSS news with bankruptcy keywords ───────────────────────
        rss_records = self._rss_bankruptcy_news()
        records.extend(rss_records)

        if not records:
            return pd.DataFrame(columns=["company","date","type","source","detail"])

        df = pd.DataFrame(records)
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["company"])
        df = df[df["company"].str.strip() != ""]
        df = df.drop_duplicates(subset=["company"]).sort_values("date", ascending=False)
        return df.head(50)

    def _edgar_bankruptcies(self, start_date: str, end_date: str) -> list[dict]:
        """Search SEC EDGAR 8-K filings for Chapter 11 keywords."""
        url = (
            "https://efts.sec.gov/LATEST/search-index"
            "?q=%22chapter+11%22+%22voluntary+petition%22"
            f"&forms=8-K&dateRange=custom&startdt={start_date}&enddt={end_date}"
            "&hits.hits.total.value=true"
        )
        edgar_hdrs = {**_HDRS, "User-Agent": "GlobalMacroDashboard epsilonright@gmail.com"}
        records = []
        try:
            resp = requests.get(url, headers=edgar_hdrs, timeout=12)
            if resp.status_code == 200:
                data = resp.json()
                hits = data.get("hits", {}).get("hits", [])
                for hit in hits[:30]:
                    src = hit.get("_source", {})
                    name = src.get("entity_name", "").strip()
                    if name:
                        records.append({
                            "company": name,
                            "date":    src.get("file_date", ""),
                            "type":    "Chapter 11",
                            "source":  "SEC EDGAR",
                            "detail":  f"8-K filing: {src.get('period_of_report','')}",
                        })
        except Exception as e:
            logger.warning(f"EDGAR bankruptcy search: {e}")
        return records

    def _rss_bankruptcy_news(self) -> list[dict]:
        """Scan RSS feeds for bankruptcy / default news."""
        keywords = ["bankruptcy", "chapter 11", "default", "insolvency",
                    "files for protection", "restructuring"]
        records = []
        for source, feed_url in [
            ("Reuters", "https://feeds.reuters.com/reuters/businessNews"),
            ("WSJ",     "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"),
        ]:
            try:
                feed = feedparser.parse(feed_url)
                for entry in feed.entries[:30]:
                    title = entry.get("title", "").lower()
                    summary = entry.get("summary", "").lower()
                    if any(kw in title or kw in summary for kw in keywords):
                        records.append({
                            "company": entry.get("title", ""),
                            "date":    entry.get("published", ""),
                            "type":    "News",
                            "source":  source,
                            "detail":  entry.get("summary", "")[:150],
                        })
            except Exception as e:
                logger.debug(f"RSS bankruptcy {source}: {e}")
        return records

    def get_default_rate_history(self) -> pd.DataFrame:
        """
        High-yield default rate proxy via HY OAS spread history.
        Also fetches FRED delinquency / charge-off series as credit quality proxies.
        """
        end   = datetime.today()
        start = end - timedelta(days=365 * 10)
        series = {
            "HY OAS (bps)":          "BAMLH0A0HYM2OAS",
            "C&I Loan Delinquency%": "DRSDCIS",
            "Credit Card Charge-off%":"DRCCLACBS",
        }
        frames = {}
        for label, sid in series.items():
            try:
                s = web.DataReader(sid, "fred", start, end)
                frames[label] = s.iloc[:, 0]
            except Exception as e:
                logger.debug(f"FRED default proxy {sid}: {e}")
        if not frames:
            return pd.DataFrame()
        df = pd.DataFrame(frames)
        df.index = pd.to_datetime(df.index)
        return df.dropna(how="all")

    # ══════════════════════════════════════════════════════════════════════════
    # MACRO / MARKET / NEWS (kept from v1, lightly revised)
    # ══════════════════════════════════════════════════════════════════════════

    def get_macro_kpis(self) -> dict:
        end   = datetime.today()
        start = end - timedelta(days=400)
        result = {}

        if self._fred:
            fetch_map = {
                "Fed Funds Rate": "FEDFUNDS",
                "CPI YoY":        "CPIAUCSL",
                "Core PCE YoY":   "PCEPILFE",
                "Unemployment":   "UNRATE",
                "US GDP Growth":  "A191RL1Q225SBEA",
                "M2 Growth":      "M2SL",
            }
            for label, sid in fetch_map.items():
                try:
                    s = self._fred.get_series(sid, start, end).dropna()
                    val = float(s.pct_change(12).iloc[-1] * 100) if label in ("CPI YoY","Core PCE YoY","M2 Growth") else float(s.iloc[-1])
                    prev = float(s.pct_change(12).iloc[-2] * 100) if label in ("CPI YoY","Core PCE YoY","M2 Growth") else float(s.iloc[-2])
                    result[label] = {"value": round(val,2), "prev": round(prev,2)}
                except Exception:
                    pass
            try:
                t10 = self._fred.get_series("DGS10", start, end).dropna()
                t2  = self._fred.get_series("DGS2",  start, end).dropna()
                sp  = (t10-t2).dropna()
                result["10Y-2Y Spread"] = {"value": round(float(sp.iloc[-1]),2), "prev": round(float(sp.iloc[-2]),2)}
            except Exception:
                pass
        else:
            # FRED via pandas_datareader (no key)
            pdr_series = {
                "Fed Funds Rate": ("FEDFUNDS", False),
                "Unemployment":   ("UNRATE",   False),
                "CPI YoY":       ("CPIAUCSL", True),
                "Core PCE YoY":  ("PCEPILFE", True),
            }
            end_str   = end.strftime("%Y-%m-%d")
            start_str = start.strftime("%Y-%m-%d")
            for label, (sid, yoy) in pdr_series.items():
                try:
                    s = web.DataReader(sid, "fred", start_str, end_str).iloc[:, 0].dropna()
                    val  = float(s.pct_change(12).iloc[-1] * 100) if yoy else float(s.iloc[-1])
                    prev = float(s.pct_change(12).iloc[-2] * 100) if yoy else float(s.iloc[-2])
                    result[label] = {"value": round(val,2), "prev": round(prev,2)}
                except Exception:
                    pass
            # Fallback market-based KPIs
            proxies = {"VIX": "^VIX", "US 10Y": "^TNX", "Gold": "GC=F", "S&P 500": "^GSPC"}
            for label, tkr in proxies.items():
                if label not in result:
                    try:
                        h = yf.Ticker(tkr).history(period="5d")["Close"].dropna()
                        result[label] = {"value": round(float(h.iloc[-1]),2), "prev": round(float(h.iloc[-2]),2)}
                    except Exception:
                        pass
        return result

    def get_macro_history(self, series_id: str, lookback_years: int = 5) -> pd.Series:
        start = datetime.today() - timedelta(days=lookback_years * 365)
        if self._fred:
            try:
                s = self._fred.get_series(series_id, start)
                s.index = pd.to_datetime(s.index)
                return s.dropna()
            except Exception:
                pass
        try:
            s = web.DataReader(series_id, "fred", start.strftime("%Y-%m-%d"))
            s.index = pd.to_datetime(s.index)
            return s.iloc[:, 0].dropna()
        except Exception as e:
            logger.warning(f"Macro history {series_id}: {e}")
            return pd.Series(dtype=float)

    def get_sector_performance(self, period: str = "1y") -> pd.Series:
        tickers = list(SECTOR_ETFS.values())
        labels  = list(SECTOR_ETFS.keys())
        try:
            raw = yf.download(tickers, period=period, progress=False)["Close"]
            if isinstance(raw, pd.Series):
                raw = raw.to_frame()
            raw.columns = [labels[tickers.index(c)] if c in tickers else c for c in raw.columns]
            total = ((1 + raw.pct_change().dropna(how="all")).cumprod().iloc[-1] - 1) * 100
            total.name = "Return%"
            return total.sort_values(ascending=False)
        except Exception as e:
            logger.warning(f"Sector perf: {e}")
            return pd.Series(dtype=float)

    def get_global_rates(self) -> pd.DataFrame:
        records = {}
        for label, tkr in GLOBAL_RATES_YF.items():
            try:
                h = yf.Ticker(tkr).history(period="5d")["Close"].dropna()
                if not h.empty:
                    records[label] = {
                        "rate": round(float(h.iloc[-1]), 3),
                        "chg":  round(float(h.iloc[-1] - h.iloc[-2]), 3) if len(h) > 1 else 0.0,
                    }
            except Exception:
                pass
        return pd.DataFrame(records).T

    def get_cross_asset_returns(self, period: str = "1mo") -> pd.Series:
        assets = {
            "S&P 500": "^GSPC", "NASDAQ": "^IXIC", "Russell 2000": "^RUT",
            "MSCI EM": "EEM",   "Gold": "GC=F",     "Oil (WTI)": "CL=F",
            "DXY": "DX-Y.NYB",  "Bitcoin": "BTC-USD",
            "Agg Bonds": "AGG", "HY Bonds": "HYG",  "IG Bonds": "LQD",
        }
        records = {}
        for label, tkr in assets.items():
            try:
                h = yf.Ticker(tkr).history(period=period)["Close"].dropna()
                if len(h) >= 2:
                    records[label] = round(float((h.iloc[-1] / h.iloc[0] - 1) * 100), 2)
            except Exception:
                pass
        return pd.Series(records, name="Return%").sort_values(ascending=False)

    def get_company_data(self, ticker: str) -> dict:
        tkr  = yf.Ticker(ticker)
        info = tkr.info or {}
        hist = tkr.history(period="1y")
        return {
            "info": {
                "name":        info.get("longName", ticker),
                "sector":      info.get("sector", "N/A"),
                "industry":    info.get("industry", "N/A"),
                "market_cap":  info.get("marketCap"),
                "pe_ratio":    info.get("trailingPE"),
                "pb_ratio":    info.get("priceToBook"),
                "roe":         info.get("returnOnEquity"),
                "debt_equity": info.get("debtToEquity"),
                "div_yield":   info.get("dividendYield"),
                "beta":        info.get("beta"),
                "description": info.get("longBusinessSummary", ""),
            },
            "history": hist,
        }

    def get_news(self, max_items: int = 30) -> list[dict]:
        articles = []
        for source_name, feed_url in NEWS_RSS_FEEDS:
            if len(articles) >= max_items:
                break
            try:
                feed = feedparser.parse(feed_url)
                for entry in feed.entries[:6]:
                    articles.append({
                        "title":   entry.get("title", ""),
                        "source":  source_name,
                        "url":     entry.get("link", ""),
                        "time":    entry.get("published", "")[:25],
                        "summary": entry.get("summary", "")[:220],
                    })
            except Exception as e:
                logger.debug(f"RSS {source_name}: {e}")
        seen, unique = set(), []
        for a in articles:
            if a["title"] and a["title"] not in seen:
                seen.add(a["title"])
                unique.append(a)
        return unique[:max_items]

    def get_factor_returns(self, lookback_years: int = 3) -> pd.DataFrame:
        end   = datetime.today()
        start = end - timedelta(days=lookback_years * 365)
        try:
            ff = web.DataReader("F-F_Research_Data_5_Factors_2x3", "famafrench", start, end)
            df = ff[0] / 100.0
            df.index = df.index.to_timestamp()
            return df
        except Exception as e:
            logger.warning(f"Fama-French: {e}")
            return pd.DataFrame()

    def get_cumulative_factors(self, lookback_years: int = 3) -> pd.DataFrame:
        ff = self.get_factor_returns(lookback_years)
        if ff.empty:
            return pd.DataFrame()
        factors = [c for c in ff.columns if c != "RF"]
        return (1 + ff[factors]).cumprod()
