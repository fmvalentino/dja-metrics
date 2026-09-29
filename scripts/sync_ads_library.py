#!/usr/bin/env python3
"""
Sync the curated ADS Library (the "Explore it yourself" button's target) to the
current confirmed-papers list. Unlike update_metrics.py, this is NOT run by the
monthly GitHub Action -- run it by hand, roughly every 6 months, so a person
looks at new candidates before anything public changes.

Usage:
    ADS_TOKEN=... python scripts/sync_ads_library.py            # dry run
    ADS_TOKEN=... python scripts/sync_ads_library.py --apply    # applies it

What a dry run does:
  1. Runs discover() -- the same live search + scoring update_metrics.py uses.
  2. Compares today's candidates against data/known_candidates.csv, the record of
     every bibcode already seen in a previous sync. Anything not in there is new.
  3. If there ARE new candidates, writes two HTML checkbox review tables (new
     confirmed / new contaminants) -- same pattern as the notebook's clean/
     contaminants tables. Nothing already reviewed before shows up again.
  4. Prints the library add/remove diff (computed from ALL current confirmed
     papers, not just new ones -- this part doesn't change).

--apply additionally:
  - Prompts for confirmation if there are new candidates you haven't necessarily
    looked at yet (no way to verify a human actually read the HTML files, only
    that they exist and were regenerated this run).
  - Applies the add/remove to the ADS Library.
  - Marks today's full candidate set as "known" in data/known_candidates.csv, so
    next time only genuinely new arrivals are shown again.
"""
import csv
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from update_metrics import ADS_TOKEN, LIBRARY_ID_FILE, MIN_SCORE, ROOT, discover  # noqa: E402
from review_html import save_review_html  # noqa: E402

ADS_BIBLIB = "https://api.adsabs.harvard.edu/v1/biblib"
LIBRARY_NAME = "Dawn JWST Archive papers (curated)"
LIBRARY_DESCRIPTION = (
    "Confirmed papers using the Dawn JWST Archive (DJA). Curated by full-text search "
    "plus manual review; updated roughly every 6 months. "
    "See https://fmvalentino.github.io/dja-metrics/ for the live paper count "
    "and https://github.com/fmvalentino/dja-metrics for the method."
)

KNOWN_CSV = ROOT / "data" / "known_candidates.csv"
REVIEW_NEW_CLEAN_HTML = ROOT / "scripts" / "review_new_clean.html"
REVIEW_NEW_CONTAM_HTML = ROOT / "scripts" / "review_new_contaminants.html"


def h():
    return {"Authorization": f"Bearer {ADS_TOKEN}", "Content-Type": "application/json"}


def get_library(library_id):
    # rows defaults to 20 server-side -- without it, "documents" is silently truncated
    # and everything past the first page looks like it needs re-adding.
    r = requests.get(f"{ADS_BIBLIB}/libraries/{library_id}", headers=h(),
                      params={"rows": 5000}, timeout=30)
    r.raise_for_status()
    return r.json()


def create_library(bibcodes):
    r = requests.post(f"{ADS_BIBLIB}/libraries", headers=h(), json={
        "name": LIBRARY_NAME, "description": LIBRARY_DESCRIPTION,
        "public": True, "bibcode": sorted(bibcodes),
    }, timeout=60)
    r.raise_for_status()
    return r.json()["id"]


def update_documents(library_id, bibcodes, action):
    if not bibcodes:
        return
    r = requests.post(f"{ADS_BIBLIB}/documents/{library_id}", headers=h(),
                       json={"bibcode": sorted(bibcodes), "action": action}, timeout=60)
    r.raise_for_status()


