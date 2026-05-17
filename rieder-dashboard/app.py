"""
Rieder-Style Global Macro Dashboard  v2
────────────────────────────────────────
Tabs:
  Macro       – KPI cards, rate history, global rates, cross-asset returns
  Yield Curves– US / EU / Japan / UK / Canada snapshot + historical spreads
  Factors     – Fama-French 5-factor cumulative returns & correlation
  Cross-Asset – Sector rotation, multi-period returns, VIX
  Credit      – Corporate bond yields by rating, OAS spreads, defaults/bankruptcies
  Company     – Individual equity OHLC, fundamentals
  News        – Live RSS + NewsAPI feed

Deploy:
  python app.py            # local  → http://localhost:8050
  gunicorn app:server      # prod
"""

import os
import logging
from datetime import datetime

import dash
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
from dash import dcc, html, Input, Output, State
from flask_caching import Cache
from dotenv import load_dotenv

from data_fetcher import DataFetcher, SECTOR_ETFS, CORP_OAS_SERIES

load_dotenv()
logging.basicConfig(level=logging.INFO)

# ── App / cache setup ─────────────────────────────────────────────────────────
app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.CYBORG, dbc.icons.BOOTSTRAP],
    suppress_callback_exceptions=True,
    meta_tags=[{"name": "viewport", "content": "width=device-width, initial-scale=1"}],
    title="Global Macro Dashboard",
)
server = app.server
cache  = Cache(server, config={"CACHE_TYPE": "SimpleCache", "CACHE_DEFAULT_TIMEOUT": 300})
fetcher = DataFetcher()

# ── Palette ───────────────────────────────────────────────────────────────────
C = {
    "bg":     "#0d1117", "card":   "#161b22", "border": "#30363d",
    "primary":"#58a6ff", "green":  "#3fb950", "red":    "#f85149",
    "yellow": "#d29922", "orange": "#e3a057", "text":   "#e6edf3",
    "muted":  "#8b949e",
    # per-region colours for yield curves
    "US":     "#58a6ff", "EU":     "#3fb950", "Japan":  "#f78166",
    "UK":     "#d29922", "Canada": "#bc8cff",
}

CHART = dict(
    paper_bgcolor=C["card"], plot_bgcolor=C["bg"],
    font=dict(color=C["text"], family="Inter, Arial"),
    xaxis=dict(gridcolor=C["border"], showgrid=True, zeroline=False),
    yaxis=dict(gridcolor=C["border"], showgrid=True, zeroline=False),
    legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(size=11)),
    margin=dict(l=50, r=20, t=40, b=40),
)

# ── Shared helpers ─────────────────────────────────────────────────────────────

def kpi_card(label, value, prev=None, unit="%", decimals=2):
    val_fmt = f"{value:.{decimals}f}{unit}" if isinstance(value, (int, float)) else str(value)
    delta_el = html.Span()
    if isinstance(value, (int, float)) and prev is not None:
        delta = value - prev
        clr   = C["green"] if delta >= 0 else C["red"]
        delta_el = html.Span(
            f" {'▲' if delta>=0 else '▼'} {abs(delta):.2f}",
            style={"color": clr, "fontSize": "0.75rem"},
        )
    return dbc.Card(
        dbc.CardBody([
            html.P(label, className="text-muted mb-1",
                   style={"fontSize":"0.68rem","letterSpacing":"0.08em","textTransform":"uppercase"}),
            html.H4([val_fmt, delta_el], className="mb-0", style={"color": C["text"]}),
        ]),
        style={"background": C["card"], "border": f"1px solid {C['border']}"},
        className="h-100",
    )


def sh(title, subtitle=""):
    return html.Div([
        html.H5(title, style={"color": C["primary"], "marginBottom": "2px"}),
        html.P(subtitle, className="text-muted",
               style={"fontSize":"0.78rem","marginBottom":"12px"}) if subtitle else html.Span(),
    ])


def select(id_, options, value, width="200px"):
    return dbc.Select(
        id=id_, options=options, value=value,
        style={"background": C["card"], "color": C["text"],
               "border": f"1px solid {C['border']}", "width": width},
    )


# ══════════════════════════════════════════════════════════════════════════════
# TAB LAYOUTS
# ══════════════════════════════════════════════════════════════════════════════

def tab_macro():
    return dbc.Container([
        html.Br(),
        sh("Macro Overview", "Key economic indicators – FRED + market data"),
        html.Div(id="macro-kpi-row"),
        html.Br(),
        dbc.Row([
            dbc.Col([
                sh("Rate History"),
                select("macro-series-select", [
                    {"label":"Fed Funds Rate","value":"FEDFUNDS"},
                    {"label":"10Y Treasury",  "value":"DGS10"},
                    {"label":"2Y Treasury",   "value":"DGS2"},
                    {"label":"10Y-2Y Spread", "value":"T10Y2Y"},
                    {"label":"CPI YoY",       "value":"CPIAUCSL"},
                    {"label":"Core PCE YoY",  "value":"PCEPILFE"},
                    {"label":"Unemployment",  "value":"UNRATE"},
                    {"label":"M2 Money",      "value":"M2SL"},
                ], "DGS10", "220px"),
                dcc.Graph(id="macro-history-chart", style={"height":"340px"}),
            ], md=8),
            dbc.Col([
                sh("Global Rates"),
                html.Div(id="global-rates-table"),
                html.Br(),
                sh("Cross-Asset 1M Returns"),
                dcc.Graph(id="cross-asset-chart", style={"height":"240px"}),
            ], md=4),
        ]),
    ], fluid=True)


