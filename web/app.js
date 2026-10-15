// 0DTE Trade Checker frontend. Talks to /api/market and /api/check (app.py).
// All text from the server is inserted with textContent (never innerHTML).

const $ = (id) => document.getElementById(id);
const state = { market: null, type: "call", strike: null, premiumEdited: false, lastCheck: null, timer: null };
const REFRESH_MS = 30_000;

// ---------- small helpers ----------
function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") node.className = v;
    else if (k === "style") Object.assign(node.style, v);
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null) node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return node;
}
const svgEl = (tag, attrs = {}) => {
  const n = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  return n;
};
const icon = (paths) => {
  const s = svgEl("svg", { viewBox: "0 0 24 24", "aria-hidden": "true" });
  for (const d of paths) s.append(svgEl("path", { d }));
  return s;
};
const ICONS = {
  cheap: ["M5 12l5 5 9-10"],
  fair: ["M6 9h12", "M6 15h12"],
  expensive: ["M12 7v6", "M12 17h.01", "M10.3 3.9L2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"],
  info: ["M12 16v-4", "M12 8h.01", "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20z"],
};

const money = (v, d = 2) => (v < 0 ? "−$" : "$") + Math.abs(v).toFixed(d);
const signedMoney = (v) => (v >= 0 ? "+" : "−") + "$" + Math.abs(v).toFixed(2);
const pct = (v, d = 0) => (v * 100).toFixed(d) + "%";
const signedPct = (v, d = 2) => (v >= 0 ? "+" : "−") + Math.abs(v * 100).toFixed(d) + "%";
const duration = (min) => {
  min = Math.round(min);
  if (min < 60) return `${min}m`;
  const h = Math.floor(min / 60), m = min % 60;
  return m ? `${h}h ${m}m` : `${h}h`;
};
const expiryLabel = (iso) =>
  new Date(iso + "T12:00:00").toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });

async function api(path) {
  const res = await fetch(path, { headers: { Accept: "application/json" } });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : `Request failed (${res.status})`);
  return body;
}

// ---------- market ----------
async function loadMarket({ silent = false } = {}) {
  const btn = $("refresh");
  btn.classList.add("spinning");
  try {
    state.market = await api("/api/market");
    renderMarket();
    populateStrikes();
    await runCheck();
  } catch (err) {
    if (!silent) showError(err.message);
    $("market-status").textContent = "Market data unavailable";
  } finally {
    btn.classList.remove("spinning");
    scheduleRefresh();
  }
}

function scheduleRefresh() {
  clearTimeout(state.timer);
  if (state.market?.market_open) state.timer = setTimeout(() => loadMarket({ silent: true }), REFRESH_MS);
}

function isToday(m) { return m.minutes_left <= 390; }

function renderMarket() {
  const m = state.market;
  const status = $("market-status");
  status.replaceChildren();
  $("market-dot").classList.toggle("live", m.market_open);
  const src = m.source === "alpaca" ? "Alpaca" : "Yahoo (delayed)";
  if (m.market_open) {
    status.append("SPY ", el("b", {}, money(m.spot)), ` · ${duration(m.minutes_left)}`,
      el("span", { class: "pill-extra" }, ` to close · ${src}`));
  } else {
    status.append(el("span", { class: "pill-extra" }, "Market closed · "), "SPY ", el("b", {}, money(m.spot)),
      ` · expires ${expiryLabel(m.expiry)}`);
  }
  $("expiry-word").textContent = isToday(m) ? "today" : `on ${expiryLabel(m.expiry)}`;
  const em = m.expected;
  $("mini-market").hidden = false;
  $("mini-move").textContent = `±${money(em.move)} (${pct(em.move_pct, 2)})`;
  $("mini-range").textContent = `${money(em.low)} – ${money(em.high)}`;
}

function quotesFor(type) { return state.market?.quotes[type] ?? []; }

