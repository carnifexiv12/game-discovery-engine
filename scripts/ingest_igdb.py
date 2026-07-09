#!/usr/bin/env python3
"""Ingest games from IGDB into the committed SQLite database (data/gde.sqlite).

IGDB is the spine of the pipeline: one request stream yields the catalogue plus,
via each game's external_games, the Steam app id for the majority of PC titles
(no fuzzy name-matching). That app id later unlocks the Steam augmentation pass
(scripts/ingest_steam.py) deterministically.

Auth
----
IGDB authenticates through Twitch OAuth (client_credentials grant). Set
IGDB_CLIENT_ID and IGDB_CLIENT_SECRET in the environment or in a gitignored
.env at the repo root (this script loads it automatically):

    IGDB_CLIENT_ID=...
    IGDB_CLIENT_SECRET=...

Filtering
---------
We pull *main games only* (game_type = 0), excluding DLC, bundles, editions, and
version forks (version_parent = null), and require a minimum IGDB rating_count so
we start with a corpus of real, known titles (~10-20k) rather than the full
~300k database of stubs. Tune with --min-rating-count; preview the size first
with --count-only.

Captured per game
-----------------
igdb_id, steam_appid (from external_games, Steam = external_game_source 1), title, slug,
year (from first_release_date), developer (first involved company flagged
developer), platforms, summary, cover_image_id, screenshot_ids. The summary and
the IGDB genres/themes are written to the `corpus` table as enrichment input
(sources: igdb_summary, igdb_tags).

Usage
-----
    python scripts/ingest_igdb.py --count-only               # how many match?
    python scripts/ingest_igdb.py --min-rating-count 8       # full pull
    python scripts/ingest_igdb.py --min-rating-count 8 --limit 500   # smoke test

Idempotent: games upsert on igdb_id; corpus rows are replaced per (game, source),
so re-running refreshes rather than duplicates.

Stdlib only — no pip install required.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = REPO_ROOT / "data" / "gde.sqlite"
ENV_PATH = REPO_ROOT / ".env"

TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
IGDB_GAMES_URL = "https://api.igdb.com/v4/games"
IGDB_COUNT_URL = "https://api.igdb.com/v4/games/count"

# IGDB deprecated the `category` field (games) and `external_games.category` in
# 2024, replacing them with `game_type` and `external_games.external_game_source`.
MAIN_GAME_TYPE = 0              # games.game_type value for a main game
STEAM_EXTERNAL_SOURCE = 1      # external_games.external_game_source value for Steam
PAGE_LIMIT = 500                # IGDB hard maximum rows per request

# IGDB field-expansion selection. Dotted paths pull nested objects in one call.
FIELDS = (
    "name, slug, summary, first_release_date, rating_count, "
    "cover.image_id, genres.name, themes.name, platforms.name, "
    "screenshots.image_id, involved_companies.developer, "
    "involved_companies.company.name, "
    "external_games.external_game_source, external_games.uid"
)


def load_env(path: Path) -> None:
    """Populate os.environ from a KEY=value .env file (does not override real env)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def get_token(client_id: str, client_secret: str) -> str:
    body = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
        }
    ).encode()
    req = urllib.request.Request(TWITCH_TOKEN_URL, data=body, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode())
    token = payload.get("access_token")
    if not token:
        raise RuntimeError(f"Twitch token exchange returned no access_token: {payload}")
    return token