def tab_yield_curves():
    return dbc.Container([
        html.Br(),
        sh("Global Yield Curves",
           "Scraped live: US Treasury.gov · ECB API · Japan MoF · BoE · Bank of Canada"),
        dbc.Row([
            dbc.Col([
                dbc.Row([
                    dbc.Col(
                        dbc.Checklist(
                            id="yc-regions",
                            options=[
                                {"label": "US Treasury", "value": "US"},
                                {"label": "EU Bund",     "value": "EU"},
                                {"label": "Japan JGB",   "value": "Japan"},
                                {"label": "UK Gilt",     "value": "UK"},
                                {"label": "Canada",      "value": "Canada"},
                            ],
                            value=["US", "EU", "Japan", "UK", "Canada"],
                            inline=True,
                            inputStyle={"marginRight":"4px"},
                            labelStyle={"marginRight":"14px","fontSize":"0.82rem"},
                        ),
                        width=12, className="mb-2",
                    ),
                ]),
                dcc.Graph(id="yc-snapshot", style={"height":"400px"}),
                html.Div(id="yc-data-status",
                         style={"fontSize":"0.72rem","color":C["muted"],"marginTop":"4px"}),
            ], md=7),
            dbc.Col([
                sh("Historical Rates"),
                select("yc-region", [
                    {"label":"US Treasuries",  "value":"US"},
                    {"label":"European Bunds", "value":"EU"},
                    {"label":"Japan JGBs",     "value":"Japan"},
                    {"label":"UK Gilts",       "value":"UK"},
                    {"label":"Canada",         "value":"Canada"},
                ], "US"),
                html.Br(),
                dcc.Graph(id="yc-history", style={"height":"320px"}),
            ], md=5),
        ]),
        html.Br(),
        sh("US 10Y-2Y Inversion Tracker"),
        dcc.Graph(id="yc-spread-history", style={"height":"240px"}),
    ], fluid=True)


def tab_factors():
    return dbc.Container([
        html.Br(),
        sh("Fama-French 5-Factor Returns",
           "Source: Ken French Data Library  |  Monthly rebalanced"),
        dbc.Row([
            dbc.Col([
                select("factor-lookback",[
                    {"label":"1 Year","value":"1"},
                    {"label":"3 Years","value":"3"},
                    {"label":"5 Years","value":"5"},
                ],"3","160px"),
                html.Br(),
                dcc.Graph(id="factor-cumulative", style={"height":"380px"}),
            ], md=8),
            dbc.Col([
                sh("Factor Definitions"),
                *[html.Div([
                    html.Span("■ ", style={"color": clr, "fontSize":"1.1rem"}),
                    html.Strong(nm, style={"color":C["text"]}),
                    html.Span(f" — {desc}",
                              style={"color":C["muted"],"fontSize":"0.78rem"}),
                ], className="mb-2") for nm,desc,clr in [
                    ("Mkt-RF","Market excess return",    C["primary"]),
                    ("SMB",   "Small Minus Big",         "#c9d1d9"),
                    ("HML",   "High Minus Low (value)",  C["yellow"]),
                    ("RMW",   "Robust Minus Weak (profit)",C["green"]),
                    ("CMA",   "Conservative Minus Aggressive",C["red"]),
                ]],
                html.Br(),
                sh("Factor Correlation"),
                dcc.Graph(id="factor-correlation", style={"height":"260px"}),
            ], md=4),
        ]),
        html.Br(),
        sh("Monthly Factor Returns – Last 12 Months"),
        dcc.Graph(id="factor-monthly", style={"height":"280px"}),
    ], fluid=True)


def tab_crossasset():
    return dbc.Container([
        html.Br(),
        sh("Sector & Industry Rotation", "US equity sector ETF performance"),
        dbc.Row([
            dbc.Col([
                select("sector-period",[
                    {"label":"1 Month","value":"1mo"},
                    {"label":"3 Months","value":"3mo"},
                    {"label":"YTD","value":"ytd"},
                    {"label":"1 Year","value":"1y"},
                ],"1mo","160px"),
                html.Br(),
                dcc.Graph(id="sector-chart", style={"height":"340px"}),
            ], md=6),
            dbc.Col([
                sh("Multi-Period Asset Returns"),
                dcc.Graph(id="asset-returns-chart", style={"height":"380px"}),
            ], md=6),
        ]),
        html.Br(),
        sh("VIX – Fear Index (1 Year)"),
        dcc.Graph(id="vix-chart", style={"height":"240px"}),
    ], fluid=True)