function populateStrikes() {
  const quotes = quotesFor(state.type);
  const select = $("strike");
  const spot = state.market.spot;
  const near = quotes.filter((q) => Math.abs(q.strike - spot) <= 12);
  const list = near.length ? near : quotes;
  if (!list.some((q) => q.strike === state.strike)) {
    // default: first strike just out of the money (the most common 0DTE buy)
    const otm = state.type === "call"
      ? list.filter((q) => q.strike > spot).sort((a, b) => a.strike - b.strike)[0]
      : list.filter((q) => q.strike < spot).sort((a, b) => b.strike - a.strike)[0];
    state.strike = (otm ?? list[0])?.strike ?? null;
    state.premiumEdited = false;
  }
  select.replaceChildren(...list.map((q) => {
    const where = q.strike === state.market.expected.atm_strike ? " · ATM" : "";
    return el("option", { value: q.strike, selected: q.strike === state.strike },
      `${q.strike.toFixed(q.strike % 1 ? 1 : 0)}${where}  —  ask ${money(q.ask)}`);
  }));
  select.disabled = !list.length;
  syncPremium();
}

function currentQuote() { return quotesFor(state.type).find((q) => q.strike === state.strike); }

function syncPremium() {
  const q = currentQuote();
  const input = $("premium");
  if (q && !state.premiumEdited) input.value = q.ask.toFixed(2);
  const chips = $("quote-chips");
  chips.replaceChildren();
  if (!q) return;
  for (const [name, v] of [["Bid", q.bid], ["Mid", q.mid], ["Ask", q.ask]]) {
    const active = Math.abs(Number(input.value) - v) < 0.005;
    chips.append(el("button", {
      type: "button", class: "chip" + (active ? " active" : ""),
      onclick: () => { input.value = v.toFixed(2); state.premiumEdited = name !== "Ask"; syncPremium(); runCheck(); },
    }, `${name} ${money(v)}`));
  }
}

// ---------- check ----------
let checkSeq = 0;
async function runCheck() {
  if (state.strike == null) return;
  const seq = ++checkSeq;
  const params = new URLSearchParams({ type: state.type, strike: state.strike });
  const premium = Number($("premium").value);
  if (state.premiumEdited && premium > 0) params.set("premium", premium.toFixed(2));
  const results = $("results");
  results.classList.add("loading");
  results.setAttribute("aria-busy", "true");
  $("submit").disabled = true;
  try {
    const data = await api(`/api/check?${params}`);
    if (seq !== checkSeq) return;           // a newer request is in flight
    state.lastCheck = data;
    render(data);
  } catch (err) {
    if (seq === checkSeq) showError(err.message);
  } finally {
    if (seq === checkSeq) {
      results.classList.remove("loading");
      results.setAttribute("aria-busy", "false");
      $("submit").disabled = false;
    }
  }
}

function showError(msg) {
  $("notes").replaceChildren(el("div", { class: "note error", role: "alert" }, icon(ICONS.expensive), el("span", {}, msg)));
}

function renderNotes(notes) {
  const shown = notes.filter((n) => !n.startsWith("Dropped "));   // routine cleaning, not worth a banner
  $("notes").replaceChildren(...shown.map((n) => el("div", { class: "note" }, icon(ICONS.info), el("span", {}, n))));
}

function render(data) {
  $("empty-state").hidden = true;
  $("result-body").hidden = false;
  renderNotes(data.notes);
  renderVerdict(data);
  renderPriceMap(data.trade);
  renderTiles(data.trade);
  renderHistory(data);
  renderGreeks(data);
}

// ---------- verdict ----------
const VERDICT_TEXT = {
  Cheap: "History says SPY made this move more often than the price implies.",
  Fair: "The price is roughly in line with how SPY has actually moved.",
  Expensive: "You'd be paying for a move SPY rarely made in this window.",
};

