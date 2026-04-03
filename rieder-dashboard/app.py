"""
Rieder-Style Global Macro Dashboard
────────────────────────────────────
Multi-tab Dash application covering:
  • Macro Overview   – KPI cards + key rate history
  • Yield Curves     – US / EU / Asia snapshot + historical spread
  • Factor Returns   – Fama-French 5-factor cumulative performance
  • Cross-Asset      – Sector rotation, global rates, asset returns
  • Company View     – Individual equity fundamentals + price history
  • Market News      – Live trading news from RSS / NewsAPI

Deploy:
  pip install -r requirements.txt
  python app.py                    # local dev
  gunicorn app:server              # production
"""

import os
import logging
from datetime import datetime

import dash
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import plotly.express as px
from dash import dcc, html, Input, Output, State
from flask_caching import Cache
from dotenv import load_dotenv

from data_fetcher import DataFetcher, SECTOR_ETFS, MACRO_SERIES

load_dotenv()
logging.basicConfig(level=logging.INFO)

# ── App setup ─────────────────────────────────────────────────────────────────
app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.CYBORG, dbc.icons.BOOTSTRAP],
    suppress_callback_exceptions=True,
    meta_tags=[{"name": "viewport", "content": "width=device-width, initial-scale=1"}],
    title="Global Macro Dashboard",
)
server = app.server  # Expose Flask server for gunicorn

cache = Cache(server, config={"CACHE_TYPE": "SimpleCache", "CACHE_DEFAULT_TIMEOUT": 300})

fetcher = DataFetcher()

# ── Colour palette ─────────────────────────────────────────────────────────────
COLORS = {
    "bg":        "#0d1117",
    "card":      "#161b22",
    "border":    "#30363d",
    "primary":   "#58a6ff",
    "green":     "#3fb950",
    "red":       "#f85149",
    "yellow":    "#d29922",
    "text":      "#e6edf3",
    "muted":     "#8b949e",
    "us":        "#58a6ff",
    "eu":        "#3fb950",
    "asia":      "#f78166",
}

CHART_LAYOUT = dict(
    paper_bgcolor=COLORS["card"],
    plot_bgcolor=COLORS["bg"],
    font=dict(color=COLORS["text"], family="Inter, Arial"),
    xaxis=dict(gridcolor=COLORS["border"], showgrid=True),
    yaxis=dict(gridcolor=COLORS["border"], showgrid=True),
    legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(size=11)),
    margin=dict(l=50, r=20, t=40, b=40),
)

# ── Helper components ──────────────────────────────────────────────────────────

def kpi_card(label: str, value, prev=None, unit: str = "%", decimals: int = 2):
    val_fmt  = f"{value:.{decimals}f}{unit}" if isinstance(value, (int, float)) else str(value)
    if isinstance(value, (int, float)) and prev is not None:
        delta = value - prev
        arrow = "▲" if delta >= 0 else "▼"
        clr   = COLORS["green"] if delta >= 0 else COLORS["red"]
        delta_el = html.Span(f" {arrow} {abs(delta):.2f}", style={"color": clr, "fontSize": "0.75rem"})
    else:
        delta_el = html.Span()

    return dbc.Card(
        dbc.CardBody([
            html.P(label, className="text-muted mb-1", style={"fontSize": "0.7rem", "letterSpacing": "0.08em", "textTransform": "uppercase"}),
            html.H4([val_fmt, delta_el], className="mb-0", style={"color": COLORS["text"]}),
        ]),
        style={"background": COLORS["card"], "border": f"1px solid {COLORS['border']}"},
        className="h-100",
    )


def section_header(title: str, subtitle: str = ""):
    return html.Div([
        html.H5(title, style={"color": COLORS["primary"], "marginBottom": "2px"}),
        html.P(subtitle, className="text-muted", style={"fontSize": "0.78rem", "marginBottom": "12px"}) if subtitle else html.Span(),
    ])


# ── Tab layouts ────────────────────────────────────────────────────────────────