def tab_credit():
    return dbc.Container([
        html.Br(),
        sh("Credit Markets – Corporate Bonds & Default Monitor",
           "Yields: FRED ICE BofA indices (no key needed) · Defaults: SEC EDGAR 8-K"),
        # ── OAS Snapshot KPIs ────────────────────────────────────────────────
        html.Div(id="credit-oas-kpis"),
        html.Br(),
        dbc.Row([
            dbc.Col([
                sh("Corporate Bond Yields by Rating"),
                dcc.Graph(id="corp-yields-chart", style={"height":"360px"}),
            ], md=6),
            dbc.Col([
                sh("OAS Spreads History"),
                select("oas-lookback",[
                    {"label":"1 Year","value":"1"},
                    {"label":"3 Years","value":"3"},
                    {"label":"5 Years","value":"5"},
                ],"3","160px"),
                html.Br(),
                dcc.Graph(id="oas-history-chart", style={"height":"300px"}),
            ], md=6),
        ]),
        html.Br(),
        dbc.Row([
            dbc.Col([
                sh("Credit Stress Indicators"),
                dcc.Graph(id="credit-stress-chart", style={"height":"280px"}),
            ], md=7),
            dbc.Col([
                sh("Credit Spread vs Treasury (Spread = Corp - T10Y)"),
                dcc.Graph(id="corp-spread-chart", style={"height":"280px"}),
            ], md=5),
        ]),
        html.Br(),
        sh("Recent Corporate Bankruptcies & Defaults",
           "Source: SEC EDGAR 8-K filings (Chapter 11) + Reuters/Bloomberg RSS"),
        dbc.Row([
            dbc.Col([
                dbc.Button("Refresh Defaults", id="defaults-refresh-btn",
                           color="outline-primary", size="sm", n_clicks=0),
                select("defaults-period",[
                    {"label":"Last 30 days","value":"30"},
                    {"label":"Last 90 days","value":"90"},
                    {"label":"Last 1 year", "value":"365"},
                ],"90","160px"),
            ], className="d-flex gap-2 align-items-center mb-3"),
        ]),
        html.Div(id="defaults-table"),
    ], fluid=True)


def tab_company():
    return dbc.Container([
        html.Br(),
        sh("Company Analysis", "Equity fundamentals + OHLC price history"),
        dbc.Row([dbc.Col([
            dbc.InputGroup([
                dbc.Input(id="company-ticker", placeholder="Ticker (e.g. AAPL)",
                          value="AAPL", debounce=True,
                          style={"background":C["card"],"color":C["text"],
                                 "border":f"1px solid {C['border']}"}),
                dbc.Button("Analyse", id="company-btn", color="primary", n_clicks=0),
            ], style={"maxWidth":"360px"}),
        ])]),
        html.Br(),
        html.Div(id="company-kpis"),
        html.Br(),
        dcc.Graph(id="company-price-chart", style={"height":"380px"}),
        html.Br(),
        html.P(id="company-description",
               style={"color":C["muted"],"fontSize":"0.82rem","lineHeight":"1.6"}),
    ], fluid=True)


def tab_news():
    return dbc.Container([
        html.Br(),
        sh("Market News & Trading Intelligence",
           "Live from Reuters, Yahoo Finance, WSJ RSS"),
        dbc.Row([dbc.Col([
            dbc.Button("Refresh", id="news-refresh-btn",
                       color="outline-primary", size="sm", n_clicks=0),
        ])]),
        html.Br(),
        html.Div(id="news-feed"),
    ], fluid=True)


def news_card(a: dict):
    return dbc.Card(dbc.CardBody([
        html.A(a["title"], href=a.get("url","#"), target="_blank",
               style={"color":C["primary"],"fontWeight":"600",
                      "textDecoration":"none","fontSize":"0.92rem"}),
        html.P(a.get("summary",""), className="mt-1 mb-1",
               style={"color":C["muted"],"fontSize":"0.78rem","lineHeight":"1.5"}),
        html.Small(f"{a.get('source','')}  •  {a.get('time','')}",
                   style={"color":C["border"]}),
    ]), className="mb-2",
    style={"background":C["card"],"border":f"1px solid {C['border']}"})


# ══════════════════════════════════════════════════════════════════════════════
# MAIN LAYOUT
# ══════════════════════════════════════════════════════════════════════════════

NAVBAR = dbc.Navbar(
    dbc.Container([
        dbc.Row([
            dbc.Col(html.Div([
                html.Span("◈ ", style={"color":C["primary"],"fontSize":"1.4rem"}),
                html.Span("GLOBAL MACRO", style={"fontWeight":"900","letterSpacing":"0.15em",
                                                  "color":C["text"],"fontSize":"1.1rem"}),
                html.Span("  DASHBOARD", style={"fontWeight":"300","letterSpacing":"0.1em",
                                                 "color":C["muted"],"fontSize":"0.85rem"}),
            ])),
            dbc.Col(html.Div(id="live-clock",
                             style={"textAlign":"right","color":C["muted"],
                                    "fontSize":"0.8rem","fontFamily":"monospace"}),
                    width="auto"),
        ], align="center", className="w-100"),
    ], fluid=True),
    color=C["card"], dark=True,
    style={"borderBottom":f"1px solid {C['border']}"},
)

TABS = dbc.Tabs([
    dbc.Tab(label="Macro",        tab_id="tab-macro"),
    dbc.Tab(label="Yield Curves", tab_id="tab-yc"),
    dbc.Tab(label="Factors",      tab_id="tab-factors"),
    dbc.Tab(label="Cross-Asset",  tab_id="tab-crossasset"),
    dbc.Tab(label="Credit",       tab_id="tab-credit"),
    dbc.Tab(label="Company",      tab_id="tab-company"),
    dbc.Tab(label="News",         tab_id="tab-news"),
], id="main-tabs", active_tab="tab-macro",
   style={"background":C["card"],"borderBottom":f"1px solid {C['border']}"})

