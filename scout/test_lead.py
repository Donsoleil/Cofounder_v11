"""Offline end-to-end test of the evidence stage. Everything here is SAMPLE data in a temp folder.

Fake SerpApi, fake Jev and a local web server stand in for the real services. The fake model is deliberately naive
(it says "retirement: yes" for any text containing "retire") so the quote gates have to catch its mistakes.
This tests the pipeline mechanics only; it makes no claim about Jev's accuracy.
    python3 -I scout/test_lead.py
"""
import http.server
import json
import os
import re
import shutil
import socketserver
import sys
import tempfile
import threading

TMP = tempfile.mkdtemp(prefix="lead-test-")
os.environ.update(SCOUT_HOME=TMP, SERPAPI_API_KEY="TESTKEY-serp-0000", TYPESAFE_API_KEY="TESTKEY-jev-0000", SCOUT_TEST="sample")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scout as S  # noqa: E402
import lead  # noqa: E402
import letters  # noqa: E402
import lead_verify  # noqa: E402

RESULTS = []


def T(name, cond, detail=""):
    RESULTS.append(bool(cond))
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{detail}]" if detail else ""))


FILL = " We repair, install and maintain residential plumbing, water heaters, furnaces and air conditioning systems across the county, with licensed and insured crews, honest estimates and same day service on most calls."
PAGES = {
    "/": '<html><head><title>Sample Alpha Plumbing Inc | Anaheim CA</title></head><body><h1>Sample Alpha Plumbing Inc.</h1><p>100 Test Way, Anaheim, CA 92801. Call (714) 555-0100.</p>'
         '<p>Serving Orange County since 1980. Family owned and operated.</p><a href="/about">About</a> <a href="/team">Our Team</a> <a href="/services">Services</a><script>var x="IGNORED";</script></body></html>',
    "/about": "<html><body><h1>Our story</h1><p>Sample Alpha Plumbing was founded by Walter Reed in 1980, and Walter retired in 1995. Owned and operated by Maria Lopez.</p><p>" + FILL + "</p></body></html>",
    "/team": "<html><body><h1>Meet the team</h1><p>Tom Jones, lead technician. Ana Ruiz, office manager. Maria Lopez, owner.</p><p>" + FILL + "</p></body></html>",
    "/services": "<html><body><h1>Services</h1><p>Plumbing, water heaters, furnaces and air conditioning.</p><p>" + FILL + "</p></body></html>",
    "/charlie": "<html><head><title>A different company</title></head><body><p>Welcome. 55 Unrelated Blvd, Elsewhere.</p><p>" + FILL + "</p></body></html>",
}


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/robots.txt":
            body, code = b"User-agent: *\nAllow: /\n", 200
        elif self.path in PAGES:
            body, code = PAGES[self.path].encode(), 200
        else:
            body, code = b"not found", 404
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8" if code == 200 and not self.path.endswith("txt") else "text/plain")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


srv = socketserver.TCPServer(("127.0.0.1", 0), H)
PORT = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{PORT}"

# ---- sample tracker
cols = S.OWNED + [S.OUTREACH, "score_from_other_prompt"]
base = {c: "" for c in cols}


def row(bid, name, addr, city, zipc, elig="ready", out="ready", **kw):
    return {**base, "biz_id": bid, "legal_name": name, "address": addr, "city": city, "state": "CA", "zip": zipc, "eligibility_status": elig,
            "outreach_status": out, "reg_match_confidence": "high", "reg_date": "1980-01-01", "reg_record_id": "999001",
            "reg_evidence_url": "https://www2.cslb.ca.gov/OnlineServices/CheckLicenseII/CheckLicense.aspx", "score_from_other_prompt": "keep-me", **kw}