def tab_macro():
    return dbc.Container([
        html.Br(),
        section_header("Macro Overview", "Key economic indicators – live from FRED"),
        html.Div(id="macro-kpi-row"),
        html.Br(),
        dbc.Row([
            dbc.Col([
                section_header("Rate History"),
                dbc.Select(
                    id="macro-series-select",
                    options=[
                        {"label": "Fed Funds Rate",  "value": "FEDFUNDS"},
                        {"label": "10Y Treasury",    "value": "DGS10"},
                        {"label": "2Y Treasury",     "value": "DGS2"},
                        {"label": "10Y-2Y Spread",   "value": "T10Y2Y"},
                        {"label": "CPI YoY",         "value": "CPIAUCSL"},
                        {"label": "Core PCE YoY",    "value": "PCEPILFE"},
                        {"label": "Unemployment",    "value": "UNRATE"},
                        {"label": "M2 Money Supply", "value": "M2SL"},
                    ],
                    value="DGS10",
                    style={"background": COLORS["card"], "color": COLORS["text"],
                           "border": f"1px solid {COLORS['border']}", "width": "220px"},
                ),
                dcc.Graph(id="macro-history-chart", style={"height": "340px"}),
            ], md=8),
            dbc.Col([
                section_header("Global Rates"),
                html.Div(id="global-rates-table"),
                html.Br(),
                section_header("Cross-Asset Returns (1M)"),
                dcc.Graph(id="cross-asset-chart", style={"height": "240px"}),
            ], md=4),
        ]),
    ], fluid=True)


def tab_yield_curves():
    return dbc.Container([
        html.Br(),
        section_header("Global Yield Curves", "Spot curves: US Treasuries · EU Bunds · Japan JGBs"),
        dbc.Row([
            dbc.Col(dcc.Graph(id="yc-snapshot", style={"height": "380px"}), md=7),
            dbc.Col([
                section_header("Historical Spreads"),
                dbc.Select(
                    id="yc-region",
                    options=[
                        {"label": "US Treasuries", "value": "US"},
                        {"label": "European Bunds", "value": "EU"},
                        {"label": "Japan JGBs",    "value": "Asia"},
                    ],
                    value="US",
                    style={"background": COLORS["card"], "color": COLORS["text"],
                           "border": f"1px solid {COLORS['border']}"},
                ),
                html.Br(),
                dcc.Graph(id="yc-history", style={"height": "300px"}),
            ], md=5),
        ]),
        html.Br(),
        section_header("Yield Curve Inversion Tracker"),
        dcc.Graph(id="yc-spread-history", style={"height": "240px"}),
    ], fluid=True)


def tab_factors():
    return dbc.Container([
        html.Br(),
        section_header("Fama-French 5-Factor Model Returns",
                       "Source: Ken French Data Library  |  Monthly rebalanced"),
        dbc.Row([
            dbc.Col([
                dbc.Select(
                    id="factor-lookback",
                    options=[
                        {"label": "1 Year",  "value": "1"},
                        {"label": "3 Years", "value": "3"},
                        {"label": "5 Years", "value": "5"},
                    ],
                    value="3",
                    style={"background": COLORS["card"], "color": COLORS["text"],
                           "border": f"1px solid {COLORS['border']}", "width": "160px"},
                ),
                html.Br(),
                dcc.Graph(id="factor-cumulative", style={"height": "380px"}),
            ], md=8),
            dbc.Col([
                section_header("Factor Definitions"),
                html.Div([
                    factor_legend("Mkt-RF", "Market excess return", COLORS["primary"]),
                    factor_legend("SMB",    "Small Minus Big (size)", "#c9d1d9"),
                    factor_legend("HML",    "High Minus Low (value)", COLORS["yellow"]),
                    factor_legend("RMW",    "Robust Minus Weak (profitability)", COLORS["green"]),
                    factor_legend("CMA",    "Conservative Minus Aggressive (investment)", COLORS["red"]),
                ]),
                html.Br(),
                section_header("Factor Correlation"),
                dcc.Graph(id="factor-correlation", style={"height": "260px"}),
            ], md=4),
        ]),
        html.Br(),
        section_header("Monthly Factor Returns Bar Chart"),
        dcc.Graph(id="factor-monthly", style={"height": "280px"}),
    ], fluid=True)


def factor_legend(name, desc, color):
    return html.Div([
        html.Span("■ ", style={"color": color, "fontSize": "1.1rem"}),
        html.Strong(name, style={"color": COLORS["text"]}),
        html.Span(f" — {desc}", style={"color": COLORS["muted"], "fontSize": "0.78rem"}),
    ], className="mb-2")


