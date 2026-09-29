# DJA papers & community

Live at **[fmvalentino.github.io/dja-metrics](https://fmvalentino.github.io/dja-metrics/)**.

This page tracks how many papers use the Dawn JWST Archive (DJA), how that
number is growing, and who is behind the work. It is not a citation-advantage
study — that analysis (DJA papers vs. a matched sample of non-DJA JWST papers,
bootstrap confidence intervals) lives in `dja_auto_discovery.ipynb` and is
deliberately left out of this page.

## What it shows

**Confirmed DJA papers.** A running count, currently 433, with a bar chart of
new papers by publication year (2023: 2, 2024: 33, 2025: 148, 2026: 250 so
far). A paper is counted once its use of DJA data is confirmed by full-text
search and, where the automatic score is ambiguous, a manual read of the
matched passage. Mentioning JWST or citing the archive in passing is not
enough.

**Community reach.** Four numbers, deliberately simpler than a citation
count:

- *Unique author names* across all confirmed papers (2,481) — a name-string
  match, so it is a floor, not an exact headcount: the same person spelled two
  ways in ADS counts twice.
- *Unique first authors* (328) — how many different people have led a DJA
  paper, as opposed to one group publishing repeatedly.
- *Share with no Cosmic Dawn Center co-author* (70.9%) — papers written
  entirely outside the group that builds and runs the archive. This is an
  upper bound on external use: an author from the Center whose ADS
  affiliation string omits its name is miscounted as external.
- *Median authors per paper* (16).

**A live search link.** One button opens the same full-text query this page
is built from, running directly on NASA ADS. It will show more papers than
the count above, because it has not been through the scoring and manual
review step.

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

`scripts/update_metrics.py` reruns this search once a month
(`.github/workflows/update.yml`, 1st of the month) and commits the refreshed
`data/metrics.json`, which `index.html` reads at load time. No server, no
database, no build step.

## Files

```
index.html                   the page
assets/style.css, chart.js   styling and the bar chart, no dependencies
data/metrics.json            the numbers currently on the page
data/manual_flags.csv        manually corrected papers (see "Where the numbers come from")
scripts/update_metrics.py    rebuilds data/metrics.json from ADS
.github/workflows/update.yml monthly rebuild
```

`assets/style.css` reuses the fonts and colors from
[dawn-cph.github.io/dja](https://dawn-cph.github.io/dja/) (Roboto Slab, Open
Sans, the same gold accent), so the page can be folded into that site later
with little restyling.

## Maintenance

Setup (the `ADS_TOKEN` repository secret, GitHub Pages) is already done. The
one recurring task: when a paper is manually reclassified in the notebook's
own review step, copy the update across so this page picks it up too —

```
cp ../ads_dja_papers/flagged_contaminants.csv data/manual_flags.csv
git add data/manual_flags.csv && git commit -m "Sync manual flags" && git push
```

Skipping this does not break anything; a newly flagged paper is just scored
automatically here until the next sync.
