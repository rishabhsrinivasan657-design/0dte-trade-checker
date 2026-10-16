// SPY intraday price chart (SVG): today's 5-minute closes from 9:30 to 4:00, with
// yesterday's close, the market's expected range out to 4 PM, and optional strike / breakeven lines.
import { el, money, svgEl } from "./common.js";

const OPEN = 9 * 60 + 30, CLOSE = 16 * 60;
const toMin = (hhmm) => { const [h, m] = hhmm.split(":").map(Number); return h * 60 + m; };
const clock = (min) => { const h = Math.floor(min / 60), m = min % 60; return `${((h + 11) % 12) + 1}:${String(m).padStart(2, "0")}`; };
const X_TICKS = [570, 660, 720, 780, 840, 900, 960];   // 9:30, 11:00 ... 4:00

function niceStep(span, target = 5) {
  const raw = span / target, mag = 10 ** Math.floor(Math.log10(raw));
  return [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw);
}

/**
 * data: /api/spy response. lines: [{price, label, kind: "strike" | "breakeven"}].
 */
export function drawSpyChart(box, data, { lines = [], height = 300 } = {}) {
  box.replaceChildren();
  if (!data?.bars?.length) {
    box.append(el("p", { class: "chart-empty" }, "SPY price data isn't available right now."));
    return;
  }
  const W = Math.max(box.clientWidth, 320), H = height;
  const M = { l: 12, r: 76, t: 14, b: 26 };
  const iw = W - M.l - M.r, ih = H - M.t - M.b;

  const pts = data.bars.map((b) => ({ m: toMin(b.t) + 5, p: b.c }));            // bar END time
  if (data.live && data.last != null && data.now) pts.push({ m: Math.max(toMin(data.now), pts[pts.length - 1].m), p: data.last });
  const exp = data.live ? data.expected : null;
  const values = [...data.bars.flatMap((b) => [b.h, b.l]), data.prev_close, data.last,
    ...(exp ? [exp.low, exp.high] : []), ...lines.map((l) => l.price)].filter((v) => v != null);
  let lo = Math.min(...values), hi = Math.max(...values);
  const pad = (hi - lo) * 0.08 || 0.5;
  lo -= pad; hi += pad;
  const x = (m) => M.l + ((m - OPEN) / (CLOSE - OPEN)) * iw;
  const y = (p) => M.t + (1 - (p - lo) / (hi - lo)) * ih;

  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, class: "spy-svg", role: "img", tabindex: "0",
    "aria-label": `SPY today: ${money(pts[0].p)} at the open, ${money(pts[pts.length - 1].p)} now. Use arrow keys to read prices.` });

  // grid + price axis (right side, like trading platforms); axis numbers make way for the price tags
  const lastP = pts[pts.length - 1].p;
  const tagYs = [data.prev_close, lastP, ...lines.map((l) => l.price)].filter((v) => v != null && v >= lo && v <= hi).map(y);
  const step = niceStep(hi - lo);
  const grid = svgEl("g", { class: "grid" }), axis = svgEl("g", { class: "axis" });
  for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) {
    grid.append(svgEl("line", { x1: M.l, x2: W - M.r, y1: y(v), y2: y(v) }));
    if (tagYs.some((ty) => Math.abs(ty - y(v)) < 14)) continue;
    const t = svgEl("text", { x: W - M.r + 8, y: y(v) + 4 }); t.textContent = v.toFixed(step < 1 ? 2 : 0); axis.append(t);
  }
  const xticks = iw < 420 ? [570, 720, 840, 960] : X_TICKS;              // fewer labels on narrow screens
  for (const m of xticks) {
    const t = svgEl("text", { x: x(m), y: H - 7, "text-anchor": m === OPEN ? "start" : m === CLOSE ? "end" : "middle" });
    t.textContent = clock(m); axis.append(t);
  }
  svg.append(grid, axis);

  // expected range from now to 4 PM
  const lastPt = pts[pts.length - 1];
  if (exp) {
    svg.append(svgEl("rect", { class: "exp-band", x: x(lastPt.m), y: y(exp.high), width: Math.max(0, x(CLOSE) - x(lastPt.m)), height: y(exp.low) - y(exp.high) }));
    const t = svgEl("text", { class: "exp-label", x: x(CLOSE) - 6, y: y(exp.high) - 6, "text-anchor": "end" });
    t.textContent = `Expected range by 4 PM ±${money(exp.move)}`;
    svg.append(t);
  }

  // reference lines at their true price; their right-axis tags are nudged apart so they never overlap
  const refs = [{ price: data.prev_close, cls: "prev" }, ...lines.map((l) => ({ price: l.price, cls: l.kind })),
                { price: lastPt.p, cls: "last" }].filter((r) => r.price != null && r.price >= lo && r.price <= hi);
  for (const r of refs) {
    r.y = y(r.price);
    if (r.cls !== "last") svg.append(svgEl("line", { class: `ref ${r.cls}`, x1: M.l, x2: W - M.r, y1: r.y, y2: r.y }));
  }

  // price line + soft area
  const d = pts.map((p, i) => `${i ? "L" : "M"}${x(p.m).toFixed(1)},${y(p.p).toFixed(1)}`).join("");
  svg.append(svgEl("path", { class: "spy-area", d: `${d}L${x(lastPt.m).toFixed(1)},${M.t + ih}L${x(pts[0].m).toFixed(1)},${M.t + ih}Z` }));
  svg.append(svgEl("path", { class: "spy-line", d }));
  svg.append(svgEl("circle", { class: "spy-now", cx: x(lastPt.m), cy: y(lastPt.p), r: 4.5 }));

  const GAP = 19;
  const sorted = [...refs].sort((a, b) => a.y - b.y);
  sorted.forEach((r, i) => { r.ty = i ? Math.max(r.y, sorted[i - 1].ty + GAP) : r.y; });
  const overflow = sorted.length ? sorted[sorted.length - 1].ty - (M.t + ih) : 0;
  if (overflow > 0) sorted.forEach((r) => (r.ty -= overflow));             // keep the stack inside the plot
  for (const r of sorted) {
    const g = svgEl("g", { class: `tag ${r.cls}` });
    if (Math.abs(r.ty - r.y) > 1) g.append(svgEl("line", { class: "tag-leader", x1: W - M.r - 4, y1: r.y, x2: W - M.r + 2, y2: r.ty }));
    g.append(svgEl("rect", { x: W - M.r + 2, y: r.ty - 9, width: M.r - 4, height: 18, rx: 4 }));
    const t = svgEl("text", { x: W - M.r + 8, y: r.ty + 4 }); t.textContent = r.price.toFixed(2);
    g.append(t);
    svg.append(g);
  }

  // legend for the reference lines (identity is never color-only)
  const legend = el("div", { class: "spy-legend" },
    el("span", {}, el("span", { class: "line-key k-spy" }), "SPY"),
    data.prev_close != null ? el("span", {}, el("span", { class: "line-key k-prev" }), "Prev close") : null,
    ...lines.map((l) => el("span", {}, el("span", { class: `line-key k-${l.kind}` }), `${l.label} ${money(l.price)}`)),
    exp ? el("span", {}, el("span", { class: "swatch k-exp" }), "Expected range") : null);

  // hover / keyboard crosshair
  const cross = svgEl("line", { class: "crosshair", y1: M.t, y2: M.t + ih, visibility: "hidden" });
  const dot = svgEl("circle", { class: "hover-dot", r: 4.5, visibility: "hidden" });
  const hit = svgEl("rect", { x: M.l, y: M.t, width: iw, height: ih, fill: "transparent" });
  svg.append(cross, dot, hit);
  const tip = document.getElementById("tooltip");
  let idx = pts.length - 1;
  const show = (i, cx, cy) => {
    idx = i;
    const p = pts[i], px = x(p.m);
    cross.setAttribute("x1", px); cross.setAttribute("x2", px); cross.setAttribute("visibility", "visible");
    dot.setAttribute("cx", px); dot.setAttribute("cy", y(p.p)); dot.setAttribute("visibility", "visible");
    const chg = data.prev_close ? p.p - data.prev_close : null;
    tip.replaceChildren(el("div", { class: "tt-title" }, i === pts.length - 1 && data.live ? "Now" : clock(p.m)),
      el("div", { class: "tt-row" }, el("span", {}, "SPY"), el("b", {}, money(p.p))),
      chg != null ? el("div", { class: "tt-row" }, el("span", {}, "vs prev close"),
        el("b", {}, `${chg >= 0 ? "+" : "−"}${Math.abs(chg).toFixed(2)} (${chg >= 0 ? "+" : "−"}${Math.abs(chg / data.prev_close * 100).toFixed(2)}%)`)) : null);
    tip.hidden = false;
    const r = svg.getBoundingClientRect();
    const sx = cx ?? r.left + (px / W) * r.width, sy = cy ?? r.top + (y(p.p) / H) * r.height;
    tip.style.left = `${Math.min(sx + 14, innerWidth - tip.offsetWidth - 8)}px`;
    tip.style.top = `${sy - 12}px`;
  };
  const hide = () => { tip.hidden = true; cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); };
  hit.addEventListener("pointermove", (e) => {
    const r = svg.getBoundingClientRect(), sx = ((e.clientX - r.left) / r.width) * W;
    let best = 0;
    pts.forEach((p, i) => { if (Math.abs(x(p.m) - sx) < Math.abs(x(pts[best].m) - sx)) best = i; });
    show(best, e.clientX, e.clientY);
  });
  hit.addEventListener("pointerleave", hide);
  svg.addEventListener("blur", hide);
  svg.addEventListener("focus", () => show(idx));
  svg.addEventListener("keydown", (e) => {
    if (e.key === "ArrowRight") { show(Math.min(idx + 1, pts.length - 1)); e.preventDefault(); }
    if (e.key === "ArrowLeft") { show(Math.max(idx - 1, 0)); e.preventDefault(); }
    if (e.key === "Escape") hide();
  });
  box.append(legend, svg);
}

