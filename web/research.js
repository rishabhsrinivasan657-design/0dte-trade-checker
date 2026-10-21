// Research page: renders web/research.json (made by scripts/run_research.py).
import { $, el, svgEl, pct, setupThemeToggle } from "./common.js";

setupThemeToggle();

const NAMES = {
  "market (ATM implied vol)": "Market (options prices)",
  seasonal: "Time-of-day rule",
  har: "HAR (classic finance model)",
  gbm: "ML model (gradient boosting)",
  gbm_vix1d: "ML model + VIX1D (used live)",
  "blend (market × gbm)": "Blend: market × ML",
};
const COLOR = {
  market: "var(--series-1)", history: "var(--series-2)", model: "var(--series-3)",
  neutral: "var(--muted)",
};
const modelColor = (name) => name.startsWith("market") ? COLOR.market : name.startsWith("gbm") ? COLOR.model
  : name.startsWith("blend") ? "color-mix(in srgb, var(--series-1) 50%, var(--series-3))" : COLOR.neutral;
const cents = (r) => `${r >= 0 ? "+" : "−"}${Math.abs(r * 100).toFixed(0)}¢`;
const signedPct = (r, d = 0) => `${r >= 0 ? "+" : "−"}${Math.abs(r * 100).toFixed(d)}%`;

async function main() {
  let R;
  try {
    const res = await fetch("research.json", { cache: "no-cache" });
    if (!res.ok) throw new Error(res.status);
    R = await res.json();
  } catch (e) {
    $("lede").textContent = `${bt.trades.toLocaleString()} simulated 0DTE trades (${R.options_range[0].slice(0, 4)}–${R.options_range[1].slice(0, 4)}) and ` +
    `${R.ml.days.toLocaleString()} days of SPY data (${R.spy_range[0].slice(0, 4)}–${R.spy_range[1].slice(0, 4)}).`;
    return;
  }
  render(R);
  let w = 0;
  new ResizeObserver(([e]) => {
    if (Math.round(e.contentRect.width) !== w) { w = Math.round(e.contentRect.width); render(R); }
  }).observe(document.querySelector(".research"));
}

function render(R) {
  const bt = R.backtest;
  $("lede").textContent =
    `${R.ml.rows.toLocaleString()} moments on ${R.ml.days.toLocaleString()} trading days of SPY data (${R.spy_range[0]} to ${R.spy_range[1]}) ` +
    `to train and test the model, and ${bt.trades.toLocaleString()} simulated 0DTE option purchases on ${bt.days} days ` +
    `(${R.options_range[0]} to ${R.options_range[1]}) to see what actually made money.`;
  $("generated").textContent = `Results generated ${R.generated.replace("T", " ")} ET.`;
  renderFindings(R);
  renderVs(R);
  renderScore(R);
  renderBacktest(R);
  renderExits(R);
  renderStraddles(R);
  renderCalibration(R);
  renderYears(R);
  renderCosts(R);
}

// ---------- findings (computed, never hard-coded) ----------
function renderFindings(R) {
  const s = Object.fromEntries(R.model_vs_market.scores.map((r) => [r.model, r]));
  const mkt = s["market (ATM implied vol)"], ml = s.gbm_vix1d || s.gbm, blend = s["blend (market × gbm)"];
  const better = ml.qlike < mkt.qlike;
  const all = R.backtest.all[0];
  const hv = Object.fromEntries(R.backtest.by_history_verdict.map((r) => [r.verdict, r]));
  const mv = Object.fromEntries(R.backtest.by_model_verdict.map((r) => [r.model_verdict, r]));
  const verdictLine = (v, who) => v.Cheap && v.Expensive
    ? `${who}: "Cheap" options returned ${cents(v.Cheap.return_per_dollar)} per $1, "Expensive" ones ${cents(v.Expensive.return_per_dollar)}.`
    : `${who}: not enough trades in each group.`;
  const blendBest = blend.qlike < Math.min(mkt.qlike, ml.qlike);
  const cards = [
    { k: "Forecasting", h: better
        ? `The ML model was more accurate: ${pct(1 - ml.qlike / mkt.qlike)} less error than the market`
        : `The market was more accurate: the ML model had ${pct(ml.qlike / mkt.qlike - 1)} more error`,
      p: `Forecast error (QLIKE, lower is better) on ${R.model_vs_market.moments.toLocaleString()} matching moments: market ${mkt.qlike.toFixed(3)}, ML ${ml.qlike.toFixed(3)}.` +
         (blendBest ? ` Averaging the two was best of all (${blend.qlike.toFixed(3)}): they make different mistakes.` : "") },
    { k: "Buying 0DTE", h: `Every $1 of options bought returned ${cents(all.return_per_dollar)}`,
      p: `Win rate ${pct(all.win_rate)} across ${all.trades.toLocaleString()} trades; 90% range ${cents(all.ci_low)} to ${cents(all.ci_high)} per $1, after assumed costs.` },
    { k: "Do the verdicts help?", h: verdictHeadline(hv, mv),
      p: `${verdictLine(hv, "History check")} ${verdictLine(mv, "ML model")}` },
  ];
  $("findings").replaceChildren(...cards.map((c) => el("article", { class: "card finding" },
    el("p", { class: "eyebrow" }, c.k), el("h2", {}, c.h), el("p", {}, c.p))));
}

