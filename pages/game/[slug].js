import Link from "next/link";
import Layout from "../../components/Layout";
import ClaimForm from "../../components/ClaimForm";
import {
  Cover,
  JsonLd,
  KinCard,
  StoreButtons,
  TraitBarRow,
} from "../../components/ui";
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

export default function GamePage({ site, game, kin }) {
  const base = site.url.replace(/\/$/, "");
  const metaBits = [
    game.year,
    game.developer,
    game.platforms?.join(", "),
  ].filter(Boolean);

  const videoGameLd = {
    "@context": "https://schema.org",
    "@type": "VideoGame",
    name: game.title,
    url: `${base}/game/${game.slug}`,
    description: game.summary,
    datePublished: game.year ? String(game.year) : undefined,
    author: game.developer
      ? { "@type": "Organization", name: game.developer }
      : undefined,
    gamePlatform: game.platforms,
    keywords: game.characteristics.map((c) => c.name).join(", "),
  };

  return (
    <Layout
      site={site}
      title={`${game.title} — ${site.name}`}
      description={game.tagline || game.summary}
      path={`/game/${game.slug}`}
      head={<JsonLd data={videoGameLd} />}
    >
      <div className="game-header">
        <Cover game={game} />
        <div>
          <h1>{game.title}</h1>
          <p className="metaline">
            {metaBits.map((b, i) => (
              <span key={i}>{b}</span>
            ))}
          </p>
          <p className="summary">{game.summary}</p>
          <StoreButtons game={game} />
        </div>
      </div>

      {game.sections.map((section, i) => (
        <section className="prose-section" key={i}>
          <h2>{section.heading}</h2>
          <p>{section.prose}</p>
          <TraitBarRow traits={section.traits} />
        </section>
      ))}

      <p className="section-label">Kindred games</p>
      {kin.length > 0 ? (
        <>
          {kin.slice(0, 3).map((k) => (
            <KinCard key={k.slug} kin={k} />
          ))}
          <p style={{ marginTop: 8 }}>
            <Link href={`/games-like/${game.slug}`}>
              See all games like {game.title} →
            </Link>
          </p>
        </>
      ) : (
        <p style={{ color: "var(--text-faint)" }}>
          Kindred games are still being computed for this title.
        </p>
      )}

      <ClaimForm game={game} />
    </Layout>
  );
}
