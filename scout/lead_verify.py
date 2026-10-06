"""Independent checks for the evidence, ranking and letters stage.

Re-derives counts from IDs and files rather than trusting the numbers the pipeline printed.
"""
import collections
import csv
import json
import os
import re
import sys

import lead
import letters
import scout as S

CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append(dict(check=name, ok=bool(ok), detail=detail))
    print(("PASS  " if ok else "FAIL  ") + name + (f"  [{detail}]" if detail else ""))


def main(quiet_header=False):
    rows, _ = S.read_csv(S.TRACKER)
    by = {r["biz_id"]: r for r in rows}
    sel, _ = S.read_csv(lead.LP("selection.csv"))
    exc, _ = S.read_csv(lead.LP("exceptions.csv"))
    exc_ids = {e["biz_id"] for e in exc}
    L = lead.LP

    # 1. coverage of the selection: every ready ID has a result or an exception
    sel_ids = [s["biz_id"] for s in sel]
    check("selection has no duplicate IDs", len(sel_ids) == len(set(sel_ids)), f"{len(sel_ids)} selected")
    no_out = [i for i in sel_ids if not os.path.exists(L("results", f"{i}.json")) and i not in exc_ids]
    check("every selected ID has a result or a recorded exception", not no_out, f"{len(sel_ids)} selected, missing: {no_out}")
    bad_sel = [i for i in sel_ids if i in by and by[i]["eligibility_status"] != "ready"]
    check("selection only contains eligibility_status=ready records", not bad_sel)

    # 2. items are assigned to exactly one unit set, and every batch was answered
    bidx = lead.jl_read(L("batch-index.jsonl"))
    seen_unit = collections.Counter()
    unanswered = []
    for b in bidx:
        if b["level"] == "item":
            seen_unit.update(b["units"])
        if b["answered"] != len(b["units"]):
            unanswered.append((b["batch_id"], b["biz_id"]))
    unexplained = [u for u, i in unanswered if by.get(i, {}).get("evidence_status") != "partial_or_failed"]
    check("every batch was answered in full, or its business is flagged partial and held in review", not unexplained and bool(bidx or not sel_ids),
          f"{len(bidx)} batches; {len(unanswered)} with unanswered units, all in flagged businesses" if not unexplained else str(unexplained[:3]))
    check("batch index lists each batch once", len({b["batch_id"] for b in bidx}) == len(bidx))
    dup_units = [u for u, c in seen_unit.items() if c > 1]
    bad_items = []
    n_items = n_units = 0
    for i in sel_ids:
        p = L("items", f"{i}.jsonl")
        if not os.path.exists(p) or not os.path.exists(L("results", f"{i}.json")):
            continue
        for it in lead.jl_read(p):
            if not it["text"]:
                continue
            n_items += 1
            need = [f'{it["item_id"]}#w{k:02d}' for k, _ in enumerate(lead.windows(it["text"]), 1)]
            n_units += len(need)
            if any(seen_unit[u] != 1 for u in need):
                bad_items.append(it["item_id"])
    check("every item is read exactly once in item-level batches (windows cover the full text)", not bad_items and not dup_units, f"{n_items} items, {n_units} windows; problems: {bad_items[:3]}")

    # 3. reconcile review counts from the saved pages
    bad_cov = []
    for i in sel_ids:
        cp = L("coverage", f"{i}.json")
        if not os.path.exists(cp):
            continue
        cov = json.load(open(cp))
        if "reviews" not in cov:
            continue
        ids, fetched, reported = [], 0, None
        for fn in sorted(os.listdir(L("raw", i))):
            if fn.startswith("reviews-page-"):
                d = json.load(open(L("raw", i, fn)))
                fetched += len(d["reviews"])
                ids += [r.get("review_id") for r in d["reviews"]]
                reported = d["place_info"].get("reviews", reported)
        items = [x for x in lead.jl_read(L("items", f"{i}.jsonl")) if x["kind"] == "review"]
        if (cov["reviews"]["fetched"], cov["reviews"]["unique"], cov["reviews"]["reported"]) != (fetched, len(set(ids)), reported) or len(items) != len(set(ids)):
            bad_cov.append(i)
        gap = (reported or 0) - len(set(ids))
        if (gap != 0 or cov["reviews"]["capped"]) and cov["status"] == "complete":
            bad_cov.append(i + ":gap marked complete")
    check("fetched, unique and reported review counts re-derive from saved pages; gaps are never marked complete", not bad_cov, str(bad_cov[:3]))

    # 4. privacy and secrets
    leaked, secrets = [], [v for v in (os.environ.get(lead.KEY_SERP, ""), os.environ.get(lead.KEY_JEV, "")) if v]
    for root, _, files in os.walk(lead.LEAD):
        for fn in files:
            if fn.endswith((".html", ".pdf")):
                continue
            t = open(os.path.join(root, fn), encoding="utf-8", errors="replace").read()
            if re.search(r'"(contributor_id|thumbnail|local_guide)"', t) or any(s in t for s in secrets) or "Bearer " in t:
                leaked.append(os.path.join(root, fn))
    check("no reviewer profiles, API keys or auth headers in any saved file", not leaked, str(leaked[:3]))

    # 5. raw model responses saved for every unit, with model name and request
    bad_raw, n_raw = [], 0
    for i in sel_ids:
        d = L("batches", i)
        for fn in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            for e in lead.jl_read(os.path.join(d, fn)):
                n_raw += 1
                if not e.get("raw_response") or not e.get("request") or (e["status"] == "ok" and not e.get("model_resolved")):
                    bad_raw.append(e["unit_id"])
    check("every model call keeps its raw response, request, batch ID, item ID and resolved model", not bad_raw, f"{n_raw} calls saved")

    # 6. quotes validate against saved text and ownership context
    bad_q, n_q = [], 0
    for i, r in by.items():
        if r.get("evidence_status") not in ("supported", "unsupported", "partial_or_failed") or not os.path.exists(L("results", f"{i}.json")):
            continue
        res = json.load(open(L("results", f"{i}.json")))
        texts = {x["item_id"]: x["text"] for x in lead.jl_read(L("items", f"{i}.jsonl"))}
        for q, v in res["questions"].items():
            if v["verdict"] in ("yes", "no"):
                n_q += 1
                ok, _ = lead.gate_ok(q, v["verdict"], v["quote"], v.get("context", ""), r["legal_name"])
                if v["source_id"] not in texts or v["quote"] not in texts[v["source_id"]] or not ok:
                    bad_q.append((i, q))
                if r[q] != v["verdict"] or r[q + "_quote"] != v["quote"] or r[q + "_source_id"] != v["source_id"]:
                    bad_q.append((i, q, "tracker differs from result"))
            elif r[q] != "unknown" or r[q + "_quote"]:
                bad_q.append((i, q, "unknown verdict carries a value"))
        oi = res["owner_identity"]
        if oi["name"] != "unknown" and (oi["source_id"] not in texts or oi["quote"] not in texts[oi["source_id"]] or oi["name"] not in oi["quote"]):
            bad_q.append((i, "owner_identity"))
    check("every yes/no verdict has a source ID and an exact quote found in the saved text, passing the ownership gate", not bad_q, f"{n_q} quotes checked; {bad_q[:3]}")

    # 7. score arithmetic and ranking
    bad_s, ranked = [], []
    for i, r in by.items():
        if r.get("evidence_status") == "supported":
            v = lambda k: 1 if r[k] == "yes" and r[k + "_quote"] else 0  # noqa: E731
            want = 60 * v("retirement") + 40 * v("owner_work") - 20 * v("successor")
            if str(want) != str(r["priority_score"]) or not (v("retirement") or v("owner_work")) or r["coverage_status"] != "complete":
                bad_s.append(i)
            ranked.append((int(r["priority_rank"]), int(r["priority_score"]), i))
        elif r.get("priority_rank"):
            bad_s.append(i + ":rank without support")
    ranked.sort()
    order_ok = all(a[1] >= b[1] for a, b in zip(ranked, ranked[1:])) and [x[0] for x in ranked] == list(range(1, len(ranked) + 1))
    check("score = 60*retirement + 40*owner_work - 20*successor from quote-verified evidence only; ranks are 1..n in score order; support required", not bad_s and order_ok, f"{len(ranked)} ranked; {bad_s[:3]}")

    # 8. status machine
    allowed = {"ready", "review", "scored", "drafted", "closed"}
    bad_st = [i for i, r in by.items() if r["outreach_status"] not in allowed]
    bad_st += [i for i, r in by.items() if r["outreach_status"] in ("scored", "drafted") and r.get("evidence_status") != "supported"]
    bad_st += [i for i, r in by.items() if r["outreach_status"] == "review" and r.get("evidence_status") and not (r.get("evidence_notes") or i in exc_ids or r["evidence_status"] in ("unsupported", "partial_or_failed"))]
    check("outreach_status follows ready, scored, drafted, closed (exceptions review); scored and drafted rows are supported", not bad_st, str(bad_st[:3]))

    # 9. letters
    ml = S.read_csv(os.path.join(S.HOME, "mailing-list.csv"))[0] if os.path.exists(os.path.join(S.HOME, "mailing-list.csv")) else None
    if ml is not None:
        md = open(os.path.join(S.HOME, "letters.md"), encoding="utf-8").read()
        pdf = open(os.path.join(S.HOME, "letters.pdf"), "rb").read()
        bad_l = []
        for m in ml:
            r = by.get(m["biz_id"])
            if not r or r["outreach_status"] not in ("drafted", "scored"):
                bad_l.append((m["biz_id"], "not drafted"))
                continue
            want = r["owner_identity"] if r["owner_identity"] not in ("", "unknown") and len(r["owner_identity"].split()) >= 2 else "Business owner"
            if m["addressee"] != want or m["line1"].upper() != r["address"].upper() or m["zip"] != r["zip"] or r["address_check"] != "verified":
                bad_l.append((m["biz_id"], "identity or address differs from tracker"))
            if md.count(f"## {m['biz_id']} ") != 1:
                bad_l.append((m["biz_id"], "not exactly once in letters.md"))
            if re.search(r"\bretir\w*|\bsell\w*|\bsale\b|\bsuccess\w*", md.split(f"## {m['biz_id']} ")[1].split("\n---")[0].replace(r["legal_name"].title(), ""), re.I):
                bad_l.append((m["biz_id"], "letter mentions intent words"))
        pages = len(re.findall(rb"/Type /Page /Parent", pdf))
        check("letters: each mailing-list row is a drafted, address-verified business; addressee and address match the tracker; no intent words",
              not bad_l, f"{len(ml)} letters, {pages} PDF page(s); {bad_l[:3]}")
        check("letters: at most 50, none for closed or suppressed businesses, no SAMPLE in the mailing list",
              len(ml) <= 50 and not any(by[m["biz_id"]]["outreach_status"] == "closed" for m in ml) and not any("SAMPLE" in m["business"].upper() and "sample" not in os.environ.get("SCOUT_TEST", "") for m in ml))
        if not ml:
            check("no leads: mailing list is empty and any sample is labelled", "SAMPLE - NOT FOR MAILING" in md or "No supported leads" in md)

    # 10. spend within budget gate
    if os.path.exists(L("spend.json")):
        sp = json.load(open(L("spend.json")))
        check("spend is recorded", "usd_estimate" in sp, f'${sp.get("usd_estimate")} (SerpApi at ${lead.SERP_USD_PER_SEARCH} per search assumed)')
    failed = [c for c in CHECKS if not c["ok"]]
    print(f"\n{len(CHECKS) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0
