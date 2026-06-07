import yfinance as yf
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.lines import Line2D

def compute_hurst_exponent(price_series):
    """
    Computes the Hurst Exponent (H) using Rescaled Range (R/S) Analysis.
    Captures Mandelbrot's fractal memory profile of the index.
    """
    prices = np.asarray(price_series)
    returns = np.log(prices[1:] / prices[:-1])
    N = len(returns)

    lags = [int(l) for l in np.logspace(1, np.log10(N / 2), num=10)]
    lags = sorted(list(set(lags)))

    RS_dict = {}
    for lag in lags:
        num_chunks = N // lag
        rs_list = []
        for i in range(num_chunks):
            chunk = returns[i * lag:(i + 1) * lag]
            mean_adj = chunk - np.mean(chunk)
            cum_sum = np.cumsum(mean_adj)
            R = np.max(cum_sum) - np.min(cum_sum)
            S = np.std(chunk)
            if S > 0:
                rs_list.append(R / S)
        if rs_list:
            RS_dict[lag] = np.mean(rs_list)

    if len(RS_dict) < 2:
        return 0.5, {}, None

    x = np.log(list(RS_dict.keys()))
    y = np.log(list(RS_dict.values()))
    poly = np.polyfit(x, y, 1)
    H = np.clip(poly[0], 0.05, 0.95)
    return H, RS_dict, poly


def run_fbm_simulation(S0, H, mu_annual, sigma_annual, years=10, paths=1000):
    """
    Simulates Fractional Brownian Motion paths over a multi-year horizon.
    """
    trading_days = int(years * 252)
    dt = 1 / 252

    # Auto-covariance for fractional Gaussian noise
    covariance = np.zeros((trading_days, trading_days))
    for i in range(trading_days):
        for j in range(trading_days):
            covariance[i, j] = 0.5 * (
                abs(i - j + 1) ** (2 * H)
                - 2 * abs(i - j) ** (2 * H)
                + abs(i - j - 1) ** (2 * H)
            )

    L = np.linalg.cholesky(covariance + np.eye(trading_days) * 1e-6)

    forecast_matrix = np.zeros((trading_days + 1, paths))
    forecast_matrix[0, :] = S0

    daily_drift = (mu_annual - 0.5 * (sigma_annual ** 2)) * dt
    daily_diffusion = sigma_annual * (dt ** H)

    for p in range(paths):
        z = np.random.normal(0, 1, trading_days)
        fgn = np.dot(L, z)
        log_increments = daily_drift + daily_diffusion * fgn
        forecast_matrix[1:, p] = S0 * np.exp(np.cumsum(log_increments))

    return forecast_matrix


def plot_rs_analysis(RS_dict, poly, H):
    """Log-log R/S plot with the fitted Hurst slope line."""
    lags = np.array(sorted(RS_dict.keys()))
    rs_vals = np.array([RS_dict[l] for l in lags])

    fit_x = np.linspace(np.log(lags[0]), np.log(lags[-1]), 100)
    fit_y = np.polyval(poly, fit_x)

    fig, ax = plt.subplots(figsize=(7, 5))
    ax.scatter(np.log(lags), np.log(rs_vals), color="#2563EB", zorder=3, s=60, label="R/S observations")
    ax.plot(fit_x, fit_y, color="#DC2626", linewidth=1.8,
            label=f"OLS fit  H = {H:.4f}")

    # Reference lines for H = 0.5 (random walk)
    ref_y = 0.5 * fit_x + (fit_y[0] - 0.5 * fit_x[0])
    ax.plot(fit_x, ref_y, color="gray", linewidth=1.2, linestyle="--", label="H = 0.50 (random walk)")

    ax.set_xlabel("log(lag)", fontsize=11)
    ax.set_ylabel("log(R/S)", fontsize=11)
    ax.set_title("Mandelbrot R/S Analysis — Hurst Exponent Estimation", fontsize=12, fontweight="bold")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    return fig


def plot_simulation_fan(sim_paths, S0, start_year=2017, years=10):
    """Fan chart of fBm paths with percentile bands and sample trajectories."""
    total_steps = sim_paths.shape[0]  # trading_days + 1
    t_days = np.arange(total_steps)
    t_years = start_year + t_days / 252

    p5  = np.percentile(sim_paths, 5,  axis=1)
    p10 = np.percentile(sim_paths, 10, axis=1)
    p25 = np.percentile(sim_paths, 25, axis=1)
    p50 = np.percentile(sim_paths, 50, axis=1)
    p75 = np.percentile(sim_paths, 75, axis=1)
    p90 = np.percentile(sim_paths, 90, axis=1)
    p95 = np.percentile(sim_paths, 95, axis=1)

    fig, ax = plt.subplots(figsize=(11, 6))

    ax.fill_between(t_years, p5,  p95, alpha=0.12, color="#2563EB", label="5–95th pctl")
    ax.fill_between(t_years, p10, p90, alpha=0.18, color="#2563EB", label="10–90th pctl")
    ax.fill_between(t_years, p25, p75, alpha=0.28, color="#2563EB", label="25–75th pctl")
    ax.plot(t_years, p50, color="#1D4ED8", linewidth=2.2, label="Median (50th pctl)")

    # Sample paths
    rng = np.random.default_rng(42)
    sample_idx = rng.choice(sim_paths.shape[1], size=12, replace=False)
    for idx in sample_idx:
        ax.plot(t_years, sim_paths[:, idx], color="#93C5FD", linewidth=0.6, alpha=0.55)

    ax.axhline(S0, color="#6B7280", linewidth=1, linestyle=":", label=f"Base level ({S0:,.0f})")
    ax.set_xlabel("Year", fontsize=11)
    ax.set_ylabel("Nifty 50 Index Level", fontsize=11)
    ax.set_title(
        f"Nifty 50 — fBm Monte Carlo Fan Chart  ({start_year}–{start_year + years})\n"
        f"{sim_paths.shape[1]:,} paths  |  trained on 2016–2026",
        fontsize=12, fontweight="bold"
    )
    ax.legend(fontsize=9, loc="upper left")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    return fig


