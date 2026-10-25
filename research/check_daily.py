"""Daily-mode check: run `daily` day by day in a separate output folder and verify that
(1) every day equals the same day of `refill`, and
(2) every empty cell has a legitimate reason (no cell with an EEX price is left empty).

    uv run python research/check_daily.py [--vwap data/synthetic_vwaps.csv] [--offset 0]
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
KEYS = ["reference_date", "product", "region", "unit", "tenor"]
COMPARE = ["price", "source", "eex_settle", "anchors", "trust", "hist_deviation", "hist_audit", "missing_reason"]


def config(out: Path, offset: int) -> Path:
    text = (ROOT / "config.toml").read_text(encoding="utf-8")
    text = re.sub(r"(?m)^output_dir\s*=.*$", f'output_dir = "{out.as_posix()}"', text)
    text = re.sub(r"(?m)^mapping\s*=.*$", f'mapping = "{(ROOT / "mappings" / "products.csv").as_posix()}"', text)
    eex = (ROOT.parent / "eex_scraper" / "output" / "curves" / "POWER").as_posix()
    text = re.sub(r"(?m)^eex_curves_dir\s*=.*$", f'eex_curves_dir = "{eex}"', text)
    text = re.sub(r"(?m)^offset_days\s*=.*$", f"offset_days = {offset}", text)
    path = out / "config.toml"
    out.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def run(cfg: Path, *args: str) -> None:
    done = subprocess.run(
        [sys.executable, str(ROOT / "run.py"), "--config", str(cfg), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if done.returncode:
        sys.exit(done.stdout[-3000:] + done.stderr[-3000:])


def read(path: Path) -> pd.DataFrame:
    f = pd.read_csv(path, keep_default_na=False, na_values=[""], low_memory=False)
    for c in ("region", "unit", "anchors", "missing_reason", "hist_audit"):
        if c in f:
            f[c] = f[c].fillna("").astype(str)
    return f


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vwap", default=str(ROOT / "data" / "synthetic_vwaps.csv"))
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--history-days", type=int, default=0)
    ap.add_argument("--out", type=Path, default=ROOT / "output_check")
    a = ap.parse_args()
    out = (a.out / f"h{a.history_days}").resolve()

    ref_cfg = config(out / f"refill_off{a.offset}", a.offset)
    extra = ["--history-days", str(a.history_days)]
    run(ref_cfg, "refill", "--vwap", a.vwap, *extra)
    refill = read(ref_cfg.parent / "filled_history.csv")

    day_cfg = config(out / f"daily_off{a.offset}", a.offset)
    days = sorted(refill["reference_date"].unique())
    for d in days:
        run(day_cfg, "daily", "--date", d, "--vwap", a.vwap, *extra)
    daily = pd.concat([read(p) for p in sorted((day_cfg.parent / "filled").glob("*.csv"))], ignore_index=True)

    m = refill[KEYS + COMPARE].merge(daily[KEYS + COMPARE], on=KEYS, how="outer", suffixes=("_refill", "_daily"), indicator=True)
    only = m["_merge"].value_counts().to_dict()
    both = m[m["_merge"] == "both"]
    diffs = {}
    for c in COMPARE:
        x, y = both[f"{c}_refill"], both[f"{c}_daily"]
        if pd.api.types.is_numeric_dtype(x):
            bad = ~(((x - y).abs() < 1e-9) | (x.isna() & y.isna()))
        else:
            bad = x.fillna("").astype(str) != y.fillna("").astype(str)
        diffs[c] = int(bad.sum())

    missing = refill[refill["source"] == "missing"]
    reasons = missing["missing_reason"].value_counts().to_dict()
    unexplained = missing[missing["missing_reason"].isin(["", "no_estimate"])]
    fillable = missing[missing["missing_reason"] != "zero_delivery_hours"]
    with_eex_left_empty = missing[missing["eex_settle"].notna() & (missing["hours"] > 0)]

    print(f"days={len(days)} | cells refill={len(refill)} daily={len(daily)} | rows match: {only}")
    print("cells that differ between daily and refill, by column:", diffs)
    print("empty cells by reason:", reasons)
    print("empty cells WITHOUT a legitimate reason:", len(unexplained))
    print("empty cells although EEX had a price:", len(with_eex_left_empty))
    print("empty cells other than zero delivery hours:", len(fillable))
    ok = (
        only.get("left_only", 0) == only.get("right_only", 0) == 0
        and not any(diffs.values())
        and unexplained.empty
        and with_eex_left_empty.empty
    )
    print("RESULT:", "OK" if ok else "PROBLEMS FOUND")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
