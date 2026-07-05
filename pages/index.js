import Layout from "../components/Layout";
import { GameCard, JsonLd } from "../components/ui";
import { getAllGames, getSiteMeta } from "../lib/data";

export async function getStaticProps() {
  return {
    props: {
      site: getSiteMeta(),
      games: getAllGames(),
    },
  };
}

export default function Home({ site, games }) {
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

        <form
          className="searchbar"
          onSubmit={(e) => e.preventDefault()}
          role="search"
          aria-label="Search games (coming soon)"
        >
          <input
            type="text"
            placeholder="Search a game or a feeling…"
            aria-label="Search"
            disabled
          />
          <button type="submit" disabled>
            Search
          </button>
        </form>
        <p className="hint">Search is a stub for now — browse the sample set below.</p>
      </section>

      <div className="card-grid">
        {games.map((g) => (
          <GameCard key={g.slug} game={g} />
        ))}
      </div>
    </Layout>
  );
}
