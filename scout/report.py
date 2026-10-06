"""Readable results page (static HTML, no external assets)."""
import collections
import html

E = html.escape

CSS = """
:root{--bg:#fbfaf7;--fg:#1d1d1b;--mut:#6b6a63;--card:#fff;--line:#e4e1d8;--acc:#0b5f8a;--ok:#1e7a46;--warn:#9a6a00;--bad:#9b2c2c}
@media (prefers-color-scheme:dark){:root{--bg:#171715;--fg:#ecebe6;--mut:#a09f96;--card:#1f1f1c;--line:#34332e;--acc:#6bb7e0;--ok:#62c28a;--warn:#e0b04a;--bad:#e08a8a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1080px;margin:0 auto;padding:24px 16px 64px}h1{font-size:1.7rem;margin:0 0 4px}h2{font-size:1.2rem;margin:36px 0 10px;border-bottom:1px solid var(--line);padding-bottom:6px}
.sub{color:var(--mut);margin:0 0 16px}.note{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--warn);padding:10px 14px;border-radius:6px;margin:14px 0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px}.stat{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px}
.stat b{display:block;font-size:1.6rem}.stat span{color:var(--mut);font-size:.85rem}
table{width:100%;border-collapse:collapse;font-size:.88rem;background:var(--card)}th,td{border:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}th{background:var(--bg);position:sticky;top:0}
.wrap{overflow-x:auto}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin:14px 0}
.card h3{margin:0 0 2px;font-size:1.1rem}.tag{display:inline-block;font-size:.75rem;padding:1px 8px;border-radius:99px;border:1px solid var(--line);color:var(--mut);margin-right:4px}
.ready{color:var(--ok);border-color:var(--ok)}.review{color:var(--warn);border-color:var(--warn)}.closed{color:var(--bad);border-color:var(--bad)}
dl{display:grid;grid-template-columns:170px 1fr;gap:4px 12px;margin:10px 0 0}dt{color:var(--mut)}dd{margin:0;overflow-wrap:anywhere}a{color:var(--acc)}
input{width:100%;padding:8px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--fg);margin:8px 0}
@media(max-width:600px){dl{grid-template-columns:1fr}dt{margin-top:6px}}code{font-size:.85em}
"""


def _kv(pairs):
    return "<dl>" + "".join(f"<dt>{E(k)}</dt><dd>{v}</dd>" for k, v in pairs if v not in (None, "")) + "</dl>"


def _link(url, text):
    return f'<a href="{E(url)}" target="_blank" rel="noopener">{E(text)}</a>' if url else E(text)


def _card(g, row):
    st = row["eligibility_status"]
    loans = "<br>".join(
        f'{E(l["loan_number"])} · {"first" if l["draw"] == "1" else "second"} draw · approved {E(l["date_approved"])} · '
        f'${float(l["current_approval_amount"]):,.0f} · {E(l["loan_status"])}' for l in g["loans"])
    jobs = "<br>".join(f'{E(l["loan_number"])}: {E(l["hist_jobs_reported"])} (historical, as reported on the loan)' for l in g["loans"])
    proxy = "<br>".join(
        f'{E(l["loan_number"])}: ' + (f'${float(l["hist_payroll_proxy_usd"]):,.0f} per year (historical proxy)' if l["hist_payroll_proxy_usd"] else
                                       "unknown (" + E(l["payroll_proxy_basis"].replace("unknown: ", "")) + ")") for l in g["loans"])
    reg = ""
    if row["reg_record_id"]:
        reg = (f'CSLB license <b>#{E(row["reg_record_id"])}</b> ({_link(row["reg_evidence_url"], "open CSLB Check License")}, enter the number) · issued <b>{E(row["reg_date"])}</b> '
               f'({E(str(row["reg_age_years"]))} years before {E(row["age_as_of"])}) · confidence <b>{E(row["reg_match_confidence"])}</b>')
    else:
        reg = "unknown"
    if row["pension_status"] == "plan_found":
        pen = (f'{E(row["pension_form"])} · plan <code>{E(row["pension_plan_id"])}</code> “{E(row["pension_plan_name"])}” · '
               f'plan year {E(row["pension_plan_period_begin"])} to {E(row["pension_plan_period_end"])} · filed {E(row["pension_filing_date"])} · '
               f'active participants <b>{E(row["pension_active_participants"] or "not reported")}</b> · codes {E(row["pension_benefit_codes"])} · '
               f'{_link(row["pension_evidence_url"], "DOL dataset file")} (ACK_ID <code>{E(row["pension_ack_id"])}</code>)')
    else:
        pen = "unknown: " + E(row["pension_match_evidence"])
    return (f'<div class="card"><h3>{E(row["biz_id"])} · {E(row["legal_name"])}</h3>'
            f'<div><span class="tag {st}">{E(st)}</span><span class="tag">{E(row["address"])}, {E(row["city"])} {E(row["zip"])}</span></div>'
            + _kv([("Why this status", E(row["eligibility_reason"])),
                   ("PPP loans (kept separate)", loans + "<br>" + _link(row["loan_evidence_url"], "SBA PPP FOIA dataset") + " (file public_150k_plus_240930.csv)"),
                   ("Historical JobsReported", jobs), ("Historical payroll proxy", proxy),
                   ("Registration date", reg), ("What the date means", E(row["reg_date_meaning"])),
                   ("Match evidence", E(row["reg_match_evidence"])),
                   ("Retirement plan", pen), ("Other plans (never summed)", E(row["pension_other_plans"]))]) + "</div>")


