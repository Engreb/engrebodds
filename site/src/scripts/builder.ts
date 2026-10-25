// Bet builder (members only): greedy, most likely first, one selection per match, until the target price.
import { buildSlip, type BuilderMatch } from "../lib/builder";
import { comboLabel, odds, parseKey, pct } from "../lib/markets";

const raw = document.getElementById("bdata")?.textContent;
const out = document.getElementById("bout");
if (raw && out) {
  const matches = JSON.parse(raw) as BuilderMatch[];
  const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
  const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);

  const run = () => {
    const target = parseFloat($<HTMLInputElement>("bt").value) || 5;
    const minP = parseInt($<HTMLInputElement>("bp").value, 10) / 100;
    $("bpv").textContent = pct(minP);
    const keys = [...document.querySelectorAll<HTMLInputElement>("#bform .checks input:checked")].map((i) => i.value);
    const s = buildSlip(matches, { target, minP, keys });
    if (!s.legs.length) {
      out.innerHTML = '<div class="slip"><div class="empty">No selections qualify. Lower the minimum probability or add markets.</div></div>';
      return;
    }
    const copy = s.legs.map((l) => `${l.match}: ${comboLabel([parseKey(l.key)])} @ ${odds(l.fair)}`).join("\n") + `\nTotal ${odds(s.odds)} (fair odds)`;
    out.innerHTML = `<div class="slip"><div class="slip-top"><div><span class="eyebrow">Your ${target} odds slip</span><h3>${s.legs.length} selection${s.legs.length > 1 ? "s" : ""}</h3></div><span class="tot">${odds(s.odds)}</span></div>` +
      s.legs.map((l) => `<div class="slip-line"><span class="m"><b>${esc(l.match)}</b><small>${esc(l.meta)} · ${esc(comboLabel([parseKey(l.key)]))}</small></span><span class="pct">${pct(l.p)}</span><span class="odds fair">${odds(l.fair)}</span></div>`).join("") +
      `<div class="slip-foot"><span>Chance every leg lands (model): <b style="color:var(--ink)">${pct(s.chance, 1)}</b></span><span>${s.reached ? "Target reached" : "Closest available to target"}</span>` +
      `<button class="btn sec" type="button" data-copy="${esc(copy)}">Copy selections</button></div></div>`;
  };
  document.getElementById("bform")?.addEventListener("input", run);
  run();
}
