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

let tooltipEl = null;
function tooltip() {
  if (!tooltipEl) {
    tooltipEl = document.createElement("div");
    tooltipEl.className = "chart-tooltip";
    document.body.appendChild(tooltipEl);
  }
  return tooltipEl;
}

// svgId: target <svg>. rows: [{year, partial, ...}]. valueKey: which field to plot.
// shortFmt: true rounds large values (citations) to a compact label so bars stay readable.
// unit: plain-language noun for the hover tooltip, which always shows the exact,
// un-rounded number even where the bar's own label is compact-formatted (e.g. "11k").
function drawChart(svgId, rows, valueKey, shortFmt, unit) {
  const svg = document.getElementById(svgId);
  svg.innerHTML = "";

  const W = 400, H = 290;
  const padL = 8, padR = 8, padTop = 34, padBottom = 56;
  const plotW = W - padL - padR;
  const plotH = H - padTop - padBottom;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);

  const max = Math.max(...rows.map(d => d[valueKey]));
  const n = rows.length;
  const gap = 0.35;                    // gap as a fraction of bar width
  const slot = plotW / n;
  const barW = slot / (1 + gap);

  const labelFmt = shortFmt ? compactFmt : fmt;

  // light horizontal reference lines (no axis, no numbers -- values are labelled directly)
  [0.25, 0.5, 0.75, 1].forEach(f => {
    const y = padTop + plotH * (1 - f);
    el("line", { x1: padL, x2: padL + plotW, y1: y, y2: y, class: "grid-line" }, svg);
  });

  rows.forEach((d, i) => {
    const v = d[valueKey];
    const h = max > 0 ? (v / max) * plotH : 0;
    const x = padL + i * slot + (slot - barW) / 2;
    const y = padTop + plotH - h;

    const bar = el("rect", {
      x, y, width: barW, height: Math.max(h, 1),
      rx: 3, ry: 3,
      class: "bar" + (d.partial ? " partial" : ""),
    }, svg);
    bar.style.setProperty("--i", i);

    const tipText = `${fmt(v)} ${unit} · ${d.partial ? d.year + " (year to date)" : d.year}`;
    bar.addEventListener("mouseenter", () => {
      const tip = tooltip();
      tip.textContent = tipText;
      tip.classList.add("show");
    });
    bar.addEventListener("mousemove", (e) => {
      const tip = tooltip();
      tip.style.left = e.clientX + "px";
      tip.style.top = e.clientY + "px";
    });
    bar.addEventListener("mouseleave", () => tooltip().classList.remove("show"));

    const val = el("text", {
      x: x + barW / 2, y: y - 8, class: "bar-label", "text-anchor": "middle",
    }, svg);
    val.textContent = labelFmt(v);
    val.style.setProperty("--i", i);

    const label = d.partial ? `${d.year}†` : `${d.year}`;
    el("text", {
      x: x + barW / 2, y: padTop + plotH + 22, class: "axis-label", "text-anchor": "middle",
    }, svg).textContent = label;
  });

  if (rows.some(d => d.partial)) {
    el("text", {
      x: padL + plotW, y: padTop + plotH + 42, class: "axis-label", "text-anchor": "end",
    }, svg).textContent = "† year to date";
  }
}

// Bars grow up (shortest delay first = oldest year first) and their value labels fade in
// right after, the first time each chart scrolls into view. One-shot: once played, the
// chart is left in its finished state. Skipped entirely for prefers-reduced-motion.
function animateOnView(svgIds) {
  const svgs = svgIds.map(id => document.getElementById(id)).filter(Boolean);
  if (!svgs.length) return;

  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduceMotion || !("IntersectionObserver" in window)) {
    svgs.forEach(s => s.classList.add("in"));
    return;
  }

  const io = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add("in");
        io.unobserve(entry.target);
      }
    });
  }, { threshold: 0.3 });
  svgs.forEach(s => io.observe(s));
}

