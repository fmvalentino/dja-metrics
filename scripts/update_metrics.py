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


def ads_get(path, params):
    r = requests.get(ADS_BASE + path, headers={"Authorization": f"Bearer {ADS_TOKEN}"},
                      params=params, timeout=30)
    r.raise_for_status()
    return r.json()


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


def n_authors(doc):
    return len(doc.get("author") or [])


def first_author(doc):
    au = doc.get("author") or []
    return au[0] if au else None


def dawn_affiliated(doc):
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
    print(f"{len(clean)} confirmed DJA papers "
          f"({sum(1 for v in flags.values() if v == 'clean')} manually corrected in)")
    return clean, contaminants, scored


def main():
    clean, contaminants, _scored = discover()

    by_year = Counter()
    citations_by_year = Counter()
    dawn_count = 0
    for doc in clean:
        y = doc.get("year")
        if y and str(y).isdigit():
            by_year[int(y)] += 1
            citations_by_year[int(y)] += doc.get("citation_count", 0) or 0
        if dawn_affiliated(doc):
            dawn_count += 1

    # Merge author name variants ("Smith, J." / "Smith, John") into one identity where
    # possible -- see resolve_authors's docstring for exactly how and its limits.
    identities, orcid_coverage = resolve_authors(clean)
    unique_people = len(set(identities.values()))
    unique_first_authors = len({identities[(d["bibcode"], 0)] for d in clean if d.get("author")})

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
