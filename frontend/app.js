// CropWatch – mørkt kontrollrom. Region-agnostisk: alt drives av data fra API/JSON.

const $ = (id) => document.getElementById(id);
const regionSel = $("region");
const areaSel = $("area");

let regions = [];
let currentStatus = null;
let map, markerLayer;
let charts = {};

const COLOR = { green:"#22c55e", yellow:"#eab308", red:"#ef4444", unknown:"#64748b",
                blue:"#38bdf8", cyan:"#22d3ee", amber:"#f59e0b", grey:"#64748b" };
const STATUS_TEXT = { green:"Normalt / bra", yellow:"Følg med", red:"Avvik", unknown:"Ingen data" };

// ---- datakilde: statiske filer (GitHub Pages) først, ellers levende API ----
async function loadJson(staticPath, apiPath) {
  try { const r = await fetch(staticPath, { cache:"no-store" }); if (r.ok) return await r.json(); }
  catch (e) {}
  return fetch(apiPath).then((r) => r.json());
}
const getRegions = () => loadJson("data/regions.json", "/api/regions");
const getStatus = (id) => loadJson(`data/${id}.json`, `/api/regions/${id}/status`);

// ---- Chart.js mørkt tema + glød-plugin -------------------------------------
Chart.defaults.color = "#8b9bb4";
Chart.defaults.font.family = "ui-sans-serif, system-ui, sans-serif";
Chart.register({
  id: "glow",
  beforeDatasetDraw(c, args) {
    if (args.meta.dataset && c.data.datasets[args.index]?.glow) {
      const ctx = c.ctx; ctx.save();
      ctx.shadowColor = c.data.datasets[args.index].borderColor;
      ctx.shadowBlur = 12;
    }
  },
  afterDatasetDraw(c, args) {
    if (c.data.datasets[args.index]?.glow) c.ctx.restore();
  },
});

async function init() {
  map = L.map("map", { attributionControl:false }).setView([-15,-50], 4);
  L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", { maxZoom:12 }).addTo(map);
  markerLayer = L.layerGroup().addTo(map);

  regions = await getRegions();
  regionSel.innerHTML = regions.map((r) => `<option value="${r.id}">${r.name}</option>`).join("");
  regionSel.onchange = onRegionChange;
  areaSel.onchange = render;
  if (regions.length) await onRegionChange();
}

async function onRegionChange() {
  const region = regions.find((r) => r.id === regionSel.value);
  areaSel.innerHTML = region.areas.map((a) => `<option value="${a.id}">${a.name}</option>`).join("");

  markerLayer.clearLayers();
  const pts = [];
  region.areas.forEach((a) => {
    const m = L.circleMarker([a.lat,a.lon], { radius:8, color:"#22d3ee", fillColor:"#22d3ee", fillOpacity:.7, weight:2 })
      .addTo(markerLayer).bindTooltip(a.name);
    m.on("click", () => { areaSel.value = a.id; render(); });
    pts.push([a.lat,a.lon]);
  });
  if (pts.length) map.fitBounds(pts, { padding:[40,40], maxZoom:7 });

  currentStatus = await getStatus(region.id);
  const lr = currentStatus.last_run || {};
  $("meta").innerHTML = lr.ndvi
    ? `NDVI: ${fmtDate(lr.ndvi)}<br>vær: ${fmtDate(lr.weather)}`
    : "Data fylles inn …";
  render();
}

function render() {
  if (!currentStatus) return;
  const a = currentStatus.areas[areaSel.value];
  if (!a) return;
  const enso = currentStatus.enso || {};
  const cycle = currentStatus.cycle || {};
  renderNarrative(a, enso, cycle);
  renderHero(a, enso, cycle);
  renderCycle(cycle);
  renderNdviChart(a.ndvi);
  renderEnsoChart(enso);
  renderRainChart(a.rainfall);
  renderGddChart(a.gdd);
  renderStress(a);
}

