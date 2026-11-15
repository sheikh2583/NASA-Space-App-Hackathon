"""Network-free regression tests for the MCD64A1 acquisition boundary."""
from __future__ import annotations

import datetime as dt
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

import pytest

from tools import get_mcd64a1 as mcd


HDF = mcd.HDF4_MAGIC + b"a small HDF body"
ITEM = {
    "id": "G123-CMR",
    "collection_concept_id": "C123-PROV",
    "producer_granule_id": "MCD64A1.A2018365.h25v06.061.2019001000000.hdf",
    "title": "MCD64A1.A2018365.h25v06.061.2019001000000.hdf",
    "time_start": "2018-12-31T00:00:00.000Z",
    "granule_size": f"{len(HDF) / 1024**2:.12f}",
    "links": [{"rel": "http://esipfed.org/ns/fedsearch/1.1/data#", "href": "https://data.example/file.hdf"}],
}


class Response:
    def __init__(self, body: bytes, headers=None):
        self.body = body
        self.headers = headers or {}
        self.offset = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        if size < 0:
            size = len(self.body) - self.offset
        part = self.body[self.offset:self.offset + size]
        self.offset += len(part)
        return part


class FakeOpener:
    def __init__(self, pages=None, payload=HDF):
        self.pages = list(pages or [(self._feed([ITEM], 1), {})])
        self.payload = payload
        self.requests = []

    @staticmethod
    def _feed(items, hits):
        import json
        return json.dumps({"feed": {"hits": hits, "entry": items}}).encode()

    def open(self, request, timeout=60):
        self.requests.append(request)
        if urlsplit(request.full_url).hostname == "cmr.earthdata.nasa.gov":
            if urlsplit(request.full_url).path.endswith("collections.json"):
                return Response(self._feed([{"id": "C123-PROV", "short_name": "MCD64A1", "version_id": "061"}], 1))
            body, headers = self.pages.pop(0)
            return Response(body, headers)
        return Response(self.payload)


def args():
    return (dt.date(2018, 12, 31), dt.date(2019, 1, 2), (88.0, 20.0, 92.0, 27.0))


def test_read_token_uses_process_then_only_token_from_env_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("OTHER=value\nEARTHDATA_TOKEN='test-token'\n", encoding="utf-8")
    assert mcd.read_token({"EARTHDATA_TOKEN": "process-token"}, env_file) == "process-token"
    assert mcd.read_token({}, env_file) == "test-token"
    env_file.write_text("EARTHDATA_TOKEN=\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Set EARTHDATA_TOKEN"):
        mcd.read_token({}, env_file)


def test_query_filters_collection_and_preserves_cross_year_date_window():
    fake = FakeOpener()
    start, end, bbox = args()
    assert mcd.cmr_query(start, end, bbox, "test-token", opener=fake) == [ITEM]
    query = parse_qs(urlsplit(fake.requests[1].full_url).query)
    assert query["collection_concept_id"] == ["C123-PROV"]
    assert query["short_name"] == ["MCD64A1"]
    assert query["version"] == ["061"]
    assert query["provider"] == ["LPCLOUD"]
    assert query["temporal"] == ["2018-12-31T00:00:00Z,2019-01-02T23:59:59Z"]
    assert "test-token" not in fake.requests[1].full_url
    assert fake.requests[1].get_header("Authorization") == "Bearer test-token"


def test_lpdaac_catalog_size_estimate_uses_binary_megabytes():
    assert mcd._expected_size({"granule_size": "3.36979"}) == round(3.36979 * 1024**2)


def test_cmr_search_after_paginates_and_deduplicates():
    first = FakeOpener._feed([ITEM], 2)
    second_item = {**ITEM, "id": "G124-CMR", "producer_granule_id": "MCD64A1.A2019001.h25v06.061.2019002000000.hdf",
                   "title": "MCD64A1.A2019001.h25v06.061.2019002000000.hdf"}
    second = FakeOpener._feed([ITEM, second_item], 2)
    fake = FakeOpener([(first, {"CMR-Search-After": "page-two"}), (second, {})])
    results = mcd.cmr_query(*args(), "test-token", opener=fake)
    assert [item["id"] for item in results] == ["G123-CMR", "G124-CMR"]
    assert fake.requests[2].get_header("Cmr-search-after") == "page-two"
    assert "CMR-Search-After" not in parse_qs(urlsplit(fake.requests[2].full_url).query)