def tab_crossasset():
    return dbc.Container([
        html.Br(),
        section_header("Sector & Industry Rotation", "US equity sector ETF performance"),
        dbc.Row([
            dbc.Col([
                dbc.Select(
                    id="sector-period",
                    options=[
                        {"label": "1 Month",  "value": "1mo"},
                        {"label": "3 Months", "value": "3mo"},
                        {"label": "YTD",      "value": "ytd"},
                        {"label": "1 Year",   "value": "1y"},
                    ],
                    value="1mo",
                    style={"background": COLORS["card"], "color": COLORS["text"],
                           "border": f"1px solid {COLORS['border']}", "width": "160px"},
                ),
                html.Br(),
                dcc.Graph(id="sector-chart", style={"height": "340px"}),
            ], md=6),
            dbc.Col([
                section_header("Asset Class Returns"),
                dcc.Graph(id="asset-returns-chart", style={"height": "380px"}),
            ], md=6),
        ]),
        html.Br(),
        section_header("VIX Term Structure & Volatility"),
        dcc.Graph(id="vix-chart", style={"height": "240px"}),
    ], fluid=True)


def tab_company():
    return dbc.Container([
        html.Br(),
        section_header("Company Analysis", "Equity fundamentals + technicals"),
        dbc.Row([
            dbc.Col([
                dbc.InputGroup([
                    dbc.Input(id="company-ticker", placeholder="Enter ticker (e.g. AAPL)",
                              value="AAPL", debounce=True,
                              style={"background": COLORS["card"], "color": COLORS["text"],
                                     "border": f"1px solid {COLORS['border']}"}),
                    dbc.Button("Analyse", id="company-btn", color="primary", n_clicks=0),
                ], style={"maxWidth": "360px"}),
            ]),
        ]),
        html.Br(),
        html.Div(id="company-kpis"),
        html.Br(),
        dcc.Graph(id="company-price-chart", style={"height": "380px"}),
        html.Br(),
        html.P(id="company-description",
               style={"color": COLORS["muted"], "fontSize": "0.82rem", "lineHeight": "1.6"}),
    ], fluid=True)


def tab_news():
    return dbc.Container([
        html.Br(),
        section_header("Market News & Trading Intelligence", "Live from Reuters, FT, Bloomberg, WSJ RSS"),
        dbc.Row([
            dbc.Col([
                dbc.Button("Refresh News", id="news-refresh-btn",
                           color="outline-primary", size="sm", n_clicks=0),
            ]),
        ]),
        html.Br(),
        html.Div(id="news-feed"),
    ], fluid=True)


def news_card(article: dict):
    return dbc.Card(
        dbc.CardBody([
            dbc.Row([
                dbc.Col([
                    html.A(article["title"],
                           href=article.get("url", "#"),
                           target="_blank",
                           style={"color": COLORS["primary"], "fontWeight": "600",
                                  "textDecoration": "none", "fontSize": "0.92rem"}),
                    html.P(article.get("summary", ""),
                           className="mt-1 mb-1",
                           style={"color": COLORS["muted"], "fontSize": "0.78rem",
                                  "lineHeight": "1.5"}),
                    html.Small(
                        f"{article.get('source', '')}  •  {article.get('time', '')[:16]}",
                        style={"color": COLORS["border"]}
                    ),
                ]),
            ]),
        ]),
        className="mb-2",
        style={"background": COLORS["card"], "border": f"1px solid {COLORS['border']}"},
    )


# ── Main Layout ────────────────────────────────────────────────────────────────

NAVBAR = dbc.Navbar(
    dbc.Container([
        dbc.Row([
            dbc.Col(html.Div([
                html.Span("◈ ", style={"color": COLORS["primary"], "fontSize": "1.4rem"}),
                html.Span("GLOBAL MACRO", style={"fontWeight": "900", "letterSpacing": "0.15em",
                                                  "color": COLORS["text"], "fontSize": "1.1rem"}),
                html.Span("  DASHBOARD",  style={"fontWeight": "300", "letterSpacing": "0.1em",
                                                  "color": COLORS["muted"], "fontSize": "0.85rem"}),
            ])),
            dbc.Col(
                html.Div(id="live-clock",
                         style={"textAlign": "right", "color": COLORS["muted"],
                                "fontSize": "0.8rem", "fontFamily": "monospace"}),
                width="auto",
            ),
        ], align="center", className="w-100"),
    ], fluid=True),
    color=COLORS["card"],
    dark=True,
    style={"borderBottom": f"1px solid {COLORS['border']}"},
)