app.layout = html.Div([
    NAVBAR,
    TABS,
    html.Div(id="tab-content",
             style={"minHeight":"80vh","background":C["bg"],"padding":"0 0 40px 0"}),
    dcc.Interval(id="clock-interval",  interval=1_000,    n_intervals=0),
    dcc.Interval(id="data-interval",   interval=300_000,  n_intervals=0),
], style={"background":C["bg"],"minHeight":"100vh","fontFamily":"Inter,system-ui,Arial"})


# ══════════════════════════════════════════════════════════════════════════════
# CALLBACKS
# ══════════════════════════════════════════════════════════════════════════════

@app.callback(Output("live-clock","children"), Input("clock-interval","n_intervals"))
def update_clock(_):
    now = datetime.utcnow()
    return f"UTC  {now.strftime('%Y-%m-%d  %H:%M:%S')}  |  Data delayed ≤ 15 min"


@app.callback(Output("tab-content","children"), Input("main-tabs","active_tab"))
def render_tab(tab):
    return {
        "tab-macro":       tab_macro,
        "tab-yc":          tab_yield_curves,
        "tab-factors":     tab_factors,
        "tab-crossasset":  tab_crossasset,
        "tab-credit":      tab_credit,
        "tab-company":     tab_company,
        "tab-news":        tab_news,
    }.get(tab, tab_macro)()


# ── Macro ──────────────────────────────────────────────────────────────────────

@app.callback(Output("macro-kpi-row","children"), Input("data-interval","n_intervals"))
@cache.memoize(timeout=300)
def update_macro_kpis(_):
    kpis = fetcher.get_macro_kpis()
    units = {"Fed Funds Rate":"%","CPI YoY":"%","Core PCE YoY":"%",
             "Unemployment":"%","10Y-2Y Spread":"bp","US GDP Growth":"%",
             "M2 Growth":"%","VIX":"","US 10Y":"%","Gold":"$","S&P 500":""}
    cards = []
    for label, data in kpis.items():
        unit = units.get(label, "")
        cards.append(dbc.Col(kpi_card(label, data["value"], data.get("prev"), unit=unit),
                             xs=6, sm=4, md=3, lg=2, className="mb-3"))
    return dbc.Row(cards)


@app.callback(Output("macro-history-chart","figure"),
              Input("macro-series-select","value"), Input("data-interval","n_intervals"))
@cache.memoize(timeout=300)
def update_macro_chart(sid, _):
    s = fetcher.get_macro_history(sid, lookback_years=5)
    if s.empty:
        return go.Figure(layout=go.Layout(**CHART, title="No data – FRED API key recommended"))
    if sid in {"CPIAUCSL","PCEPILFE","M2SL"}:
        s = s.pct_change(12).dropna() * 100
        ylabel = "YoY %"
    else:
        ylabel = "%"
    fig = go.Figure(go.Scatter(x=s.index, y=s.values, mode="lines",
                               line=dict(color=C["primary"], width=2),
                               fill="tozeroy", fillcolor="rgba(88,166,255,0.07)"))
    fig.update_layout(**CHART, title=sid, yaxis_title=ylabel)
    return fig


@app.callback(Output("global-rates-table","children"), Input("data-interval","n_intervals"))
@cache.memoize(timeout=300)
def update_global_rates(_):
    df = fetcher.get_global_rates()
    if df.empty:
        return html.P("Rate data unavailable", className="text-muted")
    rows = []
    for idx, row in df.iterrows():
        rate = row.get("rate", 0)
        chg  = row.get("chg",  0)
        clr  = C["green"] if chg >= 0 else C["red"]
        rows.append(html.Tr([
            html.Td(idx,      style={"color":C["muted"],"fontSize":"0.8rem"}),
            html.Td(f"{rate:.3f}%", style={"color":C["text"],"fontFamily":"monospace"}),
            html.Td(f"{'▲' if chg>=0 else '▼'} {abs(chg):.3f}",
                    style={"color":clr,"fontFamily":"monospace","fontSize":"0.8rem"}),
        ]))
    return dbc.Table(
        [html.Thead(html.Tr([html.Th("Region"),html.Th("Rate"),html.Th("Chg")])),
         html.Tbody(rows)],
        bordered=False, size="sm",
        style={"color":C["text"],"background":"transparent"},
    )


@app.callback(Output("cross-asset-chart","figure"), Input("data-interval","n_intervals"))
@cache.memoize(timeout=300)
def update_cross_asset(_):
    rets = fetcher.get_cross_asset_returns("1mo")
    if rets.empty:
        return go.Figure(layout=go.Layout(**CHART))
    colors = [C["green"] if v >= 0 else C["red"] for v in rets.values]
    fig = go.Figure(go.Bar(x=rets.values, y=rets.index, orientation="h",
                           marker_color=colors,
                           text=[f"{v:+.1f}%" for v in rets.values],
                           textposition="outside"))
    fig.update_layout(**CHART, title="1-Month Returns (%)",
                      margin=dict(l=100, r=60, t=40, b=20))
    return fig