function renderVerdict({ trade: t, history: h }) {
  const badge = $("verdict-badge");
  const v = h?.verdict;
  const key = v ? v.toLowerCase() : "fair";
  badge.className = `badge ${key}`;
  badge.replaceChildren(icon(ICONS[key]), v ?? "No verdict");
  $("verdict-option").textContent =
    `${t.option_type.toUpperCase()} ${t.strike} · pay ${money(t.premium)} · SPY ${money(t.spot)}`;
  $("verdict-line").textContent = v ? VERDICT_TEXT[v] : "Historical data is unavailable right now, so there's no verdict.";

  const rows = [{ label: "Market's odds it finishes in the money", value: t.prob_itm, color: "var(--series-1)" }];
  if (h) {
    const used = h.basis === "intraday" ? h.intraday : h.daily;
    const what = h.basis === "intraday" ? `${used.n_days} recent days, same time window` : `${used.n_days} days, scaled to time left`;
    rows.push({ label: `How often it happened (${what})`, value: h.hist_prob, color: "var(--series-2)" });
  }
  const max = Math.max(0.05, ...rows.map((r) => r.value));
  $("compare").replaceChildren(...rows.map((r) => el("div", { class: "compare-row" },
    el("div", { class: "compare-label" }, el("span", { class: "swatch", style: { background: r.color } }), r.label),
    el("div", { class: "track" }, el("div", { class: "fill", style: { width: `${(r.value / max) * 100}%`, background: r.color } })),
    el("div", { class: "compare-value" }, pct(r.value)),
  )));
  $("verdict-fine").textContent = h
    ? `"Cheap" or "Expensive" means history differs from the market by more than 5 percentage points. ` +
      `Not a prediction: history can't see today's news.`
    : "";
}

// ---------- price map ----------
function renderPriceMap(t) {
  const em = t.expected;
  const box = $("price-map");
  const values = [em.low, em.high, t.spot, t.strike, t.breakeven];
  let lo = Math.min(...values), hi = Math.max(...values);
  const pad = (hi - lo) * 0.12 || 1;
  lo -= pad; hi += pad;
  const pos = (v) => ((v - lo) / (hi - lo)) * 100;

  const dir = t.move_needed >= 0 ? "rise" : "fall";
  $("map-sub").textContent =
    `SPY must ${dir} ${money(Math.abs(t.move_needed))} by 4 PM to break even. ` +
    `That's ${t.move_vs_expected.toFixed(1)}× the move the market expects today.`;
  box.setAttribute("aria-label",
    `SPY now ${money(t.spot)}, strike ${t.strike}, breakeven ${money(t.breakeven)}, expected range ${money(em.low)} to ${money(em.high)}`);

  // Labels sit above or below the axis. If a label would collide with one already placed,
  // try the other side, then one tier further out (with a longer stem).
  const width = box.clientWidth || 600;
  const minGapPct = (96 / width) * 100;
  const marks = [
    { v: t.breakeven, title: "Breakeven", color: "var(--series-2)", prefer: "above" },
    { v: t.spot, title: "SPY now", color: "var(--ink)", prefer: "above" },
    { v: t.strike, title: "Strike", color: "var(--axis)", prefer: "below" },
  ];
  const placed = [];
  for (const m of marks) {
    m.p = pos(m.v);
    const other = m.prefer === "above" ? "below" : "above";
    const free = (row, tier) => placed.every((q) => q.row !== row || q.tier !== tier || Math.abs(q.p - m.p) >= minGapPct);
    outer: for (let tier = 0; tier < 3; tier++) {
      for (const row of [m.prefer, other]) if (free(row, tier)) { Object.assign(m, { row, tier }); break outer; }
    }
    if (!m.row) Object.assign(m, { row: m.prefer, tier: 3 });
    placed.push(m);
  }
  const TIER = 40, STEM = 12, LABEL = 34;
  const tiers = (row) => Math.max(-1, ...placed.filter((m) => m.row === row).map((m) => m.tier)) + 1;
  const axisY = Math.max(16, tiers("above") * TIER + LABEL - TIER + STEM + 14);
  box.style.height = `${axisY + Math.max(16, tiers("below") * TIER + LABEL - TIER + STEM + 14)}px`;

  const a = Math.min(t.spot, t.breakeven), b = Math.max(t.spot, t.breakeven);
  const parts = [
    el("div", { class: "pm-band", style: { top: `${axisY - 13}px`, left: `${pos(em.low)}%`, width: `${pos(em.high) - pos(em.low)}%` } }),
    el("div", { class: "pm-axis", style: { top: `${axisY - 1}px` } }),
    el("div", { class: "pm-gap", style: { top: `${axisY - 2}px`, left: `${pos(a)}%`, width: `${pos(b) - pos(a)}%` } }),
  ];
  for (const m of placed) {
    const left = Math.min(Math.max(m.p, 7), 93);   // keep text inside the card
    const text = el("div", { class: "pm-text", style: { transform: `translateX(${(left - m.p) * width / 100}px)` } },
      m.title, el("b", {}, money(m.v)));
    const stem = el("div", { class: "pm-stem", style: { height: `${STEM + m.tier * TIER}px` } });
    const dot = el("div", { class: "pm-dot", style: { background: m.color } });
    const above = m.row === "above";
    parts.push(el("div", {
      class: "pm-mark",
      style: { left: `${m.p}%`, top: `${axisY + (above ? 6 : -6)}px`, transform: `translate(-50%, ${above ? "-100%" : "0"})` },
    }, ...(above ? [text, stem, dot] : [dot, stem, text])));
  }
  box.replaceChildren(...parts);

  // legend under the map
  let legend = box.nextElementSibling;
  if (!legend?.classList.contains("legend")) {
    legend = el("div", { class: "legend", style: { marginTop: "14px", marginBottom: "0" } });
    box.after(legend);
  }
  legend.replaceChildren(
    el("span", {}, el("span", { class: "swatch", style: { background: "var(--accent-wash)", border: "1px solid var(--series-1)" } }),
      `Market's expected range (±${money(em.move)})`),
    el("span", {}, el("span", { class: "line-key", style: { background: "var(--series-2)", height: "4px" } }), "Move you need"),
  );
}