function verdictHeadline(hv, mv) {
  const gap = (v) => (v.Cheap && v.Expensive) ? v.Cheap.return_per_dollar - v.Expensive.return_per_dollar : 0;
  const clear = (v) => v.Cheap && v.Expensive && v.Expensive.ci_high < v.Cheap.ci_low;   // ranges don't overlap
  if (clear(mv) && !clear(hv)) return `Only the ML model's "Expensive" warning clearly flagged worse trades`;
  if (clear(mv) && clear(hv)) return `Both "Expensive" warnings flagged worse trades`;
  if (gap(mv) > 0 || gap(hv) > 0) return `"Expensive" options did worse, but not clearly beyond luck`;
  return "The verdicts didn't separate good trades from bad";
}

// ---------- model vs market ----------
function renderVs(R) {
  const rows = [...R.model_vs_market.scores].sort((a, b) => a.qlike - b.qlike);
  const best = rows[0].model;
  $("vs-sub").textContent = `${R.model_vs_market.moments.toLocaleString()} moments · shorter bar = smaller error`;
  const max = Math.max(...rows.map((r) => r.qlike));
  $("vs-chart").replaceChildren(...rows.map((r) => el("div", { class: "compare-row" },
    el("div", { class: "compare-label" }, el("span", { class: "swatch", style: { background: modelColor(r.model) } }),
      NAMES[r.model] ?? r.model, r.model === best ? el("span", { class: "used-tag" }, "best") : null),
    el("div", { class: "track" }, el("div", { class: "fill", style: { width: `${(r.qlike / max) * 100}%`, background: modelColor(r.model) } })),
    el("div", { class: "compare-value" }, r.qlike.toFixed(3)))));
  table($("vs-table"), ["Forecast", "Error (QLIKE)", "Typical miss", "Inside ±1 sd"],
    rows.map((r) => [NAMES[r.model] ?? r.model, r.qlike.toFixed(3), `${Math.round((Math.exp(r.log_mae) - 1) * 100)}%`, pct(r.within_1sd)]),
    "Inside ±1 sd should be ~68%.");
}

// ---------- backtest ----------
function renderBacktest(R) {
  const b = R.backtest;
  $("bt-sub").textContent = "Calls and puts within $5 of SPY, bought 10 AM–3 PM, held to 4 PM";
  $("bt-legend").replaceChildren(
    legendItem(COLOR.neutral, "All trades"), legendItem(COLOR.history, "Grouped by history verdict"), legendItem(COLOR.model, "Grouped by ML verdict"));
  const order = ["Cheap", "Fair", "Expensive"];
  const pick = (arr, key) => order.map((v) => arr.find((r) => r[key] === v)).filter(Boolean);
  dotChart($("bt-chart"), [
    { title: "", rows: b.all.map((r) => row(r, "All trades", COLOR.neutral)) },
    { title: "History check (v1)", rows: pick(b.by_history_verdict, "verdict").map((r) => row(r, r.verdict, COLOR.history)) },
    { title: "ML model (v2)", rows: pick(b.by_model_verdict, "model_verdict").map((r) => row(r, r.model_verdict, COLOR.model)) },
  ]);
  dotChart($("otm-chart"), [{ title: "", rows: b.by_otm.map((r) => row(r, r.bucket, COLOR.neutral)) }]);
  dotChart($("time-chart"), [{ title: "", rows: b.by_time.map((r) => row(r, `${r.time}  (${16 - +r.time.slice(0, 2)}h left)`, COLOR.neutral)) }]);
}

