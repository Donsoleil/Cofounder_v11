#!/usr/bin/env python3
"""Evidence, ranking and letters for ready Orange County businesses (prompt 2).  Stdlib only.

    python3 -I scout/lead.py select | estimate | collect | read | score | draft | status | verify
    python3 -I scout/lead.py run --budget-usd 5       # select -> collect -> read -> score, budget-gated
    python3 -I scout/lead.py close biz-151 --as mailed --confirm

Keys are read from SERPAPI_API_KEY and TYPESAFE_API_KEY and are never printed or saved.
See SCOUT.md part 2 for the rules, official-doc facts and cost model.
"""
import argparse
import collections
import concurrent.futures as cf
import csv
import datetime
import hashlib
import html.parser
import io
import json
import math
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scout as S  # noqa: E402
from norm import STRONG, addr_level, confidence, name_match, name_variants, norm  # noqa: E402

# ---- providers (official docs, checked 2026-10-06) --------------------------------------
JEV_URL = "https://api.typesafe.ai/v1/systemone"      # docs.typesafe.ai/api.md
SERP_URL = "https://serpapi.com/search.json"           # serpapi.com/google-maps-reviews-api
KEY_SERP, KEY_JEV = "SERPAPI_API_KEY", "TYPESAFE_API_KEY"
MODEL = "jev-latest"                                   # resolves to jev-1.13.0; resolved name is saved per response
JEV_USD_PER_TOKEN = 0.042 / 1e6                        # $42 per billion input tokens, output free (docs.typesafe.ai/models.md)
SERP_USD_PER_SEARCH = float(os.environ.get("SERPAPI_PRICE_PER_SEARCH_USD", "0.025"))  # Starter plan $25/1000; free plan = 0
UA = "oc-hvac-scout/1.0 (public business research)"
QSET = "q1"
BATCH_SIZE, WINDOW_CHARS, OVERLAP, MAX_REVIEW_PAGES = 25, 8000, 800, 400
CAVEAT = ("Record age is not owner age. Reviews and web pages are not proof of intent. "
          "Priority is a ranking rule, not a probability of sale.")

HOME = S.HOME
LEAD = os.path.join(HOME, "scout", "lead")
LP = lambda *a: os.path.join(LEAD, *a)  # noqa: E731
EV_COLS = ["evidence_status", "coverage_status", "owner_identity", "owner_identity_source_id", "owner_identity_quote",
           "owner_work", "owner_work_source_id", "owner_work_quote", "successor", "successor_source_id",
           "successor_quote", "retirement", "retirement_source_id", "retirement_quote", "priority_score",
           "priority_rank", "address_check", "letter_status", "evidence_run_id", "evidence_notes"]
TRANSITIONS = {"ready": {"scored", "review"}, "review": {"ready", "scored"}, "scored": {"drafted", "review"},
               "drafted": {"closed"}, "closed": set()}

OWNER_RE = re.compile(r"\b(owners?|owned|owner[- ]operators?|founders?|co-?founders?|president|proprietor|principal|ceo)\b", re.I)
WORK_RE = re.compile(r"\b(install\w*|repair\w*|fix\w*|servic(?:e|ed|es|ing)|came out|came to|showed up|replac\w+|diagnos\w+|"
                     r"worked on|did the (?:work|job)|personally|himself|herself|hands[- ]on|on[- ]site|performed|arrived|"
                     r"on the (?:job|roof|tools|calls?))\b", re.I)
SUCC_RE = re.compile(r"\b(successors?|take[s]? over|taking over|took over|will run|next generation|second generation|"
                     r"third generation|(?:his|her|their) (?:son|daughter|nephew|niece|partner)|new owner|passing (?:down|the)|"
                     r"transition(?:ing)?|handing (?:over|off)|inherit\w*|succession)\b", re.I)
RETIRE_RE = re.compile(r"\b(retire[ds]?|retiring|retirement)\b", re.I)
NEG_RE = {"owner_work": re.compile(r"\b(does not|doesn't|no longer|never|not)\b[^.]{0,60}\b(work|service|install|repair|field|calls?)\b", re.I),
          "successor": re.compile(r"\b(no (?:one|successor)|nobody|no plans? (?:for|to)|not (?:handing|passing))\b", re.I),
          "retirement": re.compile(r"\b(not retiring|no plans? to retire|isn't retiring|is not retiring|never (?:plans?|intends?) to retire)\b", re.I)}
NAME_TOKEN = re.compile(r"\b[A-Z][a-z]+(?:[-'][A-Z]?[a-z]+)?\b")
NOT_NAMES = {"The", "Our", "His", "Her", "He", "She", "They", "We", "I", "Owner", "Owners", "Founder", "President", "Family", "Orange",
             "County", "California", "Anaheim", "Irvine", "Santa", "Ana", "Plumbing", "Heating", "Air", "Conditioning", "Inc", "Company", "Co",
             "Since", "And", "Is", "Was", "Has", "Have", "Will", "Son", "Daughter", "Partner", "Nephew", "Niece", "Mr", "Mrs", "Ms", "Dr"}
