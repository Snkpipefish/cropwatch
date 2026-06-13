// Sukker no. 11 – driveroversikt. Leser data/sugar11.json (bygget lokalt).

const $ = (id) => document.getElementById(id);
const GREEN = "#22c55e", RED = "#ef4444", MUTED = "#64748b", AMBER = "#f59e0b";

const DIR_TXT = { 1: "▲ presser opp", "-1": "▼ presser ned", 0: "— nøytral" };
const DIR_CLS = { 1: "up", "-1": "down", 0: "flat" };

const NAMES = {
  brl: "Brasil-valuta (real)", ethanol: "Etanol vs sukker",
  unica: "Brasil-produksjon", india: "India-eksport",
  cot: "Spekulanter", enso: "El Niño / La Niña",
  ndvi: "Plantehelse (satellitt)", frost: "Frost-vakt Brasil",
  season: "Sesong", bedrock: "Bedrock-dommen",
};

async function init() {
  let d;
  try {
    const r = await fetch("data/sugar11.json", { cache: "no-store" });
    if (!r.ok) throw new Error();
    d = await r.json();
  } catch (e) {
    $("grid").innerHTML = `<div class="panel"><div class="err">Ingen data ennå.
      Kjør oppdateringsskriptet lokalt: <b>.venv/bin/python scripts/export_sugar11.py --push</b></div></div>`;
    return;
  }
  $("meta").textContent = "bygget " + (d.built_utc || "").replace("T", " ").slice(0, 16);
  renderPrice(d.price);
  renderPressure(d.pressure || {});
  renderDrivers(d);
}

// ---- ferskhet ---------------------------------------------------------------
function freshness(asof) {
  if (!asof) return `<span class="fresh old">ingen dato</span>`;
  const days = Math.floor((Date.now() - new Date(asof)) / 864e5);
  const cls = days <= 14 ? "" : days <= 45 ? "warn" : "old";
  const txt = days <= 0 ? "i dag" : days === 1 ? "1 dag" : `${days} dager`;
  return `<span class="fresh ${cls}">${asof.slice(0, 10)} · ${txt}</span>`;
}

// ---- topp: pris -------------------------------------------------------------
function renderPrice(p) {
  const el = $("pricePanel");
  if (!p || p.error) { el.innerHTML = `<div class="err">Pris utilgjengelig</div>`; return; }
  const chg = p.chg_5d_pct;
  const chgTxt = chg == null ? "" : `${chg >= 0 ? "+" : ""}${chg}% siste 5 dager`;
  el.style.setProperty("--c", chg >= 0 ? GREEN : RED);
  el.innerHTML = `
    <div class="label">Pris — sukker no. 11 (cent/lb)</div>
    <div class="big">${p.close.toFixed(2)}</div>
    <div class="sub">${chgTxt} · nivå siste år: ${pctWord(p.pct_1y)}</div>
    <svg id="spark" viewBox="0 0 300 70" preserveAspectRatio="none"></svg>
    ${freshness(p.asof)}`;
  drawSpark(p.spark || []);
}

function drawSpark(vals) {
  if (vals.length < 2) return;
  const min = Math.min(...vals), max = Math.max(...vals), span = max - min || 1;
  const pts = vals.map((v, i) =>
    `${(i / (vals.length - 1)) * 300},${66 - ((v - min) / span) * 60}`).join(" ");
  const up = vals[vals.length - 1] >= vals[0];
  $("spark").innerHTML =
    `<polyline points="${pts}" fill="none" stroke="${up ? GREEN : RED}"
       stroke-width="2.2" stroke-linejoin="round"
       style="filter:drop-shadow(0 0 5px ${up ? GREEN : RED})"/>`;
}

