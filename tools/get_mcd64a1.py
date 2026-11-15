"""Acquire selected NASA MCD64A1.061 burned-area granules from Earthdata CMR.

The Earthdata token is read from EARTHDATA_TOKEN in the process environment or
the repository .env file. It is never written to output or forwarded across a
host redirect. Raw products and the local acquisition manifest stay under data/.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
CMR = "https://cmr.earthdata.nasa.gov/search/granules.json"
CMR_COLLECTIONS = "https://cmr.earthdata.nasa.gov/search/collections.json"
PROVIDER = "LPCLOUD"
SHORT_NAME = "MCD64A1"
VERSION = "061"
PAGE_SIZE = 2000
CHUNK_SIZE = 1024 * 1024
HDF4_MAGIC = b"\x0e\x03\x13\x01"


def read_token(env: dict[str, str] | None = None, env_file: Path | None = None) -> str:
    """Read only EARTHDATA_TOKEN; never include file contents in an error."""
    values = os.environ if env is None else env
    token = values.get("EARTHDATA_TOKEN", "").strip()
    if token:
        return token.strip('"').strip("'").strip()
    path = ROOT / ".env" if env_file is None else env_file
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == "EARTHDATA_TOKEN":
                token = value.strip().strip('"').strip("'").strip()
                if token:
                    return token
    except FileNotFoundError:
        pass
    except OSError:
        raise RuntimeError("Could not read the local Earthdata credential file.") from None
    raise RuntimeError("Set EARTHDATA_TOKEN in the process environment or local .env file.")


def _date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from None


def _bbox(value: str) -> tuple[float, float, float, float]:
    try:
        coords = tuple(float(item) for item in value.split(","))
    except ValueError:
        coords = ()
    if (len(coords) != 4 or not all(map(lambda n: -180 <= n <= 180, (coords[0], coords[2])))
            or not all(map(lambda n: -90 <= n <= 90, (coords[1], coords[3])))
            or coords[0] >= coords[2] or coords[1] >= coords[3]):
        raise argparse.ArgumentTypeError("bbox must be min_lon,min_lat,max_lon,max_lat")
    return coords  # type: ignore[return-value]


class _SafeRedirect(HTTPRedirectHandler):
    """Keep bearer authorization on the original HTTPS host only."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is None:
            return None
        old = urlsplit(req.full_url)
        new = urlsplit(newurl)
        if (old.scheme != "https" or new.scheme != "https" or
                (old.hostname or "").lower() != (new.hostname or "").lower()):
            redirected.remove_header("Authorization")
            redirected.remove_header("authorization")
        return redirected


def _opener():
    return build_opener(_SafeRedirect())


def _open(opener, request: Request, timeout: float = 60):
    try:
        return opener.open(request, timeout=timeout)
    except HTTPError as exc:
        raise RuntimeError(f"Earthdata request failed with HTTP {exc.code}.") from None
    except (URLError, OSError, TimeoutError) as exc:
        reason = getattr(exc, "reason", exc)
        raise RuntimeError(f"Earthdata request failed ({type(reason).__name__}); check network access and token validity.") from None


def _catalog_json(opener, request: Request) -> tuple[dict, object]:
    """Read a CMR page with bounded retries for transient connection/read timeouts."""
    for attempt in range(3):
        try:
            with _open(opener, request) as response:
                body = response.read()
                headers = response.headers
            return json.loads(body.decode("utf-8")), headers
        except (OSError, TimeoutError, UnicodeError, json.JSONDecodeError):
            if attempt == 2:
                raise RuntimeError("Earthdata catalog request timed out or returned unreadable data.") from None
            time.sleep(1 + attempt)
        except RuntimeError as exc:
            if "HTTP 401" in str(exc) or "HTTP 403" in str(exc) or attempt == 2:
                raise
            time.sleep(1 + attempt)
    raise RuntimeError("Earthdata catalog request failed.")


def _collection_concept_id(token: str, opener) -> str:
    query = urlencode({"short_name": SHORT_NAME, "version": VERSION, "provider": PROVIDER, "page_size": "2"})
    request = Request(f"{CMR_COLLECTIONS}?{query}", headers={"Accept": "application/json", "Authorization": f"Bearer {token}"})
    payload, _ = _catalog_json(opener, request)
    entries = payload.get("feed", {}).get("entry", [])
    matches = [entry for entry in entries
               if entry.get("short_name") == SHORT_NAME
               and str(entry.get("version_id", entry.get("version", ""))) == VERSION
               and entry.get("id")]
    if len(matches) != 1:
        raise RuntimeError("Could not resolve one exact MCD64A1.061 collection in CMR.")
    return str(matches[0]["id"])


