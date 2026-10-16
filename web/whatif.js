// What-if map: profit/loss for SPY price (rows) × clock time (columns), as an HTML table heatmap.
// Diverging colors: blue = profit, red = loss, neutral gray near $0. Values are $ for your contracts.
import { el, money } from "./common.js";

const fmtPnl = (v) => `${v >= 0 ? "+" : "−"}${Math.abs(Math.round(v)).toLocaleString()}`;

export function renderWhatIf(box, g, { contracts = 1, caption = "" } = {}) {
  const mult = 100 * contracts;
  const values = g.pnl.flat().map((v) => v * mult);
  const maxAbs = Math.max(1, ...values.map(Math.abs));
  const spotRow = g.prices.reduce((best, p, i) => Math.abs(p - g.spot) < Math.abs(g.prices[best] - g.spot) ? i : best, 0);
  const strikeRow = g.prices.findIndex((p) => Math.abs(p - g.strike) < 1e-9);
  const tip = document.getElementById("tooltip");

  const head = el("tr", {}, el("th", { class: "wi-corner", scope: "col" }, "SPY at"),
    ...g.times.map((t, j) => el("th", { scope: "col" }, j === 0 && g.first_is_now ? "Now" : t)));
  const rows = g.prices.map((p, i) => {
    const tags = [];
    if (i === spotRow) tags.push("now");
    if (i === strikeRow) tags.push("strike");
    return el("tr", { class: i === spotRow ? "wi-spot" : "" },
      el("th", { scope: "row" }, money(p), tags.length ? el("span", { class: "wi-tag" }, tags.join(" · ")) : null),
      ...g.pnl[i].map((v, j) => {
        const val = v * mult;
        const k = Math.min(1, Math.abs(val) / maxAbs);
        const strength = Math.round(12 + 70 * Math.sqrt(k));                     // % of the hue mixed into the surface
        const hue = val >= 0 ? "var(--div-pos)" : "var(--div-neg)";
        const bg = Math.abs(val) < maxAbs * 0.02 ? "var(--div-mid)" : `color-mix(in srgb, ${hue} ${strength}%, var(--surface))`;
        const when = j === 0 && g.first_is_now ? "now" : `at ${g.times[j]}`;
        const label = `If SPY is ${money(p)} ${when}: ${val >= 0 ? "profit" : "loss"} of ${money(Math.abs(val), 0)}`;
        const cell = el("td", { class: strength > 55 ? "wi-strong" : "", style: { background: bg }, tabindex: "0", "aria-label": label },
          fmtPnl(val));
        const show = () => {
          const option = (v + g.premium);
          tip.replaceChildren(el("div", { class: "tt-title" }, `SPY ${money(p)} ${when}`),
            el("div", { class: "tt-row" }, el("span", {}, "Option worth"), el("b", {}, money(option))),
            el("div", { class: "tt-row" }, el("span", {}, contracts > 1 ? `P/L (${contracts} contracts)` : "P/L (1 contract)"),
              el("b", {}, `${val >= 0 ? "+" : "−"}${money(Math.abs(val), 0)}`)));
          tip.hidden = false;
          const r = cell.getBoundingClientRect();
          tip.style.left = `${Math.min(r.right + 6, innerWidth - tip.offsetWidth - 8)}px`;
          tip.style.top = `${r.top - 8}px`;
        };
        cell.addEventListener("pointerenter", show);
        cell.addEventListener("focus", show);
        cell.addEventListener("pointerleave", () => (tip.hidden = true));
        cell.addEventListener("blur", () => (tip.hidden = true));
        return cell;
      }));
  });
  box.replaceChildren(
    el("div", { class: "table-wrap" }, el("table", { class: "whatif", "aria-describedby": caption ? `${box.id}-note` : null },
      el("thead", {}, head), el("tbody", {}, ...rows))),
    caption ? el("p", { class: "fineprint", id: `${box.id}-note` }, caption) : "");
}
