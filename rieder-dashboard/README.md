# Global Macro Dashboard  
*Inspired by Rick Rieder (BlackRock) — Fixed Income / Global Macro view*

## Features

| Tab | What you see |
|-----|-------------|
| **Macro** | Live KPI cards (Fed Funds, CPI, PCE, Unemployment, GDP, M2), rate history charts, global benchmark rates, cross-asset 1M returns |
| **Yield Curves** | Snapshot comparison: US Treasuries · EU Bunds · Japan JGBs, 1-year history, 10Y-2Y inversion tracker |
| **Factors** | Fama-French 5-factor cumulative returns (Mkt-RF, SMB, HML, RMW, CMA), correlation matrix, monthly bar chart |
| **Cross-Asset** | Sector rotation (all 11 GICS), multi-period asset returns, VIX 1-year history |
| **Company** | OHLC candlestick + volume, P/E, P/B, ROE, Debt/Equity, beta, dividend yield |
| **News** | Live feed from Reuters, FT, Bloomberg, WSJ RSS; NewsAPI if key provided |

## Quick Start

```bash
cd rieder-dashboard

# 1. Install dependencies
pip install -r requirements.txt

# 2. Set API keys (optional but recommended)
cp .env.example .env
# Edit .env and add:
#   FRED_API_KEY  — free at https://fred.stlouisfed.org/docs/api/api_key.html
#   NEWS_API_KEY  — free tier at https://newsapi.org

# 3. Run locally
python app.py
# Open http://localhost:8050
```

## Deploy to the Web

### Render.com (Free Tier – Recommended)
1. Push this folder to a GitHub repo
2. Go to [render.com](https://render.com) → New Web Service → connect repo
3. Build: `pip install -r requirements.txt`
4. Start: `gunicorn app:server --workers 2 --timeout 120`
5. Add env vars: `FRED_API_KEY`, `NEWS_API_KEY`

### Heroku
```bash
heroku create my-macro-dashboard
heroku config:set FRED_API_KEY=xxx NEWS_API_KEY=yyy
git push heroku main
```

### Railway / Fly.io
Both auto-detect Python apps and read the `Procfile`.

## Data Sources

| Data | Source | Key needed? |
|------|--------|-------------|
| US macro (CPI, GDP, rates) | FRED API | Yes – free |
| US yield curve | FRED API | Yes – free |
| EU yield curve | ECB Statistics API | No |
| Japan JGBs | Bank of Japan + yfinance | No |
| Fama-French factors | Ken French Data Library | No |
| Market prices / sectors | Yahoo Finance (yfinance) | No |
| News | RSS feeds (Reuters, FT, WSJ) | No |
| Richer news | NewsAPI | Free tier |

## Architecture

```
app.py           — Dash app, layout, all callbacks
data_fetcher.py  — All data fetching (FRED, yfinance, ECB, FF, RSS)
requirements.txt — Python dependencies
Procfile         — Production server (gunicorn)
render.yaml      — Render.com deploy config
.env.example     — Environment variable template
```