// ---------- tiles ----------
function renderTiles(t) {
  const em = t.expected;
  const tile = (label, value, ...sub) => el("article", { class: "card tile" },
    el("div", { class: "tile-label" }, label), el("div", { class: "tile-value" }, value), el("div", { class: "tile-sub" }, ...sub));
  const flatPct = t.model_value > 0 ? t.loss_if_flat / t.model_value : 0;
  $("tiles").replaceChildren(
    tile("Breakeven at close", money(t.breakeven),
      "SPY ", el("b", {}, signedMoney(t.move_needed)), ` (${signedPct(t.move_needed / t.spot)})`),
    tile("Expected move today", `±${money(em.move)}`,
      "Range ", el("b", {}, `${money(em.low, 0)}–${money(em.high, 0)}`), ` · IV ${pct(em.atm_iv, 1)}`),
    tile("Chance of profit", pct(t.prob_profit),
      "Finishes in the money: ", el("b", {}, pct(t.prob_itm))),
    tile("Time decay", `${money(t.theta_per_hour)}/hr`,
      `If SPY sits still ${duration(t.hours_ahead * 60)}: `, el("b", {}, `${money(t.model_value)} → ${money(t.value_later)}`),
      ` (−${pct(flatPct)})`),
  );
}

// ---------- history ----------
function renderHistory({ trade: t, history: h }) {
  const table = $("history-table");
  if (!h) {
    $("history-sub").textContent = "Historical data couldn't be loaded.";
    table.replaceChildren();
    return;
  }
  $("history-sub").textContent =
    `SPY needed ${signedPct(h.needed_itm_pct)} to reach the strike and ${signedPct(h.needed_profit_pct)} to break even, ` +
    `over the last ${duration(h.window_minutes)} of the trading day.`;
  const head = el("thead", {}, el("tr", {},
    el("th", {}, "Source"), el("th", { class: "num" }, "Days"),
    el("th", { class: "num" }, "Finished ITM"), el("th", { class: "num" }, "Past breakeven")));
  const fmtDay = (iso, opts) => new Date(iso + "T12:00:00").toLocaleDateString("en-US", opts);
  const period = (r) => r.basis === "daily"
    ? `${fmtDay(r.first_day, { month: "short", year: "numeric" })} – ${fmtDay(r.last_day, { month: "short", year: "numeric" })}`
    : `${fmtDay(r.first_day, { month: "short", day: "numeric" })} – ${fmtDay(r.last_day, { month: "short", day: "numeric" })}`;
  const source = (name, sub, used) => el("td", {},
    el("div", {}, name, used ? el("span", { class: "used-tag" }, "used for verdict") : null),
    el("div", { class: "cell-sub" }, sub));
  const row = (r, name) => el("tr", { class: h.basis === r.basis ? "used" : "" },
    source(name, period(r), h.basis === r.basis),
    el("td", { class: "num" }, r.n_days), el("td", { class: "num" }, pct(r.itm)), el("td", { class: "num" }, pct(r.profit)));
  const body = el("tbody", {},
    el("tr", { class: "market" }, source("Market's odds", "Black-Scholes, today's prices", false),
      el("td", { class: "num" }, "—"), el("td", { class: "num" }, pct(t.prob_itm)), el("td", { class: "num" }, pct(t.prob_profit))),
    h.intraday ? row(h.intraday, "Intraday, 5-min bars") : null,
    h.daily ? row(h.daily, "Daily, scaled to time left") : null);
  table.replaceChildren(head, body);
}