function renderScore(R) {
  const S = R.trade_score;
  if (!S) return;
  const order = ["avoid", "poor", "typical"];
  const ratings = order.map((k) => S.ratings.find((r) => r.rating === k)).filter(Boolean);
  $("score-sub").textContent = `Tested on months the model never saw · ${S.oos_trades.toLocaleString()} trades`;
  dotChart($("score-chart"), [
    { title: "Rating shown in the Checker", rows: ratings.map((r) => row(r, r.label, r.rating === "avoid" ? "var(--critical)" : r.rating === "poor" ? "#ec835a" : COLOR.neutral)) },
    { title: "ML score, inside the same distance & time group", rows: S.within_group.map((r) => row(r, r.within, COLOR.model)) },
    { title: "ML move forecast vs market, inside the same group", rows: S.ml_ratio_within_group.map((r) => row(r, r.mlr, COLOR.model)) },
  ]);
  const up = S.within_group.find((r) => r.within === "upper third"), lo = S.within_group.find((r) => r.within === "lower third");
  $("score-fine").textContent = up && lo && Math.abs(up.return_per_dollar - lo.return_per_dollar) < 0.03
    ? "Most of the rating's power comes from distance and time left; the ML move forecast adds a smaller signal."
    : "The score still separates outcomes inside groups of similar trades.";
}

function row(r, label, color) {
  return { label, value: r.return_per_dollar, low: r.ci_low, high: r.ci_high, color,
    detail: `${r.trades.toLocaleString()} trades · ${r.days} days · win rate ${pct(r.win_rate)}` };
}

function renderExits(R) {
  if (!R.exits) return;
  const groups = ["in the money", "near the money (0–0.5 moves out)", "far out of the money (0.5+ moves)"];
  const order = (rows) => [...rows].sort((a, b) => b.return_per_dollar - a.return_per_dollar);
  const hold = R.exits.rules.find((r) => r.rule === "hold");
  const best = order(R.exits.rules)[0];
  $("exits-sub").textContent = `Best: ${best.label} (${cents(best.return_per_dollar)} vs ${cents(hold.return_per_dollar)} holding)`;
  dotChart($("exits-chart"), [
    { title: "All trades", rows: order(R.exits.rules).map((r) => ({ ...row(r, r.label, r.rule === "hold" ? COLOR.neutral : COLOR.market),
      detail: `${row(r, "", "").detail} · without best day ${cents(r.ex_best_day)}` })) },
    ...groups.map((g) => ({ title: g, rows: order(R.exits.by_group.filter((r) => r.group === g)).map((r) =>
      ({ ...row(r, r.label, r.rule === "hold" ? COLOR.neutral : COLOR.market), detail: `${row(r, "", "").detail} · without best day ${cents(r.ex_best_day)}` })) })),
  ]);
  const hs = R.exits.hold_vs_sell_all?.[0];
  $("exits-fine").textContent = hs ? `"Without best day" shows how much one rare day (like 2025-04-09) drives an average.` : "";
}

function renderStraddles(R) {
  const s = R.straddles;
  const bounds = Object.fromEntries(s.ratio_bounds.map((r) => [r.signal, r]));
  $("straddle-sub").textContent = "Call + put at the money, grouped by ML vs market forecast";
  dotChart($("straddle-chart"), [{ title: "", rows: s.by_signal.map((r) => ({
    ...row(r, r.signal, COLOR.model),
    detail: `${row(r, "", "").detail} · model/market vol ${bounds[r.signal]?.min.toFixed(2)}–${bounds[r.signal]?.max.toFixed(2)}`,
  })) }]);
}

