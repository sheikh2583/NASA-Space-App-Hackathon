from ws2_platform.api import app as api
from types import SimpleNamespace


def test_validation_metadata_reports_legacy_evidence_as_limited(tmp_path, monkeypatch):
    validation = tmp_path / "validation"
    validation.mkdir()
    monkeypatch.setattr(api, "store", lambda: SimpleNamespace(data_dir=str(tmp_path)))
    body = api.meta_validation()
    evidence = body["runtime_evidence"]
    assert evidence["status"] == "missing"
    assert evidence["disposition"] == "limited-research-demo"
    assert evidence["serving_revision"] == "local"
    assert {"status", "reasons", "scope", "disposition", "serving_revision"} <= evidence.keys()