// ---------- greeks charts ----------
function renderGreeks({ trade: t, greeks_curve: g, market }) {
  const colors = ["var(--series-1)", "var(--series-2)"];
  const same = Math.abs(g.series[0].gamma[0] - g.series[1].gamma[0]) < 1e-12;
  const series = same ? [g.series[1]] : g.series;
  const cols = same ? [colors[1]] : colors;
  $("greeks-sub").textContent =
    `SPY held at ${money(t.spot)}, from the 9:30 open (left) to 5 minutes before the close (right). ` +
    `At the money, both explode in the final hour.`;
  $("greeks-legend").replaceChildren(...series.map((s, i) =>
    el("span", {}, el("span", { class: "line-key", style: { background: cols[i] } }), s.label)));
  const nowMin = market.market_open ? t.minutes_left : null;
  drawLineChart($("chart-gamma"), g.minutes, series.map((s, i) => ({ label: s.label, color: cols[i], values: s.gamma })),
    { fmt: (v) => v.toFixed(3), nowMin });
  drawLineChart($("chart-theta"), g.minutes, series.map((s, i) => ({ label: s.label, color: cols[i], values: s.theta_per_hour })),
    { fmt: (v) => money(v), nowMin });
}

function niceTicks(min, max, count = 4) {
  if (min === max) { max = min + 1; }
  const span = max - min, step0 = span / count;
  const mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= step0);
  const ticks = [];
  for (let v = Math.floor(min / step) * step; v <= max + step * 1e-9; v += step) ticks.push(+v.toFixed(10));
  return ticks;
}

const X_TICKS = [[390, "Open"], [300, "5h"], [240, "4h"], [180, "3h"], [120, "2h"], [60, "1h"], [5, "5m"]];

