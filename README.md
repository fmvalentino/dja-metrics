# DJA papers & community

This page tracks how many papers use the [Dawn JWST Archive (DJA)](https://dawn-cph.github.io/dja/), how that number is growing, and who is behind the work.

## What it shows

**Confirmed DJA papers.** A running count with a bar chart of
new papers by publication year. A paper is counted once its use of DJA data is confirmed by full-text search and, where the automatic score is ambiguous, a manual read of the matched passage. Mentioning JWST or citing the archive in passing is not
enough to enter the list. Occasional human check includes recently submitted papers appearing on arXiv, but not refereeed yet, to reduce the lag of the automatic updates of ADS/NASA

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
assets/style.css, chart.js   styling and the bar chart, no dependencies
data/metrics.json            the numbers currently on the page
data/manual_flags.csv        manually corrected papers (see "Where the numbers come from")
scripts/update_metrics.py    rebuilds data/metrics.json from ADS
.github/workflows/update.yml monthly rebuild
notebook/                    a runnable example that reproduces the numbers above
```