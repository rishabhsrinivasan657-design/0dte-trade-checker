// Position tracker: follows one option you bought, every 15 seconds.
import { $, el, svgEl, icon, ICONS, money, pct, duration, expiryLabel, setupThemeToggle } from "./common.js";
import { renderWhatIf } from "./whatif.js";
import { drawSpyChart, renderTicker } from "./spychart.js";

setupThemeToggle();

const REFRESH_MS = 15_000;
const STORE = "position-v1";
const state = { market: null, spy: null, pos: null, timer: null, history: [], last: null };

const cents = (r) => `${r < 0 ? "−" : "+"}${Math.abs(r * 100).toFixed(0)}¢`;
const signedMoney0 = (v) => `${v >= 0 ? "+" : "−"}${money(Math.abs(v), 0)}`;
const store = {
  get() { try { return JSON.parse(localStorage.getItem(STORE)); } catch (e) { return null; } },
  set(v) { try { localStorage.setItem(STORE, JSON.stringify(v)); } catch (e) {} },
};

async function api(path) {
  const res = await fetch(path, { headers: { Accept: "application/json" } });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : `Request failed (${res.status})`);
  return body;
}

// ---------- form ----------
const formType = () => document.querySelector('input[name="type"]:checked').value;

function populateStrikes(selected) {
  const quotes = state.market?.quotes[formType()] ?? [];
  const spot = state.market?.spot ?? 0;
  const list = quotes.filter((q) => Math.abs(q.strike - spot) <= 15);
  const pick = selected ?? list.reduce((b, q) => (Math.abs(q.strike - spot) < Math.abs(b.strike - spot) ? q : b), list[0] ?? {}).strike;
  $("strike").replaceChildren(...list.map((q) => el("option", { value: q.strike, selected: q.strike === pick },
    `${q.strike}  —  now ${money(q.bid)} / ${money(q.ask)}`)));
  $("strike").disabled = !list.length;
}

function fillForm(p) {
  document.querySelector(`input[name="type"][value="${p.type}"]`).checked = true;
  populateStrikes(p.strike);
  $("paid").value = p.paid.toFixed(2);
  $("contracts").value = p.contracts;
}

function readForm() {
  const paid = Number($("paid").value), contracts = Math.max(1, Math.round(Number($("contracts").value) || 1));
  if (!(paid > 0)) throw new Error("Enter the price you paid per share (e.g. 0.61).");
  return { type: formType(), strike: Number($("strike").value), paid, contracts, expiry: state.market?.expiry };
}

// ---------- loop ----------
async function refresh() {
  clearTimeout(state.timer);
  $("refresh").classList.add("spinning");
  try {
    [state.market, state.spy] = await Promise.all([api("/api/market"), api("/api/spy").catch(() => null)]);
    renderTicker(state.market, state.spy);
    if (!state.pos) { populateStrikes(); drawChart(); }
    if (state.pos && state.pos.expiry && state.pos.expiry !== state.market.expiry) {
      renderExpired(state.pos);
    } else if (state.pos) {
      const p = state.pos;
      const data = await api(`/api/check?${new URLSearchParams({ type: p.type, strike: p.strike, premium: p.paid.toFixed(2) })}`);
      render(data);
    }
  } catch (err) {
    $("notes").replaceChildren(el("div", { class: "note error", role: "alert" }, icon(ICONS.expensive), el("span", {}, err.message)));
  } finally {
    $("refresh").classList.remove("spinning");
    if (state.market?.market_open) state.timer = setTimeout(refresh, REFRESH_MS);
  }
}

function drawChart() {
  const t = state.last?.trade, p = state.pos, spy = state.spy;
  const lines = t && p ? [{ price: t.strike, label: "Strike", kind: "strike" }, { price: t.breakeven, label: "Your breakeven", kind: "breakeven" }] : [];
  $("live-chip").hidden = !spy?.live;
  $("chart-sub").textContent = spy ? (spy.live ? "5-min" : `Last session · ${expiryLabel(spy.date)}`) : "Price data unavailable";
  drawSpyChart($("spy-chart"), spy, { lines, height: 320 });
}

// ---------- render ----------
function setTracking(on) {
  for (const id of ["result-body", "pnl-card", "hold-card", "rules-card"]) $(id).hidden = !on;
  $("empty-state").hidden = on;
}

function renderExpired(p) {
  state.last = null;
  setTracking(false);
  drawChart();
  $("notes").replaceChildren();
  $("empty-state").replaceChildren(
    el("h2", { class: "empty-title" }, `Your ${p.type} ${p.strike} expired`),
    el("p", { class: "card-sub" }, `Expired ${expiryLabel(p.expiry)}, 4:00 PM. Pick a ${expiryLabel(state.market.expiry)} strike to track a new one.`));
  $("submit").textContent = "Track this position";
}