# ── Yield Curves ───────────────────────────────────────────────────────────────

TENOR_ORDER = ["1M","2M","3M","6M","1Y","2Y","3Y","5Y","7Y","10Y","15Y","20Y","30Y","40Y"]

@app.callback(
    Output("yc-snapshot",    "figure"),
    Output("yc-data-status", "children"),
    Input("yc-regions",      "value"),
    Input("data-interval",   "n_intervals"),
)
@cache.memoize(timeout=300)
def update_yc_snapshot(selected_regions, _):
    region_colors = {"US":"US","EU":"EU","Japan":"Japan","UK":"UK","Canada":"Canada"}
    fetch_fns = {
        "US":     lambda: fetcher.get_us_yield_curve(lookback_days=5).iloc[-1].dropna(),
        "EU":     fetcher.get_eu_yield_curve,
        "Japan":  fetcher.get_japan_yield_curve,
        "UK":     fetcher.get_uk_yield_curve,
        "Canada": fetcher.get_canada_yield_curve,
    }
    labels_map = {
        "US":"US Treasury","EU":"EU Bund","Japan":"Japan JGB",
        "UK":"UK Gilt","Canada":"Canada Govt",
    }

    fig = go.Figure()
    status_parts = []
    for region in (selected_regions or []):
        try:
            s = fetch_fns[region]()
            if s.empty:
                status_parts.append(f"⚠ {labels_map[region]}: no data")
                continue
            # sort by tenor order
            ordered = [t for t in TENOR_ORDER if t in s.index]
            s = s[ordered]
            fig.add_trace(go.Scatter(
                x=s.index.tolist(), y=s.values.tolist(),
                mode="lines+markers", name=labels_map[region],
                line=dict(color=C[region_colors[region]], width=2.5),
                marker=dict(size=5),
            ))
            status_parts.append(f"✓ {labels_map[region]}")
        except Exception as e:
            status_parts.append(f"✗ {labels_map[region]}: {e}")

    fig.update_layout(**CHART,
                      title="Global Yield Curves – Current Snapshot",
                      yaxis_title="Yield (%)", xaxis_title="Maturity")
    return fig, "  |  ".join(status_parts)


@app.callback(Output("yc-history","figure"),
              Input("yc-region","value"), Input("data-interval","n_intervals"))
@cache.memoize(timeout=300)
def update_yc_history(region, _):
    df = fetcher.get_yield_curve_history(region, lookback_days=365)
    if df.empty:
        return go.Figure(layout=go.Layout(**CHART,
                                          title="No historical data for this region"))
    fig = go.Figure()
    clr_map = {"2Y":C["primary"],"5Y":C["yellow"],"10Y":C["green"],"30Y":C["red"]}
    for col in df.columns:
        fig.add_trace(go.Scatter(x=df.index, y=df[col], mode="lines", name=col,
                                 line=dict(color=clr_map.get(col,"#aaa"), width=1.8)))
    fig.update_layout(**CHART, title=f"{region} Yield History (1Y)", yaxis_title="Yield (%)")
    return fig


@app.callback(Output("yc-spread-history","figure"), Input("data-interval","n_intervals"))
@cache.memoize(timeout=300)
def update_yc_spread(_):
    df = fetcher.get_yield_curve_history("US", lookback_days=730)
    if df.empty or "10Y" not in df.columns or "2Y" not in df.columns:
        return go.Figure(layout=go.Layout(**CHART))
    spread = (df["10Y"] - df["2Y"]).dropna()
    colors = [C["green"] if v >= 0 else C["red"] for v in spread.values]
    fig = go.Figure(go.Bar(x=spread.index, y=spread.values, marker_color=colors))
    fig.add_hline(y=0, line_dash="dot", line_color=C["muted"])
    fig.update_layout(**CHART, title="US 10Y-2Y Spread (Inversion Tracker)",
                      yaxis_title="Spread (%)")
    return fig


# ── Factors ────────────────────────────────────────────────────────────────────

FACTOR_COLORS = {"Mkt-RF":C["primary"],"SMB":"#c9d1d9","HML":C["yellow"],
                 "RMW":C["green"],"CMA":C["red"]}

@app.callback(Output("factor-cumulative","figure"),
              Input("factor-lookback","value"), Input("data-interval","n_intervals"))
@cache.memoize(timeout=600)
def update_factor_cum(years, _):
    cum = fetcher.get_cumulative_factors(int(years))
    if cum.empty:
        return go.Figure(layout=go.Layout(**CHART, title="Factor data unavailable"))
    fig = go.Figure()
    for col in cum.columns:
        fig.add_trace(go.Scatter(x=cum.index, y=cum[col], mode="lines", name=col,
                                 line=dict(color=FACTOR_COLORS.get(col,"#aaa"), width=2)))
    fig.add_hline(y=1.0, line_dash="dot", line_color=C["muted"])
    fig.update_layout(**CHART, title=f"FF5 Cumulative Returns ({years}Y)",
                      yaxis_title="Growth of $1")
    return fig


@app.callback(Output("factor-correlation","figure"),
              Input("factor-lookback","value"), Input("data-interval","n_intervals"))
