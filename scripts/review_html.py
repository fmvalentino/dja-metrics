"""
Sortable HTML checkbox review table -- the same pattern dja_auto_discovery.ipynb
uses for its clean/contaminants tables, trimmed down and reused by
sync_ads_library.py for exactly one job: let a human look at NEW candidates
(never seen in a previous sync) before they reach the confirmed list.

Ticking a row and clicking "Download flagged CSV" produces a
flagged_<table_id>.csv with the same bibcode/from_table/to_table/ads_url
columns update_metrics.py's load_manual_flags() already reads -- drop it
straight into data/manual_flags.csv, no format translation needed.
"""
import html
import json
import re
from datetime import datetime

ADS_ABS = "https://ui.adsabs.harvard.edu/abs/{}/abstract"
DJA_RE  = re.compile(r"(?<![A-Za-z0-9])DJA(?![A-Za-z0-9])")

SORT_JS = (
    "document.querySelectorAll('th').forEach(function(th, i){"
    "th.style.cursor='pointer'; th.title='Click to sort';"
    "th.addEventListener('click', function(){"
    "var tb=th.closest('table').tBodies[0], rows=Array.from(tb.rows),"
    "asc=th.dataset.asc!=='1'; th.dataset.asc=asc?'1':'0';"
    "document.querySelectorAll('th').forEach(function(h){h.textContent=h.textContent.replace(/ [\\u25B2\\u25BC]$/,'');});"
    "th.textContent+=asc?' \\u25B2':' \\u25BC';"
    "function v(r){var c=r.cells[i].querySelector('input'); if(c) return c.checked?1:0;"
    "var t=r.cells[i].textContent.trim(), n=parseFloat(t); return isNaN(n)?t.toLowerCase():n;}"
    "rows.sort(function(a,b){var x=v(a), y=v(b);"
    "if(typeof x==='number' && typeof y==='number') return asc?x-y:y-x;"
    "if(typeof x==='number') return -1; if(typeof y==='number') return 1;"
    "return asc?x.localeCompare(y):y.localeCompare(x);});"
    "rows.forEach(function(r){tb.appendChild(r);});"
    "});});"
)

FLAG_JS = r'''
(function(){
  var boxes = document.querySelectorAll("input.flag"), cnt = document.getElementById("nflag");
  function refresh(){
    var n = 0;
    boxes.forEach(function(b){ if (b.checked) n++; b.closest("tr").classList.toggle("flagged", b.checked); });
    cnt.textContent = n;
  }
  boxes.forEach(function(b){ b.addEventListener("change", refresh); });
  document.getElementById("dl").addEventListener("click", function(){
    var lines = ["bibcode,from_table,to_table,ads_url"];
    boxes.forEach(function(b){
      if (b.checked) lines.push([b.dataset.b, CFG.id, CFG.to,
        "https://ui.adsabs.harvard.edu/abs/" + b.dataset.b + "/abstract"].join(","));
    });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([lines.join("\n") + "\n"], {type: "text/csv"}));
    a.download = "flagged_" + CFG.id + ".csv";
    document.body.appendChild(a); a.click(); a.remove();
  });
  refresh();
})();
'''

CSS = ("body{font-family:sans-serif;margin:1.5em}table{border-collapse:collapse;font-size:13px}"
       "th{background:#2b2b2b;color:#fff;text-align:left;position:sticky;top:0;z-index:1}"
       "th,td{padding:6px 10px;border-bottom:1px solid #ddd;vertical-align:top}"
       "tr:hover td{background:#f5f5f5}tr.flagged td{background:#fee2e2}"
       "mark{background:#fef08a}td:last-child{max-width:50em}"
       ".bar{margin:.6em 0 1em;padding:.6em .8em;background:#f1f5f9;border-radius:6px;font-size:13px}"
       ".bar button{margin-right:.5em;padding:.3em .8em}")


def _hl(text):
    t = html.escape(text)
    t = re.sub(r"(Dawn JWST Archive)", r"<mark><b>\1</b></mark>", t, flags=re.I)
    return DJA_RE.sub("<mark><b>DJA</b></mark>", t)


def save_review_html(rows, path, title, table_id, to_table, hint):
    """
    rows: list of {"bibcode","ads_url","title","year","score","reason","snippets"}
    (the last three straight from score_paper's return dict). Ticking a row and
    downloading marks it for `to_table` the next time data/manual_flags.csv is
    read.
    """
    trs = []
    for r in rows:
        snippets = r.get("snippets", [])
        # prefer genuine hits, but a low-score paper usually has none -- for those, fall
        # back to whatever snippet was found so the reason (e.g. "author initials") is
        # actually checkable, not just asserted
        shown = [x for x in snippets if x.get("hit")] or snippets
        snip_html = "<br><br>".join(_hl(x["text"]) for x in shown) or "<i>no full-text snippet at all</i>"
        trs.append(
            f'<tr><td><input type="checkbox" class="flag" data-b="{html.escape(r["bibcode"])}"></td>'
            f'<td>{html.escape(r["bibcode"])}</td>'
            f'<td><a href="{r["ads_url"]}" target="_blank">ADS</a></td>'
            f'<td>{r.get("year", "")}</td>'
            f'<td>{r["score"]}</td>'
            f'<td>{html.escape(r["reason"])}</td>'
            f'<td>{html.escape(r["title"])}</td>'
            f'<td>{snip_html}</td></tr>'
        )
    body = (
        "<table><thead><tr><th>wrong?</th><th>bibcode</th><th>ads</th><th>year</th>"
        "<th>score</th><th>reason</th><th>title</th><th>matched snippet</th></tr></thead>"
        f"<tbody>{''.join(trs)}</tbody></table>"
    )
    cfg = json.dumps({"id": table_id, "to": to_table})
    bar = (f'<div class="bar">{hint} Flagged: <b id="nflag">0</b> '
           f'<button id="dl">Download flagged CSV</button>'
           f'<br>Save the file as <code>data/flagged_{table_id}.csv</code> and merge it into '
           f'<code>data/manual_flags.csv</code>, then re-run the sync script.</div>')
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"<!doctype html><meta charset='utf-8'><title>{title}</title><style>{CSS}</style>"
                f"<h2>{title} ({len(rows)} papers) &mdash; click a column to sort</h2>{bar}{body}"
                f"<script>var CFG={cfg};{FLAG_JS}{SORT_JS}</script>")
