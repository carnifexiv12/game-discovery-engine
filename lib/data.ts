/**
 * Build-time data loader.
 *
 * Reads the static JSON export produced by scripts/export_json.py
 * (data/export/games.json and data/export/kin.json) and exposes typed
 * accessors used from getStaticProps / getStaticPaths. These run only at build
 * time in Node, so synchronous fs reads are fine and nothing ships to the
 * client except the props each page selects.
 *
 * A small hand-authored sample export lives in data/export/ so every route
 * renders before the real pipeline exists.
 */

import fs from "fs";
import path from "path";

export type GroupName =
  | "tone_theme"
  | "mechanics"
  | "aesthetic"
  | "structure";

/** A trait reference as it appears attached to a game or section. */
export interface TraitRef {
  id: string;
  name: string;
  /** Present on a game's top-level characteristics; optional on section pills. */
  group?: GroupName | string;
  /** 0..1 strength of the trait for this game. */
  weight: number;
  rationale?: string;
}

export interface ProseSection {
  heading: string;
  prose: string;
  traits: TraitRef[];
}

export interface StoreLinks {
  steam?: string;
  official?: string;
  [store: string]: string | undefined;
}

export interface Game {
  slug: string;
  igdb_id: number;
  steam_appid: number | null;
  title: string;
  year: number | null;
  developer: string | null;
  platforms: string[];
  summary: string;
  cover_image_id: string | null;
  cover_url: string | null;
  screenshot_ids: string[];
  store_links: StoreLinks;
  tagline: string;
  characteristics: TraitRef[];
  sections: ProseSection[];
}

export interface KinEntry {
  slug: string;
  title: string;
  /** Raw similarity score, 0..1. */
  score: number;
  /** Whole-number match percentage for display. */
  match_pct: number;
  blurb: string;
  reasoning: string;
  traits: TraitRef[];
}

export interface SiteMeta {
  name: string;
  url: string;
  description: string;
}

interface GamesFile {
  site: SiteMeta;
  games: Game[];
}

interface KinFile {
  kin: Record<string, KinEntry[]>;
}

const EXPORT_DIR = path.join(process.cwd(), "data", "export");

function readJson<T>(file: string): T {
  const full = path.join(EXPORT_DIR, file);
  return JSON.parse(fs.readFileSync(full, "utf-8")) as T;
}

// Read once per build. Module scope is fine: the export is static.
let _gamesFile: GamesFile | null = null;
let _kinFile: KinFile | null = null;

function gamesFile(): GamesFile {
  if (!_gamesFile) _gamesFile = readJson<GamesFile>("games.json");
  return _gamesFile;
}

function kinFile(): KinFile {
  if (!_kinFile) _kinFile = readJson<KinFile>("kin.json");
  return _kinFile;
}

export function getSiteMeta(): SiteMeta {
  return gamesFile().site;
}

export function getAllGames(): Game[] {
  return gamesFile().games;
}

export function getAllSlugs(): string[] {
  return getAllGames().map((g) => g.slug);
}

export function getGameBySlug(slug: string): Game | null {
  return getAllGames().find((g) => g.slug === slug) ?? null;
}

export function getKinForGame(slug: string): KinEntry[] {
  return kinFile().kin[slug] ?? [];
}

/** Games with at least one kin entry — the routes worth statically generating. */
export function getGamesWithKin(): string[] {
  const kin = kinFile().kin;
  return Object.keys(kin).filter((slug) => (kin[slug]?.length ?? 0) > 0);
}