@cache.memoize(timeout=600)
def update_factor_corr(years, _):
    ff = fetcher.get_factor_returns(int(years))
    if ff.empty:
        return go.Figure(layout=go.Layout(**CHART))
    factors = [c for c in ff.columns if c != "RF"]
    corr = ff[factors].corr()
    fig = px.imshow(corr, text_auto=".2f", color_continuous_scale="RdBu_r",
                    zmin=-1, zmax=1, aspect="auto")
    fig.update_layout(**CHART, title="Factor Correlation",
                      coloraxis_showscale=False, margin=dict(l=20,r=20,t=40,b=20))
    return fig


@app.callback(Output("factor-monthly","figure"),
              Input("factor-lookback","value"), Input("data-interval","n_intervals"))
@cache.memoize(timeout=600)
def update_factor_monthly(years, _):
    ff = fetcher.get_factor_returns(int(years))
    if ff.empty:
        return go.Figure(layout=go.Layout(**CHART))
    factors = [c for c in ff.columns if c != "RF"]
    last12  = ff[factors].tail(12) * 100
    fig = go.Figure()
    for col in last12.columns:
        fig.add_trace(go.Bar(x=last12.index.strftime("%b %Y"), y=last12[col],
                             name=col, marker_color=FACTOR_COLORS.get(col,"#aaa")))
    fig.update_layout(**CHART, barmode="group",
                      title="Monthly Factor Returns – Last 12 Months (%)",
                      yaxis_title="%")
    return fig


# ── Cross-Asset ────────────────────────────────────────────────────────────────

@app.callback(Output("sector-chart","figure"),
              Input("sector-period","value"), Input("data-interval","n_intervals"))
@cache.memoize(timeout=300)
def update_sectors(period, _):
    perf = fetcher.get_sector_performance(period)
    if perf.empty:
        return go.Figure(layout=go.Layout(**CHART))
    colors = [C["green"] if v >= 0 else C["red"] for v in perf.values]
    fig = go.Figure(go.Bar(x=perf.index, y=perf.values, marker_color=colors,
                           text=[f"{v:+.1f}%" for v in perf.values],
                           textposition="outside"))
    fig.update_layout(**CHART, title=f"US Sector Returns ({period})",
                      yaxis_title="%", xaxis_tickangle=-30)
    return fig


@app.callback(Output("asset-returns-chart","figure"), Input("data-interval","n_intervals"))
@cache.memoize(timeout=300)
def update_asset_returns(_):
    import yfinance as yf
    assets = {"S&P 500":"^GSPC","NASDAQ":"^IXIC","Gold":"GC=F",
              "Oil":"CL=F","DXY":"DX-Y.NYB","Agg Bonds":"AGG"}
    periods = {"1W":"5d","1M":"1mo","3M":"3mo","YTD":"ytd"}
    clrs = [C["primary"],C["green"],C["yellow"],C["red"]]
    fig = go.Figure()
    for (period_label, yf_period), clr in zip(periods.items(), clrs):
        vals, labels = [], []
        for label, tkr in assets.items():
            try:
                h = yf.Ticker(tkr).history(period=yf_period)["Close"].dropna()
                if len(h) >= 2:
                    vals.append(round((h.iloc[-1]/h.iloc[0]-1)*100, 2))
                    labels.append(label)
            except Exception:
                pass
        if vals:
            fig.add_trace(go.Bar(x=labels, y=vals, name=period_label, marker_color=clr))
    fig.update_layout(**CHART, barmode="group", title="Multi-Period Asset Returns",
                      yaxis_title="%", xaxis_tickangle=-20)
    return fig


@app.callback(Output("vix-chart","figure"), Input("data-interval","n_intervals"))
@cache.memoize(timeout=120)
def update_vix(_):
    import yfinance as yf
    h = yf.Ticker("^VIX").history(period="1y")["Close"].dropna()
    fig = go.Figure(go.Scatter(x=h.index, y=h.values, mode="lines",
                               line=dict(color=C["yellow"], width=2),
                               fill="tozeroy", fillcolor="rgba(210,153,34,0.08)"))
    fig.add_hline(y=20, line_dash="dot", line_color=C["muted"],
                  annotation_text="20 – Elevated")
    fig.add_hline(y=30, line_dash="dot", line_color=C["red"],
                  annotation_text="30 – Stress")
    fig.update_layout(**CHART, title="VIX (1Y)", yaxis_title="VIX Level")
    return fig


# ── Credit Tab ─────────────────────────────────────────────────────────────────

@app.callback(Output("credit-oas-kpis","children"), Input("data-interval","n_intervals"))
@cache.memoize(timeout=300)
def update_oas_kpis(_):
    snapshot = fetcher.get_credit_spread_snapshot()
    if not snapshot:
        return html.P("OAS data loading… (FRED ICE BofA series)",
                      className="text-muted")
    cards = []
    rating_order = ["IG OAS","HY OAS","BBB OAS","BB OAS","B OAS"]
    for label in rating_order:
        if label not in snapshot:
            continue
        d = snapshot[label]
        zscore = d["zscore"]
        clr = C["red"] if zscore > 1 else (C["yellow"] if zscore > 0 else C["green"])
        cards.append(dbc.Col(html.Div([
            html.P(label, className="text-muted mb-0",
                   style={"fontSize":"0.68rem","textTransform":"uppercase","letterSpacing":"0.08em"}),
            html.H4(f"{d['value']:.0f}bp", className="mb-0",
                    style={"color":C["text"]}),
            html.Small(f"Z={zscore:+.1f}  |  {d['pct']:.0f}th %ile  |  5Y avg {d['5y_avg']:.0f}bp",
                       style={"color":clr,"fontSize":"0.72rem"}),
        ], style={"background":C["card"],"border":f"1px solid {C['border']}",
                  "borderRadius":"6px","padding":"10px 14px"}),
            xs=6, sm=4, md=3, lg=2, className="mb-3"))
    return dbc.Row(cards)


