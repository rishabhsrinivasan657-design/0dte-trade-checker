// Strategy Lab page: today's live setups (from /api/strategies) next to each strategy's backtest.
import { $, el, icon, ICONS, money, pct, odds, expiryLabel, setupThemeToggle } from "./common.js";
import { renderTicker } from "./spychart.js";

setupThemeToggle();
const REFRESH_MS = 30_000;
let timer;

async function api(path) {
  const res = await fetch(path, { headers: { Accept: "application/json" } });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : `Request failed (${res.status})`);
  return body;
}

// ---------- small builders ----------
const signedPct = (r, d = 1) => (r == null ? "—" : `${r >= 0 ? "+" : "−"}${Math.abs(r * 100).toFixed(d)}%`);
const VERDICT_CLASS = (v = "") => v.startsWith("Too few") ? "typical" : v.startsWith("Worked after") ? "good" : v.startsWith("Positive") ? "neutral"
  : v.startsWith("Worked only") ? "poor" : "avoid";
const put = (id, ...kids) => $(id).replaceChildren(...kids.flat().filter((k) => k != null));
const chip = (text, cls) => el("span", { class: `rating-chip ${cls}` }, text);

function sig(id, text, cls) {
  const node = $(id);
  node.className = `sig ${cls}`;
  node.textContent = text;
}

function setupBlock(c, title) {
  if (!c) return el("p", { class: "model-empty" }, "No clean quotes for this setup right now.");
  return el("div", { class: "setup" },
    el("h3", {}, title),
    el("table", { class: "legs" }, el("tbody", {}, ...c.legs.map((l) => el("tr", { class: l.action },
      el("td", {}, l.action === "sell" ? "Sell" : "Buy"), el("td", {}, `${l.strike} ${l.type}`), el("td", { class: "num" }, money(l.price)))))),
    el("dl", { class: "stat-list compact" },
      el("dt", {}, "Credit"), el("dd", {}, `${money(c.credit)}`, el("small", {}, `${money(c.credit * 100, 0)} per condor`)),
      el("dt", {}, "Max loss"), el("dd", {}, money(c.max_loss), el("small", {}, `${money(c.max_loss * 100, 0)} per condor`)),
      el("dt", {}, "Keeps credit if SPY ends"), el("dd", {}, `${c.short_strikes[0]}–${c.short_strikes[1]}`, el("small", {}, `market odds ${odds(c.chance_max_profit)}`)),
      el("dt", {}, "Profit zone"), el("dd", {}, `${money(c.breakevens[0])}–${money(c.breakevens[1])}`, el("small", {}, `market odds ${odds(c.chance_profit)}`))),
    c.stop ? el("p", { class: "stop-rule" }, icon(ICONS.info), el("span", {}, c.stop)) : null);
}

// one backtest row: label · return (with 90% range) · win rate · verdict
function btTable(rows, { label, basis = "per $1 risked", highlight, fmt = signedPct, unit = "trades" } = {}) {
  const max = Math.max(0.01, ...rows.map((r) => Math.max(Math.abs(r.ci_low ?? 0), Math.abs(r.ci_high ?? 0), Math.abs(r.ret))));
  const bar = (r) => el("div", { class: "mbar" },
    el("span", { class: "mbar-zero" }),
    el("span", { class: "mbar-ci", style: { left: `${50 + (r.ci_low / max) * 50}%`, width: `${((r.ci_high - r.ci_low) / max) * 50}%` } }),
    el("span", { class: `mbar-dot ${r.ret >= 0 ? "pos" : "neg"}`, style: { left: `${50 + (r.ret / max) * 50}%` } }));
  return el("div", { class: "bt-wrap" }, el("table", { class: "bt-table" },
    el("thead", {}, el("tr", {}, el("th", {}, label), el("th", { class: "num" }, `Return ${basis}`), el("th", { class: "bar-cell" }, ""), el("th", { class: "num" }, "Won"), el("th", {}, ""))),
    el("tbody", {}, ...rows.map((r) => el("tr", { class: highlight && highlight(r) ? "used" : "" },
      el("td", {}, r.label, r.n ? el("div", { class: "cell-sub" }, `${r.n.toLocaleString()} ${unit}`) : null),
      el("td", { class: "num strong" }, fmt(r.ret)),
      el("td", { class: "bar-cell" }, bar(r)),
      el("td", { class: "num" }, r.win == null ? "—" : pct(r.win)),
      el("td", {}, r.verdict ? chip(r.verdict.replace("Positive after costs, but within the range of luck", "Within luck").replace("Worked only before trading costs", "Only before costs").replace("Too few days to tell", "Too few days"), VERDICT_CLASS(r.verdict)) : null))))));
}

