#!/usr/bin/env python3
"""
build_site.py — Static crypto comparison website generator.

Fetches top 100 cryptocurrencies from CoinGecko and generates a
static dark-mode dashboard (site/index.html) plus 100 individual
coin pages (site/coin/<id>.html) with 7-day Chart.js charts.

Usage:
    python build_site.py
"""

import html
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_URL = (
    "https://api.coingecko.com/api/v3/coins/markets"
    "?vs_currency=usd&order=market_cap_desc&per_page=100&page=1"
    "&sparkline=true&price_change_percentage=24h,7d"
)
CHART_JS_CDN = "https://cdn.jsdelivr.net/npm/chart.js"

SITE_DIR = Path("site")
COIN_DIR = SITE_DIR / "coin"

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 3
REQUEST_TIMEOUT = 30

# Shared dark-theme CSS (inline via <style>, no external files).
BASE_CSS = """
  :root { --bg:#0d1117; --text:#c9d1d9; --accent:#58a6ff; --border:#30363d;
          --green:#3fb950; --red:#f85149; --card:#161b22; }
  * { box-sizing:border-box; }
  body { background:var(--bg); color:var(--text); font-family:-apple-system,
         BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
         margin:0; padding:0; line-height:1.5; }
  header, main, footer { max-width:1100px; margin:0 auto; padding:20px; }
  a { color:var(--accent); text-decoration:none; }
  a:hover { text-decoration:underline; }
  h1 { font-size:1.8rem; margin-bottom:0.25rem; }
  .timestamp { color:#8b949e; font-size:0.9rem; margin-bottom:1rem; }
  #searchInput { width:100%; padding:10px 12px; margin:12px 0 16px;
    background:var(--card); border:1px solid var(--border); border-radius:6px;
    color:var(--text); font-size:1rem; }
  #searchInput::placeholder { color:#8b949e; }
  .table-wrapper { overflow-x:auto; border:1px solid var(--border);
    border-radius:8px; }
  table { width:100%; border-collapse:collapse; min-width:720px;
    background:var(--card); }
  th, td { padding:12px 14px; text-align:right; border-bottom:1px solid var(--border);
    white-space:nowrap; }
  th:first-child, td:first-child, th:nth-child(2), td:nth-child(2) { text-align:left; }
  th { background:#0d1117; cursor:pointer; user-select:none; }
  th.sortable:hover { color:var(--accent); }
  th .arrow { color:var(--accent); font-size:0.8em; }
  tbody tr:hover { background:#1c2128; }
  .coin-cell { display:flex; align-items:center; gap:10px; }
  .coin-cell img { width:24px; height:24px; border-radius:50%; }
  .positive { color:var(--green); }
  .negative { color:var(--red); }
  .stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr));
    gap:12px; margin:20px 0; }
  .stat-card { background:var(--card); border:1px solid var(--border);
    border-radius:8px; padding:14px 16px; }
  .stat-card .label { font-size:0.8rem; color:#8b949e;
    text-transform:uppercase; letter-spacing:0.05em; }
  .stat-card .value { font-size:1.25rem; font-weight:600; margin-top:4px; }
  .coin-header { display:flex; align-items:center; gap:14px; margin-top:10px; }
  .coin-header img { width:48px; height:48px; border-radius:50%; }
  .chart-container { background:var(--card); border:1px solid var(--border);
    border-radius:8px; padding:16px; margin:20px 0; }
  .back-link { display:inline-block; margin:10px 0; }
  .ad { margin:32px 0 8px; padding:24px; text-align:center;
    border:1px dashed var(--border); border-radius:8px; color:#8b949e;
    background:var(--card); font-size:0.9rem; letter-spacing:0.05em; }
  canvas { width:100% !important; max-height:400px; }
"""

