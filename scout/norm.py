"""Name and address normalization used for every join in the scout.

Rules are deliberately conservative: a name match alone never counts. Callers
must also get an address level of 'street' or 'street_loose' before a join is
treated as verified.
"""
import re
import unicodedata

TOKEN_CANON = {"INCORPORATED": "INC", "CORPORATION": "CORP", "COMPANY": "CO", "COMPANIES": "CO",
               "LIMITED": "LTD"}
LEGAL = {"INC", "LLC", "LLP", "LP", "LTD", "CORP", "CO", "PLLC", "PC", "PA"}
GENERIC = {"AIR", "CONDITIONING", "HEATING", "COOLING", "PLUMBING", "MECHANICAL", "SERVICE",
           "SERVICES", "AND", "THE", "HVAC", "REFRIGERATION", "ELECTRIC", "HEAT", "A", "C", "OF",
           "DRAIN", "SEWER", "ROOTER", "CONTRACTORS", "CONTRACTING", "CONSTRUCTION", "SYSTEMS",
           "INDUSTRIES", "INC", "LLC", "LLP", "LP", "LTD", "CORP", "CO", "PLLC", "PC", "PA"}
DBA_SPLIT = re.compile(r"\s+(?:D\s*/\s*B\s*/\s*A|D\.B\.A\.?|DBA|DOING BUSINESS AS|A\s*/\s*K\s*/\s*A|AKA)\s+",
                       re.I)

ADDR_ABBR = {"STREET": "ST", "AVENUE": "AVE", "AV": "AVE", "BOULEVARD": "BLVD", "ROAD": "RD",
             "DRIVE": "DR", "LANE": "LN", "COURT": "CT", "CIRCLE": "CIR", "PLACE": "PL",
             "HIGHWAY": "HWY", "PARKWAY": "PKWY", "TERRACE": "TER", "WAY": "WAY", "NORTH": "N",
             "SOUTH": "S", "EAST": "E", "WEST": "W", "SUITE": "STE", "UNIT": "STE", "APARTMENT": "APT",
             "BUILDING": "BLDG", "FLOOR": "FL", "ROOM": "RM", "SPACE": "SPC"}
UNIT_TOKENS = {"STE", "APT", "BLDG", "FL", "RM", "SPC", "TRLR", "LOT"}
DIRECTIONS = {"N", "S", "E", "W", "NE", "NW", "SE", "SW"}


def _ascii_upper(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode("ascii")
    return s.upper()


def norm(s):
    """Uppercase, ASCII, '&'->AND, punctuation removed, legal suffixes canonical."""
    s = _ascii_upper(s).replace("&", " AND ").replace("'", "")
    s = re.sub(r"[^A-Z0-9]+", " ", s).strip()
    s = re.sub(r"\bP L L C\b", "PLLC", s)
    s = re.sub(r"\bL L C\b", "LLC", s)
    s = re.sub(r"\bL L P\b", "LLP", s)
    s = re.sub(r"\bL P\b$", "LP", s)
    s = re.sub(r"\bP C\b$", "PC", s)
    return " ".join(TOKEN_CANON.get(t, t) for t in s.split())


def core(s):
    toks = norm(s).split()
    if toks and toks[0] == "THE":
        toks = toks[1:]
    while toks and toks[-1] in LEGAL:
        toks.pop()
    return " ".join(toks)


def tokenkey(s):
    toks = sorted(t for t in core(s).split() if t not in GENERIC)
    return " ".join(toks)


def name_variants(raw):
    """[(kind, strict, core, tokenkey)]; kind is 'primary' or 'dba' for text after DBA/AKA."""
    parts = DBA_SPLIT.split(_ascii_upper(raw or ""))
    out = []
    for i, p in enumerate(parts):
        st = norm(p)
        if st:
            out.append(("primary" if i == 0 else "dba", st, core(p), tokenkey(p)))
    return out


def name_match(variants_a, variants_b):
    """Best name relation between two variant lists: 'E' strict, 'C' core, 'T' token, or ''."""
    best = ""
    rank = {"": 0, "T": 1, "C": 2, "E": 3}
    for _, s1, c1, t1 in variants_a:
        for _, s2, c2, t2 in variants_b:
            r = "E" if s1 == s2 else "C" if c1 and c1 == c2 else "T" if t1 and t1 == t2 else ""
            if rank[r] > rank[best]:
                best = r
    return best


def zip5(z):
    m = re.search(r"\d{5}", z or "")
    return m.group(0) if m else ""


def street_key(addr):
    a = _ascii_upper(addr).replace("#", " STE ")
    a = re.sub(r"[^A-Z0-9]+", " ", a).strip()
    a = re.sub(r"\bP O BOX\b|\bPOST OFFICE BOX\b", "PO BOX", a)
    toks = [ADDR_ABBR.get(t, t) for t in a.split()]
    while toks and toks[-1] == "0":  # '8435 Westglen Drive 0.0' style artifacts
        toks.pop()
    for i, t in enumerate(toks):
        if t in UNIT_TOKENS and i > 0:
            toks = toks[:i]
            break
    return " ".join(toks)


def _house_and_street(sk):
    toks = sk.split()
    if len(toks) < 2 or not toks[0].isdigit():
        return "", ""
    rest = [t for t in toks[1:] if t not in DIRECTIONS]
    return toks[0], (rest[0] if rest else "")


def addr_level(a, b):
    """Compare two (street, city, zip) tuples. Returns the strongest agreement found:
    'street' > 'street_loose' > 'zip' > 'city' > 'none'."""
    s1, s2 = street_key(a[0]), street_key(b[0])
    z1, z2 = zip5(a[2]), zip5(b[2])
    c1, c2 = norm(a[1]), norm(b[1])
    if s1 and s1 == s2 and (not z1 or not z2 or z1 == z2 or c1 == c2):
        return "street"
    h1, n1 = _house_and_street(s1)
    h2, n2 = _house_and_street(s2)
    if h1 and h1 == h2 and n1 and n1 == n2 and (z1 == z2 or c1 == c2):
        return "street_loose"
    if z1 and z1 == z2:
        return "zip"
    if c1 and c1 == c2:
        return "city"
    return "none"


STRONG = {"street", "street_loose"}
_ADDR_RANK = {"none": 0, "city": 1, "zip": 2, "street_loose": 3, "street": 4}


def best_addr_level(addrs_a, addrs_b):
    best, pair = "none", None
    for a in addrs_a:
        for b in addrs_b:
            lv = addr_level(a, b)
            if _ADDR_RANK[lv] > _ADDR_RANK[best]:
                best, pair = lv, (a, b)
    return best, pair


def confidence(name_rel, addr_lv):
    """Join confidence from name relation + address level. Only 'high' may drive a
    'ready' status; everything else routes to review."""
    if name_rel == "E" and addr_lv in STRONG:
        return "high"
    if name_rel == "D":  # legal name and DBA both documented on the other record
        return "high" if addr_lv in STRONG or addr_lv == "zip" else ("medium" if addr_lv == "city" else "none")
    if (name_rel in ("C", "T") and addr_lv in STRONG) or (name_rel == "E" and addr_lv == "zip"):
        return "medium"
    if (name_rel in ("E", "C") and addr_lv == "city") or (name_rel in ("C", "T") and addr_lv == "zip"):
        return "low"
    return "none"
