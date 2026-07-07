/**
 * Shared presentational components. Kept framework-free (plain elements +
 * classes from styles/globals.css) to match the prose-first editorial look.
 */

import Link from "next/link";
import React from "react";
import type { Game, KinEntry, TraitRef } from "../lib/data";

/** Deterministic gradient cover derived from a string, so builds are stable. */
export function coverGradient(seed: string): string {
  let h = 0;
  for (let i = 0; i < seed.length; i++) {
    h = (h * 31 + seed.charCodeAt(i)) % 360;
  }
  const a = h;
  const b = (h + 48) % 360;
  return `linear-gradient(150deg, hsl(${a} 62% 42%), hsl(${b} 58% 28%))`;
}

const STEAM_CDN = "https://cdn.akamai.steamstatic.com/steam/apps";

/** Landscape store capsule (460×215). */
export function steamHeader(appid: number): string {
  return `${STEAM_CDN}/${appid}/header.jpg`;
}

/** Portrait library art (600×900), a better fit for the 3:4 cover slots. */
export function steamLibraryPortrait(appid: number): string {
  return `${STEAM_CDN}/${appid}/library_600x900.jpg`;
}

export function Cover({
  game,
  small = false,
}: {
  game: Pick<Game, "slug" | "title" | "cover_url" | "steam_appid">;
  small?: boolean;
}) {
  const cls = small ? "cover small" : "cover";

  // Ordered art candidates: a curated cover_url wins, then the Steam header,
  // then the portrait library art. onError walks down the list; once it runs
  // out we render the deterministic gradient. All srcs are server-rendered, so
  // the CDN URL ships in the static HTML; the swap only runs client-side.
  const candidates: string[] = [];
  if (game.cover_url) candidates.push(game.cover_url);
  if (game.steam_appid) {
    candidates.push(steamHeader(game.steam_appid));
    candidates.push(steamLibraryPortrait(game.steam_appid));
  }

  const [idx, setIdx] = React.useState(0);
  const src = candidates[idx];

  if (src) {
    return (
      <div className={cls} style={{ padding: 0 }}>
        {/* Static export + images.unoptimized: plain img is intentional. */}
        <img
          src={src}
          alt={`${game.title} cover art`}
          loading="lazy"
          onError={() => setIdx((i) => i + 1)}
        />
      </div>
    );
  }

  return (
    <div className={cls} style={{ background: coverGradient(game.slug) }}>
      {game.title}
    </div>
  );
}

/** Small landscape cover thumbnail for kin cards; gradient on missing/broken art. */
export function KinThumb({
  slug,
  title,
  steam_appid,
}: Pick<KinEntry, "slug" | "title" | "steam_appid">) {
  const [failed, setFailed] = React.useState(false);
  if (!steam_appid || failed) {
    return (
      <div
        className="kin-thumb"
        style={{ background: coverGradient(slug) }}
        aria-hidden="true"
      />
    );
  }
  return (
    <div className="kin-thumb">
      <img
        src={steamHeader(steam_appid)}
        alt={`${title} cover art`}
        loading="lazy"
        onError={() => setFailed(true)}
      />
    </div>
  );
}

export function TraitBar({ trait }: { trait: TraitRef }) {
  const pct = Math.round(trait.weight * 100);
  return (
    <div className="trait-bar">
      <div className="label">
        <span>{trait.name}</span>
        <span className="pct">{pct}%</span>
      </div>
      <div className="track">
        <div className="fill" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export function TraitBarRow({ traits }: { traits: TraitRef[] }) {
  if (!traits?.length) return null;
  return (
    <div className="trait-bars">
      {traits.map((t) => (
        <TraitBar key={t.id} trait={t} />
      ))}
    </div>
  );
}

export function TraitPills({ traits }: { traits: TraitRef[] }) {
  if (!traits?.length) return null;
  return (
    <div className="trait-pills">
      {traits.map((t) => (
        <span className="pill" key={t.id}>
          {t.name}
          <span className="w">{Math.round(t.weight * 100)}%</span>
        </span>
      ))}
    </div>
  );
}

export function GameCard({ game }: { game: Game }) {
  return (
    <Link href={`/game/${game.slug}`} className="game-card">
      <Cover game={game} />
      <div className="body">
        <p className="title">{game.title}</p>
        <p className="meta">
          {[game.year, game.developer].filter(Boolean).join(" · ")}
        </p>
        <p className="tagline">{game.tagline}</p>
      </div>
    </Link>
  );
}

export function StoreButtons({ game }: { game: Game }) {
  const links = game.store_links || {};
  return (
    <div className="btn-row">
      {links.steam && (
        <a className="btn" href={links.steam} target="_blank" rel="noopener noreferrer">
          Steam
        </a>
      )}
      {links.official && (
        <a className="btn" href={links.official} target="_blank" rel="noopener noreferrer">
          Official site
        </a>
      )}
      <Link className="btn primary" href={`/games-like/${game.slug}`}>
        Kindred games →
      </Link>
    </div>
  );
}

export function KinCard({ kin }: { kin: KinEntry }) {
  return (
    <div className="kin-card">
      <Link
        href={`/game/${kin.slug}`}
        className="kin-thumb-link"
        tabIndex={-1}
        aria-hidden="true"
      >
        <KinThumb slug={kin.slug} title={kin.title} steam_appid={kin.steam_appid} />
      </Link>
      <div className="kin-body">
        <div className="kin-top">
          <Link href={`/game/${kin.slug}`} className="kin-title">
            {kin.title}
          </Link>
          <span className="match">{kin.match_pct}% match</span>
        </div>
        <p className="blurb">{kin.blurb}</p>
        <p className="reasoning">{kin.reasoning}</p>
        <TraitPills traits={kin.traits} />
        <div className="kin-links">
          <Link href={`/game/${kin.slug}`}>View game</Link>
          <Link href={`/games-like/${kin.slug}`}>Games like this</Link>
        </div>
      </div>
    </div>
  );
}

export function ClaimStrip({ game }: { game: Game }) {
  return (
    <div className="claim-strip">
      <div className="claim-text">
        <strong>Work on {game.title}?</strong>
        <span>Claim this page to correct details or add missing context.</span>
      </div>
      {/* Wired to the Turnstile-gated claim form in a later pass. */}
      <button className="btn" disabled aria-disabled="true">
        Claim this game
      </button>
    </div>
  );
}

/** Renders a schema.org JSON-LD block into the document head. */
export function JsonLd({ data }: { data: Record<string, unknown> }) {
  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: JSON.stringify(data) }}
    />
  );
}
