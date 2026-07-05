import Link from "next/link";
import Layout from "../../components/Layout";
import { JsonLd, KinCard, TraitBarRow } from "../../components/ui";
import {
  getAllSlugs,
  getGameBySlug,
  getKinForGame,
  getSiteMeta,
} from "../../lib/data";

export async function getStaticPaths() {
  return {
    paths: getAllSlugs().map((slug) => ({ params: { slug } })),
    fallback: false,
  };
}

export async function getStaticProps({ params }) {
  const game = getGameBySlug(params.slug);
  if (!game) return { notFound: true };
  return {
    props: {
      site: getSiteMeta(),
      game,
      kin: getKinForGame(params.slug),
    },
  };
}

export default function GamesLike({ site, game, kin }) {
  const base = site.url.replace(/\/$/, "");

  const itemListLd = {
    "@context": "https://schema.org",
    "@type": "ItemList",
    name: `Games like ${game.title}`,
    numberOfItems: kin.length,
    itemListElement: kin.map((k, i) => ({
      "@type": "ListItem",
      position: i + 1,
      url: `${base}/game/${k.slug}`,
      name: k.title,
    })),
  };

  return (
    <Layout
      site={site}
      title={`Games like ${game.title} — ${site.name}`}
      description={`Ranked kindred games to ${game.title}, matched by tone, mechanics, aesthetic, and structure.`}
      path={`/games-like/${game.slug}`}
      head={<JsonLd data={itemListLd} />}
    >
      <section className="page-intro">
        <p className="back-link">
          <Link href={`/game/${game.slug}`}>← {game.title}</Link>
        </p>
        <h1>Games like {game.title}</h1>
        <p>Ranked by shared qualities, not genre tags.</p>
      </section>

      <div className="results-source">
        <p className="src-title">What defines {game.title}</p>
        <TraitBarRow traits={game.characteristics} />
      </div>

      <p className="section-label">
        {kin.length} kindred {kin.length === 1 ? "game" : "games"}
      </p>
      {kin.length > 0 ? (
        kin.map((k) => <KinCard key={k.slug} kin={k} />)
      ) : (
        <p style={{ color: "var(--text-faint)" }}>
          No kindred games computed yet for this title.
        </p>
      )}
    </Layout>
  );
}
