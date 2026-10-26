// Kick-off times are rendered in WAT on the server. For visitors elsewhere (e.g. the diaspora in the UK),
// rewrite them to local time and name the zone. Nothing changes for visitors already on WAT.
const local = Intl.DateTimeFormat().resolvedOptions().timeZone;
const probe = (iso: string) => new Intl.DateTimeFormat("en-GB", { timeZone: local, hour: "2-digit", minute: "2-digit", timeZoneName: "short" })
  .formatToParts(new Date(iso));
const wat = (iso: string) => new Intl.DateTimeFormat("en-GB", { timeZone: "Africa/Lagos", hour: "2-digit", minute: "2-digit" })
  .format(new Date(iso));

/** Rewrite the kick-off times under `root` (the page, or content loaded into the match drawer). */
export function localTimes(root: ParentNode = document): void {
  if (!local || local === "Africa/Lagos") return;
  for (const el of root.querySelectorAll<HTMLTimeElement>("time[data-ko]")) {
    const parts = probe(el.dateTime);
    const hm = parts.filter((p) => p.type === "hour" || p.type === "minute").map((p) => p.value).join(":");
    const zone = parts.find((p) => p.type === "timeZoneName")?.value ?? "";
    if (hm === wat(el.dateTime)) continue; // same clock time: keep "WAT"-free short form
    el.textContent = el.dataset.ko === "long" ? `${hm} ${zone}` : hm;
    el.title = `${wat(el.dateTime)} WAT`;
  }
}

localTimes();
