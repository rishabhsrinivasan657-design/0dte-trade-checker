// 0DTE Trade Checker frontend. Talks to /api/market and /api/check (app.py).
// All text from the server is inserted with textContent (never innerHTML).

import { renderWhatIf } from "./whatif.js";
import { drawSpyChart, renderTicker } from "./spychart.js";
import { $, el, svgEl, icon, ICONS, money, signedMoney, pct, signedPct, duration, expiryLabel, setupThemeToggle, odds, cents, breakevenSub } from "./common.js";

const state = { market: null, spy: null, type: "call", strike: null, premiumEdited: false, lastCheck: null, timer: null, research: null };
const REFRESH_MS = 30_000;

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
    await Promise.all([runCheck(), loadSpy()]);
  } catch (err) {
    if (!silent) showError(err.message);
    $("tk-last").textContent = "unavailable";
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
  renderTicker(m, state.spy);
  $("expiry-word").textContent = isToday(m) ? "today" : expiryLabel(m.expiry);
}

async function loadSpy() {
  try {
    state.spy = await api("/api/spy");
  } catch (e) {
    state.spy = null;
  }
  renderTicker(state.market, state.spy);
  drawChart();
}

function drawChart() {
  const t = state.lastCheck?.trade;
  const lines = t ? [{ price: t.strike, label: "Strike", kind: "strike" }, { price: t.breakeven, label: "Breakeven", kind: "breakeven" }] : [];
  const spy = state.spy;
  $("live-chip").hidden = !spy?.live;
  $("chart-sub").textContent = spy ? (spy.live ? "5-min" : `Last session · ${expiryLabel(spy.date)}`) : "Price data unavailable";
  drawSpyChart($("spy-chart"), spy, { lines });
}

function quotesFor(type) { return state.market?.quotes[type] ?? []; }

function populateStrikes() {
  const quotes = quotesFor(state.type);
  const select = $("strike");
  const spot = state.market.spot;
  const near = quotes.filter((q) => Math.abs(q.strike - spot) <= 8);   // same window as /api/scan
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

function currentQuote() {
  // Prefer the quote from the latest check (fresher than the option list loaded with the page)
  const live = state.liveQuote;
  if (live && live.type === state.type && live.strike === state.strike) return live;
  return quotesFor(state.type).find((q) => q.strike === state.strike);
}

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
  results.classList.add("loading");   // .center.loading dims the chart + decision while refreshing
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
  const t = data.trade;
  state.liveQuote = { type: t.option_type, strike: t.strike, bid: t.bid, mid: t.mid, ask: t.ask };
  syncPremium();                       // premium box + chips now match the prices this analysis used
  $("empty-state").hidden = true;
  $("decision-card").hidden = false;
  $("result-body").hidden = false;
  renderNotes(data.notes);
  renderDecision(data);
  loadCompare();
  renderVerdict(data);
  renderEvidence(data);
  drawChart();
  renderWhatIf($("whatif"), data.scenarios, {
    caption: `1 contract at ${money(data.trade.premium)} · IV held at ${pct(data.scenarios.iv, 1)}`,
  });
  renderTiles(data.trade);
  renderModel(data);
  renderHistory(data);
  renderGreeks(data);
}

// ---------- decision (the bottom line) ----------
const RATING_ICON = { avoid: ICONS.expensive, poor: ICONS.info, typical: ICONS.fair };
const zGroup = (z) => z < 0 ? "in the money" : z < 0.5 ? "near the money (0–0.5 moves out)" : "far out of the money (0.5+ moves)";

