# Crypto Comparison Website

Static crypto dashboard showing the top 100 cryptocurrencies by market cap.
Data is fetched from the [CoinGecko API](https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=100&page=1&sparkline=true&price_change_percentage=24h,7d)
and rendered into plain HTML/CSS/JS — no backend required.

Generated output:

- `site/index.html` — sortable, searchable dashboard (dark mode)
- `site/coin/<id>.html` — one detail page per coin with a 7-day Chart.js price chart

## Run locally

Requirements: Python 3.11+.

```bash
pip install -r requirements.txt
python build_site.py
```

Then open `site/index.html` in your browser.
The script prints progress (`Fetching data...`, `Generating index...`,
`Generating coin pages...`, `Done! Site built in site/`) and exits
gracefully if the CoinGecko API is unreachable after 3 retries.

## Auto-deploy

The workflow in `.github/workflows/deploy.yml`:

1. Runs daily at `06:00 UTC` (`cron: '0 6 * * *'`) and on every push to `main`.
2. Uses `ubuntu-latest`, checks out with `actions/checkout@v4`, sets up
   Python 3.11 with `actions/setup-python@v5`.
3. Runs `pip install -r requirements.txt` and `python build_site.py`.
4. Commits the refreshed `site/` folder back with
   `stefanzweifel/git-auto-commit-action@v5` (`Update crypto data`).
5. Uploads `site/` via `actions/upload-pages-artifact@v3` and deploys it
   with `actions/deploy-pages@v4`.

Permissions: `contents: write` (to push rebuilt HTML) and
`pages: write` (+ `id-token: write` for Pages deploy).

To enable Pages: repo **Settings → Pages → Source: GitHub Actions**.

## GitHub Pages URL structure

Once enabled, the site is served at:

```text
https://<username>.github.io/<repo>/
https://<username>.github.io/<repo>/coin/<id>.html
```

Example:

```text
https://octocat.github.io/crypto-site/
https://octocat.github.io/crypto-site/coin/bitcoin.html
```