// Dot + interval chart in plain HTML: value dot, 90% range bar, zero line.
function dotChart(box, groups) {
  const all = groups.flatMap((g) => g.rows);
  let lo = Math.min(0, ...all.map((r) => r.low)), hi = Math.max(0, ...all.map((r) => r.high));
  const pad = (hi - lo) * 0.06 || 0.1;
  lo -= pad; hi += pad;
  const x = (v) => ((v - lo) / (hi - lo)) * 100;
  const ticks = niceTicks(lo, hi, 5).filter((t) => t >= lo && t <= hi);
  const parts = [];
  for (const g of groups) {
    if (g.title) parts.push(el("div", { class: "dot-group" }, g.title));
    for (const r of g.rows) {
      parts.push(el("div", { class: "dot-row", tabindex: "0", "aria-label": `${r.label}: ${signedPct(r.value)} per dollar (90% range ${signedPct(r.low)} to ${signedPct(r.high)})` },
        el("div", { class: "dot-label" }, el("div", {}, r.label), el("div", { class: "cell-sub" }, r.detail)),
        el("div", { class: "dot-plot" },
          ...ticks.map((t) => el("div", { class: t === 0 ? "dot-zero" : "dot-grid", style: { left: `${x(t)}%` } })),
          el("div", { class: "dot-ci", style: { left: `${x(r.low)}%`, width: `${x(r.high) - x(r.low)}%`, background: r.color } }),
          el("div", { class: "dot-mark", style: { left: `${x(r.value)}%`, background: r.color } })),
        el("div", { class: "dot-value" }, signedPct(r.value))));
    }
  }
  parts.push(el("div", { class: "dot-row dot-axis" }, el("div"),
    el("div", { class: "dot-plot" }, ...ticks.map((t) => el("span", { style: { left: `${x(t)}%` } }, signedPct(t)))), el("div")));
  box.replaceChildren(...parts);
}

function niceTicks(min, max, count = 4) {
  const span = max - min, step0 = span / count;
  const mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= step0);
  const out = [];
  for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) out.push(+v.toFixed(10));
  return out;
}

// ---------- calibration ----------
function renderCalibration(R) {
  const series = [
    { key: "market", label: "Market (Black-Scholes N(d₂))", color: COLOR.market },
    { key: "history", label: "History check", color: COLOR.history },
    { key: "model", label: "ML model", color: COLOR.model },
  ].map((s) => ({ ...s, points: R.calibration[s.key].filter((p) => p.n >= 30) }));
  $("cal-legend").replaceChildren(...series.map((s) => legendItem(s.color, s.label, true)));

  const fig = $("cal-chart");
  const W = Math.max(fig.clientWidth, 280), H = Math.min(360, Math.max(240, W * 0.55));
  const M = { l: 44, r: 12, t: 10, b: 34 }, iw = W - M.l - M.r, ih = H - M.t - M.b;
  const x = (v) => M.l + v * iw, y = (v) => M.t + (1 - v) * ih;
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Calibration: predicted probability vs actual frequency" });
  const grid = svgEl("g", { class: "grid" }), axis = svgEl("g", { class: "axis" });
  for (const v of [0, 0.25, 0.5, 0.75, 1]) {
    grid.append(svgEl("line", { x1: M.l, x2: W - M.r, y1: y(v), y2: y(v) }));
    const ty = svgEl("text", { x: M.l - 8, y: y(v) + 4, "text-anchor": "end" }); ty.textContent = pct(v); axis.append(ty);
    const tx = svgEl("text", { x: x(v), y: H - 14, "text-anchor": v === 0 ? "start" : v === 1 ? "end" : "middle" }); tx.textContent = pct(v); axis.append(tx);
  }
  const xl = svgEl("text", { x: M.l + iw / 2, y: H - 1, "text-anchor": "middle" }); xl.textContent = "predicted chance"; axis.append(xl);
  svg.append(grid, axis, svgEl("line", { class: "diag", x1: x(0), y1: y(0), x2: x(1), y2: y(1) }));
  const tip = $("tooltip");
  for (const s of series) {
    if (!s.points.length) continue;
    svg.append(svgEl("path", { class: "series", stroke: s.color,
      d: s.points.map((p, i) => `${i ? "L" : "M"}${x(p.predicted).toFixed(1)},${y(p.actual).toFixed(1)}`).join("") }));
    for (const p of s.points) {
      svg.append(svgEl("circle", { class: "hover-dot", cx: x(p.predicted), cy: y(p.actual), r: 4.5, fill: s.color }));
      const hit = svgEl("circle", { cx: x(p.predicted), cy: y(p.actual), r: 12, fill: "transparent", tabindex: "0" });
      const show = (e) => {
        tip.replaceChildren(el("div", { class: "tt-title" }, s.label),
          el("div", { class: "tt-row" }, el("span", {}, "Predicted"), el("b", {}, pct(p.predicted))),
          el("div", { class: "tt-row" }, el("span", {}, "Actually happened"), el("b", {}, pct(p.actual))),
          el("div", { class: "tt-row" }, el("span", {}, "Trades"), el("b", {}, p.n.toLocaleString())));
        tip.hidden = false;
        const r = hit.getBoundingClientRect();
        tip.style.left = `${Math.min(r.right + 8, innerWidth - tip.offsetWidth - 8)}px`;
        tip.style.top = `${r.top - 10}px`;
      };
      hit.addEventListener("pointerenter", show);
      hit.addEventListener("focus", show);
      hit.addEventListener("pointerleave", () => (tip.hidden = true));
      hit.addEventListener("blur", () => (tip.hidden = true));
      svg.append(hit);
    }
  }
  fig.replaceChildren(svg);

  const fix = R.realistic_chance;
  if (fix) {
    const f = (e) => `${e.brier_market.toFixed(4)} → ${e.brier_realistic.toFixed(4)}`;
    $("cal-fix").textContent = `The Checker's "realistic chance" uses this curve: error ${f(fix.profit)} on later, unseen days.`;
  }

  // table view of the same numbers
  const lowBin = (s) => s.points[0];
  table($("cal-table"), ["Source", "Trades", "Average miss", "Lowest bucket: said → happened"],
    series.map((s) => {
      const n = s.points.reduce((a, p) => a + p.n, 0);
      const miss = s.points.reduce((a, p) => a + Math.abs(p.actual - p.predicted) * p.n, 0) / n;
      const lo = lowBin(s);
      return [s.label, n.toLocaleString(), `${(miss * 100).toFixed(1)} pts`, `${pct(lo.predicted, 1)} → ${pct(lo.actual, 1)}`];
    }), "");
}

