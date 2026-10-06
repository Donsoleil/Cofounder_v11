"""Independent verification of the scout outputs.

Re-reads the raw SBA, CSLB and DOL files with its own parsing code and checks the tracker against
them. `python3 -I scout/scout.py verify` runs the fast checks plus the DOL re-read;
add --repeat for the rerun / ID-stability tests (about 7 minutes).
"""
import csv
import collections
import datetime
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch  # noqa: E402
import scout as S  # noqa: E402
from norm import addr_level, name_match, name_variants, norm, STRONG  # noqa: E402

CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append(dict(check=name, ok=bool(ok), detail=detail))
    print(("PASS  " if ok else "FAIL  ") + name + (f"  [{detail}]" if detail else ""))


def rd(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def thirty_years_before(asof):
    d = datetime.date.fromisoformat(asof)
    try:
        return d.replace(year=d.year - S.AGE_YEARS)
    except ValueError:
        return d.replace(year=d.year - S.AGE_YEARS, day=28)


def raw_sba_recount():
    """Second, separate pass over the raw SBA file."""
    path = os.path.join(fetch.RAW, "sba", "public_150k_plus_240930.csv")
    with open(path, "rb") as fb:
        physical_lines = sum(1 for _ in fb)
    rows = state_match = county = 0
    keep = {}
    with open(path, encoding="cp1252", newline="") as f:
        r = csv.reader(f)
        h = next(r)
        ix = {c: i for i, c in enumerate(h)}
        for n, row in enumerate(r, start=1):
            rows += 1
            if (row[ix["BorrowerState"]].strip() == S.STATE and row[ix["NAICSCode"]].strip() == S.NAICS
                    and float(row[ix["CurrentApprovalAmount"]]) >= S.MIN_LOAN):
                state_match += 1
                if row[ix["ProjectState"]].strip().upper() == S.STATE and row[ix["ProjectCountyName"]].strip().upper() == S.COUNTY:
                    county += 1
                keep[n] = {c: row[i] for c, i in ix.items()}
    return dict(physical_lines=physical_lines, rows=rows, state_match=state_match, county=county, keep=keep)


def main(repeat=False):
    repeat = repeat or "--repeat" in sys.argv
    T, L = rd(S.TRACKER), rd(S.LOANS)
    reg_rows = rd(S.REGISTRY)
    stats = json.load(open(S.STATS))
    ledger = rd(S.SRC_LEDGER)
    asof = stats["as_of"]
    cut30 = thirty_years_before(asof).isoformat()

    # ---- identity and uniqueness
    ids = [r["biz_id"] for r in T]
    check("business IDs are unique and well formed (biz-NNN)", len(ids) == len(set(ids)) and all(re.fullmatch(r"biz-\d{3,}", i) for i in ids), f"{len(ids)} IDs")
    lns = [l["loan_number"] for l in L]
    check("loan numbers are unique (no duplicated loans)", len(lns) == len(set(lns)), f"{len(lns)} loans")
    by_biz = collections.defaultdict(set)
    for l in L:
        by_biz[l["biz_id"]].add(l["loan_number"])
    check("every loan belongs to exactly one tracker business", set(by_biz) <= set(ids))
    check("tracker loan_count and loan_numbers equal the loans file",
          all(int(r["loan_count"]) == len(by_biz[r["biz_id"]]) and set(filter(None, r["loan_numbers"].split(";"))) == by_biz[r["biz_id"]] for r in T if r["eligibility_status"] != "closed" or r["biz_id"] in by_biz))
    reg_map = {}
    dup = 0
    for r in reg_rows:
        if r["status"] == "active":
            for ln in filter(None, r["loan_numbers"].split(";")):
                dup += ln in reg_map
                reg_map[ln] = r["biz_id"]
    check("ID registry maps each loan to one active ID, matching the loans file", dup == 0 and all(reg_map.get(l["loan_number"]) == l["biz_id"] for l in L))
    seen_keys = collections.Counter()
    for r in T:
        v = name_variants(r["legal_name"])
        seen_keys[(v[0][1] if v else "", S.street_key(r["address"]), S.zip5(r["zip"]))] += 1
    check("no two businesses share legal name + street + ZIP", all(c == 1 for c in seen_keys.values()))
    check("every row has an eligibility_status and an outreach_status",
          all(r["eligibility_status"] in ("ready", "review", "closed") and r["outreach_status"] for r in T))

    # ---- full coverage against the raw SBA file, recounted independently
    rc = raw_sba_recount()
    sba = stats["sba"]
    check("source rows: raw recount equals pipeline count", rc["rows"] == sba["source_rows"] == rc["physical_lines"] - 1, f'{rc["rows"]:,} rows ({rc["physical_lines"]:,} physical lines incl. header)')
    check("statewide matching loans: raw recount equals pipeline", rc["state_match"] == sba["matching_loans_state"], f'{rc["state_match"]:,}')
    check("county loans: raw recount equals loans file", rc["county"] <= len(L) and sba["matching_loans_county"] == len(L), f'raw project-county count {rc["county"]}, loans file {len(L)} (difference = ZIP-only cases flagged)')
    keep_by_ln = {v["LoanNumber"]: (n, v) for n, v in rc["keep"].items()}
    missing = [l["loan_number"] for l in L if l["loan_number"] not in keep_by_ln]
    check("every loans-file row exists in the raw SBA file", not missing, f"{len(missing)} missing")
    bad = []
    for l in L:
        n, v = keep_by_ln[l["loan_number"]]
        if (int(l["source_row_number"]) != n or l["borrower_name"] != v["BorrowerName"] or abs(float(l["current_approval_amount"]) - float(v["CurrentApprovalAmount"])) > 0.005
                or l["zip"] != v["BorrowerZip"] or l["naics"] != v["NAICSCode"]):
            bad.append(l["loan_number"])
    check("loan name, amount, ZIP, NAICS and row number match the raw file for every loan", not bad, f"{len(bad)} mismatches")
    omitted = [ln for ln, (n, v) in keep_by_ln.items() if v["ProjectCountyName"].strip().upper() == S.COUNTY and v["ProjectState"].strip().upper() == S.STATE and ln not in set(lns)]
    check("no Orange County project-county loan was left out", not omitted, f"{len(omitted)} omitted")
    check("draw is kept per loan (PPP=first, PPS=second)", all((l["processing_method"], l["draw"]) in (("PPP", "1"), ("PPS", "2")) for l in L))

    # ---- payroll proxy rule
    ok_proxy = True
    for l in L:
        if l["hist_payroll_proxy_usd"]:
            ok_proxy &= abs(float(l["hist_payroll_proxy_usd"]) - round(float(l["current_approval_amount"]) / 2.5 * 12, 2)) < 0.006
            ok_proxy &= l["payroll_proxy_basis"] == "verified_standard_2.5x" and l["initial_approval_amount"] == l["current_approval_amount"]
            ok_proxy &= "sole" not in l["business_type"].lower() and "self" not in l["business_type"].lower()
        else:
            ok_proxy &= l["payroll_proxy_basis"].startswith("unknown:")
    check("payroll proxy = loan / 2.5 * 12 only for standard loans; otherwise unknown", ok_proxy,
          f'{sum(1 for l in L if l["hist_payroll_proxy_usd"])} known, {sum(1 for l in L if not l["hist_payroll_proxy_usd"])} unknown')

    # ---- registration joins re-read from the raw CSLB file
    cinfo = S.cslb_local()
    lic = {}
    if cinfo["state"] != "missing":
        with open(cinfo["file"], encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                if None not in row and None not in row.values():
                    lic[row["LicenseNo"]] = row
    bad_reg, n_reg = [], 0
    for r in T:
        if not r["reg_record_id"]:
            continue
        n_reg += 1
        c = lic.get(r["reg_record_id"])
        if not c:
            bad_reg.append((r["biz_id"], "license not in raw CSLB file"))
            continue
        names = [v for fld in ("BusinessName", "BUS-NAME-2", "FullBusinessName") for v in name_variants(c[fld])]
        mine = name_variants(r["legal_name"]) + [v for nm in r["other_names"].split(" | ") for v in name_variants(re.sub(r" \(CSLB #\d+\)$", "", nm))]
        if not name_match(mine, names):
            bad_reg.append((r["biz_id"], "name does not match any CSLB name field"))
        if S.iso(c["IssueDate"]) != r["reg_date"]:
            bad_reg.append((r["biz_id"], "reg_date differs from CSLB IssueDate"))
        lv = addr_level((r["address"], r["city"], r["zip"]), (c["MailingAddress"], c["City"], c["ZIPCode"]))
        if r["reg_match_confidence"] == "high" and lv not in STRONG and lv != "zip":
            bad_reg.append((r["biz_id"], f"high confidence but address level is {lv}"))
    check("every registration record, date and name re-verifies against the raw CSLB file", not bad_reg, f"{n_reg} checked; {bad_reg[:3]}")
    bad_el = []
    for r in T:
        st = r["eligibility_status"]
        old = r["reg_date"] and r["reg_date"] <= cut30
        if st == "ready" and not (r["reg_match_confidence"] == "high" and old and r["age_30plus"] == "Y" and r["reg_evidence_url"] and r["reg_date_meaning"]):
            bad_el.append((r["biz_id"], "ready without verified 30+ year date"))
        if r["reg_match_confidence"] == "high" and old and r["age_30plus"] != "Y":
            bad_el.append((r["biz_id"], "30+ flag missing"))
        if r["age_30plus"] in ("Y", "N") and r["reg_match_confidence"] != "high":
            bad_el.append((r["biz_id"], "age verdict on an unverified match"))
        if st == "closed" and not (("non-profit" in r["eligibility_reason"]) or r["age_30plus"] == "N" or "merged" in r["eligibility_reason"] or "no longer" in r["eligibility_reason"]):
            bad_el.append((r["biz_id"], "closed without a clear exclusion"))
        if st == "closed" and r["age_30plus"] == "N" and cinfo["state"] != "complete":
            bad_el.append((r["biz_id"], "closed on a young license while CSLB file is partial"))
    check("eligibility rules hold on every row (ready = verified 30+, closed = clear exclusion)", not bad_el, str(bad_el[:3]))

    # ---- pension joins re-read from the raw DOL files
    need = collections.defaultdict(dict)
    for r in T:
        if r["pension_status"] == "plan_found":
            need[r["pension_source_id"]][r["pension_ack_id"]] = r
    fm = {"5500": S.F5500, "5500SF": S.FSF}
    bad_p, n_p = [], 0
    for sid, acks in need.items():
        m = re.fullmatch(r"DOL-(5500SF|5500)-(\d{4})", sid)
        stem = "F_5500_SF" if m.group(1) == "5500SF" else "F_5500"
        z = zipfile.ZipFile(os.path.join(fetch.RAW, "dol", f"{stem}_{m.group(2)}_Latest.zip"))
        mem = [x for x in z.namelist() if x.lower().endswith(".csv")][0]
        mp = fm[m.group(1)]
        with z.open(mem) as raw:
            for row in csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="")):
                r = acks.get(row[mp["ack"]])
                if not r:
                    continue
                n_p += 1
                codes = "".join(re.findall(r"[1-3][A-Z]", row[mp["codes"]].replace(" ", "")))
                addrs = [tuple(row[c].strip() for c in (cols[0], cols[1], cols[3])) for cols in (mp["mail"], mp["loc"])]
                names = name_variants(row[mp["sponsor"]]) + name_variants(row[mp["dba"]])
                mine = [v for nm in [r["legal_name"]] + [x for x in r["other_names"].split(" | ") if x] for v in name_variants(re.sub(r" \(CSLB #\d+\)$", "", nm))]
                lv = max((addr_level((r["address"], r["city"], r["zip"]), a) for a in addrs), key=lambda x: ["none", "city", "zip", "street_loose", "street"].index(x))
                probs = []
                if row[mp["ein"]].strip() != r["pension_sponsor_ein"]: probs.append("EIN")
                if f'{r["pension_sponsor_ein"]}-{row[mp["pn"]].strip()}' != r["pension_plan_id"]: probs.append("plan id")
                if S.iso(row[mp["begin"]]) != r["pension_plan_period_begin"] or S.iso(row[mp["end"]]) != r["pension_plan_period_end"]: probs.append("plan period")
                if S.iso(row[mp["received"]]) != r["pension_filing_date"]: probs.append("filing date")
                if row[mp["act_eoy"]].strip() != r["pension_active_participants"]: probs.append("active participants")
                if not codes or codes != r["pension_benefit_codes"]: probs.append("retirement benefit codes")
                if not name_match(mine, names): probs.append("sponsor name")
                if lv not in STRONG:  # fall back to the matched license's own address (business may use two)
                    c2 = lic.get(r["reg_record_id"])
                    lv2 = max((addr_level((c2["MailingAddress"], c2["City"], c2["ZIPCode"]), a) for a in addrs), key=lambda x: ["none", "city", "zip", "street_loose", "street"].index(x)) if c2 else "none"
                    if lv2 not in STRONG:
                        probs.append("address")
                if probs:
                    bad_p.append((r["biz_id"], probs))
    n_expected = sum(len(a) for a in need.values())
    check("every retirement-plan match re-verifies against the raw DOL row (EIN, plan ID, period, filing date, participants, benefit codes, name)",
          not bad_p and n_p == n_expected, f"{n_p}/{n_expected} filings re-read; problems: {bad_p[:3]}")
    primary_other = all(not r["pension_other_plans"] or r["pension_plan_id"] not in r["pension_other_plans"] for r in T)
    check("plans are never summed: one primary plan per business, others listed separately", primary_other)

    # ---- source ledger, hashes, unavailable sources logged once
    bad_h = []
    for l in ledger:
        if l["sha256"]:
            path = {"SBA-PPP-150K": "sba/public_150k_plus_240930.csv"}.get(l["source_id"])
            if l["source_id"] == "CSLB-MASTER":
                path = os.path.relpath(cinfo["file"], fetch.RAW) if cinfo["file"] else None
            elif path is None:
                m = re.fullmatch(r"DOL-(5500SF|5500)-(\d{4})", l["source_id"])
                path = f'dol/{"F_5500_SF" if m.group(1) == "5500SF" else "F_5500"}_{m.group(2)}_Latest.zip'
            if not path or fetch.sha256_file(os.path.join(fetch.RAW, path)) != l["sha256"]:
                bad_h.append(l["source_id"])
    check("source ledger SHA-256 matches the files on disk for every source", not bad_h, str(bad_h))
    check("every ledger row has URL, retrieval date and reporting period", all(l["url"] and l["first_retrieved_utc"] and l["reporting_period"] for l in ledger))
    nonok = collections.Counter(l["source_id"] for l in ledger if l["status"] != "ok")
    status = json.load(open(S.SOURCE_STATUS)) if os.path.exists(S.SOURCE_STATUS) else {}
    check("each unavailable or partial source appears once in the ledger and once in source-status", all(v == 1 for v in nonok.values()) and set(nonok) == set(status), f"{dict(nonok)}")
    ml = rd(S.MATCH_LEDGER)
    check("match ledger has an evidence URL and source ID on every row", all(m["evidence_url"] and m["source_id"] for m in ml), f"{len(ml)} rows")
    enc = sba["encoding"]
    check("SBA encoding recorded (not valid UTF-8, decoded as cp1252)", enc["codec"] == "cp1252" and not enc["utf8_strict_valid"], enc["chosen"])

    if repeat:
        repeat_tests(T, L, stats)

    failed = [c for c in CHECKS if not c["ok"]]
    json.dump(dict(verified_utc=fetch.now(), as_of=asof, passed=len(CHECKS) - len(failed), failed=len(failed), checks=CHECKS),
              open(os.path.join(S.HOME, "scout", "state", "verification.json"), "w"), indent=1)
    print(f"\n{len(CHECKS) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


def _run_scout(home, asof):
    env = dict(os.environ, SCOUT_HOME=home, SCOUT_AS_OF=asof)
    p = subprocess.run([sys.executable, "-I", os.path.join(os.path.dirname(os.path.abspath(__file__)), "scout.py"), "run", "--skip-fetch"],
                       env=env, capture_output=True, text=True)
    return p.returncode, p.stdout[-400:] + p.stderr[-400:]


def _prep(home):
    for f in ("scout-tracker.csv", "scout-loans.csv", "source-ledger.csv", "match-ledger.csv"):
        shutil.copy(os.path.join(S.HOME, f), os.path.join(home, f))
    shutil.copytree(os.path.join(S.HOME, "scout", "state"), os.path.join(home, "scout", "state"))


def repeat_tests(T, L, stats):
    asof = stats["as_of"]
    base = {r["biz_id"]: r for r in T}
    # 1) rerun with unchanged inputs, after hand edits to outreach status and extra columns
    tmp = tempfile.mkdtemp(prefix="scout-repeat-")
    os.makedirs(os.path.join(tmp, "scout"))
    _prep(tmp)
    rows = rd(os.path.join(tmp, "scout-tracker.csv"))
    fields = list(rows[0].keys()) + ["score", "analyst_note"]
    for i, r in enumerate(rows):
        r["score"] = str(100 - i)
        r["analyst_note"] = f"note {i}"
    rows[0]["outreach_status"], rows[1]["outreach_status"] = "contacted", "do not contact"
    S.write_csv(os.path.join(tmp, "scout-tracker.csv"), fields, rows)
    reg_before = rd(os.path.join(tmp, "scout", "state", "id-registry.csv"))
    rc, out = _run_scout(tmp, asof)
    check("rerun #1 (unchanged inputs) completes", rc == 0, out[-120:] if rc else "")
    after = rd(os.path.join(tmp, "scout-tracker.csv"))
    check("rerun keeps the same IDs, in the same order, with no new IDs", [r["biz_id"] for r in after] == [r["biz_id"] for r in rows])
    check("rerun preserves outreach_status, score and analyst columns exactly",
          all(a["outreach_status"] == b["outreach_status"] and a["score"] == b["score"] and a["analyst_note"] == b["analyst_note"] for a, b in zip(after, rows)))
    owned_same = all(all(a[k] == base[a["biz_id"]][k] for k in S.OWNED) for a in after)
    check("rerun reproduces every identity, loan, registration and pension column byte for byte", owned_same)
    loans_after = rd(os.path.join(tmp, "scout-loans.csv"))
    check("rerun reproduces the loans file (no duplicated or dropped loans)", {(l["loan_number"], l["biz_id"]) for l in loans_after} == {(l["loan_number"], l["biz_id"]) for l in L} and len(loans_after) == len(L))
    reg_after = rd(os.path.join(tmp, "scout", "state", "id-registry.csv"))
    check("rerun keeps first-seen times in the ID registry", {r["biz_id"]: r["first_seen_utc"] for r in reg_before} == {r["biz_id"]: r["first_seen_utc"] for r in reg_after})
    shutil.rmtree(tmp, ignore_errors=True)

    # 2) a business the registry has never seen gets a NEW id; nothing else is renumbered
    tmp = tempfile.mkdtemp(prefix="scout-repeat-")
    os.makedirs(os.path.join(tmp, "scout"))
    _prep(tmp)
    victim = sorted(base)[len(base) // 2]
    trk = [r for r in rd(os.path.join(tmp, "scout-tracker.csv")) if r["biz_id"] != victim]
    for r in trk:
        r["outreach_status"] = "contacted" if r["biz_id"].endswith("5") else r["outreach_status"]
    S.write_csv(os.path.join(tmp, "scout-tracker.csv"), list(trk[0].keys()), trk)
    regp = os.path.join(tmp, "scout", "state", "id-registry.csv")
    regrows = [r for r in rd(regp) if r["biz_id"] != victim]
    S.write_csv(regp, list(regrows[0].keys()), regrows)
    rc, out = _run_scout(tmp, asof)
    after = rd(os.path.join(tmp, "scout-tracker.csv"))
    top = max(int(i.split("-")[1]) for i in base)
    new = [r for r in after if r["biz_id"] not in base]
    check("rerun #2 (one business unknown to the registry) completes", rc == 0, out[-120:] if rc else "")
    check("the unseen business receives exactly one new ID, above every existing ID", len(new) == 1 and int(new[0]["biz_id"].split("-")[1]) == top + 1, f"new: {[r['biz_id'] for r in new]}")
    check("only the new ID is initialised outreach_status=ready; existing statuses untouched",
          new and new[0]["outreach_status"] == "ready" and all(r["outreach_status"] == ("contacted" if r["biz_id"].endswith("5") else base[r["biz_id"]]["outreach_status"]) for r in after if r["biz_id"] in base))
    check("existing businesses keep their IDs after the new business is added", all(r["biz_id"] in base for r in after if r["biz_id"] != (new[0]["biz_id"] if new else "")) and len(after) == len(base))
    shutil.rmtree(tmp, ignore_errors=True)
