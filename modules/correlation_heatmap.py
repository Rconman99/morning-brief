"""Generate interactive HTML correlation heatmap for portfolio + watchlist tickers."""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import logging
from datetime import datetime

import pandas as pd
import numpy as np

from lib.data_envelope import create_envelope, save_envelope
from lib.api import yahoo_finance_price_history

logger = logging.getLogger(__name__)


def setup_logging():
    """Call this ONLY inside main(). NEVER at module level."""
    log_dir = PROJECT_ROOT / "data" / "outputs"
    log_dir.mkdir(parents=True, exist_ok=True)
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            handlers=[
                logging.StreamHandler(),
                logging.FileHandler(log_dir / "run.log", mode="a")
            ]
        )


def load_config():
    """Load portfolio and watchlist configs."""
    portfolio_path = PROJECT_ROOT / "config" / "portfolio.json"
    watchlist_path = PROJECT_ROOT / "config" / "watchlist.json"
    
    portfolio = json.loads(portfolio_path.read_text()) if portfolio_path.exists() else {"holdings": []}
    watchlist = json.loads(watchlist_path.read_text()) if watchlist_path.exists() else {"tickers": []}
    
    return portfolio, watchlist


def get_all_tickers(portfolio, watchlist):
    """Get union of portfolio holdings and watchlist tickers."""
    portfolio_tickers = {h["ticker"] for h in portfolio.get("holdings", [])}
    watchlist_tickers = set(watchlist.get("tickers", []))
    all_tickers = sorted(list(portfolio_tickers | watchlist_tickers))
    return all_tickers, portfolio_tickers


def download_prices(tickers: list, period: str = "6mo") -> pd.DataFrame:
    """Download 6 months of daily close prices for all tickers. Returns DataFrame with ticker columns."""
    dfs = []
    for ticker in tickers:
        logger.info("Downloading price history for %s", ticker)
        hist = yahoo_finance_price_history(ticker, period=period)
        if hist.empty:
            logger.warning("Failed to get prices for %s", ticker)
            continue
        
        # Extract Close column and rename to ticker
        if isinstance(hist, pd.DataFrame):
            if "Close" in hist.columns:
                df_single = hist[["Close"]].rename(columns={"Close": ticker})
            else:
                # hist is a DataFrame with ticker as column name already
                df_single = hist.copy()
                df_single.columns = [ticker]
        else:
            # hist is a Series
            df_single = pd.DataFrame({ticker: hist})
        
        dfs.append(df_single)
    
    if not dfs:
        logger.error("No price data retrieved for any ticker")
        return pd.DataFrame()
    
    # Combine into single DataFrame, aligned by date
    df = pd.concat(dfs, axis=1)
    df = df.dropna(how='all')  # Remove rows where all prices are NaN
    logger.info("Price data shape: %s", df.shape)
    return df


def calculate_correlations(prices: pd.DataFrame) -> pd.DataFrame:
    """Calculate pairwise correlation matrix from price data."""
    if prices.empty or len(prices) < 2:
        logger.warning("Insufficient price data for correlation")
        return pd.DataFrame()
    
    # Calculate daily returns
    returns = prices.pct_change().dropna()
    if returns.empty:
        logger.warning("No returns calculated")
        return pd.DataFrame()
    
    # Calculate correlation matrix
    corr_matrix = returns.corr()
    logger.info("Correlation matrix shape: %s", corr_matrix.shape)
    return corr_matrix


def find_high_correlations(corr_matrix: pd.DataFrame, threshold: float = 0.70) -> list:
    """Find pairs with correlations above threshold, excluding self-correlations."""
    high_corrs = []
    tickers = list(corr_matrix.columns)
    
    for i, ticker1 in enumerate(tickers):
        for ticker2 in tickers[i+1:]:
            corr_val = corr_matrix.loc[ticker1, ticker2]
            if not pd.isna(corr_val) and corr_val >= threshold:
                high_corrs.append({
                    "pair": f"{ticker1}_{ticker2}",
                    "ticker1": ticker1,
                    "ticker2": ticker2,
                    "corr": round(corr_val, 3),
                    "trend": "stable"  # TODO: compute rolling 30-day trend
                })
    
    high_corrs.sort(key=lambda x: x["corr"], reverse=True)
    logger.info("Found %d high correlations (> %.2f)", len(high_corrs), threshold)
    return high_corrs