INDEX_JS = """
  const searchInput = document.getElementById('searchInput');
  searchInput.addEventListener('input', function() {
    const q = this.value.toLowerCase();
    document.querySelectorAll('#coinTable tbody tr').forEach(row => {
      const text = row.getAttribute('data-search') || '';
      row.style.display = text.includes(q) ? '' : 'none';
    });
  });

  // Sortable table: click a header to sort asc/desc.
  // Numeric columns use data-value attributes; coin column falls back to text.
  let sortCol = -1, sortAsc = true;
  document.querySelectorAll('#coinTable th.sortable').forEach((th) => {
    th.addEventListener('click', () => {
      const table = document.getElementById('coinTable');
      const tbody = table.querySelector('tbody');
      const rows = Array.from(tbody.querySelectorAll('tr'));
      const col = parseInt(th.getAttribute('data-col'), 10);
      const numeric = th.getAttribute('data-numeric') === '1';
      if (sortCol === col) { sortAsc = !sortAsc; } else { sortCol = col; sortAsc = true; }
      rows.sort((a, b) => {
        let av, bv;
        if (numeric) {
          av = parseFloat(a.cells[col].getAttribute('data-value') || '0');
          bv = parseFloat(b.cells[col].getAttribute('data-value') || '0');
          if (isNaN(av)) av = 0; if (isNaN(bv)) bv = 0;
          return sortAsc ? av - bv : bv - av;
        } else {
          av = a.cells[col].textContent.trim().toLowerCase();
          bv = b.cells[col].textContent.trim().toLowerCase();
          return sortAsc ? av.localeCompare(bv) : bv.localeCompare(av);
        }
      });
      rows.forEach(r => tbody.appendChild(r));
      document.querySelectorAll('#coinTable th .arrow').forEach(el => el.textContent = '');
      const arrow = th.querySelector('.arrow');
      if (arrow) arrow.textContent = sortAsc ? ' \\u25B2' : ' \\u25BC';
    });
  });
"""