rows = [row("biz-901", "SAMPLE ALPHA PLUMBING INC", "100 TEST WAY", "ANAHEIM", "92801"),
        row("biz-902", "SAMPLE BRAVO HEATING INC", "200 TEST WAY", "IRVINE", "92602"),
        row("biz-903", "SAMPLE CHARLIE AIR INC", "300 TEST WAY", "ORANGE", "92865"),
        row("biz-904", "SAMPLE DELTA INC", "400 TEST WAY", "ORANGE", "92865", elig="review"),
        row("biz-905", "SAMPLE ECHO INC", "500 TEST WAY", "ORANGE", "92865"),
        row("biz-906", "SAMPLE FOXTROT INC", "600 TEST WAY", "ORANGE", "92865", out="closed")]
S.write_csv(S.TRACKER, cols, rows)
S.write_csv(lead.LP("suppression.csv"), ["biz_id", "name", "address", "reason", "added_utc", "source"],
            [dict(biz_id="biz-905", name="", address="", reason="test opt-out", added_utc="x", source="test")])
os.makedirs(lead.LP(), exist_ok=True)
json.dump(dict(name="Test Buyer", return_address=["1 Sample Street", "Sampletown, CA 90000"], contact="test@example.invalid",
               buyer_background="I have operated small service businesses for ten years.",
               interest="I am interested in learning how established plumbing and heating companies in Orange County are run."),
          open(lead.LP("sender.json"), "w"))

# ---- fake SerpApi
def mk_reviews(prefix, texts, n):
    out = []
    for i in range(n):
        t = texts.get(i, "Great job, fast and friendly. Would call again.")
        out.append(dict(review_id=f"{prefix}-{i:03d}", link=f"https://maps.example/review/{prefix}-{i:03d}", rating=5, date="a year ago", iso_date=f"2025-01-{(i % 27) + 1:02d}T00:00:00Z",
                        snippet=t, likes=0, user=dict(name=f"Reviewer {i}", contributor_id=str(1000 + i), thumbnail="https://x/y.png", local_guide=False, reviews=3), images=["https://x/i.jpg"]))
    return out


ALPHA = mk_reviews("A", {0: "Great service. The owner, Maria Lopez, personally installed our new furnace.",
                          1: "As a retired teacher I appreciate fast service and an honest owner.",
                          2: "The owner called me back but the technician installed the unit.",
                          3: "Our owner Maria is retiring at the end of the year.",
                          4: "Maria Lopez is the owner of this shop. Her daughter Elena will take over the business."}, 21)
BRAVO = mk_reviews("B", {}, 5)
CHARLIE = mk_reviews("C", {2: "Good work. FAILME please"}, 25)
SERP_CALLS = []


def fake_serp(p):
    SERP_CALLS.append(dict(p))
    lead.SPEND["serp_searches"] += 1
    if p["engine"] == "google_maps" and p.get("type") == "search":
        q = p["q"]
        if "ALPHA" in q:
            return dict(local_results=[dict(title="Sample Alpha Plumbing", address="100 Test Way, Anaheim, CA 92801", place_id="PID_A", data_id="D_A", reviews=21, rating=4.9, website=BASE + "/", phone="(714) 555-0100"),
                                       dict(title="Sample Alpha Plumbing", address="900 Other Rd, Fresno, CA 93701", place_id="PID_X", data_id="D_X", reviews=3, rating=3.0)])
        if "BRAVO" in q:
            return dict(place_results=dict(title="Sample Bravo Heating", address="200 Test Way, Irvine, CA 92602", place_id="PID_B", data_id="D_B", reviews=5, rating=4.0))
        if "CHARLIE" in q:
            return dict(local_results=[dict(title="Sample Charlie Air", address="300 Test Way, Orange, CA 92865", place_id="PID_C", data_id="D_C", reviews=30, rating=4.5, website=BASE + "/charlie")])
        return dict(error="Google hasn't returned any results for this query.")
    if p["engine"] == "google_maps" and p.get("type") == "place":
        return dict(place_results=dict(title="Sample Bravo Heating", reviews=5))
    if p["engine"] == "google_maps_reviews":
        pid = p.get("place_id")
        data, reported = {"PID_A": (ALPHA, 21), "PID_B": (BRAVO, 5), "PID_C": (CHARLIE, 30)}[pid]
        if "next_page_token" not in p:
            page, tok = data[:8], "t1" if len(data) > 8 else None
        else:
            page, tok = (data[7:] if pid == "PID_A" else data[8:]), None   # page 2 repeats one review for PID_A
        return dict(place_info=dict(title="x", reviews=reported), reviews=page, serpapi_pagination=dict(next_page_token=tok) if tok else {})
    raise AssertionError(p)


