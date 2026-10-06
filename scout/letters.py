"""Letter drafting: letters.md, letters.pdf and mailing-list.csv by business ID.  Stdlib only.

Rules: up to 50 supported businesses, one verified detail each, the sender's own interest and a request for a
conversation. Never says or implies the owner wants to sell or retire. Unknown owner is addressed as
"Business owner". Businesses with an uncertain address go to review and are left out of the mailing list.
No supported leads gives an empty mailing list; any sample letter is labelled and excluded.
"""
import datetime
import os
import re

import scout as S

MAX_LETTERS = 50
SENDER_FIELDS = {"name": "your full name", "return_address": "your return address as a list of lines, ending with city, state and ZIP",
                 "contact": "a phone number or email where owners can reach you", "buyer_background": "one or two truthful sentences about who you are as a buyer",
                 "interest": "one sentence on what you are interested in, with no mention of anyone selling or retiring"}
BANNED = re.compile(r"\b(retir\w*|sell\w*|sale|succession|successor|exit(?:ing)?|passing the business|step(?:ping)? down)\b", re.I)
DETAIL_RE = re.compile(r"\b(since\s+(?:19|20)\d\d|established\s+in\s+(?:19|20)\d\d|founded\s+in\s+(?:19|20)\d\d|family[- ]owned|serving\s+[A-Z][\w\s]+(?:County|Orange|Anaheim|Irvine|Southern California))", re.I)
HELV = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278] + [556] * 10 + [278, 278, 584, 584, 584, 556, 1015] + \
       [667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611] + \
       [278, 278, 278, 469, 556, 333] + [556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500] + \
       [334, 260, 334, 584]
assert len(HELV) == 95


def load_sender(lead):
    p = lead.LP("sender.json")
    if not os.path.exists(p):
        return None, list(SENDER_FIELDS)
    import json
    d = json.load(open(p))
    missing = [k for k in SENDER_FIELDS if not d.get(k) or (k == "return_address" and (not isinstance(d[k], list) or len(d[k]) < 2))]
    return d, missing


def pick_detail(lead, row):
    """One verified detail: an exact sentence from the business's own saved web pages, else a verified registry fact."""
    for it in lead.jl_read(lead.LP("items", f'{row["biz_id"]}.jsonl')):
        if it["kind"] != "page":
            continue
        for _, _, sent in lead.sentences(it["text"]):
            if 25 <= len(sent) <= 220 and DETAIL_RE.search(sent) and not BANNED.search(sent) and it["text"].find(sent) >= 0:
                return dict(kind="website", text=sent, source_id=it["item_id"], url=it["url"])
    if row.get("reg_match_confidence") == "high" and row.get("reg_date"):
        return dict(kind="registry", text=f'{row["legal_name"].title()} has held a California contractor license since {row["reg_date"][:4]}',
                    source_id=f'CSLB #{row["reg_record_id"]}', url=row.get("reg_evidence_url", ""))
    return None


def render_letter(sender, row, detail, date):
    owner = row.get("owner_identity", "")
    name = owner if owner and owner != "unknown" and len(owner.split()) >= 2 else "Business owner"
    lines = [sender["name"], *([sender["organization"]] if sender.get("organization") else []), *sender["return_address"], sender["contact"], "", date, "",
             name, row["legal_name"].title() if row["legal_name"].isupper() else row["legal_name"], row["address"].title(), f'{row["city"].title()}, {row["state"]} {row["zip"]}', "",
             f"Dear {name},", ""]
    body = [f"My name is {sender['name']}. {sender['buyer_background'].strip()}", "",
            (f'I read on your website: "{detail["text"]}"' if detail["kind"] == "website" else detail["text"] + ".") +
            " It stood out to me, and I wanted to reach out personally.", "",
            sender["interest"].strip(), "",
            f"Would you be open to a short conversation? You can reach me at {sender['contact']}, or reply to the address above. "
            "There is no obligation, and if you would rather not hear from me again, just say so and I will not contact you again.", "",
            "Sincerely,", "", "", sender["name"]]
    return lines + body, name


def wrap(text, size=11, width_pt=468):
    out = []
    for para in text.split("\n"):
        if not para.strip():
            out.append("")
            continue
        cur = ""
        for w in para.split(" "):
            t = (cur + " " + w).strip()
            if sum(HELV[ord(c) - 32] if 32 <= ord(c) <= 126 else 556 for c in t) * size / 1000 > width_pt and cur:
                out.append(cur)
                cur = w
            else:
                cur = t
        out.append(cur)
    return out


def make_pdf(letters, size=11, lead=14.5):
    """letters: list of text blocks, one letter each (new page per letter, more if long). Returns (bytes, first_page_of_each)."""
    pages, first = [], []
    for block in letters:
        first.append(len(pages) + 1)
        lines = wrap(block, size)
        per = int((792 - 144) / lead)
        for i in range(0, max(len(lines), 1), per):
            pages.append(lines[i:i + per])
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", None, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"]
    kids = []
    for pg in pages:
        pid, cid = len(objs) + 1, len(objs) + 2
        kids.append(pid)

        def esc(s):
            return s.encode("cp1252", "replace").replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
        stream = b"BT /F1 %d Tf %.1f TL 72 720 Td\n" % (size, lead) + b"".join(b"(" + esc(l) + b") Tj T*\n" for l in pg) + b"ET"
        objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>" % cid)
        objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objs[1] = b"<< /Type /Pages /Kids [" + b" ".join(b"%d 0 R" % k for k in kids) + b"] /Count %d >>" % len(kids)
    out, offs = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    x = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, x)
    return bytes(out), first


