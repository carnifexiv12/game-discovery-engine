/**
 * Homepage search — two-tier, over the published catalog. Lazy-loads
 * /search-index.json + Fuse.js on first focus (never on page load; the index is
 * ~1.4MB).
 *
 * TIER 1 (title): Fuse fuzzy-matches title/slug; close title matches rank first.
 * TIER 2 (trait): query tokens are mapped to vocabulary traits via an alias
 *   table (each trait's name + Steam-tag aliases + hand-tuned umbrella terms like
 *   "rpg"/"shooter"/"retro"). A game scores by the sum of its weights on the
 *   matched traits, and must soft-match EVERY resolved term (weighted
 *   intersection) — so "rpg shooter retro" returns retro shooter-RPGs, not every
 *   RPG. Matched traits are shown as chips so the user sees why it matched.
 * Mixed queries run both tiers, title matches interleaved first. When nothing
 * clears the intersection bar, the best partial matches show under a divider.
 */

import Link from "next/link";
import { useRouter } from "next/router";
import React, { useCallback, useEffect, useRef, useState } from "react";

interface TraitDef { id: string; name: string; aliases: string[]; }
interface GameEntry {
  slug: string;
  title: string;
  t: number[]; // trait indices (into traits[])
  w: number[]; // quantized weights, parallel to t
  wmap?: Map<number, number>;
}
interface IndexData { traits: TraitDef[]; games: GameEntry[]; }
interface Result { slug: string; title: string; chips: string[]; }
interface Engine {
  fuse: { search: (q: string) => { item: GameEntry; score?: number }[] };
  traits: TraitDef[];
  games: GameEntry[];
  aliasMap: Map<string, number[]>;
}

let enginePromise: Promise<Engine> | null = null;

