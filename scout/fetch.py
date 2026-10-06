"""Source downloads for the Orange County scout.

Stdlib only. Every download is streamed to disk, hashed while it streams, and
appended to scout/logs/download-log.jsonl so a run can be reproduced even when
the raw file (too big for git) is not kept.
"""
import datetime
import hashlib
import html
import http.client
import http.cookiejar
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.environ.get("SCOUT_RAW") or os.path.join(ROOT, "scout", "raw")
LOG = os.path.join(ROOT, "scout", "logs", "download-log.jsonl")
UA = "oc-hvac-scout/1.0 (public-data research; no personal contact data)"

SBA_URL = ("https://data.sba.gov/sites/default/files/distribution/"
           "SBA-OCA-2022-07-001/public_150k_plus_240930.csv")
CSLB_URL = "https://www.cslb.ca.gov/onlineservices/dataportal/ContractorList"
DOL_URL = "https://www.askebsa.dol.gov/FOIA%20Files/{y}/Latest/{f}_{y}_Latest.zip"
DOL_YEARS = (2025, 2024, 2023)  # newest first; 2025 is the latest published year

SOURCES = [
    dict(id="SBA-PPP-150K", kind="direct", url=SBA_URL, dest="sba/public_150k_plus_240930.csv",
         name="SBA PPP FOIA, loans $150,000 and over"),
    dict(id="CSLB-MASTER", kind="cslb", url=CSLB_URL, dest="cslb/MasterLicenseData.csv",
         name="California CSLB License Master (contractor licenses)"),
]
for _y in DOL_YEARS:
    for _f, _n in (("F_5500", "Form 5500"), ("F_5500_SF", "Form 5500-SF")):
        SOURCES.append(dict(id=f"DOL-{_f.replace('_', '')}-{_y}".replace("F5500", "5500"),
                            kind="direct", url=DOL_URL.format(y=_y, f=_f),
                            dest=f"dol/{_f}_{_y}_Latest.zip",
                            name=f"DOL EBSA {_n} {_y} Latest"))


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(**kw):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    kw = {"logged_utc": now(), **kw}
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(kw, sort_keys=True) + "\n")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()


def _open(opener, url, data=None, headers=None, method=None, timeout=120):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **(headers or {})},
                                 method=method)
    return opener.open(req, timeout=timeout)


def head(opener, url):
    try:
        with _open(opener, url, method="HEAD", timeout=60) as r:
            return {"status": r.status, "content_length": r.headers.get("Content-Length"),
                    "last_modified": r.headers.get("Last-Modified"), "etag": r.headers.get("ETag"),
                    "content_type": r.headers.get("Content-Type")}
    except urllib.error.HTTPError as e:
        return {"status": e.code}


def _stream(resp, dest, offset=0, gzipped=False):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    h = hashlib.sha256()
    if offset:
        with open(dest, "rb") as f:
            for b in iter(lambda: f.read(1 << 22), b""):
                h.update(b)
    n = offset
    t0 = time.time()
    z = zlib.decompressobj(16 + zlib.MAX_WBITS) if gzipped else None
    with open(dest, "ab" if offset else "wb") as f:
        while True:
            try:
                b = resp.read(1 << 20)
            except http.client.IncompleteRead as e:  # server or tunnel closed early
                b = e.partial
                if z:
                    b = z.decompress(b)
                f.write(b)
                raise IOError(f"truncated stream after {n + len(b)} bytes, {time.time() - t0:.0f}s")
            if not b:
                break
            if z:
                b = z.decompress(b)
            f.write(b)
            h.update(b)
            n += len(b)
    return n, h.hexdigest()


def _hidden(page):
    out = {}
    for m in re.finditer(r'<input[^>]*type="hidden"[^>]*>', page):
        n = re.search(r'name="([^"]+)"', m.group(0))
        v = re.search(r'value="([^"]*)"', m.group(0))
        if n:
            out[n.group(1)] = html.unescape(v.group(1)) if v else ""
    return out


