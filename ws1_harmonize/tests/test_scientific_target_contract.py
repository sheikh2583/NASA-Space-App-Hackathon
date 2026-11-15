import numpy as np
import pandas as pd

from ws1_harmonize import features


def test_calendar_lags_do_not_bridge_missing_weeks_and_features_exclude_viirs():
    panel = pd.DataFrame({
        "cell_id": [1000, 1000], "week_start": pd.to_datetime(["2020-01-06", "2020-01-20"]),
        "sat_combo": ["terra_aqua"] * 2, "terra_n": [2.0, 6.0], "aqua_n": [3.0, 4.0],
        "terra_hi": [2.0, 6.0], "aqua_hi": [3.0, 4.0], "terra_frp": [1.0, 1.0], "aqua_frp": [1.0, 1.0],
        "terra_frp_max": [1.0, 1.0], "aqua_frp_max": [1.0, 1.0], "terra_n_day": [1.0, 1.0],
        "aqua_n_day": [1.0, 1.0], "terra_n_night": [1.0, 1.0], "aqua_n_night": [1.0, 1.0],
        "terra_scan_w": [1.0, 1.0], "aqua_scan_w": [1.0, 1.0], "lat_c": [20.0, 20.0], "lon_c": [90.0, 90.0],
        "cropland_frac": [.5, .5], "forest_frac": [.2, .2],
    })
    made = features.build_features(panel)
    assert made.iloc[0].next_modis_n == 0.0 and made.iloc[1].prev_modis_n == 0.0
    features.check_features(features.FEATURES)
    assert not any(name.startswith(("snpp_", "n20_", "n21_")) for name in made.columns)


def test_oof_fit_masks_never_include_calibration_or_test_cells(monkeypatch):
    import sys
    from types import SimpleNamespace
    monkeypatch.setitem(sys.modules, "lightgbm", SimpleNamespace())
    from ws1_harmonize import evaluate

    panel = pd.DataFrame({"cell_id": [1, 2, 3, 4, 5], "week_start": pd.to_datetime(["2020-01-06"] * 5),
                          "snpp_n": [1.] * 5, "snpp_frp": [0.] * 5, "sat_combo": ["terra_aqua"] * 5})
    labels = pd.DataFrame({"cell_id": [1, 2, 3, 4, 5], "split": ["train", "train", "train", "calib", "test"],
                           "buffer": [False] * 5, "block_id": [1, 2, 3, 4, 5]})
    monkeypatch.setattr(evaluate.splits, "labels_frame", lambda ids: labels)
    monkeypatch.setattr(evaluate, "overlap", lambda p: pd.Series(np.ones(len(p), dtype=bool), index=p.index))
    fitted = []

    def fit(p, mask, rounds):
        ids = set(p.loc[mask, "cell_id"].astype(int))
        fitted.append(ids)
        assert ids <= {1, 2, 3}
        return ids

    def predict(model, p, combo, rows=None):
        assert set(p.loc[rows, "cell_id"].astype(int)).isdisjoint(model)
        return np.ones(int(rows.sum())), np.zeros(int(rows.sum()))

    monkeypatch.setattr(evaluate.model, "fit", fit)
    monkeypatch.setattr(evaluate.model, "predict", predict)
    out, _ = evaluate.oof(panel, method="lgbm")
    assert fitted and set(out.loc[out.split == "calib", "cell_id"]) == {4}
    assert set(out.loc[out.split == "test", "cell_id"]) == {5}
    assert set(out.sat_combo) == {"terra_aqua", "terra_only"}
