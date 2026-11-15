"""Pure, conservative evaluation of scientific evidence carried by a runtime."""
from __future__ import annotations

import math
import re

STATUSES = {"accepted_for_scope", "limited", "stop_condition", "insufficient_support", "missing", "incompatible"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _finite(value) -> bool:
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(_finite(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return all(_finite(v) for v in value)
    return False


def interval_status(piece: dict | None, expected_target: str = "calendar_month_daily_allocation") -> dict:
    """Recompute interval disposition from saved metrics; never trust a headline status alone."""
    if not isinstance(piece, dict):
        return {"status": "missing", "reasons": ["interval evidence is missing"], "stop_rows": []}
    metrics = piece.get("metrics")
    if not isinstance(metrics, dict) or not _finite(metrics):
        return {"status": "incompatible", "reasons": ["interval metrics are missing or non-finite"], "stop_rows": []}
    reasons = []
    target = metrics.get("month_target")
    if target != expected_target:
        reasons.append("monthly target is missing or incompatible")
    rows = metrics.get("rows")
    if not isinstance(rows, list) or not rows:
        return {"status": "missing", "reasons": reasons + ["stratified interval rows are missing"], "stop_rows": []}
    stop_rows = []
    for row in rows:
        if not isinstance(row, dict):
            return {"status": "incompatible", "reasons": ["malformed interval row"], "stop_rows": []}
        n, coverage = row.get("n"), row.get("coverage")
        if isinstance(n, int) and not isinstance(n, bool) and n >= 500 and isinstance(coverage, (float, int)) and coverage < .80:
            stop_rows.append({"stratum": row.get("stratum"), "n": n, "coverage": coverage})
    if stop_rows:
        reasons.append("one or more groups trigger the registered 0.80 stop rule")
    if reasons:
        status = "stop_condition" if stop_rows else "incompatible"
    else:
        support_ok = all(isinstance(r.get("n"), int) and r["n"] >= 500
                         and isinstance(r.get("unique_blocks"), int) and r["unique_blocks"] >= 5
                         and isinstance(r.get("unique_years"), int) and r["unique_years"] >= 5 for r in rows)
        criteria_ok = all(isinstance(r.get("coverage"), (int, float)) and .85 <= r["coverage"] <= .95
                          and isinstance(r.get("coverage_ci"), list) and len(r["coverage_ci"]) == 2
                          and .85 <= r["coverage_ci"][0] <= r["coverage_ci"][1] <= .95
                          and r.get("baseline_comparison") == "pass" for r in rows)
        if not support_ok:
            status, reasons = "insufficient_support", ["one or more groups lack registered block/year support"]
        elif criteria_ok:
            status, reasons = "accepted_for_scope", []
        else:
            status, reasons = "limited", ["coverage uncertainty or baseline comparison gate is missing or fails"]
    return {"status": status, "reasons": reasons, "stop_rows": stop_rows, "month_target": target}


def evaluate_runtime_evidence(evidence: dict | None, member_hashes: dict[str, str] | None = None) -> dict:
    """Check a runtime evidence record against bundled-member hashes and conservative status rules."""
    if not isinstance(evidence, dict):
        return {"status": "missing", "reasons": ["runtime evidence is missing"], "scope": []}
    reasons = []
    if evidence.get("schema_version") != "1.0":
        reasons.append("unsupported runtime evidence schema")
    status = evidence.get("status")
    if status not in STATUSES:
        reasons.append("unknown declared status")
        status = "incompatible"
    disposition = evidence.get("disposition")
    if status in {"missing", "incompatible", "stop_condition", "insufficient_support", "limited"} and disposition != "limited-research-demo":
        reasons.append("unaccepted evidence lacks explicit limited-research-demo disposition")
        status = "incompatible"
    if not isinstance(evidence.get("scope"), list) or not evidence["scope"]:
        reasons.append("accepted scope is missing")
        status = "missing" if status != "incompatible" else status
    hashes = evidence.get("member_hashes")
    if not isinstance(hashes, dict) or any(not isinstance(v, str) or not SHA256_RE.fullmatch(v) for v in hashes.values()):
        reasons.append("runtime member hashes are missing or malformed")
        status = "incompatible"
    elif member_hashes is not None and hashes != member_hashes:
        reasons.append("runtime member hashes do not match the archive")
        status = "incompatible"
    if not _finite(evidence):
        reasons.append("runtime evidence contains non-finite values")
        status = "incompatible"
    science = evidence.get("scientific_acceptance")
    if isinstance(science, dict) and "interval_report" in science:
        recomputed = interval_status({"metrics": science["interval_report"]})
        science_status = recomputed["status"]
        if science_status != status:
            reasons.append("declared disposition disagrees with recomputed interval acceptance")
            status = "incompatible"
    elif status == "accepted_for_scope":
        reasons.append("accepted disposition has no metrics to recompute")
        status = "incompatible"
    source_revision = evidence.get("producer_revision")
    if status == "accepted_for_scope" and (not isinstance(source_revision, str) or not source_revision):
        reasons.append("accepted evidence has no producer revision")
        status = "incompatible"
    return {"status": status, "reasons": reasons, "scope": evidence.get("scope", []),
            "producer_revision": source_revision, "serving_revision": evidence.get("serving_revision"),
            "disposition": disposition}


def legacy_runtime_disposition() -> dict:
    """Explicitly classify a supported old archive that has no provenance member."""
    return {"status": "missing", "reasons": ["legacy runtime has no producer provenance or scoped acceptance record"],
            "scope": [], "disposition": "limited-research-demo"}