OWNER_NAME_PATTERNS = [
    re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z]\.?)?(?:\s+[A-Z][a-z'\-]+)+)\s*,?\s*(?:is\s+)?(?:the\s+|our\s+|an?\s+)?(?:current\s+)?(?:co-?owner|owner[- ]operator|owner|proprietor)\b"),
    re.compile(r"\b(?:owned|operated|run)(?:\s+and\s+(?:owned|operated|run))?\s+by\s+([A-Z][a-z]+(?:\s+[A-Z]\.?)?(?:\s+[A-Z][a-z'\-]+)+)"),
    re.compile(r"\b(?:owner|proprietor)\s*[:\-]\s*([A-Z][a-z]+(?:\s+[A-Z]\.?)?(?:\s+[A-Z][a-z'\-]+)+)")]
PAST_RE = re.compile(r"\b(was founded|founded in|late|passed away|deceased|retired|in memory|formerly|former|used to)\b", re.I)
OWN = r"(?:owners?|owner[- ]operators?|founders?|president|proprietor)"
TRADE = (r"(?:install\w*|repair\w*|fix\w*|serviced?|servicing|replac\w+|diagnos\w+|came out|showed up|arrived|worked on|"
         r"did the (?:work|job)|performed)")
WORK1 = re.compile(r"\b" + OWN + r"\b((?:\W+\w+){0,8}?)\W+" + TRADE + r"\b", re.I)
WORK2 = re.compile(r"\b(?:owner|founder)\b[^.]{0,80}\b(?:himself|herself|personally)\b|\b(?:himself|herself|personally)\b[^.]{0,80}\b(?:owner|founder)\b", re.I)
WORK3 = re.compile(r"^(?:he|she)\b(?:\W+\w+){0,6}?\W+" + TRADE + r"\b", re.I)
RET1 = re.compile(r"\b" + OWN + r"\b((?:\W+\w+){0,8}?)\W+(?:retire[ds]?|retiring|retirement)\b", re.I)
RET2 = re.compile(r"\b(?:retire[ds]?|retiring|retirement)\b(?:\W+\w+){0,6}?\W+(?:of|by|for|from)\W+(?:the\W+|our\W+)?" + OWN + r"\b", re.I)
RET3 = re.compile(r"^(?:he|she|they)\b[^.]{0,40}\b(?:retire[ds]?|retiring|retirement)\b", re.I)
SELF_RET = re.compile(r"\b(?:i am|i'm|i was|as a|we are|we're|my)\s+(?:a\s+)?retire", re.I)
NEGATION = re.compile(r"\b(not|never|no|didn't|doesn't|wasn't|isn't|weren't)\b|n't", re.I)

QUESTIONS = {
    "owner_work": dict(
        instructions="Does the text explicitly say that the business's owner (or owner-operator) personally does hands-on plumbing, heating or air-conditioning work for customers, such as installing, repairing, servicing or going on calls?",
        criteria={"unknown": "The text does not say this, only hints at it, or the person is not clearly the owner.",
                  "no": "The text explicitly says the owner does not do hands-on field work, for example only manages or works in the office.",
                  "yes": "The text explicitly says the owner personally does hands-on trade work for customers."}),
    "successor": dict(
        instructions="Does the text name a specific person who is taking over, or is expected to take over, ownership or running of this business (for example a son, daughter, partner or employee)?",
        criteria={"unknown": "The text does not name a successor.",
                  "no": "The text explicitly says nobody is taking over.",
                  "yes": "The text names a specific person who will take over or run the business after the current owner."}),
    "retirement": dict(
        instructions="Does the text say that the business's OWNER is retiring, plans to retire, or has retired? A customer or reviewer saying they are retired does not count.",
        criteria={"unknown": "The text does not mention the business owner's retirement.",
                  "no": "The text explicitly says the owner is not retiring.",
                  "yes": "The text explicitly mentions the business owner's retirement, past, present or planned."}),
    "owner_status": dict(
        instructions="Does the text present a named person as the CURRENT owner of this business (not a past founder, not an employee)?",
        criteria={"unknown": "No named person is presented as the current owner.",
                  "historical": "A named person is described as a past or former founder or owner, or in the past tense only.",
                  "employee": "A named person is described as a technician, employee or manager, not the owner.",
                  "current_owner": "A named person is presented as the present owner of the business."})}
YES_CLASS = {"owner_work": "yes", "successor": "yes", "retirement": "yes", "owner_status": "current_owner"}

_lock = threading.Lock()
SPEND = collections.Counter()


class Fatal(Exception):
    pass


class Paused(Exception):
    """Time slice used up (the Composio workbench allows 180 seconds per cell). Progress is saved; run the same command again."""


DEADLINE = None


def check_deadline():
    if DEADLINE and time.time() > DEADLINE:
        raise Paused()


# ---------------------------------------------------------------- small helpers
def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha(s):
    return hashlib.sha256(s.encode("utf-8") if isinstance(s, str) else s).hexdigest()


def jl_append(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, sort_keys=True, ensure_ascii=False) + "\n")


def jl_read(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def jwrite(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, sort_keys=True, ensure_ascii=False)
    os.replace(path + ".tmp", path)


def secret(name):
    v = os.environ.get(name, "")
    if not v:
        raise Fatal(f"environment variable {name} is not set (never printed; add it in the environment settings)")
    return v


def scrub(text, *names):
    for n in names:
        v = os.environ.get(n, "")
        if v:
            text = text.replace(v, "[redacted]")
    return text


def log_exception(biz_id, stage, reason, detail=""):
    path = LP("exceptions.csv")
    rows, _ = S.read_csv(path)
    if not any(r["biz_id"] == biz_id and r["stage"] == stage and r["reason"] == reason for r in rows):
        rows.append(dict(biz_id=biz_id, stage=stage, reason=reason, detail=detail[:500], logged_utc=now()))
        S.write_csv(path, ["biz_id", "stage", "reason", "detail", "logged_utc"], rows)


def spend_usd():
    d = json.load(open(LP("spend.json"))) if os.path.exists(LP("spend.json")) else {}
    return d.get("serp_searches", 0) * SERP_USD_PER_SEARCH + d.get("jev_input_tokens", 0) * JEV_USD_PER_TOKEN


def save_spend():
    d = json.load(open(LP("spend.json"))) if os.path.exists(LP("spend.json")) else {}
    for k, v in SPEND.items():
        d[k] = d.get(k, 0) + v
    SPEND.clear()
    d["usd_estimate"] = round(d.get("serp_searches", 0) * SERP_USD_PER_SEARCH + d.get("jev_input_tokens", 0) * JEV_USD_PER_TOKEN, 4)
    d["serp_usd_per_search_assumed"] = SERP_USD_PER_SEARCH
    jwrite(LP("spend.json"), d)


# ---------------------------------------------------------------- HTTP (replaceable in tests)
def _http(url, data=None, headers=None, method=None, timeout=60):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **(headers or {})}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers or {})


def serp_get(params):
    """One SerpApi call (one credit when successful). Returns parsed JSON. The key never leaves this function."""
    q = dict(params, api_key=secret(KEY_SERP))
    for attempt in range(5):
        try:
            status, body, _ = _http(SERP_URL + "?" + urllib.parse.urlencode(q), timeout=90)
        except Exception as e:  # network error: message scrubbed, then retried
            if attempt == 4:
                raise Fatal(scrub(repr(e), KEY_SERP))
            time.sleep(2 ** attempt)
            continue
        if status in (429, 500, 502, 503, 504):
            time.sleep(2 ** attempt * 2)
            continue
        text = body.decode("utf-8", "replace")
        if status == 401:
            raise Fatal("SerpApi rejected the key (HTTP 401); check SERPAPI_API_KEY")
        try:
            res = json.loads(text)
        except ValueError:
            raise Fatal(f"SerpApi returned non-JSON (HTTP {status})")
        if res.get("error") and "hasn't returned any results" not in res["error"]:
            raise Fatal("SerpApi error: " + scrub(str(res["error"]), KEY_SERP))
        with _lock:
            SPEND["serp_searches"] += 0 if res.get("error") else 1
        jl_append(LP("collector-log.jsonl"), dict(ts=now(), params={k: v for k, v in params.items()}, http=status,
                                                  bytes=len(body), sha256=sha(body)))
        return res
    raise Fatal("SerpApi kept returning rate-limit or server errors")


