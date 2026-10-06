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

---

# Part 2: evidence, ranking and letters

Picks up from `scout-tracker.csv`. This part owns the evidence, score, draft and `outreach_status` columns. Everything here is Orange County only because the tracker is.

## 9. Choices and connections

| Choice | Selected | Reached through |
|---|---|---|
| Evidence model | Jev (TypeSafe AI, System One), paid for through your OpenRouter key | Composio `openrouter` connection, called with the workbench's `proxy_execute` at `/api/v1/systemone` (Composio's `jev` toolkit needs a key issued by TypeSafe, so an OpenRouter key is refused there) |
| Review collector | SerpApi, Google Maps Reviews engine | Composio tools `SERPAPI_LIST_GOOGLE_MAPS_REVIEWS` and `SERPAPI_GOOGLE_MAPS_SEARCH` |

**Composio is the route (your instruction).** Each toolkit needs an authenticated Composio connection: ask Composio for a connect link (`COMPOSIO_MANAGE_CONNECTIONS`, toolkits `serpapi` and `jev`), open it, and enter your own key in Composio's form. The key never appears in chat, in this repo, or in any file here. Links expire after about 10 minutes.

**Where the work runs.** Composio's remote workbench is a persistent Python 3.13 sandbox with open internet access and a preloaded `run_composio_tool` helper. The network stages (collect and read) run there, so raw responses and fetched pages stay on its disk instead of passing through chat. Cells have a hard 180-second limit; the pipeline pauses itself at about 150 seconds, saves everything, and the next cell resumes without repeating any call. Afterwards the evidence folder is zipped, uploaded with `upload_local_file`, and downloaded here (tested: a workbench upload downloads cleanly with `curl -L`). Ranking, letters and verification then run locally.

**Jev routes, pick one.** (1) OpenRouter key via Composio `openrouter` + `proxy_execute` (selected; `jev_route="openrouter"`). (2) TypeSafe key via Composio `jev` (`jev_route="jev"`, tool `JEV_EVALUATE_STATE`). (3) Direct HTTP with `OPENROUTER_API_KEY` or `TYPESAFE_API_KEY` in the environment. All three reach the same model and return the same Choice answers.

**Two differences from calling the APIs directly.** Composio returns each tool's parsed JSON, so the "raw response" saved for each model call is that JSON (model, answers, probabilities, usage), not the HTTP bytes. And the review count to reconcile still comes from `place_info.reviews`.

**Alternative route (no Composio).** The same code can call SerpApi and Jev directly. Set `SERPAPI_API_KEY` and `TYPESAFE_API_KEY` in the cloud environment's settings (the environment menu, then Edit), start a new session, and run the commands in section 12. The scripts read keys with `os.environ`, never print or save them, and the tests prove dummy keys appear in no saved file.

## 10. What the official docs say (checked 2026-10-06)

