---
name: cb-low-price-daily
description: Generate the latest daily top 10 Shanghai and Shenzhen convertible bonds using the exact low-price strategy rules from the completed historical backtest. Use for daily low-price bond screening, candidate lists, and scheduled strategy monitoring.
---

# Daily Convertible Bond Low-Price Screen

Run `scripts/screen_daily.py` against the established project database. Preserve `references/strategy_rules.md`. Do not add conversion-premium, double-low, balance, industry, stock-return, 20-day zero-volume, or last-trading-day-over-20 filters.

## Workflow

1. Resolve the project root from `--project-root`, `CB_LOW_PRICE_PROJECT_ROOT`, or the current directory.
2. Each morning, run `scripts/run_morning.py`. It identifies the latest exchange trading day strictly before the run date, incrementally downloads that session through AKShare, saves risk/redemption snapshots, and then screens.
3. Use the project Python environment containing AKShare, pandas, and pyarrow:

   `work/py312/Scripts/python.exe scripts/run_morning.py --project-root <root>`

4. Call a result “上一交易日名单” only if the update status confirms the target trading date. If the calendar or all market requests fail, keep the previous database intact and report the failure.
5. Preserve sources. Do not silently drop failed securities: the updater writes placeholder rows and a failure list so they fail normal-trading checks visibly.
6. Return the ranked table, target date, freshness status, eligible count, sequential exclusion counts, output paths, and warnings.

Pass unknown stock-risk states only because the completed backtest used `risk_unknown_policy=pass`; disclose the unknown count. Exclude `decided_redeem` from `available_from` and reject a bond on or after `last_trade_date_event`.

Running on weekends or holidays still targets the most recent completed exchange trading day and does not fabricate a new date.
