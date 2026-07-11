/**
 * Homepage search. Lazy-loads /search-index.json + Fuse.js on first focus (never
 * on page load — the index is ~3MB), fuzzy-matches title (highest) / keywords /
 * slug, and shows a keyboard-navigable results dropdown. Keywords include each
 * game's trait names AND their Steam-tag aliases, so "roguelike" or "cozy"
 * surface games strong in that trait, honoring "search a game or a feeling".
 */

import Link from "next/link";
import { useRouter } from "next/router";
import React, { useCallback, useEffect, useRef, useState } from "react";

interface Hit {
  slug: string;
  title: string;
  traits: string[];
  keywords: string[];
}

// Module-scoped so the index + Fuse instance are fetched at most once per session.
let fusePromise: Promise<{ search: (q: string) => { item: Hit }[] }> | null = null;

function loadFuse() {
  if (!fusePromise) {
    fusePromise = (async () => {
      const [{ default: Fuse }, res] = await Promise.all([
        import("fuse.js"),
        fetch("/search-index.json"),
      ]);
      const data: Hit[] = await res.json();
      return new Fuse(data, {
        threshold: 0.4,
        ignoreLocation: true,
        keys: [
          { name: "title", weight: 0.6 },
          { name: "keywords", weight: 0.3 },
          { name: "slug", weight: 0.1 },
        ],
      });
    })();
  }
  return fusePromise;
}

export default function SearchBox() {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Hit[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [ready, setReady] = useState(false);
  const fuseRef = useRef<{ search: (q: string) => { item: Hit }[] } | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);

  const ensureLoaded = useCallback(() => {
    if (!fuseRef.current) {
      loadFuse().then((f) => {
        fuseRef.current = f;
        setReady(true);
      });
    }
  }, []);

  // Recompute results as the query changes (and once the index finishes loading).
  useEffect(() => {
    const q = query.trim();
    if (!q || !fuseRef.current) {
      setResults([]);
      return;
    }
    setResults(fuseRef.current.search(q).slice(0, 8).map((r) => r.item));
    setActive(0);
  }, [query, ready]);

  // Close on outside click.
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

  // Only show the dropdown once the index is ready (avoids flashing "no match"
  // while it's still loading on the first keystroke).
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
            results.map((r, i) => (
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
                <span className="r-traits">{(r.traits || []).slice(0, 3).join(" · ")}</span>
              </li>
            ))
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
