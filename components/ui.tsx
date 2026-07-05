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

export function Cover({
  game,
  small = false,
}: {
  game: Pick<Game, "slug" | "title" | "cover_url">;
  small?: boolean;
}) {
  const cls = small ? "cover small" : "cover";
  if (game.cover_url) {
    return (
      <div className={cls} style={{ padding: 0 }}>
        {/* Static export + images.unoptimized: plain img is intentional. */}
        <img src={game.cover_url} alt={`${game.title} cover art`} />
      </div>
    );
  }
  return (
    <div className={cls} style={{ background: coverGradient(game.slug) }}>
      {game.title}
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
