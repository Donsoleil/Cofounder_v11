# SCOUT.md: Orange County plumbing and HVAC off-market scout

A rerunnable runbook. Any agent or person with a shell can follow it. It needs no special skills or plugins.

**Scope:** Orange County, California. NAICS 238220 (plumbing, heating and air-conditioning contractors). PPP loans of $150,000 or more.

**Standing cautions, repeated on every output:** record age is not owner age and does not show intent to sell. DOL participants are not current headcount. Payroll proxies are historical, loan-based numbers, not revenue or value. This tool never infers revenue or a purchase price. A missing filing is unknown, never a reason to drop a business.

## 1. What you need (capability check)

| Need | Why | Checked in the build session (2026-10-06) |
|---|---|---|
| Run code (Python 3.9+, standard library only) | the pipeline is `scout/*.py` | yes, Python 3.11 |
| Download large files over HTTPS | SBA file is 452 MB, DOL files total about 445 MB | yes for SBA and DOL |
| Call web endpoints | portal pages, CKAN and Socrata style APIs | yes |
| Write files in this folder | tracker, ledgers, results page | yes |
| About 2 GB free disk, about 10 minutes | raw downloads are kept in `scout/raw/` (git-ignored) | yes |

If you run this in a chat tool that cannot run code, open files or reach the internet, it cannot do this job. It needs an execution connection: a shell on a machine with outbound HTTPS to the hosts below. A cloud coding session, a laptop terminal or a CI runner all work.

**Two sources did not work from the build session, and the reasons are logged once in `scout/state/source-status.json`:**

1. **CSLB License Master (contractor licenses).** The portal's download stream was cut at roughly 24 to 28 MB (it never finished), and later requests got HTTP 403 from CSLB's firewall. I did not try to get around the firewall. A browser download on a normal connection should work. See section 8.
2. **California Secretary of State (SOS) charter dates.** bizfile Online sits behind a bot challenge, and SOS sells bulk data. SOS's own help guide says a BE Bulk Order "Master Unload" is $100 per unload through a bizfile account, and weekly unloads are free ([guide, section 19](https://bpd.cdn.sos.ca.gov/ucc/ucc-online-help.pdf)). That needs your account and payment, so it is not used. No parser exists for it yet because no sample file has been seen.

## 2. Run it

```bash
python3 -I scout/scout.py run                # download (reuses files that have not changed), build, write everything
python3 -I scout/scout.py run --skip-fetch   # no network, uses scout/raw/
python3 -I scout/scout.py verify             # independent re-check of every output
python3 -I scout/scout.py verify --repeat    # also reruns the build in a scratch copy to test ID stability (about 7 min)
python3 -I scout/fetch.py [SOURCE_ID ...]    # download only
python3 -I scout/fetch.py import-cslb <file.csv> "Updated as of M/D/YYYY"
```

`-I` is on purpose: it keeps Python from loading code that sits next to downloaded data.

## 3. What gets written

| File | What it is |
|---|---|
| `scout-tracker.csv` | one row per business, permanent ID `biz-001` and up |
| `scout-loans.csv` | one row per PPP loan number, with draw, loan facts, source row number |
| `source-ledger.csv` | one row per source file: URL, retrieval time, reporting period, bytes, SHA-256, encoding, row count, status |
| `match-ledger.csv` | every join considered, with both names, both addresses, confidence, decision and a clickable evidence URL |
| `results.html` | the readable page: counts, stage counts, five ready businesses, unknowns, sources, all businesses |
| `scout/state/id-registry.csv` | which loans belong to which permanent ID |
| `scout/state/run-stats.json` | every count from the last run |
| `scout/state/source-status.json` | unavailable sources, each logged once |
| `scout/state/dol-name-only-hits.csv` | same-name DOL sponsors that had no address agreement (kept for review, not counted) |
| `scout/logs/download-log.jsonl` | reproducible download record (URL, headers, bytes, SHA-256, time) |
| `scout/raw/` | raw downloads (not in git; too big). The log plus the SHA-256 in the ledger let you check a re-download is the same file |