def jev_post(payload):
    """One Jev call. Returns (http_status, raw_text). The key is added here and never logged."""
    body = json.dumps(payload).encode()
    hdr = {"Authorization": "Bearer " + secret(KEY_JEV), "Content-Type": "application/json"}
    for attempt in range(6):
        try:
            status, raw, _ = _http(JEV_URL, data=body, headers=hdr, method="POST", timeout=60)
        except Exception as e:
            if attempt == 5:
                return 0, scrub(repr(e), KEY_JEV)
            time.sleep(2 ** attempt)
            continue
        if status in (429, 529, 500, 502, 503):
            time.sleep(2 ** attempt)
            continue
        if status == 401:
            raise Fatal("Jev rejected the key (HTTP 401); check TYPESAFE_API_KEY")
        return status, raw.decode("utf-8", "replace")
    return 0, "retries exhausted"


# ---------------------------------------------------------------- tracker + selection
def load_tracker():
    rows, fields = S.read_csv(S.TRACKER)
    return rows, fields


def save_tracker(rows, fields):
    S.write_csv(S.TRACKER, fields, rows)


def ensure_columns(rows, fields):
    for c in EV_COLS:
        if c not in fields:
            fields.append(c)
    for r in rows:
        for c in EV_COLS:
            r.setdefault(c, "")
    return fields


def suppressed(row, supp):
    k = (norm(row["legal_name"]), S.street_key(row["address"]), S.zip5(row["zip"]))
    for s in supp:
        if s.get("biz_id") and s["biz_id"] == row["biz_id"]:
            return True
        if s.get("name") and norm(s["name"]) == k[0] and (not s.get("address") or S.street_key(s["address"]) == k[1]):
            return True
    return False


def set_status(row, new):
    cur = row.get("outreach_status", "")
    if cur == new:
        return
    if new not in TRANSITIONS.get(cur, set()):
        raise Fatal(f'{row["biz_id"]}: outreach_status {cur!r} -> {new!r} is not allowed')
    row["outreach_status"] = new


def cmd_select():
    rows, fields = load_tracker()
    supp, _ = S.read_csv(LP("suppression.csv"))
    if not os.path.exists(LP("suppression.csv")):
        S.write_csv(LP("suppression.csv"), ["biz_id", "name", "address", "reason", "added_utc", "source"], [])
    sel = [r for r in rows if r["eligibility_status"] == "ready" and r["outreach_status"] == "ready" and not suppressed(r, supp)]
    skipped = [r["biz_id"] for r in rows if r["eligibility_status"] == "ready" and r["outreach_status"] == "ready" and suppressed(r, supp)]
    old, _ = S.read_csv(LP("selection.csv"))
    have = {r["biz_id"]: r for r in old}
    for r in sel:
        have.setdefault(r["biz_id"], dict(biz_id=r["biz_id"], legal_name=r["legal_name"], address=f'{r["address"]}, {r["city"]} {r["zip"]}', selected_utc=now()))
    S.write_csv(LP("selection.csv"), ["biz_id", "legal_name", "address", "selected_utc"], sorted(have.values(), key=lambda x: x["biz_id"]))
    print(f"selected {len(sel)} (eligibility ready, outreach ready, not suppressed): {', '.join(r['biz_id'] for r in sel)}; held back by suppression: {', '.join(skipped) or 'none'}")
    return sel


# ---------------------------------------------------------------- cost estimate
def estimate_one(reviews=150, pages=3, avg_review_chars=300, page_chars=10000):
    pages_needed = 1 + math.ceil(max(reviews - 8, 0) / 20)
    serp = 2 + pages_needed                      # place search + place lookup (website) + review pages
    items = reviews + pages
    tokens_item = reviews * (avg_review_chars / 3.5 + 600) + pages * (page_chars / 3.5 + 600)   # 4 questions, state counted per question (upper bound)
    tokens_sent = 0.15 * items * 6 * 450 * 2     # sentence pass on about 15 percent of items, 6 sentences, 2 questions
    tokens = int(tokens_item + tokens_sent)
    return dict(serp_searches=serp, jev_tokens=tokens, usd=round(serp * SERP_USD_PER_SEARCH + tokens * JEV_USD_PER_TOKEN, 3))


def cmd_estimate(n_reviews=None):
    sel = cmd_select()
    out = []
    for r in sel:
        for label, n in (("low (50 reviews)", 50), ("typical (150)", 150), ("high (500)", 500)) if n_reviews is None else (("given", n_reviews),):
            e = estimate_one(n)
            out.append(dict(biz_id=r["biz_id"], scenario=label, **e))
    jwrite(LP("estimate.json"), dict(created_utc=now(), assumptions=dict(
        serp_usd_per_search=SERP_USD_PER_SEARCH, jev_usd_per_million_tokens=0.042,
        note="SerpApi: one credit per successful search; Starter plan $25 per 1,000 (free plan $0). Jev: $0.042 per million input tokens, output free. "
             "Review count is unknown until the place is matched, so three scenarios are shown. Token counts are an upper bound."), rows=out))
    for o in out:
        print(f'{o["biz_id"]:8} {o["scenario"]:18} SerpApi searches {o["serp_searches"]:3}  Jev tokens {o["jev_tokens"]:8,}  est. ${o["usd"]}')
    return out


# ---------------------------------------------------------------- collection: place, reviews, website pages
def parse_maps_address(a):
    m = re.match(r"(.*?),\s*([^,]+),\s*([A-Z]{2})\s*(\d{5})?", a or "")
    return (m.group(1), m.group(2), m.group(4) or "") if m else (a or "", "", S.zip5(a or ""))


def match_place(b):
    names = [b["legal_name"]] + [x for x in b.get("other_names", "").split(" | ") if x]
    mine = [v for n in names for v in name_variants(re.sub(r" \(CSLB #\d+\)$", "", n))]
    q = f'{b["legal_name"]} {b["address"]} {b["city"]} {b["state"]} {b["zip"][:5]}'
    fp = LP("raw", b["biz_id"], "place-search.json")
    cached = json.load(open(fp))["candidates"] if os.path.exists(fp) else None   # a resumed run does not spend another search
    res = {} if cached is not None else serp_get({"engine": "google_maps", "type": "search", "q": q, "hl": "en"})
    cands = [] if cached is not None else (res.get("local_results") or ([res["place_results"]] if res.get("place_results") else []))
    out = list(cached or [])
    for c in cands:
        rel = name_match(mine, name_variants(c.get("title", "")))
        lv = addr_level((b["address"], b["city"], b["zip"]), parse_maps_address(c.get("address", "")))
        # Maps titles usually drop Inc/LLC, so a suffix-only difference with the same street still counts; same words only does not
        conf = "high" if (rel in ("E", "C", "D") and lv in STRONG) else confidence(rel, lv)
        out.append(dict(place_id=c.get("place_id", ""), data_id=c.get("data_id", ""), title=c.get("title", ""), address=c.get("address", ""),
                        website=c.get("website") or (c.get("links") or {}).get("website", ""), phone=c.get("phone", ""),
                        reviews=c.get("reviews"), rating=c.get("rating"), name_relation=rel, address_level=lv, confidence=conf,
                        decision="accepted" if conf == "high" else ("rejected_namesake" if rel and lv in ("none", "city") else "rejected_weak")))
    acc = [c for c in out if c["decision"] == "accepted"]
    if cached is None:
        jwrite(fp, dict(query=q, retrieved_utc=now(), candidates=out))
    if len({c["place_id"] or c["data_id"] for c in acc}) == 1:
        return acc[0], out
    return None, out