TABS = dbc.Tabs(
    [
        dbc.Tab(label="Macro",         tab_id="tab-macro"),
        dbc.Tab(label="Yield Curves",  tab_id="tab-yc"),
        dbc.Tab(label="Factors",       tab_id="tab-factors"),
        dbc.Tab(label="Cross-Asset",   tab_id="tab-crossasset"),
        dbc.Tab(label="Company",       tab_id="tab-company"),
        dbc.Tab(label="News",          tab_id="tab-news"),
    ],
    id="main-tabs",
    active_tab="tab-macro",
    style={"background": COLORS["card"], "borderBottom": f"1px solid {COLORS['border']}"},
)

app.layout = html.Div(
    [
        NAVBAR,
        TABS,
        html.Div(id="tab-content",
                 style={"minHeight": "80vh", "background": COLORS["bg"],
                        "padding": "0 0 40px 0"}),
        dcc.Interval(id="clock-interval",  interval=1_000,  n_intervals=0),
        dcc.Interval(id="data-interval",   interval=300_000, n_intervals=0),
    ],
    style={"background": COLORS["bg"], "minHeight": "100vh",
           "fontFamily": "Inter, system-ui, Arial"},
)


# ── Callbacks ──────────────────────────────────────────────────────────────────

@app.callback(Output("live-clock", "children"), Input("clock-interval", "n_intervals"))
def update_clock(_):
    now = datetime.utcnow()
    return f"UTC  {now.strftime('%Y-%m-%d  %H:%M:%S')}  |  Data may be delayed 15 min"


@app.callback(Output("tab-content", "children"),
              Input("main-tabs", "active_tab"))
def render_tab(active_tab):
    dispatch = {
        "tab-macro":      tab_macro,
        "tab-yc":         tab_yield_curves,
        "tab-factors":    tab_factors,
        "tab-crossasset": tab_crossasset,
        "tab-company":    tab_company,
        "tab-news":       tab_news,
    }
    return dispatch.get(active_tab, tab_macro)()


# ── Macro callbacks ────────────────────────────────────────────────────────────

@app.callback(Output("macro-kpi-row", "children"), Input("data-interval", "n_intervals"))
@cache.memoize(timeout=300)
def update_macro_kpis(_):
    kpis = fetcher.get_macro_kpis()
    cards = []
    units = {
        "Fed Funds Rate": "%", "CPI YoY": "%", "Core PCE YoY": "%",
        "Unemployment": "%", "10Y-2Y Spread": "bp", "US GDP Growth": "%",
        "M2 Growth": "%",
    }
    for label, data in kpis.items():
        unit = units.get(label, "")
        cards.append(dbc.Col(
            kpi_card(label, data["value"], data.get("prev"), unit=unit),
            xs=6, sm=4, md=3, lg=2, className="mb-3",
        ))
    return dbc.Row(cards)


@app.callback(Output("macro-history-chart", "figure"),
              Input("macro-series-select", "value"),
              Input("data-interval", "n_intervals"))
@cache.memoize(timeout=300)
def update_macro_chart(series_id, _):
    s = fetcher.get_macro_history(series_id, lookback_years=5)
    if s.empty:
        return go.Figure(layout=go.Layout(**CHART_LAYOUT, title="No FRED data (API key needed)"))

    # YoY transform for price-level series
    yoy_series = {"CPIAUCSL", "PCEPILFE", "M2SL"}
    if series_id in yoy_series:
        s = s.pct_change(12).dropna() * 100
        ylabel = "YoY %"
    else:
        ylabel = "%"

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=s.index, y=s.values,
        mode="lines", name=series_id,
        line=dict(color=COLORS["primary"], width=2),
        fill="tozeroy",
        fillcolor="rgba(88,166,255,0.08)",
    ))
    fig.update_layout(**CHART_LAYOUT,
                      title=dict(text=series_id, font=dict(size=13)),
                      yaxis_title=ylabel)
    return fig


