#!/usr/bin/env python3
"""Initialize the committed SQLite database (data/gde.sqlite).

Creates the full schema used across the ingest -> enrich -> kin -> export
pipeline. Running this script is idempotent: it creates tables only if they do
not already exist, so it is safe to re-run against an existing database.

Schema
------
games                  One row per game. IGDB is the primary source; steam_appid
                       links to the Steam catalogue when known. JSON columns
                       (platforms, screenshot_ids) hold serialized arrays.
characteristics        The controlled vocabulary of traits, mirrored from
                       data/vocabulary.json (id, group_name, name, definition).
game_characteristics   Weighted trait assignments per game, with a rationale
                       string explaining why the trait applies. weight is 0..1.
kin                    Precomputed "kindred games" edges: for each game, a set
                       of related games with a similarity score and a blurb.
corpus                 Raw source text per game (reviews, descriptions, etc.)
                       used as input to enrichment. source labels the origin.

Usage
-----
    python scripts/init_db.py [--db data/gde.sqlite] [--seed-vocab]

--seed-vocab  Also populate the `characteristics` table from
              data/vocabulary.json so the DB has the trait catalogue on first
              build. Existing rows with the same id are left untouched.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = REPO_ROOT / "data" / "gde.sqlite"
VOCAB_PATH = REPO_ROOT / "data" / "vocabulary.json"

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    igdb_id         INTEGER PRIMARY KEY,
    steam_appid     INTEGER,
    title           TEXT NOT NULL,
    slug            TEXT NOT NULL UNIQUE,
    year            INTEGER,
    developer       TEXT,
    platforms       TEXT,            -- JSON array of platform names
    summary         TEXT,
    cover_image_id  TEXT,
    screenshot_ids  TEXT             -- JSON array of image ids
);

CREATE TABLE IF NOT EXISTS characteristics (
    id          TEXT PRIMARY KEY,    -- e.g. "tone_melancholic"
    group_name  TEXT NOT NULL,       -- tone | theme | mechanics | aesthetic | structure
    name        TEXT NOT NULL,
    definition  TEXT,
    scale       TEXT                 -- "graded" | "binary" (weight interpretation)
);

CREATE TABLE IF NOT EXISTS game_characteristics (
    game_id            INTEGER NOT NULL REFERENCES games(igdb_id),
    characteristic_id  TEXT    NOT NULL REFERENCES characteristics(id),
    weight             REAL    NOT NULL DEFAULT 0.0,
    rationale          TEXT,
    PRIMARY KEY (game_id, characteristic_id)
);

CREATE TABLE IF NOT EXISTS kin (
    game_id      INTEGER NOT NULL REFERENCES games(igdb_id),
    kin_game_id  INTEGER NOT NULL REFERENCES games(igdb_id),
    kind         TEXT    NOT NULL DEFAULT 'match',   -- 'match' | 'hidden_gem'
    rank         INTEGER NOT NULL DEFAULT 0,
    score        REAL    NOT NULL DEFAULT 0.0,
    blurb        TEXT,
    PRIMARY KEY (game_id, kin_game_id, kind)
);

CREATE TABLE IF NOT EXISTS corpus (
    game_id  INTEGER NOT NULL REFERENCES games(igdb_id),
    source   TEXT    NOT NULL,       -- e.g. "steam_reviews", "igdb_summary"
    text     TEXT
);

CREATE INDEX IF NOT EXISTS idx_gc_char ON game_characteristics(characteristic_id);
CREATE INDEX IF NOT EXISTS idx_kin_game ON kin(game_id);
CREATE INDEX IF NOT EXISTS idx_corpus_game ON corpus(game_id);
"""


def init_db(db_path: Path, seed_vocab: bool = False) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)
        if seed_vocab and VOCAB_PATH.exists():
            _seed_vocabulary(conn)
        conn.commit()
    finally:
        conn.close()
    print(f"Initialized database at {db_path}")


def _seed_vocabulary(conn: sqlite3.Connection) -> None:
    vocab = json.loads(VOCAB_PATH.read_text(encoding="utf-8"))
    rows = []
    for group_key, group in vocab.get("groups", {}).items():
        for trait in group.get("traits", []):
            rows.append(
                (
                    trait["id"],
                    group_key,
                    trait["name"],
                    trait.get("definition", ""),
                    trait.get("scale", "graded"),
                )
            )
    conn.executemany(
        "INSERT OR IGNORE INTO characteristics (id, group_name, name, definition, scale) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    print(f"Seeded {len(rows)} characteristics from vocabulary.json")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the committed SQLite database.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Path to the SQLite file.")
    parser.add_argument(
        "--seed-vocab",
        action="store_true",
        help="Populate characteristics from data/vocabulary.json.",
    )
    args = parser.parse_args()
    init_db(Path(args.db), seed_vocab=args.seed_vocab)


if __name__ == "__main__":
    main()