const row = (r, label) => ({ label, ret: r.return_per_dollar, ci_low: r.ci_low, ci_high: r.ci_high, win: r.win_rate, n: r.trades, verdict: r.verdict });

// ---------- cards ----------
function renderGate(s, lab) {
  const live = s.live;
  if (!live) sig("gate-sig", "Market closed", "off");
  else if (s.gate === "sell") sig("gate-sig", "Sell signal", "on");
  else if (s.gate === "hold off") sig("gate-sig", "No signal", "off");
  else sig("gate-sig", "ML unavailable", "off");
  const ratio = s.ml_ratio;
  put("gate-live", 
    el("p", { class: "live-line" }, !live ? "Signals run during market hours."
      : ratio == null ? "ML forecast unavailable right now."
      : `ML expects a ${Math.abs((1 - ratio) * 100).toFixed(0)}% ${ratio < 1 ? "smaller" : "bigger"} move than priced (sell when below ${Math.round((1 - s.gate_threshold) * 100)}% smaller).`),
    live ? setupBlock(s.condor, "Today's condor") : null);
  const c = lab?.short_condor, st = lab?.short_straddle;
  if (!c) { put("gate-bt", el("p", { class: "model-empty" }, "No backtest results yet.")); return; }
  const gates = c.by_ml_gate.map((r) => row(r, r.ml_gate));
  const all = row(c.all[0], "Every condor");
  const s0 = st.all[0];
  put("gate-bt", 
    el("h3", {}, "Backtest · iron condors"),
    btTable([...gates, all], { label: "When the ML said…", highlight: (r) => r.label.startsWith("ML: smaller") }),
    el("p", { class: "fineprint" },
      `Straddles (unlimited risk): ${signedPct(s0.return_per_dollar)} per $1 collected, won ${pct(s0.win_rate)}, `,
      el("b", {}, `worst trade ${money(s0.worst_trade_contract, 0)} per contract (${s0.worst_day})`), "."));
}

function renderBreakeven(s, lab) {
  sig("be-sig", s.live ? "Rule-based" : "Market closed", s.live ? "info" : "off");
  put("be-live", s.live ? setupBlock(s.breakeven, "Today's setup") : el("p", { class: "live-line" }, "Signals run during market hours."));
  const b = lab?.breakeven_condor;
  if (!b) { put("be-bt"); return; }
  const rows = b.by_variant_time.map((r) => row(r, `${r.variant === "held to 4 PM" ? "No stops" : "With stops"} · ${r.time}`));
  put("be-bt", 
    el("h3", {}, "Backtest · does the stop help?"),
    btTable(rows, { label: "Version · entry", highlight: (r) => r.label.startsWith("With stops") }),
    el("p", { class: "fineprint" }, `${pct(b.stop_rate)} of trades hit a stop.`));
}

