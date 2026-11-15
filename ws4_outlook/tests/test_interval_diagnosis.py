import hashlib

import pandas as pd
import pytest

from ws4_outlook.conformal import _complete_months
from ws4_outlook.diagnostics import (trace_rectangles, traced_group_diagnostics, validate_daily_keys,
                                     validate_oof_keys, verify_hashes_unchanged)


def test_calendar_months_cross_year_and_include_leap_day():
    weeks = pd.date_range("2019-12-30", "2020-03-02", freq="7D")
    months = _complete_months(weeks)
    assert pd.Timestamp("2020-02-01") in months
    assert pd.Timestamp("2020-01-01") in months


def test_missing_week_removes_month_from_complete_calendar_target():
    weeks = pd.date_range("2020-01-27", "2020-03-02", freq="7D").delete(2)
    assert pd.Timestamp("2020-02-01") not in _complete_months(weeks)


def test_duplicate_oof_or_daily_contributors_are_rejected():
    row = {"cell_id": 1, "date": "2020-02-03", "sat_combo": "terra_aqua"}
    with pytest.raises(ValueError, match="duplicate"):
        validate_oof_keys(pd.DataFrame([row, row]))
    with pytest.raises(ValueError, match="duplicate"):
        validate_daily_keys(pd.DataFrame([row, row]))


def test_rectangle_sampling_keeps_input_combinations_separate():
    date = pd.Timestamp("2020-02-03")
    oof = pd.DataFrame([
        {"cell_id": 4099503, "date": date, "split": "test", "sat_combo": "terra_aqua", "block_id": 1,
         "y_true_count": 2.0, "y_pred_count": 1.0},
        {"cell_id": 4099503, "date": date, "split": "test", "sat_combo": "terra_only", "block_id": 1,
         "y_true_count": 2.0, "y_pred_count": 9.0},
    ])
    daily = pd.DataFrame(columns=["cell_id", "date", "split", "sat_combo", "block_id", "y_true_count", "y_pred_count"])
    cells = pd.DataFrame([{"cell_id": 4099503, "landcover_dom": "cropland"}])
    table = {"version": "test", "entries": []}
    _, draws = trace_rectangles(oof, daily, cells, table, n=1, seed=7)
    assert {row["sat_combo"] for row in draws} == {"terra_aqua", "terra_only"}
    assert {row["y_pred_count"] for row in draws} == {1.0, 9.0}
    grouped = traced_group_diagnostics(draws)
    assert {row["dimension"] for row in grouped} >= {"activity", "prediction", "zero_activity", "magnitude", "season", "landcover"}
    assert all("unique_blocks" in row and "mean_drawn_cells" in row for row in grouped)


def test_diagnosis_refuses_a_modified_saved_calibration_hash(tmp_path):
    original = b"unchanged interval table"
    path = tmp_path / "interval_table.json"
    path.write_bytes(original)
    before = {"interval_table.json": hashlib.sha256(original).hexdigest()}
    assert verify_hashes_unchanged(tmp_path, before) == before
    path.write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="changed"):
        verify_hashes_unchanged(tmp_path, before)
