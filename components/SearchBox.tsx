/**
 * Homepage search dropdown. Thin UI over lib/search (shared with the /search
 * results page): lazy-loads the index + Fuse on first focus, shows the top 8
 * matches for quick navigation, and hands everything else off to the results
 * page.
 *
 * - Click a row (or ArrowDown + Enter) -> that game (quick nav).
 * - Enter / Search button with nothing highlighted -> /search?q=... (all matches).
 * - When matches > 8, a final "See all N results" row links to the same page.
 */

import Link from "next/link";
import { useRouter } from "next/router";
import React, { useCallback, useEffect, useRef, useState } from "react";
import { Engine, loadEngine, Match, search } from "../lib/search";

const DROPDOWN_MAX = 8;

export default function SearchBox({ initialQuery = "" }: { initialQuery?: string }) {
  const router = useRouter();
  const [query, setQuery] = useState(initialQuery);
  const [matches, setMatches] = useState<Match[]>([]);
  const [total, setTotal] = useState(0);
  const [partial, setPartial] = useState(false);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1); // -1 = nothing highlighted
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

  // Reflect the active query when navigated to a new /search?q= (the results
  // page passes it in). Loads the engine so a subsequent focus is instant.
  useEffect(() => {
    setQuery(initialQuery);
    if (initialQuery) ensureLoaded();
  }, [initialQuery, ensureLoaded]);

  useEffect(() => {
    const q = query.trim();
    if (!q || !engineRef.current) {
      setMatches([]);
      setTotal(0);
      setPartial(false);
      return;
    }
    const res = search(engineRef.current, q);
    setMatches(res.matches.slice(0, DROPDOWN_MAX));
    setTotal(res.matches.length);
    setPartial(res.partial);
    setActive(-1);
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

  const submitSearch = () => {
    const q = query.trim();
    if (!q) return;
    setOpen(false);
    router.push(`/search?q=${encodeURIComponent(q)}`);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      setOpen(false);
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (matches.length) setActive((a) => (a + 1) % matches.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (matches.length) setActive((a) => (a <= 0 ? matches.length - 1 : a - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      // Highlighted a row -> open it; otherwise go to the full results page.
      if (active >= 0 && matches[active]) go(matches[active].slug);
      else submitSearch();
    }
  };

  const showList = open && query.trim().length > 0 && (matches.length > 0 || ready);
  const showSeeAll = total > matches.length;

  return (
    <div className="searchbox" ref={boxRef}>
      <form
        className="searchbar"
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          submitSearch();
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
          {matches.length > 0 ? (
            <>
              {partial && (
                <li className="search-divider" aria-hidden="true">
                  Close but not exact
                </li>
              )}
              {matches.map((r, i) => (
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
              {showSeeAll && (
                <li
                  className="search-seeall"
                  role="option"
                  aria-selected={false}
                  onMouseDown={(e) => {
                    e.preventDefault();
                    submitSearch();
                  }}
                >
                  See all {total} results →
                </li>
              )}
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
