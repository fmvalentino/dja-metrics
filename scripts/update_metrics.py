#!/usr/bin/env python3
"""
Rebuild data/metrics.json from a live NASA ADS full-text search for papers
mentioning the Dawn JWST Archive (DJA).

This is a trimmed, non-interactive port of the discovery + scoring logic in
dja_auto_discovery.ipynb (the private working notebook this repo's numbers
come from) -- DJA-side only: no comparison sample, no citation bootstrap.
See ../README.md for how the two are related and how to keep them in sync.

Requires the ADS_TOKEN environment variable (never hardcode a token here --
this script and its output are public). In GitHub Actions this is read from
a repository secret; see .github/workflows/update.yml.
"""
import csv
import html
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

import requests

ROOT       = Path(__file__).resolve().parent.parent
DATA_JSON  = ROOT / "data" / "metrics.json"
FLAGS_CSV  = ROOT / "data" / "manual_flags.csv"   # manual clean/contaminant corrections, see README
LIBRARY_ID_FILE = ROOT / "data" / "ads_library_id.txt"   # id of the curated ADS Library, see sync_ads_library.py
KNOWN_CSV  = ROOT / "data" / "known_clean_bibcodes.csv"   # last run's confirmed papers, see resolve_missing

ADS_TOKEN  = os.environ.get("ADS_TOKEN")
ADS_BASE   = "https://api.adsabs.harvard.edu/v1"
CURRENT_YEAR = date.today().year

YEAR_RANGE   = "2023-"     # DJA launched mid-2023
MIN_SCORE    = 4           # papers scoring below this are false positives (see score_paper)
ROWS_PER_PAGE = 50
SLEEP_SEC    = 0.3

MAIN_Q  = 'full:("Dawn JWST Archive" OR "DJA")'
HL_Q    = '"Dawn JWST Archive" OR "DJA"'
FIELDS  = ("id,bibcode,title,author,year,aff,citation_count,doctype,identifier,arxiv_class,"
           "orcid_pub,orcid_user,orcid_other")
DJA_FQ  = [f"year:{YEAR_RANGE}", "collection:astronomy"]

# same word list as the notebook: context that suggests the *archive*, not a person's initials
ARCHIVE_CONTEXT = [
    "archive", "dataset", "catalog", "photometry", "reduction", "pipeline",
    "imaging", "mosaic", "data release", "public", "download", "grism",
    "spectroscopy", "cosmos-web", "primer", "ceers", "jades", "uncover",
    "mast", "survey", "filter", "drizzle", "nircam", "nirspec", "release",
    "miri", "msa", "micro-shutter assembly", "zenodo", "calibration",
    "heintz", "de graaff", "valentino",
    "retrieved", "data products", "initiative", "cosmic dawn center", "dawn-cph",
]

DJA_RE  = re.compile(r"(?<![A-Za-z0-9])DJA(?![A-Za-z0-9])")
FULL_RE = re.compile(r"\bDawn JWST Archive\b", re.I)
_DJA = r"(?<![A-Za-z0-9])DJA(?![A-Za-z0-9])"
INITIALS_RE = re.compile(
    _DJA + r"(?:[\s,&]|and)+(?:[A-Z]{2,4}[\s,&]+|and\s+){0,4}(?i:acknowledg|thank|grateful)"
    r"|" + _DJA + r"\s+(?:\w+\s+){0,2}?(?i:support|fund|fellowship|receiv|wish|would like)"
    r"|(?<![A-Za-z0-9])[A-Z]{2,4}(?:,|\s+and)\s+" + _DJA + r"\s+(?i:acknowledg|thank|are|is|was|were)"
    r"|" + _DJA + r"(?:,?\s*(?:and\s+)?[A-Z][A-Za-z]{1,5}\b){0,3},?\s+(?:were|are|was|is|have|has)\s+(?:\w+\s+){0,2}?(?i:supported|funded|grateful)",
)
CTX_RE  = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in ARCHIVE_CONTEXT) + r")\b", re.I)
DAWN_RE = re.compile(r"cosmic dawn", re.I)