def cmd_draft(lead):
    rows, fields = lead.load_tracker()
    fields = lead.ensure_columns(rows, fields)
    sender, missing = load_sender(lead)
    if missing:
        print("STOPPED: letters need your details first. Missing in scout/lead/sender.json:")
        for k in missing:
            print(f"  - {k}: {SENDER_FIELDS[k]}")
        return None
    bad = [k for k in ("buyer_background", "interest") if BANNED.search(sender[k])]
    if bad:
        print("STOPPED: these sender fields mention selling, retiring or succession, which the letters must never assert or imply:", ", ".join(bad))
        return None
    cand = [r for r in rows if r["outreach_status"] in ("scored", "drafted") and r["evidence_status"] == "supported" and r["priority_rank"]]
    cand.sort(key=lambda r: int(r["priority_rank"]))
    date = datetime.date.today().strftime("%B %d, %Y").replace(" 0", " ")
    letters, mailing, md = [], [], []
    for r in cand:
        if len(letters) >= MAX_LETTERS:
            break
        if r["address_check"] != "verified":
            if r["outreach_status"] == "scored":
                lead.set_status(r, "review")
            lead.log_exception(r["biz_id"], "draft", "address_uncertain", "mailing address not verified at street level")
            continue
        detail = pick_detail(lead, r)
        if not detail:
            lead.log_exception(r["biz_id"], "draft", "no_verified_detail", "no exact website sentence or verified registry fact available")
            continue
        text_lines, addressee = render_letter(sender, r, detail, date)
        text = "\n".join(text_lines)
        sal = next(i for i, l in enumerate(text_lines) if l.startswith("Dear "))
        gen = "\n".join(text_lines[sal:]).replace(detail["text"], "")
        for nm in (r["legal_name"], r["legal_name"].title(), addressee):  # a name like "Sellers Plumbing" is not an intent claim
            gen = gen.replace(nm, "")
        if BANNED.search(gen):
            lead.log_exception(r["biz_id"], "draft", "letter_check_failed", "generated text matched the banned intent words")
            continue
        letters.append((r, text, detail, addressee))
    pdf_blocks = [t for _, t, _, _ in letters]
    sample = False
    if not letters:
        sample = True
        pdf_blocks = ["SAMPLE - NOT FOR MAILING\nThis page is a labelled sample. It has no recipient and is not in mailing-list.csv.\n\n[Your name]\n[Your return address]\n\n[Date]\n\nBusiness owner\n[Business name]\n[Address]\n\nDear Business owner,\n\n[One truthful sentence about you as a buyer.]\n\n[One verified detail about the business, quoted from its own website.]\n\n[One sentence about your interest.]\n\nWould you be open to a short conversation?\n\nSincerely,\n[Your name]"]
    pdf, first = make_pdf(pdf_blocks)
    open(os.path.join(lead.HOME, "letters.pdf"), "wb").write(pdf)
    md.append("# Letters\n\n" + ("No supported leads, so there are no letters to mail and `mailing-list.csv` is empty. A labelled sample is included below and is excluded from the mailing list.\n\n---\n\n"
                                  "> SAMPLE - NOT FOR MAILING\n\n" + pdf_blocks[0].split("\n", 3)[3].replace("\n", "  \n") if sample else
                                  f"{len(letters)} draft letter(s), by business ID. You review, print, sign and post them. {lead.CAVEAT}\n"))
    for i, (r, text, detail, addressee) in enumerate(letters):
        md.append(f'\n---\n\n## {r["biz_id"]} (rank {r["priority_rank"]})\n\n' + text.replace("\n", "  \n") +
                  f'\n\n<!-- internal, not part of the letter: detail source {detail["kind"]} {detail["source_id"]} {detail["url"]}; addressee basis: '
                  f'{"sourced current owner" if addressee != "Business owner" else "owner unknown"}; address check {r["address_check"]} -->\n')
        mailing.append(dict(biz_id=r["biz_id"], rank=r["priority_rank"], addressee=addressee, business=r["legal_name"], line1=r["address"].title(),
                            city=r["city"].title(), state=r["state"], zip=r["zip"], address_check=r["address_check"], pdf_page=first[i],
                            detail_source=f'{detail["kind"]}:{detail["source_id"]}'))
        if r["outreach_status"] == "scored":
            lead.set_status(r, "drafted")
        r["letter_status"] = "drafted"
    open(os.path.join(lead.HOME, "letters.md"), "w", encoding="utf-8").write("".join(md))
    S.write_csv(os.path.join(lead.HOME, "mailing-list.csv"), ["biz_id", "rank", "addressee", "business", "line1", "city", "state", "zip", "address_check", "pdf_page", "detail_source"], mailing)
    lead.save_tracker(rows, fields)
    print(f"drafted {len(letters)} letter(s); mailing-list.csv has {len(mailing)} row(s)" + (" (sample page only, excluded)" if sample else ""))
    return letters