AD_FOOTER = '<div class="ad">Advertisement</div>'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fetch_data():
    """Fetch top-100 coins from CoinGecko with retry logic.

    Returns:
        list[dict]: Parsed JSON response.

    Exits the program gracefully (status 1) after MAX_RETRIES failures.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(API_URL, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list) or not data:
                raise ValueError("Unexpected API response shape")
            return data
        except Exception as exc:  # network, HTTP, JSON errors
            print(f"Attempt {attempt}/{MAX_RETRIES} failed: {exc}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_SECONDS)
            else:
                print("Error: Failed to fetch data from CoinGecko API "
                      f"after {MAX_RETRIES} attempts. Exiting.")
                sys.exit(1)


def esc(value):
    """HTML-escape a value for safe templating."""
    return html.escape(str(value), quote=True)


def format_price(value):
    """Format a USD price adaptively (small prices get more decimals)."""
    if value is None:
        return "N/A"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if v >= 1:
        return f"${v:,.2f}"
    if v >= 0.01:
        return f"${v:,.4f}"
    return f"${v:,.8f}".rstrip("0").rstrip(".")


def format_money(value):
    """Format large USD money values (market cap / high / low)."""
    if value is None:
        return "N/A"
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "N/A"


def format_market_cap(value):
    """Compact market-cap formatting, full value kept in data-value attr."""
    if value is None:
        return "N/A"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if v >= 1_000_000_000:
        return f"${v / 1_000_000_000:,.2f}B"
    if v >= 1_000_000:
        return f"${v / 1_000_000:,.2f}M"
    if v >= 1_000:
        return f"${v / 1_000:,.2f}K"
    return f"${v:,.2f}"


def pct_info(value):
    """Return (display_text, css_class, numeric_value) for a % change."""
    if value is None:
        return ("N/A", "", 0.0)
    try:
        v = float(value)
    except (TypeError, ValueError):
        return ("N/A", "", 0.0)
    cls = "positive" if v >= 0 else "negative"
    sign = "+" if v >= 0 else ""
    return (f"{sign}{v:.2f}%", cls, v)


def get_24h_change(coin):
    """24h % change (API names vary by request)."""
    return coin.get("price_change_percentage_24h",
                    coin.get("price_change_percentage_24h_in_currency"))


def get_7d_change(coin):
    """7d % change (API returns ..._7d_in_currency for this query)."""
    for key in ("price_change_percentage_7d_in_currency",
                "price_change_percentage_7d",
                "price_change_percentage_7d_in_currency_in_currency"):
        if coin.get(key) is not None:
            return coin.get(key)
    # Fallback: nested dict form some API versions return.
    nested = coin.get("price_change_percentage_7d_in_currency")
    return nested


def get_sparkline(coin):
    """Extract 7-day sparkline price list (may be empty)."""
    spark = coin.get("sparkline_in_7d") or {}
    prices = spark.get("price") or []
    # Downsample very long arrays to keep HTML size reasonable.
    if len(prices) > 168:
        step = len(prices) / 168
        prices = [prices[int(i * step)] for i in range(168)]
    return [float(p) for p in prices if isinstance(p, (int, float))]


# ---------------------------------------------------------------------------
# HTML builders
# ---------------------------------------------------------------------------

def build_index_rows(coins):
    """Build <tr> rows for the dashboard table."""
    rows = []
    for coin in coins:
        cid = esc(coin.get("id", ""))
        name = esc(coin.get("name", "N/A"))
        symbol = esc(str(coin.get("symbol", "")).upper())
        image = esc(coin.get("image", ""))
        rank = coin.get("market_cap_rank") or 0
        price = coin.get("current_price")
        mcap = coin.get("market_cap")

        ch24_text, ch24_cls, ch24_num = pct_info(get_24h_change(coin))
        ch7_text, ch7_cls, ch7_num = pct_info(get_7d_change(coin))

        price_num = float(price) if isinstance(price, (int, float)) else 0.0
        mcap_num = float(mcap) if isinstance(mcap, (int, float)) else 0.0

        # Hotlink CoinGecko images (no local download needed).
        rows.append(f"""      <tr data-search="{name.lower()} {symbol.lower()} {cid.lower()}">
        <td data-value="{rank}">{rank if rank else "N/A"}</td>
        <td data-value="{name.lower()}"><div class="coin-cell"><img src="{image}" alt="{name} logo" loading="lazy"><a href="coin/{cid}.html">{name} <span style="color:#8b949e">{symbol}</span></a></div></td>
        <td data-value="{price_num}">{esc(format_price(price))}</td>
        <td data-value="{ch24_num}" class="{ch24_cls}">{ch24_text}</td>
        <td data-value="{ch7_num}" class="{ch7_cls}">{ch7_text}</td>
        <td data-value="{mcap_num}">{esc(format_market_cap(mcap))}</td>
      </tr>""")
    return "\n".join(rows)


def build_index_html(coins, updated_str):
    """Render the full dashboard page."""
    rows = build_index_rows(coins)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Crypto Dashboard — Top 100 by Market Cap</title>
<style>{BASE_CSS}
</style>
</head>
<body>
<header>
  <h1>Crypto Dashboard — Top 100</h1>
  <div class="timestamp">Last updated: {esc(updated_str)} UTC</div>
  <input type="text" id="searchInput" placeholder="Search coins by name or symbol...">
</header>
<main>
  <div class="table-wrapper">
    <table id="coinTable">
      <thead>
        <tr>
          <th class="sortable" data-col="0" data-numeric="1">Rank<span class="arrow"></span></th>
          <th class="sortable" data-col="1" data-numeric="0">Coin<span class="arrow"></span></th>
          <th class="sortable" data-col="2" data-numeric="1">Price<span class="arrow"></span></th>
          <th class="sortable" data-col="3" data-numeric="1">24h %<span class="arrow"></span></th>
          <th class="sortable" data-col="4" data-numeric="1">7d %<span class="arrow"></span></th>
          <th class="sortable" data-col="5" data-numeric="1">Market Cap<span class="arrow"></span></th>
        </tr>
      </thead>
      <tbody>
{rows}
      </tbody>
    </table>
  </div>
</main>
<footer>
  {AD_FOOTER}
</footer>
<script>{INDEX_JS}
</script>
</body>
</html>
"""


