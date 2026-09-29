"""
Discovery and scoring mechanics for dja_discovery_example.ipynb.

This is the part of the method that is fixed and not worth re-reading every
time: talking to the ADS API, merging duplicate records, and scoring a
full-text hit as genuine DJA use or not. The notebook itself only calls these
functions and looks at what comes back.
"""
import html
import re
import time
import unicodedata

import requests

ADS_BASE = "https://api.adsabs.harvard.edu/v1"

# Context words that, alongside 'DJA', suggest the Dawn JWST Archive
# (as opposed to unrelated uses of the bare 'DJA' acronym, e.g. someone's initials)
ARCHIVE_CONTEXT = [
    "archive", "dataset", "catalog", "photometry", "reduction", "pipeline",
    "imaging", "mosaic", "data release", "public", "download", "grism",
    "spectroscopy", "cosmos-web", "primer", "ceers", "jades", "uncover",
    "mast", "survey", "filter", "drizzle", "nircam", "nirspec", "release",
    "miri", "msa", "micro-shutter assembly", "zenodo", "calibration",
    "heintz", "de graaff", "valentino",
    "retrieved", "data products", "initiative", "cosmic dawn center", "dawn-cph",
]

# 'DJA' must be a standalone, upper-case acronym: not preceded/followed by a
# letter or digit, so 'aDJAcent' or 'DJAs' are NOT hits, but '(DJA)' and 'DJA,' are.
DJA_RE  = re.compile(r"(?<![A-Za-z0-9])DJA(?![A-Za-z0-9])")
FULL_RE = re.compile(r"\bDawn JWST Archive\b", re.I)
# 'DJA' used as a person's initials, typically in acknowledgements, e.g.
# "DJA acknowledges support from ...", "AB, DJA and CD thank ..."
_DJA = r"(?<![A-Za-z0-9])DJA(?![A-Za-z0-9])"
INITIALS_RE = re.compile(
    _DJA + r"(?:[\s,&]|and)+(?:[A-Z]{2,4}[\s,&]+|and\s+){0,4}(?i:acknowledg|thank|grateful)"
    r"|" + _DJA + r"\s+(?:\w+\s+){0,2}?(?i:support|fund|fellowship|receiv|wish|would like)"
    r"|(?<![A-Za-z0-9])[A-Z]{2,4}(?:,|\s+and)\s+" + _DJA + r"\s+(?i:acknowledg|thank|are|is|was|were)"
    r"|" + _DJA + r"(?:,?\s*(?:and\s+)?[A-Z][A-Za-z]{1,5}\b){0,3},?\s+(?:were|are|was|is|have|has)\s+(?:\w+\s+){0,2}?(?i:supported|funded|grateful)",
)
CTX_RE  = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in ARCHIVE_CONTEXT) + r")\b", re.I)
# Any author affiliation string mentioning the Cosmic Dawn Center -- the institute
# that builds and runs the DJA. Used for the "external adoption" community stat.
DAWN_RE = re.compile(r"cosmic dawn", re.I)


