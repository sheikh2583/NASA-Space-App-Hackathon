"""Interval lookup from interval_table.json (contracts/json_schemas/interval_table.schema.json)."""
from __future__ import annotations

import json
import math


def load_table(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def _entries(table, res, sat_combo="terra_aqua"):
    entries = [e for e in table.get("entries", []) if e["res"] == res]
    scoped = [e for e in entries if e.get("stratum", "").startswith("input:")]
    if scoped:
        prefix = f"input:{sat_combo}|"
        return [e for e in scoped if e["stratum"].startswith(prefix)]
    return entries


def _plain_stratum(entry):
    value = entry["stratum"]
    return value.split("|", 1)[1] if value.startswith("input:") and "|" in value else value


def _pick_bin(entries, value):
    """Smallest bin whose max_value >= value; null max_value is the open top bin."""
    bounded = sorted([e for e in entries if e.get("max_value") is not None], key=lambda e: e["max_value"])
    for e in bounded:
        if value <= e["max_value"]:
            return e
    open_top = [e for e in entries if e.get("max_value") is None]
    return open_top[0] if open_top else (bounded[-1] if bounded else None)


def _entries_for(entries, candidates):
    for s in list(candidates) + ["all"]:
        match = [e for e in entries if _plain_stratum(e) == s]
        if match:
            return match
    return [max(entries, key=lambda e: (e["hi_mult"] - e["lo_mult"], e["hi_add"]))]


def apply_interval_many(values, strata_rows, res: str, table: dict, sat_combo: str = "terra_aqua"):
    """Vectorized apply_interval: values (array) and one strata tuple per row (most specific first).
    Same rules as apply_interval; returns (lo, hi) arrays (NaN where res has no table)."""
    import numpy as np
    v = np.asarray(values, dtype="float64")
    lo, hi = np.full(v.shape, np.nan), np.full(v.shape, np.nan)
    entries = _entries(table, res, sat_combo)
    if not entries:
        return lo, hi
    keys = {}
    for i, k in enumerate(strata_rows):
        keys.setdefault(tuple(k), []).append(i)
    for k, idx in keys.items():
        idx = np.asarray(idx)
        for is_zero in (True, False):
            selected = idx[v[idx] <= 0] if is_zero else idx[v[idx] > 0]
            if not selected.size:
                continue
            candidates = (["zero_prediction"] if is_zero else []) + list(k)
            match = _entries_for(entries, candidates)
            bounded = sorted([e for e in match if e.get("max_value") is not None], key=lambda e: e["max_value"])
            top = [e for e in match if e.get("max_value") is None] or bounded[-1:]
            bins = bounded + top[:1]
            if not bins:
                continue
            edges = np.array([e["max_value"] for e in bounded] + [np.inf])
            b = np.searchsorted(edges, v[selected], side="left").clip(0, len(bins) - 1)
            lm = np.array([e["lo_mult"] for e in bins])[b]
            hm = np.array([e["hi_mult"] for e in bins])[b]
            ha = np.array([e["hi_add"] for e in bins])[b]
            lo[selected] = np.minimum(np.maximum(0.0, v[selected] * lm), v[selected])
            hi[selected] = np.maximum(v[selected] * hm + ha, v[selected])
    return lo, hi


def apply_interval(value: float, res: str, stratum, table: dict, sat_combo: str = "terra_aqua"):
    """(lo, hi) for a value. stratum: a string or a list ordered most specific first.

    Tries each stratum in order, then 'all', then the widest entry for the resolution.
    lo = value * lo_mult, hi = value * hi_mult + hi_add. Day resolution has no table: returns (None, None).
    """
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return (None, None)
    entries = _entries(table, res, sat_combo)
    if not entries:
        return (None, None)
    candidates = (["zero_prediction"] if value <= 0 else []) + ([stratum] if isinstance(stratum, str) else list(stratum or [])) + ["all"]
    chosen = None
    for s in candidates:
        match = [e for e in entries if e["stratum"] == s]
        if match:
            chosen = _pick_bin(match, value)
            break
    if chosen is None:
        chosen = max(entries, key=lambda e: (e["hi_mult"] - e["lo_mult"], e["hi_add"]))
    lo = max(0.0, value * chosen["lo_mult"])
    hi = value * chosen["hi_mult"] + chosen["hi_add"]
    return (min(lo, value), max(hi, value))
