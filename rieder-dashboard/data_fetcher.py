"""
Data fetcher for the Rieder-style macro dashboard.
Sources: FRED (rates/macro), yfinance (market), Fama-French (factors),
         ECB API (European yields), NewsAPI/RSS (news).
"""

import os
import logging
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import requests
import feedparser
import yfinance as yf
import pandas_datareader.data as web
from fredapi import Fred

logger = logging.getLogger(__name__)

# ── Tenor labels ──────────────────────────────────────────────────────────────
US_TENORS = {
    "1M": "DGS1MO", "3M": "DGS3MO", "6M": "DGS6MO",
    "1Y": "DGS1",   "2Y": "DGS2",   "3Y": "DGS3",
    "5Y": "DGS5",   "7Y": "DGS7",   "10Y": "DGS10",
    "20Y": "DGS20", "30Y": "DGS30",
}

# German Bund proxy via yfinance (ETF-based fallback if ECB unavailable)
EU_TENORS_YF = {
    "2Y": "^DE2YT=RR", "5Y": "^DE5YT=RR",
    "10Y": "^TNX",      "30Y": "^TYX",   # placeholder; refined via ECB below
}

# Sector ETFs
SECTOR_ETFS = {
    "Technology": "XLK", "Financials": "XLF", "Healthcare": "XLV",
    "Energy": "XLE", "Consumer Disc.": "XLY", "Consumer Staples": "XLP",
    "Industrials": "XLI", "Materials": "XLB", "Utilities": "XLU",
    "Real Estate": "XLRE", "Communication": "XLC",
}

# Macro KPI series from FRED
MACRO_SERIES = {
    "Fed Funds Rate":      "FEDFUNDS",
    "CPI YoY":            "CPIAUCSL",
    "Core PCE YoY":       "PCEPILFE",
    "Unemployment":        "UNRATE",
    "10Y-2Y Spread":       None,          # computed
    "US GDP Growth":       "A191RL1Q225SBEA",
    "ISM Mfg PMI":        "MANEMP",       # proxy; actual PMI not on FRED free
    "M2 Money Supply":     "M2SL",
}

# Global benchmark rates
GLOBAL_RATES_YF = {
    "US 10Y":   "^TNX",
    "US 2Y":    "^IRX",
    "DE 10Y":   "^DE10YT=RR",
    "JP 10Y":   "^JP10YT=RR",
    "UK 10Y":   "^GB10YT=RR",
    "CN 10Y":   "^CN10YT=RR",
}

# Credit spread proxies (option-adjusted spread ETFs)
CREDIT_ETFS = {
    "HY Spread (HYG)": "HYG",
    "IG Spread (LQD)": "LQD",
    "EM Bonds (EMB)":  "EMB",
    "Short HY (SJNK)": "SJNK",
}

NEWS_RSS_FEEDS = [
    ("Reuters - Markets",  "https://feeds.reuters.com/reuters/businessNews"),
    ("FT - Markets",       "https://www.ft.com/rss/home/uk"),
    ("Bloomberg",          "https://feeds.bloomberg.com/markets/news.rss"),
    ("WSJ - Markets",      "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"),
    ("Seeking Alpha",      "https://seekingalpha.com/feed.xml"),
]