// ---- topp: samlet press ------------------------------------------------------
function renderPressure(pr) {
  const ups = Object.entries(pr).filter(([, v]) => v > 0).map(([k]) => k);
  const downs = Object.entries(pr).filter(([, v]) => v < 0).map(([k]) => k);
  const flats = Object.entries(pr).filter(([, v]) => v === 0).map(([k]) => k);
  const total = ups.length + downs.length + flats.length || 1;
  const el = $("pressPanel");
  el.style.setProperty("--c", ups.length > downs.length ? GREEN : downs.length > ups.length ? RED : MUTED);
  el.innerHTML = `
    <div class="label">Samlet press på prisen nå</div>
    <div class="balance">
      <div class="side" style="color:${RED}">${downs.length}▼</div>
      <div class="bbar">
        <div style="width:${(downs.length / total) * 100}%;background:${RED}"></div>
        <div style="width:${(flats.length / total) * 100}%;background:#334155"></div>
        <div style="width:${(ups.length / total) * 100}%;background:${GREEN}"></div>
      </div>
      <div class="side" style="color:${GREEN}">${ups.length}▲</div>
    </div>
    <div class="pills">
      ${downs.map((k) => `<span class="pill down">${NAMES[k] || k} ▼</span>`).join("")}
      ${ups.map((k) => `<span class="pill up">${NAMES[k] || k} ▲</span>`).join("")}
      ${flats.map((k) => `<span class="pill">${NAMES[k] || k}</span>`).join("")}
    </div>`;
}

// ---- driverpaneler -----------------------------------------------------------
function panel(key, dir, color, word, gauge, facts, asof) {
  return `<div class="panel drv" style="--c:${color}">
    <div class="name">${NAMES[key]}
      <span class="dir ${DIR_CLS[dir ?? 0]}">${DIR_TXT[dir ?? 0]}</span></div>
    <div class="word" style="color:${color}">${word}</div>
    ${gauge}${facts ? `<div class="facts">${facts}</div>` : ""}
    ${freshness(asof)}</div>`;
}

function gauge(pos, left, mid, right, gl, gr) {
  const p = Math.max(2, Math.min(98, pos));
  return `<div class="gauge" style="--gl:${gl};--gm:#334155;--gr:${gr}">
      <div class="tick"></div><div class="marker" style="left:${p}%"></div></div>
    <div class="ends"><span>${left}</span><span>${mid}</span><span>${right}</span></div>`;
}

const pctWord = (p) => p == null ? "ukjent" :
  p <= 15 ? "svært lavt" : p <= 35 ? "lavt" : p <= 65 ? "midt på" : p <= 85 ? "høyt" : "svært høyt";
const signed = (n, d = 1) => n == null ? "–" : (n >= 0 ? "+" : "") + Number(n).toFixed(d);