def build_coin_html(coin):
    """Render an individual coin detail page with a Chart.js 7-day chart."""
    cid = esc(coin.get("id", ""))
    name = esc(coin.get("name", "N/A"))
    symbol = esc(str(coin.get("symbol", "")).upper())
    image = esc(coin.get("image", ""))
    price = coin.get("current_price")
    mcap = coin.get("market_cap")
    high24 = coin.get("high_24h")
    low24 = coin.get("low_24h")

    ch24_text, ch24_cls, _ = pct_info(get_24h_change(coin))
    ch7_text, ch7_cls, _ = pct_info(get_7d_change(coin))

    sparkline = get_sparkline(coin)
    spark_json = json.dumps(sparkline)
    labels = json.dumps(list(range(len(sparkline))))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{name} ({symbol}) — Price &amp; Stats</title>
<script src="{CHART_JS_CDN}"></script>
<style>{BASE_CSS}
</style>
</head>
<body>
<header>
  <a class="back-link" href="../index.html">&larr; Back to Dashboard</a>
  <div class="coin-header">
    <img src="{image}" alt="{name} logo">
    <div>
      <h1 style="margin:0">{name} ({symbol})</h1>
      <div style="color:#8b949e">Rank #{esc(coin.get("market_cap_rank") or "N/A")} &middot; ID: {cid}</div>
    </div>
  </div>
</header>
<main>
  <div class="stats">
    <div class="stat-card"><div class="label">Current Price</div><div class="value">{esc(format_price(price))}</div></div>
    <div class="stat-card"><div class="label">Market Cap</div><div class="value">{esc(format_money(mcap))}</div></div>
    <div class="stat-card"><div class="label">24h High</div><div class="value">{esc(format_money(high24))}</div></div>
    <div class="stat-card"><div class="label">24h Low</div><div class="value">{esc(format_money(low24))}</div></div>
    <div class="stat-card"><div class="label">24h Change</div><div class="value {ch24_cls}">{ch24_text}</div></div>
    <div class="stat-card"><div class="label">7d Change</div><div class="value {ch7_cls}">{ch7_text}</div></div>
  </div>
  <div class="chart-container">
    <h2 style="margin-top:0">7-Day Price Chart (USD)</h2>
    <canvas id="priceChart"></canvas>
  </div>
</main>
<footer>
  {AD_FOOTER}
</footer>
<script>
  const prices = {spark_json};
  const labels = {labels};
  const ctx = document.getElementById('priceChart').getContext('2d');
  const gradient = ctx.createLinearGradient(0, 0, 0, 320);
  gradient.addColorStop(0, 'rgba(88,166,255,0.45)');
  gradient.addColorStop(1, 'rgba(88,166,255,0.0)');
  new Chart(ctx, {{
    type: 'line',
    data: {{
      labels: labels,
      datasets: [{{
        label: '{name} 7d price (USD)',
        data: prices,
        borderColor: '#58a6ff',
        backgroundColor: gradient,
        fill: true,
        tension: 0.25,
        pointRadius: 0,
        borderWidth: 2
      }}]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: true,
      plugins: {{ legend: {{ labels: {{ color: '#c9d1d9' }} }} }},
      scales: {{
        x: {{ ticks: {{ color: '#8b949e', maxTicksLimit: 8 }}, grid: {{ color: '#21262d' }} }},
        y: {{ ticks: {{ color: '#8b949e' }}, grid: {{ color: '#21262d' }} }}
      }}
    }}
  }});
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("Fetching data...")
    coins = fetch_data()
    print(f"Fetched {len(coins)} coins.")

    # Create directories: site/ and site/coin/
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    COIN_DIR.mkdir(parents=True, exist_ok=True)

    updated_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    print("Generating index...")
    index_html = build_index_html(coins, updated_str)
    (SITE_DIR / "index.html").write_text(index_html, encoding="utf-8")

    print("Generating coin pages...")
    for coin in coins:
        cid = coin.get("id")
        if not cid:
            continue
        # Basic sanitization for filesystem safety.
        safe_id = "".join(c for c in str(cid) if c.isalnum() or c in ("-", "_")).strip()
        if not safe_id:
            continue
        page = build_coin_html(coin)
        (COIN_DIR / f"{safe_id}.html").write_text(page, encoding="utf-8")

    print("Done! Site built in site/")


if __name__ == "__main__":
    main()