# ---- fake Jev: naive keyword model
JEV_CALLS = []


def fake_jev(payload):
    JEV_CALLS.append(payload)
    st = payload["state"]
    text = (st.get("context", "") + " " + st["text"]).strip()
    if "FAILME" in text:
        return 422, json.dumps(dict(error="validation failed"))
    ans = {}
    for q in payload["questions"]:
        if q == "owner_work":
            c = "yes" if re.search(r"owner.*(install|repair|fix)|personally", text, re.I) else "unknown"
        elif q == "retirement":
            c = "yes" if re.search(r"retir", text, re.I) else "unknown"
        elif q == "successor":
            c = "yes" if re.search(r"(his|her) (son|daughter)|take over", text, re.I) else "unknown"
        else:
            c = "current_owner" if re.search(r"owned and operated by|[A-Z][a-z]+ [A-Z][a-z]+,? (is )?(the )?owner|owner,? [A-Z][a-z]+ [A-Z][a-z]+", text) else ("historical" if "founded by" in text else "unknown")
        opts = list(payload["questions"][q]["criteria"])
        pr = {o: (0.9 if o == c else round(0.1 / (len(opts) - 1), 3)) for o in opts}
        ans[q] = dict(type="choice", choice=c, probabilities=pr, confidence=0.8)
    return 200, json.dumps(dict(model="jev-1.13.0", answers=ans, usage=dict(input_tokens=max(1, len(json.dumps(payload)) // 4), output_tokens=20)))


lead.serp_get, lead.jev_post = fake_serp, fake_jev
lead.http_get = lambda url: lead._http(url, headers={"Accept": "text/html"}, timeout=10)

print("== select, estimate")
sel = lead.cmd_select()
T("selection = ready+ready, unsuppressed, not closed, not review", [r["biz_id"] for r in sel] == ["biz-901", "biz-902", "biz-903"], str([r["biz_id"] for r in sel]))
est = lead.cmd_estimate()
T("estimate prints three scenarios per business", len(est) == 9)
print("== collect")
lead.cmd_collect(5.0)
cov = {b: json.load(open(lead.LP("coverage", f"{b}.json"))) for b in ("biz-901", "biz-902", "biz-903")}
T("namesake in Fresno rejected, true place matched", cov["biz-901"]["place"]["place_id"] == "PID_A" and any(c["decision"] == "rejected_namesake" for c in json.load(open(lead.LP("raw", "biz-901", "place-search.json")))["candidates"]))
T("ALPHA reviews reconcile: fetched 22, unique 21, reported 21, complete", (cov["biz-901"]["reviews"]["fetched"], cov["biz-901"]["reviews"]["unique"], cov["biz-901"]["reviews"]["reported"], cov["biz-901"]["status"]) == (22, 21, 21, "complete"), str(cov["biz-901"]["reviews"]))
T("ALPHA fetched home, about, team and services pages separately", cov["biz-901"]["pages"]["fetched"] == 4, str([a["kind"] + ":" + a["status"] for a in cov["biz-901"]["pages"]["attempts"]]))
T("BRAVO has no website listed and is still complete for reviews", cov["biz-902"]["status"] == "complete" and cov["biz-902"]["pages"]["status"] == "no_website_listed")
T("CHARLIE gap (30 reported, 25 unique) and wrong-site identity make it partial", cov["biz-903"]["status"] == "partial" and cov["biz-903"]["pages"]["status"] == "identity_failed", str(cov["biz-903"]["notes"]))
raw_all = "".join(open(os.path.join(dp, f), encoding="utf-8", errors="replace").read() for dp, _, fs in os.walk(lead.LP("raw")) for f in fs if f.endswith(".json"))
T("reviewer profiles discarded in saved pages", "contributor_id" not in raw_all and "thumbnail" not in raw_all and "Reviewer 1" not in raw_all)
items_a = lead.jl_read(lead.LP("items", "biz-901.jsonl"))
T("item IDs are stable and unique", len({i["item_id"] for i in items_a}) == len(items_a) == 25, str(len(items_a)))
print("== read")
n_before = len(JEV_CALLS)
lead.cmd_read(5.0)
n_calls = len(JEV_CALLS) - n_before
res_a = json.load(open(lead.LP("results", "biz-901.json")))
q = res_a["questions"]
T("owner_work yes with exact quote from the owner review", q["owner_work"]["verdict"] == "yes" and "personally installed" in q["owner_work"]["quote"], q["owner_work"].get("quote", ""))
T("retirement yes only from the owner review, not the retired-teacher review or the founder's retirement", q["retirement"]["verdict"] == "yes" and "Maria is retiring" in q["retirement"]["quote"], q["retirement"].get("quote", ""))
T("successor yes names a person and uses the owner context", q["successor"]["verdict"] == "yes" and "Elena" in q["successor"]["quote"], q["successor"].get("quote", ""))
T("current owner identity is Maria Lopez from the business's own page, historical founder ignored", res_a["owner_identity"]["name"] == "Maria Lopez", str(res_a["owner_identity"]))
rej = " | ".join(c["quote"] for c in res_a["candidates_rejected"])
T("naive-model false positives were rejected by the gates", "retired teacher" in rej and "technician installed" in rej and "Walter retired" in rej, rej[:200])
T("item-level reads: every item answered, none failed for ALPHA", res_a["item_units"] == res_a["item_units_answered"] and not res_a["failed"], f'{res_a["item_units_answered"]}/{res_a["item_units"]}')
res_c = json.load(open(lead.LP("results", "biz-903.json")))
T("a failed model call is recorded, not hidden", len(res_c["failed"]) >= 1, str(res_c["failed"]))
T("Jev probabilities kept in their own file", os.path.exists(lead.LP("jev-probabilities", "biz-901.csv")))
bf = [e for fn in os.listdir(lead.LP("batches", "biz-901")) for e in lead.jl_read(os.path.join(lead.LP("batches", "biz-901"), fn))]
T("raw responses, requests, batch and item IDs saved for every call", all(e["raw_response"] and e["request"] and e["batch_id"] and e["item_id"] for e in bf) and all(e["model_resolved"] == "jev-1.13.0" for e in bf if e["status"] == "ok"), f"{len(bf)} calls")
n2 = len(JEV_CALLS)
lead.cmd_read(5.0)
again = JEV_CALLS[n2:]
T("rerun reuses unchanged results: only the one failed call is retried", len(again) == 1 and "FAILME" in again[0]["state"]["text"], f"{len(again)} new calls")
s2 = len(SERP_CALLS)
lead.cmd_collect(5.0)
T("rerun does not recollect: zero new SerpApi calls", len(SERP_CALLS) == s2)
print("== score")
ranked = lead.cmd_score()
tr = {r["biz_id"]: r for r in S.read_csv(S.TRACKER)[0]}
T("ALPHA scored 80 = 60 + 40 - 20 and ranked 1", tr["biz-901"]["priority_score"] == "80" and tr["biz-901"]["priority_rank"] == "1" and tr["biz-901"]["outreach_status"] == "scored", str({k: tr["biz-901"][k] for k in ("priority_score", "priority_rank", "outreach_status")}))
T("BRAVO complete but unsupported stays review", tr["biz-902"]["outreach_status"] == "review" and tr["biz-902"]["evidence_status"] == "unsupported")
T("CHARLIE partial stays review with a reason", tr["biz-903"]["outreach_status"] == "review" and tr["biz-903"]["evidence_status"] == "partial_or_failed" and tr["biz-903"]["evidence_notes"], tr["biz-903"]["evidence_notes"])
T("other prompts' columns are preserved", all(r["score_from_other_prompt"] == "keep-me" for r in tr.values()))
T("untouched rows keep their status", tr["biz-904"]["outreach_status"] == "ready" and tr["biz-906"]["outreach_status"] == "closed")
print("== budget gate")
by = {r["biz_id"]: r for r in S.read_csv(S.TRACKER)[0]}
T("budget gate records an exception instead of spending", lead.collect_business(by["biz-901"], 0.0) is None and any(e["reason"] == "budget_exceeded" for e in S.read_csv(lead.LP("exceptions.csv"))[0]))
print("== letters")
letters.cmd_draft(lead)
tr = {r["biz_id"]: r for r in S.read_csv(S.TRACKER)[0]}
ml = S.read_csv(os.path.join(TMP, "mailing-list.csv"))[0]
md = open(os.path.join(TMP, "letters.md"), encoding="utf-8").read()
T("one letter, for ALPHA, addressed to the sourced owner", len(ml) == 1 and ml[0]["biz_id"] == "biz-901" and ml[0]["addressee"] == "Maria Lopez", str(ml))
T("letter has one verified detail from the site, an interest, a conversation request, and no sale or retirement words", 'Serving Orange County since 1980' in md and "short conversation" in md and not re.search(r"retir|sell|sale|succession", md.split("## biz-901")[1], re.I))
T("status moved scored -> drafted", tr["biz-901"]["outreach_status"] == "drafted")
pdf = open(os.path.join(TMP, "letters.pdf"), "rb").read()
T("PDF is well formed with one page", pdf.startswith(b"%PDF-1.4") and pdf.rstrip().endswith(b"%%EOF") and len(re.findall(rb"/Type /Page /Parent", pdf)) == 1)
if shutil.which("pdftotext"):
    import subprocess
    txt = subprocess.run(["pdftotext", os.path.join(TMP, "letters.pdf"), "-"], capture_output=True, text=True).stdout
    T("PDF text extracts and contains the greeting and sender", "Dear Maria Lopez," in txt and "Test Buyer" in txt)
print("== verify")
rc = lead_verify.main()
T("independent verification passes", rc == 0)
print("== close and resume")
lead.cmd_close("biz-901", "mailed", True)
lead.cmd_select()
tr = {r["biz_id"]: r for r in S.read_csv(S.TRACKER)[0]}
T("closed after confirmation, never reselected", tr["biz-901"]["outreach_status"] == "closed" and "biz-901" not in [r["biz_id"] for r in lead.cmd_select()])
letters.cmd_draft(lead)
T("no leads left: mailing list empty, sample labelled and excluded", S.read_csv(os.path.join(TMP, "mailing-list.csv"))[0] == [] and "SAMPLE - NOT FOR MAILING" in open(os.path.join(TMP, "letters.md"), encoding="utf-8").read())
try:
    lead.set_status(tr["biz-901"], "ready")
    T("closed cannot move back", False)
except lead.Fatal:
    T("closed cannot move back", True)
leak = [dp for dp, _, fs in os.walk(TMP) for f in fs if "TESTKEY" in open(os.path.join(dp, f), encoding="utf-8", errors="ignore").read()]
T("dummy API keys appear in no saved file", not leak, str(leak[:2]))
print(f"\n{sum(RESULTS)} passed, {len(RESULTS) - sum(RESULTS)} failed")
(print("kept:", TMP) if os.environ.get("SCOUT_KEEP") else shutil.rmtree(TMP, ignore_errors=True))
sys.exit(0 if all(RESULTS) else 1)