def ads_get(token, path, params):
    r = requests.get(ADS_BASE + path, headers={"Authorization": f"Bearer {token}"},
                      params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def strip_em(text):
    """Remove ADS highlight tags <em>...</em> and decode HTML entities."""
    return html.unescape(re.sub(r"</?em>", "", text))


def paginated_search(token, q, fq, fl, hl_q, rows=50, max_rows=5000, sleep_sec=0.3):
    """
    Page through ADS /search/query, requesting highlighted snippets alongside the
    metadata. Sorting on (date, bibcode) rather than date alone avoids the
    duplicate/skipped rows that ties in a date-only sort can cause.
    """
    start = 0
    while start < max_rows:
        params = {
            "q": q, "fq": fq, "fl": fl,
            "rows": min(rows, max_rows - start), "start": start,
            "sort": "date asc, bibcode asc",
            "hl": "true", "hl.fl": "body,abstract,title,ack",
            "hl.q": hl_q, "hl.snippets": "5",
        }
        resp = ads_get(token, "/search/query", params)
        response = resp.get("response", {})
        docs = response.get("docs", [])
        highlighting = resp.get("highlighting", {})
        yield docs, highlighting
        num_found = response.get("numFound", 0)
        start += len(docs)
        if not docs or start >= num_found:
            break
        time.sleep(sleep_sec)


def dedupe(docs, hl):
    """
    Merge duplicate records of the same paper (arXiv preprint + published version,
    or several preprint versions), linked through shared arXiv ids in ADS
    `identifier` or an identical long title. Keeps the published record (else the
    most cited), with the highest citation count and the highlights of whichever
    record has them. Returns (docs, highlights).
    """
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def keys(d):
        ids = [i.lower() for i in (d.get("identifier") or []) if re.match(r"(?i)arxiv:|\d{4}arxiv", i)]
        title = re.sub(r"\W+", " ", html.unescape((d.get("title") or [""])[0])).strip().lower()
        ks = ["id:" + d["bibcode"].lower()] + ["id:" + i for i in ids]
        if len(title) > 25:
            ks.append("t:" + title)
        return ks

    for d in docs:
        ks = keys(d)
        for k in ks[1:]:
            parent[find(ks[0])] = find(k)
    groups = {}
    for d in docs:
        groups.setdefault(find("id:" + d["bibcode"].lower()), []).append(d)
    out, out_hl = [], {}
    for g in groups.values():
        g.sort(key=lambda d: (bool(re.match(r"\d{4}arXiv", d["bibcode"])), -(d.get("citation_count") or 0)))
        kept = dict(g[0])
        kept["citation_count"] = max((d.get("citation_count") or 0) for d in g)
        out.append(kept)
        for d in g:
            if d["bibcode"] in hl:
                out_hl[kept["bibcode"]] = hl[d["bibcode"]]
                break
    return out, out_hl


def score_paper(bibcode, hl_map):
    """
    Score 1-9 based on ADS full-text highlight snippets.

    9   'Dawn JWST Archive' appears verbatim
    8   'DJA' + >=2 archive-context keywords
    6   'DJA' + 1 archive-context keyword
    5   'DJA' in the body field
    4   'DJA' in another field (title / abstract / ack)
    3   snippet present but no DJA / Dawn JWST Archive match
    1   no snippet at all

    Returns {"score", "reason", "snippets"} -- the snippets are kept so a low or
    borderline score can be read and checked, not just trusted.
    """
    hl = hl_map.get(bibcode, {})
    snippets = []
    for field, texts in hl.items():
        if isinstance(texts, list):
            for t in texts:
                snippets.append({"field": field, "text": strip_em(t)})

    if not snippets:
        return {"score": 1, "reason": "No full-text hit", "snippets": []}

    max_score = 0
    for s in snippets:
        has_full = bool(FULL_RE.search(s["text"]))
        ctx = len(set(m.lower() for m in CTX_RE.findall(s["text"])))
        no_init = INITIALS_RE.sub(" ", s["text"])
        has_dja = bool(DJA_RE.search(no_init))
        s["initials"] = bool(DJA_RE.search(s["text"])) and not has_dja
        if (has_dja and not has_full and s["field"] == "ack" and ctx == 0
                and re.search(r"acknowledg|thank|support|fellowship|grant|fund", s["text"], re.I)):
            has_dja = False
            s["initials"] = True
        s["hit"] = has_full or has_dja

        if has_full:                          sc = 9
        elif has_dja and ctx >= 2:             sc = 8
        elif has_dja and ctx == 1:             sc = 6
        elif has_dja and s["field"] == "body": sc = 5
        elif has_dja:                          sc = 4
        else:                                  sc = 3
        if s["hit"] and s["field"] == "body" and sc < 9:
            sc = min(sc + 1, 9)
        s["score"] = sc
        max_score = max(max_score, sc)

    snippets.sort(key=lambda x: x["score"], reverse=True)
    only_initials = max_score < 4 and any(x.get("initials") for x in snippets)
    reason = (
        "DJA is author initials (acknowledgements)" if only_initials else
        "Strong archive context"        if max_score >= 7 else
        "DJA present, moderate context" if max_score >= 5 else
        "DJA found, context ambiguous"  if max_score >= 4 else
        "Weak / indirect reference"
    )
    return {"score": max_score, "reason": reason, "snippets": snippets}


def is_galaxy(arxiv_class, galaxy_classes):
    cls = arxiv_class or []
    return bool(set(cls) & set(galaxy_classes)) or not any(c.startswith("astro-ph") for c in cls)


def n_authors(doc):
    return len(doc.get("author") or [])


def first_author(doc):
    au = doc.get("author") or []
    return au[0] if au else None


def dawn_affiliated(doc):
    """True if any author's ADS affiliation string mentions the Cosmic Dawn Center."""
    return any(DAWN_RE.search(a) for a in (doc.get("aff") or []) if a and a != "-")


def _strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _surname(name):
    last, _, _ = name.partition(",")
    return _strip_accents(last).strip().lower()


def _given_tokens(name):
    """'J.-S.' -> ['j','s']; 'Jiasheng' -> ['jiasheng']; 'Katriona M. L.' -> ['katriona','m','l']."""
    _, _, rest = name.partition(",")
    toks = re.split(r"[\s\-]+", _strip_accents(rest).strip())
    return [t.strip(".").lower() for t in toks if t.strip(".")]


def _names_compatible(name_a, name_b):
    """
    True if two names sharing a surname could plausibly be the same person: every
    given/middle-name token that is spelled out (not a bare initial) in BOTH names must
    match exactly; a token that is only an initial on one side just needs to agree on
    its first letter. This is what tells 'Ellis, R.' / 'Ellis, Richard' apart from
    'Zhang, Junkai' / 'Zhang, Junyu' -- same surname, but neither given name is a bare
    initial and they disagree once spelled out. First-initial-only matching (an earlier
    version of this function) merged those wrongly; this is the fix.
    """
    ta, tb = _given_tokens(name_a), _given_tokens(name_b)
    if not ta or not tb:
        return False
    for x, y in zip(ta, tb):
        if len(x) > 1 and len(y) > 1:
            if x != y:
                return False
        elif x[0] != y[0]:
            return False
    return True


def resolve_authors(docs):
    """
    Assigns each (bibcode, position) author slot an identity key, merging occurrences
    that are very likely the same person:

      1. Same ORCID (orcid_pub, else orcid_user, else orcid_other) -> same identity.
         High confidence -- this is what ORCID is for.
      2. No ORCID on this occurrence, but the exact same name string has a known ORCID
         somewhere else in the dataset -> linked to that identity. Still high confidence:
         it takes an exact full "Last, First Middle" match, not just a surname.
      3. Never seen with an ORCID anywhere -> clustered within each surname by
         _names_compatible (see its docstring). A heuristic, and the only tier that can
         mis-merge or mis-split -- e.g. two people who share a surname, initial-only
         given names on both records, and are in fact different ('Huang, J.' could be
         almost anyone) still collide here. Rare in practice; spot-check if in doubt.

    Returns (identities, orcid_coverage): identities is {(bibcode, position): key};
    orcid_coverage is the fraction of author-occurrences carrying some ORCID.
    """
    def orcid_of(doc, i):
        for field in ("orcid_pub", "orcid_user", "orcid_other"):
            vals = doc.get(field) or []
            if i < len(vals) and vals[i] and vals[i] != "-":
                return vals[i]
        return None

    occurrences = []
    name_to_orcid = {}
    for doc in docs:
        for i, name in enumerate(doc.get("author") or []):
            orcid = orcid_of(doc, i)
            occurrences.append((doc["bibcode"], i, name, orcid))
            if orcid:
                name_to_orcid.setdefault(name, orcid)

    # tier 3: cluster the remaining names within each surname by mutual compatibility
    # (union-find), instead of a flat "surname + first initial" key that can't tell
    # apart two different given names sharing a first letter.
    remaining = {name for _, _, name, orcid in occurrences if not orcid and name not in name_to_orcid}
    by_surname = {}
    for name in remaining:
        by_surname.setdefault(_surname(name), []).append(name)

    name_cluster = {}
    for surname, names in by_surname.items():
        parent = {n: n for n in names}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for a in range(len(names)):
            for b in range(a + 1, len(names)):
                if _names_compatible(names[a], names[b]):
                    ra, rb = find(names[a]), find(names[b])
                    if ra != rb:
                        parent[ra] = rb
        for n in names:
            name_cluster[n] = (surname, find(n))

    identities = {}
    n_with_orcid = 0
    for bib, i, name, orcid in occurrences:
        if orcid:
            key = ("orcid", orcid)
            n_with_orcid += 1
        elif name in name_to_orcid:
            key = ("orcid", name_to_orcid[name])
        else:
            key = ("name",) + name_cluster[name]
        identities[(bib, i)] = key

    coverage = n_with_orcid / len(occurrences) if occurrences else 0.0
    return identities, coverage


def results_to_df(results, ads_abs_fmt, galaxy_classes):
    """results: list of {'doc': ..., 'scored': ...} -> one row per paper."""
    import pandas as pd

    rows = []
    for r in results:
        doc, sc = r["doc"], r["scored"]
        year_raw = doc.get("year", "")
        rows.append({
            "bibcode":        doc.get("bibcode", ""),
            "ads_url":        ads_abs_fmt.format(doc.get("bibcode", "")),
            "title":          html.unescape((doc.get("title") or ["Untitled"])[0]),
            "year":           int(year_raw) if str(year_raw).isdigit() else None,
            "citation_count": doc.get("citation_count", 0) or 0,
            "galaxy":         is_galaxy(doc.get("arxiv_class"), galaxy_classes),
            "n_authors":      n_authors(doc),
            "first_author":   first_author(doc),
            "authors":        " | ".join(doc.get("author") or []),
            "dawn_author":    dawn_affiliated(doc),
            "score":          sc["score"],
            "reason":         sc["reason"],
        })
    return pd.DataFrame(rows)


def load_manual_flags(path):
    """bibcode -> 'clean' | 'contaminants', from a flagged_contaminants.csv-style file."""
    import csv
    import os

    over = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                over[row["bibcode"]] = row["to_table"]
    return over