## 4. Sources

| ID | URL | Used for |
|---|---|---|
| SBA-PPP-150K | https://data.sba.gov/sites/default/files/distribution/SBA-OCA-2022-07-001/public_150k_plus_240930.csv (page: https://data.sba.gov/dataset/ppp-foia) | the loan universe |
| CSLB-MASTER | https://www.cslb.ca.gov/onlineservices/dataportal/ContractorList (form: choose "License Master", then download CSV) | original trade-license date, registered names, DBA |
| DOL-5500-YYYY, DOL-5500SF-YYYY | https://www.askebsa.dol.gov/FOIA%20Files/YYYY/Latest/F_5500_YYYY_Latest.zip and `F_5500_SF_YYYY_Latest.zip`, for 2025, 2024, 2023 (index: https://www.dol.gov/agencies/ebsa/about-ebsa/our-activities/public-disclosure/foia/form-5500-datasets) | retirement plan filings |

2025 is the latest published DOL year and is still filling in as late filers report, so 2024 and 2023 are searched too. A business's plan comes from the newest plan period found.

## 5. Rules

**SBA file.** Streamed, never loaded whole. The encoding is detected on every run by a full byte pass. The 9/30/2024 file is not valid UTF-8; its only non-ASCII bytes are 0xBF, 0xA0, 0xAE, 0xE9 and 0xF6, so it is read as Windows-1252 (bytes 0x80 to 0x9F are absent, so latin-1 reads identically). Some `¿` characters are upstream damage to letters like ñ; the name matcher drops them.
Filter: `BorrowerState=CA`, `NAICSCode=238220`, `CurrentApprovalAmount>=150000`. Then Orange County: `ProjectState=CA` and `ProjectCountyName=ORANGE`. A loan whose borrower ZIP says Orange but whose county field disagrees (or the reverse) is kept, flagged `county_zip_check`, and sent to review. The SBA file has no county column for the borrower, so this is the check available.
Reported separately: source rows, statewide matching loans, Orange County loans, business groups.
Every loan number is its own row. The draw comes from `ProcessingMethod`: PPP is first draw, PPS is second draw.

**Business groups.** Two loans are one business only if name and address both agree: same legal name and street; same legal name and ZIP; same name ignoring Inc/LLC and same street and ZIP; or a DBA on one loan equals the other's name at the same street. Every rule that fired is stored. Names with the same legal name at a different address are not merged; both are flagged for review.

**Permanent IDs.** `scout-tracker.csv` IDs are `biz-NNN`. A rerun reuses an ID if any of its loan numbers, or its identity key, is already in `id-registry.csv`. New businesses get the next number. IDs are never renumbered or recycled. If new evidence joins two old IDs, the lower one survives and the other is marked merged and closed.

**Registration date.** Meaning is stored beside every date. CSLB's `IssueDate` is the original issue date of a license. It is a license date, not an SOS charter date, and a business can be older than its current license (for example after a change of entity). Confidence:

| Confidence | Name relation | Address |
|---|---|---|
| high | exact, or legal name and DBA both on the record | same street, or same house number and street name (DBA case: or same ZIP) |
| medium | same ignoring Inc/LLC, or same distinctive words | same street; or exact name with same ZIP |
| low | exact or suffix-only | same city or ZIP only |

Only a single `high` match is "verified". More than one `high`, or only `medium`, goes to review. A name match with no address agreement is not a match.

**Eligibility.**
- `ready`: verified registration date 30 or more years before the run date, and license status CLEAR, and no open conflicts.
- `review`: any uncertain join, a missing registration result, a county conflict, a possible duplicate, a license that is not CLEAR, or a verified date under 30 years while the registration source is incomplete.
- `closed`: a clear exclusion only. Those are a borrower flagged non-profit, a verified date under 30 years from a complete source, a business merged into another ID, or one that left the SBA extract.
The 30-year flag is stored per row (`age_30plus`) with the run date (`age_as_of`).