def find_risk_clusters(corr_matrix: pd.DataFrame, threshold: float = 0.75) -> list:
    """Identify groups of 3+ tickers all correlated > threshold."""
    clusters = []
    tickers = list(corr_matrix.columns)
    
    # Simple greedy clustering: for each ticker, find all tickers correlated > threshold
    visited = set()
    for i, ticker in enumerate(tickers):
        if ticker in visited:
            continue
        
        cluster = {ticker}
        for other in tickers[i+1:]:
            corr_val = corr_matrix.loc[ticker, other]
            if not pd.isna(corr_val) and corr_val >= threshold:
                cluster.add(other)
        
        # Only count as cluster if 3+ tickers
        if len(cluster) >= 3:
            cluster_list = sorted(list(cluster))
            # Check if this cluster is a superset of any already found
            is_new = True
            for existing in clusters:
                if set(existing["tickers"]) == set(cluster_list):
                    is_new = False
                    break
            
            if is_new:
                # Calculate average correlation within cluster
                cluster_corrs = []
                for j, t1 in enumerate(cluster_list):
                    for t2 in cluster_list[j+1:]:
                        corr_val = corr_matrix.loc[t1, t2]
                        if not pd.isna(corr_val):
                            cluster_corrs.append(corr_val)
                
                avg_corr = round(np.mean(cluster_corrs), 3) if cluster_corrs else None
                clusters.append({
                    "tickers": cluster_list,
                    "avg_corr": avg_corr,
                    "size": len(cluster_list)
                })
                visited.update(cluster)
    
    logger.info("Found %d risk clusters (corr > %.2f)", len(clusters), threshold)
    return clusters


def flatten_correlation_matrix(corr_matrix: pd.DataFrame) -> dict:
    """Flatten correlation matrix to dict format."""
    flat = {}
    tickers = list(corr_matrix.columns)
    
    for i, ticker1 in enumerate(tickers):
        for ticker2 in tickers[i:]:
            corr_val = corr_matrix.loc[ticker1, ticker2]
            key = f"{ticker1}_{ticker2}"
            flat[key] = round(corr_val, 3) if not pd.isna(corr_val) else None
    
    return flat