function compactFmt(n) {
  return n >= 1000 ? (n / 1000).toFixed(n >= 10000 ? 0 : 1) + "k" : fmt(n);
}

// Fades + lifts each matched element in the first time it scrolls into view (or immediately,
// for whatever is already on screen at load). One-shot, and skipped for prefers-reduced-motion.
function revealOnView(selector) {
  const els = Array.from(document.querySelectorAll(selector));
  if (!els.length) return;

  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduceMotion || !("IntersectionObserver" in window)) {
    els.forEach(e => e.classList.add("in"));
    return;
  }

  const io = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add("in");
        io.unobserve(entry.target);
      }
    });
  }, { threshold: 0.15 });
  els.forEach(e => io.observe(e));
}

// Counts a number up from 0 to target, easing out, the first time its element scrolls into
// view. formatter renders the in-progress (rounded) and final value alike.
function countUp(target_el, target, formatter, duration = 900) {
  const start = performance.now();
  function tick(now) {
    const t = Math.min(1, (now - start) / duration);
    const eased = 1 - Math.pow(1 - t, 3);
    target_el.textContent = formatter(target * eased);
    if (t < 1) requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
}

// items: [{el, target, formatter}]
function countUpOnView(items) {
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduceMotion || !("IntersectionObserver" in window)) {
    items.forEach(it => { it.el.textContent = it.formatter(it.target); });
    return;
  }

  const io = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      const item = items.find(it => it.el === entry.target);
      if (entry.isIntersecting && item) {
        countUp(item.el, item.target, item.formatter);
        io.unobserve(entry.target);
      }
    });
  }, { threshold: 0.4 });
  items.forEach(it => io.observe(it.el));
}

async function main() {
  let data;
  try {
    const res = await fetch("data/metrics.json", { cache: "no-store" });
    data = await res.json();
  } catch (err) {
    document.getElementById("total-papers").textContent = "—";
    document.getElementById("papers-as-of").textContent = "—";
    document.getElementById("load-error").hidden = false;
    console.error("Could not load data/metrics.json", err);
    return;
  }

  drawChart("growth-chart", data.by_year, "count", false, "papers");
  drawChart("citations-chart", data.citations_by_year, "cumulative", true, "citations");
  animateOnView(["growth-chart", "citations-chart"]);

  const c = data.community;
  document.getElementById("stat-authors-note").textContent =
    `Matched by ORCID where available (${c.orcid_coverage_pct}% of author entries).`;

  countUpOnView([
    { el: document.getElementById("total-papers"), target: data.total_papers, formatter: v => fmt(Math.round(v)) },
    { el: document.getElementById("stat-authors"), target: c.unique_authors, formatter: v => fmt(Math.round(v)) },
    { el: document.getElementById("stat-first-authors"), target: c.unique_first_authors, formatter: v => fmt(Math.round(v)) },
    { el: document.getElementById("stat-external"), target: c.external_adoption_pct, formatter: v => Math.round(v) + "%" },
    { el: document.getElementById("stat-team-size"), target: c.median_authors_per_paper, formatter: v => Math.round(v) },
  ]);

  const genDate = new Date(data.generated_at);
  const genDateFmt = genDate.toLocaleDateString("en-US", {
    year: "numeric", month: "long", day: "numeric",
  });
  document.getElementById("last-updated").textContent = genDateFmt;
  document.getElementById("papers-as-of").textContent = genDateFmt;

  document.getElementById("ads-search-link").href = data.ads_search_url;

  const libLink = document.getElementById("ads-library-link");
  if (data.ads_library_url) {
    libLink.href = data.ads_library_url;
  } else {
    // no library synced yet (see scripts/sync_ads_library.py) -- fall back to the live search
    libLink.href = data.ads_search_url;
    libLink.innerHTML = 'Search ADS <span class="arrow" aria-hidden="true">&rarr;</span>';
  }
}

main();
revealOnView(".reveal"); // independent of the metrics.json fetch, so sections still reveal on a load error