class DataFetcher:
    def __init__(self, fred_api_key: str = "", news_api_key: str = ""):
        self.fred_api_key = fred_api_key or os.getenv("FRED_API_KEY", "")
        self.news_api_key = news_api_key or os.getenv("NEWS_API_KEY", "")
        self._fred = Fred(api_key=self.fred_api_key) if self.fred_api_key else None

    # ── Yield Curves ──────────────────────────────────────────────────────────

    def get_us_yield_curve(self, lookback_days: int = 365) -> pd.DataFrame:
        """Return a DataFrame: index=date, columns=tenor labels, values=yield%."""
        end = datetime.today()
        start = end - timedelta(days=lookback_days)

        if self._fred:
            try:
                frames = {}
                for tenor, series_id in US_TENORS.items():
                    s = self._fred.get_series(series_id, start, end)
                    frames[tenor] = s
                df = pd.DataFrame(frames).dropna(how="all")
                df.index = pd.to_datetime(df.index)
                return df
            except Exception as e:
                logger.warning(f"FRED yield curve failed: {e}, falling back to yfinance")

        # Fallback: yfinance Treasury tickers
        tickers = ["^IRX", "^FVX", "^TNX", "^TYX"]
        labels  = ["3M",   "5Y",   "10Y",  "30Y"]
        raw = yf.download(tickers, start=start, end=end, progress=False)["Close"]
        raw.columns = labels
        return raw.dropna(how="all")

    def get_eu_yield_curve(self) -> pd.DataFrame:
        """
        Fetch ECB government bond yield curve (AAA-rated / all issuers).
        Returns today's snapshot across tenors 1Y-30Y.
        """
        tenors = [1, 2, 3, 5, 7, 10, 15, 20, 30]
        ecb_base = "https://data-api.ecb.europa.eu/service/data"
        records = {}

        for t in tenors:
            # ECB yield curve dataset: YC, param SR (spot rate), maturity in years
            series_key = f"YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_{t:02d}Y"
            url = f"{ecb_base}/{series_key}?lastNObservations=5&format=jsondata"
            try:
                resp = requests.get(url, timeout=8)
                if resp.status_code == 200:
                    data = resp.json()
                    obs = data["dataSets"][0]["series"]["0:0:0:0:0:0:0"]["observations"]
                    # get the most recent value
                    latest_key = max(obs.keys(), key=int)
                    records[f"{t}Y"] = float(obs[latest_key][0])
            except Exception as e:
                logger.debug(f"ECB {t}Y failed: {e}")

        if not records:
            # Fallback: German Bund proxies via yfinance
            bund_tickers = {
                "2Y": "^DE2YT=RR", "5Y": "^DE5YT=RR",
                "10Y": "^DE10YT=RR", "30Y": "^DE30YT=RR",
            }
            for label, tkr in bund_tickers.items():
                try:
                    hist = yf.Ticker(tkr).history(period="5d")
                    if not hist.empty:
                        records[label] = float(hist["Close"].dropna().iloc[-1])
                except Exception:
                    pass

        if records:
            s = pd.Series(records, name="EU Yield")
            s.index.name = "Tenor"
            return s.sort_index()
        return pd.Series(dtype=float)

    def get_asia_yield_curve(self) -> pd.DataFrame:
        """
        Japanese JGB yields via BoJ statistics API + FRED fallback.
        Returns today's snapshot across tenors.
        """
        # Try BoJ API
        boj_tenors = {
            "1Y": "IR01'MABJPY1Y", "2Y": "IR01'MABJPY2Y",
            "5Y": "IR01'MABJPY5Y", "10Y": "IR01'MABJPY10Y",
            "20Y": "IR01'MABJPY20Y", "30Y": "IR01'MABJPY30Y",
        }
        records = {}

        # BoJ CSV API
        try:
            url = ("https://www.stat-search.boj.or.jp/ssi/mtshtml/ir01_d_1_en.csv")
            resp = requests.get(url, timeout=8)
            if resp.status_code == 200:
                lines = resp.text.strip().split("\n")
                # Header is on row 8, data starts row 9
                if len(lines) > 9:
                    header = [x.strip().strip('"') for x in lines[7].split(",")]
                    last_row = [x.strip().strip('"') for x in lines[-1].split(",")]
                    for h, v in zip(header[1:], last_row[1:]):
                        try:
                            records[h] = float(v)
                        except ValueError:
                            pass
        except Exception as e:
            logger.debug(f"BoJ API failed: {e}")

        if not records and self._fred:
            # FRED fallback for Japan rates
            fred_jp = {"2Y": "IRLTLT01JPM156N", "10Y": "IRLTLT01JPM156N"}
            try:
                s = self._fred.get_series("IRLTLT01JPM156N",
                                          datetime.today() - timedelta(days=90))
                records["10Y"] = float(s.dropna().iloc[-1])
            except Exception:
                pass

        # yfinance fallback
        jgb_yf = {
            "2Y": "^JP2YT=RR", "5Y": "^JP5YT=RR",
            "10Y": "^JP10YT=RR", "30Y": "^JP30YT=RR",
        }
        if not records:
            for label, tkr in jgb_yf.items():
                try:
                    hist = yf.Ticker(tkr).history(period="5d")
                    if not hist.empty:
                        records[label] = float(hist["Close"].dropna().iloc[-1])
                except Exception:
                    pass

        if records:
            s = pd.Series(records, name="Japan JGB")
            s.index.name = "Tenor"
            return s.sort_index()
        return pd.Series(dtype=float)

    def get_yield_curve_history(self, region: str = "US",
                                lookback_days: int = 365) -> pd.DataFrame:
        """Historical yield curve data for 2Y, 5Y, 10Y, 30Y."""
        end   = datetime.today()
        start = end - timedelta(days=lookback_days)

        if region == "US" and self._fred:
            series = {"2Y": "DGS2", "5Y": "DGS5", "10Y": "DGS10", "30Y": "DGS30"}
            frames = {}
            for label, sid in series.items():
                try:
                    frames[label] = self._fred.get_series(sid, start, end)
                except Exception:
                    pass
            if frames:
                df = pd.DataFrame(frames)
                df.index = pd.to_datetime(df.index)
                return df.dropna(how="all")

        tickers = {
            "US":  ["^IRX", "^FVX", "^TNX", "^TYX"],
            "EU":  ["^DE2YT=RR", "^DE5YT=RR", "^DE10YT=RR", "^DE30YT=RR"],
            "Asia":["^JP2YT=RR", "^JP5YT=RR", "^JP10YT=RR", "^JP30YT=RR"],
        }
        labels = ["2Y", "5Y", "10Y", "30Y"]
        tkrs = tickers.get(region, tickers["US"])
        raw = yf.download(tkrs, start=start, end=end, progress=False)["Close"]
        if isinstance(raw, pd.Series):
            raw = raw.to_frame()
        raw.columns = labels[:raw.shape[1]]
        return raw.dropna(how="all")

    # ── Factor Returns ────────────────────────────────────────────────────────

    def get_factor_returns(self, lookback_years: int = 3) -> pd.DataFrame:
        """
        Fama-French 5 factors (Mkt-RF, SMB, HML, RMW, CMA) from Ken French library.
        Returns monthly returns as percentages.
        """
        end   = datetime.today()
        start = end - timedelta(days=lookback_years * 365)
        try:
            ff = web.DataReader("F-F_Research_Data_5_Factors_2x3",
                                "famafrench", start, end)
            df = ff[0] / 100.0   # convert from % to decimal
            df.index = df.index.to_timestamp()
            return df
        except Exception as e:
            logger.warning(f"Fama-French data failed: {e}")
            return pd.DataFrame()

    def get_momentum_factor(self, lookback_years: int = 3) -> pd.Series:
        """Carhart momentum factor (MOM) from Ken French library."""
        end   = datetime.today()
        start = end - timedelta(days=lookback_years * 365)
        try:
            mom = web.DataReader("F-F_Momentum_Factor", "famafrench", start, end)
            s = mom[0]["Mom   "] / 100.0
            s.index = s.index.to_timestamp()
            return s
        except Exception as e:
            logger.warning(f"Momentum factor failed: {e}")
            return pd.Series(dtype=float)

    def get_cumulative_factors(self, lookback_years: int = 3) -> pd.DataFrame:
        """Cumulative factor returns (growth of $1)."""
        ff = self.get_factor_returns(lookback_years)
        if ff.empty:
            return pd.DataFrame()
        # Drop RF column if present
        factors = [c for c in ff.columns if c != "RF"]
        cum = (1 + ff[factors]).cumprod()
        return cum

    # ── Macro Indicators ──────────────────────────────────────────────────────

    def get_macro_kpis(self) -> dict:
        """Return dict of key macro indicator values."""
        result = {}
        end   = datetime.today()
        start = end - timedelta(days=400)

        if not self._fred:
            return self._macro_kpis_yfinance()

        fetch_map = {
            "Fed Funds Rate":  "FEDFUNDS",
            "CPI YoY":         "CPIAUCSL",
            "Core PCE YoY":    "PCEPILFE",
            "Unemployment":    "UNRATE",
            "US GDP Growth":   "A191RL1Q225SBEA",
            "M2 Growth":       "M2SL",
        }

        for label, sid in fetch_map.items():
            try:
                s = self._fred.get_series(sid, start, end).dropna()
                if label in ("CPI YoY", "Core PCE YoY", "M2 Growth"):
                    # Compute YoY %
                    val = float(s.pct_change(12).iloc[-1] * 100)
                else:
                    val = float(s.iloc[-1])
                result[label] = {"value": round(val, 2),
                                 "prev":  round(float(s.iloc[-2]) if len(s) > 1 else val, 2)}
            except Exception as e:
                logger.debug(f"FRED KPI {label}: {e}")

        # 10Y-2Y spread
        try:
            t10 = self._fred.get_series("DGS10", start, end).dropna()
            t2  = self._fred.get_series("DGS2",  start, end).dropna()
            spread = (t10 - t2).dropna()
            result["10Y-2Y Spread"] = {"value": round(float(spread.iloc[-1]), 2),
                                       "prev":  round(float(spread.iloc[-2]) if len(spread) > 1 else 0, 2)}
        except Exception:
            pass

        return result

    def _macro_kpis_yfinance(self) -> dict:
        """Fallback KPIs from yfinance if FRED unavailable."""
        result = {}
        proxies = {
            "S&P 500":   "^GSPC", "VIX":       "^VIX",
            "US 10Y":    "^TNX",  "Gold":       "GC=F",
            "Oil (WTI)": "CL=F",  "DXY":        "DX-Y.NYB",
        }
        for label, tkr in proxies.items():
            try:
                hist = yf.Ticker(tkr).history(period="5d")["Close"].dropna()
                val  = float(hist.iloc[-1])
                prev = float(hist.iloc[-2]) if len(hist) > 1 else val
                result[label] = {"value": round(val, 2), "prev": round(prev, 2)}
            except Exception:
                pass
        return result

    def get_macro_history(self, series_id: str, lookback_years: int = 5) -> pd.Series:
        """Fetch a FRED series as a pandas Series."""
        if not self._fred:
            return pd.Series(dtype=float)
        start = datetime.today() - timedelta(days=lookback_years * 365)
        try:
            s = self._fred.get_series(series_id, start)
            s.index = pd.to_datetime(s.index)
            return s.dropna()
        except Exception as e:
            logger.warning(f"FRED {series_id}: {e}")
            return pd.Series(dtype=float)

    # ── Market Data ───────────────────────────────────────────────────────────

    def get_sector_performance(self, period: str = "1y") -> pd.DataFrame:
        """Return sector ETF returns for the given period."""
        tickers = list(SECTOR_ETFS.values())
        labels  = list(SECTOR_ETFS.keys())
        try:
            raw = yf.download(tickers, period=period, progress=False)["Close"]
            if isinstance(raw, pd.Series):
                raw = raw.to_frame()
            # Align columns
            raw.columns = [labels[tickers.index(c)] if c in tickers else c
                           for c in raw.columns]
            rets = raw.pct_change().dropna(how="all")
            # Total return over the period
            total = ((1 + rets).cumprod().iloc[-1] - 1) * 100
            total.name = "Return%"
            return total.sort_values(ascending=False)
        except Exception as e:
            logger.warning(f"Sector performance: {e}")
            return pd.Series(dtype=float)

    def get_global_rates(self) -> pd.DataFrame:
        """Current global benchmark rates."""
        records = {}
        for label, tkr in GLOBAL_RATES_YF.items():
            try:
                hist = yf.Ticker(tkr).history(period="5d")["Close"].dropna()
                if not hist.empty:
                    records[label] = {
                        "rate": round(float(hist.iloc[-1]), 3),
                        "chg":  round(float(hist.iloc[-1] - hist.iloc[-2]), 3)
                                if len(hist) > 1 else 0.0,
                    }
            except Exception:
                pass
        return pd.DataFrame(records).T

    def get_cross_asset_returns(self, period: str = "1mo") -> pd.Series:
        """Returns across asset classes."""
        assets = {
            "S&P 500":     "^GSPC", "NASDAQ":    "^IXIC",
            "Russell 2000":"^RUT",  "MSCI EM":   "EEM",
            "Gold":        "GC=F",  "Oil (WTI)": "CL=F",
            "DXY":         "DX-Y.NYB", "Bitcoin": "BTC-USD",
            "Agg Bonds":   "AGG",   "HY Bonds":  "HYG",
        }
        records = {}
        for label, tkr in assets.items():
            try:
                hist = yf.Ticker(tkr).history(period=period)["Close"].dropna()
                if len(hist) >= 2:
                    ret = (hist.iloc[-1] / hist.iloc[0] - 1) * 100
                    records[label] = round(float(ret), 2)
            except Exception:
                pass
        return pd.Series(records, name="Return%").sort_values(ascending=False)

    def get_company_data(self, ticker: str) -> dict:
        """Fetch company fundamentals + price history."""
        tkr = yf.Ticker(ticker)
        info = tkr.info or {}
        hist = tkr.history(period="1y")

        return {
            "info": {
                "name":        info.get("longName", ticker),
                "sector":      info.get("sector", "N/A"),
                "industry":    info.get("industry", "N/A"),
                "market_cap":  info.get("marketCap", None),
                "pe_ratio":    info.get("trailingPE", None),
                "pb_ratio":    info.get("priceToBook", None),
                "roe":         info.get("returnOnEquity", None),
                "debt_equity": info.get("debtToEquity", None),
                "div_yield":   info.get("dividendYield", None),
                "beta":        info.get("beta", None),
                "description": info.get("longBusinessSummary", ""),
            },
            "history": hist,
        }

    # ── News ──────────────────────────────────────────────────────────────────

    def get_news(self, max_items: int = 30) -> list[dict]:
        """Fetch top financial news from RSS feeds + NewsAPI."""
        articles = []

        # NewsAPI
        if self.news_api_key:
            try:
                url = (
                    "https://newsapi.org/v2/top-headlines"
                    f"?category=business&language=en&pageSize=20"
                    f"&apiKey={self.news_api_key}"
                )
                resp = requests.get(url, timeout=8)
                if resp.status_code == 200:
                    for a in resp.json().get("articles", []):
                        articles.append({
                            "title":  a.get("title", ""),
                            "source": a.get("source", {}).get("name", ""),
                            "url":    a.get("url", ""),
                            "time":   a.get("publishedAt", ""),
                            "summary": a.get("description", ""),
                        })
            except Exception as e:
                logger.debug(f"NewsAPI failed: {e}")

        # RSS fallback
        for source_name, feed_url in NEWS_RSS_FEEDS:
            if len(articles) >= max_items:
                break
            try:
                feed = feedparser.parse(feed_url)
                for entry in feed.entries[:5]:
                    articles.append({
                        "title":   entry.get("title", ""),
                        "source":  source_name,
                        "url":     entry.get("link", ""),
                        "time":    entry.get("published", ""),
                        "summary": entry.get("summary", "")[:200],
                    })
            except Exception as e:
                logger.debug(f"RSS {source_name} failed: {e}")

        # Deduplicate by title
        seen = set()
        unique = []
        for a in articles:
            if a["title"] not in seen and a["title"]:
                seen.add(a["title"])
                unique.append(a)

        return unique[:max_items]

    # ── Volatility Surface / VIX term structure ───────────────────────────────

    def get_vix_term_structure(self) -> pd.Series:
        """VIX futures term structure (VX1–VX8 continuous contracts)."""
        tickers = {
            "Spot":  "^VIX",
            "1M":    "^VIX1D",   # approximations
            "3M":    "^VIX3M",
            "6M":    "^VIX6M",
        }
        records = {}
        for label, tkr in tickers.items():
            try:
                hist = yf.Ticker(tkr).history(period="2d")["Close"].dropna()
                if not hist.empty:
                    records[label] = round(float(hist.iloc[-1]), 2)
            except Exception:
                pass
        return pd.Series(records)
