// CropWatch – mørkt kontrollrom. Region-agnostisk. Viser alt som "mot normalt".

const $ = (id) => document.getElementById(id);
const regionSel = $("region");
const areaSel = $("area");

let regions = [];
let currentStatus = null;
let map, markerLayer;
let charts = {};

async function loadJson(staticPath, apiPath) {
  try { const r = await fetch(staticPath, { cache:"no-store" }); if (r.ok) return await r.json(); } catch (e) {}
  return fetch(apiPath).then((r) => r.json());
}
const getRegions = () => loadJson("data/regions.json", "/api/regions");
const getStatus = (id) => loadJson(`data/${id}.json`, `/api/regions/${id}/status`);

Chart.defaults.color = "#8b9bb4";
Chart.defaults.font.family = "ui-sans-serif, system-ui, sans-serif";

// Fargede soner bak El Niño-grafen (rød = El Niño, blå = La Niña).
Chart.register({
  id:"ensoZones",
  beforeDatasetsDraw(c) {
    if (c.canvas.id !== "ensoChart") return;
    const { ctx, chartArea:a, scales:{ y } } = c;
    const yhi = y.getPixelForValue(0.5), ylo = y.getPixelForValue(-0.5);
    ctx.save();
    ctx.fillStyle = "rgba(239,68,68,.12)";
    ctx.fillRect(a.left, a.top, a.right-a.left, yhi-a.top);
    ctx.fillStyle = "rgba(56,189,248,.12)";
    ctx.fillRect(a.left, ylo, a.right-a.left, a.bottom-ylo);
    ctx.restore();
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
  $("meta").innerHTML = lr.ndvi ? `oppdatert ${fmtDate(lr.ndvi)}` : "data fylles inn …";
  render();
}

function render() {
  if (!currentStatus) return;
  const a = currentStatus.areas[areaSel.value];
  if (!a) return;
  renderVegTile(a.ndvi);
  renderCycleTile(currentStatus.cycle || {});
  renderEnsoTile(currentStatus.enso || {});
  renderCycle(currentStatus.cycle || {});
  renderDeviationChart(a.ndvi);
  renderEnsoChart(currentStatus.enso || {});
  renderRainChart(a.rainfall);
  renderStress(a);
}

const clamp = (v,lo,hi) => Math.max(lo, Math.min(hi, v));

// ---- Hero-fliser med målere -----------------------------------------------
function renderVegTile(ndvi) {
  const an = ndvi.latest ? ndvi.latest.anomaly : null;
  let word = "Ukjent", color = "#64748b", pos = 50;
  if (an != null) {
    pos = 50 + clamp(an/0.15, -1, 1) * 50;
    if (an >= 0.05) { word = "Sterk"; color = "#22c55e"; }
    else if (an > -0.05) { word = "Normal"; color = "#22c55e"; }
    else if (an > -0.12) { word = "Litt svak"; color = "#eab308"; }
    else { word = "Svak"; color = "#ef4444"; }
  }
  $("tileVeg").style.setProperty("--c", color);
  $("tileVeg").innerHTML =
    `<div class="label">Plantehelse</div>
     <div class="bigword">${word}</div>
     <div class="gauge" style="--gl:#ef4444;--gm:#334155;--gr:#22c55e">
       <div class="tick"></div><div class="marker" style="left:${pos}%"></div></div>
     <div class="ends"><span>svakere</span><span>normalt</span><span>sterkere</span></div>`;
}

function renderEnsoTile(enso) {
  const oni = enso.latest_oni;
  const color = enso.state==="El Niño" ? "#ef4444" : enso.state==="La Niña" ? "#38bdf8" : "#22c55e";
  const pos = oni!=null ? 50 + clamp(oni/2.0, -1, 1) * 50 : 50;
  $("tileEnso").style.setProperty("--c", color);
  $("tileEnso").innerHTML =
    `<div class="label">El Niño / La Niña</div>
     <div class="bigword">${enso.state || "Ukjent"}</div>
     <div class="gauge" style="--gl:#38bdf8;--gm:#334155;--gr:#f59e0b">
       <div class="tick"></div><div class="marker" style="left:${pos}%"></div></div>
     <div class="ends"><span>La Niña</span><span>nøytral</span><span>El Niño</span></div>`;
}

function renderCycleTile(cycle) {
  const c = cycle.current;
  const color = c ? c.color : "#64748b";
  const segs = (cycle.timeline||[]).map(t => `<div class="seg" style="background:${t.color}"></div>`).join("");
  const now = cycle.today_fraction!=null ? `<div class="now" style="left:${cycle.today_fraction*100}%"></div>` : "";
  $("tileCycle").style.setProperty("--c", color);
  $("tileCycle").innerHTML =
    `<div class="label">Vekstsyklus</div>
     <div class="bigword">${c ? c.phase.replace(/\s*\(.*\)/,"") : "–"}</div>
     <div class="phasebar">${segs}${now}</div>
     <div class="ends"><span>jan</span><span>i dag</span><span>des</span></div>`;
}

// ---- Syklus-tidslinje (full) ----------------------------------------------
function renderCycle(cycle) {
  const letters = ["J","F","M","A","M","J","J","A","S","O","N","D"];
  const bar = $("cycleBar");
  bar.innerHTML = (cycle.timeline||[]).map((t,i) =>
    `<div class="mon" style="background:${t.color}" title="${t.phase}">${letters[i]}</div>`).join("");
  if (cycle.today_fraction!=null) {
    const m = document.createElement("div"); m.className="today";
    m.style.left=(cycle.today_fraction*100)+"%"; bar.appendChild(m);
  }
  $("cycleLegend").innerHTML = (cycle.phases||[]).map(p =>
    `<span><i style="background:${p.color}"></i>${p.name}</span>`).join("");
  if (cycle.current) $("cycleTitle").textContent = `Vekstsyklus — nå: ${cycle.current.phase}`;
}

// ---- Grafer ----------------------------------------------------------------
function destroy(k){ if (charts[k]){ charts[k].destroy(); delete charts[k]; } }
const GRID = { color:"rgba(255,255,255,.05)" };

// Plantehelse som AVVIK mot normalt: søyler opp (grønn) = friskere enn vanlig.
function renderDeviationChart(ndvi) {
  destroy("ndvi");
  const s = (ndvi.series||[]).filter(p => p.anomaly!=null).slice(-26);
  charts.ndvi = new Chart($("ndviChart"), {
    type:"bar",
    data:{ labels: s.map(p=>p.date),
      datasets:[{ data: s.map(p=>p.anomaly),
        backgroundColor: s.map(p => p.anomaly>=0 ? "rgba(34,197,94,.85)" : "rgba(239,68,68,.85)"),
        borderRadius:3 }]},
    options:{ responsive:true,
      scales:{ x:{ grid:{display:false}, ticks:{ maxTicksLimit:6 } },
               y:{ grid:GRID, ticks:{ callback:v => v>0?"over":v<0?"under":"normalt" } } },
      plugins:{ legend:{display:false},
        tooltip:{ callbacks:{ label:(c)=> (c.raw>=0?"+":"")+c.raw.toFixed(2)+" mot normalt" } } } },
  });
}

function renderEnsoChart(enso) {
  destroy("enso");
  const s = enso.series||[];
  $("ensoTitle").textContent = `El Niño gjennom tiden — nå: ${enso.state||"–"}`;
  charts.enso = new Chart($("ensoChart"), {
    type:"line",
    data:{ labels: s.map(p=>p.date),
      datasets:[{ data: s.map(p=>p.oni), borderColor:"#f8fafc", borderWidth:2.5,
        pointRadius:0, tension:.3 }]},
    options:{ responsive:true, interaction:{mode:"index",intersect:false},
      scales:{ x:{ grid:GRID, ticks:{ maxTicksLimit:6 } },
               y:{ grid:GRID, suggestedMin:-2.5, suggestedMax:2.5,
                   ticks:{ callback:v => v>=0.5?"El Niño":v<=-0.5?"La Niña":v===0?"nøytral":"" } } },
      plugins:{ legend:{display:false} } },
  });
}

function renderRainChart(rain) {
  destroy("rain");
  charts.rain = new Chart($("rainChart"), {
    type:"line",
    data:{ labels: rain.series.map(p=>p.doy),
      datasets:[
        { label:"normalt", data:rain.series.map(p=>p.baseline_mm), borderColor:"#64748b",
          borderDash:[6,4], borderWidth:1.5, pointRadius:0, tension:.2 },
        { label:"i år", data:rain.series.map(p=>p.cumulative_mm), borderColor:"#38bdf8",
          borderWidth:2.5, pointRadius:0, tension:.2, fill:"-1", backgroundColor:"rgba(56,189,248,.10)" },
      ]},
    options:{ responsive:true, interaction:{mode:"index",intersect:false},
      scales:{ x:{ grid:GRID, display:false, ticks:{display:false} }, y:{ grid:GRID } },
      plugins:{ legend:{ position:"bottom", labels:{ boxWidth:12, usePointStyle:true } } } },
  });
}

// ---- Stress-brikker --------------------------------------------------------
const STATUS_COLOR = { green:"#22c55e", yellow:"#eab308", red:"#ef4444", unknown:"#64748b" };
function chip(color, icon, big, lbl) {
  return `<div class="chip" style="--c:${color}"><div class="ic">${icon}</div>
    <div><div class="big">${big}</div><div class="lbl">${lbl}</div></div></div>`;
}
function renderStress(a) {
  $("stress").innerHTML =
    chip(STATUS_COLOR[a.heat_stress.status], "🔥",
      `${a.heat_stress.hot_days} dager`, `med ekstrem varme (over ${a.heat_stress.threshold_c}°C)`) +
    chip(STATUS_COLOR[a.drought_stress.status], "💧",
      `${a.drought_stress.longest_dry_streak} dager`, "lengste periode uten regn");
}

const fmtDate = (s) => (s ? new Date(s).toLocaleDateString("no-NO") : "–");
init();