/** Header ticker text: SPY last, change vs previous close. */
export function spyChange(data) {
  if (!data?.prev_close || data.last == null) return null;
  const chg = data.last - data.prev_close;
  return { last: data.last, chg, pct: chg / data.prev_close };
}

/** Fill the header ticker (shared by Checker and Track). m = /api/market, spy = /api/spy (may be null). */
export function renderTicker(m, spy) {
  const $ = (id) => document.getElementById(id);
  if (!m) return;
  $("market-dot").classList.toggle("live", m.market_open);
  const last = spy?.live && spy.last != null ? spy.last : m.spot;
  $("tk-last").textContent = money(last);
  const c = spyChange(spy);
  const chg = $("tk-chg");
  if (c) {
    chg.className = `tk-chg ${c.chg >= 0 ? "up" : "down"}`;
    chg.textContent = `${c.chg >= 0 ? "▲ +" : "▼ −"}${Math.abs(c.chg).toFixed(2)} (${c.chg >= 0 ? "+" : "−"}${Math.abs(c.pct * 100).toFixed(2)}%)`;
  } else chg.textContent = "";
  $("tk-move").textContent = m.expected ? `±${money(m.expected.move)}` : "—";
  const mins = Math.round(m.minutes_left);
  $("tk-time-label").textContent = m.market_open ? "To close" : "Market";
  $("tk-time").textContent = m.market_open ? (mins >= 60 ? `${Math.floor(mins / 60)}h ${mins % 60}m` : `${mins}m`) : "Closed";
  const src = $("tk-src");
  if (src) src.textContent = m.source === "alpaca" ? "Alpaca" : "Yahoo (delayed)";
}
