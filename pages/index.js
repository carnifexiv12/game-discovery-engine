import Layout from "../components/Layout";
import SearchBox from "../components/SearchBox";
import { GameCard, JsonLd } from "../components/ui";
import { getAllGames, getSiteMeta } from "../lib/data";

export async function getStaticProps() {
  // Feature a slice on the landing page — embedding the whole catalog here
  // makes a multi-MB page. Cards only need a handful of fields, so project down.
  const all = getAllGames();
  const games = all.slice(0, 60).map((g) => ({
    slug: g.slug,
    title: g.title,
    year: g.year,
    developer: g.developer,
    tagline: g.tagline,
    steam_appid: g.steam_appid,
    cover_url: g.cover_url,
  }));
  return {
    props: {
      site: getSiteMeta(),
      games,
      total: all.length,
    },
  };
}

export default function Home({ site, games, total }) {
  const websiteLd = {
    "@context": "https://schema.org",
    "@type": "WebSite",
    name: site.name,
    url: site.url,
    description: site.description,
    potentialAction: {
      "@type": "SearchAction",
      target: `${site.url.replace(/\/$/, "")}/games-like/{search_term}`,
      "query-input": "required name=search_term",
    },
  };

  return (
    <Layout
      site={site}
      title={`${site.name} — find your next game by what it feels like`}
      description={site.description}
      path="/"
      head={<JsonLd data={websiteLd} />}
    >
      <section className="hero">
        <h1>{site.name}</h1>
        <p className="lede">{site.description}</p>

        <SearchBox />
        <p className="hint">
          Search {total.toLocaleString()} games by title — or by a feeling, like
          {" "}“cozy”, “roguelike”, or “melancholy”.
        </p>
      </section>

      <p className="section-label" id="featured">Featured games</p>
      <div className="card-grid">
        {games.map((g) => (
          <GameCard key={g.slug} game={g} />
        ))}
      </div>
    </Layout>
  );
}