# Known DAWN/DJA core-team members whose ADS affiliation string doesn't reliably say
# "Cosmic Dawn" (e.g. Brammer's papers mostly just say "Niels Bohr Institute, University
# of Copenhagen" -- true, but not distinctive: plenty of non-DAWN astronomers share that
# affiliation, so matching on it directly would be a false-positive risk of its own).
# A paper with one of these people as an author is DAWN-affiliated regardless of wording.
# Keyed by ORCID -- see dawn_affiliated().
DAWN_TEAM_ORCIDS = {
    "0000-0003-2680-005X": "Gabriel Brammer",
}

# Papers describing each DJA data product -- a formal citation to one of these is a
# high-confidence signal for which product a paper actually built on. See
# dja_auto_discovery.ipynb section 8 for how these four were chosen and spot-checked
# before this was ported here.
KEY_PAPERS = {
    "photometry":   {"2023ApJ...947...20V": "Valentino+23"},
    "spectroscopy": {
        "2025A&A...693A..60H": "Heintz+25",
        "2025A&A...697A.189D": "de Graaff+25",
        "2025A&A...699A.358V": "Valentino+25",
    },
}

# Product-specific terms, matched against a paper's own DJA-context snippet (not the
# whole paper -- a bare "NIRCam" mention is noise anywhere else in a JWST paper, but is a
# real signal right next to the DJA reference itself). Fallback for whoever cites none of
# the four KEY_PAPERS (e.g. links the DJA website instead of a methods paper) -- lower
# confidence than a citation match. Two tiers: a "strong" hit (an instrument/mode name, a
# filter designation like F277W, or "zspec") outweighs a "weak" one (a generic word like
# "catalog", or a team-member surname that also shows up on the other product's papers).
# Excludes ambiguous bare terms like "redshift" (photo-z and spec-z catalogs both use it).
PHOTOM_STRONG_RE = re.compile(r"nircam|\bmiri\b|mosaic|segmentation|psf-matched|morpholog|\bF\d{3,4}[WMN]\b", re.I)
PHOTOM_WEAK_RE   = re.compile(r"photometr|catalog|imaging|multiband|grizli|\bimages?\b|magnitud|genin", re.I)
SPEC_STRONG_RE   = re.compile(r"nirspec|prism|\bmsa\b|msaexp|grism|zspec", re.I)
SPEC_WEAK_RE     = re.compile(r"spectr|emission[- ]line|broad[- ]line|heintz|de graaff", re.I)


def product_score(text):
    """(photometry, spectroscopy) score for a snippet: 2 per strong hit, 1 per weak hit,
    each counted once (a repeated word doesn't add more). Whichever side scores higher
    wins; equal nonzero scores mean genuine dual evidence ("both"); zero-zero means no
    usable signal at all."""
    photom = 2 * bool(PHOTOM_STRONG_RE.search(text)) + bool(PHOTOM_WEAK_RE.search(text))
    spec   = 2 * bool(SPEC_STRONG_RE.search(text))   + bool(SPEC_WEAK_RE.search(text))
    return photom, spec


def ads_get(path, params, retries=3):
    """A single flaky request (timeout, transient 5xx) shouldn't fail the whole monthly
    run -- there are more ADS calls per run now (citation lookups, the missing-paper
    safety net) than a bare retry-free version could comfortably absorb."""
    for attempt in range(retries):
        try:
            r = requests.get(ADS_BASE + path, headers={"Authorization": f"Bearer {ADS_TOKEN}"},
                              params=params, timeout=30)
            r.raise_for_status()
            return r.json()
        except (requests.exceptions.RequestException,) as e:
            if attempt == retries - 1:
                raise
            print(f"  ADS request failed ({e}), retrying ({attempt + 1}/{retries})...")
            time.sleep(2 * (attempt + 1))


def strip_em(text):
    return html.unescape(re.sub(r"</?em>", "", text))


