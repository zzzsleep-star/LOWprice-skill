#!/usr/bin/env python3
"""Incrementally acquire the latest completed trading session through AKShare."""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import random
import time
from pathlib import Path

import akshare as ak
import numpy as np
import pandas as pd
import requests


def args():
    p = argparse.ArgumentParser()
    p.add_argument("--project-root", required=True, type=Path)
    p.add_argument("--run-date", help="Local date used to find the prior exchange session")
    p.add_argument("--workers", type=int, default=4)
    return p.parse_args()


def bounded_requests():
    original = requests.sessions.Session.request
    def request(self, *a, **kw):
        kw.setdefault("timeout", 25)
        return original(self, *a, **kw)
    requests.sessions.Session.request = request


def prior_trade_date(run_date: pd.Timestamp, old_calendar: pd.DataFrame) -> tuple[pd.Timestamp, pd.DataFrame, str]:
    try:
        cal = ak.tool_trade_date_hist_sina()
        cal = pd.DataFrame({"trade_date": pd.to_datetime(cal["trade_date"], errors="coerce")}).dropna().drop_duplicates()
        source = "AKShare.tool_trade_date_hist_sina"
    except Exception as exc:
        cal = old_calendar.copy()
        source = f"local_calendar_fallback:{type(exc).__name__}"
    dates = cal.loc[cal.trade_date.lt(run_date), "trade_date"]
    if dates.empty:
        raise RuntimeError("No exchange trading day before run date")
    return pd.Timestamp(dates.max()).normalize(), cal.sort_values("trade_date"), source


def fetch_one(rec: dict, wanted_dates: pd.DatetimeIndex) -> tuple[str, pd.DataFrame | None, str | None]:
    code = str(rec["bond_code"]).zfill(6)
    symbol = str(rec["exchange"]).lower() + code
    last = None
    for attempt in range(1, 4):
        try:
            raw = ak.bond_zh_hs_cov_daily(symbol=symbol)
            if raw is None or raw.empty:
                return code, None, "empty_response"
            raw = raw.rename(columns={"date": "trade_date", "volume": "volume_raw"})
            raw["trade_date"] = pd.to_datetime(raw["trade_date"], errors="coerce")
            hit = raw.loc[raw.trade_date.isin(wanted_dates)].copy()
            if hit.empty:
                return code, None, "requested_dates_absent"
            return code, hit.drop_duplicates("trade_date", keep="last"), None
        except Exception as exc:
            last = f"{type(exc).__name__}: {str(exc)[:300]}"
            if attempt < 3:
                time.sleep(attempt + random.random())
    return code, None, last


def update_snapshots(out: Path, retrieved: str, target: pd.Timestamp) -> dict:
    status = {}
    try:
        stock = ak.stock_zh_a_spot_em()
        code_col = next(c for c in stock.columns if str(c) in {"代码", "证券代码"})
        name_col = next(c for c in stock.columns if str(c) in {"名称", "证券简称"})
        snap = stock[[code_col, name_col]].rename(columns={code_col:"stock_code", name_col:"stock_name"})
        snap["stock_code"] = snap.stock_code.astype(str).str.zfill(6)
        names = snap.stock_name.astype(str).str.upper()
        snap["stock_risk_status"] = np.where(names.str.match(r"^\*?ST") | names.str.contains("退", na=False), "risk", "normal")
        snap["target_trade_date"] = target.date().isoformat()
        snap["retrieved_at"] = retrieved
        snap.to_csv(out / "stock_risk_snapshot.csv", index=False, encoding="utf-8-sig")
        status["stock_risk"] = "success"
    except Exception as exc:
        status["stock_risk"] = f"failed:{type(exc).__name__}:{str(exc)[:200]}"
    try:
        red = ak.bond_cb_redeem_jsl()
        code_col = next(c for c in red.columns if str(c) in {"代码", "转债代码", "债券代码"})
        status_cols = [c for c in red.columns if "强赎" in str(c) or "状态" in str(c)]
        text = red[status_cols].fillna("").astype(str).agg(lambda row: "|".join(row.tolist()), axis=1) if status_cols else pd.Series("", index=red.index)
        snap = pd.DataFrame({"bond_code": red[code_col].astype(str).str.zfill(6), "status_text": text})
        snap["decided_redeem"] = text.str.contains(r"已公告|公告强赎|强赎实施|赎回登记", regex=True, na=False)
        snap["target_trade_date"] = target.date().isoformat()
        snap["retrieved_at"] = retrieved
        snap.to_csv(out / "redemption_snapshot.csv", index=False, encoding="utf-8-sig")
        status["redemption"] = "success"
    except Exception as exc:
        status["redemption"] = f"failed:{type(exc).__name__}:{str(exc)[:200]}"
    return status