function drawLineChart(fig, minutes, series, { fmt, nowMin }) {
  fig.querySelector("svg")?.remove();
  const W = Math.max(fig.clientWidth, 260), H = 220;
  const M = { l: 52, r: 14, t: 14, b: 26 };
  const iw = W - M.l - M.r, ih = H - M.t - M.b;
  const all = series.flatMap((s) => s.values);
  const ticks = niceTicks(Math.min(0, ...all), Math.max(0, ...all));
  const y0 = ticks[0], y1 = ticks[ticks.length - 1];
  const x = (m) => M.l + ((390 - m) / (390 - 5)) * iw;
  const y = (v) => M.t + (1 - (v - y0) / (y1 - y0)) * ih;

  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, tabindex: "0", role: "img",
    "aria-label": `${fig.querySelector("figcaption").textContent}. Use arrow keys to read values.` });
  const grid = svgEl("g", { class: "grid" }), axis = svgEl("g", { class: "axis" });
  for (const tv of ticks) {
    grid.append(svgEl("line", { x1: M.l, x2: W - M.r, y1: y(tv), y2: y(tv) }));
    const label = svgEl("text", { x: M.l - 8, y: y(tv) + 4, "text-anchor": "end" });
    label.textContent = fmt(tv);
    axis.append(label);
  }
  for (const [m, name] of X_TICKS) {
    const label = svgEl("text", { x: x(m), y: H - 6, "text-anchor": m === 390 ? "start" : m === 5 ? "end" : "middle" });
    label.textContent = name;
    axis.append(label);
  }
  svg.append(grid, svgEl("line", { class: "baseline", x1: M.l, x2: W - M.r, y1: y(0), y2: y(0) }), axis);

  if (nowMin != null && nowMin >= 5 && nowMin <= 390) {
    const g = svgEl("g", { class: "now" });
    g.append(svgEl("line", { x1: x(nowMin), x2: x(nowMin), y1: M.t, y2: M.t + ih }));
    const tx = svgEl("text", { x: x(nowMin) + 4, y: M.t + 10 });
    tx.textContent = "now";
    g.append(tx);
    svg.append(g);
  }

  for (const s of series) {
    const d = minutes.map((m, i) => `${i ? "L" : "M"}${x(m).toFixed(1)},${y(s.values[i]).toFixed(1)}`).join("");
    svg.append(svgEl("path", { class: "series", d, stroke: s.color }));
  }

  // hover layer: crosshair snaps to the nearest minute, one tooltip lists every series
  const cross = svgEl("line", { class: "crosshair", y1: M.t, y2: M.t + ih, visibility: "hidden" });
  const dots = series.map((s) => svgEl("circle", { class: "hover-dot", r: 5, fill: s.color, visibility: "hidden" }));
  const hit = svgEl("rect", { x: M.l, y: M.t, width: iw, height: ih, fill: "transparent" });
  svg.append(cross, ...dots, hit);
  const tip = $("tooltip");
  let idx = minutes.length - 1;

  const show = (i, clientX, clientY) => {
    idx = i;
    const cx = x(minutes[i]);
    cross.setAttribute("x1", cx); cross.setAttribute("x2", cx); cross.setAttribute("visibility", "visible");
    dots.forEach((dot, k) => {
      dot.setAttribute("cx", cx); dot.setAttribute("cy", y(series[k].values[i])); dot.setAttribute("visibility", "visible");
    });
    tip.replaceChildren(
      el("div", { class: "tt-title" }, `${duration(minutes[i])} before close`),
      ...series.map((s) => el("div", { class: "tt-row" },
        el("span", {}, el("span", { class: "line-key", style: { background: s.color } }), s.label),
        el("b", {}, fmt(s.values[i])))));
    tip.hidden = false;
    const r = svg.getBoundingClientRect();
    const px = clientX ?? r.left + (cx / W) * r.width, py = clientY ?? r.top + r.height / 3;
    const tw = tip.offsetWidth;
    tip.style.left = `${Math.min(px + 14, window.innerWidth - tw - 8)}px`;
    tip.style.top = `${py - 10}px`;
  };
  const hide = () => {
    tip.hidden = true;
    cross.setAttribute("visibility", "hidden");
    dots.forEach((d) => d.setAttribute("visibility", "hidden"));
  };
  const nearest = (clientX) => {
    const r = svg.getBoundingClientRect();
    const sx = ((clientX - r.left) / r.width) * W;
    let best = 0;
    minutes.forEach((m, i) => { if (Math.abs(x(m) - sx) < Math.abs(x(minutes[best]) - sx)) best = i; });
    return best;
  };
  hit.addEventListener("pointermove", (e) => show(nearest(e.clientX), e.clientX, e.clientY));
  hit.addEventListener("pointerleave", hide);
  svg.addEventListener("blur", hide);
  svg.addEventListener("keydown", (e) => {
    if (e.key === "ArrowRight") { show(Math.min(idx + 1, minutes.length - 1)); e.preventDefault(); }
    if (e.key === "ArrowLeft") { show(Math.max(idx - 1, 0)); e.preventDefault(); }
    if (e.key === "Escape") hide();
  });
  svg.addEventListener("focus", () => show(idx));
  fig.append(svg);
}

// ---------- events ----------
document.querySelectorAll('input[name="type"]').forEach((r) => r.addEventListener("change", (e) => {
  state.type = e.target.value;
  state.strike = null;
  populateStrikes();
  runCheck();
}));
$("strike").addEventListener("change", (e) => {
  state.strike = Number(e.target.value);
  state.premiumEdited = false;
  syncPremium();
  runCheck();
});
$("premium").addEventListener("input", () => { state.premiumEdited = true; syncPremium(); });
$("trade-form").addEventListener("submit", (e) => { e.preventDefault(); runCheck(); });
$("refresh").addEventListener("click", () => loadMarket());
$("theme-toggle").addEventListener("click", () => {
  const root = document.documentElement;
  const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  root.dataset.theme = dark ? "light" : "dark";
  try { localStorage.setItem("theme", root.dataset.theme); } catch (e) {}
});

let resizeTimer, lastWidth = 0;
new ResizeObserver(([entry]) => {
  const w = Math.round(entry.contentRect.width);
  if (w === lastWidth) return;                 // only width changes need a redraw
  lastWidth = w;
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { if (state.lastCheck) render(state.lastCheck); }, 120);
}).observe($("results"));

loadMarket();