def paginated_search(q, fq, fl, hl_q, rows=ROWS_PER_PAGE, max_rows=5000):
    start = 0
    while start < max_rows:
        params = {
            "q": q, "fq": fq, "fl": fl,
            "rows": min(rows, max_rows - start), "start": start,
            "sort": "date asc, bibcode asc",   # unique sort -- avoids page overlap/skip on ties
            "hl": "true", "hl.fl": "body,abstract,title,ack",
            "hl.q": hl_q, "hl.snippets": "5",
        }
        resp = ads_get("/search/query", params)
        response = resp.get("response", {})
        docs = response.get("docs", [])
        highlighting = resp.get("highlighting", {})
        yield docs, highlighting
        num_found = response.get("numFound", 0)
        start += len(docs)
        if not docs or start >= num_found:
            break
        time.sleep(SLEEP_SEC)


def citing_bibcodes(key_bibcode):
    """All bibcodes citing `key_bibcode`, via ADS's own citation graph (correctly folds
    in citations to the arXiv preprint of a since-published citing paper, unlike matching
    against raw `reference` lists by hand)."""
    out, start = set(), 0
    while start < 2000:
        resp = ads_get("/search/query", {
            "q": f"citations(bibcode:{key_bibcode})", "fl": "bibcode",
            "rows": 200, "start": start, "sort": "date asc, bibcode asc",
        })
        response = resp.get("response", {})
        docs = response.get("docs", [])
        out.update(d["bibcode"] for d in docs)
        start += len(docs)
        if not docs or start >= response.get("numFound", 0):
            break
        time.sleep(SLEEP_SEC)
    return out


def dedupe(docs, hl):
    """Merge preprint + published records of the same paper. Same logic as the notebook."""
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
    Score 1-9 from ADS full-text highlight snippets. Identical rule set to the notebook.
    Returns {"score", "reason", "snippets"} -- not just the number -- so a borderline or
    surprising score can actually be read and checked (see sync_ads_library.py's review
    tables), not just trusted.
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
        if has_full:                            sc = 9
        elif has_dja and ctx >= 2:               sc = 8
        elif has_dja and ctx == 1:               sc = 6
        elif has_dja and s["field"] == "body":   sc = 5
        elif has_dja:                            sc = 4
        else:                                    sc = 3
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