function render(data) {
  const p = state.pos, t = data.trade, ev = data.evidence;
  state.last = data;
  setTracking(true);
  drawChart();

  // "Dropped quotes" is routine; "premium above the ask" is a pre-trade warning, meaningless for a price already paid
  const notes = data.notes.filter((n) => !n.startsWith("Dropped ") && !n.startsWith("Premium $"));
  if (!data.market.market_open) notes.unshift(`Market closed · showing the ${expiryLabel(data.market.expiry)} expiry · live from 9:30 AM ET`);
  $("notes").replaceChildren(...notes.map((n) => el("div", { class: "note" }, icon(ICONS.info), el("span", {}, n))));

  // P&L: value if sold now = bid
  const mult = 100 * p.contracts;
  const pnl = (t.bid - p.paid) * mult, pnlPct = t.bid / p.paid - 1;
  $("pnl-label").textContent = `${p.type.toUpperCase()} ${p.strike} × ${p.contracts} @ ${money(p.paid)}`;
  const v = $("pnl-value");
  v.className = `pnl-value ${pnl >= 0 ? "pos" : "neg"}`;
  v.replaceChildren(signedMoney0(pnl), el("span", { class: "pnl-pct" }, ` (${pnlPct >= 0 ? "+" : "−"}${pct(Math.abs(pnlPct))})`));
  $("pnl-sub").textContent = `If sold now at the ${money(t.bid)} bid`;
  recordHistory(pnl);
  drawSpark();

  renderHold(ev, t, p);
  renderTiles(t, ev, p);
  renderRules(ev);
  renderWhatIf($("whatif"), data.scenarios, {
    contracts: p.contracts,
    caption: `${p.contracts} contract${p.contracts > 1 ? "s" : ""} at ${money(p.paid)} · IV held at ${pct(data.scenarios.iv, 1)}`,
  });
}

function zGroup(z) {
  return z < 0 ? "in the money" : z < 0.5 ? "near the money (0–0.5 moves out)" : "far out of the money (0.5+ moves)";
}

function renderHold(ev, t, p) {
  const h = ev?.hold_vs_sell, line = $("hold-line");
  if (!h) {
    line.textContent = "No close match in history";
    $("hold-sub").textContent = t.minutes_left > 390 ? "Same-day positions only." : "Too few similar past positions.";
    $("hold-stats").replaceChildren();
    $("hold-fine").textContent = "";
    return;
  }
  const r = h.return_per_dollar, ex = h.ex_best_day;
  let verdict, cls;
  if (h.ci_high < 0 && (ex ?? r) < 0) { verdict = "History favors selling"; cls = "neg"; }
  else if (h.ci_low > 0 && (ex ?? r) > 0) { verdict = "History favors holding"; cls = "pos"; }
  else { verdict = "No clear edge either way"; cls = ""; }
  line.replaceChildren(el("span", { class: cls }, verdict));
  $("hold-sub").textContent = `${h.trades.toLocaleString()} similar positions · ${h.t_label}`;
  const stat = (k, val, sub) => el("div", { class: "mini-stat" }, el("div", { class: "k" }, k), el("div", { class: "v" }, val), el("div", { class: "s" }, sub));
  $("hold-stats").replaceChildren(
    stat("Holding beat selling", pct(h.hold_won), ""),
    stat("Holding vs selling", cents(r), `per $1 · range ${cents(h.ci_low)} to ${cents(h.ci_high)}`),
    stat("Without the best day", ex == null ? "—" : cents(ex), ""),
  );
  $("hold-fine").textContent = "";
}

function renderTiles(t, ev, p) {
  const mult = 100 * p.contracts;
  const stat = (k, v, sub) => el("div", { class: "pnl-stat" }, el("div", { class: "k" }, k), el("div", { class: "v" }, v), el("div", { class: "s" }, sub));
  $("pnl-stats").replaceChildren(
    stat("Breakeven", money(t.breakeven), `SPY ${t.move_needed >= 0 ? "+" : "−"}${money(Math.abs(t.move_needed))}`),
    stat("Chance of profit", pct(ev ? ev.realistic_profit : t.prob_profit), ""),
    stat("Time decay", `${money(t.theta_per_hour * mult)}/hr`, ""),
    stat("Time left", duration(t.minutes_left), ""),
  );
  const row = (k, v, sub) => [el("dt", {}, k), el("dd", {}, v, sub ? el("small", {}, sub) : null)];
  $("ticket-stats").replaceChildren(
    ...row("Bid / Ask now", `${money(t.bid)} / ${money(t.ask)}`, `mid ${money(t.mid)}`),
    ...row("Position value", money(t.bid * mult, 0), `paid ${money(p.paid * mult, 0)}`),
    ...row("Implied vol", pct(t.iv, 1), `ATM ${pct(t.expected.atm_iv, 1)}`),
    ...row("Delta / Gamma", `${t.delta.toFixed(2)} / ${t.gamma.toFixed(3)}`),
    ...row("Market odds", `${pct(t.prob_itm)} in the money`),
  );
}

