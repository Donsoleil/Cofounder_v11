#!/usr/bin/env python3
"""Orange County, CA plumbing/HVAC off-market scout.  Stdlib only.  See SCOUT.md.

    python3 -I scout/scout.py run            # fetch (reuse if unchanged) -> build -> verify
    python3 -I scout/scout.py run --skip-fetch
    python3 -I scout/scout.py verify

Outputs (repo root): scout-tracker.csv, scout-loans.csv, source-ledger.csv,
match-ledger.csv, results.html.  State: scout/state/.  Raw downloads: scout/raw/
(git-ignored); scout/logs/download-log.jsonl is the reproducible download record.
"""
import argparse
import collections
import csv
import datetime
import io
import json
import os
import re
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch  # noqa: E402
from norm import (STRONG, best_addr_level, confidence, name_match, name_variants,  # noqa: E402
                  norm, street_key, zip5)

# ---- scope (change these and rerun to scout another region / trade) -------------------
REGION = "Orange County, California"
STATE, COUNTY, NAICS, MIN_LOAN, AGE_YEARS = "CA", "ORANGE", "238220", 150000.0, 30
# ----------------------------------------------------------------------------------------

ROOT = fetch.ROOT
HOME = os.environ.get("SCOUT_HOME") or ROOT
AS_OF = os.environ.get("SCOUT_AS_OF") or datetime.date.today().isoformat()
P = lambda *a: os.path.join(HOME, *a)  # noqa: E731
TRACKER, LOANS = P("scout-tracker.csv"), P("scout-loans.csv")
SRC_LEDGER, MATCH_LEDGER = P("source-ledger.csv"), P("match-ledger.csv")
REGISTRY, STATS = P("scout", "state", "id-registry.csv"), P("scout", "state", "run-stats.json")
SOURCE_STATUS = P("scout", "state", "source-status.json")

SBA_PAGE = "https://data.sba.gov/dataset/ppp-foia"
# CSLB has no per-license deep link that works without a session; this lookup page is live and takes the license number
CSLB_DETAIL = "https://www2.cslb.ca.gov/OnlineServices/CheckLicenseII/CheckLicense.aspx"
DOL_PAGE = ("https://www.dol.gov/agencies/ebsa/about-ebsa/our-activities/public-disclosure/"
            "foia/form-5500-datasets")
TRADE_CLASSES = {"C20", "C36", "C38", "C4", "C43", "C34", "C61", "C-20", "C-36"}
CAVEAT = ("Record age is not owner age and does not show intent to sell. Participants are not "
          "current headcount. Payroll proxies are historical loan-based figures, not revenue "
          "or value.")

OWNED = [
    "biz_id", "legal_name", "other_names", "address", "city", "state", "zip", "county",
    "loan_count", "loan_numbers", "draws",
    "first_draw_loan_number", "first_draw_date", "first_draw_amount_usd",
    "hist_jobs_reported_first_draw", "hist_payroll_proxy_first_draw_usd",
    "second_draw_loan_number", "second_draw_date", "second_draw_amount_usd",
    "hist_jobs_reported_second_draw", "hist_payroll_proxy_second_draw_usd", "payroll_proxy_basis",
    "reg_source", "reg_record_id", "reg_name_on_record", "reg_date", "reg_date_meaning",
    "reg_age_years", "age_30plus", "age_as_of", "reg_match_confidence", "reg_match_evidence",
    "reg_status", "reg_class", "reg_evidence_url",
    "pension_status", "pension_form", "pension_plan_id", "pension_plan_name",
    "pension_plan_period_begin", "pension_plan_period_end", "pension_filing_date",
    "pension_active_participants", "pension_benefit_codes", "pension_sponsor_ein",
    "pension_ack_id", "pension_match_confidence", "pension_match_evidence",
    "pension_plan_count", "pension_other_plans", "pension_source_id", "pension_evidence_url",
    "loan_evidence_url", "eligibility_status", "eligibility_reason", "ein_known", "ein_source",
]
OUTREACH = "outreach_status"
USER_INPUT = {"ein_known", "ein_source"}  # supplied independently; never overwritten

F5500 = dict(ack="ACK_ID", begin="FORM_PLAN_YEAR_BEGIN_DATE", end="FORM_TAX_PRD",
             plan_name="PLAN_NAME", pn="SPONS_DFE_PN", sponsor="SPONSOR_DFE_NAME",
             dba="SPONS_DFE_DBA_NAME", ein="SPONS_DFE_EIN", act_eoy="TOT_ACTIVE_PARTCP_CNT",
             act_boy="TOT_ACT_PARTCP_BOY_CNT", codes="TYPE_PENSION_BNFT_CODE",
             received="DATE_RECEIVED",
             mail=("SPONS_DFE_MAIL_US_ADDRESS1", "SPONS_DFE_MAIL_US_CITY",
                   "SPONS_DFE_MAIL_US_STATE", "SPONS_DFE_MAIL_US_ZIP"),
             loc=("SPONS_DFE_LOC_US_ADDRESS1", "SPONS_DFE_LOC_US_CITY",
                  "SPONS_DFE_LOC_US_STATE", "SPONS_DFE_LOC_US_ZIP"))
FSF = dict(ack="ACK_ID", begin="SF_PLAN_YEAR_BEGIN_DATE", end="SF_TAX_PRD", plan_name="SF_PLAN_NAME",
           pn="SF_PLAN_NUM", sponsor="SF_SPONSOR_NAME", dba="SF_SPONSOR_DFE_DBA_NAME",
           ein="SF_SPONS_EIN", act_eoy="SF_TOT_ACT_PARTCP_EOY_CNT", act_boy="SF_TOT_ACT_PARTCP_BOY_CNT",
           codes="SF_TYPE_PENSION_BNFT_CODE", received="DATE_RECEIVED",
           mail=("SF_SPONS_US_ADDRESS1", "SF_SPONS_US_CITY", "SF_SPONS_US_STATE", "SF_SPONS_US_ZIP"),
           loc=("SF_SPONS_LOC_US_ADDRESS1", "SF_SPONS_LOC_US_CITY", "SF_SPONS_LOC_US_STATE",
                "SF_SPONS_LOC_US_ZIP"))


# ---------------------------------------------------------------- small helpers
def read_csv(path):
    if not os.path.exists(path):
        return [], []
    with open(path, newline="", encoding="utf-8") as f:
        rd = csv.DictReader(f)
        return list(rd), rd.fieldnames or []