function renderMomentum(s, lab) {
  const m = s.momentum;
  if (m.state === "set") sig("mom-sig", `${m.direction.toUpperCase()}${m.strong ? " · strong" : ""}`, "info");
  else sig("mom-sig", m.state === "waiting" ? "Waiting for 10:00" : "Not available", "off");
  put("mom-live", el("p", { class: "live-line" }, m.text),
    m.state === "set" ? el("p", { class: "cell-sub" }, m.window === "open" ? "Entry window is open (3:30–4:00 PM)." : m.window === "later" ? "Entry at 3:30 PM ET." : "Closed for today.") : null);
  const mo = lab?.momentum;
  if (!mo) { put("mom-bt"); return; }
  const u = Object.entries(mo.underlying).map(([k, v]) => ({ label: k, ret: v.avg_bp / 1e4, ci_low: v.ci_low_bp / 1e4, ci_high: v.ci_high_bp / 1e4, win: v.hit_rate, n: v.days,
    verdict: v.ci_low_bp > 0 ? "Worked after costs" : v.avg_bp > 0 ? "Positive after costs, but within the range of luck" : "Did not work" }));
  const o = [...mo.options.map((r) => row(r, "Buy ATM option · every day")), ...mo.options_strong.map((r) => row(r, "Buy ATM option · strong days"))];
  put("mom-bt", 
    el("h3", {}, "Backtest · SPY 3:30→4:00 in the signal's direction"),
    btTable(u, { label: "Sample", basis: "per day (SPY)", unit: "days", fmt: (r) => `${r >= 0 ? "+" : "−"}${Math.abs(r * 1e4).toFixed(2)} bp` }),
    el("p", { class: "fineprint" }, "1 bp = 0.01%. A real effect would need to clear trading costs of several bp."),
    el("h3", { style: { marginTop: "14px" } }, "As an options trade (2024–2026)"),
    btTable(o, { label: "Version", basis: "per $1 paid" }));
}

function renderTiming(s, lab) {
  const ev = s.event;
  sig("time-sig", ev ? `Today: ${ev} day` : "No scheduled event today", ev ? "on" : "off");
  const c = lab?.short_condor;
  if (!c) { put("time-bt"); return; }
  const best = [...c.by_time].sort((a, b) => b.return_per_dollar - a.return_per_dollar)[0];
  const order = ["no event", "CPI", "FOMC", "FOMC+CPI"];
  put("time-bt", 
    el("div", { class: "two-up" },
      el("div", {}, el("h3", {}, "Iron condor by entry time"),
        btTable(c.by_time.map((r) => row(r, `${r.time}`)), { label: "Entered at", highlight: (r) => r.label === best.time })),
      el("div", {}, el("h3", {}, "Iron condor by day type"),
        btTable(order.map((k) => c.by_event.find((r) => r.event === k)).filter(Boolean).map((r) => row(r, r.event === "no event" ? "Normal day" : `${r.event} day`)),
          { label: "Day", highlight: (r) => ev && r.label.startsWith(ev) }),
        el("p", { class: "fineprint" }, "Event-day samples are small, so their ranges are wide."))));
}

// ---------- page ----------
async function refresh() {
  clearTimeout(timer);
  $("refresh").classList.add("spinning");
  try {
    const [m, spy, data] = await Promise.all([api("/api/market"), api("/api/spy").catch(() => null), api("/api/strategies")]);
    renderTicker(m, spy);
    const s = data.signals, lab = data.lab;
    put("lab-today", 
      chip(data.market.live ? "Live" : `Market closed · ${expiryLabel(data.market.expiry)} next`, data.market.live ? "good" : "typical"),
      s.event ? chip(`${s.event} day`, "poor") : null,
      s.ml_ratio != null ? chip(`ML/market move ${s.ml_ratio.toFixed(2)}`, "typical") : null);
    put("notes", ...data.notes.filter((n) => !n.startsWith("Dropped ")).map((n) => el("div", { class: "note" }, icon(ICONS.info), el("span", {}, n))));
    renderGate(s, lab);
    renderBreakeven(s, lab);
    renderMomentum(s, lab);
    renderTiming(s, lab);
    if (data.market.live) timer = setTimeout(refresh, REFRESH_MS);
  } catch (err) {
    put("notes", el("div", { class: "note error", role: "alert" }, icon(ICONS.expensive), el("span", {}, err.message)));
  } finally {
    $("refresh").classList.remove("spinning");
  }
}

$("refresh").addEventListener("click", refresh);
refresh();