def place_website(place):
    """Website from the Maps listing. If the search result lacked one, search again by exact title and address."""
    if place.get("website"):
        return place["website"]
    res = serp_get({"engine": "google_maps", "type": "search", "q": f'{place["title"]} {place["address"]}', "hl": "en"})
    pr = res.get("place_results") or next((c for c in res.get("local_results", []) if c.get("place_id") == place["place_id"]), {})
    place["phone"] = place.get("phone") or pr.get("phone", "")
    if place.get("reviews") is None:
        place["reviews"] = pr.get("reviews")
    return pr.get("website") or (pr.get("links") or {}).get("website", "")


def sanitize_review(r):
    keep = {k: r[k] for k in ("review_id", "link", "rating", "date", "iso_date", "iso_date_of_last_edit", "snippet", "extracted_snippet", "likes", "response", "source", "position") if k in r}
    return keep  # reviewer profile ("user"), photos and details are discarded


def collect_reviews(b, place):
    """Follow next_page_token until it runs out. Each page is saved as it arrives, so a paused run resumes from disk."""
    bid = b["biz_id"]
    pid = place["place_id"] or place["data_id"]
    token, seen, pages, reviews, reported, capped = None, set(), 0, [], place.get("reviews"), False
    while True:
        pages += 1
        fp = LP("raw", bid, f"reviews-page-{pages:03d}.json")
        if os.path.exists(fp):
            d = json.load(open(fp))
            info, san, pag = d["place_info"], d["reviews"], d.get("serpapi_pagination", {})
        else:
            check_deadline()
            p = {"engine": "google_maps_reviews", "hl": "en", "sort_by": "newestFirst"}
            p["place_id" if place["place_id"] else "data_id"] = pid
            if token:
                p.update(next_page_token=token, num=20)
            res = serp_get(p)
            info, san, pag = res.get("place_info") or {}, [sanitize_review(x) for x in (res.get("reviews") or [])], res.get("serpapi_pagination") or {}
            jwrite(fp, dict(params={k: v for k, v in p.items()}, retrieved_utc=now(), place_info=info, reviews=san, serpapi_pagination=pag))
        if info.get("reviews") is not None:
            reported = info["reviews"]
        reviews += san
        token = pag.get("next_page_token")
        if not token or token in seen or not san:
            break
        seen.add(token)
        if pages >= MAX_REVIEW_PAGES:
            capped = True
            break
    uniq = {}
    for r in reviews:
        uniq.setdefault(r.get("review_id") or sha(json.dumps(r, sort_keys=True)), r)
    return dict(pages=pages, fetched=len(reviews), unique=len(uniq), reported=reported, capped=capped, reviews=list(uniq.items()))


class _Text(html.parser.HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "template"}
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article", "footer", "header"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.links, self.title, self._skip, self._in_title, self._href, self._atext = [], [], "", 0, False, None, []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "a":
            self._href, self._atext = dict(attrs).get("href"), []

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False
        if tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join("".join(self._atext).split())))
            self._href = None
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, d):
        if self._in_title:
            self.title += d
        if not self._skip:
            self.parts.append(d)
            if self._href is not None:
                self._atext.append(d)