def write_results(path, groups, stats, ledger, tracker, source_status):
    by_id = {r["biz_id"]: r for r in tracker}
    sba = stats["sba"]
    elig = collections.Counter(r["eligibility_status"] for r in tracker)
    reg = collections.Counter(r["reg_match_confidence"] for r in tracker)
    pen = collections.Counter(r["pension_status"] for r in tracker)
    age30 = collections.Counter(r["age_30plus"] for r in tracker)
    loans = [l for g in groups for l in g["loans"]]
    proxy_known = sum(1 for l in loans if l["hist_payroll_proxy_usd"])
    ready = [g for g in groups if by_id[g["biz_id"]]["eligibility_status"] == "ready"]
    shown = ready[:5]

    def stat(n, label):
        return f'<div class="stat"><b>{n:,}</b><span>{E(label)}</span></div>'

    out = [f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
           f'<title>OC Plumbing and HVAC Scout</title><style>{CSS}</style></head><body><main>',
           f'<h1>{E(stats["region"])} plumbing and HVAC scout</h1>',
           f'<p class="sub">NAICS 238220 · PPP loans of $150,000 or more · built {E(stats["as_of"])} · tracker: <code>scout-tracker.csv</code></p>',
           f'<div class="note">{E("Record age is not owner age and does not show intent to sell. Participants are not current headcount. Payroll proxies are historical, loan-based figures, not revenue or value. Missing filings are unknown, not a no.")}</div>',
           "<h2>Counts, kept separate</h2><div class=\"grid\">",
           stat(sba["source_rows"], "rows in the SBA $150K+ source file"),
           stat(sba["matching_loans_state"], f"loans matching state {'CA'}, NAICS 238220, $150K+ (statewide)"),
           stat(sba["matching_loans_county"], "of those in Orange County (loans, each loan number and draw kept)"),
           stat(stats["business_groups"], "business groups after name and address de-duplication"), "</div>",
           f'<p class="sub">Source file: {sba["source_rows"]:,} data rows, {sba["header_cols"]} columns, {sba.get("malformed_rows", 0)} malformed, '
           f'{sba.get("duplicate_loan_numbers", 0)} duplicate loan numbers, {sba.get("blank_borrower_state", 0)} blank BorrowerState rows '
           f'({sba.get("naics_rows_blank_state", 0)} of them are NAICS 238220, so the state filter loses nothing). Encoding: {E(sba["encoding"]["chosen"])} '
           f'(not valid UTF-8; non-ASCII bytes {E(", ".join(sba["encoding"]["nonascii_bytes"]))}). County check: {sba["county_zip_conflicts"]} loan(s) where project county and borrower ZIP disagree.</p>',
           "<h2>Stage counts</h2><div class=\"wrap\"><table><tr><th>Stage</th><th>Count</th></tr>"]
    for k in ("ready", "review", "closed"):
        out.append(f"<tr><td>Eligibility: {k}</td><td>{elig.get(k, 0)}</td></tr>")
    for k, v in sorted(reg.items()):
        out.append(f"<tr><td>Registration match: {E(k)}</td><td>{v}</td></tr>")
    out.append(f"<tr><td>Registration date 30+ years old (flag)</td><td>{age30.get('Y', 0)}</td></tr>"
               f"<tr><td>Registration date under 30 years</td><td>{age30.get('N', 0)}</td></tr>"
               f"<tr><td>Registration date unknown</td><td>{age30.get('unknown', 0)}</td></tr>")
    for k, v in sorted(pen.items()):
        out.append(f"<tr><td>Retirement plan: {E(k)}</td><td>{v}</td></tr>")
    out.append(f"<tr><td>Loans with a historical payroll proxy / unknown</td><td>{proxy_known} / {len(loans) - proxy_known}</td></tr></table></div>")
    out.append(f"<h2>Ready businesses ({len(shown)} shown; five were asked for)</h2>")
    if len(shown) < 5:
        out.append(f'<div class="note">Only {len(ready)} business{"" if len(ready) == 1 else "es"} can honestly be called ready right now, so fewer than five are shown. Ready needs a verified registration or license date at least 30 years old, and the CSLB file is only partial (see below). Nothing was loosened to reach five.</div>')
    if not shown:
        out.append('<div class="note">No business is ready yet. A business is ready only with a verified registration or license date at least 30 years old. See unfinished work below.</div>')
    else:
        out.append(f'<p class="sub">The first {len(shown)} of {len(ready)} ready businesses by ID, not ranked or chosen for quality.</p>')
        out += [_card(g, by_id[g["biz_id"]]) for g in shown]
    out.append("<h2>Unknowns and unfinished work</h2><ul>")
    for sid, v in source_status.items():
        out.append(f"<li><b>{E(sid)} unavailable:</b> {E(v['reason'])} (logged {E(v['first_logged_utc'])})</li>")
    out.append(f"<li>{reg.get('unknown_partial_source', 0) + reg.get('unavailable', 0)} businesses have an unknown registration date because the CSLB file is partial or missing; {reg.get('none', 0)} had no license with name and address agreement in a complete file.</li>"
               f"<li>CSLB’s file lists only current or expired-but-renewable licenses. Businesses licensed under a different name, or whose license is long gone, show as unknown.</li>"
               f"<li>California SOS charter dates were not used. bizfile Online blocks scripts, and SOS sells bulk data (a BE Master Unload is $100 through a bizfile account, per SOS's own help guide). That needs your account, so it is left for you. Sole proprietors have no SOS charter anyway.</li>"
               f"<li>{pen.get('unknown_no_filing_found', 0)} businesses have no matching retirement-plan filing in the 2025, 2024 or 2023 DOL files. That is unknown, not a no. The 2025 file is still filling in as late filers report.</li>"
               f"<li>{len(loans) - proxy_known} of {len(loans)} loans have no payroll proxy because they were not verifiably standard 2.5x uncapped payroll loans.</li></ul>")
    out.append("<h2>Sources</h2><div class=\"wrap\"><table><tr><th>ID</th><th>Retrieved</th><th>Reporting period</th><th>Rows</th><th>SHA-256</th><th>Status</th></tr>")
    for r in ledger:
        out.append(f'<tr><td>{_link(r["url"], r["source_id"])}</td><td>{E(r["first_retrieved_utc"])}</td><td>{E(r["reporting_period"])}</td>'
                   f'<td>{E(r["rows"])}</td><td><code>{E(r["sha256"][:16])}…</code></td><td>{E(r["status"])}{(" · " + E(r["notes"])) if r["notes"] else ""}</td></tr>')
    out.append("</table></div><h2>All businesses</h2><input id=\"q\" placeholder=\"Filter by name, ID, city or status\">"
               "<div class=\"wrap\"><table id=\"t\"><tr><th>ID</th><th>Business</th><th>City</th><th>Loans</th><th>Registration</th><th>Age 30+</th><th>Plan</th><th>Status</th></tr>")
    for r in tracker:
        out.append(f'<tr><td>{E(r["biz_id"])}</td><td>{E(r["legal_name"])}</td><td>{E(r["city"])}</td><td>{E(r["loan_count"])}</td>'
                   f'<td>{E(r["reg_date"] or "unknown")} ({E(r["reg_match_confidence"])})</td><td>{E(r["age_30plus"])}</td>'
                   f'<td>{E(r["pension_status"].replace("_", " "))}</td><td><span class="tag {E(r["eligibility_status"])}">{E(r["eligibility_status"])}</span></td></tr>')
    out.append("</table></div><script>document.getElementById('q').addEventListener('input',function(e){var v=e.target.value.toLowerCase();"
               "document.querySelectorAll('#t tr').forEach(function(r,i){if(i)r.style.display=r.textContent.toLowerCase().indexOf(v)<0?'none':''})})</script></main></body></html>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("".join(out))
