/* Brickonomy client: Chart.js charts + refresh-progress polling. */
(function () {
  "use strict";

  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  // Static-export mode: pages are served from GitHub Pages (or any file host);
  // API endpoints become pre-rendered .json files under the site base path.
  const IS_STATIC = document.body.dataset.static === "1";
  // Relative prefix for the current page ('' at the site root, '../' one level
  // down), so the export works at any mount point.
  const BASE = document.body.dataset.base || "";
  const apiURL = (path) => (IS_STATIC ? `${BASE}${path.replace(/^\//, "")}.json` : path);

  const CHART_DEFAULTS = {
    color: css("--muted") || "#66718f",
    borderColor: css("--grid") || "rgba(124,160,255,0.10)",
  };
  if (window.Chart) {
    Chart.defaults.color = CHART_DEFAULTS.color;
    Chart.defaults.borderColor = CHART_DEFAULTS.borderColor;
    Chart.defaults.font.family = css("--font-body") || 'system-ui, -apple-system, "Segoe UI", sans-serif';
  }

  const SOURCE_STYLE = {
    blended:   { color: css("--s1") || "#3987e5", width: 2.5, label: "Value" },
    bricklink: { color: css("--s2") || "#d95926", width: 1.5, label: "BrickLink" },
    ebay:      { color: css("--s3") || "#199e70", width: 1.5, label: "eBay" },
    brickowl:  { color: css("--s4") || "#c98500", width: 1.5, label: "BrickOwl" },
  };

  // ── phone header: search toggle ─────────────────────────────────────────
  // Collapses the search row behind an icon button so a 54px phone header
  // stays one line; desktop never renders .searchtoggle (see style.css), so
  // this is inert there.
  const searchToggle = document.getElementById("searchToggle");
  const searchRow = document.getElementById("siteSearchRow");
  if (searchToggle && searchRow) {
    const setOpen = (open) => {
      searchRow.classList.toggle("open", open);
      searchToggle.setAttribute("aria-expanded", open ? "true" : "false");
      if (open) {
        const input = searchRow.querySelector("input");
        if (input) input.focus();
      }
    };
    searchToggle.addEventListener("click", () =>
      setOpen(!searchRow.classList.contains("open")));
    document.addEventListener("click", (e) => {
      if (!searchRow.classList.contains("open")) return;
      if (!searchRow.contains(e.target) && e.target !== searchToggle) setOpen(false);
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && searchRow.classList.contains("open")) setOpen(false);
    });
  }

  // ── phone bottom dock: "More" panel ─────────────────────────────────────
  const dockMoreBtn = document.getElementById("dockMoreBtn");
  const dockPanel = document.getElementById("dockMorePanel");
  const dockScrim = document.getElementById("dockScrim");
  if (dockMoreBtn && dockPanel && dockScrim) {
    const setOpen = (open) => {
      dockPanel.hidden = !open;
      dockScrim.hidden = !open;
      dockMoreBtn.setAttribute("aria-expanded", open ? "true" : "false");
    };
    dockMoreBtn.addEventListener("click", () => setOpen(dockPanel.hidden));
    dockScrim.addEventListener("click", () => setOpen(false));
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !dockPanel.hidden) setOpen(false);
    });
  }

  // ── set detail: history + forecast ─────────────────────────────────────
  const historyCanvas = document.getElementById("historyChart");
  if (historyCanvas && window.Chart) {
    const itemId = historyCanvas.dataset.item;
    const retiredYear = historyCanvas.dataset.retired;

    // Vertical marker at the estimated retirement date.
    const retirementMarker = {
      id: "retirementMarker",
      afterDatasetsDraw(chart) {
        if (!retiredYear) return;
        const x = chart.scales.x.getPixelForValue(new Date(`${retiredYear}-06-30`));
        const { top, bottom } = chart.chartArea;
        if (x < chart.chartArea.left || x > chart.chartArea.right) return;
        const c = chart.ctx;
        c.save();
        c.setLineDash([4, 4]);
        c.strokeStyle = css("--s2") || "#d95926";
        c.lineWidth = 1.5;
        c.beginPath(); c.moveTo(x, top); c.lineTo(x, bottom); c.stroke();
        c.setLineDash([]);
        c.fillStyle = css("--s2") || "#d95926";
        c.font = "11px system-ui, sans-serif";
        c.textAlign = "center";
        c.fillText("retired", x, top + 11);
        c.restore();
      },
    };

    fetch(apiURL(`/api/sets/${itemId}/history`))
      .then((r) => r.json())
      .then((data) => {
        const datasets = [];
        for (const [source, style] of Object.entries(SOURCE_STYLE)) {
          const pts = (data.series[source] || []).map((p) => ({ x: p.t, y: p.v }));
          if (!pts.length) continue;
          datasets.push({
            label: style.label, data: pts,
            borderColor: style.color, backgroundColor: style.color,
            borderWidth: style.width, pointRadius: pts.length < 15 ? 3 : 0,
            tension: 0.25, spanGaps: true,
          });
        }

        // Used-condition blended value, so both conditions read on one axis.
        const usedPts = ((data.series_used || {}).blended || [])
          .map((p) => ({ x: p.t, y: p.v }));
        if (usedPts.length) {
          datasets.push({
            label: "Used", data: usedPts,
            borderColor: css("--s5") || "#9085e9",
            backgroundColor: css("--s5") || "#9085e9",
            borderWidth: 2, pointRadius: usedPts.length < 15 ? 3 : 0,
            tension: 0.25, spanGaps: true,
          });
        }
        if (data.forecast && data.forecast.length > 1) {
          const f = data.forecast;
          datasets.push({
            label: "Forecast", data: f.map((p) => ({ x: p.t, y: p.v })),
            borderColor: SOURCE_STYLE.blended.color, borderDash: [6, 5],
            borderWidth: 2, pointRadius: 3, tension: 0, fill: false,
          });
          datasets.push({
            label: "Forecast high", data: f.map((p) => ({ x: p.t, y: p.hi })),
            borderColor: "transparent", pointRadius: 0, fill: "+1",
            backgroundColor: css("--band"), tension: 0,
          });
          datasets.push({
            label: "Forecast low", data: f.map((p) => ({ x: p.t, y: p.lo })),
            borderColor: "transparent", pointRadius: 0, fill: false, tension: 0,
          });
        }
        const chart = new Chart(historyCanvas, {
          type: "line",
          data: { datasets },
          plugins: [retirementMarker],
          options: {
            responsive: true, maintainAspectRatio: false,
            interaction: { mode: "nearest", axis: "x", intersect: false },
            plugins: {
              legend: { display: false },
              tooltip: {
                filter: (item) => !item.dataset.label.startsWith("Forecast "),
                callbacks: {
                  label: (item) =>
                    ` ${item.dataset.label}: ${item.parsed.y.toLocaleString()} ${data.currency}`,
                },
              },
            },
            scales: {
              // Let Chart.js pick the tick unit. Pinning it to "month" put a
              // label on every month, and the forecast runs years out — sixty
              // rotated labels that read as a smear.
              x: { type: "time", grid: { display: false },
                   ticks: { autoSkip: true, maxTicksLimit: 10 } },
              y: { ticks: { callback: (v) => v.toLocaleString() } },
            },
          },
        });

        // Range buttons clamp both ends of the x axis. Clamping only the
        // start left the forecast stretching the axis to 2031, so eight
        // months of actual scans were squeezed into the far left edge.
        // The earliest actual scan across every observed series. A range that
        // reaches back past it just adds empty axis: 3Y on a set first scanned
        // eight months ago drew two and a half years of nothing and bunched
        // the real points against the right edge.
        const firstScan = datasets
          .filter((d) => !d.label.startsWith("Forecast"))
          .flatMap((d) => d.data.map((p) => p.x))
          .sort()[0];

        const clamp = (btn) => {
          const days = Number(btn.dataset.range);
          const x = chart.options.scales.x;
          if (!days) {
            delete x.min;
            delete x.max;
          } else {
            const from = new Date();
            from.setDate(from.getDate() - days);
            let start = from.toISOString().slice(0, 10);
            if (firstScan && firstScan > start) start = firstScan;
            x.min = start;
            // A year of forecast beyond today: enough to see where the trend
            // is pointing without the horizon dominating the history.
            const to = new Date();
            to.setDate(to.getDate() + 365);
            x.max = to.toISOString().slice(0, 10);
          }
        };
        document.querySelectorAll(".rangebtn").forEach((btn) => {
          if (btn.classList.contains("active")) clamp(btn);
          btn.addEventListener("click", () => {
            document.querySelectorAll(".rangebtn").forEach((b) => b.classList.remove("active"));
            btn.classList.add("active");
            clamp(btn);
            chart.update();
          });
        });
        chart.update();
      })
      .catch(() => { historyCanvas.parentElement.textContent = "Could not load price history."; });
  }

  // ── portfolio: value over time ─────────────────────────────────────────
  const pfCanvas = document.getElementById("portfolioChart");
  if (pfCanvas && window.Chart) {
    // On the encrypted export the history arrives inside the decrypted
    // payload — it is never published as a fetchable .json, or it would give
    // away what the encryption is there to protect.
    const preloaded = window.__portfolioHistory;
    (preloaded
      ? Promise.resolve(preloaded)
      : fetch(apiURL("/api/portfolio/history")).then((r) => r.json()))
      .then((data) => {
        const pts = (data.series || []).map((p) => ({ x: p.t, y: p.v }));
        if (!pts.length) {
          pfCanvas.parentElement.textContent = "No history yet — run a scan to record the first snapshot.";
          return;
        }
        // The series starts where most of the collection had been scanned.
        // Before that its rise is the scanner catching up rather than the sets
        // gaining value, so say where it starts and why rather than let a
        // three-point chart look broken.
        const note = document.getElementById("portfolioChartNote");
        if (note && data.tracked) {
          const parts = [`${pts.length} scans of ${data.tracked} tracked sets, `
                         + `from ${data.covered_from}.`];
          // A day whose total covers fewer sets is low partly because the rest
          // had not been scanned, not because they were worth less. Saying so
          // is the difference between a portfolio that grew and a scanner that
          // caught up.
          if (data.thin_points) {
            parts.push(`The earliest ${data.thin_points === 1 ? "point covers"
                        : `${data.thin_points} points cover`} as few as `
                       + `${data.min_covered} of them, so some of the rise is `
                       + "the scanner catching up rather than prices moving.");
          }
          note.textContent = parts.join(" ");
        }
        new Chart(pfCanvas, {
          type: "line",
          data: {
            datasets: [{
              label: "Portfolio value", data: pts,
              borderColor: SOURCE_STYLE.blended.color, borderWidth: 2.5,
              backgroundColor: css("--band"), fill: true,
              pointRadius: pts.length < 15 ? 3 : 0, tension: 0.25,
            }],
          },
          options: {
            responsive: true, maintainAspectRatio: false,
            plugins: {
              legend: { display: false },
              tooltip: { callbacks: { label: (item) =>
                ` ${item.parsed.y.toLocaleString()} ${data.currency}` } },
            },
            scales: {
              x: { type: "time", grid: { display: false },
                   ticks: { autoSkip: true, maxTicksLimit: 8 } },
              y: { ticks: { callback: (v) => v.toLocaleString() } },
            },
          },
        });
      })
      .catch(() => { pfCanvas.parentElement.textContent = "Could not load portfolio history."; });
  }

  // ── header search: instant catalog lookup, works statically too ───────
  const searchInput = document.getElementById("siteSearch");
  const searchResults = document.getElementById("siteSearchResults");
  if (searchInput && searchResults) {
    let index = null, active = -1;
    const loadIndex = () => loadCatalog().then((items) => (index = items));

    const render = (matches) => {
      active = -1;
      if (!matches.length) { searchResults.hidden = true; return; }
      searchResults.innerHTML = matches
        .map((i) => `<a href="${itemURL(i)}"><b>${i.id}</b> ${i.name}
          <span>${i.type === "M" ? "minifig" : [i.theme, i.year].filter(Boolean).join(" · ")}</span></a>`)
        .join("");
      searchResults.hidden = false;
    };

    const search = () => {
      const q = searchInput.value.trim().toLowerCase();
      if (q.length < 2) { searchResults.hidden = true; return; }
      loadCatalog().then((items) => {
        const starts = [], contains = [];
        for (const i of items) {
          const id = i.id.toLowerCase(), name = (i.name || "").toLowerCase();
          if (id.startsWith(q)) starts.push(i);
          else if (id.includes(q) || name.includes(q)) contains.push(i);
          if (starts.length >= 8) break;
        }
        render(starts.concat(contains).slice(0, 8));
      });
    };

    searchInput.addEventListener("input", search);
    searchInput.addEventListener("focus", () => { loadIndex(); search(); });
    searchInput.addEventListener("keydown", (e) => {
      const links = [...searchResults.querySelectorAll("a")];
      if (e.key === "Escape") { searchResults.hidden = true; searchInput.blur(); }
      if (!links.length) return;
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        active = (active + (e.key === "ArrowDown" ? 1 : -1) + links.length) % links.length;
        links.forEach((l, i) => l.classList.toggle("on", i === active));
      } else if (e.key === "Enter") {
        e.preventDefault();
        (links[active] || links[0]).click();
      }
    });
    document.addEventListener("click", (e) => {
      if (!searchResults.contains(e.target) && e.target !== searchInput) {
        searchResults.hidden = true;
      }
    });
  }

  // ── catalog helpers shared by the browser and the lite set page ───────
  let catalogPromise = null;
  const loadCatalog = () => {
    if (!catalogPromise) {
      catalogPromise = fetch(apiURL("/api/index"))
        .then((r) => r.json())
        .then((d) => {
          // Compact wire format: rows of
          // [id, name, themeIndex, year, parts, type, priced, valueNew, valueUsed]
          const themes = d.themes || [];
          catalogCurrency = d.currency || "ILS";
          return (d.rows || []).map((r) => ({
            id: r[0], name: r[1], theme: r[2] >= 0 ? themes[r[2]] : "",
            year: r[3] || null, parts: r[4] || null, type: r[5], p: r[6],
            vnew: r[7] || 0, vused: r[8] || 0,
          }));
        })
        .catch(() => []);
    }
    return catalogPromise;
  };
  // Set by loadCatalog from the index; the catalog table formats with it.
  let catalogCurrency = "ILS";

  const itemURL = (i) =>
    i.p
      ? (IS_STATIC ? `${BASE}sets/${i.id}.html` : `/sets/${i.id}`)
      : (IS_STATIC ? `${BASE}set.html?id=${encodeURIComponent(i.id)}`
                   : `/sets/${i.id}`);
  const setImg = (id, type) =>
    type === "M" || /^[a-z]/i.test(id)
      ? `https://img.bricklink.com/ItemImage/MN/0/${id}.png`
      : `https://img.bricklink.com/ItemImage/SN/0/${id.includes("-") ? id : id + "-1"}.png`;
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  // ── full-catalog browser (static Sets page over ~23k rows) ────────────
  const catalogTable = document.getElementById("catalogTable");
  if (catalogTable) {
    const qEl = document.getElementById("catalogSearch");
    const themeEl = document.getElementById("catalogTheme");
    const sortEl = document.getElementById("catalogSort");
    const pricedEl = document.getElementById("catalogPriced");
    const countEl = document.getElementById("catalogCount");
    const moreBtn = document.getElementById("catalogMore");
    const body = catalogTable.querySelector("tbody");
    const catalogKind = catalogTable.dataset.kind || "";
    const SYM = { ILS: "₪", USD: "$", EUR: "€", GBP: "£" };
    const catalogMoney = (v) => (SYM[catalogCurrency] || catalogCurrency + " ") +
      v.toLocaleString(undefined, { maximumFractionDigits: v >= 100 ? 0 : 2 });
    const PAGE = 100;
    let all = [], shown = 0, matches = [];

    const sorters = {
      year: (a, b) => (b.year || 0) - (a.year || 0) || a.id.localeCompare(b.id),
      "year-asc": (a, b) => (a.year || 9999) - (b.year || 9999) || a.id.localeCompare(b.id),
      id: (a, b) => a.id.localeCompare(b.id, undefined, { numeric: true }),
      parts: (a, b) => (b.parts || 0) - (a.parts || 0),
      value: (a, b) => (b.vnew || 0) - (a.vnew || 0),
    };

    // Same treatment as the server-rendered tables: clip the name rather than
    // wrap it, and drop the "(minifig)" tag on a page where every row is one.
    const rowHTML = (i) => `<tr>
      <td class="name"><a href="${itemURL(i)}" title="${esc(i.id)} ${esc(i.name)}"><img
               class="thumb" src="${setImg(i.id, i.type)}" alt="" loading="lazy"
               onerror="this.style.visibility='hidden'"><b>${esc(i.id)}</b> ${esc(i.name)}</a>${
          i.type === "M" && catalogKind !== "M"
            ? '<span style="color:var(--muted)"> (minifig)</span>' : ""}</td>
      <td class="theme" title="${esc(i.theme)}">${esc(i.theme) || "—"}</td>
      <td class="num">${i.year || "—"}</td>
      <td class="num">${i.parts ? i.parts.toLocaleString() : "—"}</td>
      <td class="num rc-value"><b>${i.vnew ? catalogMoney(i.vnew) : "—"}</b></td>
      <td class="num">${i.vused ? catalogMoney(i.vused) : "—"}</td>
      <td class="rc-delta">${(i.vnew || i.vused)
              ? '<span class="chip on">priced</span>'
              : i.p
                ? '<span class="chip" style="color:var(--muted)" title="Scanned, but BrickLink had no sold history and too few asks to price it from.">no price yet</span>'
                : '<span class="chip" style="color:var(--muted)">not scanned</span>'}</td>
    </tr>`;

    const draw = (append) => {
      const slice = matches.slice(append ? shown : 0, (append ? shown : 0) + PAGE);
      if (!append) { body.innerHTML = ""; shown = 0; }
      body.insertAdjacentHTML("beforeend", slice.map(rowHTML).join(""));
      shown += slice.length;
      countEl.textContent = `${matches.length.toLocaleString()} matching item${
        matches.length === 1 ? "" : "s"} — showing ${shown.toLocaleString()}`;
      moreBtn.hidden = shown >= matches.length;
    };

    const apply = () => {
      const q = qEl.value.trim().toLowerCase();
      const theme = themeEl.value;
      const pricedOnly = pricedEl.checked;
      matches = all.filter((i) => {
        // The page declares which half of the catalog it is: without this the
        // minifig tab listed sets too.
        if (catalogKind && i.type !== catalogKind) return false;
        // A price, not a page. `p` means the item has its own exported page,
        // which a scan yields even when it finds nothing usable: ten sets
        // were flagged priced while showing "—" in both value columns.
        if (pricedOnly && !(i.vnew || i.vused)) return false;
        if (theme && i.theme !== theme) return false;
        if (!q) return true;
        return i.id.toLowerCase().includes(q) || (i.name || "").toLowerCase().includes(q);
      });
      matches.sort(sorters[sortEl.value] || sorters.year);
      draw(false);
    };

    loadCatalog().then((items) => {
      all = items;
      const themes = [...new Set(items.map((i) => i.theme).filter(Boolean))].sort();
      themeEl.insertAdjacentHTML("beforeend",
        themes.map((t) => `<option value="${esc(t)}">${esc(t)}</option>`).join(""));
      // Deep-link support: /sets?q=falcon keeps working in the static build.
      const params = new URLSearchParams(location.search);
      if (params.get("q")) qEl.value = params.get("q");
      if (params.get("theme")) themeEl.value = params.get("theme");
      apply();
    });

    [qEl, themeEl, sortEl, pricedEl].forEach((el) =>
      el.addEventListener("input", apply));
    moreBtn.addEventListener("click", () => draw(true));
  }

  // ── lite set page for catalog items without scraped prices ────────────
  const lite = document.getElementById("liteSet");
  if (lite) {
    const id = new URLSearchParams(location.search).get("id") || "";
    document.getElementById("liteId").textContent = id || "<set>";
    const blNum = id.includes("-") ? id : `${id}-1`;
    document.getElementById("liteBL").href =
      `https://www.bricklink.com/v2/catalog/catalogitem.page?S=${encodeURIComponent(blNum)}`;
    document.getElementById("liteBE").href =
      `https://www.brickeconomy.com/set/${encodeURIComponent(blNum)}`;
    document.getElementById("liteEbay").href =
      `https://www.ebay.com/sch/i.html?_nkw=lego+${encodeURIComponent(id)}`;

    loadCatalog().then((items) => {
      const item = items.find((i) => i.id === id);
      if (!item) {
        document.getElementById("liteName").textContent = id || "Unknown set";
        document.getElementById("liteMeta").textContent =
          "Not in the catalog index. Import it with python -m brickonomy.rebrickable.";
        return;
      }
      document.title = `${item.id} ${item.name} · Brickonomy`;
      document.getElementById("liteTheme").textContent = item.theme || "Catalog";
      document.getElementById("liteName").innerHTML =
        `${esc(item.name)} <span style="color:var(--muted);font-weight:400">· ${esc(item.id)}</span>`;
      document.getElementById("liteMeta").textContent = [
        item.year ? `Released ${item.year}` : null,
        item.parts ? `${item.parts.toLocaleString()} parts` : null,
      ].filter(Boolean).join(" · ");

      const related = items
        .filter((i) => i.theme && i.theme === item.theme && i.id !== item.id)
        .sort((a, b) => Math.abs((a.year || 0) - (item.year || 0)) -
                        Math.abs((b.year || 0) - (item.year || 0)))
        .slice(0, 12);
      document.getElementById("liteRelated").innerHTML = related.map((i) => `
        <a class="relcard" href="${itemURL(i)}">
          <img src="${setImg(i.id, i.type)}" alt="" loading="lazy" onerror="this.style.visibility='hidden'">
          <div class="relname"><b>${esc(i.id)}</b> ${esc((i.name || "").slice(0, 34))}</div>
          <div class="relmeta">${i.year || ""}${(i.vnew || i.vused) ? " · priced" : ""}</div>
        </a>`).join("");
    });
  }

  // ── client-side table filter (static pages have no server search) ──────
  document.querySelectorAll("input[data-filter-table]").forEach((input) => {
    const table = document.getElementById(input.dataset.filterTable);
    if (!table) return;
    input.addEventListener("input", () => {
      const needle = input.value.trim().toLowerCase();
      table.querySelectorAll("tr").forEach((tr, i) => {
        if (i === 0) return; // header
        tr.style.display = !needle || tr.textContent.toLowerCase().includes(needle) ? "" : "none";
      });
    });
  });

  // ── sortable tables ────────────────────────────────────────────────────
  // Any <table data-sortable> gets click-to-sort headers. Numeric cells carry
  // data-v="<raw number>" so sorting is never fooled by "₪3.23", "▲ 8%" or
  // "—"; anything else compares as text. Missing values sink to the bottom in
  // both directions, which is what you want when half a column is unscanned.
  const cellValue = (row, idx) => {
    const td = row.children[idx];
    if (!td) return null;
    if (td.hasAttribute("data-v")) {
      const raw = td.getAttribute("data-v");
      if (raw === "" || raw === null) return null;
      const n = parseFloat(raw);
      return Number.isNaN(n) ? raw.toLowerCase() : n;
    }
    const text = td.textContent.trim();
    if (!text || text === "—") return null;
    return text.toLowerCase();
  };

  const compare = (a, b) => {
    if (a === null && b === null) return 0;
    if (a === null) return 1;            // blanks last, always
    if (b === null) return -1;
    if (typeof a === "number" && typeof b === "number") return a - b;
    return String(a).localeCompare(String(b), undefined, { numeric: true });
  };

  window.brickonomySortRows = (rows, idx, dir) =>
    rows.sort((r1, r2) => {
      const c = compare(cellValue(r1, idx), cellValue(r2, idx));
      // Blanks stay last even when the direction flips.
      if (cellValue(r1, idx) === null || cellValue(r2, idx) === null) return c;
      return dir === "desc" ? -c : c;
    });

  document.querySelectorAll("table[data-sortable]").forEach((table) => {
    const head = table.tHead ? table.tHead.rows[0] : table.rows[0];
    const body = table.tBodies[0];
    if (!head || !body) return;

    [...head.cells].forEach((th, idx) => {
      if (th.dataset.nosort !== undefined) return;
      th.tabIndex = 0;
      th.dataset.sort = "";
      const run = () => {
        // First click on a numeric column sorts high→low; text sorts A→Z.
        const wasNumeric = body.rows[0] &&
          body.rows[0].children[idx] &&
          body.rows[0].children[idx].hasAttribute("data-v");
        const current = th.dataset.sort;
        const dir = current === "asc" ? "desc" : current === "desc" ? "asc"
                                                                   : (wasNumeric ? "desc" : "asc");
        [...head.cells].forEach((o) => { o.dataset.sort = ""; });
        th.dataset.sort = dir;
        window.brickonomySortRows([...body.rows], idx, dir)
          .forEach((tr) => body.appendChild(tr));
      };
      th.addEventListener("click", run);
      th.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); run(); }
      });
    });
  });

  // ── minifig breakdown: sort the tiles, and swap grid ⇄ table ───────────
  const figGrid = document.getElementById("figGrid");
  if (figGrid) {
    const figSort = document.getElementById("figSort");
    const figTable = document.getElementById("figTable");
    const viewBox = document.getElementById("figView");

    const sorters = {
      "value-desc": (a, b) => (+b.dataset.value) - (+a.dataset.value),
      "value-asc": (a, b) => (+a.dataset.value) - (+b.dataset.value),
      "name-asc": (a, b) => a.dataset.name.localeCompare(b.dataset.name),
      "qty-desc": (a, b) => (+b.dataset.qty) - (+a.dataset.qty),
    };
    const applySort = () => {
      const fn = sorters[figSort.value] || sorters["value-desc"];
      [...figGrid.children].sort(fn).forEach((el) => figGrid.appendChild(el));
      localStorage.setItem("figSort", figSort.value);
    };
    const saved = localStorage.getItem("figSort");
    if (saved && sorters[saved]) figSort.value = saved;
    figSort.addEventListener("change", applySort);
    applySort();

    const setView = (view) => {
      figGrid.hidden = view !== "grid";
      figTable.hidden = view !== "table";
      viewBox.querySelectorAll("[data-view]").forEach((b) =>
        b.classList.toggle("on", b.dataset.view === view));
      localStorage.setItem("figView", view);
    };
    viewBox.querySelectorAll("[data-view]").forEach((b) =>
      b.addEventListener("click", () => setView(b.dataset.view)));
    setView(localStorage.getItem("figView") === "table" ? "table" : "grid");

    // ── used / new figure prices ─────────────────────────────────────────
    // Every tile and cell ships both conditions as data attributes; this
    // swaps which one is shown, including the sort key so "most valuable"
    // keeps meaning the condition on screen.
    const condBox = document.getElementById("figCond");
    if (condBox) {
      const setCond = (cond) => {
        condBox.querySelectorAll("[data-cond]").forEach((b) =>
          b.classList.toggle("on", b.dataset.cond === cond));

        figGrid.querySelectorAll(".figtile").forEach((tile) => {
          tile.dataset.value = tile.dataset[cond === "used" ? "vUsed" : "vNew"] || 0;
          const price = tile.querySelector(".figtile-value");
          const share = tile.querySelector(".figtile-share");
          if (price) price.textContent = tile.dataset[cond === "used" ? "priceUsed" : "priceNew"];
          if (share) share.textContent = tile.dataset[cond === "used" ? "shareUsed" : "shareNew"] || "";
        });

        document.querySelectorAll(".figcell-value").forEach((td) => {
          td.dataset.v = td.dataset[cond === "used" ? "vUsed" : "vNew"] || "";
          td.textContent = td.dataset[cond === "used" ? "priceUsed" : "priceNew"];
        });
        document.querySelectorAll(".figcell-share").forEach((td) => {
          td.dataset.v = td.dataset[cond === "used" ? "vUsed" : "vNew"] || "";
          td.textContent = td.dataset[cond === "used" ? "shareUsed" : "shareNew"];
        });

        const setText = (id, key) => {
          const el = document.getElementById(id);
          if (el && el.dataset[key] !== undefined) el.textContent = el.dataset[key];
        };
        setText("figFootValue", cond === "used" ? "priceUsed" : "priceNew");
        setText("figFootShare", cond === "used" ? "shareUsed" : "shareNew");

        const col = document.getElementById("figColValue");
        if (col) col.textContent = `Value (${cond})`;

        const totalLabel = document.getElementById("figsTotalLabel");
        const total = condBox.dataset[cond === "used" ? "totalUsed" : "totalNew"];
        if (totalLabel) totalLabel.textContent = total ? `, ${total} together` : "";

        const note = document.getElementById("figNote");
        const noteText = note && note.dataset[cond === "used" ? "noteUsed" : "noteNew"];
        if (noteText) note.textContent = noteText;

        // The header chip tracks the same condition, so the two never disagree.
        const chip = document.getElementById("figPctChip");
        if (chip) {
          const pct = chip.dataset[cond === "used" ? "used" : "new"];
          const partial = chip.dataset[cond === "used" ? "partialUsed" : "partialNew"];
          chip.textContent = pct
            ? `🧍 ${pct}% in figs${partial ? " (partial)" : ""}`
            : "🧍 figs not priced";
        }

        localStorage.setItem("figCond", cond);
        applySort();
      };
      condBox.querySelectorAll("[data-cond]").forEach((b) =>
        b.addEventListener("click", () => setCond(b.dataset.cond)));
      setCond(localStorage.getItem("figCond") === "new" ? "new" : "used");
    }
  }

  // ── set detail: minifigs vs parts, as a share of the part-out value ────
  const splitCanvas = document.getElementById("splitChart");
  if (splitCanvas && window.Chart) {
    const SYMBOLS = { ILS: "₪", USD: "$", EUR: "€", GBP: "£" };
    const ccy = splitCanvas.dataset.ccy || "ILS";
    const figs = parseFloat(splitCanvas.dataset.figs) || 0;
    const parts = parseFloat(splitCanvas.dataset.parts) || 0;
    const total = figs + parts;
    new Chart(splitCanvas, {
      type: "doughnut",
      data: {
        labels: ["Minifigures", "Parts"],
        datasets: [{
          data: [figs, parts],
          backgroundColor: [css("--s1") || "#3987e5", css("--s2") || "#d95926"],
          borderColor: css("--bg") || "#0b0f1a",
          borderWidth: 2,
          hoverOffset: 6,
        }],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        cutout: "58%",
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (item) => {
                const v = item.parsed;
                const pct = total ? ((v / total) * 100).toFixed(0) : 0;
                const sym = SYMBOLS[ccy] || ccy + " ";
                return ` ${item.label}: ${sym}${v.toLocaleString(undefined, {
                  maximumFractionDigits: v >= 100 ? 0 : 2,
                })} (${pct}%)`;
              },
            },
          },
        },
      },
    });
  }

  // ── import cost: recompute the landed-cost table as shipping changes ──
  const importCard = document.getElementById("importCard");
  if (importCard) {
    const SYMBOLS = { ILS: "₪", USD: "$", EUR: "€", GBP: "£" };
    const ccy = importCard.dataset.ccy || "ILS";
    // Mirrors currency.money so the recomputed cells match the rendered ones.
    const money = (v) => {
      const sym = SYMBOLS[ccy] || ccy + " ";
      return sym + v.toLocaleString(undefined, {
        minimumFractionDigits: Math.abs(v) >= 100 ? 0 : 2,
        maximumFractionDigits: Math.abs(v) >= 100 ? 0 : 2,
      });
    };
    const vatPct = parseFloat(importCard.dataset.vat) || 0;
    // USD -> display currency, taken from the server's own conversion so the
    // page never needs a live rate of its own.
    const rate = parseFloat(importCard.dataset.shipRate) || 1;
    const shipInput = document.getElementById("importShip");

    const recompute = () => {
      const usd = Math.max(0, parseFloat(shipInput.value) || 0);
      const ship = usd * rate;
      importCard.querySelectorAll("tr[data-cond]").forEach((tr) => {
        const base = parseFloat(tr.dataset.base) || 0;
        tr.querySelector(".import-ship").textContent = money(ship);
        tr.querySelector(".import-total").innerHTML =
          "<b>" + money(base + (base * vatPct) / 100 + ship) + "</b>";
      });
      localStorage.setItem("importShipUsd", String(usd));
    };
    const savedShip = localStorage.getItem("importShipUsd");
    if (savedShip !== null && savedShip !== "") shipInput.value = savedShip;
    shipInput.addEventListener("input", recompute);
    recompute();
  }

  // ── rescan button: re-price the set and every minifig in it, then reload ──
  const rescanBtn = document.getElementById("rescanBtn");
  if (rescanBtn) {
    const IDLE = "↻ Rescan prices";
    const notes = document.getElementById("rescanNotes");
    const notesLog = document.getElementById("rescanLog");
    const notesHead = document.getElementById("rescanNotesHead");

    // The worker already keeps a running log; mirror it here so a multi-minute
    // scan shows what it is doing instead of a frozen button.
    const showNotes = (s) => {
      if (!notes) return;
      notes.hidden = false;
      const lines = s.log || [];
      const atBottom =
        notesLog.scrollHeight - notesLog.scrollTop - notesLog.clientHeight < 40;
      notesLog.textContent = lines.length ? lines.join("\n") : "Starting…";
      if (atBottom) notesLog.scrollTop = notesLog.scrollHeight;
      const left = (s.queue || []).length;
      notesHead.textContent = [
        s.current_item ? `scanning ${s.current_item}` : null,
        left ? `${left} queued` : null,
        (s.errors || []).length ? `${s.errors.length} source error(s)` : null,
      ].filter(Boolean).join(" · ");
    };

    rescanBtn.addEventListener("click", () => {
      const id = rescanBtn.dataset.item;
      const body = new URLSearchParams({ item_id: id, force: "true", with_figs: "true" });
      rescanBtn.disabled = true;
      rescanBtn.textContent = "Scanning prices…";
      if (notes) {
        notes.hidden = false;
        notesLog.textContent = "Starting…";
        notesHead.textContent = "";
      }
      fetch("/refresh", { method: "POST", body,
                          headers: { Accept: "application/json" } })
        .then((r) => {
          if (r.status === 409) {
            rescanBtn.textContent = "Another scan is running — try again soon";
            setTimeout(() => {
              rescanBtn.disabled = false;
              rescanBtn.textContent = IDLE;
            }, 4000);
            return;
          }
          const poll = () =>
            fetch("/api/refresh/status")
              .then((res) => res.json())
              .then((s) => {
                showNotes(s);
                const busy = s.current_item === id || (s.queue || []).includes(id) || s.running;
                if (!busy) {
                  // Keep the notes on screen long enough to be read: the
                  // reload wipes them, and the last lines say what was found.
                  rescanBtn.textContent = "Done — reloading…";
                  return setTimeout(() => window.location.reload(), 1500);
                }
                // The figs are scanned one at a time after the set, so this
                // runs for minutes — name what it is on rather than stall.
                const left = (s.queue || []).length;
                rescanBtn.textContent = s.current_item && s.current_item !== id
                  ? `Scanning ${s.current_item}…${left ? ` (${left} left)` : ""}`
                  : "Scanning prices…";
                setTimeout(poll, 3000);
              })
              .catch(() => setTimeout(poll, 6000));
          setTimeout(poll, 3000);
        })
        .catch(() => {
          rescanBtn.disabled = false;
          rescanBtn.textContent = IDLE;
        });
    });
  }

  // ── auto-scan banner: wait for this item's scan, then show the prices ──
  const autoScan = document.getElementById("autoScan");
  if (autoScan) {
    const id = autoScan.dataset.item;
    const text = document.getElementById("autoScanText");
    let seen = false;                    // became visible in the job state
    const poll = () => {
      // Absolute path is safe: the banner only renders on the live server,
      // never in the static export.
      fetch("/api/refresh/status")
        .then((r) => r.json())
        .then((s) => {
          const queued = (s.queue || []).indexOf(id);
          const active = s.current_item === id;
          if (active || queued >= 0) {
            seen = true;
            if (text) {
              text.textContent = active
                ? "Fetching prices from BrickLink, eBay and BrickOwl…"
                : `Queued for a refresh, ${queued} ahead.`;
            }
            setTimeout(poll, 3000);
          } else if (seen || !s.running) {
            window.location.reload();    // our scan finished — show the result
          } else {
            setTimeout(poll, 3000);
          }
        })
        .catch(() => setTimeout(poll, 6000));
    };
    setTimeout(poll, 2500);
  }

  // ── refresh page: live progress polling ────────────────────────────────
  const scanStatus = document.getElementById("scanStatus");
  if (scanStatus) {
    const poll = () => {
      fetch("/api/refresh/status")
        .then((r) => r.json())
        .then((s) => {
          const bar = document.getElementById("scanBar");
          const log = document.getElementById("scanLog");
          if (bar && s.total) bar.style.width = `${Math.round((s.done / s.total) * 100)}%`;
          if (log) { log.textContent = s.log.join("\n"); log.scrollTop = log.scrollHeight; }
          const done = document.getElementById("scanDone");
          const total = document.getElementById("scanTotal");
          const current = document.getElementById("scanCurrent");
          if (done) done.textContent = s.done;
          if (total) total.textContent = s.total;
          if (current) current.innerHTML = s.current_item ? `— currently: <b>${s.current_item}</b>` : "";
          if (s.running) setTimeout(poll, 2000);
          else if (scanStatus.dataset.running === "1") window.location.reload();
        })
        .catch(() => setTimeout(poll, 5000));
    };
    if (scanStatus.dataset.running === "1") setTimeout(poll, 2000);
  }

  // ── compare: two to four items side by side ─────────────────────────────
  const cmpSearch = document.getElementById("cmpSearch");
  if (cmpSearch) {
    const MAX = 4;
    const chosenEl = document.getElementById("cmpChosen");
    const suggestEl = document.getElementById("cmpSuggest");
    const tableCard = document.getElementById("cmpTableCard");
    const chartCard = document.getElementById("cmpChartCard");
    const hintEl = document.getElementById("cmpHint");
    const factsCache = new Map();
    const histCache = new Map();
    let chosen = [];
    let cmpChart = null;

    // Deduped: add() refuses a repeat, but a hand-edited or shared URL comes
    // in through here instead, and ?ids=76178,76178 drew the set against
    // itself with every row a tie.
    const idsFromURL = () =>
      [...new Set((new URLSearchParams(location.search).get("ids") || "")
        .split(",").map((s) => s.trim()).filter(Boolean))].slice(0, MAX);

    // The comparison lives in the URL so it can be linked and reloaded. On the
    // static site the path is a file, so only the query is rewritten.
    const syncURL = () => {
      const u = new URL(location.href);
      if (chosen.length) u.searchParams.set("ids", chosen.join(","));
      else u.searchParams.delete("ids");
      history.replaceState(null, "", u);
    };

    const cached = (map, id, path) => {
      if (!map.has(id)) {
        map.set(id, fetch(apiURL(path))
          .then((r) => (r.ok ? r.json() : null))
          .catch(() => null));
      }
      return map.get(id);
    };
    const facts = (id) =>
      cached(factsCache, id, `/api/sets/${encodeURIComponent(id)}/facts`);
    const histOf = (id) =>
      cached(histCache, id, `/api/sets/${encodeURIComponent(id)}/history`);

    const sign = (ccy) => ({ ILS: "₪", USD: "$", EUR: "€", GBP: "£" }[ccy] || "");
    const money = (v, ccy) =>
      v == null ? "—" : `${sign(ccy)}${Math.round(v).toLocaleString()}`;
    const pct = (v) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(1)}%`);
    const DASH = "—";

    // Which way is "better" per row. null means the question does not apply — a
    // set is not better for being older, and marking one would be noise.
    const ROWS = [
      { label: "Year", get: (f) => f.year, fmt: (v) => v || DASH, best: null },
      { label: "Theme", get: (f) => f.theme, fmt: (v) => v || DASH, best: null },
      { label: "Parts", get: (f) => f.parts,
        fmt: (v) => (v ? v.toLocaleString() : DASH), best: "high" },
      { label: "Minifigures", get: (f) => f.minifigs,
        fmt: (v) => v || DASH, best: "high" },
      // Shown in the currency LEGO priced it in: converting an MSRP invents a
      // price that never existed on any shelf.
      { label: "Retail", get: (f) => f.retail,
        fmt: (v, f) => (f.retail_native == null ? DASH
          : `${sign(f.retail_ccy)}${f.retail_native}`), best: null },
      { label: "Value — new", get: (f) => f.value_new,
        fmt: (v, f) => money(v, f.currency), best: "high" },
      { label: "Value — used", get: (f) => f.value_used,
        fmt: (v, f) => money(v, f.currency), best: "high" },
      { label: "Per part", get: (f) => f.ppp,
        fmt: (v, f) => (v == null ? DASH : `${sign(f.currency)}${v.toFixed(2)}`),
        best: "low" },
      { label: "Growth / yr", get: (f) => f.growth, fmt: pct, best: "high" },
      { label: "vs retail / yr", get: (f) => f.vs_retail, fmt: pct, best: "high" },
      { label: "Forecast", get: (f) => f.forecast && f.forecast.value,
        fmt: (v, f) => (v == null ? DASH
          : `${money(v, f.currency)} by ${f.forecast.year}`), best: "high" },
      { label: "Part-out value", get: (f) => f.part_out_new,
        fmt: (v, f) => money(v, f.currency), best: "high" },
      { label: "Figures, % of set", get: (f) => f.fig_share && f.fig_share.pct,
        fmt: (v, f) => (v == null ? DASH
          : `${v.toFixed(0)}%${f.fig_share.priced < f.fig_share.figs
              ? ` (${f.fig_share.priced}/${f.fig_share.figs} priced)` : ""}`),
        best: "high" },
      { label: "Sold / 6mo", get: (f) => f.sales_6mo,
        fmt: (v) => (v == null ? DASH : v), best: "high" },
      { label: "Cheapest now", get: (f) => f.cheapest && f.cheapest.price,
        fmt: (v, f) => (v == null ? DASH
          : `${money(v, f.currency)} · ${f.cheapest.source}`), best: "low" },
      { label: "Status", get: (f) => f.phase,
        fmt: (v) => (v ? v.replace(/_/g, " ").toLowerCase() : DASH), best: null },
    ];

    const itemHref = (id) => (IS_STATIC
      ? `${BASE}set.html?id=${encodeURIComponent(id)}`
      : `/sets/${encodeURIComponent(id)}`);

    const renderChips = () => {
      chosenEl.innerHTML = chosen.map((id) =>
        `<span class="chip">${esc(id)}<a href="#" data-drop="${esc(id)}"
           style="margin-left:6px;text-decoration:none"
           aria-label="Remove ${esc(id)}">✕</a></span>`).join("");
      chosenEl.querySelectorAll("[data-drop]").forEach((a) =>
        a.addEventListener("click", (e) => {
          e.preventDefault();
          chosen = chosen.filter((x) => x !== a.dataset.drop);
          render();
        }));
    };

    const renderTable = (all) => {
      const head = `<tr><th></th>${all.map((f) =>
        `<th><a href="${itemHref(f.id)}" title="${esc(f.id)} ${esc(f.name)}"><img
             class="thumb" src="${setImg(f.id, f.item_type)}" alt="" loading="lazy"
             onerror="this.style.visibility='hidden'"><b>${esc(f.id)}</b></a>
           <div class="soft" style="font-weight:400">${esc((f.name || "").slice(0, 32))}</div>
         </th>`).join("")}</tr>`;

      const body = ROWS.map((row) => {
        const vals = all.map((f) => row.get(f));
        let winner = -1;
        if (row.best) {
          const nums = vals.map((v) => (typeof v === "number" ? v : null));
          const real = nums.filter((v) => v != null);
          // One number is not a comparison: nothing is better than a dash, and
          // a tie has no winner to mark.
          if (real.length > 1) {
            const target = row.best === "high" ? Math.max(...real) : Math.min(...real);
            if (real.filter((v) => v === target).length === 1) winner = nums.indexOf(target);
          }
        }
        return `<tr><th style="text-align:left;white-space:nowrap">${row.label}</th>${
          all.map((f, i) => `<td class="num"${i === winner
            ? ' style="color:var(--ok);font-weight:600"' : ""}>${row.fmt(vals[i], f)}${
            i === winner ? " ●" : ""}</td>`).join("")}</tr>`;
      }).join("");

      tableCard.querySelector("tbody").innerHTML = head + body;
      tableCard.hidden = false;
    };

    const renderChart = (all, series) => {
      if (cmpChart) { cmpChart.destroy(); cmpChart = null; }
      if (!window.Chart) return;
      const palette = ["--s1", "--s2", "--s3", "--s5"];
      const sets = all.map((f, i) => {
        const pts = ((series[i] || {}).series || {}).blended || [];
        return pts.length ? {
          // The scan count belongs beside the name: with a few months of
          // history a line can be two points, and two points drawn as a line
          // look like a trend.
          label: `${f.id} ${(f.name || "").slice(0, 18)} · ${pts.length} scan${
            pts.length === 1 ? "" : "s"}`,
          data: pts.map((p) => ({ x: p.t, y: p.v })),
          borderColor: css(palette[i]) || "#4f8cff",
          backgroundColor: css(palette[i]) || "#4f8cff",
          borderWidth: 2, pointRadius: pts.length < 15 ? 3 : 0,
          tension: 0.25, spanGaps: true, fill: false,
        } : null;
      }).filter(Boolean);

      if (!sets.length) { chartCard.hidden = true; return; }
      chartCard.hidden = false;
      cmpChart = new Chart(document.getElementById("cmpChart"), {
        type: "line",
        data: { datasets: sets },
        options: {
          responsive: true, maintainAspectRatio: false,
          interaction: { mode: "nearest", intersect: false },
          scales: {
            x: { type: "time", grid: { display: false },
                 ticks: { autoSkip: true, maxTicksLimit: 8 } },
            y: { ticks: { callback: (v) => v.toLocaleString() } },
          },
        },
      });
    };

    const render = () => {
      syncURL();
      renderChips();
      if (chosen.length < 2) {
        tableCard.hidden = true;
        chartCard.hidden = true;
        if (cmpChart) { cmpChart.destroy(); cmpChart = null; }
        hintEl.textContent = chosen.length
          ? "One more to compare against."
          : "Pick at least two. Only scanned items carry prices — the rest show what "
            + "the catalog knows and dashes for the rest.";
        return;
      }
      hintEl.textContent = "";
      Promise.all(chosen.map(facts)).then((all) => {
        const ok = all.filter(Boolean);
        if (ok.length < 2) {
          tableCard.hidden = true;
          chartCard.hidden = true;
          hintEl.textContent = "Those items are not in the catalog.";
          return;
        }
        renderTable(ok);
        Promise.all(ok.map((f) => histOf(f.id))).then((h) => renderChart(ok, h));
      });
    };

    const add = (id) => {
      if (chosen.includes(id) || chosen.length >= MAX) return;
      chosen.push(id);
      cmpSearch.value = "";
      suggestEl.hidden = true;
      render();
    };

    cmpSearch.addEventListener("input", () => {
      const q = cmpSearch.value.trim().toLowerCase();
      if (q.length < 2) { suggestEl.hidden = true; return; }
      loadCatalog().then((items) => {
        const hits = [];
        for (const i of items) {
          if (i.id.toLowerCase().includes(q)
              || (i.name || "").toLowerCase().includes(q)) hits.push(i);
          if (hits.length >= 8) break;
        }
        if (!hits.length) { suggestEl.hidden = true; return; }
        suggestEl.innerHTML = hits.map((i) =>
          `<a href="#" data-add="${esc(i.id)}"><b>${esc(i.id)}</b> ${esc(i.name)}
             <span>${esc(i.theme || (i.type === "M" ? "minifig" : ""))}</span></a>`).join("");
        suggestEl.hidden = false;
        suggestEl.querySelectorAll("[data-add]").forEach((a) =>
          a.addEventListener("click", (e) => { e.preventDefault(); add(a.dataset.add); }));
      });
    });
    cmpSearch.addEventListener("keydown", (e) => {
      if (e.key === "Escape") suggestEl.hidden = true;
    });
    document.addEventListener("click", (e) => {
      if (!suggestEl.contains(e.target) && e.target !== cmpSearch) suggestEl.hidden = true;
    });
    document.getElementById("cmpClear").addEventListener("click", () => {
      chosen = [];
      render();
    });

    chosen = idsFromURL();
    render();
  }
})();

/* PWA: register the service worker. Live server: /sw.js (a route that stamps
   the version). Static export: sw.js sits at the export root, so climb there
   with the page's own base prefix — the scope then covers the whole site
   wherever it is mounted (github.io/<repo>/, a custom domain, /docs). */
if ("serviceWorker" in navigator) {
  var swUrl = document.body.dataset.static === "1"
    ? (document.body.dataset.base || "") + "sw.js"
    : "/sw.js";
  navigator.serviceWorker.register(swUrl).catch(function () {
    /* http:// LAN preview or an old browser — the site works without it. */
  });
}