function renderDrivers(d) {
  const pr = d.pressure || {};
  const out = [];

  const B = d.brl;
  if (B && !B.error) out.push(panel("brl", pr.brl, dirColor(pr.brl),
    B.chg_5d_pct > 1 ? "Realen svekkes" : B.chg_5d_pct < -1 ? "Realen styrkes" : "Stabil real",
    gauge(B.pct_3y ?? 50, "sterk real", "3-års midt", "svak real", GREEN, RED),
    `Kurs <b>${B.usdbrl}</b> · ${signed(B.chg_5d_pct)}% siste 5 dager${B.source ? ` · kilde ${B.source}` : ""}`, B.asof));

  const E = d.ethanol;
  if (E && !E.error) out.push(panel("ethanol", pr.ethanol, dirColor(pr.ethanol),
    E.ratio_pct_3y <= 25 ? "Etanol frister" : E.ratio_pct_3y >= 75 ? "Sukker frister" : "Balansert",
    gauge(E.ratio_pct_3y ?? 50, "etanol frister", "", "sukker frister", GREEN, RED),
    `Bruker velger mellom sukker og etanol — etanol-drag betyr mindre sukker. Etanol <b>${E.eth_brl_liter} BRL/l</b>`, E.asof));

  const U = d.unica;
  if (U && !U.error) out.push(panel("unica", pr.unica, dirColor(pr.unica),
    U.sugar_production_yoy_pct > 5 ? "Mye sukker" : U.sugar_production_yoy_pct < -5 ? "Lite sukker" : "Som i fjor",
    gauge(50 + Math.max(-50, Math.min(50, U.sugar_production_yoy_pct)), "mindre enn i fjor", "", "mer enn i fjor", GREEN, RED),
    `Produksjon <b>${signed(U.sugar_production_yoy_pct)}%</b> mot i fjor ·
     sukkerandel <b>${U.mix_sugar_pct}%</b> (forrige ${U.mix_sugar_pct_prev}%) · ${U.period || ""}`, U.asof));

  const I = d.india;
  if (I && !I.error) out.push(panel("india", pr.india, dirColor(pr.india),
    I.exports_12m_yoy_pct > 10 ? "Eksporterer mye" : I.exports_12m_yoy_pct < -10 ? "Holder igjen" : "Normalt",
    gauge(50 + Math.max(-50, Math.min(50, (I.exports_12m_yoy_pct ?? 0) / 2)), "mindre eksport", "", "mer eksport", GREEN, RED),
    `Eksport siste 12 mnd: <b>${signed(I.exports_12m_yoy_pct)}%</b> mot året før`, I.asof));

  const C = d.cot;
  if (C && !C.error) out.push(panel("cot", pr.cot, C.extreme ? AMBER : dirColor(pr.cot),
    C.extreme ? "Strukket posisjon" : C.mm_net < 0 ? "Vedder på fall" : "Vedder på oppgang",
    gauge(C.pct_52w ?? 50, "mest short (1 år)", "", "mest long (1 år)", "#38bdf8", "#f59e0b"),
    `Fond netto <b>${(C.mm_net / 1000).toFixed(0)}k</b> kontrakter${C.extreme ? " · <b>ekstrem → vending-risiko</b>" : ""}`, C.asof));

  const N = d.enso;
  if (N && !N.error) out.push(panel("enso", pr.enso, N.oni >= 0.5 ? RED : N.oni <= -0.5 ? "#38bdf8" : GREEN,
    N.state + (N.strength ? ` (${N.strength})` : ""),
    gauge(50 + Math.max(-50, Math.min(50, (N.oni / 2) * 50)), "La Niña", "nøytral", "El Niño", "#38bdf8", AMBER),
    `Trend: <b>${N.trend}</b>${N.oni >= 0.4 && N.oni < 0.5 ? " · nærmer seg El Niño-grensen" : ""}`, N.asof));

  const V = d.ndvi;
  if (V && !V.error) out.push(panel("ndvi", pr.ndvi, dirColor(pr.ndvi),
    V.weighted_anomaly < -0.03 ? "Svakere enn normalt" : V.weighted_anomaly > 0.03 ? "Sterkere enn normalt" : "Normal",
    gauge(50 + Math.max(-50, Math.min(50, (V.weighted_anomaly / 0.12) * 50)), "svakere", "normalt", "sterkere", RED, GREEN),
    `Vektet Brasil 55% · India 30% · Thailand 15% — <a href="index.html" style="color:#38bdf8">se felt</a>`, V.asof));

  const F = d.frost;
  if (F && !F.error) out.push(panel("frost", pr.frost,
    F.frost_days_30d > 0 ? RED : F.window_active ? AMBER : MUTED,
    F.frost_days_30d > 0 ? "FROST!" : F.window_active ? "Vakt aktiv" : "Utenfor sesong",
    gauge(F.min_temp_14d == null ? 50 : Math.max(2, Math.min(98, ((F.min_temp_14d - F.threshold_c) / 15) * 100)),
      `frost (${F.threshold_c}°)`, "", "trygt", RED, GREEN),
    `Kaldeste natt siste 14 d: <b>${F.min_temp_14d ?? "–"}°C</b> · jun–aug er frostvinduet i Centro-Sul`, F.asof));

  const S = d.season;
  if (S && !S.error) out.push(panel("season", pr.season, dirColor(pr.season),
    S.phase || "–",
    gauge(S.score * 100, "pris-tungt", "", "pris-vennlig", RED, GREEN),
    `Måned ${S.month} av 12 · historisk ${S.score >= 0.9 ? "sterk" : S.score <= 0.7 ? "svak" : "middels"} periode for prisen`, S.asof));

  const K = d.bedrock;
  if (K && !K.error) {
    const setup = K.latest_setup;
    const fl = K.floor || {};
    out.push(panel("bedrock", 0, setup && setup.direction === "buy" ? GREEN : RED,
      setup ? `${setup.direction === "buy" ? "KJØP" : "SELG"} ${setup.score} (${setup.grade})` : "–",
      "",
      `Terskel kjøp <b>${fl.buy}</b> / selg <b>${fl.sell}</b>` +
      (fl.pending ? ` · <b style="color:${AMBER}">anbefalt selg-terskel ${fl.recommended_sell} venter på godkjenning</b>` : ""),
      K.asof));
  }

  $("grid").innerHTML = out.join("");
}

const dirColor = (v) => v > 0 ? GREEN : v < 0 ? RED : MUTED;

init();