@app.callback(Output("corp-yields-chart","figure"), Input("data-interval","n_intervals"))
@cache.memoize(timeout=300)
def update_corp_yields(_):
    df = fetcher.get_corporate_bond_yields(lookback_years=3)
    if df.empty:
        return go.Figure(layout=go.Layout(**CHART, title="Corporate yield data unavailable"))

    rating_colors = {
        "AAA": "#3fb950", "AA": "#58a6ff", "A": "#d29922",
        "BBB": "#e3a057", "HY": "#f85149", "IG": "#bc8cff",
    }
    # Use last available row as snapshot bar chart
    latest = df.dropna(how="all").iloc[-1].dropna()
    ratings = [r for r in ["AAA","AA","A","BBB","IG","HY"] if r in latest.index]
    vals    = [latest[r] for r in ratings]
    colors  = [rating_colors.get(r,"#aaa") for r in ratings]

    fig = go.Figure(go.Bar(
        x=ratings, y=vals, marker_color=colors,
        text=[f"{v:.2f}%" for v in vals], textposition="outside",
    ))
    fig.update_layout(**CHART, title="Corporate Bond Effective Yield by Rating (Today)",
                      yaxis_title="Yield %")
    return fig


@app.callback(Output("oas-history-chart","figure"),
              Input("oas-lookback","value"), Input("data-interval","n_intervals"))
@cache.memoize(timeout=300)
def update_oas_history(years, _):
    df = fetcher.get_credit_spreads_history(lookback_years=int(years))
    if df.empty:
        return go.Figure(layout=go.Layout(**CHART, title="OAS data loading…"))
    oas_colors = {
        "IG OAS":C["green"],"HY OAS":C["red"],"BBB OAS":C["yellow"],
        "BB OAS":C["orange"],"B OAS":"#ff7b72",
    }
    fig = go.Figure()
    for col in df.columns:
        fig.add_trace(go.Scatter(x=df.index, y=df[col], mode="lines", name=col,
                                 line=dict(color=oas_colors.get(col,"#aaa"), width=1.8)))
    fig.update_layout(**CHART, title=f"OAS Spreads History ({years}Y)",
                      yaxis_title="OAS (bps)")
    return fig


@app.callback(Output("credit-stress-chart","figure"), Input("data-interval","n_intervals"))
@cache.memoize(timeout=600)
def update_credit_stress(_):
    df = fetcher.get_default_rate_history()
    if df.empty:
        return go.Figure(layout=go.Layout(**CHART,
                                          title="Credit stress data loading…"))
    fig = go.Figure()
    stress_colors = {
        "HY OAS (bps)":          C["red"],
        "C&I Loan Delinquency%": C["yellow"],
        "Credit Card Charge-off%":C["orange"],
    }
    for col in df.columns:
        # Normalise each series to its own y-axis range for readability
        series = df[col].dropna()
        fig.add_trace(go.Scatter(
            x=series.index, y=series.values, mode="lines", name=col,
            line=dict(color=stress_colors.get(col,"#aaa"), width=1.8),
        ))
    fig.update_layout(**CHART, title="Credit Stress Indicators (10Y)",
                      yaxis_title="Level", legend_orientation="h",
                      legend=dict(y=-0.25, x=0))
    return fig


@app.callback(Output("corp-spread-chart","figure"), Input("data-interval","n_intervals"))
@cache.memoize(timeout=300)
def update_corp_spread(_):
    df = fetcher.get_credit_spread_vs_treasury()
    if df.empty:
        return go.Figure(layout=go.Layout(**CHART, title="Spread data loading…"))
    fig = go.Figure()
    spread_colors = {"AAA Spread":C["green"],"A Spread":C["yellow"],
                     "BBB Spread":C["orange"],"HY Spread":C["red"]}
    for col in df.columns:
        fig.add_trace(go.Scatter(x=df.index, y=df[col], mode="lines", name=col,
                                 line=dict(color=spread_colors.get(col,"#aaa"), width=1.8)))
    fig.add_hline(y=0, line_dash="dot", line_color=C["muted"])
    fig.update_layout(**CHART, title="Corp Yield Spread vs 10Y Treasury (%)",
                      yaxis_title="%")
    return fig