// ---- Fortelling: "hva har skjedd" -----------------------------------------
function renderNarrative(a, enso, cycle) {
  const veg = { green:"normal eller bedre enn", yellow:"litt svakere enn", red:"klart svakere enn",
                unknown:"ukjent mot" }[a.ndvi.status] || "ukjent mot";
  const ndvi = a.ndvi.latest;
  const ndviTxt = ndvi ? `NDVI ${ndvi.value.toFixed(2)}, ${signed(ndvi.anomaly)} mot snittet` : "uten data";
  let cycleTxt = "";
  if (cycle.current) cycleTxt = ` Vi er i fasen <b>${cycle.current.phase}</b> (måned ${cycle.current.month_in_phase} av ${cycle.current.phase_length}).`;
  let ensoTxt = "";
  if (enso.state && enso.state !== "Ukjent") {
    const s = enso.strength ? ` ${enso.strength}` : "";
    ensoTxt = ` El Niño/La Niña er <b>${enso.state}${s}</b> (ONI ${signed(enso.latest_oni)}, ${enso.trend}) – det påvirker nedbøren her.`;
  }
  $("narrative").innerHTML =
    `Vegetasjonen er <b>${veg} normalt</b> for årstiden (${ndviTxt}).${cycleTxt}${ensoTxt}`;
}

// ---- Hero-fliser -----------------------------------------------------------
function tile(el, color, label, value, stateText, sub) {
  el.style.setProperty("--c", color);
  el.innerHTML =
    `<div class="label">${label}</div>
     <div class="value">${value}</div>
     <div class="state"><span class="led"></span>${stateText}</div>
     <div class="sub">${sub}</div>`;
}
function renderHero(a, enso, cycle) {
  const ndvi = a.ndvi.latest;
  tile($("tileVeg"), COLOR[a.ndvi.status] || COLOR.unknown, "Vegetasjon",
    ndvi ? ndvi.value.toFixed(2) : "–", STATUS_TEXT[a.ndvi.status],
    ndvi && ndvi.anomaly!=null ? `avvik ${signed(ndvi.anomaly)} mot normalt` : "—");

  const c = cycle.current;
  tile($("tileCycle"), c ? c.color : COLOR.unknown, "Syklus",
    c ? `${c.month_in_phase} / ${c.phase_length}` : "–",
    c ? c.phase : "ukjent", c ? "måned i fasen" : "—");

  const ensoColor = enso.state==="El Niño" ? COLOR.red : enso.state==="La Niña" ? COLOR.blue : COLOR.green;
  tile($("tileEnso"), ensoColor, "El Niño / La Niña",
    enso.latest_oni!=null ? signed(enso.latest_oni) : "–",
    enso.state || "ukjent", enso.strength ? `${enso.strength}, ${enso.trend}` : `trend: ${enso.trend||"—"}`);
}

// ---- Syklus-tidslinje ------------------------------------------------------
function renderCycle(cycle) {
  const letters = ["J","F","M","A","M","J","J","A","S","O","N","D"];
  const bar = $("cycleBar");
  bar.innerHTML = (cycle.timeline||[]).map((t,i) =>
    `<div class="mon" style="background:${t.color}" title="${t.phase}">${letters[i]}</div>`).join("");
  if (cycle.today_fraction!=null) {
    const mark = document.createElement("div");
    mark.className = "today"; mark.style.left = (cycle.today_fraction*100)+"%";
    bar.appendChild(mark);
  }
  $("cycleLegend").innerHTML = (cycle.phases||[]).map((p) =>
    `<span><i style="background:${p.color}"></i>${p.name}</span>`).join("");
  if (cycle.current) $("cycleTitle").textContent = `Nå: ${cycle.current.phase}`;
}

// ---- Grafer ----------------------------------------------------------------
function destroy(k){ if (charts[k]){ charts[k].destroy(); delete charts[k]; } }
const GRID = { color:"rgba(255,255,255,.05)" };

function renderNdviChart(ndvi) {
  destroy("ndvi");
  charts.ndvi = new Chart($("ndviChart"), {
    type:"line",
    data:{ labels: ndvi.series.map(p=>p.date),
      datasets:[
        { label:"Historisk snitt", data:ndvi.series.map(p=>p.baseline),
          borderColor:"#64748b", borderDash:[6,4], borderWidth:1.5, pointRadius:0, tension:.3 },
        { label:"Målt NDVI", data:ndvi.series.map(p=>p.value), glow:true,
          borderColor:"#22d3ee", borderWidth:2.5, pointRadius:0, tension:.3,
          fill:"-1", backgroundColor:"rgba(34,211,238,.10)" },
      ]},
    options: baseOpts("NDVI"),
  });
}

