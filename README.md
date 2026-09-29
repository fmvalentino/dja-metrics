# DJA metrics page

A small static page showing how many papers use the Dawn JWST Archive, how fast
that's growing, and roughly who's behind them. It rebuilds itself once a month
from a live NASA ADS search — no server, no build step, no database.

**Live numbers today:** 433 confirmed papers, 2,481 unique author names, 328
unique first authors, 71% with no Cosmic Dawn Center co-author. These come
straight from `dja_auto_discovery.ipynb`'s last full run (2026-09-29); this
folder's own `data/metrics.json` was seeded from that same run.

## How it works

```
index.html          the page (fetches data/metrics.json at load time)
assets/style.css     styling, matched to dawn-cph.github.io/dja's fonts and colors
assets/chart.js      draws the bar chart, fills in the numbers, sets the ADS link
data/metrics.json    the numbers the page reads — rebuilt monthly, committed by the Action
data/manual_flags.csv  manual clean/contaminant corrections (see below)
scripts/update_metrics.py  rebuilds data/metrics.json from ADS
.github/workflows/update.yml  runs the script on the 1st of every month
```

`update_metrics.py` is a trimmed copy of the DJA-side discovery and scoring
logic in the private working notebook — same query, same scoring rules, same
duplicate-merging — but it only computes what this page shows (paper counts,
author counts). It does not run the comparison-sample search or the citation
bootstrap; those stay in the notebook.

## Setting this up on GitHub

1. Create the repo (or a subfolder of an existing one — adjust paths below if so) and push this
   folder's contents.
2. **Settings → Secrets and variables → Actions → New repository secret**, name it `ADS_TOKEN`,
   paste your ADS API token (ui.adsabs.harvard.edu/user/settings/token). The workflow reads it
   from there — it is never written into any file in this repo.
3. **Settings → Pages** → deploy from the branch this is pushed to (root, or `/docs` if you move
   these files there). GitHub gives you a `https://<user>.github.io/<repo>/` URL.
4. **Actions tab** → run the "Update DJA metrics" workflow once by hand
   (`workflow_dispatch`) to confirm it can write `data/metrics.json` with the
   `contents: write` permission already set in the workflow file.

## Before you push

- **`dja_auto_discovery.ipynb` still has the real ADS token hardcoded in cell 2.**
  Nothing in *this* folder contains it — `update_metrics.py` reads `ADS_TOKEN`
  from the environment — but if you ever push the notebook itself to a public
  repo, strip the token out first (swap it for an env-var read, same pattern
  as the script here).
- The methodology link at the bottom of `index.html` is a `#` placeholder —
  point it at wherever the notebook ends up living publicly, once it does.

## Keeping manual corrections in sync

`data/manual_flags.csv` is a snapshot of the notebook's `flagged_contaminants.csv`
(108 papers the automatic score misclassified, corrected by hand) at the time
this was built. If you flag more papers in the notebook's review workflow
later, copy the updated file over:

```
cp ../ads_dja_papers/flagged_contaminants.csv data/manual_flags.csv
git add data/manual_flags.csv && git commit -m "Sync manual flags" && git push
```

Without this, the monthly rebuild still runs fine — it just falls back to the
automatic score alone for any *newly* flagged paper until you sync.

## Merging into the main DJA website later

The color and type tokens in `assets/style.css` (`--accent`, `--ink`, `--body`,
`--border`, `--panel`, and the Roboto Slab / Open Sans pairing) are read
straight off `dawn-cph.github.io/dja`'s own stylesheet, so dropping this
content into that Jekyll site later should mostly mean: move `index.html`'s
`<body>` content into a page template, keep `assets/chart.js` and
`data/metrics.json`, and let the site's own `main.css` take over instead of
`assets/style.css`.
