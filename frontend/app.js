// CropWatch dashbord-logikk. Region-agnostisk: alt drives av /api-svarene.

const regionSel = document.getElementById("region");
const areaSel = document.getElementById("area");
const cardsEl = document.getElementById("cards");
const updatedEl = document.getElementById("updated");

let regions = [];
let currentStatus = null;
let map, markerLayer;
let charts = {};

const STATUS_TEXT = { green: "Normalt / bra", yellow: "Følg med", red: "Avvik", unknown: "Ingen data" };

// Henter data. På GitHub Pages finnes ferdige JSON-filer (data/...); lokalt med
// uvicorn finnes de ikke, og vi faller tilbake til det levende API-et.
async function loadJson(staticPath, apiPath) {
  try {
    const r = await fetch(staticPath, { cache: "no-store" });
    if (r.ok) return await r.json();
  } catch (e) { /* ingen statisk fil – bruk API */ }
  return fetch(apiPath).then((r) => r.json());
}
const getRegions = () => loadJson("data/regions.json", "/api/regions");
const getStatus = (id) => loadJson(`data/${id}.json`, `/api/regions/${id}/status`);

async function init() {
  map = L.map("map").setView([-15, -50], 4);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "© OpenStreetMap", maxZoom: 12,
  }).addTo(map);
  markerLayer = L.layerGroup().addTo(map);

  regions = await getRegions();
  regionSel.innerHTML = regions
    .map((r) => `<option value="${r.id}">${r.name}</option>`)
    .join("");
  regionSel.onchange = onRegionChange;
  areaSel.onchange = render;
  if (regions.length) await onRegionChange();
}

async function onRegionChange() {
  const region = regions.find((r) => r.id === regionSel.value);
  areaSel.innerHTML = region.areas
    .map((a) => `<option value="${a.id}">${a.name}</option>`)
    .join("");

  // Tegn kartmarkører for områdene
  markerLayer.clearLayers();
  const pts = [];
  region.areas.forEach((a) => {
    const m = L.marker([a.lat, a.lon]).addTo(markerLayer).bindTooltip(a.name);
    m.on("click", () => { areaSel.value = a.id; render(); });
    pts.push([a.lat, a.lon]);
  });
  if (pts.length) map.fitBounds(pts, { padding: [40, 40], maxZoom: 7 });

  currentStatus = await getStatus(region.id);
  updatedEl.textContent = currentStatus.last_run.ndvi
    ? `Sist hentet – NDVI: ${fmtDate(currentStatus.last_run.ndvi)}, vær: ${fmtDate(currentStatus.last_run.weather)}`
    : "Ingen henting ennå – data fylles inn i bakgrunnen.";
  render();
}

function render() {
  if (!currentStatus) return;
  const a = currentStatus.areas[areaSel.value];
  if (!a) return;
  renderCards(a);
  renderNdviChart(a.ndvi);
  renderRainChart(a.rainfall);
  renderGddChart(a.gdd);
}

function card(title, status, big, sub) {
  return `<div class="card ${status}">
    <h3>${title}</h3>
    <div class="big">${big}</div>
    <div class="sub">${sub} <span class="badge ${status}">${STATUS_TEXT[status]}</span></div>
  </div>`;
}

function renderCards(a) {
  const ndvi = a.ndvi.latest;
  const rain = a.rainfall;
  cardsEl.innerHTML = [
    card("Vegetasjon (NDVI)", a.ndvi.status,
      ndvi ? ndvi.value.toFixed(2) : "–",
      ndvi && ndvi.anomaly != null ? `Avvik ${signed(ndvi.anomaly)} fra normalt` : "—"),
    card("Nedbør hittil i år", rain.status,
      rain.ratio_to_normal != null ? `${Math.round(rain.ratio_to_normal * 100)}%` : "–",
      "av normal mengde"),
    card("Varmestress (30 d)", a.heat_stress.status,
      `${a.heat_stress.hot_days} dg`,
      `over ${a.heat_stress.threshold_c}°C`),
    card("Tørkestress (30 d)", a.drought_stress.status,
      `${a.drought_stress.longest_dry_streak} dg`,
      "lengste tørre periode"),
  ].join("");
}

function destroyChart(key) { if (charts[key]) { charts[key].destroy(); delete charts[key]; } }

function renderNdviChart(ndvi) {
  destroyChart("ndvi");
  const labels = ndvi.series.map((p) => p.date);
  charts.ndvi = new Chart(document.getElementById("ndviChart"), {
    type: "line",
    data: {
      labels,
      datasets: [
        { label: "NDVI (målt)", data: ndvi.series.map((p) => p.value),
          borderColor: "#2563eb", backgroundColor: "#2563eb", tension: .25, pointRadius: 0 },
        { label: "Historisk snitt", data: ndvi.series.map((p) => p.baseline),
          borderColor: "#94a3b8", borderDash: [6, 4], tension: .25, pointRadius: 0 },
      ],
    },
    options: chartOpts("NDVI"),
  });
}

function renderRainChart(rain) {
  destroyChart("rain");
  charts.rain = new Chart(document.getElementById("rainChart"), {
    type: "line",
    data: {
      labels: rain.series.map((p) => p.doy),
      datasets: [
        { label: "I år (mm)", data: rain.series.map((p) => p.cumulative_mm),
          borderColor: "#0891b2", tension: .2, pointRadius: 0 },
        { label: "Normal (mm)", data: rain.series.map((p) => p.baseline_mm),
          borderColor: "#94a3b8", borderDash: [6, 4], tension: .2, pointRadius: 0 },
      ],
    },
    options: chartOpts("mm akkumulert", "Dag i året"),
  });
}

function renderGddChart(gdd) {
  destroyChart("gdd");
  charts.gdd = new Chart(document.getElementById("gddChart"), {
    type: "line",
    data: {
      labels: gdd.series.map((p) => p.date),
      datasets: [
        { label: `GDD ${gdd.year}`, data: gdd.series.map((p) => p.gdd_cumulative),
          borderColor: "#16a34a", tension: .2, pointRadius: 0, fill: true,
          backgroundColor: "rgba(22,163,74,.08)" },
      ],
    },
    options: chartOpts("Akkumulert GDD"),
  });
}

function chartOpts(yTitle, xTitle) {
  return {
    responsive: true, interaction: { mode: "index", intersect: false },
    scales: {
      y: { title: { display: true, text: yTitle } },
      x: { title: { display: !!xTitle, text: xTitle || "" }, ticks: { maxTicksLimit: 8 } },
    },
    plugins: { legend: { position: "bottom" } },
  };
}

const signed = (n) => (n >= 0 ? "+" : "") + n.toFixed(2);
const fmtDate = (s) => (s ? new Date(s).toLocaleDateString("no-NO") : "–");

init();
