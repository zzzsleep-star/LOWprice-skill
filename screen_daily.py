#!/usr/bin/env python3
"""Generate a point-in-time daily top-10 list using the established backtest code."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--project-root", type=Path)
    p.add_argument("--as-of", help="Requested date, YYYY-MM-DD; defaults to latest local date")
    p.add_argument("--output-dir", type=Path)
    p.add_argument("--top-k", type=int, default=10)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.project_root or os.environ.get("CB_LOW_PRICE_PROJECT_ROOT") or Path.cwd()).resolve()
    framework = root / "outputs" / "backtest_framework"
    if not framework.exists():
        raise FileNotFoundError(f"Backtest framework not found: {framework}")
    sys.path.insert(0, str(framework))
    from config import BacktestConfig
    from data import load_data, prepare_panel, validate
    from pool import screen_pool

    master, daily, calendar, events = load_data(root)
    validate(master, daily)
    panel = prepare_panel(master, daily, calendar, events)
    local_latest = pd.Timestamp(panel.trade_date.max()).normalize()
    requested = pd.Timestamp(args.as_of).normalize() if args.as_of else local_latest
    available = panel.loc[panel.trade_date.le(requested), "trade_date"]
    if available.empty:
        raise ValueError(f"No observations on or before {requested.date()}")
    signal_date = pd.Timestamp(available.max()).normalize()
    stale = signal_date < requested

    cfg = BacktestConfig()
    day = panel.loc[panel.trade_date.eq(signal_date)].copy()
    live_dir = root / "outputs" / "daily_live"
    risk_path = live_dir / "stock_risk_snapshot.csv"
    if risk_path.exists():
        risk = pd.read_csv(risk_path, dtype={"stock_code": str})
        if "target_trade_date" in risk:
            risk = risk.loc[pd.to_datetime(risk.target_trade_date, errors="coerce").eq(signal_date)]
        risk["stock_code"] = risk.stock_code.astype(str).str.zfill(6)
        risk = risk.drop_duplicates("stock_code", keep="last")
        risk_map = risk.set_index("stock_code")["stock_risk_status"]
        day["stock_code"] = day.stock_code.astype(str).str.zfill(6)
        mapped = day.stock_code.map(risk_map)
        day.loc[mapped.notna(), "stock_risk_status"] = mapped[mapped.notna()]
    redeem_path = live_dir / "redemption_snapshot.csv"
    if redeem_path.exists():
        redeem = pd.read_csv(redeem_path, dtype={"bond_code": str})
        if "target_trade_date" in redeem:
            redeem = redeem.loc[pd.to_datetime(redeem.target_trade_date, errors="coerce").eq(signal_date)]
        redeem["bond_code"] = redeem.bond_code.astype(str).str.zfill(6)
        decided_flag = redeem.decided_redeem.astype(str).str.lower().isin(["true", "1", "yes"])
        decided = set(redeem.loc[decided_flag, "bond_code"])
        hit = day.bond_code.astype(str).str.zfill(6).isin(decided)
        day.loc[hit, "decision_status"] = "decided_redeem"
        day.loc[hit, "available_from"] = signal_date
    screened, counts = screen_pool(day, cfg)
    eligible = screened.loc[screened.pool_pass].copy()
    eligible["bond_code"] = eligible.bond_code.astype(str).str.zfill(6)
    eligible = eligible.sort_values(["close", "bond_code"], ascending=[True, True])
    eligible["rank"] = range(1, len(eligible) + 1)
    top = eligible.head(args.top_k).copy()

    amount_sources = (daily.loc[daily.trade_date.eq(signal_date)]
                      .groupby("bond_code", as_index=False)["amount_for_screening_source"].first())
    top = top.merge(amount_sources, on="bond_code", how="left", suffixes=("", "_day"))
    cols = [c for c in [
        "rank", "trade_date", "bond_code", "bond_name", "stock_code", "stock_name",
        "close", "rating_raw", "issue_rating_verified", "rating_status", "maturity_date",
        "days_to_maturity", "amount_5d", "amount_for_screening_source",
        "stock_risk_status", "decision_status", "available_from",
        "last_trade_date_event", "data_source"
    ] if c in top.columns]
    top = top[cols]

    out_root = args.output_dir or root / "outputs" / "daily_low_price" / signal_date.strftime("%Y-%m-%d")
    out_root.mkdir(parents=True, exist_ok=True)
    top.to_csv(out_root / "low_price_top10.csv", index=False, encoding="utf-8-sig")
    screened.to_csv(out_root / "screening_detail.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame([counts]).to_csv(out_root / "screening_summary.csv", index=False, encoding="utf-8-sig")

    unknown_risk = int(day.stock_risk_status.eq("unknown").sum())
    estimated_top = int(top.get("amount_for_screening_source", pd.Series(dtype=str)).astype(str)
                        .str.contains("estimated", case=False, na=False).sum())
    metadata = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "requested_as_of": requested.date().isoformat(),
        "signal_date": signal_date.date().isoformat(),
        "local_latest_trade_date": local_latest.date().isoformat(),
        "is_stale_vs_requested": bool(stale),
        "eligible_count": int(len(eligible)),
        "selected_count": int(len(top)),
        "unknown_stock_risk_count_on_signal_date": unknown_risk,
        "top10_estimated_amount_source_count": estimated_top,
        "rules": {"min_rating": "A+", "min_days_to_maturity": 90,
                  "min_amount_5d_yuan": 50000000, "min_close_yuan": 90,
                  "exclude_effective_decided_redeem": True,
                  "sort": "close ascending, bond_code ascending", "top_k": args.top_k,
                  "risk_unknown_policy": "pass", "allow_unverified_rating": True},
        "sequential_exclusion_counts": counts,
    }
    (out_root / "run_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    title = "最新可用历史筛选" if stale else "低价策略筛选"
    lines = [f"# {title}（{signal_date.date()}）", "",
             f"请求日期：{requested.date()}；本地行情最新日期：{local_latest.date()}。",
             f"候选池 {len(eligible)} 只，输出 {len(top)} 只。", "",
             top.to_markdown(index=False) if not top.empty else "无符合条件的证券。", "",
             f"股票风险状态 unknown：{unknown_risk} 条；原回测按 pass 处理。",
             f"前十中使用估算成交额来源：{estimated_top} 只。"]
    (out_root / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(metadata, ensure_ascii=False, indent=2, default=str))
    print(f"OUTPUT_DIR={out_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
