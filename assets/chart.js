// Reads data/metrics.json (rebuilt monthly by scripts/update_metrics.py via GitHub Actions)
// and fills in the counter, growth chart, stats and search link. No build step, no dependencies.

const SVG_NS = "http://www.w3.org/2000/svg";

function el(tag, attrs, parent) {
  const e = document.createElementNS(SVG_NS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(e);
  return e;
}

function fmt(n) {
  return n.toLocaleString("en-US");
}

function drawChart(byYear) {
  const svg = document.getElementById("growth-chart");
  svg.innerHTML = "";

  const W = 640, H = 290;
  const padL = 8, padR = 8, padTop = 34, padBottom = 56;
  const plotW = W - padL - padR;
  const plotH = H - padTop - padBottom;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);

  const max = Math.max(...byYear.map(d => d.count));
  const n = byYear.length;
  const gap = 0.35;                    // gap as a fraction of bar width
  const slot = plotW / n;
  const barW = slot / (1 + gap);

  // light horizontal reference lines (no axis, no numbers -- values are labelled directly)
  [0.25, 0.5, 0.75, 1].forEach(f => {
    const y = padTop + plotH * (1 - f);
    el("line", { x1: padL, x2: padL + plotW, y1: y, y2: y, class: "grid-line" }, svg);
  });

  byYear.forEach((d, i) => {
    const h = max > 0 ? (d.count / max) * plotH : 0;
    const x = padL + i * slot + (slot - barW) / 2;
    const y = padTop + plotH - h;

    el("rect", {
      x, y, width: barW, height: Math.max(h, 1),
      rx: 3, ry: 3,
      class: "bar" + (d.partial ? " partial" : ""),
    }, svg);

    el("text", {
      x: x + barW / 2, y: y - 8, class: "bar-label", "text-anchor": "middle",
    }, svg).textContent = fmt(d.count);

    const label = d.partial ? `${d.year}†` : `${d.year}`;
    el("text", {
      x: x + barW / 2, y: padTop + plotH + 22, class: "axis-label", "text-anchor": "middle",
    }, svg).textContent = label;
  });

  if (byYear.some(d => d.partial)) {
    el("text", {
      x: padL + plotW, y: padTop + plotH + 42, class: "axis-label", "text-anchor": "end",
    }, svg).textContent = "† year to date";
  }
}

async function main() {
  let data;
  try {
    const res = await fetch("data/metrics.json", { cache: "no-store" });
    data = await res.json();
  } catch (err) {
    document.getElementById("total-papers").textContent = "—";
    document.getElementById("load-error").hidden = false;
    console.error("Could not load data/metrics.json", err);
    return;
  }

  document.getElementById("total-papers").textContent = fmt(data.total_papers);
  drawChart(data.by_year);

  const c = data.community;
  document.getElementById("stat-authors").textContent = fmt(c.unique_author_names);
  document.getElementById("stat-first-authors").textContent = fmt(c.unique_first_authors);
  document.getElementById("stat-team-size").textContent = c.median_authors_per_paper;
  document.getElementById("stat-external").textContent = c.external_adoption_pct + "%";

  const genDate = new Date(data.generated_at);
  document.getElementById("last-updated").textContent = genDate.toLocaleDateString("en-US", {
    year: "numeric", month: "long", day: "numeric",
  });

  document.getElementById("ads-search-link").href = data.ads_search_url;
}

main();
