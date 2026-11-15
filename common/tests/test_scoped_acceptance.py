import copy
import hashlib

from common.acceptance import evaluate_runtime_evidence, interval_status, legacy_runtime_disposition


def interval_piece(**overrides):
    row = {"stratum": "week | terra_aqua | positive", "n": 600, "coverage": .90,
           "unique_blocks": 5, "unique_years": 5, "coverage_ci": [.86, .94], "baseline_comparison": "pass"}
    metrics = {"month_target": "calendar_month_daily_allocation", "rows": [row]}
    metrics.update(overrides)
    return {"metrics": metrics}


def test_recomputes_stop_rows_even_when_engineering_qa_is_green():
    piece = interval_piece()
    piece["metrics"]["rows"][0]["coverage"] = .60
    result = interval_status(piece)
    assert result["status"] == "stop_condition"
    assert result["stop_rows"][0]["n"] == 600


def test_wrong_month_target_is_incompatible():
    result = interval_status(interval_piece(month_target="legacy_whole_weeks_by_monday_month"))
    assert result["status"] == "incompatible"


def test_accepted_scope_needs_supported_metrics_and_matching_hashes():
    raw_hash = hashlib.sha256(b"artifact").hexdigest()
    report = interval_piece()["metrics"]
    evidence = {"schema_version": "1.0", "status": "accepted_for_scope", "disposition": "accepted",
               "scope": ["week | terra_aqua | positive"], "producer_revision": "abc123",
               "member_hashes": {"validation/intervals_by_stratum.json": raw_hash},
               "scientific_acceptance": {"interval_report": report}}
    assert evaluate_runtime_evidence(evidence, evidence["member_hashes"])["status"] == "accepted_for_scope"
    stale = evaluate_runtime_evidence(evidence, {"validation/intervals_by_stratum.json": "0" * 64})
    assert stale["status"] == "incompatible"


def test_declared_accepted_cannot_override_failed_metrics_or_missing_evidence():
    evidence = {"schema_version": "1.0", "status": "accepted_for_scope", "disposition": "accepted",
                "scope": ["demo"], "producer_revision": "abc123", "member_hashes": {},
                "scientific_acceptance": {"interval_report": interval_piece(month_target="old")["metrics"]}}
    result = evaluate_runtime_evidence(evidence, {})
    assert result["status"] == "incompatible"
    assert "disagrees" in " ".join(result["reasons"])
    assert legacy_runtime_disposition()["status"] == "missing"


def test_missing_and_nonfinite_metrics_never_pass():
    assert interval_status(None)["status"] == "missing"
    bad = interval_piece()
    bad["metrics"]["rows"][0]["coverage"] = float("nan")
    assert interval_status(bad)["status"] == "incompatible"
    evidence = {"schema_version": "1.0", "status": "limited", "disposition": "limited-research-demo",
                "scope": ["demo"], "member_hashes": {"x": "0" * 64}, "scientific_acceptance": {"value": float("inf")}}
    assert evaluate_runtime_evidence(evidence)["status"] == "incompatible"