def cmr_query(start: dt.date, end: dt.date, bbox: tuple[float, float, float, float], token: str,
              opener=None) -> list[dict]:
    """Return all matching granules for the exact collection and requested space/time."""
    if start > end:
        raise ValueError("start date must not be after end date")
    opener = opener or _opener()
    collection_id = _collection_concept_id(token, opener)
    params = {
        "collection_concept_id": collection_id,
        "short_name": SHORT_NAME,
        "version": VERSION,
        "provider": PROVIDER,
        "temporal": f"{start.isoformat()}T00:00:00Z,{end.isoformat()}T23:59:59Z",
        "bounding_box": ",".join(f"{part:g}" for part in bbox),
        "page_size": str(PAGE_SIZE),
        "sort_key[]": "start_date",
    }
    after = None
    seen_tokens: set[str] = set()
    granules: dict[str, dict] = {}
    while True:
        query = dict(params)
        url = f"{CMR}?{urlencode(query, doseq=True)}"
        headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
        if after:
            headers["CMR-Search-After"] = after
        request = Request(url, headers=headers)
        payload, headers = _catalog_json(opener, request)
        next_after = headers.get("CMR-Search-After")
        total = payload.get("feed", {}).get("hits")
        entries = payload.get("feed", {}).get("entry", [])
        for item in entries:
            if item.get("collection_concept_id") != collection_id:
                continue
            granule_id = item.get("id")
            if granule_id:
                granules[str(granule_id)] = item
        if not next_after:
            if isinstance(total, int) and total > PAGE_SIZE:
                raise RuntimeError("Earthdata catalog pagination did not return a continuation token.")
            break
        if next_after in seen_tokens:
            raise RuntimeError("Earthdata catalog repeated a pagination token.")
        seen_tokens.add(next_after)
        after = next_after
    return sorted(granules.values(), key=lambda item: (item.get("time_start", ""), item.get("id", "")))


def _download_href(item: dict) -> str:
    links = item.get("links", [])
    for link in links:
        rel = str(link.get("rel", ""))
        href = link.get("href")
        if (href and "data#" in rel and urlsplit(href).scheme == "https"
                and urlsplit(href).path.lower().endswith(".hdf")):
            return href
    raise RuntimeError("A matching catalog granule has no HTTPS data link.")


def _filename(item: dict) -> str:
    name = str(item.get("producer_granule_id") or item.get("title") or "")
    name = Path(name).name
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise RuntimeError("Catalog returned an unsafe or unexpected granule name.")
    if not name.lower().endswith(".hdf"):
        name += ".hdf"
    if not name.lower().endswith(".hdf"):
        raise RuntimeError("Catalog returned an unsafe or unexpected granule name.")
    if not name.startswith("MCD64A1.A") or ".061." not in name:
        raise RuntimeError("Catalog granule did not match MCD64A1 collection 061 naming.")
    return name


