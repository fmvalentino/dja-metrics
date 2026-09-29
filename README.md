# DJA papers & community

This page tracks how many papers use the [Dawn JWST Archive (DJA)](https://dawn-cph.github.io/dja/), how that number is growing, and who is behind the work.

## What it shows

**Confirmed DJA papers.** A running count with two bar charts: new papers by
publication year, and DJA papers' citations, cumulative by publication year.
A paper is counted once its use of DJA data is confirmed by full-text search
and, where the automatic score is ambiguous, a manual read of the matched
passage. Mentioning JWST or citing the archive in passing is not enough to
enter the list. Occasional human check includes recently submitted papers
appearing on arXiv, but not refereeed yet, to reduce the lag of the automatic
updates of ADS/NASA

**Community reach.** Four numbers:

- *Unique author names* across all confirmed papers — this is a name-string
  match, and thus a ceiling rather than an exact headcount: the same person spelled two
  ways in ADS counts twice.
- *Unique first authors* — how many different people have led a DJA
  paper, as opposed to one group publishing repeatedly.
- *Share with no Cosmic Dawn Center co-author* — papers written
  entirely outside the group that builds and runs the archive. This is an
  upper bound on external use: an author from the Center whose ADS
  affiliation string omits its name is miscounted as external.
- *Median authors per paper*.

**A curated library, not a live search.** The button opens
[an ADS Library](https://ui.adsabs.harvard.edu/public-libraries/oRy6GKOGTZuLWFsERj_QHg)
holding exactly the confirmed papers — sortable, exportable, with ADS's own
citation metrics. It's a fixed list, synced by hand roughly every 6 months
(`scripts/sync_ads_library.py`), not a live query, so nothing but confirmed
DJA papers ever shows up there. The methodology section also links a second,
always-current search on ADS with known false positives excluded by bibcode —
useful for checking what's arrived since the last library sync, at the cost
of occasionally showing a paper that hasn't been reviewed yet.

## Where the numbers come from

Full-text search on ADS for "Dawn JWST Archive" or the standalone acronym
"DJA", papers from 2023 on, published or on arXiv. Each match is scored from
its highlighted context: a verbatim "Dawn JWST Archive" scores highest; "DJA"
next to archive-related terms (pipeline, mosaic, NIRSpec, data release, and
so on) scores above a bare, context-free "DJA"; a bare "DJA" that reads as a
person's initials in an acknowledgement is excluded. Preprint and published
records of the same paper are merged, keeping the published version. Papers
the automatic score gets wrong are corrected by hand and recorded in
`data/manual_flags.csv`.

## How it stays current

Two different cadences, on purpose:

- **The numbers on the page** (`data/metrics.json`) refresh monthly,
  automatically — `scripts/update_metrics.py` runs on the 1st
  (`.github/workflows/update.yml`) and commits whatever it finds. A brand-new
  false positive can sit in the count for up to a few weeks, until the next
  manual review; historically that's been rare (45 of 478 candidates ever,
  ~9%, mostly caught the first time).
- **The curated ADS Library** (the "Explore it yourself" button) only updates
  when a person runs `scripts/sync_ads_library.py` and reviews the diff —
  roughly every 6 months. It never changes automatically, so it can never show
  an unreviewed paper.

```
ADS_TOKEN=... python scripts/sync_ads_library.py            # dry run: prints the diff only
ADS_TOKEN=... python scripts/sync_ads_library.py --apply    # applies it, after you've read the diff
```

## Reproduce it yourself

`notebook/dja_discovery_example.ipynb` runs the same search and scoring end to
end and reproduces every number on this page, cell by cell, with the query,
the scoring rule, and each intermediate table visible as you go. It needs a
free ADS API token (`export ADS_TOKEN=...`, see the notebook's first cell) and
nothing else private. The wider citation-impact analysis (DJA papers against a
matched sample of non-DJA JWST papers, with a bootstrap confidence interval)
is a separate, larger study and is not part of this page or this notebook.

## Files

```
index.html                   the page
assets/style.css, chart.js   styling and the two bar charts, no dependencies
data/metrics.json            the numbers currently on the page
data/manual_flags.csv        manually corrected papers (see "Where the numbers come from")
data/ads_library_id.txt      id of the curated ADS Library the "Explore" button links to
scripts/update_metrics.py    rebuilds data/metrics.json from ADS -- monthly, automatic
scripts/sync_ads_library.py  syncs the curated library -- manual, ~every 6 months
.github/workflows/update.yml monthly rebuild of data/metrics.json only
notebook/                    a runnable example that reproduces the numbers above
```