@app.callback(Output("global-rates-table", "children"), Input("data-interval", "n_intervals"))
@cache.memoize(timeout=300)
def update_global_rates(_):
    df = fetcher.get_global_rates()
    if df.empty:
        return html.P("Rate data unavailable", className="text-muted")
    rows = []
    for idx, row in df.iterrows():
        rate = row.get("rate", 0)
        chg  = row.get("chg", 0)
        clr  = COLORS["green"] if chg >= 0 else COLORS["red"]
        rows.append(html.Tr([
            html.Td(idx, style={"color": COLORS["muted"], "fontSize": "0.8rem"}),
            html.Td(f"{rate:.3f}%", style={"color": COLORS["text"], "fontFamily": "monospace"}),
            html.Td(f"{'▲' if chg >= 0 else '▼'} {abs(chg):.3f}",
                    style={"color": clr, "fontFamily": "monospace", "fontSize": "0.8rem"}),
        ]))
    return dbc.Table(
        [html.Thead(html.Tr([html.Th("Region"), html.Th("Rate"), html.Th("Chg")])),
         html.Tbody(rows)],
        bordered=False, size="sm",
        style={"color": COLORS["text"], "background": "transparent"},
    )


@app.callback(Output("cross-asset-chart", "figure"), Input("data-interval", "n_intervals"))
@cache.memoize(timeout=300)
def update_cross_asset_chart(_):
    rets = fetcher.get_cross_asset_returns("1mo")
    if rets.empty:
        return go.Figure(layout=go.Layout(**CHART_LAYOUT))
    colors = [COLORS["green"] if v >= 0 else COLORS["red"] for v in rets.values]
    fig = go.Figure(go.Bar(
        x=rets.values, y=rets.index,
        orientation="h",
        marker_color=colors,
        text=[f"{v:+.1f}%" for v in rets.values],
        textposition="outside",
    ))
    fig.update_layout(**CHART_LAYOUT, title="1-Month Returns (%)", margin=dict(l=100, r=50, t=40, b=20))
    return fig


# ── Yield Curve callbacks ──────────────────────────────────────────────────────

@app.callback(Output("yc-snapshot", "figure"), Input("data-interval", "n_intervals"))
@cache.memoize(timeout=300)
def update_yc_snapshot(_):
    us_df = fetcher.get_us_yield_curve(lookback_days=5)
    eu_s  = fetcher.get_eu_yield_curve()
    asia_s = fetcher.get_asia_yield_curve()

    fig = go.Figure()

    # US – use last row
    if not us_df.empty:
        us_latest = us_df.iloc[-1].dropna()
        tenor_order = ["1M","3M","6M","1Y","2Y","3Y","5Y","7Y","10Y","20Y","30Y"]
        us_x = [t for t in tenor_order if t in us_latest.index]
        fig.add_trace(go.Scatter(
            x=us_x, y=[us_latest[t] for t in us_x],
            mode="lines+markers", name="US Treasury",
            line=dict(color=COLORS["us"], width=2.5),
            marker=dict(size=5),
        ))

    # EU
    if not eu_s.empty:
        fig.add_trace(go.Scatter(
            x=eu_s.index.tolist(), y=eu_s.values.tolist(),
            mode="lines+markers", name="EU Bund",
            line=dict(color=COLORS["eu"], width=2.5),
            marker=dict(size=5),
        ))

    # Asia
    if not asia_s.empty:
        fig.add_trace(go.Scatter(
            x=asia_s.index.tolist(), y=asia_s.values.tolist(),
            mode="lines+markers", name="Japan JGB",
            line=dict(color=COLORS["asia"], width=2.5),
            marker=dict(size=5),
        ))

    fig.update_layout(**CHART_LAYOUT,
                      title="Global Yield Curves – Current Snapshot",
                      yaxis_title="Yield (%)",
                      xaxis_title="Maturity")
    return fig


@app.callback(Output("yc-history", "figure"),
              Input("yc-region", "value"),
              Input("data-interval", "n_intervals"))
