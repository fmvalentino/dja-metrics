#!/usr/bin/env python3
"""
Sync the curated ADS Library (the "Explore it yourself" button's target) to the
current confirmed-papers list. Unlike update_metrics.py, this is NOT run by the
monthly GitHub Action -- run it by hand, roughly every 6 months, so a person
looks at the diff before anything public changes.

Usage:
    ADS_TOKEN=... python scripts/sync_ads_library.py            # dry run: prints the diff only
    ADS_TOKEN=... python scripts/sync_ads_library.py --apply    # applies it after you've reviewed

The library holds exactly the "clean" bibcodes from discover() (same scoring +
manual-flags logic update_metrics.py uses), nothing more. Its id lives in
data/ads_library_id.txt -- delete that file and re-run to create a fresh library
instead of updating the existing one.
"""
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from update_metrics import ADS_TOKEN, LIBRARY_ID_FILE, discover  # noqa: E402

ADS_BIBLIB = "https://api.adsabs.harvard.edu/v1/biblib"
LIBRARY_NAME = "Dawn JWST Archive papers (curated)"
LIBRARY_DESCRIPTION = (
    "Confirmed papers using the Dawn JWST Archive (DJA). Curated by full-text search "
    "plus manual review; updated roughly every 6 months. "
    "See https://fmvalentino.github.io/dja-metrics/ for the live paper count "
    "and https://github.com/fmvalentino/dja-metrics for the method."
)


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


def main():
    apply = "--apply" in sys.argv
    if not ADS_TOKEN:
        sys.exit("ADS_TOKEN environment variable is not set -- see README.md")

    clean, _ = discover()
    current = {d["bibcode"]: d for d in clean}
    new_bibcodes = set(current)

    if not LIBRARY_ID_FILE.exists():
        if not apply:
            print(f"No library yet ({LIBRARY_ID_FILE} missing). Dry run: would create one "
                  f"with all {len(new_bibcodes)} confirmed papers. Re-run with --apply to create it.")
            return
        library_id = create_library(new_bibcodes)
        LIBRARY_ID_FILE.write_text(library_id + "\n")
        print(f"Created library {library_id} with {len(new_bibcodes)} papers.")
        print(f"Public URL: https://ui.adsabs.harvard.edu/public-libraries/{library_id}")
        return

    library_id = LIBRARY_ID_FILE.read_text().strip()
    existing = set(get_library(library_id)["documents"])

    to_add = new_bibcodes - existing
    to_remove = existing - new_bibcodes

    print(f"Library {library_id}: {len(existing)} papers currently, {len(new_bibcodes)} confirmed now.")
    print(f"\n+ {len(to_add)} to add:")
    for b in sorted(to_add):
        title = (current[b].get("title") or ["?"])[0]
        print(f"    {b}  {title[:80]}")
    print(f"\n- {len(to_remove)} to remove (no longer confirmed -- reclassified, or a manual flag changed):")
    for b in sorted(to_remove):
        print(f"    {b}  https://ui.adsabs.harvard.edu/abs/{b}/abstract")

    if not to_add and not to_remove:
        print("\nNothing to do -- the library already matches the current confirmed list.")
        return

    if not apply:
        print("\nDry run -- nothing changed. Re-run with --apply once this diff looks right.")
        return

    update_documents(library_id, to_add, "add")
    update_documents(library_id, to_remove, "remove")
    print(f"\nApplied. Public URL: https://ui.adsabs.harvard.edu/public-libraries/{library_id}")


if __name__ == "__main__":
    main()
