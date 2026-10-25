// Match URLs: /predictions/{home}-vs-{away}-{yyyy-mm-dd}. Two fixtures with the same slug
// (same team names on the same date, e.g. two clubs called Arsenal) get "-{fixture id}" appended.

export function slugify(name: string): string {
  return name
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/&/g, " and ")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

export function matchSlug(home: string, away: string, date: string): string {
  return `${slugify(home)}-vs-${slugify(away)}-${date}`;
}

export interface SlugParts {
  base: string; // without the id suffix
  date: string;
  id: number | null;
}

/** Parse "arsenal-vs-chelsea-2026-10-25" or "...-2026-10-25-123". Null when it isn't a match slug. */
export function parseMatchSlug(slug: string): SlugParts | null {
  const m = /^(.+-vs-.+)-(\d{4}-\d{2}-\d{2})(?:-(\d+))?$/.exec(slug);
  if (!m) return null;
  return { base: `${m[1]}-${m[2]}`, date: m[2], id: m[3] ? Number(m[3]) : null };
}

/** Slugs for a day's fixtures, disambiguating collisions with the fixture id. */
export function assignSlugs<T extends { id: number; home: string; away: string; date: string }>(rows: T[]): Map<number, string> {
  const counts = new Map<string, number>();
  for (const r of rows) {
    const s = matchSlug(r.home, r.away, r.date);
    counts.set(s, (counts.get(s) ?? 0) + 1);
  }
  const out = new Map<number, string>();
  for (const r of rows) {
    const s = matchSlug(r.home, r.away, r.date);
    out.set(r.id, counts.get(s)! > 1 ? `${s}-${r.id}` : s);
  }
  return out;
}