@cache.memoize(timeout=300)
def update_yc_history(region, _):
    df = fetcher.get_yield_curve_history(region, lookback_days=365)
    if df.empty:
        return go.Figure(layout=go.Layout(**CHART_LAYOUT))
    fig = go.Figure()
    clr_map = {"2Y": COLORS["primary"], "5Y": COLORS["yellow"],
               "10Y": COLORS["green"], "30Y": COLORS["red"]}
    for col in df.columns:
        fig.add_trace(go.Scatter(
            x=df.index, y=df[col], mode="lines",
            name=col, line=dict(color=clr_map.get(col, "#fff"), width=1.8),
        ))
    fig.update_layout(**CHART_LAYOUT, title=f"{region} Yield History (1Y)",
                      yaxis_title="Yield (%)")
    return fig


@app.callback(Output("yc-spread-history", "figure"), Input("data-interval", "n_intervals"))
@cache.memoize(timeout=300)
def update_yc_spread(_):
    df = fetcher.get_yield_curve_history("US", lookback_days=730)
    if df.empty or "10Y" not in df.columns or "2Y" not in df.columns:
        return go.Figure(layout=go.Layout(**CHART_LAYOUT))

    spread = (df["10Y"] - df["2Y"]).dropna()
    colors = [COLORS["green"] if v >= 0 else COLORS["red"] for v in spread.values]
    fig = go.Figure(go.Bar(x=spread.index, y=spread.values, marker_color=colors))
    fig.add_hline(y=0, line_dash="dot", line_color=COLORS["muted"])
    fig.update_layout(**CHART_LAYOUT,
                      title="US 10Y-2Y Yield Spread (Inversion Tracker)",
                      yaxis_title="Spread (%)")
    return fig


# ── Factor callbacks ───────────────────────────────────────────────────────────

@app.callback(Output("factor-cumulative", "figure"),
              Input("factor-lookback", "value"),
              Input("data-interval", "n_intervals"))
@cache.memoize(timeout=600)
def update_factor_cumulative(years, _):
    cum = fetcher.get_cumulative_factors(int(years))
    if cum.empty:
        return go.Figure(layout=go.Layout(**CHART_LAYOUT,
                                          title="Install pandas-datareader for factor data"))
    factor_colors = {
        "Mkt-RF": COLORS["primary"],
        "SMB":    "#c9d1d9",
        "HML":    COLORS["yellow"],
        "RMW":    COLORS["green"],
        "CMA":    COLORS["red"],
    }
    fig = go.Figure()
    for col in cum.columns:
        fig.add_trace(go.Scatter(
            x=cum.index, y=cum[col], mode="lines", name=col,
            line=dict(color=factor_colors.get(col, "#aaa"), width=2),
        ))
    fig.add_hline(y=1.0, line_dash="dot", line_color=COLORS["muted"])
    fig.update_layout(**CHART_LAYOUT,
                      title=f"Fama-French 5-Factor Cumulative Returns ({years}Y)",
                      yaxis_title="Growth of $1")
    return fig


@app.callback(Output("factor-correlation", "figure"),
              Input("factor-lookback", "value"),
              Input("data-interval", "n_intervals"))
@cache.memoize(timeout=600)
def update_factor_corr(years, _):
    ff = fetcher.get_factor_returns(int(years))
    if ff.empty:
        return go.Figure(layout=go.Layout(**CHART_LAYOUT))
    factors = [c for c in ff.columns if c != "RF"]
    corr = ff[factors].corr()
    fig = px.imshow(
        corr, text_auto=".2f",
        color_continuous_scale="RdBu_r", zmin=-1, zmax=1,
        aspect="auto",
    )
    fig.update_layout(**CHART_LAYOUT, title="Factor Correlation Matrix",
                      coloraxis_showscale=False, margin=dict(l=20, r=20, t=40, b=20))
    return fig


@app.callback(Output("factor-monthly", "figure"),
              Input("factor-lookback", "value"),
              Input("data-interval", "n_intervals"))