def html_to_text(raw_bytes, content_type=""):
    m = re.search(r"charset=([\w-]+)", content_type or "", re.I) or re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", raw_bytes[:4096], re.I)
    enc = (m.group(1).decode() if m and isinstance(m.group(1), bytes) else (m.group(1) if m else "utf-8"))
    try:
        s = raw_bytes.decode(enc, "replace")
    except LookupError:
        s = raw_bytes.decode("utf-8", "replace")
    p = _Text()
    p.feed(s)
    text = re.sub(r"[ \t\r\f\v]+", " ", "".join(p.parts))
    text = "\n".join(l.strip() for l in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text, " ".join(p.title.split()), p.links


_robots = {}


def robots_ok(url):
    host = urllib.parse.urlsplit(url)
    key = f"{host.scheme}://{host.netloc}"
    if key not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        try:
            st, body, _ = http_get(key + "/robots.txt")
            if st == 200:
                rp.parse(body.decode("utf-8", "replace").splitlines())
            else:
                rp = None
        except Exception:
            rp = None
        _robots[key] = rp
    rp = _robots[key]
    return True if rp is None else rp.can_fetch(UA, url)


def http_get(url):
    return _http(url, headers={"Accept": "text/html,application/xhtml+xml"}, timeout=25)


def fetch_page(url, max_bytes=5_000_000):
    if not robots_ok(url):
        return dict(url=url, status="robots_disallowed")
    try:
        st, body, hdr = http_get(url)
    except Exception as e:
        return dict(url=url, status="error", error=repr(e)[:200])
    ct = hdr.get("Content-Type", hdr.get("content-type", ""))
    if st != 200 or "html" not in ct.lower():
        return dict(url=url, status=f"http_{st}", content_type=ct)
    if len(body) > max_bytes:
        return dict(url=url, status="too_large", bytes=len(body))
    text, title, links = html_to_text(body, ct)
    return dict(url=url, status="ok", http=st, content_type=ct, bytes=len(body), text=text, title=title, links=links, sha256=sha(body), raw=body)


PAGE_CLASSES = [("about", re.compile(r"(^|/)(about|about-us|our-story|who-we-are|company|our-company|history)(/|$|\.)", re.I), ["/about", "/about-us", "/our-story"]),
                ("team", re.compile(r"(^|/)(team|our-team|meet-the-team|meet-our-team|staff|our-staff|leadership|people|our-people)(/|$|\.)", re.I), ["/team", "/our-team", "/meet-the-team"]),
                ("services", re.compile(r"(^|/)(services|our-services|what-we-do)(/|$|\.)", re.I), ["/services", "/our-services"])]


def site_identity_ok(b, place, home):
    t = home["text"] + " " + home.get("title", "")
    core = " ".join(norm(name_variants(b["legal_name"])[0][2]).split())
    toks = [x for x in re.split(r"\s+", core) if len(x) > 2]
    name_hit = bool(toks) and all(x.lower() in norm(t).lower() for x in toks[:3])
    street = S.street_key(b["address"])
    num_name = " ".join(street.split()[:2])
    digits = re.sub(r"\D", "", place.get("phone", ""))[-7:]
    addr_hit = bool(num_name) and num_name.lower() in norm(t).lower()
    phone_hit = bool(digits) and digits in re.sub(r"\D", "", t)
    zip_hit = S.zip5(b["zip"]) in t
    return name_hit and (addr_hit or phone_hit or zip_hit), dict(name_hit=name_hit, address_hit=addr_hit, phone_hit=phone_hit, zip_hit=zip_hit)


def collect_pages(b, place, website):
    site = website if re.match(r"https?://", website) else "https://" + website
    attempts, got = [], []
    home = fetch_page(site)
    attempts.append(dict(kind="home", url=site, status=home["status"]))
    if home["status"] != "ok":
        return dict(site=site, identity=None, attempts=attempts, pages=[], status="home_unreachable")
    ok, why = site_identity_ok(b, place, home)
    if not ok:
        return dict(site=site, identity=why, attempts=attempts, pages=[], status="identity_failed")
    base = urllib.parse.urlsplit(home["url"] if "url" in home else site)
    home_sha = home["text"] and sha(home["text"])
    got.append(("home", site, home))
    seen_urls = {site.rstrip("/")}
    for kind, rx, probes in PAGE_CLASSES:
        cand = []
        for href, text in home["links"]:
            u = urllib.parse.urljoin(site, href or "")
            sp = urllib.parse.urlsplit(u)
            if sp.netloc.replace("www.", "") != base.netloc.replace("www.", "") or u.rstrip("/") in seen_urls:
                continue
            if rx.search(sp.path) or (kind != "services" and rx.search(re.sub(r"\s+", "-", text.lower()))):
                cand.append(u.split("#")[0])
        cand += [urllib.parse.urljoin(site, p) for p in probes]
        done = False
        for u in dict.fromkeys(cand):
            if u.rstrip("/") in seen_urls:
                continue
            pg = fetch_page(u)
            attempts.append(dict(kind=kind, url=u, status=pg["status"]))
            if pg["status"] == "ok" and len(pg["text"]) > 200 and sha(pg["text"]) != home_sha:
                got.append((kind, u, pg))
                seen_urls.add(u.rstrip("/"))
                done = True
                break
        if not done:
            attempts.append(dict(kind=kind, url="", status="no_existing_page_found"))
    pages = []
    for kind, u, pg in got:
        raw = pg.pop("raw", b"")
        jwrite_bytes = LP("raw", b["biz_id"], "pages", sha(raw)[:16] + ".html")
        os.makedirs(os.path.dirname(jwrite_bytes), exist_ok=True)
        open(jwrite_bytes, "wb").write(raw)
        pages.append(dict(kind=kind, url=u, title=pg.get("title", ""), text=pg["text"], sha256=pg["sha256"], raw_file=os.path.relpath(jwrite_bytes, LEAD)))
    return dict(site=site, identity=why, attempts=attempts, pages=pages, status="ok")


def stable_item_id(kind, key):
    return {"review": "rev-", "page": "pg-"}[kind] + hashlib.sha1(key.encode()).hexdigest()[:12]


def review_text(r):
    t = r.get("snippet")
    if not t:
        e = r.get("extracted_snippet")
        t = e.get("original") if isinstance(e, dict) else e
    return (t or "").strip()


def collect_business(b, budget_usd):
    bid = b["biz_id"]
    est = estimate_one()
    if spend_usd() + est["usd"] > budget_usd:
        log_exception(bid, "collect", "budget_exceeded", f"projected ${est['usd']} would pass the ${budget_usd} budget; spent so far ${spend_usd():.3f}")
        print(f"{bid}: not run, budget gate")
        return None
    cov = dict(biz_id=bid, started_utc=now(), status="in_progress", notes=[])
    place, cands = match_place(b)
    cov["place_candidates"] = len(cands)
    if not place:
        cov.update(status="failed", reason="place_not_matched")
        log_exception(bid, "collect", "place_not_matched", f"{len(cands)} Maps candidates, none matched name and address")
        jwrite(LP("coverage", f"{bid}.json"), cov)
        save_spend()
        return cov
    cov["place"] = {k: place[k] for k in ("place_id", "data_id", "title", "address", "phone", "name_relation", "address_level")}
    rv = collect_reviews(b, place)
    cov["reviews"] = {k: rv[k] for k in ("pages", "fetched", "unique", "reported", "capped")}
    items = []
    for rid, r in rv["reviews"]:
        txt = review_text(r)
        resp = ((r.get("response") or {}).get("snippet", "") if isinstance(r.get("response"), dict) else "").strip()
        full = txt + (("\n\n[Business reply] " + resp) if resp else "")   # the business's own reply is read and quotable too
        items.append(dict(item_id=stable_item_id("review", f'{place["place_id"] or place["data_id"]}|{rid}'), biz_id=bid, kind="review", review_id=rid,
                          url=r.get("link", ""), date=r.get("iso_date") or r.get("date", ""), rating=r.get("rating"), text=full, text_sha256=sha(full),
                          review_text_only=txt, business_reply=resp, collected_utc=now()))
    cov["reviews"]["with_text"] = sum(1 for i in items if i["text"])
    web = place_website(place)
    cov["website"] = web
    if web:
        pg = collect_pages(b, place, web)
        cov["pages"] = dict(status=pg["status"], identity=pg["identity"], attempts=pg["attempts"], fetched=len(pg["pages"]))
        for p in pg["pages"]:
            items.append(dict(item_id=stable_item_id("page", p["url"]), biz_id=bid, kind="page", page_type=p["kind"], url=p["url"], date="", title=p["title"],
                              text=p["text"], text_sha256=sha(p["text"]), raw_file=p["raw_file"], collected_utc=now()))
        if pg["status"] != "ok":
            log_exception(bid, "collect", "website_" + pg["status"], web)
    else:
        cov["pages"] = dict(status="no_website_listed", fetched=0)
        log_exception(bid, "collect", "website_not_listed", "Maps listing shows no website")
    old = {i["item_id"]: i for i in jl_read(LP("items", f"{bid}.jsonl"))}
    for i in items:
        old[i["item_id"]] = i
    os.makedirs(LP("items"), exist_ok=True)
    with open(LP("items", f"{bid}.jsonl"), "w", encoding="utf-8") as f:
        for k in sorted(old):
            f.write(json.dumps(old[k], sort_keys=True, ensure_ascii=False) + "\n")
    notes = []
    if rv["reported"] is None:
        notes.append("reported review count unavailable")
    elif rv["unique"] != rv["reported"]:
        notes.append(f'reviews: reported {rv["reported"]}, fetched {rv["fetched"]}, unique {rv["unique"]} (gap {rv["reported"] - rv["unique"]})')
    if rv["capped"]:
        notes.append("review page cap reached")
    if cov["pages"]["status"] in ("home_unreachable", "identity_failed"):
        notes.append("website " + cov["pages"]["status"])
    cov.update(status="partial" if notes else "complete", notes=notes, finished_utc=now(), items=len(items))
    jwrite(LP("coverage", f"{bid}.json"), cov)
    save_spend()
    print(f'{bid}: place matched, reviews unique {rv["unique"]}/{rv["reported"]}, pages {cov["pages"].get("fetched", 0)}, coverage {cov["status"]}')
    return cov


def cmd_collect(budget, fresh=False):
    sel = cmd_select()
    rows, _ = load_tracker()
    by = {r["biz_id"]: r for r in rows}
    try:
        for r in sel:
            b = by[r["biz_id"]]
            cp = LP("coverage", f'{b["biz_id"]}.json')
            if fresh and os.path.exists(cp):
                os.remove(cp)
                for fn in os.listdir(LP("raw", b["biz_id"])) if os.path.isdir(LP("raw", b["biz_id"])) else []:
                    if fn.startswith("reviews-page-") or fn == "place-search.json":
                        os.remove(LP("raw", b["biz_id"], fn))
            if os.path.exists(cp) and json.load(open(cp))["status"] in ("complete", "partial"):
                print(f'{b["biz_id"]}: already collected, reusing')
                continue
            collect_business(b, budget)
    except Paused:
        print("PAUSED: time slice used up; progress is saved, run collect again")
    finally:
        save_spend()


# ---------------------------------------------------------------- reading: windows, batches, Jev
def windows(text, size=WINDOW_CHARS, overlap=OVERLAP):
    if len(text) <= size:
        return [(0, len(text))]
    out, start = [], 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            sp = text.rfind(" ", start + size - 300, end)
            end = sp if sp > start else end
        out.append((start, end))
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return out


def sentences(text):
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    out, pos = [], 0
    for p in parts:
        p = p.strip()
        if len(p) < 4:
            continue
        i = text.find(p, pos)
        if i >= 0:
            out.append((i, i + len(p), p))
            pos = i + len(p)
    return out


def build_payload(b, unit, qnames):
    return {"model": MODEL, "state": {"business_name": b["legal_name"], "business_city": b["city"], "source_type": unit["kind"],
                                      "source_date": unit.get("date", ""), "context": unit.get("context", ""), "text": unit["text"]},
            "questions": {q: {"type": "choice", "instructions": QUESTIONS[q]["instructions"], "criteria": QUESTIONS[q]["criteria"]} for q in qnames}}


def parse_jev(raw):
    try:
        d = json.loads(raw)
        ans = d["answers"]
        for q, a in ans.items():
            if a.get("type") == "choice" and (not isinstance(a.get("probabilities"), dict) or "choice" not in a):
                return None
        return d
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def run_units(b, units, qmap, level):
    """Read units in bounded batches. units: list of dict(unit_id,item_id,kind,text,...). qmap: unit_id -> [questions]."""
    bid = b["biz_id"]
    path_dir = LP("batches", bid)
    done = {}
    for fn in sorted(os.listdir(path_dir)) if os.path.isdir(path_dir) else []:
        for e in jl_read(os.path.join(path_dir, fn)):
            if e.get("status") == "ok" and e.get("qset") == QSET:
                done[(e["unit_id"], e["text_sha256"], tuple(e["questions"]))] = e
    results, failed = {}, []
    units = sorted(units, key=lambda u: u["unit_id"])
    batches = [units[i:i + BATCH_SIZE] for i in range(0, len(units), BATCH_SIZE)]
    for n, chunk in enumerate(batches, 1):
        check_deadline()
        batch_id = f"{bid}-{level}-{n:03d}"
        todo = []
        for u in chunk:
            k = (u["unit_id"], sha(u["text"]), tuple(qmap[u["unit_id"]]))
            if k in done:
                results[u["unit_id"]] = done[k]
            else:
                todo.append(u)

        def one(u):
            payload = build_payload(b, u, qmap[u["unit_id"]])
            st, raw = jev_post(payload)
            return u, payload, st, raw
        if todo:
            with cf.ThreadPoolExecutor(max_workers=8) as ex:
                for u, payload, st, raw in ex.map(one, todo):
                    parsed = parse_jev(raw) if st == 200 else None
                    e = dict(batch_id=batch_id, unit_id=u["unit_id"], item_id=u["item_id"], level=level, qset=QSET, questions=qmap[u["unit_id"]],
                             text_sha256=sha(u["text"]), request=payload, http=st, raw_response=raw, status="ok" if parsed else "failed", requested_utc=now())
                    if parsed:
                        e["answers"], e["model_resolved"], e["usage"] = parsed["answers"], parsed.get("model", ""), parsed.get("usage", {})
                        with _lock:
                            SPEND["jev_input_tokens"] += int(parsed.get("usage", {}).get("input_tokens", 0))
                        results[u["unit_id"]] = e
                    else:
                        failed.append(u["unit_id"])
                    jl_append(os.path.join(path_dir, f"{batch_id}.jsonl"), e)
        upsert_batch_index(dict(batch_id=batch_id, biz_id=bid, level=level, units=[u["unit_id"] for u in chunk], answered=sum(1 for u in chunk if u["unit_id"] in results), ts=now()))
    return results, failed


def upsert_batch_index(rec):
    """One record per batch ID, so a resume never lists a batch twice."""
    path = LP("batch-index.jsonl")
    rows = {r["batch_id"]: r for r in jl_read(path)}
    rows[rec["batch_id"]] = rec
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        for k in sorted(rows):
            f.write(json.dumps(rows[k], sort_keys=True) + "\n")
    os.replace(path + ".tmp", path)


def prob_row(unit_id, q, a):
    return dict(unit_id=unit_id, question=q, choice=a.get("choice"), confidence=a.get("confidence"), probabilities=json.dumps(a.get("probabilities", {}), sort_keys=True))


NOT_SUBJECT = re.compile(r"\b(technicians?|techs?|installers?|crew|team|staff|employees?|guys|plumbers?|but|while|then|however)\b", re.I)


def _subject_match(rx, text):
    """True if the owner is the subject: no negation in the matched span and nobody else named between owner and verb."""
    for m in rx.finditer(text):
        mid = m.group(1) if m.lastindex else ""
        if not NEGATION.search(m.group(0)) and not NOT_SUBJECT.search(mid or ""):
            return True
    return False


def gate_ok(q, verdict, quote, prev, biz_name):
    """Validate a supporting quote against ownership context. Returns (ok, why)."""
    ctx = (prev + " " + quote).strip()
    if verdict in ("yes", "current_owner"):
        if q == "owner_work":
            ok = _subject_match(WORK1, quote) or _subject_match(WORK2, quote) or (bool(OWNER_RE.search(prev)) and _subject_match(WORK3, quote))
            return ok, "the owner must be the one doing the trade work"
        if q == "successor":
            caps = [t for t in NAME_TOKEN.findall(quote)[1:] if t not in NOT_NAMES and t.lower() not in norm(biz_name).lower().split()]
            return (bool(SUCC_RE.search(quote)) and bool(caps) and bool(OWNER_RE.search(ctx))), "needs a succession term, a named person and an owner in context"
        if q == "retirement":
            if SELF_RET.search(quote):
                return False, "the retirement is the writer's own"
            ok = _subject_match(RET1, quote) or _subject_match(RET2, quote) or (bool(OWNER_RE.search(prev)) and bool(RET3.search(quote)))
            return ok, "the retirement must be the owner's"
        if q == "owner_status":
            return (bool(owner_name_from(quote)) and not PAST_RE.search(quote)), "needs a full name presented as a present owner, in the present tense"
    if verdict == "no":
        rx = NEG_RE.get(q)
        return (bool(rx and rx.search(quote)) and (q != "owner_work" or bool(OWNER_RE.search(ctx)))), "no needs an explicit contradiction"
    return False, "not a yes or no verdict"


def owner_name_from(quote):
    for rx in OWNER_NAME_PATTERNS:
        for m in rx.finditer(quote):
            name = m.group(1).strip()
            toks = name.split()
            if len(toks) >= 2 and not any(t in NOT_NAMES for t in toks):
                return name
    return ""


def read_business(b, budget):
    bid = b["biz_id"]
    items = [i for i in jl_read(LP("items", f"{bid}.jsonl")) if i["text"]]
    if not items:
        return dict(biz_id=bid, status="failed", reason="no_items")
    units, qmap, text_of = [], {}, {}
    for it in items:
        for k, (s, e) in enumerate(windows(it["text"]), 1):
            uid = f'{it["item_id"]}#w{k:02d}'
            u = dict(unit_id=uid, item_id=it["item_id"], kind=it["kind"], date=it.get("date", ""), text=it["text"][s:e], start=s, end=e)
            units.append(u)
            qmap[uid] = list(QUESTIONS)
            text_of[uid] = it
    est_tokens = sum(len(u["text"]) / 3.5 + 600 for u in units)
    if spend_usd() + est_tokens * JEV_USD_PER_TOKEN > budget:
        log_exception(bid, "read", "budget_exceeded", f"item pass needs about ${est_tokens * JEV_USD_PER_TOKEN:.3f}")
        return dict(biz_id=bid, status="failed", reason="budget_exceeded")
    res1, failed1 = run_units(b, units, qmap, "item")
    # sentence pass: locate exact quotes for anything the item pass flagged
    s_units, s_q = [], {}
    for u in units:
        r = res1.get(u["unit_id"])
        if not r:
            continue
        trig = [q for q in QUESTIONS if (r["answers"][q]["choice"] == YES_CLASS[q] or r["answers"][q]["choice"] == "no"
                                           or r["answers"][q]["probabilities"].get(YES_CLASS[q], 0) >= 0.10)]
        if not trig:
            continue
        sents = sentences(u["text"])
        for k, (s, e, txt) in enumerate(sents):
            prev = sents[k - 1][2] if k else ""
            uid = f'{u["unit_id"]}#s{k:03d}'
            s_units.append(dict(unit_id=uid, item_id=u["item_id"], kind=u["kind"], date=u.get("date", ""), text=txt, context=prev, parent=u["unit_id"], start=u["start"] + s, end=u["start"] + e))
            s_q[uid] = trig
    res2, failed2 = ({}, [])
    if s_units:
        est2 = sum(len(x["text"]) / 3.5 + 300 * len(s_q[x["unit_id"]]) for x in s_units) * JEV_USD_PER_TOKEN
        if spend_usd() + est2 > budget:
            log_exception(bid, "read", "budget_exceeded", f"sentence pass needs about ${est2:.3f}")
            return dict(biz_id=bid, status="failed", reason="budget_exceeded")
        res2, failed2 = run_units(b, s_units, s_q, "sentence")
    # retain probabilities separately
    prows = [prob_row(uid, q, a) for uid, r in {**res1, **res2}.items() for q, a in r["answers"].items()]
    S.write_csv(LP("jev-probabilities", f"{bid}.csv"), ["unit_id", "question", "choice", "confidence", "probabilities"], sorted(prows, key=lambda r: (r["unit_id"], r["question"])))
    # normalise
    cand = collections.defaultdict(list)
    conflicts = []
    for su in s_units:
        r = res2.get(su["unit_id"])
        if not r:
            continue
        item = text_of[su["parent"]]
        for q in s_q[su["unit_id"]]:
            a = r["answers"].get(q)
            if not a:
                continue
            ch = a["choice"]
            want = YES_CLASS[q]
            if ch not in (want, "no"):
                continue
            verdict = "yes" if ch == want else "no"
            quote = su["text"]
            full = item["text"]
            exact = full.find(quote) >= 0 and full[full.find(quote):full.find(quote) + len(quote)] == quote
            ok, why = gate_ok(q, ch if verdict == "yes" and q == "owner_status" else verdict, quote, su.get("context", ""), b["legal_name"])
            cand[q].append(dict(question=q, verdict=verdict, source_id=item["item_id"], source_url=item.get("url", ""), source_date=item.get("date", ""),
                                quote=quote, context=su.get("context", ""), exact_in_saved_text=exact, gate_ok=ok, gate_note=why,
                                validated=bool(exact and ok), p=a["probabilities"].get(ch, 0), confidence=a.get("confidence"), unit_id=su["unit_id"],
                                name=owner_name_from(quote) if q == "owner_status" else ""))
    out = dict(biz_id=bid, status="ok", questions={}, item_units=len(units), item_units_answered=len(res1), sentence_units=len(s_units),
               sentence_units_answered=len(res2), failed=failed1 + failed2, model_resolved=sorted({r.get("model_resolved", "") for r in {**res1, **res2}.values()}))
    for q in ("owner_work", "successor", "retirement"):
        v = [c for c in cand[q] if c["validated"]]
        yes = sorted([c for c in v if c["verdict"] == "yes"], key=lambda c: -c["p"])
        no = sorted([c for c in v if c["verdict"] == "no"], key=lambda c: -c["p"])
        if yes and no:
            out["questions"][q] = dict(verdict="unknown", note="conflicting validated evidence", yes=yes[0], no=no[0])
        elif yes:
            out["questions"][q] = dict(verdict="yes", **{k: yes[0][k] for k in ("source_id", "source_url", "source_date", "quote", "context", "p")})
        elif no:
            out["questions"][q] = dict(verdict="no", **{k: no[0][k] for k in ("source_id", "source_url", "source_date", "quote", "context", "p")})
        else:
            out["questions"][q] = dict(verdict="unknown", note="no validated quote" if cand[q] else "no evidence")
    names = {}
    for c in cand["owner_status"]:
        if c["verdict"] == "yes" and c["validated"] and c["name"]:
            names.setdefault(c["name"], []).append(c)
    if len(names) == 1:
        name, cs = next(iter(names.items()))
        c = sorted(cs, key=lambda c: -c["p"])[0]
        out["owner_identity"] = dict(name=name, source_id=c["source_id"], source_url=c["source_url"], quote=c["quote"])
    else:
        out["owner_identity"] = dict(name="unknown", note="conflicting names" if names else "no sourced current owner")
    out["candidates_rejected"] = [dict(q=q, quote=c["quote"][:200], why=c["gate_note"] if not c["gate_ok"] else "quote not found in saved text") for q in cand for c in cand[q] if not c["validated"]]
    jwrite(LP("results", f"{bid}.json"), out)
    save_spend()
    return out


def cmd_read(budget):
    sel = cmd_select()
    rows, _ = load_tracker()
    by = {r["biz_id"]: r for r in rows}
    try:
        for r in sel:
            bid = r["biz_id"]
            if not os.path.exists(LP("coverage", f"{bid}.json")):
                log_exception(bid, "read", "not_collected", "no coverage file; run collect first")
                continue
            out = read_business(by[bid], budget)
            print(f'{bid}: read {out.get("item_units_answered", 0)}/{out.get("item_units", 0)} item units, status {out["status"]}')
    except Paused:
        print("PAUSED: time slice used up; progress is saved, run read again")
    finally:
        save_spend()


# ---------------------------------------------------------------- scoring and status
def priority(q):
    v = lambda k: 1 if q[k]["verdict"] == "yes" and q[k].get("quote") else 0  # noqa: E731
    return 60 * v("retirement") + 40 * v("owner_work") - 20 * v("successor"), v("retirement"), v("owner_work"), v("successor")


def cmd_score():
    rows, fields = load_tracker()
    fields = ensure_columns(rows, fields)
    by = {r["biz_id"]: r for r in rows}
    ranked = []
    run_id = "ev-" + now().replace(":", "").replace("-", "")
    sel, _ = S.read_csv(LP("selection.csv"))
    for s in sel:
        bid = s["biz_id"]
        r = by.get(bid)
        if not r or r["outreach_status"] not in ("ready", "review"):
            continue
        cpath, rpath = LP("coverage", f"{bid}.json"), LP("results", f"{bid}.json")
        if not os.path.exists(cpath):
            log_exception(bid, "score", "no_coverage", "collect did not run or was budget-gated")
            r.update(evidence_status="exception: not collected", evidence_run_id=run_id)
            if r["outreach_status"] == "ready":
                set_status(r, "review")
            continue
        cov = json.load(open(cpath))
        res = json.load(open(rpath)) if os.path.exists(rpath) else None
        problems = list(cov.get("notes", [])) if cov["status"] != "failed" else [cov.get("reason", "collection failed")]
        if res is None:
            problems.append("evidence not read")
        elif res["item_units_answered"] != res["item_units"] or res["failed"]:
            problems.append(f'{res["item_units"] - res["item_units_answered"]} item unit(s) unanswered')
        complete = not problems and cov["status"] == "complete"
        r.update(coverage_status="complete" if complete else "partial", evidence_run_id=run_id, evidence_notes="; ".join(problems))
        if res:
            q = res["questions"]
            for k in ("owner_work", "successor", "retirement"):
                r[k], r[k + "_source_id"], r[k + "_quote"] = q[k]["verdict"], q[k].get("source_id", ""), q[k].get("quote", "")
            oi = res["owner_identity"]
            r["owner_identity"], r["owner_identity_source_id"], r["owner_identity_quote"] = oi["name"], oi.get("source_id", ""), oi.get("quote", "")
            score, ret, work, succ = priority(q)
        else:
            score, ret, work, succ = 0, 0, 0, 0
        pl = cov.get("place", {})
        lv = addr_level((r["address"], r["city"], r["zip"]), parse_maps_address(pl.get("address", ""))) if pl else "none"
        r["address_check"] = "verified" if lv in STRONG else "uncertain"
        supported = complete and (ret or work)
        r["priority_score"] = score if complete else ""
        r["evidence_status"] = "supported" if supported else ("unsupported" if complete else "partial_or_failed")
        if supported:
            set_status(r, "scored")
            ranked.append((score, ret, work, bid))
        elif r["outreach_status"] == "ready":
            set_status(r, "review")
    ranked.sort(key=lambda x: (-x[0], -x[1], -x[2], x[3]))
    for i, (_, _, _, bid) in enumerate(ranked, 1):
        by[bid]["priority_rank"] = i
    save_tracker(rows, fields)
    S.write_csv(LP("ranking.csv"), ["rank", "biz_id", "priority_score", "retirement_yes", "owner_work_yes", "successor_yes"],
                [dict(rank=i, biz_id=b_, priority_score=sc, retirement_yes=rt, owner_work_yes=w, successor_yes=int(by[b_]["successor"] == "yes" and bool(by[b_]["successor_quote"]))) for i, (sc, rt, w, b_) in enumerate(ranked, 1)])
    print(f"scored: {len(ranked)} supported and ranked; others stay in review (see exceptions.csv and evidence_notes)")
    return ranked


def cmd_close(biz, why, confirm):
    if not confirm:
        raise SystemExit("closing needs --confirm, and only after the owner of this tracker confirms mailed, declined or opted out")
    rows, fields = load_tracker()
    fields = ensure_columns(rows, fields)
    r = next(x for x in rows if x["biz_id"] == biz)
    set_status(r, "closed")
    r["letter_status"] = f"closed: {why}"
    save_tracker(rows, fields)
    if why == "opted_out":
        sp, _ = S.read_csv(LP("suppression.csv"))
        sp.append(dict(biz_id=biz, name=r["legal_name"], address=r["address"], reason="opted out", added_utc=now(), source="confirmed by tracker owner"))
        S.write_csv(LP("suppression.csv"), ["biz_id", "name", "address", "reason", "added_utc", "source"], sp)
    print(f"{biz} closed ({why})")


def cmd_status():
    rows, _ = load_tracker()
    print(collections.Counter((r["eligibility_status"], r["outreach_status"]) for r in rows))
    print(f"spent so far (estimate): ${spend_usd():.3f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["select", "estimate", "collect", "read", "score", "draft", "status", "verify", "run", "close"])
    ap.add_argument("biz", nargs="?")
    ap.add_argument("--budget-usd", type=float, default=5.0)
    ap.add_argument("--as", dest="why", choices=["mailed", "declined", "opted_out"])
    ap.add_argument("--confirm", action="store_true")
    ap.add_argument("--fresh", action="store_true", help="collect: discard saved review pages and collect again")
    a = ap.parse_args()
    try:
        if a.cmd == "select":
            cmd_select()
        elif a.cmd == "estimate":
            cmd_estimate()
        elif a.cmd == "collect":
            cmd_collect(a.budget_usd, a.fresh)
        elif a.cmd == "read":
            cmd_read(a.budget_usd)
        elif a.cmd == "score":
            cmd_score()
        elif a.cmd == "run":
            cmd_estimate()
            cmd_collect(a.budget_usd)
            cmd_read(a.budget_usd)
            cmd_score()
        elif a.cmd == "draft":
            import letters
            letters.cmd_draft(sys.modules[__name__])
        elif a.cmd == "close":
            cmd_close(a.biz, a.why, a.confirm)
        elif a.cmd == "status":
            cmd_status()
        elif a.cmd == "verify":
            import lead_verify
            sys.exit(lead_verify.main())
    except Fatal as e:
        print("STOPPED:", e)
        sys.exit(2)