def plot_historical_and_forecast(historical_close, sim_paths, train_end_year=2016, years=10):
    """Historical price series stitched to the forecast fan."""
    n_hist = len(historical_close)
    hist_dates = pd.date_range(start="2016-01-01", periods=n_hist, freq="B")

    total_steps = sim_paths.shape[0]
    fcast_dates = pd.bdate_range(start=f"{train_end_year}-06-07", periods=total_steps)

    p10 = np.percentile(sim_paths, 10, axis=1)
    p50 = np.percentile(sim_paths, 50, axis=1)
    p90 = np.percentile(sim_paths, 90, axis=1)

    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(hist_dates, historical_close, color="#111827", linewidth=1.5, label="Historical (2012–2016)")
    ax.axvline(fcast_dates[0], color="#6B7280", linewidth=1, linestyle="--")

    ax.fill_between(fcast_dates, p10, p90, alpha=0.22, color="#F59E0B", label="10–90th pctl forecast")
    ax.plot(fcast_dates, p50, color="#D97706", linewidth=2, label="Median forecast")

    ax.set_xlabel("Date", fontsize=11)
    ax.set_ylabel("Nifty 50 Index Level", fontsize=11)
    ax.set_title("Nifty 50 — Historical vs Fractional Brownian Motion Forecast", fontsize=12, fontweight="bold")
    ax.legend(fontsize=9)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    return fig


def execute_nifty_fractal_pipeline():
    print("Fetching Nifty 50 data (2016–2026) via yfinance...")
    nifty_df = yf.download("^NSEI", start="2016-01-01", end="2026-06-07", progress=False)

    if nifty_df.empty:
        raise ValueError("Data retrieval failed. Verify connection or ticker symbol.")

    if isinstance(nifty_df.columns, pd.MultiIndex):
        nifty_df.columns = nifty_df.columns.get_level_values(0)

    close_series = nifty_df["Close"].dropna().values

    log_ret = np.log(close_series[1:] / close_series[:-1])
    mu_annual    = np.mean(log_ret) * 252
    sigma_annual = np.std(log_ret)  * np.sqrt(252)

    H, RS_dict, poly = compute_hurst_exponent(close_series)
    S0 = close_series[-1]

    print("\n--- ESTIMATED MODEL PARAMETERS (2016–2026) ---")
    print(f"Base Training Close (End of 2016):  {S0:,.2f}")
    print(f"Annualized Drift (mu):              {mu_annual:.2%}")
    print(f"Annualized Volatility (sigma):      {sigma_annual:.2%}")
    print(f"Mandelbrot Hurst Exponent (H):      {H:.4f}")

    if H > 0.55:
        regime = "trend-reinforcing persistence (H > 0.5)"
    elif H < 0.45:
        regime = "mean-reverting anti-persistence (H < 0.5)"
    else:
        regime = "near-random walk (H ≈ 0.5)"
    print(f"Market regime: {regime}")

    print("\nSimulating 10-year forward paths (2 000 paths)…  [may take ~30 s]")
    sim_paths = run_fbm_simulation(S0, H, mu_annual, sigma_annual, years=10, paths=2000)

    # --- Tabular summary ---
    print("\n--- 10-YEAR FRACTAL FORECAST DISTRIBUTION ---")
    print(f"{'Year':<6}{'Bear (10th)':<20}{'Median (50th)':<20}{'Bull (90th)':<20}")
    print("-" * 66)
    for yr in range(1, 11):
        dist = sim_paths[yr * 252, :]
        print(f"Yr {yr:<3}{np.percentile(dist, 10):<20,.0f}"
              f"{np.percentile(dist, 50):<20,.0f}"
              f"{np.percentile(dist, 90):<20,.0f}")

    # --- Plots ---
    print("\nRendering plots…")

    fig1 = plot_rs_analysis(RS_dict, poly, H)
    fig2 = plot_simulation_fan(sim_paths, S0, start_year=2026, years=10)
    fig3 = plot_historical_and_forecast(close_series, sim_paths, train_end_year=2026, years=10)

    fig1.savefig("mandelbrot_rs_analysis.png", dpi=150)
    fig2.savefig("mandelbrot_fbm_fan.png",     dpi=150)
    fig3.savefig("mandelbrot_historical_forecast.png", dpi=150)
    print("Saved: mandelbrot_rs_analysis.png, mandelbrot_fbm_fan.png, mandelbrot_historical_forecast.png")

    plt.show()


if __name__ == "__main__":
    execute_nifty_fractal_pipeline()