def fetch_direct(src, force=False):
    dest = os.path.join(RAW, src["dest"])
    opener = urllib.request.build_opener()
    meta = head(opener, src["url"])
    want = int(meta["content_length"]) if meta.get("content_length") else None
    if meta.get("status") != 200:
        return dict(src, status="unavailable", http_status=meta.get("status"), meta=meta)
    if not force and os.path.exists(dest) and want and os.path.getsize(dest) == want:
        sha = sha256_file(dest)
        mt = datetime.datetime.fromtimestamp(os.path.getmtime(dest), datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        log(event="adopted_existing", source_id=src["id"], url=src["url"], dest=src["dest"],
            bytes=want, sha256=sha, head=meta, retrieved_utc=mt)
        return dict(src, status="ok", bytes=want, sha256=sha, head=meta, action="reused")
    last = None
    for attempt in range(1, 5):
        try:
            offset = os.path.getsize(dest) if (os.path.exists(dest) and not force and attempt > 1) else 0
            hdr = {"Range": f"bytes={offset}-"} if offset else {}
            with _open(opener, src["url"], headers=hdr) as r:
                if offset and r.status != 206:
                    offset = 0
                n, sha = _stream(r, dest, offset)
            if want and n != want:
                raise IOError(f"size {n} != Content-Length {want}")
            log(event="downloaded", source_id=src["id"], url=src["url"], dest=src["dest"], bytes=n,
                sha256=sha, attempt=attempt, head=meta)
            return dict(src, status="ok", bytes=n, sha256=sha, head=meta, action="downloaded")
        except Exception as e:  # network cut, short read, proxy reset
            last = repr(e)
            time.sleep(2 ** attempt)
    log(event="failed", source_id=src["id"], url=src["url"], error=last)
    return dict(src, status="unavailable", error=last)


def _last_event(source_id, events):
    last = None
    if os.path.exists(LOG):
        for line in open(LOG, encoding="utf-8"):
            e = json.loads(line)
            if e.get("source_id") == source_id and e.get("event") in events:
                last = e["logged_utc"]
    return last


def fetch_cslb(src, force=False):
    """CSLB's portal is an ASP.NET form: choose 'License Master', then click download."""
    dest = os.path.join(RAW, src["dest"])
    last_fail = _last_event("CSLB-MASTER", ("failed",))
    if (not force and last_fail and not os.path.exists(dest + ".asof")
            and (datetime.datetime.now(datetime.timezone.utc) - datetime.datetime.strptime(
                last_fail, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)).total_seconds() < 6 * 3600):
        return dict(src, status="unavailable", error=f"not retried: last attempt failed {last_fail} (CSLB's firewall "
                    f"rate-limits; retry after 6 hours or import the file by hand)")
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    try:
        with _open(opener, src["url"]) as r:
            page = r.read().decode("utf-8", "replace")
        f = _hidden(page)
        f.update({"__EVENTTARGET": "ctl00$MainContent$ddlStatus", "__EVENTARGUMENT": "",
                  "ctl00$MainContent$ddlStatus": "M"})
        with _open(opener, src["url"], data=urllib.parse.urlencode(f).encode()) as r:
            page2 = r.read().decode("utf-8", "replace")
        asof = re.search(r'id="MainContent_lblMasterFile">([^<]*)<', page2)
        asof = asof.group(1).strip() if asof else ""
        if "lbMasterCSV" not in page2:
            raise IOError("CSV download link not present after form postback")
        if (not force and os.path.exists(dest) and os.path.exists(dest + ".asof")
                and open(dest + ".asof").read() == asof):
            sha = sha256_file(dest)
            log(event="adopted_existing", source_id=src["id"], url=src["url"], dest=src["dest"],
                bytes=os.path.getsize(dest), sha256=sha, portal_asof=asof)
            return dict(src, status="ok", bytes=os.path.getsize(dest), sha256=sha,
                        portal_asof=asof, action="reused")
        f2 = _hidden(page2)
        f2.update({"__EVENTTARGET": "ctl00$MainContent$lbMasterCSV", "__EVENTARGUMENT": "",
                   "ctl00$MainContent$ddlStatus": "M"})
        body = urllib.parse.urlencode(f2).encode()
        last = None
        for attempt in range(1, 4):
            gz = attempt % 2 == 1
            try:
                with _open(opener, src["url"], data=body, timeout=300,
                           headers={"Accept-Encoding": "gzip" if gz else "identity"}) as r:
                    ctype, disp = r.headers.get("Content-Type"), r.headers.get("Content-Disposition")
                    cenc = (r.headers.get("Content-Encoding") or "").lower()
                    if "html" in (ctype or "").lower():
                        raise IOError(f"portal returned {ctype} instead of a CSV")
                    n, sha = _stream(r, dest, 0, gzipped=(cenc == "gzip"))
                with open(dest, "rb") as chk:  # a firewall page is not a license file
                    head = chk.read(64)
                if not head.lstrip(b"\xef\xbb\xbf").startswith(b"LicenseNo,") or n < 1_000_000:
                    os.replace(dest, dest + ".rejected")
                    raise IOError(f"response was not the license CSV ({n} bytes: {head[:40]!r})")
                open(dest + ".asof", "w").write(asof)
                for stale in (dest + ".partial", dest + ".partial.asof"):
                    if os.path.exists(stale):
                        os.remove(stale)
                log(event="downloaded", source_id=src["id"], url=src["url"], dest=src["dest"],
                    bytes=n, sha256=sha, portal_asof=asof, content_type=ctype,
                    content_disposition=disp, content_encoding=cenc, attempt=attempt,
                    method="POST form postback ddlStatus=M then lbMasterCSV")
                return dict(src, status="ok", bytes=n, sha256=sha, portal_asof=asof,
                            action="downloaded")
            except urllib.error.HTTPError as e:
                last = f"HTTP {e.code}"
                log(event="attempt_failed", source_id=src["id"], attempt=attempt, error=last)
                if e.code in (403, 429):  # firewall or rate limit: do not hammer
                    break
            except Exception as e:
                last = repr(e)
                log(event="attempt_failed", source_id=src["id"], attempt=attempt, error=last,
                    gzip=gz)
            if last and "was not the license CSV" in last:
                break  # firewall rejected us: stop, do not hammer
            time.sleep(240 * attempt)
        if os.path.exists(dest):
            with open(dest, "rb") as chk:
                looks_like_csv = chk.read(64).lstrip(b"\xef\xbb\xbf").startswith(b"LicenseNo,")
            if looks_like_csv:  # keep what arrived, clearly labelled, never named as complete
                os.replace(dest, dest + ".partial")
                open(dest + ".partial.asof", "w").write(asof)
                log(event="kept_partial", source_id=src["id"], dest=src["dest"] + ".partial",
                    bytes=os.path.getsize(dest + ".partial"), sha256=sha256_file(dest + ".partial"), portal_asof=asof,
                    note="truncated stream; whole rows only are usable")
        raise IOError(last)
    except Exception as e:
        log(event="failed", source_id=src["id"], url=src["url"], error=repr(e))
        return dict(src, status="unavailable", error=repr(e))


def import_cslb(path, asof_text):
    """Use a license master CSV downloaded by hand from the CSLB portal."""
    dest = os.path.join(RAW, "cslb", "MasterLicenseData.csv")
    with open(path, "rb") as f:
        head = f.read(64)
    if not head.lstrip(b"\xef\xbb\xbf").startswith(b"LicenseNo,"):
        raise SystemExit("not a CSLB license master CSV (header must start with LicenseNo,)")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    import shutil
    shutil.copyfile(path, dest)
    open(dest + ".asof", "w").write(asof_text)
    for stale in (dest + ".partial", dest + ".partial.asof"):
        if os.path.exists(stale):
            os.remove(stale)
    sha = sha256_file(dest)
    log(event="manual_import", source_id="CSLB-MASTER", dest="cslb/MasterLicenseData.csv",
        bytes=os.path.getsize(dest), sha256=sha, portal_asof=asof_text, note="downloaded by hand from the CSLB portal")
    print("imported", dest, sha)


def fetch(src, force=False):
    return (fetch_cslb if src["kind"] == "cslb" else fetch_direct)(src, force)


if __name__ == "__main__":
    import sys
    if sys.argv[1:2] == ["import-cslb"]:
        import_cslb(sys.argv[2], sys.argv[3])
        raise SystemExit
    ids = sys.argv[1:]
    for s in SOURCES:
        if not ids or s["id"] in ids:
            print(json.dumps({k: v for k, v in fetch(s).items() if k != "head"}, indent=1))