**Retirement plans (Form 5500 and 5500-SF).** The national files are searched with no state filter, so out-of-state sponsors are included. A filing counts only with a retirement benefit code (`[1-3][A-Z]` in `TYPE_PENSION_BNFT_CODE` or `SF_TYPE_PENSION_BNFT_CODE`; welfare-only filings are rejected). Matching uses a supplied EIN (`ein_known` column) first, else sponsor name or sponsor DBA plus an address agreement against the loan address or the matched license address. Same-name sponsors without address agreement are listed in `dol-name-only-hits.csv` and not counted. Saved per plan: form, plan ID (`EIN-planNumber`), plan name, plan period start and end, filing date (`DATE_RECEIVED`), active participants at plan-year end, benefit codes, filing ID (`ACK_ID`), sponsor EIN. Plans are never summed. One primary plan is shown (newest plan period, then newest filing date); other plans are listed beside it. No filing found means `unknown_no_filing_found`, and the business stays in play.

**Payroll proxy.** `loan / 2.5 * 12`, per loan, never added across draws, only when every check the SBA file allows passes: processing method PPP or PPS, initial amount equals current amount, nothing undisbursed, no EIDL refinance proceeds, not a sole proprietor or self-employed borrower, and not at the program cap ($10M first draw, $2M second draw). The file does not carry the lender's payroll workpapers, so this is "passes every available check", not proof. Anything else is `unknown` with the reason. Historical `JobsReported` is kept in its own columns and is never mixed with DOL participants.

**Tracker ownership.** This scout owns the identity, loan, registration, pension and eligibility columns (list in `scout.py`, `OWNED`). On a rerun it rewrites only those. `outreach_status` is set to `ready` only when a new ID is created. Any other column a person or another prompt adds (scores, notes) is kept as is, in place. `ein_known` and `ein_source` are inputs you can fill in; they are never overwritten.

## 6. Verification

`verify` re-reads the raw files with separate code and checks: unique IDs and loan numbers; raw row recount equals the pipeline's; every loan matches its raw row; every registration join re-verifies against the CSLB file; every pension join re-verifies against its DOL row (EIN, plan ID, period, filing date, participants, benefit codes, name, address); payroll-proxy arithmetic; eligibility rules; SHA-256 of every source; unavailable sources logged once. `--repeat` adds rerun tests: unchanged rerun reproduces every column, hand-edited outreach status and extra columns survive, and a business unknown to the registry gets one new ID without renumbering anything.

## 7. Changing scope

Edit the constants at the top of `scout/scout.py` (`REGION`, `STATE`, `COUNTY`, `NAICS`, `MIN_LOAN`, `AGE_YEARS`). The CSLB source is California-only; for another state, replace the registration stage with that state's source. Use a new folder or branch, since IDs are permanent per tracker.

## 8. Unfinished work and how to finish it

1. **Full CSLB file.** In a browser open https://www.cslb.ca.gov/onlineservices/dataportal/ContractorList, pick "License Master", download the CSV, then run `python3 -I scout/fetch.py import-cslb <file> "Updated as of M/D/YYYY"` (use the date the page shows) and `python3 -I scout/scout.py run`. Until then the scout runs on a partial file in limited mode: a match in it is real, but "not found" and "license under 30 years" stay review and never close. The partial file holds licenses issued 2015 to 2026 and 18 from the 1930s and 1940s, and none from 1950 to 2014, so almost all 30-year-old businesses show as unknown.
2. **SOS charter dates.** Optional second route for sole-proprietor-free entities: buy a bizfile BE Master Unload ($100) and add a parser once you have a sample. Not built.
3. **Loan-level payroll verification** is limited to what the SBA file shows.
4. **EINs** are not in the SBA or CSLB data. Supply `ein_known` where you have an independent source.
5. The CSLB retry policy waits 6 hours after a failed attempt, so a rerun never hammers their firewall.