def load_known():
    """bibcode -> 'clean' | 'contaminants', from data/known_candidates.csv."""
    known = {}
    if KNOWN_CSV.exists():
        with open(KNOWN_CSV, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                known[row["bibcode"]] = row["classification"]
    return known


def write_known(clean, contaminants):
    with open(KNOWN_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["bibcode", "classification"])
        for d in sorted(clean, key=lambda d: d["bibcode"]):
            w.writerow([d["bibcode"], "clean"])
        for d in sorted(contaminants, key=lambda d: d["bibcode"]):
            w.writerow([d["bibcode"], "contaminants"])


def rows_for_html(docs, scored, expected_clean):
    """
    expected_clean: True for the new-confirmed table, False for the new-rejected
    table. A doc whose raw automatic score disagrees with which table it's in got
    there via a manual_flags.csv override, not the scorer -- say so plainly, or a
    "confirmed" row showing a raw score of 1 reads as a bug in the review table
    itself rather than what it is.
    """
    import html as html_mod
    rows = []
    for d in docs:
        sc = scored.get(d["bibcode"], {"score": "?", "reason": "?", "snippets": []})
        auto_clean = isinstance(sc["score"], int) and sc["score"] >= MIN_SCORE
        reason = sc["reason"]
        if auto_clean != expected_clean:
            auto_side = "clean" if auto_clean else "contaminants"
            reason = f"MANUAL override (automatic score alone says {auto_side}: {reason})"
        rows.append({
            "bibcode": d["bibcode"],
            "ads_url": f"https://ui.adsabs.harvard.edu/abs/{d['bibcode']}/abstract",
            "title": html_mod.unescape((d.get("title") or ["Untitled"])[0]),
            "year": d.get("year", ""),
            "score": sc["score"],
            "reason": reason,
            "snippets": sc["snippets"],
        })
    return rows


def main():
    apply = "--apply" in sys.argv
    if not ADS_TOKEN:
        sys.exit("ADS_TOKEN environment variable is not set -- see README.md")

    clean, contaminants, scored = discover()
    known = load_known()

    new_clean = [d for d in clean if d["bibcode"] not in known]
    new_contam = [d for d in contaminants if d["bibcode"] not in known]
    n_new = len(new_clean) + len(new_contam)

    if n_new:
        print(f"\n{n_new} candidates never seen in a previous sync "
              f"({len(new_clean)} auto-confirmed, {len(new_contam)} auto-rejected):")
        if new_clean:
            save_review_html(rows_for_html(new_clean, scored, expected_clean=True), REVIEW_NEW_CLEAN_HTML,
                              "New confirmed DJA papers", "new_clean", "contaminants",
                              "Tick papers that do <b>not</b> genuinely use DJA.")
            print(f"  -> {REVIEW_NEW_CLEAN_HTML}")
        if new_contam:
            save_review_html(rows_for_html(new_contam, scored, expected_clean=False), REVIEW_NEW_CONTAM_HTML,
                              "New rejected candidates", "new_contaminants", "clean",
                              "Tick papers that <b>do</b> use DJA.")
            print(f"  -> {REVIEW_NEW_CONTAM_HTML}")
        print("  Open these, tick anything misclassified, download the flagged CSV(s), and "
              "merge them into data/manual_flags.csv before you trust the diff below.")
    else:
        print("\nNo candidates since the last sync that haven't already been reviewed.")

    if not LIBRARY_ID_FILE.exists():
        if not apply:
            print(f"\nNo library yet. Dry run: would create one with all {len(clean)} "
                  f"confirmed papers. Re-run with --apply to create it.")
            return
        library_id = create_library(d["bibcode"] for d in clean)
        LIBRARY_ID_FILE.write_text(library_id + "\n")
        write_known(clean, contaminants)
        print(f"Created library {library_id} with {len(clean)} papers.")
        print(f"Public URL: https://ui.adsabs.harvard.edu/public-libraries/{library_id}")
        return

    library_id = LIBRARY_ID_FILE.read_text().strip()
    existing = set(get_library(library_id)["documents"])
    new_bibcodes = {d["bibcode"] for d in clean}

    to_add = new_bibcodes - existing
    to_remove = existing - new_bibcodes

    print(f"\nLibrary {library_id}: {len(existing)} papers currently, {len(new_bibcodes)} confirmed now.")
    print(f"+ {len(to_add)} to add:")
    for b in sorted(to_add):
        doc = next(d for d in clean if d["bibcode"] == b)
        title = (doc.get("title") or ["?"])[0]
        print(f"    {b}  {title[:80]}")
    print(f"- {len(to_remove)} to remove (no longer confirmed):")
    for b in sorted(to_remove):
        print(f"    {b}  https://ui.adsabs.harvard.edu/abs/{b}/abstract")

    if not to_add and not to_remove:
        print("\nLibrary already matches the current confirmed list.")
        if apply:
            write_known(clean, contaminants)
        return

    if not apply:
        print("\nDry run -- nothing changed. Re-run with --apply once this looks right.")
        return

    if n_new:
        print(f"\n{n_new} candidates were new this run (see the HTML files above). "
              "Applying now will treat any you have NOT corrected in data/manual_flags.csv "
              "as correctly auto-classified.")
        if input("Continue? [y/N] ").strip().lower() != "y":
            print("Aborted -- nothing changed.")
            return

    update_documents(library_id, to_add, "add")
    update_documents(library_id, to_remove, "remove")
    write_known(clean, contaminants)
    print(f"\nApplied. Public URL: https://ui.adsabs.harvard.edu/public-libraries/{library_id}")


if __name__ == "__main__":
    main()