def igdb_post(url: str, client_id: str, token: str, apicalypse: str,
              *, retries: int = 4) -> object:
    """POST an Apicalypse query, retrying on 429 / 5xx with backoff."""
    headers = {
        "Client-ID": client_id,
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            url, data=apicalypse.encode(), headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as err:
            transient = err.code == 429 or 500 <= err.code < 600
            if transient and attempt < retries:
                wait = 2 ** attempt
                print(f"  IGDB {err.code}; retry in {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            detail = err.read().decode(errors="replace")
            raise RuntimeError(f"IGDB {err.code} for query [{apicalypse}]: {detail}") from err
    raise RuntimeError("unreachable")


def where_clause(min_rating_count: int) -> str:
    return (
        f"game_type = {MAIN_GAME_TYPE} & version_parent = null "
        f"& rating_count >= {min_rating_count}"
    )


def count_games(client_id: str, token: str, min_rating_count: int) -> int:
    query = f"where {where_clause(min_rating_count)};"
    result = igdb_post(IGDB_COUNT_URL, client_id, token, query)
    return int(result.get("count", 0))


def iter_games(client_id: str, token: str, min_rating_count: int,
               *, limit: int | None, sleep: float):
    """Keyset-paginate by id (IGDB offset paging caps at 5000; id-cursor does not)."""
    last_id = 0
    fetched = 0
    where = where_clause(min_rating_count)
    while True:
        page_size = PAGE_LIMIT
        if limit is not None:
            page_size = min(PAGE_LIMIT, limit - fetched)
            if page_size <= 0:
                return
        query = (
            f"fields {FIELDS}; "
            f"where {where} & id > {last_id}; "
            f"sort id asc; limit {page_size};"
        )
        rows = igdb_post(IGDB_GAMES_URL, client_id, token, query)
        if not rows:
            return
        for row in rows:
            yield row
        fetched += len(rows)
        last_id = rows[-1]["id"]
        if len(rows) < page_size:
            return
        time.sleep(sleep)


def steam_appid(row: dict) -> int | None:
    for ext in row.get("external_games", []) or []:
        if ext.get("external_game_source") == STEAM_EXTERNAL_SOURCE:
            uid = ext.get("uid")
            try:
                return int(uid)
            except (TypeError, ValueError):
                return None
    return None


def developer_name(row: dict) -> str | None:
    for company in row.get("involved_companies", []) or []:
        if company.get("developer") and company.get("company"):
            return company["company"].get("name")
    return None


def release_year(row: dict) -> int | None:
    ts = row.get("first_release_date")
    if not ts:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).year


def names(items) -> list[str]:
    return [i["name"] for i in (items or []) if i.get("name")]


def to_game_row(row: dict) -> dict:
    return {
        "igdb_id": row["id"],
        "steam_appid": steam_appid(row),
        "title": row.get("name") or "Untitled",
        "slug": row.get("slug") or f"igdb-{row['id']}",
        "year": release_year(row),
        "developer": developer_name(row),
        "platforms": json.dumps(names(row.get("platforms"))),
        "summary": row.get("summary"),
        "cover_image_id": (row.get("cover") or {}).get("image_id"),
        "screenshot_ids": json.dumps(
            [s["image_id"] for s in row.get("screenshots", []) or [] if s.get("image_id")]
        ),
    }


UPSERT_GAME = """
INSERT INTO games (igdb_id, steam_appid, title, slug, year, developer,
                   platforms, summary, cover_image_id, screenshot_ids)
VALUES (:igdb_id, :steam_appid, :title, :slug, :year, :developer,
        :platforms, :summary, :cover_image_id, :screenshot_ids)
ON CONFLICT(igdb_id) DO UPDATE SET
    steam_appid=excluded.steam_appid, title=excluded.title, slug=excluded.slug,
    year=excluded.year, developer=excluded.developer, platforms=excluded.platforms,
    summary=excluded.summary, cover_image_id=excluded.cover_image_id,
    screenshot_ids=excluded.screenshot_ids;
"""


def write_corpus(conn: sqlite3.Connection, game_id: int, source: str, text: str) -> None:
    conn.execute(
        "DELETE FROM corpus WHERE game_id = ? AND source = ?", (game_id, source)
    )
    if text:
        conn.execute(
            "INSERT INTO corpus (game_id, source, text) VALUES (?, ?, ?)",
            (game_id, source, text),
        )


def persist(conn: sqlite3.Connection, row: dict) -> tuple[bool, bool]:
    """Upsert one game + its corpus. Returns (had_steam_appid, had_summary)."""
    game = to_game_row(row)
    conn.execute(UPSERT_GAME, game)
    write_corpus(conn, game["igdb_id"], "igdb_summary", row.get("summary") or "")
    tags = ", ".join(names(row.get("genres")) + names(row.get("themes")))
    write_corpus(conn, game["igdb_id"], "igdb_tags", tags)
    return (game["steam_appid"] is not None, bool(row.get("summary")))


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest games from IGDB into gde.sqlite.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Path to the SQLite file.")
    parser.add_argument(
        "--min-rating-count", type=int, default=8,
        help="Minimum IGDB rating_count to include (higher = fewer, better-known games).",
    )
    parser.add_argument("--limit", type=int, default=None, help="Cap total games (smoke test).")
    parser.add_argument("--count-only", action="store_true", help="Print the match count and exit.")
    parser.add_argument("--sleep", type=float, default=0.25, help="Seconds between requests (rate limit).")
    args = parser.parse_args()

    load_env(ENV_PATH)
    client_id = os.environ.get("IGDB_CLIENT_ID")
    client_secret = os.environ.get("IGDB_CLIENT_SECRET")
    if not client_id or not client_secret:
        sys.exit("IGDB_CLIENT_ID / IGDB_CLIENT_SECRET not set (env or .env).")

    token = get_token(client_id, client_secret)
    total = count_games(client_id, token, args.min_rating_count)
    print(f"IGDB matches for rating_count >= {args.min_rating_count}: {total} games")
    if args.count_only:
        return

    db_path = Path(args.db)
    if not db_path.exists():
        sys.exit(f"{db_path} not found — run scripts/init_db.py first.")

    conn = sqlite3.connect(db_path)
    ingested = with_steam = with_summary = 0
    try:
        for row in iter_games(
            client_id, token, args.min_rating_count, limit=args.limit, sleep=args.sleep
        ):
            had_steam, had_summary = persist(conn, row)
            ingested += 1
            with_steam += int(had_steam)
            with_summary += int(had_summary)
            if ingested % 500 == 0:
                conn.commit()
                print(f"  {ingested} ingested ({with_steam} with Steam id)…")
        conn.commit()
    finally:
        conn.close()

    pct = (100 * with_steam // ingested) if ingested else 0
    print(
        f"Done. Ingested {ingested} games — {with_steam} with a Steam app id "
        f"({pct}%), {with_summary} with a summary."
    )


if __name__ == "__main__":
    main()
