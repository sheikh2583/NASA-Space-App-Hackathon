import numpy as np
import pandas as pd
import pytest

from common.intervals import apply_interval, apply_interval_many
from ws4_outlook import conformal


def test_finite_sample_quantile_edges_and_nonfinite_rejection():
    assert conformal._conformal_q(np.array([3.0])) == 3.0
    scores = np.arange(1.0, 11.0)
    assert conformal._conformal_q(scores) == 10.0  # ceil((n + 1) * .9), one-based rank
    with pytest.raises(ValueError, match="finite"):
        conformal._conformal_q(np.array([1.0, np.nan]))


def test_combination_scoped_entries_select_independent_bands():
    table = {"version": "v2-conformal-combo", "level": .9, "entries": [
        {"res": "week", "stratum": "input:terra_aqua|all", "max_value": None,
         "lo_mult": .5, "hi_mult": 2.0, "hi_add": 1.0},
        {"res": "week", "stratum": "input:terra_only|all", "max_value": None,
         "lo_mult": .25, "hi_mult": 3.0, "hi_add": 4.0},
    ]}
    aa = apply_interval(2, "week", ["missing"], table, sat_combo="terra_aqua")
    terra = apply_interval(2, "week", ["missing"], table, sat_combo="terra_only")
    assert aa == (1.0, 5.0)
    assert terra == (.5, 10.0)


def test_zero_prediction_fallback_is_prediction_time_only_and_uses_calibration_support():
    sparse = pd.DataFrame({"pred": [0.0] * 29, "y": list(range(29))})
    result = conformal._zero_prediction_fallback(sparse)
    assert result == {"n": 29, "status": "sparse_fallback", "hi_add": None}
    supported = pd.DataFrame({"pred": [0.0] * 30, "y": list(range(30))})
    result = conformal._zero_prediction_fallback(supported)
    assert result["status"] == "fitted" and result["n"] == 30 and result["hi_add"] == pytest.approx(27.0)
    table = {"version": "v2-conformal-combo-zero", "level": .9, "entries": [
        {"res": "week", "stratum": "input:terra_aqua|zero_prediction", "max_value": None,
         "lo_mult": 0.0, "hi_mult": 1.0, "hi_add": result["hi_add"]},
        {"res": "week", "stratum": "input:terra_aqua|all", "max_value": None,
         "lo_mult": 0.0, "hi_mult": 2.0, "hi_add": 1.0},
    ]}
    lo, hi = apply_interval_many([0.0, 2.0], [("all",), ("all",)], "week", table)
    assert list(lo) == [0.0, 0.0] and list(hi) == pytest.approx([27.0, 5.0])


def test_combo_zero_candidate_records_sparse_calibration_fallback(monkeypatch):
    calls = []

    def samples(oof, cells, split, n, res, rng, *, sat_combo, max_side=10, daily=None):
        calls.append((split, res, sat_combo))
        nrows = 40 if split == "calib" else 8
        pred = 1.0 if split == "calib" else 2.0
        truth = pred + (1 if sat_combo == "terra_aqua" else 4)
        return pd.DataFrame({"pred": [pred] * nrows, "y": [truth] * nrows,
                             "landcover": ["cropland"] * nrows, "season": ["q1"] * nrows,
                             "n_cells": [4] * nrows, "drawn_n_cells": [4] * nrows,
                             "score": [abs(np.log((truth + 1) / (pred + 1)))] * nrows})

    monkeypatch.setattr(conformal, "_aggregate_samples", samples)
    table, report = conformal.build_table(pd.DataFrame(), pd.DataFrame(), n_samples=40, daily=pd.DataFrame(), candidate="combo_zero")
    assert set(row["sat_combo"] for row in report["zero_prediction_support"]) == {"terra_aqua", "terra_only"}
    assert all(row["status"] == "sparse_fallback" for row in report["zero_prediction_support"])
    assert {"calib", "test"} == {split for split, _, _ in calls}
    assert all(any(entry["stratum"].startswith(f"input:{combo}|") for entry in table["entries"])
               for combo in ("terra_aqua", "terra_only"))
    from common.intervals import apply_interval
    aa = apply_interval(1, "week", "landcover:cropland|season:q1", table, sat_combo="terra_aqua")
    terra = apply_interval(1, "week", "landcover:cropland|season:q1", table, sat_combo="terra_only")
    assert aa[1] < terra[1]


def test_legacy_build_table_keeps_the_frozen_global_candidate(monkeypatch):
    combos = []

    def samples(oof, cells, split, n, res, rng, *, sat_combo, max_side=10, daily=None):
        combos.append(sat_combo)
        pred = 1.0
        truth = 2.0
        return pd.DataFrame({"pred": [pred] * 40, "y": [truth] * 40, "landcover": ["cropland"] * 40,
                             "season": ["q1"] * 40, "n_cells": [4] * 40, "drawn_n_cells": [4] * 40,
                             "score": [abs(np.log((truth + 1) / (pred + 1)))] * 40})

    monkeypatch.setattr(conformal, "_aggregate_samples", samples)
    table, _ = conformal.build_table(pd.DataFrame(), pd.DataFrame(), n_samples=40, daily=pd.DataFrame())
    assert table["version"] == "v1-conformal"
    assert set(combos) == {"terra_aqua"}
    assert not any(entry["stratum"].startswith("input:") for entry in table["entries"])


def test_calendar_month_support_crosses_year_and_excludes_a_missing_week():
    weeks = pd.date_range("2019-12-30", "2020-03-02", freq="7D")
    assert pd.Timestamp("2020-02-01") in conformal._complete_months(weeks)
    missing = weeks.delete(list(weeks).index(pd.Timestamp("2020-02-10")))
    assert pd.Timestamp("2020-02-01") not in conformal._complete_months(missing)


def test_legacy_table_behavior_remains_global_for_existing_runtimes():
    legacy = {"version": "v1-conformal", "level": .9, "entries": [
        {"res": "week", "stratum": "all", "max_value": None, "lo_mult": .5, "hi_mult": 2.0, "hi_add": 1.0}]}
    assert apply_interval(2, "week", "missing", legacy, sat_combo="terra_only") == (1.0, 5.0)