function renderRules(ev) {
  const table = $("rules-table");
  if (!ev?.exit_rules_by_group) { table.replaceChildren(); $("rules-sub").textContent = "Not available."; return; }
  const group = zGroup(ev.z);
  const rows = ev.exit_rules_by_group.filter((r) => r.group === group).sort((a, b) => b.return_per_dollar - a.return_per_dollar);
  $("rules-sub").textContent = `Options ${group} · return per $1, higher is better`;
  const best = rows[0]?.rule;
  table.replaceChildren(
    el("thead", {}, el("tr", {}, el("th", {}, "Rule"), el("th", { class: "num" }, "Trades"), el("th", { class: "num" }, "Won"),
      el("th", { class: "num" }, "Per $1"), el("th", { class: "num" }, "w/o best day"))),
    el("tbody", {}, ...rows.map((r) => el("tr", { class: r.rule === best ? "used" : "" },
      el("td", {}, r.label, r.rule === best ? el("span", { class: "used-tag" }, "best") : null),
      el("td", { class: "num" }, r.trades.toLocaleString()), el("td", { class: "num" }, pct(r.win_rate)),
      el("td", { class: "num" }, cents(r.return_per_dollar)), el("td", { class: "num" }, r.ex_best_day == null ? "—" : cents(r.ex_best_day))))));
}

// ---------- P&L history sparkline (this browser only) ----------
function recordHistory(pnl) {
  const now = Date.now();
  state.history.push({ t: now, v: pnl });
  state.history = state.history.filter((h) => now - h.t < 3 * 3600_000).slice(-720);
  store.set({ ...state.pos, history: state.history });
}

function drawSpark() {
  const box = $("spark"), pts = state.history;
  if (pts.length < 2) { box.replaceChildren(el("figcaption", {}, "Chart fills in as updates arrive")); return; }
  const W = 220, H = 64, pad = 4;
  const ts = pts.map((p) => p.t), vs = pts.map((p) => p.v);
  const t0 = Math.min(...ts), t1 = Math.max(...ts), lo = Math.min(0, ...vs), hi = Math.max(0, ...vs);
  const x = (t) => pad + ((t - t0) / (t1 - t0 || 1)) * (W - 2 * pad);
  const y = (v) => pad + (1 - (v - lo) / (hi - lo || 1)) * (H - 2 * pad);
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, role: "img",
    "aria-label": `P/L moved from ${signedMoney0(vs[0])} to ${signedMoney0(vs[vs.length - 1])} since tracking started` });
  svg.append(svgEl("line", { class: "spark-zero", x1: pad, x2: W - pad, y1: y(0), y2: y(0) }));
  svg.append(svgEl("path", { class: "spark-line", d: pts.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join("") }));
  const last = pts[pts.length - 1];
  svg.append(svgEl("circle", { class: "spark-dot", cx: x(last.t), cy: y(last.v), r: 4 }));
  box.replaceChildren(svg, el("figcaption", {}, `since ${new Date(t0).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`));
}

// ---------- events ----------
document.querySelectorAll('input[name="type"]').forEach((r) => r.addEventListener("change", () => populateStrikes()));
$("strike").addEventListener("change", () => {
  const q = (state.market?.quotes[formType()] ?? []).find((x) => x.strike === Number($("strike").value));
  if (q && !$("paid").value) $("paid").value = q.ask.toFixed(2);
});
$("pos-form").addEventListener("submit", (e) => {
  e.preventDefault();
  try {
    const p = readForm();
    const same = state.pos && p.type === state.pos.type && p.strike === state.pos.strike && p.paid === state.pos.paid;
    if (!same) state.history = [];
    state.pos = p;
    store.set({ ...p, history: state.history });
    $("submit").textContent = "Update position";
    refresh();
  } catch (err) {
    $("notes").replaceChildren(el("div", { class: "note error", role: "alert" }, icon(ICONS.expensive), el("span", {}, err.message)));
  }
});
$("refresh").addEventListener("click", refresh);

// start: URL (?type=call&strike=772&paid=0.61) > saved position > empty form
(async function start() {
  const q = new URLSearchParams(location.search);
  const saved = store.get();
  try { state.market = await api("/api/market"); renderTicker(state.market, null); } catch (e) { /* refresh() will show the error */ }
  if (q.get("strike") && q.get("paid")) {
    state.pos = { type: q.get("type") === "put" ? "put" : "call", strike: Number(q.get("strike")), paid: Number(q.get("paid")),
                  contracts: 1, expiry: state.market?.expiry };
    store.set({ ...state.pos, history: [] });
    history.replaceState(null, "", "track.html");      // keep the address clean; the position is saved
  } else if (saved?.strike) {
    state.pos = { type: saved.type, strike: saved.strike, paid: saved.paid, contracts: saved.contracts || 1, expiry: saved.expiry };
    state.history = Array.isArray(saved.history) ? saved.history : [];
  }
  if (state.pos) { fillForm(state.pos); $("submit").textContent = "Update position"; }
  else populateStrikes();
  refresh();
})();
