import json

from scripts.runtime_bundle import EVIDENCE, pack, validate_archive
from scripts.tests.test_runtime_bundle import data_fixture
from scripts import check_render


def test_new_archive_carries_explicit_limited_evidence_and_member_hashes(tmp_path):
    source = tmp_path / "data"
    data_fixture(source)
    archive = tmp_path / "runtime.zip"
    pack(source, archive)
    names = validate_archive(archive)
    assert EVIDENCE in names
    import zipfile
    with zipfile.ZipFile(archive) as package:
        evidence = json.loads(package.read(EVIDENCE))
        assert evidence["disposition"] == "limited-research-demo"
        assert evidence["status"] == "missing"
        assert set(evidence["member_hashes"]) == set(names) - {EVIDENCE}


def test_tampered_member_hash_refuses_archive(tmp_path):
    source = tmp_path / "data"
    data_fixture(source)
    archive = tmp_path / "runtime.zip"
    pack(source, archive)
    import zipfile
    changed = tmp_path / "changed.zip"
    with zipfile.ZipFile(archive) as src, zipfile.ZipFile(changed, "w") as dst:
        for name in src.namelist():
            payload = src.read(name)
            if name == "cells.parquet":
                payload += b"tamper"
            dst.writestr(name, payload)
    import pytest
    with pytest.raises(ValueError, match="incompatible"):
        validate_archive(changed)


def test_scientifically_incompatible_archive_is_allowed_only_as_limited_demo(tmp_path):
    source = tmp_path / "data"
    data_fixture(source)
    (source / "validation/intervals_by_stratum.json").write_text(
        json.dumps({"metrics": {"month_target": "old", "rows": [{"n": 20, "coverage": .9}]}}), encoding="utf-8")
    archive = tmp_path / "runtime.zip"
    pack(source, archive)
    assert "runtime_evidence.json" in validate_archive(archive)
    import zipfile
    with zipfile.ZipFile(archive) as package:
        evidence = json.loads(package.read("runtime_evidence.json"))
    assert evidence["status"] == "incompatible"
    assert evidence["disposition"] == "limited-research-demo"


def test_render_evidence_check_requires_expected_serving_revision(monkeypatch):
    monkeypatch.setattr(check_render, "http", lambda *args, **kwargs: (200, json.dumps({
        "runtime_evidence": {"status": "limited", "disposition": "limited-research-demo", "serving_revision": "abc"}})))
    assert check_render.check_runtime_evidence("https://example.test", "abc")[0]
    assert not check_render.check_runtime_evidence("https://example.test", "old")[0]
