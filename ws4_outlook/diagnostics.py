"""Read-only, attributable diagnosis of the saved interval baseline (T54)."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import itertools
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from common.grid import N_COLS
from common import metrics
from common.intervals import apply_interval_many
from ws4_outlook.conformal import CALENDAR_MONTH, _complete_months, daily_oof
from ws4_outlook.interval_report import _magnitude_labels, report as interval_report
from common.series import season_label

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "data/runs/T51-baseline-20261106T125252Z/baseline/processed"
DEFAULT_OUTPUT = ROOT / "results/runs/T54-interval-diagnosis-20261115"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_hashes_unchanged(baseline: Path, expected: dict[str, str]) -> dict[str, str]:
    after = {name: sha256(baseline / name) for name in expected}
    if after != expected:
        raise RuntimeError("protected baseline artifacts changed during read-only diagnosis")
    return after


def validate_oof_keys(oof: pd.DataFrame) -> None:
    key = ["cell_id", "date", "sat_combo"]
    if oof.duplicated(key).any():
        raise ValueError(f"duplicate contributing OOF IDs for keys {key}")


def validate_daily_keys(daily: pd.DataFrame) -> None:
    key = ["cell_id", "date", "sat_combo"]
    if daily.duplicated(key).any():
        raise ValueError(f"duplicate daily contributor IDs for keys {key}")


def trace_rectangles(oof: pd.DataFrame, daily: pd.DataFrame, cells: pd.DataFrame, table: dict,
                     n: int = 400, seed: int = 5301) -> tuple[list[dict], list[dict]]:
    """Repeat held-out rectangle sampling while retaining cell IDs and period/block dependence."""
    rng = np.random.default_rng(seed)
    metadata, samples = [], []
    landcover = cells.set_index("cell_id")["landcover_dom"]
    for res in ("week", "month"):
        for combo in ("terra_aqua", "terra_only"):
            weekly = oof[(oof.split == "test") & (oof.sat_combo == combo)].copy()
            weekly["date"] = pd.to_datetime(weekly.date)
            if res == "week":
                weekly["period"] = weekly.date
                data = weekly.set_index(["period", "cell_id"])[["y_true_count", "y_pred_count"]]
                periods = pd.DatetimeIndex(sorted(weekly.period.unique()))
            else:
                periods = pd.DatetimeIndex(_complete_months(weekly.date))
                d = daily[(daily.split == "test") & (daily.sat_combo == combo)].copy()
                d["period"] = pd.to_datetime(d.date).dt.to_period("M").dt.start_time
                data = d[d.period.isin(periods)].groupby(["period", "cell_id"])[["y_true_count", "y_pred_count"]].sum()
            by_block = {int(block): group for block, group in weekly.groupby("block_id")}
            block_ids = list(by_block)
            for draw in range(n):
                if not block_ids or not len(periods):
                    break
                block = block_ids[int(rng.integers(len(block_ids)))]
                ids = np.sort(by_block[block].cell_id.unique())
                center = int(ids[int(rng.integers(len(ids)))])
                height, width = (int(x) for x in rng.integers(1, 21, 2))
                row, col = divmod(center, N_COLS)
                rectangle = np.array([(row + y) * N_COLS + col + x for y in range(height) for x in range(width)])
                selected = np.intersect1d(ids, rectangle)
                period = pd.Timestamp(periods[int(rng.integers(len(periods)))])
                if not selected.size:
                    continue
                try:
                    period_data = data.xs(period, level="period")
                    values = period_data.reindex(selected).dropna(how="all")
                except KeyError:
                    continue
                if isinstance(values, pd.Series):
                    values = values.to_frame().T
                contributing = sorted(set(int(x) for x in values.index))
                if not contributing:
                    continue
                y = float(values.y_true_count.sum())
                pred = float(values.y_pred_count.sum())
                lc = landcover.reindex(contributing).dropna().mode()
                season = f"q{((period.month - 1) // 3) + 1}"
                lo, hi = apply_interval_many(np.array([pred]), [(f"landcover:{lc.iloc[0] if len(lc) else 'unknown'}|season:{season}",)],
                                             res, table)
                record = {"resolution": res, "sat_combo": combo, "draw": draw, "block_id": block,
                          "period": period.date().isoformat(), "year": period.year, "n_contributing_cells": len(contributing),
                          "drawn_n_cells": height * width, "cell_ids": contributing,
                          "landcover": str(lc.iloc[0]) if len(lc) else "unknown", "season": season_label(period.month),
                          "y_true_count": y, "y_pred_count": pred, "lo": float(lo[0]), "hi": float(hi[0])}
                samples.append(record)
            # Pairwise overlap uses the actual contributing cell-period keys, not draws as independent units.
            group = [x for x in samples if x["resolution"] == res and x["sat_combo"] == combo]
            overlaps = []
            for left, right in itertools.combinations(group, 2):
                a = {(cell_id, left["period"]) for cell_id in left["cell_ids"]}
                b = {(cell_id, right["period"]) for cell_id in right["cell_ids"]}
                union = a | b
                overlaps.append(len(a & b) / len(union) if union else 0.0)
            metadata.append({"resolution": res, "sat_combo": combo, "draws": len(group),
                             "mean_pairwise_cell_period_jaccard": float(np.mean(overlaps)) if overlaps else None,
                             "max_pairwise_cell_period_jaccard": float(np.max(overlaps)) if overlaps else None,
                             "unique_blocks": len({x["block_id"] for x in group}),
                             "unique_periods": len({x["period"] for x in group}),
                             "unique_years": len({x["year"] for x in group})})
    return metadata, samples


def traced_group_diagnostics(samples: list[dict]) -> list[dict]:
    """Metrics and dependence support from the separately traced, ID-retaining sample."""
    result = []
    dimensions = ("all", "activity", "prediction", "zero_activity", "area_size", "magnitude", "season", "landcover")
    for res in ("week", "month"):
        for combo in ("terra_aqua", "terra_only"):
            frame = [row for row in samples if row["resolution"] == res and row["sat_combo"] == combo]
            magnitude = _magnitude_labels(pd.Series([r["y_true_count"] for r in frame])) if frame else pd.Series(dtype=str)
            for dimension in dimensions:
                groups: dict[str, list[dict]] = {}
                for i, row in enumerate(frame):
                    if dimension == "all":
                        label, include = "all", True
                    elif dimension == "activity":
                        label, include = "positive", row["y_true_count"] > 0
                    elif dimension == "prediction":
                        label, include = "zero_with_positive_truth", row["y_true_count"] > 0 and row["y_pred_count"] <= 0
                    elif dimension == "zero_activity":
                        label, include = "zero", row["y_true_count"] <= 0
                    elif dimension == "area_size":
                        n = row["n_contributing_cells"]
                        label = "1-10" if n <= 10 else "11-50" if n <= 50 else "51-100" if n <= 100 else "101-400"
                        include = n <= 400
                    elif dimension == "magnitude":
                        label, include = str(magnitude.iloc[i]), True
                    elif dimension == "season":
                        label, include = row["season"], True
                    else:
                        label, include = row["landcover"], True
                    if include:
                        groups.setdefault(label, []).append(row)
                defaults = {"activity": ["positive"], "prediction": ["zero_with_positive_truth"],
                            "zero_activity": ["zero"], "area_size": ["1-10", "11-50", "51-100", "101-400"],
                            "magnitude": ["zero", "q1", "q2", "q3", "q4"],
                            "season": ["q1", "q2", "q3", "q4"]}
                for label in defaults.get(dimension, []):
                    groups.setdefault(label, [])
                for value, rows in groups.items():
                    y = np.array([r["y_true_count"] for r in rows], dtype=float)
                    pred = np.array([r["y_pred_count"] for r in rows], dtype=float)
                    lo = np.array([r["lo"] for r in rows], dtype=float)
                    hi = np.array([r["hi"] for r in rows], dtype=float)
                    cells_used = {cell for row in rows for cell in row["cell_ids"]}
                    n_rows = len(rows)
                    result.append({"res": res, "sat_combo": combo, "dimension": dimension, "value": value,
                                   "n": n_rows, "coverage": float(metrics.coverage(y, lo, hi)) if n_rows else None,
                                   "below_lower": float((y < lo).mean()) if n_rows else None,
                                   "above_upper": float((y > hi).mean()) if n_rows else None,
                                   "mean_truth": float(y.mean()) if n_rows else None,
                                   "mean_prediction": float(pred.mean()) if n_rows else None,
                                   "mean_width": float((hi - lo).mean()) if n_rows else None,
                                   "interval_score": float(metrics.interval_score(y, lo, hi, alpha=.1)) if n_rows else None,
                                   "unique_cells": len(cells_used), "unique_blocks": len({r["block_id"] for r in rows}),
                                   "unique_periods": len({r["period"] for r in rows}),
                                   "unique_years": len({r["year"] for r in rows}),
                                   "mean_contributing_cells": float(np.mean([r["n_contributing_cells"] for r in rows])) if n_rows else None,
                                   "mean_drawn_cells": float(np.mean([r["drawn_n_cells"] for r in rows])) if n_rows else None,
                                   "max_drawn_cells": max(r["drawn_n_cells"] for r in rows) if n_rows else None})
    return result


def named_area_support(oof: pd.DataFrame, cells: pd.DataFrame) -> list[dict]:
    test = oof[oof.split == "test"]
    areas = []
    for area_id, area in cells.groupby("admin1_id", dropna=True):
        required = set(int(x) for x in area.cell_id)
        by_combo = {}
        for combo in ("terra_aqua", "terra_only"):
            available = set(int(x) for x in test.loc[test.sat_combo == combo, "cell_id"].unique())
            covered = required & available
            areasize = len(required)
            by_combo[combo] = {"required_cells": areasize, "covered_test_cells": len(covered),
                               "complete_oof_coverage": len(covered) == areasize}
        areas.append({"area_id": str(area_id), "area_name": str(area.admin1_name.iloc[0]), "cells": len(required),
                      "by_combo": by_combo, "disposition": "supported only with complete coherent held-out coverage"
                      if all(v["complete_oof_coverage"] for v in by_combo.values()) else "insufficient_support"})
    return areas


def diagnose(baseline: Path = BASELINE, output: Path = DEFAULT_OUTPUT, overlap_draws: int = 400) -> dict:
    names = ("predictions_oof.parquet", "grid_daily.parquet", "cells.parquet", "interval_table.json")
    hashes_before = {name: sha256(baseline / name) for name in names}
    oof = pd.read_parquet(baseline / "predictions_oof.parquet")
    grid = pd.read_parquet(baseline / "grid_daily.parquet", columns=["cell_id", "date", "sat", "n_det"])
    cells = pd.read_parquet(baseline / "cells.parquet")
    table_bytes = (baseline / "interval_table.json").read_bytes()
    table = json.loads(table_bytes)
    validate_oof_keys(oof)
    daily = daily_oof(oof, grid)
    validate_daily_keys(daily)
    if len(daily) and daily.sat_combo.nunique() != oof.sat_combo.nunique():
        raise ValueError("daily target does not retain each input combination")
    # Detailed diagnostic reuses the frozen table and the registered 4,000 test rectangles per resolution/combo.
    from ws4_outlook.interval_report import report
    detailed = report(oof, cells, table, n=4000, seed=7, daily=daily)
    overlap, draws = trace_rectangles(oof, daily, cells, table, n=overlap_draws, seed=5301)
    traced_groups = traced_group_diagnostics(draws)
    support = []
    for (split, combo), group in oof.groupby(["split", "sat_combo"]):
        dates = pd.to_datetime(group.date)
        support.append({"split": split, "sat_combo": combo, "rows": int(len(group)),
                        "unique_cells": int(group.cell_id.nunique()), "unique_blocks": int(group.block_id.nunique()),
                        "unique_weeks": int(dates.nunique()), "unique_years": int(dates.dt.year.nunique()),
                        "positive_truth_rows": int((group.y_true_count > 0).sum()),
                        "zero_prediction_positive_truth": int(((group.y_true_count > 0) & (group.y_pred_count <= 0)).sum()),
                        "mean_truth": float(group.y_true_count.mean()), "mean_prediction": float(group.y_pred_count.mean())})
    months = pd.to_datetime(daily.date).dt.to_period("M")
    month_day_counts = daily.assign(month=months).groupby(["sat_combo", "month"]).date.nunique()
    complete_months = sorted(str(x) for x in _complete_months(daily.date))
    hashes_after = verify_hashes_unchanged(baseline, hashes_before)
    if hashlib.sha256(table_bytes).hexdigest() != hashes_after["interval_table.json"]:
        raise RuntimeError("protected calibration table changed during read-only diagnosis")
    output.mkdir(parents=True, exist_ok=True)
    (output / "rectangle-draws.jsonl").write_text("".join(json.dumps(row, separators=(",", ":")) + "\n" for row in draws), encoding="utf-8")
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    source_files = (ROOT / "ws4_outlook/diagnostics.py", ROOT / "ws4_outlook/conformal.py",
                    ROOT / "ws4_outlook/interval_report.py", ROOT / "ws1_harmonize/write_harmonized.py")
    source_hashes = {str(path.relative_to(ROOT)): sha256(path) for path in source_files}
    patch_hash = hashlib.sha256(json.dumps(source_hashes, sort_keys=True).encode()).hexdigest()
    result = {"run_id": "T54-interval-diagnosis-20261115", "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
              "source_revision": revision.stdout.strip() if revision.returncode == 0 else None,
              "source_file_hashes": source_hashes, "source_patch_identity_sha256": patch_hash,
              "environment": {"python": sys.version, "platform": platform.platform(), "pandas": pd.__version__,
                              "numpy": np.__version__, "device": "n/a (read-only; no fit)"},
              "command": "python -m ws4_outlook.diagnostics --overlap-draws 400",
              "configuration": {"interval_report_draws_per_resolution_combo": 4000, "interval_report_seed": 7,
                                "overlap_draws_per_resolution_combo": overlap_draws, "overlap_seed": 5301,
                                "maximum_rectangle_cells": 400},
              "mode": "read-only; no fit or calibration writer", "baseline": str(baseline), "input_hashes": hashes_before,
              "calibration_table_sha256_before_after": [hashes_before["interval_table.json"], hashes_after["interval_table.json"]],
              "month_target": CALENDAR_MONTH, "daily_rows": int(len(daily)),
              "calendar_months_with_daily_oof": int(months.nunique()), "complete_calendar_months": len(complete_months),
              "complete_month_range": [complete_months[0], complete_months[-1]] if complete_months else [],
              "combo_daily_rows": daily.groupby("sat_combo").size().to_dict(),
              "combination_month_min_max_unique_days": {str(k): [int(v.min()), int(v.max())] for k, v in
                                                           month_day_counts.groupby(level=0).agg(["min", "max"]).iterrows()},
              "interval_report": detailed, "traced_group_diagnostics": traced_groups,
              "oof_support": support, "rectangle_overlap": overlap,
              "rectangle_draws_file": str(output / "rectangle-draws.jsonl"),
              "named_area_support": named_area_support(oof, cells),
              "limitations": ["Test blocks cover only part of each named AOI; no full-area claim is supported.",
                              "Rectangle draws overlap and share blocks/years; they are not independent physical observations.",
                              "The saved interval table is a development baseline and is not modified."]}
    (output / "diagnosis.json").write_text(json.dumps(result, indent=2, default=str, allow_nan=False) + "\n", encoding="utf-8")
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--overlap-draws", type=int, default=400)
    args = parser.parse_args(argv)
    result = diagnose(args.baseline, args.output, args.overlap_draws)
    print(json.dumps({"run_id": result["run_id"], "month_target": result["month_target"],
                      "interval_acceptance_status": result["interval_report"]["acceptance_status"],
                      "interval_stop_rows": len(result["interval_report"]["stop_rows"]),
                      "complete_calendar_months": result["complete_calendar_months"],
                      "rectangle_draws": result["rectangle_draws_file"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