@cache.memoize(timeout=600)
def update_factor_monthly(years, _):
    ff = fetcher.get_factor_returns(int(years))
    if ff.empty:
        return go.Figure(layout=go.Layout(**CHART_LAYOUT))
    factors = [c for c in ff.columns if c != "RF"]
    last12 = ff[factors].tail(12) * 100

    fig = go.Figure()
    factor_colors = {
        "Mkt-RF": COLORS["primary"], "SMB": "#c9d1d9",
        "HML": COLORS["yellow"],     "RMW": COLORS["green"],
        "CMA":    COLORS["red"],
    }
    for col in last12.columns:
        fig.add_trace(go.Bar(
            x=last12.index.strftime("%b %Y"),
            y=last12[col], name=col,
            marker_color=factor_colors.get(col, "#aaa"),
        ))
    fig.update_layout(**CHART_LAYOUT,
                      title="Monthly Factor Returns – Last 12 Months (%)",
                      barmode="group", yaxis_title="%")
    return fig


# ── Cross-Asset callbacks ──────────────────────────────────────────────────────

@app.callback(Output("sector-chart", "figure"),
              Input("sector-period", "value"),
              Input("data-interval", "n_intervals"))
@cache.memoize(timeout=300)
def update_sectors(period, _):
    perf = fetcher.get_sector_performance(period)
    if perf.empty:
        return go.Figure(layout=go.Layout(**CHART_LAYOUT))
    colors = [COLORS["green"] if v >= 0 else COLORS["red"] for v in perf.values]
    fig = go.Figure(go.Bar(
        x=perf.index, y=perf.values,
        marker_color=colors,
        text=[f"{v:+.1f}%" for v in perf.values],
        textposition="outside",
    ))
    fig.update_layout(**CHART_LAYOUT,
                      title=f"US Sector Returns ({period})",
                      yaxis_title="%",
                      xaxis_tickangle=-30)
    return fig


@app.callback(Output("asset-returns-chart", "figure"),
              Input("data-interval", "n_intervals"))
@cache.memoize(timeout=300)
def update_asset_returns(_):
    periods = {"1W": "5d", "1M": "1mo", "3M": "3mo", "YTD": "ytd"}
    assets  = {
        "S&P 500": "^GSPC", "NASDAQ": "^IXIC", "Gold": "GC=F",
        "Oil": "CL=F", "DXY": "DX-Y.NYB", "Agg Bonds": "AGG",
    }
    fig = go.Figure()
    clr_seq = [COLORS["primary"], COLORS["green"], COLORS["yellow"],
               COLORS["red"], "#c9d1d9", "#7ee787"]
    for (period_label, yf_period), clr in zip(periods.items(), clr_seq * 4):
        vals, labels = [], []
        for label, tkr in assets.items():
            try:
                hist = __import__("yfinance").Ticker(tkr).history(period=yf_period)["Close"].dropna()
                if len(hist) >= 2:
                    vals.append(round((hist.iloc[-1] / hist.iloc[0] - 1) * 100, 2))
                    labels.append(label)
            except Exception:
                pass
        if vals:
            fig.add_trace(go.Bar(x=labels, y=vals, name=period_label,
                                 marker_color=clr))
    fig.update_layout(**CHART_LAYOUT, barmode="group",
                      title="Multi-Period Asset Returns",
                      yaxis_title="%", xaxis_tickangle=-20)
    return fig


@app.callback(Output("vix-chart", "figure"), Input("data-interval", "n_intervals"))
@cache.memoize(timeout=120)
def update_vix(_):
    import yfinance as yf
    hist = yf.Ticker("^VIX").history(period="1y")["Close"].dropna()
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=hist.index, y=hist.values,
        mode="lines", name="VIX",
        line=dict(color=COLORS["yellow"], width=2),
        fill="tozeroy",
        fillcolor="rgba(210,153,34,0.08)",
    ))
    fig.add_hline(y=20, line_dash="dot", line_color=COLORS["muted"],
                  annotation_text="20 (elevated)", annotation_position="top right")
    fig.add_hline(y=30, line_dash="dot", line_color=COLORS["red"],
                  annotation_text="30 (stress)", annotation_position="top right")
    fig.update_layout(**CHART_LAYOUT, title="VIX – 1 Year", yaxis_title="VIX Level")
    return fig


# ── Company callbacks ──────────────────────────────────────────────────────────

