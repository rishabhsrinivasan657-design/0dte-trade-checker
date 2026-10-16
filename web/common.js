// Shared helpers for the checker page (app.js) and the research page (research.js).
// All text from the server is inserted with textContent (never innerHTML).

export const $ = (id) => document.getElementById(id);

// ---------- small helpers ----------
export function el(tag, props = {}, ...children) {
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
export const svgEl = (tag, attrs = {}) => {
  const n = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  return n;
};
export const icon = (paths) => {
  const s = svgEl("svg", { viewBox: "0 0 24 24", "aria-hidden": "true" });
  for (const d of paths) s.append(svgEl("path", { d }));
  return s;
};
export const ICONS = {
  cheap: ["M5 12l5 5 9-10"],
  fair: ["M6 9h12", "M6 15h12"],
  expensive: ["M12 7v6", "M12 17h.01", "M10.3 3.9L2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"],
  info: ["M12 16v-4", "M12 8h.01", "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20z"],
};

export const money = (v, d = 2) => (v < 0 ? "−$" : "$") + Math.abs(v).toFixed(d);
export const signedMoney = (v) => (v >= 0 ? "+" : "−") + "$" + Math.abs(v).toFixed(2);
export const pct = (v, d = 0) => (v * 100).toFixed(d) + "%";
export const signedPct = (v, d = 2) => (v >= 0 ? "+" : "−") + Math.abs(v * 100).toFixed(d) + "%";
export const duration = (min) => {
  min = Math.round(min);
  if (min < 60) return `${min}m`;
  const h = Math.floor(min / 60), m = min % 60;
  return m ? `${h}h ${m}m` : `${h}h`;
};
export const expiryLabel = (iso) =>
  new Date(iso + "T12:00:00").toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });


// Theme toggle, shared by every page
export function setupThemeToggle() {
  $("theme-toggle")?.addEventListener("click", () => {
    const root = document.documentElement;
    const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("theme", root.dataset.theme); } catch (e) {}
  });
}

// Probabilities never show as certain: ">99%" / "<1%"
export const odds = (p) => (p >= 0.995 ? ">99%" : p > 0 && p < 0.005 ? "<1%" : pct(p));
// Cents per $1, without a "−0¢"
export const cents = (r) => { const c = Math.round(Math.abs(r) * 100); return c === 0 ? "0¢" : `${r < 0 ? "−" : "+"}${c}¢`; };
// Breakeven line: how far SPY must go, or how much room is left if you're already past it
export function breakevenSub(t) {
  const past = t.option_type === "call" ? t.spot >= t.breakeven : t.spot <= t.breakeven;
  const gap = Math.abs(t.breakeven - t.spot);
  return past ? `$${gap.toFixed(2)} cushion` : `SPY needs ${t.move_needed >= 0 ? "+" : "−"}$${gap.toFixed(2)}`;
}