| Fact | Source |
|---|---|
| Google's own Places API returns "a maximum of 5 reviews", sorted by relevance, with no paging. So it cannot collect every review, and the Composio Google Maps tool (which wraps it) is not used. | [Places API reference](https://developers.google.com/maps/documentation/places/web-service/reference/rest/v1/places) |
| SerpApi reviews engine: `engine=google_maps_reviews` with `place_id` or `data_id`; first page returns 8; later pages use `next_page_token` plus the original parameters, and `num` up to 20; `sort_by` accepts `qualityScore`, `newestFirst`, `ratingHigh`, `ratingLow`; each review has `review_id`, `link`, `rating`, `date`, `iso_date`, `snippet`, `likes` and a `user` object (profile, which this scout discards); `place_info.reviews` is the total, used to reconcile. | [Reviews API](https://serpapi.com/google-maps-reviews-api) |
| SerpApi place search: `engine=google_maps&type=search&q=...`; one place: `type=place&place_id=...`. Items carry `data_id`, `place_id`, `title`, `address`, `phone`, `rating`, `reviews`. | [Google Maps API](https://serpapi.com/google-maps-api) |
| SerpApi billing: only successful searches count; cached, errored and failed ones do not; a page of 100 results and an empty one each count as 1. Plans: Free $0 for 250 searches a month, Starter $25 for 1,000, Developer $75 for 5,000. | [Pricing](https://serpapi.com/pricing) |
| Jev through OpenRouter: `POST https://openrouter.ai/api/v1/systemone`, Bearer OpenRouter key, model `typesafe/jev-1.13` (or `jev-latest`), same `state` and `questions` body; the response adds `id` and `provider`, resolves the model to a dated name (for example `typesafe/jev-1.13-20260917`), and reports `usage.cost` in dollars, which the spend ledger uses. Errors: 401 missing auth, 402 insufficient credits, 413 payload too large, 429 rate limit. OpenRouter lists a 32K-token context for Jev (TypeSafe's own page says 64K with a 32K state budget), so the smaller number is the one to respect; reading windows are about 2K tokens. Output tokens are free. | [OpenRouter System One reference](https://openrouter.ai/docs/api/api-reference/systemone/submit-a-system-one-request), [OpenRouter Jev guide](https://openrouter.ai/docs/guides/community/jev) |
| Jev direct at TypeSafe: `POST https://api.typesafe.ai/v1/systemone`, header `Authorization: Bearer <key>`, body `model` (`jev-latest`, which resolves to `jev-1.13.0`), `state`, and `questions` (each with `type`, `instructions`, and for Choice a `criteria` map of up to 255 options). | [API](https://docs.typesafe.ai/api.md), [Models](https://docs.typesafe.ai/models.md) |
| Jev Choice answer: `choice`, the full `probabilities` map over every option, and `confidence`; the response also has `usage.input_tokens`. Errors: 401 bad key, 422 invalid request, 429 rate limit, 529 overloaded. | [Choice](https://docs.typesafe.ai/primitives/choice.md), [API](https://docs.typesafe.ai/api.md) |
| Jev limits: 64K context, 32K tokens for state plus the longest question, text only; $42 per billion input tokens ($0.042 per million), output free; 100K tokens per second and 80 requests per second (dynamic). | [Models](https://docs.typesafe.ai/models.md) |
| Jev weaknesses: accuracy falls when the state holds unrelated detail; it reads instructions literally; **it leans toward the first-listed Choice option**. | [Jev 1.13 limits](https://docs.typesafe.ai/model-jaggedness/jev-1.13.md) |
| Jev confidence is a statistic of one model's probability shape. The docs say nothing about comparing it across models, so this scout never does. | [Confidence](https://docs.typesafe.ai/confidence.md) |

Not confirmed from the docs I could read: whether SerpApi place results include a `website` key, and the shape of an owner reply on a review. The code handles both when present and falls back to a second `type=place` lookup for the website. The first live run will show which is true; treat this as open until then. Jev's docs give no limit on questions per call; this scout sends 4.

## 11. Cost model and the $5 pilot gate

| Item | Rate | Source |
|---|---|---|
| SerpApi search | `SERPAPI_PRICE_PER_SEARCH_USD`, default $0.025 (the Starter plan rate; the Free plan's first 250 a month cost $0 extra) | pricing page |
| Jev input | $0.042 per million tokens, output free | models page |

Per business: SerpApi searches = 2 (place search, place lookup) + 1 + ceil((reviews - 8) / 20) review pages. Jev tokens are bounded above by assuming state is billed once per question (4 questions per read). For the one ready business, `estimate` prints:

| Scenario | SerpApi searches | Jev tokens | Estimate |
|---|---|---|---|
| 50 reviews | 6 | 87,587 | $0.154 |
| 150 reviews | 11 | 237,158 | $0.285 |
| 500 reviews | 28 | 760,658 | $0.732 |

These are estimates, not quotes: the review count is unknown until the place is matched, and SerpApi's per-search price depends on your plan. The pilot budget defaults to $5 and is **cumulative** across resumes (`scout/lead/spend.json`). Before collecting or reading a business, the scout projects its cost; if that would pass the budget, it does not run and records `budget_exceeded` in `scout/lead/exceptions.csv`. Report the actual spend before scaling up.

## 12. Run it

```bash
python3 -I scout/lead.py select
python3 -I scout/lead.py estimate
python3 -I scout/lead.py run --budget-usd 5
python3 -I scout/lead.py draft
python3 -I scout/lead.py verify
python3 -I scout/lead.py close biz-151 --as mailed --confirm
python3 -I scout/test_lead.py
```

`run` is select, estimate, collect, read, score. `test_lead.py` is an offline end-to-end test with a mock model and mock collector; it uses only labelled SAMPLE data in a temp folder, and says nothing about Jev's accuracy. `LEAD_TEST_TRANSPORT=composio python3 -I scout/test_lead.py` runs the same test through the Composio mapping with a fake executor shaped like Composio's published output schemas.

### 12b. Run through Composio

1. `python3 -I scout/lead.py select` and `estimate` (local, free).
2. `python3 -I scout/composio_bundle.py` writes `scout/lead/workbench-bundle.b64` (modules, a stand-in for `scout.py`, the selected tracker rows, the local suppression and spend files).
3. In the workbench: unpack the bundle into `/mnt/files/oc`, set `SCOUT_HOME=/mnt/files/oc`, `import lead, composio_transport`, then `composio_transport.install(lead, run_composio_tool, proxy_execute=proxy_execute, jev_route="openrouter")`. The first Jev call tries the endpoint forms `https://openrouter.ai/api/v1/systemone`, `/api/v1/systemone`, `/v1/systemone` in turn and remembers the one that works, because how `proxy_execute` expects the endpoint is not documented in what I could read. If SerpApi is not connected yet, pass `place_tool="COMPOSIO_SEARCH_GOOGLE_MAPS"` for the place search only (it needs no connection); reviews still need SerpApi.
4. In each cell set `lead.DEADLINE = time.time() + 150`, then call `lead.cmd_collect(5.0)` and `lead.cmd_read(5.0)`. A `PAUSED` line means run the same cell again.
5. Zip `/mnt/files/oc/scout/lead`, `upload_local_file` it, download the link with `curl -L` here, and unzip over `scout/lead/`.
6. Locally: `lead.py score`, `draft`, `verify`.

Composio schemas checked 2026-10-06: `SERPAPI_LIST_GOOGLE_MAPS_REVIEWS` takes `place_id` or `data_id`, `sort_by`, `next_page_token`, `review_count` (1 to 20) and `language`, and returns `reviews`, `place_info` and `serpapi_pagination`; `JEV_EVALUATE_STATE` takes `model`, `state` and `questions` and returns `model`, `answers` (Choice answers carry `choice`, `probabilities`, `confidence`) and `usage`. A one-business lookup through Composio's built-in Google Maps search showed Maps lists a website and a review total, and returns reviewer names inside `user_reviews`; the pipeline stores none of that.

## 13. Rules

**Selection.** `eligibility_status=ready` and `outreach_status=ready`, not in `scout/lead/suppression.csv` (matched by business ID, or by name plus street). `closed` is never reselected. The suppression file starts empty; add opt-outs and prior contacts to it.

**Matching.** The Maps place must match the business name and the street address. Same name at another address is rejected as a namesake. A suffix-only difference (Inc, LLC) with the same street counts, because Maps titles drop suffixes; same words only does not. The website comes from the Maps listing and is accepted only if its home page contains the business name plus the street number and name, the phone, or the ZIP. No match is an exception, never a guess.

**Reviews.** Pages are followed until the token runs out. Reconciliation is stored: fetched (review objects received), unique (distinct `review_id`), reported (`place_info.reviews`). Any gap, or a page cap, means partial coverage and the business stays in review. Reviewer profiles, photos and ratings details are dropped before anything is saved. Kept per review: stable item ID, `review_id`, URL, date, rating, full text, and the business's own reply (read and quotable).

**Pages.** The home page, then one existing About, Team and Services page each, found from home-page links first and standard paths second. `robots.txt` is honoured. Each fetch attempt is logged; pages that do not exist are logged as such, not invented.

**Reading.** Every item is read in full by Jev, in windows of 8,000 characters with 800 overlap, in batches of 25 calls (a call is one window with four Choice questions). Options are listed with `unknown` first, to offset Jev's bias toward the first option. Every call saves its batch ID, item ID, full request, raw response, resolved model and token usage. Probabilities are also written separately to `scout/lead/jev-probabilities/`. Failed calls are recorded and retried on the next run; a business with an unanswered call is partial. Unchanged items are never re-read (keyed by text hash, question set and model).

**Quotes.** Jev returns a choice and probabilities, not passages. When a window looks like a yes or no, the scout re-asks sentence by sentence, takes the best sentence as the quote, and validates it: the quote must appear exactly in the saved text, and a gate must pass.
- Owner doing work: the owner is the grammatical subject of a trade verb, with no negation and nobody else (a technician) in between, or "personally" and "himself" next to the owner.
- Named successor: a succession phrase, a named person, and the owner in context.
- Retirement: the retirement belongs to the owner. A writer saying "as a retired teacher" or a founder who retired in 1995 does not count.
- Owner identity: a full name presented in the present tense as owner, owner-operator or proprietor. A founder alone, a past tense, a technician or a first name alone does not count. Two different names means unknown.

A yes needs the quote. A no needs an explicit contradiction quote. Yes and no together, or neither, means unknown. Provider confidence is saved but never compared across providers. For a model other than Jev, add an adapter that requests structured labels (verdict plus a quote copied from the text); the same exact-quote and gate checks apply.

**Score.** For a complete record only: `60*retirement + 40*owner_work - 20*successor`, each variable 1 only for a quote-verified yes. At least one of owner work or retirement must be supported. This orders a list; it is not a sale probability. Unsupported, partial or failed records stay `review`.

**Status.** `ready` to `scored` to `drafted` to `closed`; exceptions go to `review`. `closed` happens only through `close ... --confirm`, which you run after the owner of the tracker confirms mailed, declined or opted out; an opt-out also goes into the suppression file. Reruns never repeat a finished stage.

**Letters.** Up to 50 supported businesses, best rank first. Each has one verified detail (an exact sentence from the business's own saved web page, else a verified licence fact), the sender's interest, and a request for a short conversation. It never says or implies that the owner wants to sell or retire; any intent word in the generated text, or in the sender's own text, stops the draft. Unknown owner is "Business owner". An address not verified at street level sends the business to review and keeps it off the mailing list. With no leads, the mailing list is empty and any sample page is labelled "SAMPLE - NOT FOR MAILING". Outputs: `letters.pdf`, `letters.md`, `mailing-list.csv`, all by business ID. You review, print, sign and post. Those three files and the evidence state in `scout/lead/` stay out of git (they hold your address and third-party review text); tell me if you want them versioned on a private branch.

**Sender file.** The draft stage needs `scout/lead/sender.json` with `name`, `return_address` (a list of lines, ending with city, state and ZIP), `contact`, `buyer_background` (your own truthful sentences) and `interest`. The scout will not invent any of it.

## 14. Verification

`lead.py verify` checks: every selected ID has a result or an exception; every item is read exactly once and every batch answered (or its business is flagged partial); review counts re-derived from the saved pages; no reviewer profile, key or auth header in any saved file; every model call kept its raw response; every quote is exact and passes its gate; score arithmetic and ranking; the status flow; letter addresses and addressees against the tracker; no intent words in letters. Counts are derived from IDs and files, not from what the pipeline printed. Shared owner context may repeat across items; that is allowed.

## 15. Unfinished in part 2

1. **No live run yet.** The Composio connections for SerpApi and Jev still need your key entered at Composio, and the sender details have not been supplied, so nothing has been collected or drafted. The offline test covers the mechanics only.
2. Still unconfirmed: the shape of an owner reply on a review. (Settled by a live lookup: Maps place results do carry a `website`.)
3. Third-party sites may block the web-page fetch; blocked pages are logged as exceptions, not retried around.
4. A model other than Jev needs its own adapter.