function renderDecision({ decision: d, trade: t, evidence: ev }) {
  const badge = $("rating");
  const key = d.available ? d.rating : "unrated";
  badge.className = `rating ${key}`;
  badge.replaceChildren(icon(d.available ? RATING_ICON[key] : ICONS.info), d.available ? d.label : "Not rated");
  $("decision-option").textContent = `${t.option_type.toUpperCase()} ${t.strike} @ ${money(t.premium)}`;

  const g = d.group;
  const c = g ? cents(g.return_per_dollar) : "";
  $("decision-line").textContent = !d.available ? d.why_unavailable
    : d.override ? d.override
    : key === "avoid" ? `Similar trades lost ${c.slice(1)} per $1 and won ${pct(g.win_rate)} of the time.`
    : key === "poor" ? `Similar trades lost ${c.slice(1)} per $1 and won ${pct(g.win_rate)} of the time.`
    : `An average 0DTE trade: ${c} per $1, won ${pct(g.win_rate)} of the time.`;

  const chance = ev ? ev.realistic_profit : t.prob_profit;
  const flatLoss = t.loss_if_flat * 100;
  const num = (k, v, sub) => el("div", { class: "key-number" }, el("div", { class: "k" }, k), el("div", { class: "v" }, v), el("div", { class: "s" }, sub));
  $("key-numbers").replaceChildren(
    num("Cost", money(t.premium * 100, 0), "per contract"),
    num("Breakeven", money(t.breakeven), breakevenSub(t)),
    num("Chance of profit", odds(chance), ev ? `market says ${odds(t.prob_profit)}` : ""),
    num("If SPY stalls", `−${money(flatLoss, 0)}`, `next ${duration(t.hours_ahead * 60)}`),
  );

  $("why-list").replaceChildren(...d.reasons.map((r) => el("li", { class: `why-${r.tone}` },
    icon(r.tone === "bad" ? ICONS.expensive : r.tone === "good" ? ICONS.cheap : ICONS.info), el("span", {}, r.text))));

  // exit tip for options like this one, from the exit-rule research
  const rules = (ev?.exit_rules_by_group ?? []).filter((r) => r.group === zGroup(d.z));
  const best = [...rules].sort((a, b) => b.return_per_dollar - a.return_per_dollar)[0];
  const hold = rules.find((r) => r.rule === "hold");
  const trackHref = `track.html?${new URLSearchParams({ type: t.option_type, strike: t.strike, paid: t.premium.toFixed(2) })}`;
  $("if-buy").replaceChildren(
    el("div", {}, el("b", {}, "Exit tip: "),
      best && hold && best.rule !== "hold"
        ? `${best.label} (${cents(best.return_per_dollar)} vs ${cents(hold.return_per_dollar)} holding)`
        : "track it to see when to get out"),
    el("a", { class: "btn-secondary", href: trackHref }, "Track this position →"));

  $("decision-fine").textContent = d.available && d.missing?.length
    ? `Rated without ${d.missing.map((m) => m === "ml_forecast" ? "the ML forecast" : "history").join(" or ")} (unavailable right now).`
    : "";
}

const scanCache = {};
const SHORT_LABEL = { avoid: "Avoid", poor: "Poor odds", typical: "Typical" };
async function loadCompare() {
  const type = state.type;
  const cached = scanCache[type];
  if (cached && Date.now() - cached.at < 30_000) return renderCompare(cached.data);
  try {
    const data = await api(`/api/scan?type=${type}`);
    scanCache[type] = { at: Date.now(), data };
    if (type === state.type) renderCompare(data);
  } catch (e) {
    $("compare-sub").textContent = "Couldn't load strikes.";
  }
}

function renderCompare(data) {
  const t = state.lastCheck?.trade;
  // the scan has fresher asks than the list loaded with the page: refresh the dropdown labels
  for (const r of data.rows) {
    const opt = [...$("strike").options].find((o) => Number(o.value) === r.strike);
    if (opt) opt.textContent = `${r.strike}${r.strike === state.market?.expected?.atm_strike ? " · ATM" : ""}  —  ask ${money(r.ask)}`;
  }
  $("compare-title").textContent = `${data.type === "call" ? "Calls" : "Puts"} near SPY`;
  $("compare-sub").textContent = "Click a row to check it";
  const atm = state.market?.expected?.atm_strike;
  const head = el("thead", {}, el("tr", {}, el("th", {}, "Strike"), el("th", { class: "num" }, "Cost"),
    el("th", { class: "num" }, "Breakeven"), el("th", { class: "num" }, "Profit odds"), el("th", {}, "Rating")));
  const body = el("tbody", {}, ...data.rows.map((r) => {
    const current = t && r.strike === t.strike;
    const tr = el("tr", { class: `pick${current ? " current" : ""}`, tabindex: "0", role: "button",
      "aria-label": `Check the ${r.strike} ${data.type}: ${r.label ?? "not rated"}` },
      el("td", {}, `${r.strike}`, r.strike === atm ? el("span", { class: "used-tag" }, "ATM") : null, current ? el("span", { class: "used-tag" }, "selected") : null),
      el("td", { class: "num" }, money(r.cost, 0)),
      el("td", { class: "num" }, money(r.breakeven)),
      el("td", { class: "num" }, odds(r.prob_profit)),
      el("td", {}, r.rating ? el("span", { class: `rating-chip ${r.rating}`, title: r.label }, SHORT_LABEL[r.rating] ?? r.label) : "—"));
    const pick = () => {
      state.strike = r.strike;
      state.premiumEdited = false;
      $("strike").value = String(r.strike);
      syncPremium();
      runCheck();
      const card = $("decision-card").getBoundingClientRect();
      if (card.top < 60 || card.top > innerHeight * 0.6) $("decision-card").scrollIntoView({ behavior: "smooth", block: "start" });
    };
    tr.addEventListener("click", pick);
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
    return tr;
  }));
  $("compare-table").replaceChildren(head, body);
}