def load_manual_flags():
    """bibcode -> 'clean' | 'contaminants', from data/manual_flags.csv (see README)."""
    over = {}
    if FLAGS_CSV.exists():
        with open(FLAGS_CSV, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                over[row["bibcode"]] = row["to_table"]
    return over


def load_known_bibcodes():
    """{bibcode: title} confirmed clean as of the last run -- see resolve_missing."""
    known = {}
    if KNOWN_CSV.exists():
        with open(KNOWN_CSV, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                known[row["bibcode"]] = row["title"]
    return known


def save_known_bibcodes(bib_to_title):
    KNOWN_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(KNOWN_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["bibcode", "title"])
        for b in sorted(bib_to_title):
            w.writerow([b, bib_to_title[b]])


def _norm_title(t):
    return re.sub(r"\W+", " ", html.unescape(t or "")).strip().lower()


def resolve_missing(old_bib, old_title):
    """
    A bibcode confirmed clean in a past run but absent from today's fresh full-text
    search almost always means the paper just got journal-published: ADS retires the old
    arXiv bibcode and assigns a new one, but the new record's full text isn't reindexed
    for search right away (can take a few days) -- so for a while the paper is
    unfindable by the full-text query under either bibcode. ADS *does* populate the new
    record's title immediately, though (faster than it cross-links the old bibcode into
    `identifier`, which is not reliable enough yet to use here), so look it up that way
    instead and return the doc under its current, correct bibcode. Returns None if the
    title doesn't turn up an exact match -- a real removal, not a publication transition.
    """
    if len(old_title.strip()) < 15:
        return None
    # strip anything that isn't a word or space (LaTeX math like "$2.7<z<7$" is common in
    # astro titles and breaks Solr's query parser with a raw 400) -- safe to do since the
    # match below is on normalised text too, not the query string itself.
    query_title = re.sub(r"[^\w\s]", " ", html.unescape(old_title)).strip()
    if not query_title:
        return None
    resp = ads_get("/search/query", {"q": f'title:"{query_title}"', "fl": FIELDS, "rows": 5})
    docs = resp.get("response", {}).get("docs", [])
    matches = [d for d in docs if _norm_title((d.get("title") or [""])[0]) == _norm_title(old_title)]
    if not matches:
        return None
    # prefer a real article/eprint record over a conference abstract, then by citations
    matches.sort(key=lambda d: (d.get("doctype") == "abstract", -(d.get("citation_count") or 0)))
    return matches[0]


def n_authors(doc):
    return len(doc.get("author") or [])


def first_author(doc):
    au = doc.get("author") or []
    return au[0] if au else None


def dawn_affiliated(doc, identities=None):
    """
    True if the affiliation text says "Cosmic Dawn", OR any author resolves (via
    `identities`, from resolve_authors -- ORCID first, so this also catches records
    where this particular paper's ORCID field is blank but another of that person's
    papers carries it) to a name in DAWN_TEAM_ORCIDS.
    """
    if identities:
        for i in range(len(doc.get("author") or [])):
            key = identities.get((doc["bibcode"], i))
            if key and key[0] == "orcid" and key[1] in DAWN_TEAM_ORCIDS:
                return True
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
         almost anyone) still collide here. Rare in practice; spot-check
         scripts/sync_ads_library.py's output if in doubt.

    Returns (identities, orcid_coverage): identities is {(bibcode, position): key};
    orcid_coverage is the fraction of author-occurrences carrying some ORCID.
    """
    def orcid_of(doc, i):
        for field in ("orcid_pub", "orcid_user", "orcid_other"):
            vals = doc.get(field) or []
            if i < len(vals) and vals[i] and vals[i] != "-":
                return vals[i]
        return None

    occurrences = []   # (bibcode, position, name, orcid)
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


def classify_products(clean, scored):
    """
    Classify each confirmed paper by which DJA product(s) it uses -- photometry (imaging
    catalogs), spectroscopy (NIRSpec), both, or unclassified. Citation-confirmed first
    (see KEY_PAPERS), falling back to the weighted keyword pass (product_score) for
    whoever cites none of the four -- e.g. links the DJA website instead of a methods
    paper. Spot-checked in dja_auto_discovery.ipynb section 8 before this was ported
    here. Returns {bibcode: "photometry" | "spectroscopy" | "both" | "unclassified"}.
    """
    dja_bibcodes = {d["bibcode"] for d in clean}
    cite_photometry, cite_spectroscopy = set(), set()
    for bib in KEY_PAPERS["photometry"]:
        cite_photometry |= citing_bibcodes(bib) & dja_bibcodes
    for bib in KEY_PAPERS["spectroscopy"]:
        cite_spectroscopy |= citing_bibcodes(bib) & dja_bibcodes

    # keyword fallback, only for whoever cites none of the four key papers -- whichever
    # side scores higher wins; a tied nonzero score is genuine dual evidence ("both")
    neither = dja_bibcodes - cite_photometry - cite_spectroscopy
    kw_photometry, kw_spectroscopy = set(), set()
    for bib in neither:
        text = " ".join(s["text"] for s in scored.get(bib, {}).get("snippets", []) if s.get("hit"))
        photom, spec = product_score(text)
        if photom > spec:
            kw_photometry.add(bib)
        elif spec > photom:
            kw_spectroscopy.add(bib)
        elif photom > 0:
            kw_photometry.add(bib)
            kw_spectroscopy.add(bib)

    category = {}
    for bib in dja_bibcodes:
        p = bib in cite_photometry or bib in kw_photometry
        s = bib in cite_spectroscopy or bib in kw_spectroscopy
        category[bib] = ("both" if p and s else
                          "photometry" if p else
                          "spectroscopy" if s else
                          "unclassified")
    print(f"  data products -- citation-confirmed: {len(cite_photometry)} photometry, "
          f"{len(cite_spectroscopy)} spectroscopy; keyword-inferred on top: "
          f"{len(kw_photometry)} photometry, {len(kw_spectroscopy)} spectroscopy; "
          f"{len(neither - kw_photometry - kw_spectroscopy)} unclassified")
    return category


def discover():
    """
    Run the live ADS search + scoring pipeline. Returns (clean, contaminants, scored):
    clean/contaminants are lists of ADS doc dicts; scored is {bibcode: {"score",
    "reason", "snippets"}} for every candidate, clean or not, straight from score_paper
    -- i.e. the automatic verdict, before any manual_flags.csv override is applied.
    Shared by this script's monthly metrics refresh and sync_ads_library.py's slower,
    human-reviewed library sync, so the two never drift apart on what counts as
    "confirmed."
    """
    if not ADS_TOKEN:
        sys.exit("ADS_TOKEN environment variable is not set -- see README.md")

    print("Querying ADS for DJA-mentioning papers...")
    docs_map, hl_map = {}, {}
    for docs, highlighting in paginated_search(MAIN_Q, DJA_FQ, FIELDS, HL_Q):
        for doc in docs:
            bib = doc["bibcode"]
            docs_map[bib] = doc
            sid = str(doc.get("id"))
            if sid in highlighting:
                hl_map[bib] = highlighting[sid]
        print(f"  fetched {len(docs_map)} candidates so far...", end="\r")
    print(f"\nFetched {len(docs_map)} raw candidates")

    docs, hl_map = dedupe(list(docs_map.values()), hl_map)
    print(f"{len(docs)} candidates after merging preprint/published duplicates")

    flags = load_manual_flags()
    clean, contaminants, scored = [], [], {}
    for doc in docs:
        bib = doc["bibcode"]
        sc = score_paper(bib, hl_map)
        scored[bib] = sc
        auto = "clean" if sc["score"] >= MIN_SCORE else "contaminants"
        group = flags.get(bib, auto)
        (clean if group == "clean" else contaminants).append(doc)

    # Safety net: a paper confirmed clean last run that isn't anywhere in today's fresh
    # candidate pool at all (not even in contaminants) is almost always mid arXiv-to-
    # published transition (see resolve_missing), not a real disappearance -- recover it
    # under its current bibcode instead of silently dropping it for the few days ADS
    # takes to reindex the published version's full text. Skipped for anything a manual
    # flag now explicitly marks as a contaminant -- that's a deliberate removal.
    known = load_known_bibcodes()
    found = {d["bibcode"] for d in clean} | {d["bibcode"] for d in contaminants}
    # the next ledger: every currently-clean bibcode, plus (below) anything still
    # unresolved that should get another attempt next run
    ledger = {d["bibcode"]: (d.get("title") or [""])[0] for d in clean}
    recovered = 0
    for bib, title in known.items():
        if bib in found or flags.get(bib) == "contaminants":
            continue   # already accounted for, or a deliberate manual removal -- drop it
        doc = resolve_missing(bib, title)
        if doc and doc["bibcode"] not in found:
            clean.append(doc)
            found.add(doc["bibcode"])
            scored[doc["bibcode"]] = {
                "score": None, "snippets": [],
                "reason": f"Carried forward from a past run (was {bib}) -- confirmed before, "
                          "temporarily missing from ADS full-text search",
            }
            ledger[doc["bibcode"]] = (doc.get("title") or [""])[0]
            recovered += 1
        else:
            ledger[bib] = title   # couldn't resolve it yet -- keep it, try again next run

    save_known_bibcodes(ledger)
    print(f"{len(clean)} confirmed DJA papers "
          f"({sum(1 for v in flags.values() if v == 'clean')} manually corrected in"
          f"{f', {recovered} carried forward from a past run' if recovered else ''})")
    return clean, contaminants, scored


def main():
    clean, contaminants, scored = discover()

    # Merge author name variants ("Smith, J." / "Smith, John") into one identity where
    # possible -- see resolve_authors's docstring for exactly how and its limits. Computed
    # before the dawn_affiliated loop below, which also uses it (DAWN_TEAM_ORCIDS).
    identities, orcid_coverage = resolve_authors(clean)
    unique_people = len(set(identities.values()))
    unique_first_authors = len({identities[(d["bibcode"], 0)] for d in clean if d.get("author")})

    print("Classifying data products (photometry vs. spectroscopy)...")
    product_category = classify_products(clean, scored)

    by_year = Counter()
    citations_by_year = Counter()
    products_by_year = Counter()   # (year, category) -> count
    dawn_count = 0
    for doc in clean:
        y = doc.get("year")
        if y and str(y).isdigit():
            by_year[int(y)] += 1
            citations_by_year[int(y)] += doc.get("citation_count", 0) or 0
            products_by_year[(int(y), product_category[doc["bibcode"]])] += 1
        if dawn_affiliated(doc, identities):
            dawn_count += 1

    median_authors = sorted(n_authors(d) for d in clean)
    median_authors = median_authors[len(median_authors) // 2] if median_authors else 0
    external_pct = round(100 * (1 - dawn_count / len(clean)), 1) if clean else 0.0

    years_sorted = sorted(by_year)
    cum = 0
    citations_cumulative = []
    for y in years_sorted:
        cum += citations_by_year[y]
        citations_cumulative.append({"year": y, "cumulative": cum, "partial": y == CURRENT_YEAR})

    # Exclude papers already identified as false positives from the live search link below --
    # otherwise every rejected candidate reappears each time someone clicks it (see README).
    contaminant_bibcodes = sorted(d["bibcode"] for d in contaminants)
    exclude = " ".join(f'NOT bibcode:"{b}"' for b in contaminant_bibcodes)
    search_q = f'full:("Dawn JWST Archive" OR "DJA") year:[2023 TO *] collection:astronomy {exclude}'.strip()

    metrics = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_papers": len(clean),
        "by_year": [
            {"year": y, "count": by_year[y], "partial": y == CURRENT_YEAR}
            for y in years_sorted
        ],
        "citations_by_year": citations_cumulative,
        "products_by_year": [
            {
                "year": y,
                "photometry":   products_by_year[(y, "photometry")],
                "spectroscopy": products_by_year[(y, "spectroscopy")],
                "both":         products_by_year[(y, "both")],
                "unclassified": products_by_year[(y, "unclassified")],
                "partial": y == CURRENT_YEAR,
            }
            for y in years_sorted
        ],
        "community": {
            "unique_authors": unique_people,
            "orcid_coverage_pct": round(100 * orcid_coverage, 1),
            "unique_first_authors": unique_first_authors,
            "median_authors_per_paper": median_authors,
            "external_adoption_pct": external_pct,
        },
        "notes": {
            "unique_authors": "Merged via ORCID where ADS has one (see orcid_coverage_pct); name "
                               "variants without an ORCID ('Smith, J.' vs 'Smith, John') are merged "
                               "by surname plus matching every spelled-out given/middle-name token, "
                               "which can still occasionally over- or under-merge two people who "
                               "share a surname and only ever appear with bare initials.",
            "external_adoption_pct": "Share of papers with no author affiliation string mentioning "
                                      "the Cosmic Dawn Center. An upper bound: an affiliate whose "
                                      "entry omits the center's name would be miscounted as external.",
            "products_by_year": "Photometry = cites Valentino+23 or a photometry-specific term in "
                                 "the paper's own DJA-context snippet; spectroscopy = cites Heintz+25, "
                                 "de Graaff+25 or Valentino+25, or a spectroscopy-specific term. "
                                 "Citation match is high-confidence; the keyword fallback (for whoever "
                                 "cites none of the four, e.g. links the DJA website instead) is not -- "
                                 "'unclassified' means neither signal was found, not that no product "
                                 "was used. All three spectroscopy papers are from 2025, so pre-2025 "
                                 "spectroscopy use is under-counted by construction.",
        },
        # Live, always-current ADS search, false positives already identified excluded.
        # Secondary link -- the button on the page points at ads_library_url instead.
        "ads_search_url": "https://ui.adsabs.harvard.edu/search/q="
            + urllib.parse.quote(search_q) + "&sort=date%20desc",
        # A curated ADS Library: a fixed, human-reviewed set of bibcodes (see
        # scripts/sync_ads_library.py), not a live query -- no false positives, ever,
        # but only as current as the last manual sync (roughly every 6 months).
        "ads_library_url": ("https://ui.adsabs.harvard.edu/public-libraries/"
                             + LIBRARY_ID_FILE.read_text().strip()) if LIBRARY_ID_FILE.exists() else None,
    }

    DATA_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_JSON, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)
    print(f"Wrote {DATA_JSON}")


if __name__ == "__main__":
    main()