// ---------- years / costs ----------
function renderYears(R) {
  const years = [...new Set(R.ml.by_year.map((r) => r.year))];
  const models = ["seasonal", "har", "gbm"];
  const rows = years.map((y) => {
    const vals = models.map((m) => R.ml.by_year.find((r) => r.year === y && r.model === m)?.qlike);
    const best = Math.min(...vals);
    return [String(y), ...vals.map((v) => v === best ? el("b", {}, v.toFixed(3)) : v.toFixed(3))];
  });
  const overall = models.map((m) => R.ml.overall.find((r) => r.model === m).qlike);
  rows.push(["All years", ...overall.map((v) => v === Math.min(...overall) ? el("b", {}, v.toFixed(3)) : v.toFixed(3))]);
  table($("year-table"), ["Test year", NAMES.seasonal, NAMES.har, NAMES.gbm], rows, "Bold = best that year");
}

function renderCosts(R) {
  table($("cost-table"), ["Assumed spread cost", "Return per $1 (all trades)"],
    R.backtest.cost_sensitivity.map((c) => [
      c.spread_multiplier === 0 ? "None (trade price)" : c.spread_multiplier === 1 ? "Base case (1¢ or 1%)" : "Double",
      cents(c.return_per_dollar)]),
    "Sensitivity to the trading-cost assumption.");
}

// ---------- small builders ----------
function legendItem(color, label, line = false) {
  return el("span", {}, el("span", { class: line ? "line-key" : "swatch", style: { background: color } }), label);
}

function table(node, head, rows, caption) {
  const numeric = (i) => i > 0;
  node.replaceChildren(
    caption ? el("caption", {}, caption) : "",
    el("thead", {}, el("tr", {}, ...head.map((h, i) => el("th", { class: numeric(i) ? "num" : "" }, h)))),
    el("tbody", {}, ...rows.map((r) => el("tr", {}, ...r.map((c, i) => el("td", { class: numeric(i) ? "num" : "" }, c))))));
}

main();