// ---------- history check ----------
const VERDICT_TEXT = {
  Cheap: "Happened more often than priced.",
  Fair: "In line with recent history.",
  Expensive: "Happened less often than priced.",
};

function renderVerdict({ trade: t, history: h }) {
  const badge = $("verdict-badge");
  const v = h?.verdict;
  const key = v ? v.toLowerCase() : "fair";
  badge.className = `badge ${key}`;
  badge.replaceChildren(icon(ICONS[key]), v ?? "No verdict");
  $("verdict-option").textContent = "";
  $("verdict-fine").dataset.headline = v ? VERDICT_TEXT[v] : "Historical data is unavailable right now.";

  const rows = [{ label: "Market's odds (in the money)", value: t.prob_itm, color: "var(--series-1)" }];
  if (h) {
    const used = h.basis === "intraday" ? h.intraday : h.daily;
    rows.push({ label: `Happened (last ${used.n_days} days)`, value: h.hist_prob, color: "var(--series-2)" });
  }
  const max = Math.max(0.05, ...rows.map((r) => r.value));
  $("compare").replaceChildren(...rows.map((r) => el("div", { class: "compare-row" },
    el("div", { class: "compare-label" }, el("span", { class: "swatch", style: { background: r.color } }), r.label),
    el("div", { class: "track" }, el("div", { class: "fill", style: { width: `${(r.value / max) * 100}%`, background: r.color } })),
    el("div", { class: "compare-value" }, pct(r.value)),
  )));
  $("verdict-fine").textContent = h ? VERDICT_TEXT[v] : "Historical data unavailable.";
}

// ---------- trades like this one ----------

function renderEvidence({ evidence: ev, trade: t }) {
  const card = $("similar-card");
  card.hidden = !ev;
  if (!ev) return;
  const s = ev.similar;
  const line = $("similar-line");
  if (!s) {
    line.textContent = "No close match in our history";
    $("similar-sub").textContent = t.minutes_left > 390 ? "Same-day trades only." : "Too few similar past trades.";
    $("similar-stats").replaceChildren();
    $("similar-fine").textContent = "";
    return;
  }
  const r = s.return_per_dollar;
  line.replaceChildren("Past trades like this ", el("span", { class: r < 0 ? "neg" : "pos" }, `${r < 0 ? "lost" : "made"} ${cents(r).slice(1)}`), " per $1");
  $("similar-sub").textContent = `${s.trades.toLocaleString()} ${t.option_type}s · ${s.z_label} · bought ${s.time_bucket} · held to 4 PM`;
  const stat = (k, v, sub) => el("div", { class: "mini-stat" }, el("div", { class: "k" }, k), el("div", { class: "v" }, v), el("div", { class: "s" }, sub));
  $("similar-stats").replaceChildren(
    stat("Won", pct(s.win_rate), ""),
    stat("Finished in the money", pct(s.itm_rate), `market said ${pct(s.avg_prob_itm)}`),
    stat("Return per $1", cents(r), `range ${cents(s.ci_low)} to ${cents(s.ci_high)}`),
  );
  $("similar-fine").textContent = `All 0DTE trades: ${cents(ev.all_trades.return_per_dollar)} per $1.`;
}

// ---------- tiles ----------
function renderTiles(t) {
  const em = t.expected;
  const flatPct = t.model_value > 0 ? t.loss_if_flat / t.model_value : 0;
  const row = (k, v, sub) => [el("dt", {}, k), el("dd", {}, v, sub ? el("small", {}, sub) : null)];
  $("ticket-stats").replaceChildren(
    ...row("Bid / Ask", `${money(t.bid)} / ${money(t.ask)}`, `mid ${money(t.mid)}`),
    ...row("Expected move", `±${money(em.move)}`, `${money(em.low, 0)}–${money(em.high, 0)}`),
    ...row("Implied vol", pct(t.iv, 1), `ATM ${pct(em.atm_iv, 1)}`),
    ...row("Time decay", `${money(t.theta_per_hour)}/hr`),
    ...row("Market odds", `${pct(t.prob_profit)} profit`, `${pct(t.prob_itm)} ITM`),
    ...row("Delta / Gamma", `${t.delta.toFixed(2)} / ${t.gamma.toFixed(3)}`),
  );
}