function flat(val, n){ return new Array(n).fill(val); }
function renderEnsoChart(enso) {
  destroy("enso");
  const s = enso.series||[]; const n = s.length;
  $("ensoTitle").textContent = `ONI-indeks – nå: ${enso.state||"–"} (${signed(enso.latest_oni||0)})`;
  charts.enso = new Chart($("ensoChart"), {
    type:"line",
    data:{ labels: s.map(p=>p.date),
      datasets:[
        { label:"El Niño-grense", data:flat(0.5,n), borderColor:"rgba(239,68,68,.6)", borderDash:[5,4], borderWidth:1, pointRadius:0 },
        { label:"La Niña-grense", data:flat(-0.5,n), borderColor:"rgba(56,189,248,.6)", borderDash:[5,4], borderWidth:1, pointRadius:0 },
        { label:"ONI", data:s.map(p=>p.oni), glow:true, borderColor:"#f8fafc", borderWidth:2.5,
          pointRadius:0, tension:.3, fill:"origin", backgroundColor:"rgba(248,250,252,.06)" },
      ]},
    options: { ...baseOpts("ONI"), scales:{ x:{ grid:GRID, ticks:{ maxTicksLimit:6 } },
      y:{ grid:GRID, suggestedMin:-2.5, suggestedMax:2.5, title:{display:true,text:"ONI"} } } },
  });
}

function renderRainChart(rain) {
  destroy("rain");
  charts.rain = new Chart($("rainChart"), {
    type:"line",
    data:{ labels: rain.series.map(p=>p.doy),
      datasets:[
        { label:"Normal", data:rain.series.map(p=>p.baseline_mm), borderColor:"#64748b",
          borderDash:[6,4], borderWidth:1.5, pointRadius:0, tension:.2 },
        { label:"I år", data:rain.series.map(p=>p.cumulative_mm), glow:true, borderColor:"#38bdf8",
          borderWidth:2.5, pointRadius:0, tension:.2, fill:"-1", backgroundColor:"rgba(56,189,248,.10)" },
      ]},
    options: baseOpts("mm", "Dag i året"),
  });
}

function renderGddChart(gdd) {
  destroy("gdd");
  charts.gdd = new Chart($("gddChart"), {
    type:"line",
    data:{ labels: gdd.series.map(p=>p.date),
      datasets:[{ label:`GDD ${gdd.year}`, data:gdd.series.map(p=>p.gdd_cumulative), glow:true,
        borderColor:"#22c55e", borderWidth:2.5, pointRadius:0, tension:.2, fill:"origin",
        backgroundColor:"rgba(34,197,94,.08)" }]},
    options: baseOpts("GDD"),
  });
}

function baseOpts(yTitle, xTitle) {
  return { responsive:true, interaction:{ mode:"index", intersect:false },
    scales:{ x:{ grid:GRID, title:{ display:!!xTitle, text:xTitle||"" }, ticks:{ maxTicksLimit:8 } },
             y:{ grid:GRID, title:{ display:true, text:yTitle } } },
    plugins:{ legend:{ position:"bottom", labels:{ boxWidth:12, usePointStyle:true } } } };
}

// ---- Stress-fliser ---------------------------------------------------------
function mini(color, label, value, sub) {
  return `<div class="mini" style="--c:${color}"><div class="l">${label}</div>
          <div class="v">${value}</div><div class="s">${sub}</div></div>`;
}
function renderStress(a) {
  $("stress").innerHTML =
    mini(COLOR[a.heat_stress.status], "Varmedager", `${a.heat_stress.hot_days}`, `over ${a.heat_stress.threshold_c}°C`) +
    mini(COLOR[a.drought_stress.status], "Tørke-rekke", `${a.drought_stress.longest_dry_streak} dg`, "lengste tørre periode") +
    mini(COLOR.green, "Varmesum", `${Math.round(a.gdd.total_gdd)}`, `GDD i ${a.gdd.year}`);
}

const signed = (n) => (n==null ? "–" : (n>=0?"+":"") + Number(n).toFixed(2));
const fmtDate = (s) => (s ? new Date(s).toLocaleDateString("no-NO") : "–");

init();