function loadEngine(): Promise<Engine> {
  if (!enginePromise) {
    enginePromise = (async () => {
      const [{ default: Fuse }, res] = await Promise.all([
        import("fuse.js"),
        fetch("/search-index.json"),
      ]);
      const data: IndexData = await res.json();
      for (const g of data.games) {
        const m = new Map<number, number>();
        for (let i = 0; i < g.t.length; i++) m.set(g.t[i], g.w[i]);
        g.wmap = m;
      }
      const aliasMap = new Map<string, number[]>();
      data.traits.forEach((t, i) => {
        for (const a of t.aliases) {
          const arr = aliasMap.get(a);
          if (arr) arr.push(i);
          else aliasMap.set(a, [i]);
        }
      });
      const fuse = new Fuse(data.games, {
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

// Query words -> resolved trait terms. Prefers a two-word alias ("open world")
// over its parts. Unresolved words fall through to the title tier.
function resolveTerms(query: string, aliasMap: Map<string, number[]>) {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  const terms: { label: string; idxs: number[] }[] = [];
  let i = 0;
  while (i < words.length) {
    const w2 = words[i + 1];
    // Prefer a two-word intersection when both words resolve on their own
    // ("roguelike deckbuilder"); only fall back to a bigram alias when they
    // don't ("open world", "science fiction").
    const bothResolve = !!w2 && aliasMap.has(words[i]) && aliasMap.has(w2);
    const bigram = w2 ? `${words[i]} ${w2}` : null;
    if (!bothResolve && bigram && aliasMap.has(bigram)) {
      terms.push({ label: bigram, idxs: aliasMap.get(bigram)! });
      i += 2;
    } else if (aliasMap.has(words[i])) {
      terms.push({ label: words[i], idxs: aliasMap.get(words[i])! });
      i += 1;
    } else {
      i += 1;
    }
  }
  return terms;
}

function runSearch(engine: Engine, query: string): { results: Result[]; partial: boolean } {
  const q = query.trim();
  if (!q) return { results: [], partial: false };
  const { fuse, traits, games, aliasMap } = engine;
  const nameOf = (idx: number) => traits[idx].name;

  // Tier 1 — title/slug, good matches only.
  const titleHits = fuse
    .search(q)
    .filter((r) => (r.score ?? 1) <= 0.4)
    .slice(0, 8)
    .map((r) => r.item);

  // Tier 2 — weighted trait intersection.
  const terms = resolveTerms(q, aliasMap);
  let exactTrait: { g: GameEntry; score: number; chips: number[] }[] = [];
  let partialTrait: { g: GameEntry; matched: number; score: number; chips: number[] }[] = [];
  if (terms.length) {
    const scored: { g: GameEntry; matched: number; score: number; chips: number[] }[] = [];
    for (const g of games) {
      let matched = 0;
      let score = 0;
      const chips: number[] = [];
      for (const term of terms) {
        let best = 0;
        let bestIdx = -1;
        for (const idx of term.idxs) {
          const w = g.wmap!.get(idx) || 0;
          if (w > best) {
            best = w;
            bestIdx = idx;
          }
        }
        if (best > 0) {
          matched += 1;
          score += best;
          chips.push(bestIdx);
        }
      }
      if (matched > 0) scored.push({ g, matched, score, chips });
    }
    exactTrait = scored
      .filter((s) => s.matched === terms.length)
      .sort((a, b) => b.score - a.score);
    partialTrait = scored
      .filter((s) => s.matched < terms.length)
      .sort((a, b) => b.matched - a.matched || b.score - a.score);
  }

  const seen = new Set<string>();
  const results: Result[] = [];
  const push = (g: GameEntry, chips: string[]) => {
    if (seen.has(g.slug) || results.length >= 8) return;
    seen.add(g.slug);
    results.push({ slug: g.slug, title: g.title, chips });
  };

  for (const g of titleHits) push(g, g.t.slice(0, 3).map(nameOf));
  for (const s of exactTrait) push(s.g, s.chips.map(nameOf));
  if (results.length > 0) return { results, partial: false };

  // Fallback: best partial matches, shown under a "close but not exact" divider.
  for (const s of partialTrait) {
    if (seen.has(s.g.slug) || results.length >= 8) continue;
    seen.add(s.g.slug);
    results.push({ slug: s.g.slug, title: s.g.title, chips: s.chips.map(nameOf) });
  }
  return { results, partial: results.length > 0 };
}

export default function SearchBox() {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Result[]>([]);
  const [partial, setPartial] = useState(false);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [ready, setReady] = useState(false);
  const engineRef = useRef<Engine | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);

  const ensureLoaded = useCallback(() => {
    if (!engineRef.current) {
      loadEngine().then((e) => {
        engineRef.current = e;
        setReady(true);
      });
    }
  }, []);

  useEffect(() => {
    const q = query.trim();
    if (!q || !engineRef.current) {
      setResults([]);
      setPartial(false);
      return;
    }
    const res = runSearch(engineRef.current, q);
    setResults(res.results);
    setPartial(res.partial);
    setActive(0);
  }, [query, ready]);

  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, []);

  const go = (slug: string) => {
    setOpen(false);
    router.push(`/game/${slug}`);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      setOpen(false);
      return;
    }
    if (!results.length) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => (a + 1) % results.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => (a - 1 + results.length) % results.length);
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (results[active]) go(results[active].slug);
    }
  };

  const showList = open && query.trim().length > 0 && (results.length > 0 || ready);

  return (
    <div className="searchbox" ref={boxRef}>
      <form
        className="searchbar"
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          if (results[active]) go(results[active].slug);
        }}
      >
        <input
          type="text"
          placeholder="Search a game or a feeling…"
          aria-label="Search games"
          autoComplete="off"
          role="combobox"
          aria-expanded={showList}
          aria-controls="search-results"
          value={query}
          onFocus={() => {
            ensureLoaded();
            setOpen(true);
          }}
          onChange={(e) => {
            ensureLoaded();
            setQuery(e.target.value);
            setOpen(true);
          }}
          onKeyDown={onKeyDown}
        />
        <button type="submit">Search</button>
      </form>

      {showList && (
        <ul className="search-results" id="search-results" role="listbox">
          {results.length > 0 ? (
            <>
              {partial && (
                <li className="search-divider" aria-hidden="true">
                  Close but not exact
                </li>
              )}
              {results.map((r, i) => (
                <li
                  key={r.slug}
                  role="option"
                  aria-selected={i === active}
                  className={i === active ? "active" : ""}
                  onMouseEnter={() => setActive(i)}
                  onMouseDown={(e) => {
                    e.preventDefault();
                    go(r.slug);
                  }}
                >
                  <span className="r-title">{r.title}</span>
                  <span className="r-chips">
                    {r.chips.slice(0, 3).map((c) => (
                      <span className="chip" key={c}>
                        {c}
                      </span>
                    ))}
                  </span>
                </li>
              ))}
            </>
          ) : (
            <li className="search-empty">
              No match — try another title.{" "}
              <Link href="#featured">Browse featured</Link>
            </li>
          )}
        </ul>
      )}
    </div>
  );
}
