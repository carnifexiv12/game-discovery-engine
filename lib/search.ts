/**
 * Shared client-side search engine, used by both the homepage dropdown
 * (components/SearchBox) and the full results page (pages/search). Lazy-loads
 * /search-index.json + Fuse.js once (module-cached), so the ~1.4MB index is
 * never fetched on page load — only on first focus / first results view.
 *
 * TIER 1 (title): Fuse fuzzy-matches title/slug; close title matches rank first.
 * TIER 2 (trait): query tokens map to vocabulary traits via a weighted alias
 *   table. Each alias carries PER-TRAIT priority (STRONG core identity vs WEAK
 *   adjacent), so a game's score for a term is its best (weight x priority) over
 *   that term's traits — ranking leans on core identity ("rpg" favours
 *   Character Builds over Class System). A game must soft-match EVERY resolved
 *   term (weighted intersection), so "rpg shooter" returns shooter-RPGs, not
 *   every RPG. When nothing clears the intersection bar, best partials are
 *   returned with `partial: true`.
 *
 * search() returns the FULL ranked match list (title-tier first, then trait-tier
 * by score). The dropdown slices it to 8; the results page renders all of it.
 */

interface FuseLike {
  search: (q: string) => { item: GameEntry; score?: number }[];
}

export interface TraitDef {
  id: string;
  name: string;
}

export interface GameEntry {
  slug: string;
  title: string;
  appid: number | null;
  t: number[]; // trait indices (into traits[])
  w: number[]; // quantized weights (0..255), parallel to t
  wmap?: Map<number, number>;
}

interface AliasRef {
  idx: number; // trait index
  prio: number; // 0..1 priority for this (alias, trait) pair
}

interface RawIndex {
  traits: TraitDef[];
  aliases: Record<string, [number, number][]>; // term -> [[traitIdx, prio0..100]]
  games: GameEntry[];
}

export interface Match {
  slug: string;
  title: string;
  appid: number | null;
  chips: string[]; // matched (or top) trait names, for the "why it matched" pills
}

export interface SearchResult {
  matches: Match[];
  partial: boolean; // true when these are best-partial (no full intersection)
  terms: string[]; // resolved trait-term labels, for the results-page header
}

export interface Engine {
  fuse: FuseLike;
  traits: TraitDef[];
  games: GameEntry[];
  aliasMap: Map<string, AliasRef[]>;
}

let enginePromise: Promise<Engine> | null = null;

export function loadEngine(): Promise<Engine> {
  if (!enginePromise) {
    enginePromise = (async () => {
      const [{ default: Fuse }, res] = await Promise.all([
        import("fuse.js"),
        fetch("/search-index.json"),
      ]);
      const data: RawIndex = await res.json();
      for (const g of data.games) {
        const m = new Map<number, number>();
        for (let i = 0; i < g.t.length; i++) m.set(g.t[i], g.w[i]);
        g.wmap = m;
      }
      const aliasMap = new Map<string, AliasRef[]>();
      for (const [term, refs] of Object.entries(data.aliases)) {
        aliasMap.set(
          term,
          refs.map(([idx, prio]) => ({ idx, prio: prio / 100 })),
        );
      }
      const fuse: FuseLike = new Fuse(data.games, {
        includeScore: true,
        threshold: 0.3, // tighter: keeps close title matches, drops fuzzy noise
        ignoreLocation: true,
        keys: [
          { name: "title", weight: 0.85 },
          { name: "slug", weight: 0.15 },
        ],
      });
      return { fuse, traits: data.traits, games: data.games, aliasMap };
    })();
  }
  return enginePromise;
}

interface Term {
  label: string;
  refs: AliasRef[];
}

// Query words -> resolved trait terms. Prefers a two-word intersection when both
// words resolve on their own ("roguelike deckbuilder"); only falls back to a
// bigram alias when they don't ("open world"). Unresolved words fall through to
// the title tier.
function resolveTerms(query: string, aliasMap: Map<string, AliasRef[]>): Term[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  const terms: Term[] = [];
  let i = 0;
  while (i < words.length) {
    const w2 = words[i + 1];
    const bothResolve = !!w2 && aliasMap.has(words[i]) && aliasMap.has(w2);
    const bigram = w2 ? `${words[i]} ${w2}` : null;
    if (!bothResolve && bigram && aliasMap.has(bigram)) {
      terms.push({ label: bigram, refs: aliasMap.get(bigram)! });
      i += 2;
    } else if (aliasMap.has(words[i])) {
      terms.push({ label: words[i], refs: aliasMap.get(words[i])! });
      i += 1;
    } else {
      i += 1;
    }
  }
  return terms;
}

export function search(engine: Engine, query: string): SearchResult {
  const q = query.trim();
  if (!q) return { matches: [], partial: false, terms: [] };
  const { fuse, traits, games, aliasMap } = engine;
  const nameOf = (idx: number) => traits[idx].name;

  // Tier 1 — title/slug, good matches only.
  const titleHits = fuse
    .search(q)
    .filter((r) => (r.score ?? 1) <= 0.4)
    .map((r) => r.item);

  // Tier 2 — weighted trait intersection, ranked on (weight x priority).
  const terms = resolveTerms(q, aliasMap);
  let exact: { g: GameEntry; score: number; chips: number[] }[] = [];
  let partial: { g: GameEntry; matched: number; score: number; chips: number[] }[] = [];
  if (terms.length) {
    const scored: { g: GameEntry; matched: number; score: number; chips: number[] }[] = [];
    for (const g of games) {
      let matched = 0;
      let score = 0;
      const chips: number[] = [];
      for (const term of terms) {
        // Sum (weight x priority) over ALL the term's traits this game has, so a
        // broad genre fit (an RPG with builds + XP + skill tree) outranks a game
        // that maxes one adjacent trait (a visual novel with only Dialogue
        // Trees). The single best trait still drives the chip.
        let sum = 0;
        let best = 0;
        let bestIdx = -1;
        for (const ref of term.refs) {
          const s = (g.wmap!.get(ref.idx) || 0) * ref.prio;
          if (s > 0) {
            sum += s;
            if (s > best) {
              best = s;
              bestIdx = ref.idx;
            }
          }
        }
        if (sum > 0) {
          matched += 1;
          score += sum;
          chips.push(bestIdx);
        }
      }
      if (matched > 0) scored.push({ g, matched, score, chips });
    }
    exact = scored
      .filter((s) => s.matched === terms.length)
      .sort((a, b) => b.score - a.score);
    partial = scored
      .filter((s) => s.matched < terms.length)
      .sort((a, b) => b.matched - a.matched || b.score - a.score);
  }

  const seen = new Set<string>();
  const matches: Match[] = [];
  const push = (g: GameEntry, chips: string[]) => {
    if (seen.has(g.slug)) return;
    seen.add(g.slug);
    matches.push({ slug: g.slug, title: g.title, appid: g.appid, chips });
  };

  for (const g of titleHits) push(g, g.t.slice(0, 3).map(nameOf));
  for (const s of exact) push(s.g, s.chips.map(nameOf));
  const termLabels = terms.map((t) => t.label);
  if (matches.length > 0) return { matches, partial: false, terms: termLabels };

  // Fallback: best partial matches (no full intersection cleared).
  for (const s of partial) push(s.g, s.chips.map(nameOf));
  return { matches, partial: matches.length > 0, terms: termLabels };
}
