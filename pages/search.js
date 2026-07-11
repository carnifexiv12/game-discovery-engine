/**
 * Full search results — one static page, client-rendered from the ?q= query
 * param (no per-query static files, so zero file-count impact). Reuses the same
 * lib/search engine as the homepage dropdown, but renders ALL matches ranked by
 * combined trait weight, as cover-art cards with matched-trait chips.
 */

import Link from "next/link";
import { useRouter } from "next/router";
import { useEffect, useState } from "react";
import Layout from "../components/Layout";
import SearchBox from "../components/SearchBox";
import { Cover } from "../components/ui";
import { getSiteMeta } from "../lib/data";
import { loadEngine, search } from "../lib/search";

export async function getStaticProps() {
  // One static page; the query is read on the client. Only site meta at build.
  return { props: { site: getSiteMeta() } };
}

export default function SearchPage({ site }) {
  const router = useRouter();
  const q = typeof router.query.q === "string" ? router.query.q : "";
  const [state, setState] = useState({
    status: "loading",
    matches: [],
    partial: false,
  });

  useEffect(() => {
    if (!router.isReady) return;
    const query = q.trim();
    if (!query) {
      setState({ status: "empty", matches: [], partial: false });
      return;
    }
    let cancelled = false;
    setState((s) => ({ ...s, status: "loading" }));
    loadEngine().then((engine) => {
      if (cancelled) return;
      const r = search(engine, query);
      setState({ status: "done", matches: r.matches, partial: r.partial });
    });
    return () => {
      cancelled = true;
    };
  }, [router.isReady, q]);

  const { status, matches, partial } = state;
  const count = matches.length;
  const noun = count === 1 ? "game" : "games";

  let heading;
  if (status === "empty") heading = "Search the catalog";
  else if (status === "loading") heading = "Searching…";
  else if (count === 0) heading = `No games match “${q}”`;
  else if (partial) heading = `No exact match — ${count} close ${noun} to “${q}”`;
  else heading = `${count} ${noun} match “${q}”`;

  return (
    <Layout
      site={site}
      title={q ? `“${q}” — ${site.name}` : `Search — ${site.name}`}
      description={`Search ${site.name} by title, feeling, mechanic, or aesthetic.`}
      path="/search"
    >
      <section className="search-page">
        <SearchBox initialQuery={q} />
        <h1 className="search-heading">{heading}</h1>

        {status === "done" && count > 0 && (
          <div className="card-grid">
            {matches.map((m) => (
              <Link key={m.slug} href={`/game/${m.slug}`} className="game-card">
                <Cover
                  game={{
                    slug: m.slug,
                    title: m.title,
                    steam_appid: m.appid,
                    cover_url: null,
                  }}
                />
                <div className="body">
                  <p className="title">{m.title}</p>
                  {m.chips.length > 0 && (
                    <div className="trait-pills result-chips">
                      {m.chips.slice(0, 4).map((c) => (
                        <span className="pill" key={c}>
                          {c}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </Link>
            ))}
          </div>
        )}

        {status === "done" && count === 0 && (
          <p className="search-empty-page">
            Nothing matched that. Try a broader term, or{" "}
            <Link href="/#featured">browse featured games</Link>.
          </p>
        )}
      </section>
    </Layout>
  );
}