def generate_html(corr_matrix: pd.DataFrame, high_corrs: list, risk_clusters: list, 
                  portfolio_tickers: set, output_path: Path) -> None:
    """Generate self-contained interactive HTML heatmap."""
    if corr_matrix.empty:
        logger.warning("Cannot generate HTML: empty correlation matrix")
        return
    
    tickers = list(corr_matrix.columns)
    now = datetime.now().astimezone()
    
    # Build HTML
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Correlation Heatmap - {now.strftime('%Y-%m-%d')}</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #1e1e2e 0%, #2d2d44 100%);
            color: #e0e0e0;
            padding: 20px;
        }}
        
        .container {{
            max-width: 1600px;
            margin: 0 auto;
        }}
        
        .header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            padding: 30px;
            border-radius: 8px;
            margin-bottom: 30px;
            box-shadow: 0 4px 15px rgba(0, 0, 0, 0.3);
        }}
        
        .header h1 {{
            font-size: 32px;
            margin-bottom: 10px;
            font-weight: 700;
        }}
        
        .header p {{
            font-size: 14px;
            opacity: 0.9;
        }}
        
        .legend {{
            background: #2d2d44;
            padding: 20px;
            border-radius: 8px;
            margin-bottom: 20px;
            border: 1px solid #444;
        }}
        
        .legend h3 {{
            margin-bottom: 15px;
            font-size: 16px;
        }}
        
        .legend-items {{
            display: flex;
            gap: 30px;
            flex-wrap: wrap;
        }}
        
        .legend-item {{
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        
        .legend-color {{
            width: 30px;
            height: 30px;
            border-radius: 4px;
            border: 1px solid #555;
        }}
        
        .heatmap-section {{
            background: #2d2d44;
            padding: 20px;
            border-radius: 8px;
            margin-bottom: 30px;
            border: 1px solid #444;
            overflow-x: auto;
        }}
        
        .heatmap-section h2 {{
            font-size: 20px;
            margin-bottom: 20px;
            color: #667eea;
        }}
        
        .heatmap-table {{
            border-collapse: collapse;
            font-size: 13px;
            min-width: 100%;
        }}
        
        .heatmap-table th,
        .heatmap-table td {{
            border: 1px solid #444;
            padding: 8px;
            text-align: center;
            width: 50px;
            height: 50px;
            position: relative;
        }}
        
        .heatmap-table th {{
            background: #3d3d54;
            font-weight: 600;
            color: #667eea;
            padding: 12px 8px;
        }}
        
        .heatmap-table td {{
            cursor: pointer;
            transition: all 0.2s ease;
            font-weight: 500;
        }}
        
        .heatmap-table td:hover {{
            transform: scale(1.1);
            box-shadow: inset 0 0 10px rgba(255, 255, 255, 0.3);
            z-index: 10;
        }}
        
        .cell-diagonal {{
            background-color: #555;
            color: #999;
        }}
        
        .cell-high-positive {{
            background-color: #dc2626;
            color: #fff;
        }}
        
        .cell-positive {{
            background-color: #f97316;
            color: #fff;
        }}
        
        .cell-neutral {{
            background-color: #fff;
            color: #333;
        }}
        
        .cell-negative {{
            background-color: #3b82f6;
            color: #fff;
        }}
        
        .cell-high-negative {{
            background-color: #1e40af;
            color: #fff;
        }}
        
        .ticker-label {{
            font-weight: 600;
            color: #667eea;
        }}
        
        .ticker-label.portfolio {{
            color: #10b981;
            font-weight: 700;
        }}
        
        .tooltip {{
            display: none;
            position: absolute;
            background: rgba(0, 0, 0, 0.9);
            color: #fff;
            padding: 8px 12px;
            border-radius: 4px;
            font-size: 12px;
            white-space: nowrap;
            z-index: 1000;
            top: -30px;
            left: 50%;
            transform: translateX(-50%);
            pointer-events: none;
        }}
        
        .heatmap-table td:hover .tooltip {{
            display: block;
        }}
        
        .stats-section {{
            background: #2d2d44;
            padding: 20px;
            border-radius: 8px;
            margin-bottom: 30px;
            border: 1px solid #444;
        }}
        
        .stats-section h2 {{
            font-size: 20px;
            margin-bottom: 15px;
            color: #667eea;
        }}
        
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 15px;
        }}
        
        .stat-box {{
            background: #1e1e2e;
            padding: 15px;
            border-radius: 6px;
            border-left: 4px solid #667eea;
        }}
        
        .stat-box h3 {{
            font-size: 14px;
            color: #999;
            margin-bottom: 8px;
            text-transform: uppercase;
            letter-spacing: 1px;
        }}
        
        .stat-box p {{
            font-size: 18px;
            color: #667eea;
            font-weight: 600;
        }}
        
        .high-corr-item {{
            background: #1e1e2e;
            padding: 12px;
            border-radius: 6px;
            margin-bottom: 10px;
            border-left: 4px solid #f97316;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        
        .high-corr-pair {{
            font-weight: 600;
            color: #e0e0e0;
        }}
        
        .high-corr-value {{
            color: #f97316;
            font-weight: 700;
            font-size: 16px;
        }}
        
        .risk-cluster-item {{
            background: #1e1e2e;
            padding: 12px;
            border-radius: 6px;
            margin-bottom: 10px;
            border-left: 4px solid #dc2626;
        }}
        
        .cluster-tickers {{
            font-weight: 600;
            color: #e0e0e0;
            margin-bottom: 5px;
        }}
        
        .cluster-avg {{
            font-size: 13px;
            color: #999;
        }}
        
        .empty-state {{
            text-align: center;
            padding: 40px;
            color: #999;
        }}
        
        @media (max-width: 768px) {{
            .heatmap-table th,
            .heatmap-table td {{
                width: 40px;
                height: 40px;
                padding: 4px;
                font-size: 11px;
            }}
            
            .header h1 {{
                font-size: 24px;
            }}
            
            .stats-grid {{
                grid-template-columns: 1fr;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Portfolio Correlation Heatmap</h1>
            <p>Generated {now.strftime('%Y-%m-%d %H:%M:%S %Z')} | 6-Month Daily Returns</p>
        </div>
        
        <div class="legend">
            <h3>Color Legend (Pearson Correlation)</h3>
            <div class="legend-items">
                <div class="legend-item">
                    <div class="legend-color" style="background-color: #dc2626;"></div>
                    <span>Strong Positive (0.80–1.00)</span>
                </div>
                <div class="legend-item">
                    <div class="legend-color" style="background-color: #f97316;"></div>
                    <span>Moderate Positive (0.50–0.79)</span>
                </div>
                <div class="legend-item">
                    <div class="legend-color" style="background-color: #fff;"></div>
                    <span>Neutral (-0.30–0.49)</span>
                </div>
                <div class="legend-item">
                    <div class="legend-color" style="background-color: #3b82f6;"></div>
                    <span>Moderate Negative (-0.79–-0.31)</span>
                </div>
                <div class="legend-item">
                    <div class="legend-color" style="background-color: #1e40af;"></div>
                    <span>Strong Negative (-1.00–-0.80)</span>
                </div>
            </div>
        </div>
        
        <div class="heatmap-section">
            <h2>Correlation Matrix</h2>
            <table class="heatmap-table">
                <thead>
                    <tr>
                        <th></th>
"""
    
    # Add column headers
    for ticker in tickers:
        css_class = "ticker-label portfolio" if ticker in portfolio_tickers else "ticker-label"
        html += f'                        <th><span class="{css_class}">{ticker}</span></th>\n'
    
    html += """                    </tr>
                </thead>
                <tbody>
"""
    
    # Add rows
    for i, ticker1 in enumerate(tickers):
        css_class = "ticker-label portfolio" if ticker1 in portfolio_tickers else "ticker-label"
        html += f'                    <tr><th><span class="{css_class}">{ticker1}</span></th>\n'
        
        for ticker2 in tickers:
            corr_val = corr_matrix.loc[ticker1, ticker2]
            
            if ticker1 == ticker2:
                cell_class = "cell-diagonal"
                display_val = "1.0"
            elif pd.isna(corr_val):
                cell_class = "cell-neutral"
                display_val = "N/A"
            else:
                if corr_val >= 0.80:
                    cell_class = "cell-high-positive"
                elif corr_val >= 0.50:
                    cell_class = "cell-positive"
                elif corr_val >= -0.30:
                    cell_class = "cell-neutral"
                elif corr_val >= -0.80:
                    cell_class = "cell-negative"
                else:
                    cell_class = "cell-high-negative"
                display_val = f"{corr_val:.2f}"
            
            html += f'                        <td class="{cell_class}">{display_val}<div class="tooltip">{display_val}</div></td>\n'
        
        html += "                    </tr>\n"
    
    html += """                </tbody>
            </table>
        </div>
"""
    
    # High correlations section
    if high_corrs:
        html += """        <div class="stats-section">
            <h2>High Correlations (> 0.70)</h2>
"""
        for item in high_corrs[:15]:  # Show top 15
            html += f"""            <div class="high-corr-item">
                <span class="high-corr-pair">{item['ticker1']} ↔ {item['ticker2']}</span>
                <span class="high-corr-value">{item['corr']}</span>
            </div>
"""
        html += "        </div>\n"
    
    # Risk clusters section
    if risk_clusters:
        html += """        <div class="stats-section">
            <h2>Risk Clusters (3+ tickers with avg correlation > 0.75)</h2>
"""
        for cluster in risk_clusters:
            tickers_str = ", ".join(cluster["tickers"])
            avg_corr = cluster.get("avg_corr", "N/A")
            html += f"""            <div class="risk-cluster-item">
                <div>
                    <div class="cluster-tickers">{tickers_str}</div>
                    <div class="cluster-avg">Avg Correlation: {avg_corr}</div>
                </div>
            </div>
"""
        html += "        </div>\n"
    
    # Summary stats
    html += f"""        <div class="stats-section">
            <h2>Summary Statistics</h2>
            <div class="stats-grid">
                <div class="stat-box">
                    <h3>Tickers Analyzed</h3>
                    <p>{len(tickers)}</p>
                </div>
                <div class="stat-box">
                    <h3>Portfolio Holdings</h3>
                    <p>{len(portfolio_tickers)}</p>
                </div>
                <div class="stat-box">
                    <h3>High Correlations</h3>
                    <p>{len(high_corrs)}</p>
                </div>
                <div class="stat-box">
                    <h3>Risk Clusters</h3>
                    <p>{len(risk_clusters)}</p>
                </div>
            </div>
        </div>
        
        <div style="text-align: center; padding: 20px; color: #666; font-size: 12px;">
            <p>Green labels = Portfolio holdings | Data: Last 6 months of daily closes</p>
        </div>
    </div>
</body>
</html>
"""
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html)
    logger.info("Generated HTML heatmap to %s", output_path)


def main():
    """Main entry point."""
    setup_logging()
    logger.info("=== Starting correlation_heatmap module ===")
    
    try:
        # Load configs
        portfolio, watchlist = load_config()
        all_tickers, portfolio_tickers = get_all_tickers(portfolio, watchlist)
        
        if not all_tickers:
            logger.error("No tickers found in config")
            envelope = create_envelope(
                "correlation_heatmap",
                {},
                status="error",
                error="No tickers found in portfolio or watchlist"
            )
            save_envelope(envelope, "correlation_heatmap.json")
            return
        
        logger.info("Analyzing %d tickers: %s", len(all_tickers), all_tickers)
        
        # Download prices
        prices = download_prices(all_tickers)
        if prices.empty:
            logger.error("Failed to download price data")
            envelope = create_envelope(
                "correlation_heatmap",
                {},
                status="error",
                error="Failed to download price data"
            )
            save_envelope(envelope, "correlation_heatmap.json")
            return
        
        # Calculate correlations
        corr_matrix = calculate_correlations(prices)
        if corr_matrix.empty:
            logger.error("Failed to calculate correlations")
            envelope = create_envelope(
                "correlation_heatmap",
                {},
                status="error",
                error="Failed to calculate correlations"
            )
            save_envelope(envelope, "correlation_heatmap.json")
            return
        
        # Find high correlations and risk clusters
        high_corrs = find_high_correlations(corr_matrix, threshold=0.70)
        risk_clusters = find_risk_clusters(corr_matrix, threshold=0.75)
        
        # Flatten correlation matrix
        matrix_flat = flatten_correlation_matrix(corr_matrix)
        
        # Create data envelope
        data = {
            "matrix": matrix_flat,
            "high_correlations": high_corrs,
            "risk_clusters": risk_clusters,
            "tickers_analyzed": len(all_tickers),
        }
        
        envelope = create_envelope("correlation_heatmap", data, status="success")
        save_envelope(envelope, "correlation_heatmap.json")
        logger.info("Saved JSON envelope")
        
        # Generate HTML
        html_path = PROJECT_ROOT / "data" / "outputs" / f"correlation_heatmap_{datetime.now().strftime('%Y-%m-%d')}.html"
        generate_html(corr_matrix, high_corrs, risk_clusters, portfolio_tickers, html_path)
        logger.info("Saved HTML heatmap")
        
        logger.info("=== correlation_heatmap module complete ===")
        
    except Exception as e:
        logger.exception("Unexpected error in correlation_heatmap: %s", e)
        envelope = create_envelope(
            "correlation_heatmap",
            {},
            status="error",
            error=str(e)
        )
        save_envelope(envelope, "correlation_heatmap.json")


if __name__ == "__main__":
    main()