def main() -> int:
    a = args(); bounded_requests()
    root = a.project_root.resolve(); out = root / "outputs" / "daily_live"; out.mkdir(parents=True, exist_ok=True)
    daily_path = root / "outputs" / "daily" / "cb_daily_20200101_20260911.parquet"
    cal_path = root / "outputs" / "daily" / "trade_calendar.parquet"
    master_path = root / "outputs" / "cb_master_20200101_20260911.parquet"
    master = pd.read_parquet(master_path)
    daily = pd.read_parquet(daily_path)
    calendar = pd.read_parquet(cal_path)
    for c in ["listing_date", "delisting_date"]: master[c] = pd.to_datetime(master[c], errors="coerce")
    run_date = pd.Timestamp(a.run_date or dt.date.today()).normalize()
    target, new_calendar, calendar_source = prior_trade_date(run_date, calendar)
    active = master.loc[master.listing_date.le(target) & (master.delisting_date.isna() | master.delisting_date.gt(target))].copy()
    active["bond_code"] = active.bond_code.astype(str).str.zfill(6)
    daily["trade_date"] = pd.to_datetime(daily.trade_date)
    existing = set(daily.loc[daily.trade_date.eq(target), "bond_code"].astype(str).str.zfill(6))
    recent_sessions = pd.DatetimeIndex(new_calendar.loc[new_calendar.trade_date.le(target), "trade_date"].tail(20))
    dates_present = set(daily.trade_date.dropna().unique())
    missing_dates = pd.DatetimeIndex([d for d in recent_sessions if d not in dates_present])
    todo = active.copy() if len(missing_dates) else active.iloc[0:0].copy()
    retrieved = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat()
    fetched, failures = [], []
    with cf.ThreadPoolExecutor(max_workers=max(1, min(a.workers, 6))) as pool:
        futures = {pool.submit(fetch_one, r, missing_dates): r for r in todo.to_dict("records")}
        for fut in cf.as_completed(futures):
            rec = futures[fut]; code, raw, error = fut.result()
            expected = pd.DatetimeIndex([d for d in missing_dates if pd.Timestamp(rec["listing_date"]) <= d and
                                         (pd.isna(rec["delisting_date"]) or d < pd.Timestamp(rec["delisting_date"]))])
            got = raw.copy() if raw is not None else pd.DataFrame(columns=["trade_date","open","high","low","close","volume_raw"])
            got["trade_date"] = pd.to_datetime(got.get("trade_date"), errors="coerce")
            absent = [d for d in expected if d not in set(got.trade_date.dropna())]
            if absent:
                failures.append({"bond_code":code, "target_trade_date":target.date(), "error":error or "some_requested_dates_absent",
                                 "absent_dates":";".join(pd.Timestamp(d).date().isoformat() for d in absent)})
                placeholders = pd.DataFrame({"trade_date":absent, "open":np.nan, "high":np.nan, "low":np.nan,
                                             "close":np.nan, "volume_raw":np.nan})
                raw = pd.concat([got, placeholders], ignore_index=True)
            else:
                raw = got
            raw["download_status"] = np.where(raw.trade_date.isin(absent), "missing_or_failed", "success")
            for c in ["open","high","low","close","volume_raw"]: raw[c] = pd.to_numeric(raw.get(c), errors="coerce")
            raw["bond_code"] = code; raw["exchange"] = rec["exchange"]; raw["bond_name"] = rec["bond_name"]
            raw["stock_code"] = str(rec["stock_code"]).zfill(6); raw["stock_name"] = rec["stock_name"]
            raw["volume_unit"] = "bond"; raw["volume_multiplier"] = 1.0; raw["volume_bonds"] = raw.volume_raw
            raw["volume_verification_status"] = "inherits_project_verified_unit"; raw["volume_verification_source"] = "project_crosscheck"
            raw["amount_yuan"] = np.nan; raw["amount_source"] = "missing"
            raw["amount_verification_status"] = "not_available_from_akshare_sina_daily"
            raw["previous_close"] = np.nan; raw["previous_trade_date"] = pd.NaT; raw["gap_calendar_days"] = np.nan
            raw["pct_change"] = np.nan; raw["amplitude"] = np.nan; raw["turnover_amount_available"] = False
            raw["positive_volume"] = raw.volume_bonds.gt(0).where(raw.volume_bonds.notna(), pd.NA)
            raw["listing_date"] = rec["listing_date"]; raw["delisting_date"] = rec["delisting_date"]
            raw["data_source"] = "AKShare.bond_zh_hs_cov_daily/Sina"; raw["retrieved_at"] = retrieved
            raw["is_warmup"] = False
            raw["estimated_amount_yuan"] = raw.close * raw.volume_bonds
            raw["estimated_amount_method"] = "close_x_volume_bonds"
            raw["amount_for_screening"] = raw.estimated_amount_yuan
            raw["amount_for_screening_source"] = "estimated_close_x_volume_bonds"
            fetched.append(raw)
    if fetched:
        add = pd.concat(fetched, ignore_index=True)
        for c in daily.columns:
            if c not in add: add[c] = pd.NA
        combined = pd.concat([daily, add[daily.columns]], ignore_index=True)
        combined["trade_date"] = pd.to_datetime(combined.trade_date)
        combined = combined.sort_values(["bond_code","trade_date","download_status"]).drop_duplicates(["bond_code","trade_date"], keep="last")
        tmp = daily_path.with_suffix(".tmp.parquet"); combined.to_parquet(tmp, index=False); tmp.replace(daily_path)
        combined.to_csv(daily_path.with_suffix(".csv"), index=False, encoding="utf-8-sig")
    new_calendar.to_parquet(cal_path, index=False)
    pd.DataFrame(failures).to_csv(out / "daily_update_failures.csv", index=False, encoding="utf-8-sig")
    snapshot_status = update_snapshots(out, retrieved, target)
    target_rows = pd.read_parquet(daily_path)
    target_rows = target_rows.loc[pd.to_datetime(target_rows.trade_date).eq(target)]
    coverage = int(target_rows.download_status.eq("success").sum())
    status = {"run_date":str(run_date.date()), "target_trade_date":str(target.date()),
              "calendar_source":calendar_source, "active_master_count":len(active),
              "already_present":len(existing), "download_requested":len(todo),
              "backfilled_trade_dates":[pd.Timestamp(d).date().isoformat() for d in missing_dates],
              "successful_target_rows":coverage, "failure_or_absent_count":len(failures),
              "snapshot_status":snapshot_status, "retrieved_at":retrieved}
    (out / "update_status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__": raise SystemExit(main())