def _hdf4(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            return stream.read(4) == HDF4_MAGIC and path.stat().st_size > 4
    except OSError:
        return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _expected_size(item: dict) -> int | None:
    try:
        # LP DAAC's CMR granule_size values match binary MiB when checked against fetched files.
        value = float(item["granule_size"])
        return round(value * 1024**2) if value > 0 else None
    except (KeyError, TypeError, ValueError):
        return None


def _size_matches(path: Path, expected: int | None) -> bool:
    return expected is None or abs(path.stat().st_size - expected) <= max(150_000, expected * 0.002)


def _stream_file(opener, href: str, token: str, target: Path, expected: int | None, max_bytes: int) -> str:
    part = target.with_name(target.name + ".part")
    digest = hashlib.sha256()
    size = 0
    try:
        request = Request(href, headers={"Authorization": f"Bearer {token}"})
        with _open(opener, request, timeout=90) as response, part.open("wb") as output:
            first = response.read(4)
            if first != HDF4_MAGIC:
                raise RuntimeError("Downloaded object is not an HDF4 file; authentication or source response may be invalid.")
            output.write(first)
            digest.update(first)
            size = len(first)
            while True:
                chunk = response.read(CHUNK_SIZE)
                if not chunk:
                    break
                if size + len(chunk) > max_bytes:
                    raise RuntimeError("Download would exceed the declared byte budget.")
                output.write(chunk)
                digest.update(chunk)
                size += len(chunk)
            output.flush()
            os.fsync(output.fileno())
        if size <= 4:
            raise RuntimeError("Downloaded file was empty or truncated.")
        if expected is not None and abs(size - expected) > max(150_000, expected * 0.002):
            raise RuntimeError(f"Downloaded size {size} bytes differed from CMR estimate {expected} bytes.")
        os.replace(part, target)
        return digest.hexdigest()
    except (TimeoutError, OSError):
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeError("Granule transfer timed out or failed; the partial file was removed and can be retried.") from None
    except Exception:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
        raise


_manifest_lock = Lock()


def _manifest(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, sort_keys=True, ensure_ascii=True) + "\n"
    with _manifest_lock:
        with path.open("a", encoding="utf-8", newline="") as stream:
            stream.write(line)


def acquire(start: dt.date, end: dt.date, bbox: tuple[float, float, float, float], label: str,
            out_dir: Path, token: str, opener=None, manifest: Path | None = None,
            max_bytes: int = 2 * 1024**3, workers: int = 3) -> tuple[list[Path], list[str]]:
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    if not 1 <= workers <= 4:
        raise ValueError("workers must be between 1 and 4")
    opener = opener or _opener()
    manifest = manifest or out_dir.parent / "mcd64a1-manifest.jsonl"
    items = cmr_query(start, end, bbox, token, opener=opener)
    if not items:
        raise RuntimeError("No MCD64A1.061 granules matched the declared area and date range.")
    jobs = []
    required_bytes = 0
    for item in items:
        name = _filename(item)
        target = out_dir / name
        expected = _expected_size(item)
        if expected is None:
            raise RuntimeError(f"CMR did not publish a size for {name}; refusing an unbounded download.")
        reuse = target.exists() and _hdf4(target) and _size_matches(target, expected)
        allowance = target.stat().st_size if reuse else expected + max(150_000, expected * 0.002)
        required_bytes += allowance
        jobs.append((item, name, target, expected, reuse))
    if required_bytes > max_bytes:
        raise RuntimeError("Complete inventory exceeds the declared byte budget; no new granules were downloaded.")

    def fetch(job):
        item, name, target, expected, reuse = job
        target.parent.mkdir(parents=True, exist_ok=True)
        if reuse:
            digest = _sha256(target)
            action = "reused"
        else:
            print(f"Fetching {name}", flush=True)
            target.unlink(missing_ok=True)
            allowance = expected + max(150_000, expected * 0.002)
            digest = _stream_file(opener, _download_href(item), token, target, expected, allowance)
            action = "downloaded"
        size = target.stat().st_size
        _manifest(manifest, {
            "collection": f"{SHORT_NAME}.{VERSION}",
            "granule_id": item.get("id"),
            "producer_granule_id": name,
            "region": label,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "bbox": list(bbox),
            "bytes": size,
            "sha256": digest,
            "action": action,
        })
        return target, f"{action}: {name} ({size} bytes, sha256 {digest})"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(fetch, jobs))
    results.sort(key=lambda result: result[0].name)
    paths = [path for path, _ in results]
    lines = [line for _, line in results]
    return paths, lines


def inventory(start: dt.date, end: dt.date, bbox: tuple[float, float, float, float], token: str,
              opener=None) -> list[dict]:
    """Return a safe, non-downloading inventory with CMR size estimates."""
    return cmr_query(start, end, bbox, token, opener=opener)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", required=True, help="stable region label for the local manifest")
    parser.add_argument("--bbox", required=True, type=_bbox, help="min_lon,min_lat,max_lon,max_lat")
    parser.add_argument("--start", required=True, type=_date, help="inclusive YYYY-MM-DD")
    parser.add_argument("--end", required=True, type=_date, help="inclusive YYYY-MM-DD; may cross a year boundary")
    parser.add_argument("--out", type=Path, default=ROOT / "data/raw/mcd64a1")
    parser.add_argument("--manifest", type=Path, default=ROOT / "data/raw/mcd64a1-manifest.jsonl")
    parser.add_argument("--max-bytes", type=int, default=2 * 1024**3,
                        help="maximum total bytes for this invocation (default: 2 GiB)")
    parser.add_argument("--workers", type=int, default=3, help="parallel granule downloads, 1–4 (default: 3)")
    parser.add_argument("--inventory-only", action="store_true",
                        help="list matching granules and catalog size estimates without downloading")
    args = parser.parse_args(argv)
    if args.start > args.end:
        parser.error("--start must be on or before --end")
    try:
        token = read_token()
        if args.inventory_only:
            entries = inventory(args.start, args.end, args.bbox, token)
            if not entries:
                print("No MCD64A1.061 granules matched the declared area and dates.")
                return 1
            estimate = sum(_expected_size(item) or 0 for item in entries)
            unknown = sum(_expected_size(item) is None for item in entries)
            print(f"Inventory: {len(entries)} granules; estimated {estimate} bytes; unknown sizes {unknown}.")
            for item in entries:
                print(f"{_filename(item)}\t{_expected_size(item) or 'size unknown'}")
            return 0
        _, lines = acquire(args.start, args.end, args.bbox, args.region, args.out, token,
                           manifest=args.manifest, max_bytes=args.max_bytes, workers=args.workers)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    for line in lines:
        print(line)
    print(f"Acquired {len(lines)} MCD64A1 granules for {args.region}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