def write_csv(path, fields, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    os.replace(tmp, path)


def iso(d):
    """mm/dd/yyyy, yyyy-mm-dd, with optional time -> yyyy-mm-dd, else ''."""
    d = (d or "").strip()
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", d)
    if m:
        return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", d)
    return m.group(0) if m else ""


def num(x):
    try:
        return float(str(x).replace(",", "").strip())
    except ValueError:
        return None


def age_years(date_iso):
    if not date_iso:
        return None
    a, b = datetime.date.fromisoformat(date_iso), datetime.date.fromisoformat(AS_OF)
    return round((b - a).days / 365.2425, 1)


def is_30plus(date_iso):
    cutoff = datetime.date.fromisoformat(AS_OF)
    cutoff = cutoff.replace(year=cutoff.year - AGE_YEARS) if not (
        cutoff.month == 2 and cutoff.day == 29) else cutoff.replace(year=cutoff.year - AGE_YEARS, day=28)
    return datetime.date.fromisoformat(date_iso) <= cutoff


def money(x):
    return f"{x:.2f}" if x is not None else ""


def detect_encoding(open_binary):
    """One full pass: strict UTF-8 validity + histogram of non-ASCII bytes."""
    import codecs
    dec = codecs.getincrementaldecoder("utf-8")("strict")
    valid, hist, first_bad, off = True, collections.Counter(), None, 0
    with open_binary() as f:
        while True:
            b = f.read(1 << 22)
            if not b:
                break
            for m in re.finditer(rb"[\x80-\xff]", b):
                hist[b[m.start()]] += 1
            if valid:
                try:
                    dec.decode(b)
                except UnicodeDecodeError as e:
                    valid, first_bad = False, off + e.start
            off += len(b)
    if not hist:
        chosen = "ascii (valid UTF-8)"
    elif valid:
        chosen = "utf-8"
    else:
        chosen = "cp1252" if not any(0x80 <= k <= 0x9F for k in hist) or True else "latin-1"
    return dict(chosen=chosen, codec="utf-8" if chosen.startswith(("ascii", "utf-8")) else "cp1252",
                utf8_strict_valid=valid, first_invalid_utf8_offset=first_bad,
                nonascii_bytes={f"0x{k:02X}": v for k, v in sorted(hist.items())},
                note=("Bytes 0x80-0x9F absent, so cp1252 and latin-1 decode identically."
                      if hist and not any(0x80 <= k <= 0x9F for k in hist) else ""))


# ---------------------------------------------------------------- stage 1: SBA PPP
def stage_sba(path):
    enc = detect_encoding(lambda: open(path, "rb"))
    st = collections.Counter()
    zipcounty = collections.defaultdict(collections.Counter)
    loans, statewide, seen = [], [], set()
    dates_all = [None, None]
    with open(path, "r", encoding=enc["codec"], newline="") as f:
        rd = csv.reader(f)
        hdr = next(rd)
        ix = {h: i for i, h in enumerate(hdr)}
        need = ["LoanNumber", "DateApproved", "ProcessingMethod", "BorrowerName", "BorrowerAddress",
                "BorrowerCity", "BorrowerState", "BorrowerZip", "NAICSCode", "CurrentApprovalAmount",
                "InitialApprovalAmount", "ProjectCountyName", "ProjectState", "ProjectZip", "JobsReported"]
        miss = [c for c in need if c not in ix]
        if miss:
            raise SystemExit(f"SBA file missing expected columns: {miss}")
        for n, row in enumerate(rd, start=1):
            st["source_rows"] += 1
            if len(row) != len(hdr):
                st["malformed_rows"] += 1
                continue
            g = lambda c: row[ix[c]].strip()  # noqa: E731
            ln = g("LoanNumber")
            if ln in seen:
                st["duplicate_loan_numbers"] += 1
            seen.add(ln)
            d = iso(g("DateApproved"))
            if d:
                dates_all[0] = d if dates_all[0] is None or d < dates_all[0] else dates_all[0]
                dates_all[1] = d if dates_all[1] is None or d > dates_all[1] else dates_all[1]
            bstate = g("BorrowerState").upper()
            if not bstate:
                st["blank_borrower_state"] += 1
            pstate, pcounty = g("ProjectState").upper(), g("ProjectCountyName").upper()
            if pstate == STATE:
                zipcounty[zip5(g("ProjectZip"))][pcounty] += 1
            if g("NAICSCode") == NAICS:
                st["naics_rows_all_states"] += 1
                if not bstate:
                    st["naics_rows_blank_state"] += 1
            amt = num(g("CurrentApprovalAmount"))
            if bstate == STATE and g("NAICSCode") == NAICS and amt is not None and amt >= MIN_LOAN:
                st["matching_loans_state"] += 1
                rec = {c: g(c) for c in hdr}
                rec["_row"] = n
                statewide.append(rec)
    oc_zips = {z for z, c in zipcounty.items() if z and c[COUNTY] / sum(c.values()) >= 0.5}
    for rec in statewide:
        in_county = rec["ProjectState"].upper() == STATE and rec["ProjectCountyName"].upper() == COUNTY
        zip_oc = zip5(rec["BorrowerZip"]) in oc_zips
        if in_county or zip_oc:
            rec["_county_check"] = "agree" if (in_county and zip_oc) else (
                "county_field_only" if in_county else "borrower_zip_only")
            loans.append(rec)
    st["matching_loans_county"] = len(loans)
    st["county_zip_conflicts"] = sum(1 for r in loans if r["_county_check"] != "agree")
    st["oc_zip_count"] = len(oc_zips)
    return dict(enc=enc, stats=dict(st), header_cols=len(hdr), approvals_min=dates_all[0],
                approvals_max=dates_all[1], loans=loans)


def loan_record(rec, sha):
    amt, ini = num(rec["CurrentApprovalAmount"]), num(rec["InitialApprovalAmount"])
    method = rec["ProcessingMethod"].upper()
    draw = {"PPP": "1", "PPS": "2"}.get(method, "")
    jobs = num(rec["JobsReported"])
    reasons = []
    if method not in ("PPP", "PPS"):
        reasons.append("processing_method_unrecognized")
    if amt is None or ini is None or abs(amt - ini) > 0.005:
        reasons.append("amount_changed_after_approval")
    if (num(rec["UndisbursedAmount"]) or 0) > 0:
        reasons.append("undisbursed_amount")
    if (num(rec["REFINANCE_EIDL_PROCEED"]) or 0) > 0:
        reasons.append("includes_eidl_refinance")
    bt = rec["BusinessType"].lower()
    if not bt or any(k in bt for k in ("sole prop", "self-employed", "independent contractor")):
        reasons.append("self_employed_or_untyped_borrower")
    if amt is not None and ((method == "PPP" and amt >= 10_000_000) or (method == "PPS" and amt >= 2_000_000)):
        reasons.append("at_program_loan_cap")
    proxy = None if reasons else round(amt / 2.5 * 12, 2)
    return dict(
        loan_number=rec["LoanNumber"], biz_id="", draw=draw, processing_method=method,
        date_approved=iso(rec["DateApproved"]), current_approval_amount=money(amt),
        initial_approval_amount=money(ini), borrower_name=rec["BorrowerName"],
        borrower_address=rec["BorrowerAddress"], city=rec["BorrowerCity"], state=rec["BorrowerState"],
        zip=rec["BorrowerZip"], project_city=rec["ProjectCity"], project_county=rec["ProjectCountyName"],
        project_state=rec["ProjectState"], project_zip=rec["ProjectZip"], naics=rec["NAICSCode"],
        hist_jobs_reported=rec["JobsReported"], business_type=rec["BusinessType"],
        nonprofit=rec["NonProfit"], loan_status=rec["LoanStatus"], lender=rec["OriginatingLender"],
        forgiveness_amount=rec["ForgivenessAmount"], forgiveness_date=iso(rec["ForgivenessDate"]),
        term_months=rec["Term"], franchise_name=rec["FranchiseName"], county_zip_check=rec["_county_check"],
        hist_payroll_proxy_usd=money(proxy),
        payroll_proxy_basis="verified_standard_2.5x" if proxy is not None else "unknown: " + "; ".join(reasons),
        source_id="SBA-PPP-150K", source_row_number=rec["_row"], source_sha256=sha)


# ---------------------------------------------------------------- stage 2: business groups
class UF:
    def __init__(self, items):
        self.p = {i: i for i in items}

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        a, b = self.find(a), self.find(b)
        if a != b:
            self.p[max(a, b)] = min(a, b)


def _loan_keys(l):
    var = name_variants(l["borrower_name"])
    prim = var[0] if var else ("", "", "", "")
    return dict(strict=prim[1], core=prim[2], sk=street_key(l["borrower_address"]), z=zip5(l["zip"]),
                dba=[v[2] for v in var[1:] if v[2]], cores={v[2] for v in var if v[2]})


def relate(a, b):
    """Strongest reason two loans are the same business, or ''. Name AND address are both needed."""
    if a["strict"] and a["strict"] == b["strict"]:
        if a["sk"] and a["sk"] == b["sk"]:
            return "same_name_same_street"
        if a["z"] and a["z"] == b["z"]:
            return "same_name_same_zip"
    if a["core"] and a["core"] == b["core"] and a["sk"] and a["sk"] == b["sk"] and a["z"] == b["z"]:
        return "legal_suffix_variant_same_street"
    if a["sk"] and a["sk"] == b["sk"] and (set(a["dba"]) & b["cores"] or set(b["dba"]) & a["cores"]):
        return "dba_evidence_same_street"
    return ""


def build_groups(loans):
    """Group loans into businesses. Every pairwise reason that joined loans is kept as evidence."""
    keys = {l["loan_number"]: _loan_keys(l) for l in loans}
    uf = UF(list(keys))
    pair_rules = collections.defaultdict(set)
    ids = sorted(keys)
    for i, x in enumerate(ids):
        for y in ids[i + 1:]:
            r = relate(keys[x], keys[y])
            if r:
                uf.union(x, y)
                pair_rules[x].add(r)
                pair_rules[y].add(r)
    by = collections.defaultdict(list)
    rules = collections.defaultdict(set)
    for l in loans:
        root = uf.find(l["loan_number"])
        by[root].append(l)
        rules[root] |= pair_rules.get(l["loan_number"], set())
    groups = []
    for root, ls in by.items():
        ls.sort(key=lambda l: (l["date_approved"], l["loan_number"]))
        first = ls[0]
        names = []
        for l in ls:
            if l["borrower_name"] not in names:
                names.append(l["borrower_name"])
        addrs = []
        for l in ls:
            for a in ((l["borrower_address"], l["city"], l["zip"]),
                      ("", l["project_city"], l["project_zip"])):
                if a not in addrs and (a[0] or a[2]):
                    addrs.append(a)
        k = keys[first["loan_number"]]
        groups.append(dict(loans=ls, key_loan=first, legal_name=first["borrower_name"], names=names,
                           addrs=addrs, rules=sorted(rules.get(root, [])),
                           group_key="|".join((k["strict"], k["sk"], k["z"]))))
    groups.sort(key=lambda g: (name_variants(g["legal_name"])[0][2] if name_variants(g["legal_name"]) else "",
                               g["key_loan"]["loan_number"]))
    return groups


def assign_ids(groups):
    """Permanent IDs: reuse by loan number, then by identity key; never reuse for a different
    business; merges keep the lowest ID and mark the other 'merged_into'."""
    rows, _ = read_csv(REGISTRY)
    loan_to, key_to, info = {}, {}, {}
    for r in rows:
        info[r["biz_id"]] = r
        key_to[r["identity_key"]] = r["biz_id"]
        for ln in filter(None, r["loan_numbers"].split(";")):
            loan_to[ln] = r["biz_id"]
    top = max([int(r["biz_id"].split("-")[1]) for r in rows] or [0])
    claimed, events = {}, []
    now = fetch.now()
    for g in groups:
        ids = {loan_to[l["loan_number"]] for l in g["loans"] if l["loan_number"] in loan_to}
        if g["group_key"] in key_to:
            ids.add(key_to[g["group_key"]])
        ids = {i for i in ids if info.get(i, {}).get("status", "active") == "active" or True}
        live = sorted(ids, key=lambda i: int(i.split("-")[1]))
        keep = next((i for i in live if i not in claimed), None)
        if keep is None:
            top += 1
            keep = f"biz-{top:03d}"
            info[keep] = dict(biz_id=keep, status="active", identity_key=g["group_key"],
                              first_seen_utc=now, loan_numbers="", merged_into="")
            events.append(("new", keep))
        for other in live:
            if other != keep and info[other]["status"] == "active" and other not in claimed:
                info[other]["status"], info[other]["merged_into"] = "merged", keep
                events.append(("merged", other, keep))
        claimed[keep] = g
        g["biz_id"] = keep
        for l in g["loans"]:
            l["biz_id"] = keep
            loan_to[l["loan_number"]] = keep
    for i, r in info.items():
        r["loan_numbers"] = ";".join(sorted(k for k, v in loan_to.items() if v == i))
    write_csv(REGISTRY, ["biz_id", "status", "identity_key", "first_seen_utc", "loan_numbers", "merged_into"],
              sorted(info.values(), key=lambda r: int(r["biz_id"].split("-")[1])))
    return events, {i for i, r in info.items() if r["status"] == "merged"}


# ---------------------------------------------------------------- stage 3: CSLB license master
def cslb_local():
    """What CSLB data is on disk: a complete file (has .asof), a partial one, or nothing."""
    path = os.path.join(fetch.RAW, "cslb", "MasterLicenseData.csv")
    rd = lambda q: open(q, encoding="utf-8").read().strip() if os.path.exists(q) else ""  # noqa: E731
    if os.path.exists(path) and os.path.exists(path + ".asof"):
        return dict(state="complete", file=path, asof=rd(path + ".asof"))
    if os.path.exists(path + ".partial"):
        return dict(state="partial", file=path + ".partial", asof=rd(path + ".partial.asof"))
    return dict(state="missing", file=None, asof="")


def stage_cslb(groups, path):
    """Join each business to CSLB licenses. IssueDate = original license issue date."""
    S, C, T = (collections.defaultdict(set) for _ in range(3))
    for gi, g in enumerate(groups):
        for nm in g["names"]:
            for _, s, c, t in name_variants(nm):
                S[s].add(gi)
                if c:
                    C[c].add(gi)
                if t and len(t) >= 4:
                    T[t].add(gi)
    enc = detect_encoding(lambda: open(path, "rb"))
    st = collections.Counter()
    cands = collections.defaultdict(dict)  # gi -> license -> candidate
    names_only = collections.Counter()
    with open(path, "r", encoding="utf-8-sig" if enc["codec"] == "utf-8" else enc["codec"], newline="") as f:
        rd = csv.DictReader(f)
        hdr = rd.fieldnames
        for c in ("LicenseNo", "BusinessName", "BUS-NAME-2", "FullBusinessName", "MailingAddress", "City",
                  "ZIPCode", "IssueDate", "PrimaryStatus", "Classifications(s)", "BusinessType"):
            if c not in hdr:
                raise SystemExit(f"CSLB file missing expected column {c}")
        lic_min = lic_max = None
        for row in rd:
            if None in row or None in row.values():  # short or long row = cut-off or malformed
                st["malformed_rows"] += 1
                continue
            st["rows"] += 1
            yr = row["IssueDate"][-4:]
            st["issued_" + (yr[:3] + "0s" if yr.isdigit() else "unknown")] += 1
            if row["LicenseNo"].isdigit():
                ln_ = int(row["LicenseNo"])
                lic_min = ln_ if lic_min is None else min(lic_min, ln_)
                lic_max = ln_ if lic_max is None else max(lic_max, ln_)
            if row["State"] == "CA":
                st["rows_ca"] += 1
            fields = [("BusinessName", row["BusinessName"]), ("BUS-NAME-2", row["BUS-NAME-2"]),
                      ("FullBusinessName", row["FullBusinessName"])]
            hits = {}
            for fld, val in fields:
                if not val:
                    continue
                for _, s, c, t in name_variants(val):
                    for gi in S.get(s, ()):
                        hits.setdefault(gi, []).append(("E", fld, val))
                    for gi in C.get(c, ()):
                        hits.setdefault(gi, []).append(("C", fld, val))
                    for gi in T.get(t, ()) if t else ():
                        hits.setdefault(gi, []).append(("T", fld, val))
            if not hits:
                continue
            addr = [(row["MailingAddress"], row["City"], row["ZIPCode"])]
            cslb_stricts = {v[1] for _f, val in fields if val for v in name_variants(val)}
            for gi, hs in hits.items():
                rank = {"E": 3, "C": 2, "T": 1}
                rel, fld, val = max(hs, key=lambda h: rank[h[0]])
                sv = {v[1] for nm in groups[gi]["names"] for v in name_variants(nm)}
                if len(sv) >= 2 and sv <= cslb_stricts:
                    rel = "D"  # the legal name AND the DBA both appear on the license record
                lv, pair = best_addr_level(groups[gi]["addrs"], addr)
                conf = confidence(rel, lv)
                if conf == "none":
                    names_only[gi] += 1
                    continue
                issue = iso(row["IssueDate"])
                cands[gi][row["LicenseNo"]] = dict(
                    license=row["LicenseNo"], name_rel=rel, matched_field=fld, matched_text=val,
                    addr_level=lv, confidence=conf, business_name=row["BusinessName"],
                    name2=row["BUS-NAME-2"], full_name=row["FullBusinessName"],
                    address=f'{row["MailingAddress"]}, {row["City"]} {row["State"]} {row["ZIPCode"]}'.strip(),
                    issue_date=issue, reissue_date=iso(row["ReissueDate"]), status=row["PrimaryStatus"],
                    classes=re.sub(r"[\s|]+", " ", row["Classifications(s)"]).strip(),
                    business_type=row["BusinessType"], last_update=iso(row["LastUpdate"]),
                    county=row["County"])
    st["license_no_min"], st["license_no_max"] = lic_min, lic_max
    return dict(enc=enc, stats=dict(st), cands=cands, names_only=names_only)


def decide_registration(g, res, cinfo):
    """Fill g['reg'] from CSLB candidates. Only one 'high' candidate counts as verified."""
    asof_text, partial = cinfo["asof"], cinfo["state"] == "partial"
    reg = dict(source="CSLB License Master" + (" (PARTIAL file)" if partial else ""),
               state="unavailable" if cinfo["state"] == "missing" else "none", cand=None, evidence="", others=[])
    if cinfo["state"] == "missing":
        reg["evidence"] = "CSLB License Master unavailable this run (see source-ledger.csv)"
        g["reg"] = reg
        return
    cs = list(res["cands"].get(g["gi"], {}).values())
    rank = {"high": 3, "medium": 2, "low": 1}
    cs.sort(key=lambda c: (-rank[c["confidence"]], c["issue_date"] or "9999", c["license"]))
    highs = [c for c in cs if c["confidence"] == "high"]
    if len(highs) == 1:
        reg["state"], reg["cand"] = "verified", highs[0]
    elif len(highs) > 1:
        reg["state"], reg["others"] = "ambiguous", highs
    elif cs and cs[0]["confidence"] == "medium":
        mediums = [c for c in cs if c["confidence"] == "medium"]
        reg["state"] = "probable" if len(mediums) == 1 else "ambiguous"
        reg["cand"] = mediums[0] if len(mediums) == 1 else None
        reg["others"] = [] if len(mediums) == 1 else mediums
    elif cs:
        reg["state"], reg["others"] = "weak", cs
    reg["all"] = cs
    c = reg["cand"]
    if reg["state"] == "none" and partial:
        reg["state"] = "unknown_partial_source"
        reg["evidence"] = ("CSLB file received was partial (see source-ledger.csv); this business may hold a license "
                           "in the part not received. Unknown, not a no.")
    if c:
        dba = ""
        if c["full_name"] and " DBA " in c["full_name"].upper():
            dba = f' CSLB lists DBA: "{c["full_name"]}".'
        elif c["name2"] and norm(c["name2"]) != norm(c["business_name"]):
            dba = f' CSLB lists two names: "{c["business_name"]}" and "{c["name2"]}".'
        reg["evidence"] = (
            f'Name: {NAME_WORDS[c["name_rel"]]}, on CSLB {c["matched_field"]} "{c["matched_text"]}". '
            f'Address: {ADDR_WORDS[c["addr_level"]]} (SBA loan: {g["addrs"][0][0]}, {g["addrs"][0][1]} {g["addrs"][0][2]}; '
            f'CSLB mailing: {c["address"]}).{dba} CSLB IssueDate {c["issue_date"] or "blank"}'
            f'{" (license reissued " + c["reissue_date"] + ")" if c["reissue_date"] else ""}. CSLB master: {asof_text}.'
            + (" CSLB file was partial, so other licenses for this business could not be checked." if partial else ""))
    elif reg["others"]:
        reg["evidence"] = ("Several CSLB licenses fit equally well: " + "; ".join(
            f'#{o["license"]} {o["business_name"]} issued {o["issue_date"]} ({o["confidence"]})'
            for o in reg["others"][:6]) + ". Needs a human to choose.")
    g["reg"] = reg


# ---------------------------------------------------------------- stage 4: DOL Form 5500 / 5500-SF
PENSION = re.compile(r"[1-3][A-Z]")
NAME_WORDS = {"E": "exact match", "C": "same once legal suffixes (Inc, LLC) are ignored",
              "T": "same distinctive words only", "D": "documented DBA: both the legal name and the DBA appear on the record",
              "EIN": "EIN supplied"}
ADDR_WORDS = {"street": "same street address", "street_loose": "same house number and street name",
              "zip": "same ZIP only", "city": "same city only", "none": "no agreement", "ein": "not needed (EIN)"}


def stage_dol(groups, tracker_eins):
    alias_s, alias_c, alias_t = (collections.defaultdict(set) for _ in range(3))
    for gi, g in enumerate(groups):
        names = list(g["names"])
        reg = g.get("reg", {})
        if reg.get("state") == "verified":  # alternate/DBA names from a verified license
            c = reg["cand"]
            names += [n for n in (c["business_name"], c["name2"], c["full_name"]) if n]
        g["alias_names"] = names
        for nm in names:
            for _, s, c_, t in name_variants(nm):
                alias_s[s].add(gi)
                if c_:
                    alias_c[c_].add(gi)
                if t and len(t) >= 4:
                    alias_t[t].add(gi)
        addrs = list(g["addrs"])
        if reg.get("state") == "verified":
            c = reg["cand"]
            m = re.match(r"(.*), (.*?) CA (\S*)$", c["address"])
            if m:
                addrs.append((m.group(1), m.group(2), m.group(3)))
        g["match_addrs"] = addrs
    ein_to_gi = {e: gi for gi, g in enumerate(groups) for e in [tracker_eins.get(g["biz_id"])] if e}
    found = collections.defaultdict(list)  # gi -> list of filings
    nameonly = collections.defaultdict(list)  # gi -> same-name sponsors with no address agreement
    stats = {}
    files = []
    for y in fetch.DOL_YEARS:
        for stem, fmap, label in (("F_5500", F5500, "5500"), ("F_5500_SF", FSF, "5500-SF")):
            files.append((y, stem, fmap, label))
    for y, stem, fmap, label in files:
        zpath = os.path.join(fetch.RAW, "dol", f"{stem}_{y}_Latest.zip")
        sid = f"DOL-{label.replace('-', '')}-{y}"
        fs = collections.Counter()
        if not os.path.exists(zpath):
            stats[sid] = dict(status="missing")
            continue
        zf = zipfile.ZipFile(zpath)
        member = [n for n in zf.namelist() if n.lower().endswith(".csv")][0]
        enc = detect_encoding(lambda: zf.open(member))
        rng = {"begin": [None, None], "recv": [None, None]}
        with zf.open(member) as raw:
            rd = csv.DictReader(io.TextIOWrapper(raw, encoding=enc["codec"], newline=""))
            for need in ("ack", "sponsor", "dba", "ein", "codes", "begin", "end", "received", "pn"):
                if fmap[need] not in rd.fieldnames:
                    raise SystemExit(f"{sid}: missing column {fmap[need]}")
            for row in rd:
                fs["rows"] += 1
                for key, col in (("begin", fmap["begin"]), ("recv", fmap["received"])):
                    d = iso(row[col])
                    if d and "1980" <= d[:4] <= "2030":
                        r = rng[key]
                        r[0] = d if r[0] is None or d < r[0] else r[0]
                        r[1] = d if r[1] is None or d > r[1] else r[1]
                codes = "".join(PENSION.findall((row[fmap["codes"]] or "").replace(" ", "")))
                ein = (row[fmap["ein"]] or "").strip()
                hit = {}
                if ein and ein in ein_to_gi:
                    hit[ein_to_gi[ein]] = ("EIN", "high")
                for nmcol in (fmap["sponsor"], fmap["dba"]):
                    val = row[nmcol]
                    if not val:
                        continue
                    for _, s, c_, t in name_variants(val):
                        for gi in alias_s.get(s, ()):
                            hit.setdefault(gi, ("E", None))
                        for gi in alias_c.get(c_, ()):
                            if gi not in hit:
                                hit[gi] = ("C", None)
                        for gi in alias_t.get(t, ()) if t else ():
                            if gi not in hit:
                                hit[gi] = ("T", None)
                if not hit:
                    continue
                addrs = []
                for col in (fmap["mail"], fmap["loc"]):
                    a = tuple((row[c] or "").strip() for c in col)
                    if a[0] or a[3]:
                        addrs.append((a[0], a[1], a[3], a[2]))
                for gi, (rel, forced) in hit.items():
                    if forced:
                        lv, conf, pair = "ein", "high", None
                    else:
                        lv, pair = best_addr_level(groups[gi]["match_addrs"], [a[:3] for a in addrs])
                        conf = confidence(rel, lv)
                    if conf == "none":
                        fs["name_hits_without_address"] += 1
                        if rel in ("E", "C") and codes:
                            sts = {a[3] for a in addrs if a[3]}
                            fs["name_only_hits_out_of_state" if sts and "CA" not in sts else "name_only_hits_in_state"] += 1
                            nameonly[gi].append(dict(
                                source_id=sid, ack_id=row[fmap["ack"]], ein=ein, sponsor=row[fmap["sponsor"]],
                                sponsor_address="; ".join(f"{a[0]}, {a[1]} {a[3]} {a[2]}" for a in addrs),
                                sponsor_state=",".join(sorted(sts)), name_relation=rel, plan_begin=iso(row[fmap["begin"]]),
                                plan_end=iso(row[fmap["end"]]), filed=iso(row[fmap["received"]]), benefit_codes=codes))
                        continue
                    fs["name_address_hits"] += 1
                    if not codes:
                        fs["hits_rejected_no_retirement_benefit_code"] += 1
                        continue
                    states = {a[3] for a in addrs}
                    found[gi].append(dict(
                        source_id=sid, form=label, year=y, ack_id=row[fmap["ack"]], ein=ein,
                        pn=(row[fmap["pn"]] or "").strip(), plan_name=row[fmap["plan_name"]],
                        sponsor=row[fmap["sponsor"]], dba=row[fmap["dba"]], name_rel=rel,
                        addr_level=lv, confidence=conf,
                        sponsor_addr="; ".join(f"{a[0]}, {a[1]} {a[3]} {a[2]}" for a in addrs),
                        sponsor_state=",".join(sorted(s for s in states if s)),
                        begin=iso(row[fmap["begin"]]), end=iso(row[fmap["end"]]),
                        received=iso(row[fmap["received"]]),
                        act_eoy=(row[fmap["act_eoy"]] or "").strip(), act_boy=(row[fmap["act_boy"]] or "").strip(),
                        codes=codes))
        stats[sid] = dict(status="ok", rows=fs["rows"], plan_begin_min=rng["begin"][0],
                          plan_begin_max=rng["begin"][1], received_min=rng["recv"][0],
                          received_max=rng["recv"][1], encoding=enc["chosen"], counters=dict(fs),
                          encoding_detail=enc)
    return found, stats, nameonly


def decide_pension(g, filings):
    """Pick one primary plan; never sum plans. Only high-confidence filings are accepted."""
    out = dict(status="unknown_no_filing_found", primary=None, plan_count=0, others=[], accepted=[],
               review=[])
    highs = [f for f in filings if f["confidence"] == "high"]
    review = [f for f in filings if f["confidence"] != "high"]
    out["review"] = review
    if not highs:
        if review:
            out["status"] = "candidate_needs_review"
        g["pension"] = out
        return
    eins = {f["ein"] for f in highs if f["ein"]}
    if len(eins) > 1:
        out["status"], out["review"] = "ambiguous_multiple_sponsors", review + highs
        g["pension"] = out
        return
    latest = {}  # one row per (EIN, plan number, plan-year begin): newest filing wins
    for f in highs:
        k = (f["ein"], f["pn"], f["begin"])
        if k not in latest or (f["received"], f["ack_id"]) > (latest[k]["received"], latest[k]["ack_id"]):
            latest[k] = f
    uniq = sorted(latest.values(), key=lambda f: (f["end"] or f["begin"], f["received"], f["pn"]), reverse=True)
    primary = uniq[0]
    per_plan = {}
    for f in uniq:
        per_plan.setdefault((f["ein"], f["pn"]), f)  # newest period of each distinct plan
    out.update(status="plan_found", primary=primary, accepted=uniq, plan_count=len(per_plan),
               others=[f for k, f in per_plan.items() if k != (primary["ein"], primary["pn"])])
    g["pension"] = out


# ---------------------------------------------------------------- eligibility
def decide_eligibility(g, dup_names, partial=False):
    reasons, reg, loans = [], g["reg"], g["loans"]
    if any(l["nonprofit"].upper() == "Y" or "non-profit" in l["business_type"].lower() for l in loans):
        return "closed", "clear exclusion: borrower is flagged non-profit in the SBA file"
    if any(l["county_zip_check"] != "agree" for l in loans):
        reasons.append("county evidence conflict (project county and borrower ZIP disagree)")
    if g["group_key"] in dup_names:
        reasons.append("same legal name appears at another address (possible duplicate business)")
    st = reg["state"]
    if st == "verified":
        c = reg["cand"]
        if not c["issue_date"]:
            reasons.append("verified license has no issue date")
        elif is_30plus(c["issue_date"]):
            if c["status"].strip().upper() != "CLEAR":
                reasons.append(f'license status is "{c["status"]}", not CLEAR')
            if reasons:
                return "review", "; ".join(reasons)
            return "ready", (f'verified CSLB license #{c["license"]} issued {c["issue_date"]} ({age_years(c["issue_date"])} years before {AS_OF})'
                             + ("; CSLB file was partial, but any unseen license could only make the business older" if partial else ""))
        else:
            if partial:
                return "review", (f'verified CSLB license #{c["license"]} was issued {c["issue_date"]} (under {AGE_YEARS} years), but the CSLB '
                                  f'file was partial, so an older license for this business may exist. Not closed.')
            if reasons:
                return "review", "; ".join(reasons)
            return "closed", (f'clear exclusion by the {AGE_YEARS}-year rule: verified CSLB license #{c["license"]} was issued '
                              f'{c["issue_date"]}. The business could be older than this license (for example a successor entity), '
                              f'which this data cannot show.')
    why = {"unavailable": "registration source unavailable", "none": "no CSLB license found with name and address agreement",
           "probable": "probable CSLB license match is not corroborated enough to verify",
           "ambiguous": "more than one CSLB license fits; needs a human to choose",
           "weak": "only weak CSLB candidates (name with city or ZIP only)",
           "unknown_partial_source": "no license found, but the CSLB file received was partial; registration date unknown"}[st]
    return "review", "; ".join([why] + reasons)


# ---------------------------------------------------------------- outputs
def fmt_list(vals):
    return ";".join(vals)


def draw_cols(g):
    out = {}
    for d, label in (("1", "first"), ("2", "second")):
        ls = [l for l in g["loans"] if l["draw"] == d]
        out[f"{label}_draw_loan_number"] = fmt_list(l["loan_number"] for l in ls)
        out[f"{label}_draw_date"] = fmt_list(l["date_approved"] for l in ls)
        out[f"{label}_draw_amount_usd"] = fmt_list(l["current_approval_amount"] for l in ls)
        out[f"hist_jobs_reported_{label}_draw"] = fmt_list(l["hist_jobs_reported"] for l in ls)
        out[f"hist_payroll_proxy_{label}_draw_usd"] = fmt_list(l["hist_payroll_proxy_usd"] or "unknown" for l in ls) if ls else ""
    out["payroll_proxy_basis"] = " | ".join(
        f'{"first" if l["draw"] == "1" else "second"}_draw {l["loan_number"]}: {l["payroll_proxy_basis"]}'
        for l in g["loans"])
    return out


def tracker_row(g, src_asof):
    l0, reg, pen = g["key_loan"], g["reg"], g["pension"]
    c = reg["cand"]
    others = [n for n in g["names"] if norm(n) != norm(g["legal_name"])]
    if c and reg["state"] in ("verified", "probable"):
        others += [f'{n} (CSLB #{c["license"]})' for n in (c["business_name"], c["name2"]) if n and norm(n) != norm(g["legal_name"])]
    row = dict(
        biz_id=g["biz_id"], legal_name=g["legal_name"], other_names=" | ".join(others), address=l0["borrower_address"],
        city=l0["city"], state=l0["state"], zip=l0["zip"], county=COUNTY.title(),
        loan_count=len(g["loans"]), loan_numbers=fmt_list(l["loan_number"] for l in g["loans"]),
        draws=fmt_list(sorted({("first" if l["draw"] == "1" else "second") for l in g["loans"]}, reverse=True)),
        loan_evidence_url=SBA_PAGE, age_as_of=AS_OF)
    row.update(draw_cols(g))
    row.update(reg_source=reg["source"])
    if c and reg["state"] in ("verified", "probable"):
        yrs = age_years(c["issue_date"])
        ver = reg["state"] == "verified"
        row.update(reg_record_id=c["license"], reg_name_on_record=c["full_name"] or c["business_name"],
                   reg_date=c["issue_date"],
                   reg_date_meaning=("Original CSLB license issue date (IssueDate); a license date, not an SOS "
                                     "charter or business start date" + ("; license was reissued " + c["reissue_date"] if c["reissue_date"] else "")),
                   reg_age_years=(yrs if yrs is not None else "") if ver else "",
                   age_30plus=(("Y" if is_30plus(c["issue_date"]) else "N") if c["issue_date"] else "unknown") if ver else "unknown",
                   reg_status=c["status"], reg_class=c["classes"], reg_evidence_url=CSLB_DETAIL)
    else:
        row.update(reg_record_id="", reg_date="", reg_age_years="", age_30plus="unknown",
                   reg_date_meaning="unknown: no verified registration or license date",
                   reg_status="", reg_class="", reg_evidence_url="")
    row["reg_match_confidence"] = {"verified": "high", "probable": "medium", "ambiguous": "ambiguous",
                                   "weak": "low", "none": "none", "unavailable": "unavailable",
                                   "unknown_partial_source": "unknown_partial_source"}[reg["state"]]
    row["reg_match_evidence"] = reg["evidence"]
    row["pension_status"] = pen["status"]
    row["pension_source_id"] = "DOL-5500 / DOL-5500SF"
    row["pension_plan_count"] = pen["plan_count"] if pen["status"] == "plan_found" else ""
    p = pen["primary"]
    if p:
        row.update(pension_form=p["form"], pension_plan_id=f'{p["ein"]}-{p["pn"]}', pension_plan_name=p["plan_name"],
                   pension_plan_period_begin=p["begin"], pension_plan_period_end=p["end"],
                   pension_filing_date=p["received"], pension_active_participants=p["act_eoy"],
                   pension_benefit_codes=p["codes"], pension_sponsor_ein=p["ein"], pension_ack_id=p["ack_id"],
                   pension_match_confidence="high",
                   pension_match_evidence=(f'Sponsor "{p["sponsor"]}" ({p["sponsor_addr"]}); name {p["name_rel"]}, address {p["addr_level"]}. '
                                           f'Active participants are the plan-year-end count on this filing, not company headcount.'),
                   pension_source_id=p["source_id"], pension_evidence_url=fetch.DOL_URL.format(y=p["year"], f="F_5500_SF" if p["form"] == "5500-SF" else "F_5500"))
        row["pension_other_plans"] = " | ".join(
            f'{o["ein"]}-{o["pn"]} {o["begin"]}..{o["end"]} active {o["act_eoy"] or "n/a"} (ack {o["ack_id"]})' for o in pen["others"])
    elif pen["review"]:
        top = pen["review"][0]
        row["pension_match_confidence"] = top["confidence"]
        row["pension_match_evidence"] = (f'{len(pen["review"])} unverified DOL candidate(s); best: sponsor "{top["sponsor"]}", '
                                         f'EIN {top["ein"]}, name {top["name_rel"]}, address {top["addr_level"]}. Not counted as a match.')
    else:
        extra = ""
        if g.get("nameonly"):
            oos = sum(1 for h in g["nameonly"] if h["sponsor_state"] and "CA" not in h["sponsor_state"])
            extra = (f' {len(g["nameonly"])} same-name filing(s) with no address agreement were not counted '
                     f'({oos} out of state); see scout/state/dol-name-only-hits.csv.')
        row["pension_match_evidence"] = ("No retirement-plan filing found with name and address (or EIN) agreement in the "
                                         "2025, 2024 or 2023 files. Unknown, not a negative." + extra)
    return row


def match_ledger_rows(groups, asof):
    out, n = [], 0

    def add(**kw):
        nonlocal n
        n += 1
        out.append(dict(ledger_id=f"m-{n:05d}", **kw))

    for g in groups:
        add(biz_id=g["biz_id"], source_id="SBA-PPP-150K", record_id=";".join(l["loan_number"] for l in g["loans"]),
            match_basis="business group: " + (", ".join(g["rules"]) or "single loan"),
            name_in_scout=" | ".join(g["names"]), name_in_source=" | ".join(g["names"]), address_in_scout="",
            address_in_source=" | ".join(sorted({f'{l["borrower_address"]}, {l["city"]} {l["zip"]}' for l in g["loans"]})),
            address_level="", confidence="high" if len(g["loans"]) == 1 or g["rules"] else "n/a",
            decision="grouped", evidence_text=f'{len(g["loans"])} loan(s) kept separately by loan number and draw',
            evidence_url=SBA_PAGE, source_as_of=asof.get("SBA-PPP-150K", ""))
        reg = g["reg"]
        for cd in reg.get("all", []):
            decision = ("accepted" if reg["state"] == "verified" and reg["cand"] is cd else "review")
            add(biz_id=g["biz_id"], source_id="CSLB-MASTER", record_id=cd["license"],
                match_basis=f'name {cd["name_rel"]} on {cd["matched_field"]}', name_in_scout=g["legal_name"],
                name_in_source=" | ".join(x for x in (cd["business_name"], cd["name2"], cd["full_name"]) if x),
                address_in_scout=f'{g["addrs"][0][0]}, {g["addrs"][0][1]} {g["addrs"][0][2]}', address_in_source=cd["address"],
                address_level=cd["addr_level"], confidence=cd["confidence"], decision=decision,
                evidence_text=f'look up license #{cd["license"]} at the evidence URL; IssueDate {cd["issue_date"]}; status {cd["status"]}; classes {cd["classes"]}',
                evidence_url=CSLB_DETAIL, source_as_of=asof.get("CSLB-MASTER", ""))
        pen = g["pension"]
        accepted = {id(f) for f in pen["accepted"]}
        for f in pen["accepted"] + pen["review"]:
            add(biz_id=g["biz_id"], source_id=f["source_id"], record_id=f'{f["ack_id"]}|{f["ein"]}-{f["pn"]}',
                match_basis=f'name {f["name_rel"]}', name_in_scout=g["legal_name"],
                name_in_source=" | ".join(x for x in (f["sponsor"], f["dba"]) if x),
                address_in_scout=f'{g["addrs"][0][0]}, {g["addrs"][0][1]} {g["addrs"][0][2]}',
                address_in_source=f["sponsor_addr"], address_level=f["addr_level"], confidence=f["confidence"],
                decision="accepted" if id(f) in accepted else "review",
                evidence_text=f'plan {f["begin"]}..{f["end"]}, filed {f["received"]}, active participants {f["act_eoy"] or "n/a"}, codes {f["codes"]}',
                evidence_url=fetch.DOL_URL.format(y=f["year"], f="F_5500_SF" if f["form"] == "5500-SF" else "F_5500"),
                source_as_of=asof.get(f["source_id"], ""))
    return out


def log_unavailable(source_id, reason):
    """Record an unavailable source once (same reason is not logged again)."""
    cur = json.load(open(SOURCE_STATUS)) if os.path.exists(SOURCE_STATUS) else {}
    if cur.get(source_id, {}).get("reason") != reason:
        cur[source_id] = dict(reason=reason, first_logged_utc=fetch.now())
        os.makedirs(os.path.dirname(SOURCE_STATUS), exist_ok=True)
        json.dump(cur, open(SOURCE_STATUS, "w"), indent=1, sort_keys=True)
    return cur


def first_retrieved(source_id, sha):
    first = None
    if os.path.exists(fetch.LOG):
        for line in open(fetch.LOG, encoding="utf-8"):
            e = json.loads(line)
            if e.get("source_id") == source_id and e.get("sha256") == sha and e.get("event") in (
                    "downloaded", "adopted_existing", "kept_partial", "manual_import", "retrieval_time"):
                t = e.get("retrieved_utc") or e["logged_utc"]
                if first is None or t < first:
                    first = t
    return first or ""


def build_source_ledger(results, sba, cslb, dol_stats, cinfo):
    rows, asof = [], {}
    old, _ = read_csv(SRC_LEDGER)
    old = {(r["source_id"], r["sha256"]): r for r in old}
    status_file = json.load(open(SOURCE_STATUS)) if os.path.exists(SOURCE_STATUS) else {}
    for res in results:
        sid = res["id"]
        sha = res.get("sha256", "")
        rec = dict(source_id=sid, name=res["name"], url=res["url"], sha256=sha, bytes=res.get("bytes", ""),
                   status=res["status"], last_verified_utc=fetch.now(), notes="")
        rec["first_retrieved_utc"] = (old.get((sid, sha), {}).get("first_retrieved_utc") or first_retrieved(sid, sha)) if sha else ""
        h = res.get("head", {}) or {}
        rec["http_last_modified"] = h.get("last_modified") or ""
        if sid == "SBA-PPP-150K":
            rec.update(encoding=f'{sba["enc"]["chosen"]} (strict UTF-8 valid: {sba["enc"]["utf8_strict_valid"]}; decoded as {sba["enc"]["codec"]})',
                       rows=sba["stats"]["source_rows"],
                       reporting_period=f'Loans approved {sba["approvals_min"]} to {sba["approvals_max"]}; SBA FOIA release dated 2024-09-30 (file name suffix 240930)',
                       role="loan universe", notes=f'{sba["stats"].get("malformed_rows", 0)} malformed rows; {sba["stats"].get("duplicate_loan_numbers", 0)} duplicate loan numbers')
            asof[sid] = "2024-09-30"
        elif sid == "CSLB-MASTER":
            rec["role"] = "registration / original trade-license date"
            if cinfo["state"] != "missing":
                f_ = cinfo["file"]
                sha = fetch.sha256_file(f_)
                rec.update(sha256=sha, bytes=os.path.getsize(f_), status="ok" if cinfo["state"] == "complete" else cinfo["state"],
                           first_retrieved_utc=old.get((sid, sha), {}).get("first_retrieved_utc") or first_retrieved(sid, sha))
                st_ = cslb["stats"]
                dec = ", ".join(f'{k[7:]}: {v:,}' for k, v in sorted(st_.items()) if k.startswith("issued_"))
                rec.update(encoding=cslb["enc"]["chosen"], rows=st_["rows"],
                           reporting_period=(f'CSLB portal "{cinfo["asof"]}". Licenses by issue decade in the rows received: {dec}. '
                                             f'License numbers {st_["license_no_min"]} to {st_["license_no_max"]}.'))
                rec["notes"] = ("COMPLETE file." if cinfo["state"] == "complete" else
                                f'PARTIAL: download cut mid-stream ({st_.get("malformed_rows", 0)} cut-off row dropped). '
                                f'No licenses issued 1950-2014 were received.') + " The portal lists only current or expired-but-renewable licenses."
                asof[sid] = cinfo["asof"]
            else:
                rec["notes"] = status_file.get(sid, {}).get("reason", res.get("error", "unavailable"))
        else:
            d = dol_stats.get(sid, {})
            rec["role"] = "retirement plan filings"
            if d.get("status") == "ok":
                rec.update(encoding=d["encoding"], rows=d["rows"],
                           reporting_period=f'Plan years beginning {d["plan_begin_min"]}..{d["plan_begin_max"]} (dates outside 1980-2030 ignored as entry errors); filings received {d["received_min"]}..{d["received_max"]}')
                rec["notes"] = f'name+address hits: {d["counters"].get("name_address_hits", 0)}; rejected for no retirement benefit code: {d["counters"].get("hits_rejected_no_retirement_benefit_code", 0)}'
            asof[sid] = h.get("last_modified") or ""
        rows.append(rec)
    fields = ["source_id", "name", "url", "role", "first_retrieved_utc", "last_verified_utc", "http_last_modified",
              "reporting_period", "bytes", "sha256", "encoding", "rows", "status", "notes"]
    write_csv(SRC_LEDGER, fields, rows)
    return asof


def write_tracker(groups, merged_ids, src_asof):
    old_rows, old_fields = read_csv(TRACKER)
    by_id = {r["biz_id"]: r for r in old_rows}
    extras = [c for c in old_fields if c not in OWNED and c != OUTREACH]
    fields = OWNED + [OUTREACH] + extras
    new_ids = []
    seen = set()
    for g in groups:
        row = tracker_row(g, src_asof)
        st, why = g["eligibility"]
        row["eligibility_status"], row["eligibility_reason"] = st, why
        cur = by_id.get(g["biz_id"])
        seen.add(g["biz_id"])
        if cur is None:
            row[OUTREACH] = "ready"
            by_id[g["biz_id"]] = row
            new_ids.append(g["biz_id"])
        else:
            for k, v in row.items():
                if k in USER_INPUT and (cur.get(k) or "").strip():
                    continue
                cur[k] = v
            for k in USER_INPUT:
                row.setdefault(k, "")
    for bid, cur in by_id.items():
        if bid in merged_ids:
            cur["eligibility_status"], cur["eligibility_reason"] = "closed", "merged into another business ID after new evidence"
        elif bid not in seen and cur.get("eligibility_status") != "closed":
            cur["eligibility_status"], cur["eligibility_reason"] = "closed", "no longer present in the current SBA extract"
    order = [r["biz_id"] for r in old_rows] + [i for i in new_ids]
    for i in sorted(set(by_id) - set(order), key=lambda s: int(s.split("-")[1])):
        order.append(i)
    write_csv(TRACKER, fields, [by_id[i] for i in order])
    return new_ids


# ---------------------------------------------------------------- orchestration
def run(skip_fetch=False):
    t0 = fetch.now()
    results = []
    for s in fetch.SOURCES:
        results.append(fetch_source(s, skip_fetch))
    by_id = {r["id"]: r for r in results}
    sba_res = by_id["SBA-PPP-150K"]
    if sba_res["status"] != "ok":
        raise SystemExit("SBA PPP file unavailable; nothing to build. See scout/logs/download-log.jsonl")
    sba_path = os.path.join(fetch.RAW, "sba", "public_150k_plus_240930.csv")
    sba = stage_sba(sba_path)
    loans = [loan_record(r, sba_res["sha256"]) for r in sba["loans"]]
    groups = build_groups(loans)
    events, merged = assign_ids(groups)
    for gi, g in enumerate(groups):
        g["gi"] = gi
    cinfo = cslb_local()
    cslb = stage_cslb(groups, cinfo["file"]) if cinfo["state"] != "missing" else None
    if cinfo["state"] == "complete" and os.path.exists(SOURCE_STATUS):  # resolved: drop the old unavailable note
        cur = json.load(open(SOURCE_STATUS))
        if cur.pop("CSLB-MASTER", None) is not None:
            json.dump(cur, open(SOURCE_STATUS, "w"), indent=1, sort_keys=True)
    if cinfo["state"] != "complete":
        extra = ""
        if cinfo["state"] == "partial":
            extra = (f' A partial file ({os.path.getsize(cinfo["file"]):,} bytes, whole rows only) is used in limited mode: '
                     f'a match found in it is real, but "not found" and "license under 30 years" stay review, never closed.')
        log_unavailable("CSLB-MASTER", "Full CSLB License Master could not be downloaded from this environment: the portal stream was "
                        "cut at about 24-28 MB, and later requests were refused with HTTP 403 / Request Rejected by CSLB's firewall." + extra +
                        " To finish: download the License Master CSV by hand in a browser from "
                        "https://www.cslb.ca.gov/onlineservices/dataportal/ContractorList and run: "
                        "python3 -I scout/fetch.py import-cslb <file> \"<Updated as of ...>\"")
    for g in groups:
        decide_registration(g, cslb or {"cands": {}}, cinfo)
    old_rows, _ = read_csv(TRACKER)
    eins = {r["biz_id"]: re.sub(r"\D", "", r.get("ein_known", "")) for r in old_rows if len(re.sub(r"\D", "", r.get("ein_known", ""))) == 9}
    found, dol_stats, nameonly = stage_dol(groups, eins)
    write_csv(P("scout", "state", "dol-name-only-hits.csv"),
              ["biz_id", "source_id", "ack_id", "ein", "sponsor", "sponsor_address", "sponsor_state", "name_relation",
               "plan_begin", "plan_end", "filed", "benefit_codes"],
              [dict(biz_id=groups[gi]["biz_id"], **h) for gi in sorted(nameonly) for h in nameonly[gi]])
    for gi, g in enumerate(groups):
        g["nameonly"] = nameonly.get(gi, [])
    for gi, g in enumerate(groups):
        decide_pension(g, found.get(gi, []))
    names_core = collections.defaultdict(list)
    for g in groups:
        v = name_variants(g["legal_name"])
        if v:
            names_core[v[0][2]].append(g["group_key"])
    dup = {k for ks in names_core.values() if len(ks) > 1 for k in ks}
    for g in groups:
        g["eligibility"] = decide_eligibility(g, dup, cinfo["state"] == "partial")
    asof = build_source_ledger(results, sba, cslb, dol_stats, cinfo)
    write_csv(LOANS, list(loans[0].keys()), sorted(loans, key=lambda l: (l["biz_id"], l["date_approved"], l["loan_number"])))
    new_ids = write_tracker(groups, merged, asof)
    write_csv(MATCH_LEDGER, ["ledger_id", "biz_id", "source_id", "record_id", "match_basis", "name_in_scout", "name_in_source",
                             "address_in_scout", "address_in_source", "address_level", "confidence", "decision",
                             "evidence_text", "evidence_url", "source_as_of"], match_ledger_rows(groups, asof))
    cnt = collections.Counter(g["eligibility"][0] for g in groups)
    stats = dict(run_utc=t0, as_of=AS_OF, region=REGION, sba=dict(sba["stats"], encoding=sba["enc"], approvals_min=sba["approvals_min"], approvals_max=sba["approvals_max"], header_cols=sba["header_cols"]),
                 business_groups=len(groups), loans=len(loans), new_ids=new_ids, id_events=events,
                 eligibility=dict(cnt), registration=dict(collections.Counter(g["reg"]["state"] for g in groups)),
                 pension=dict(collections.Counter(g["pension"]["status"] for g in groups)),
                 cslb=(dict(cslb["stats"], encoding=cslb["enc"]) if cslb else "unavailable"), dol=dol_stats)
    os.makedirs(os.path.dirname(STATS), exist_ok=True)
    json.dump(stats, open(STATS, "w"), indent=1, sort_keys=True, default=str)
    import report
    report.write_results(P("results.html"), groups, stats, read_csv(SRC_LEDGER)[0], read_csv(TRACKER)[0],
                         json.load(open(SOURCE_STATUS)) if os.path.exists(SOURCE_STATUS) else {})
    print(json.dumps({k: stats[k] for k in ("business_groups", "loans", "eligibility", "registration", "pension")}, indent=1))
    print("new ids this run:", len(new_ids))


def fetch_source(s, skip):
    if skip:
        dest = os.path.join(fetch.RAW, s["dest"])
        if os.path.exists(dest) and (s["kind"] != "cslb" or cslb_local()["state"] == "complete"):
            h = fetch.sha256_file(dest)
            res = dict(s, status="ok", sha256=h, bytes=os.path.getsize(dest), action="reused-no-network", head={})
            if s["kind"] == "cslb":
                res["portal_asof"] = open(dest + ".asof").read()
            return res
        return dict(s, status="unavailable", error="not downloaded (run with network)")
    return fetch.fetch(s)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "verify"])
    ap.add_argument("--skip-fetch", action="store_true")
    ap.add_argument("--repeat", action="store_true", help="verify: also run the rerun / ID-stability tests")
    a = ap.parse_args()
    if a.cmd == "run":
        run(a.skip_fetch)
    else:
        import verify
        sys.exit(verify.main(a.repeat))