// ---------- ML model ----------
function compareRows(rows, fmt) {
  const max = Math.max(1e-9, ...rows.map((r) => r.value));
  return rows.map((r) => el("div", { class: "compare-row" },
    el("div", { class: "compare-label" }, el("span", { class: "swatch", style: { background: r.color } }), r.label),
    el("div", { class: "track" }, el("div", { class: "fill", style: { width: `${(r.value / max) * 100}%`, background: r.color } })),
    el("div", { class: "compare-value" }, fmt(r.value))));
}

function renderModel({ model: m, trade: t }) {
  const body = $("model-body");
  if (!m) {
    $("model-sub").textContent = "";
    body.replaceChildren(el("p", { class: "model-empty" }, "Available during market hours on expiry day."));
    $("model-fine").textContent = "";
    return;
  }
  $("model-sub").textContent = "";
  const market = "var(--series-1)", model = "var(--series-3)";
  body.replaceChildren(
    el("div", { class: "model-group" }, el("h4", {}, "Expected move by the close"),
      el("div", { class: "compare" }, compareRows([
        { label: "Market (options prices)", value: m.market_move, color: market },
        { label: "ML forecast", value: m.expected_move, color: model }], (x) => `±${money(x)}`))),
    el("div", { class: "model-group" }, el("h4", {}, "Chance it finishes in the money"),
      el("div", { class: "compare" }, compareRows([
        { label: "Market (options prices)", value: t.prob_itm, color: market },
        { label: "ML forecast", value: m.prob_itm, color: model }], (x) => pct(x)))),
  );
  const track = researchLine();
  $("model-fine").replaceChildren(track ? track + " " : "", el("a", { href: "research.html" }, "Results →"));
}

// One honest sentence about the model's track record, computed from research.json
function researchLine() {
  const R = state.research;
  if (!R) return "";
  const s = Object.fromEntries(R.model_vs_market.scores.map((r) => [r.model, r]));
  const mkt = s["market (ATM implied vol)"], ml = s.gbm;
  return ml.qlike < mkt.qlike
    ? "More accurate than the market in testing."
    : "Less accurate than the market in testing: a second opinion.";
}

// ---------- history ----------
function renderHistory({ trade: t, history: h }) {
  const table = $("history-table");
  if (!h) {
    $("history-sub").textContent = "Historical data unavailable.";
    table.replaceChildren();
    return;
  }
  $("history-sub").textContent = `Needs ${signedPct(h.needed_itm_pct)} to the strike, ${signedPct(h.needed_profit_pct)} to break even (${duration(h.window_minutes)} left)`;
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
  $("greeks-sub").textContent = "Both explode in the final hour.";
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
setupThemeToggle();

const tabs = [...document.querySelectorAll('.tabs [role="tab"]')];
function selectTab(tab, focus = false) {
  for (const t of tabs) {
    const on = t === tab;
    t.setAttribute("aria-selected", on);
    t.tabIndex = on ? 0 : -1;
    document.getElementById(t.getAttribute("aria-controls")).hidden = !on;
  }
  if (focus) tab.focus();
  if (state.lastCheck && tab.id === "tab-greeks") renderGreeks(state.lastCheck);   // charts need a visible box to size
}
tabs.forEach((t, i) => {
  t.addEventListener("click", () => selectTab(t));
  t.addEventListener("keydown", (e) => {
    const d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (d) { e.preventDefault(); selectTab(tabs[(i + d + tabs.length) % tabs.length], true); }
  });
});

let resizeTimer, lastWidth = 0;
new ResizeObserver(([entry]) => {
  const w = Math.round(entry.contentRect.width);
  if (w === lastWidth) return;                 // only width changes need a redraw
  lastWidth = w;
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { if (state.lastCheck) render(state.lastCheck); }, 120);
}).observe($("results"));

fetch("research.json").then((r) => (r.ok ? r.json() : null)).then((R) => {
  state.research = R;
  if (state.lastCheck) renderModel(state.lastCheck);
}).catch(() => {});
loadMarket();