@app.callback(
    Output("company-kpis", "children"),
    Output("company-price-chart", "figure"),
    Output("company-description", "children"),
    Input("company-btn", "n_clicks"),
    State("company-ticker", "value"),
    prevent_initial_call=False,
)
def update_company(_, ticker):
    ticker = (ticker or "AAPL").upper().strip()
    data = fetcher.get_company_data(ticker)
    info = data["info"]
    hist = data["history"]

    # KPI cards
    def fmt(v, mult=1, suffix="", decimals=2):
        if v is None:
            return "N/A"
        v *= mult
        if abs(v) >= 1e12:
            return f"{v/1e12:.1f}T{suffix}"
        if abs(v) >= 1e9:
            return f"{v/1e9:.1f}B{suffix}"
        if abs(v) >= 1e6:
            return f"{v/1e6:.1f}M{suffix}"
        return f"{v:.{decimals}f}{suffix}"

    kpi_items = [
        ("Sector",      info.get("sector", "N/A"),      None),
        ("Market Cap",  fmt(info.get("market_cap"), suffix=""), None),
        ("P/E Ratio",   fmt(info.get("pe_ratio"), suffix="x"), None),
        ("P/B Ratio",   fmt(info.get("pb_ratio"), suffix="x"), None),
        ("ROE",         fmt(info.get("roe"), mult=100, suffix="%") if info.get("roe") else "N/A", None),
        ("Debt/Equity", fmt(info.get("debt_equity"), suffix="%") if info.get("debt_equity") else "N/A", None),
        ("Div Yield",   fmt(info.get("div_yield"), mult=100, suffix="%") if info.get("div_yield") else "N/A", None),
        ("Beta",        fmt(info.get("beta"), suffix=""), None),
    ]

    cards = dbc.Row([
        dbc.Col(html.Div([
            html.H2(info.get("name", ticker), style={"color": COLORS["text"]}),
            html.Small(info.get("industry", ""), style={"color": COLORS["muted"]}),
        ]), width=12, className="mb-3"),
    ] + [
        dbc.Col(
            html.Div([
                html.P(lbl, className="text-muted mb-0", style={"fontSize": "0.7rem", "textTransform": "uppercase", "letterSpacing": "0.08em"}),
                html.H5(val, className="mb-0", style={"color": COLORS["text"]}),
            ], style={"background": COLORS["card"], "border": f"1px solid {COLORS['border']}",
                      "borderRadius": "6px", "padding": "10px 14px"}),
            xs=6, sm=4, md=3, lg=2, className="mb-2",
        )
        for lbl, val, _ in kpi_items
    ])

    # Price chart with volume
    if not hist.empty:
        fig = go.Figure()
        fig.add_trace(go.Candlestick(
            x=hist.index,
            open=hist["Open"], high=hist["High"],
            low=hist["Low"],   close=hist["Close"],
            name=ticker,
            increasing_line_color=COLORS["green"],
            decreasing_line_color=COLORS["red"],
        ))
        # Volume bars on secondary y-axis
        fig.add_trace(go.Bar(
            x=hist.index, y=hist["Volume"],
            name="Volume", yaxis="y2",
            marker_color="rgba(88,166,255,0.2)",
        ))
        fig.update_layout(
            **CHART_LAYOUT,
            title=f"{ticker} – 1 Year Price (OHLC)",
            yaxis_title="Price",
            yaxis2=dict(title="Volume", overlaying="y", side="right",
                        showgrid=False, range=[0, hist["Volume"].max() * 4]),
            xaxis_rangeslider_visible=False,
        )
    else:
        fig = go.Figure(layout=go.Layout(**CHART_LAYOUT, title="No price data"))

    description = info.get("description", "")

    return cards, fig, description


# ── News callbacks ─────────────────────────────────────────────────────────────

@app.callback(Output("news-feed", "children"),
              Input("news-refresh-btn", "n_clicks"),
              Input("data-interval", "n_intervals"))
@cache.memoize(timeout=300)
def update_news(_, __):
    articles = fetcher.get_news(max_items=25)
    if not articles:
        return html.P(
            "No news available. Add a NEWS_API_KEY in .env for richer results.",
            className="text-muted"
        )
    return [news_card(a) for a in articles]


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8050))
    debug = os.environ.get("FLASK_ENV", "development") == "development"
    app.run(debug=debug, host="0.0.0.0", port=port)