@app.callback(
    Output("defaults-table","children"),
    Input("defaults-refresh-btn","n_clicks"),
    Input("defaults-period","value"),
    Input("data-interval","n_intervals"),
)
@cache.memoize(timeout=300)
def update_defaults(_, lookback_days_str, __):
    lookback = int(lookback_days_str or 90)
    df = fetcher.get_corporate_defaults(lookback_days=lookback)
    if df.empty:
        return html.P("No bankruptcy filings found in the selected period.",
                      className="text-muted")

    rows = []
    for _, row in df.iterrows():
        date_str = row["date"].strftime("%Y-%m-%d") if pd.notna(row["date"]) else "—"
        src_badge = dbc.Badge(
            row.get("source",""), color="secondary", className="ms-1",
            style={"fontSize":"0.65rem"},
        )
        rows.append(html.Tr([
            html.Td(date_str, style={"color":C["muted"],"fontSize":"0.78rem","whiteSpace":"nowrap"}),
            html.Td([html.Strong(row.get("company",""), style={"color":C["text"],"fontSize":"0.82rem"}),
                     src_badge]),
            html.Td(html.Span(row.get("type",""), style={"color":C["red"],"fontSize":"0.78rem"})),
            html.Td(row.get("detail","")[:120], style={"color":C["muted"],"fontSize":"0.74rem"}),
        ]))

    return dbc.Table(
        [html.Thead(html.Tr([html.Th("Date"),html.Th("Company / Source"),
                             html.Th("Type"),html.Th("Detail")])),
         html.Tbody(rows)],
        bordered=False, hover=True, size="sm",
        style={"color":C["text"],"background":"transparent"},
    )


# ── Company ────────────────────────────────────────────────────────────────────

@app.callback(
    Output("company-kpis","children"),
    Output("company-price-chart","figure"),
    Output("company-description","children"),
    Input("company-btn","n_clicks"),
    State("company-ticker","value"),
    prevent_initial_call=False,
)
def update_company(_, ticker):
    ticker = (ticker or "AAPL").upper().strip()
    data   = fetcher.get_company_data(ticker)
    info   = data["info"]
    hist   = data["history"]

    def fmt(v, mult=1, suffix="", decimals=2):
        if v is None: return "N/A"
        v *= mult
        if abs(v) >= 1e12: return f"{v/1e12:.1f}T{suffix}"
        if abs(v) >= 1e9:  return f"{v/1e9:.1f}B{suffix}"
        if abs(v) >= 1e6:  return f"{v/1e6:.1f}M{suffix}"
        return f"{v:.{decimals}f}{suffix}"

    kpi_items = [
        ("Sector",      info.get("sector","N/A")),
        ("Market Cap",  fmt(info.get("market_cap"))),
        ("P/E Ratio",   fmt(info.get("pe_ratio"), suffix="x")),
        ("P/B Ratio",   fmt(info.get("pb_ratio"), suffix="x")),
        ("ROE",         fmt(info.get("roe"), mult=100, suffix="%") if info.get("roe") else "N/A"),
        ("Debt/Equity", fmt(info.get("debt_equity"), suffix="%") if info.get("debt_equity") else "N/A"),
        ("Div Yield",   fmt(info.get("div_yield"), mult=100, suffix="%") if info.get("div_yield") else "N/A"),
        ("Beta",        fmt(info.get("beta"))),
    ]
    cards = dbc.Row([
        dbc.Col(html.Div([
            html.H2(info.get("name", ticker), style={"color":C["text"]}),
            html.Small(info.get("industry",""), style={"color":C["muted"]}),
        ]), width=12, className="mb-3"),
    ] + [dbc.Col(html.Div([
            html.P(lbl, className="text-muted mb-0",
                   style={"fontSize":"0.68rem","textTransform":"uppercase","letterSpacing":"0.08em"}),
            html.H5(val, className="mb-0", style={"color":C["text"]}),
        ], style={"background":C["card"],"border":f"1px solid {C['border']}",
                  "borderRadius":"6px","padding":"10px 14px"}),
        xs=6, sm=4, md=3, lg=2, className="mb-2")
    for lbl, val in kpi_items])

    if not hist.empty:
        fig = go.Figure()
        fig.add_trace(go.Candlestick(
            x=hist.index, open=hist["Open"], high=hist["High"],
            low=hist["Low"], close=hist["Close"], name=ticker,
            increasing_line_color=C["green"], decreasing_line_color=C["red"],
        ))
        fig.add_trace(go.Bar(x=hist.index, y=hist["Volume"], name="Volume",
                             yaxis="y2", marker_color="rgba(88,166,255,0.2)"))
        fig.update_layout(
            **CHART, title=f"{ticker} – 1Y Price (OHLC)",
            yaxis_title="Price",
            yaxis2=dict(title="Volume", overlaying="y", side="right",
                        showgrid=False, range=[0, hist["Volume"].max()*4]),
            xaxis_rangeslider_visible=False,
        )
    else:
        fig = go.Figure(layout=go.Layout(**CHART, title="No price data"))

    return cards, fig, info.get("description","")


# ── News ───────────────────────────────────────────────────────────────────────

@app.callback(Output("news-feed","children"),
              Input("news-refresh-btn","n_clicks"),
              Input("data-interval","n_intervals"))
@cache.memoize(timeout=300)
def update_news(_, __):
    articles = fetcher.get_news(max_items=25)
    if not articles:
        return html.P("No news – check RSS feeds.", className="text-muted")
    return [news_card(a) for a in articles]


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port  = int(os.environ.get("PORT", 8050))
    debug = os.environ.get("FLASK_ENV","development") == "development"
    app.run(debug=debug, host="0.0.0.0", port=port)