def test_missing_pagination_cursor_fails_instead_of_truncating():
    fake = FakeOpener([(FakeOpener._feed([ITEM], mcd.PAGE_SIZE + 1), {})])
    with pytest.raises(RuntimeError, match="pagination"):
        mcd.cmr_query(*args(), "test-token", opener=fake)


def test_acquire_replaces_corrupt_partial_and_records_hash(tmp_path):
    fake = FakeOpener()
    out = tmp_path / "raw"
    manifest = tmp_path / "manifest.jsonl"
    filename = ITEM["producer_granule_id"]
    out.mkdir()
    (out / filename).write_bytes(b"<html>bad download</html>")
    paths, messages = mcd.acquire(*args(), "Bangladesh sample", out, "test-token", opener=fake, manifest=manifest)
    assert paths == [out / filename]
    assert mcd._hdf4(paths[0])
    assert not (out / (filename + ".part")).exists()
    assert "downloaded" in messages[0]
    entry = __import__("json").loads(manifest.read_text(encoding="utf-8"))
    assert entry["collection"] == "MCD64A1.061"
    assert entry["region"] == "Bangladesh sample"
    assert entry["sha256"] == mcd._sha256(paths[0])


def test_existing_valid_file_is_reused_without_download(tmp_path):
    fake = FakeOpener()
    out = tmp_path / "raw"
    out.mkdir()
    target = out / ITEM["producer_granule_id"]
    target.write_bytes(HDF)
    paths, messages = mcd.acquire(*args(), "sample", out, "test-token", opener=fake,
                                  manifest=tmp_path / "manifest.jsonl")
    assert paths == [target]
    assert "reused" in messages[0]
    assert len(fake.requests) == 2  # collection + inventory; existing product was not fetched.


def test_invalid_download_removes_part_and_never_keeps_html(tmp_path):
    fake = FakeOpener(payload=b"<html>Earthdata login</html>")
    out = tmp_path / "raw"
    with pytest.raises(RuntimeError, match="not an HDF4"):
        mcd.acquire(*args(), "sample", out, "test-token", opener=fake,
                    manifest=tmp_path / "manifest.jsonl")
    assert not list(out.glob("*.part"))
    assert not list(out.glob("*.hdf"))


def test_expired_token_error_is_redacted_and_does_not_include_url(tmp_path):
    class Expired(FakeOpener):
        def open(self, request, timeout=60):
            if urlsplit(request.full_url).hostname == "cmr.earthdata.nasa.gov":
                raise HTTPError(request.full_url, 401, "expired", Message(), None)
            return super().open(request, timeout)

    with pytest.raises(RuntimeError) as caught:
        mcd.cmr_query(*args(), "private-token", opener=Expired())
    assert "private-token" not in str(caught.value)
    assert "cmr.earthdata.nasa.gov" not in str(caught.value)
    assert "HTTP 401" in str(caught.value)


def test_redirect_drops_authorization_on_host_change():
    handler = mcd._SafeRedirect()
    original = Request("https://data.example/start", headers={"Authorization": "Bearer private-token"})
    redirect = handler.redirect_request(original, None, 302, "Found", {}, "https://bucket.example/object")
    assert redirect is not None
    assert redirect.get_header("Authorization") is None
    same_host = handler.redirect_request(original, None, 302, "Found", {}, "https://data.example/next")
    assert same_host is not None
    assert same_host.get_header("Authorization") == "Bearer private-token"


def test_byte_budget_blocks_before_replacing_existing_data(tmp_path):
    fake = FakeOpener()
    out = tmp_path / "raw"
    out.mkdir()
    target = out / ITEM["producer_granule_id"]
    target.write_bytes(HDF)
    with pytest.raises(RuntimeError, match="budget"):
        mcd.acquire(*args(), "sample", out, "test-token", opener=fake,
                    manifest=tmp_path / "manifest.jsonl", max_bytes=2)
    assert target.read_bytes() == HDF

