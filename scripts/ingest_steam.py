#!/usr/bin/env python3
"""Augment IGDB games with Steam data — a resumable crawl into data/gde.sqlite.

For every game that already has a steam_appid (set by ingest_igdb.py), this pulls
three public Steam sources — no partner access, no API key:

  appreviews  store.steampowered.com/appreviews/{appid}  -> review score + counts
              (the quality/popularity signal; "closest hidden gem" uses this)
  steamspy    steamspy.com/api.php?request=appdetails      -> crowd tags + owners
              (a HINT layer for enrichment, never the similarity substrate)
  appdetails  store.steampowered.com/api/appdetails        -> description, genres,
              categories, release date, screenshots

Resumability is the whole point. appdetails is rate-limited to ~200 requests per
5 minutes, so ~7k appids is a 50-60 hour crawl that WILL be interrupted. Every
response is cached to SQLite the instant it arrives (steam_cache), and startup
skips anything already cached — so a restart costs nothing. appdetails returns
{"success": false} for delisted / region-locked apps; those are cached as
tombstones (ok = 0) so they are never retried.

Pass order matters: appreviews and steamspy finish in well under two hours
combined (gentle limits), so run them first to get the review-score signal early
— enrichment prompt design can start against real data while the long appdetails
crawl grinds in the background. Default order is exactly that.

Each cached response is also distilled into the `corpus` table as enrichment
input (sources: steam_reviews, steam_tags, steam_description). Re-derive from the
cache at any time with --derive-only (no network).

Usage
-----
    python scripts/ingest_steam.py --passes appreviews,steamspy   # fast signal first
    python scripts/ingest_steam.py --passes appdetails            # the long grind
    python scripts/ingest_steam.py                                # all three, in order
    python scripts/ingest_steam.py --derive-only                  # rebuild corpus from cache

Stdlib only — no pip install required.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = REPO_ROOT / "data" / "gde.sqlite"

APPREVIEWS_URL = (
    "https://store.steampowered.com/appreviews/{appid}"
    "?json=1&num_per_page=0&language=all&purchase_type=all&review_type=all"
)
STEAMSPY_URL = "https://steamspy.com/api.php?request=appdetails&appid={appid}"
APPDETAILS_URL = "https://store.steampowered.com/api/appdetails?appids={appid}&l=english"

USER_AGENT = "game-discovery-engine/1.0 (+https://gamediscoveryengine.com)"

# Per-pass default delay between requests (seconds). appdetails is the strict one
# (~200 / 5 min ≈ one per 1.5s); steamspy asks for ≤1/s; appreviews is generous.
DEFAULT_SLEEP = {"appreviews": 1.0, "steamspy": 1.0, "appdetails": 1.6}
PASS_ORDER = ["appreviews", "steamspy", "appdetails"]

CACHE_DDL = """
CREATE TABLE IF NOT EXISTS steam_cache (
    appid       INTEGER NOT NULL,
    endpoint    TEXT    NOT NULL,   -- appreviews | steamspy | appdetails
    ok          INTEGER NOT NULL,   -- 1 = payload present, 0 = tombstone
    payload     TEXT,               -- raw JSON (null for tombstones)
    fetched_at  TEXT    NOT NULL,
    PRIMARY KEY (appid, endpoint)
);
"""

_TAG_RE = re.compile(r"<[^>]+>")


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def http_get_json(url: str, *, retries: int = 4):
    """GET a URL and parse JSON. Returns (ok, data). ok=False on 4xx that is not
    429 (treat as a permanent miss). Retries 429/5xx with backoff; a 429 sleeps
    long because Steam's storefront throttles aggressively."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            try:
                return True, json.loads(raw)
            except json.JSONDecodeError:
                return False, None  # non-JSON (rare HTML error page) -> miss
        except urllib.error.HTTPError as err:
            if err.code == 429 or 500 <= err.code < 600:
                if attempt < retries:
                    wait = 60 if err.code == 429 else 2 ** attempt
                    print(f"    HTTP {err.code}; backing off {wait}s", file=sys.stderr)
                    time.sleep(wait)
                    continue
                raise
            return False, None  # 4xx (e.g. 403 region lock) -> permanent miss
        except (urllib.error.URLError, TimeoutError) as err:
            if attempt < retries:
                wait = 2 ** attempt
                print(f"    {err}; retry in {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            raise
    return False, None


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #
def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(CACHE_DDL)
    conn.commit()


def all_appids(conn: sqlite3.Connection, limit: int | None) -> list[int]:
    rows = conn.execute(
        "SELECT DISTINCT steam_appid FROM games "
        "WHERE steam_appid IS NOT NULL ORDER BY steam_appid"
    ).fetchall()
    ids = [r[0] for r in rows]
    return ids[:limit] if limit else ids


def cached_appids(conn: sqlite3.Connection, endpoint: str) -> set[int]:
    rows = conn.execute(
        "SELECT appid FROM steam_cache WHERE endpoint = ?", (endpoint,)
    ).fetchall()
    return {r[0] for r in rows}


def cache_put(conn: sqlite3.Connection, appid: int, endpoint: str,
              ok: bool, payload: object) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO steam_cache (appid, endpoint, ok, payload, fetched_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            appid,
            endpoint,
            1 if ok else 0,
            json.dumps(payload) if ok else None,
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ),
    )
    conn.commit()  # commit per row: an interrupt never loses fetched work


# --------------------------------------------------------------------------- #
# Corpus derivation (enrichment input)
# --------------------------------------------------------------------------- #
def write_corpus(conn: sqlite3.Connection, game_id: int, source: str, text: str) -> None:
    conn.execute("DELETE FROM corpus WHERE game_id = ? AND source = ?", (game_id, source))
    if text:
        conn.execute(
            "INSERT INTO corpus (game_id, source, text) VALUES (?, ?, ?)",
            (game_id, source, text),
        )


def strip_html(value: str) -> str:
    return html.unescape(_TAG_RE.sub(" ", value or "")).strip()


def appid_to_game_ids(conn: sqlite3.Connection) -> dict[int, list[int]]:
    """Map each steam_appid to the igdb game_id(s) that reference it."""
    mapping: dict[int, list[int]] = {}
    for gid, appid in conn.execute(
        "SELECT igdb_id, steam_appid FROM games WHERE steam_appid IS NOT NULL"
    ):
        mapping.setdefault(appid, []).append(gid)
    return mapping


def derive_appreviews(payload: dict) -> str:
    s = (payload or {}).get("query_summary") or {}
    total = s.get("total_reviews") or 0
    pos = s.get("total_positive") or 0
    desc = s.get("review_score_desc") or ""
    if not total:
        return ""
    pct = round(100 * pos / total)
    return f"Steam reviews: {desc} — {pct}% positive of {total} reviews."


def derive_steamspy(payload: dict, top: int = 20) -> str:
    tags = (payload or {}).get("tags") or {}
    if isinstance(tags, dict) and tags:
        ranked = sorted(tags.items(), key=lambda kv: kv[1], reverse=True)[:top]
        return "Steam user tags: " + ", ".join(name for name, _ in ranked) + "."
    return ""


def derive_appdetails(payload: dict) -> str:
    data = (payload or {}).get("data") or {}
    parts: list[str] = []
    desc = strip_html(data.get("short_description") or data.get("detailed_description") or "")
    if desc:
        parts.append(desc)
    genres = [g["description"] for g in data.get("genres", []) if g.get("description")]
    cats = [c["description"] for c in data.get("categories", []) if c.get("description")]
    if genres:
        parts.append("Genres: " + ", ".join(genres) + ".")
    if cats:
        parts.append("Features: " + ", ".join(cats) + ".")
    return "\n".join(parts)


DERIVERS = {
    "appreviews": ("steam_reviews", derive_appreviews),
    "steamspy": ("steam_tags", derive_steamspy),
    "appdetails": ("steam_description", derive_appdetails),
}


def derive_one(conn, appid: int, endpoint: str, payload: object, game_ids: list[int]) -> None:
    source, fn = DERIVERS[endpoint]
    text = fn(payload) if isinstance(payload, dict) else ""
    for gid in game_ids:
        write_corpus(conn, gid, source, text)


# --------------------------------------------------------------------------- #
# Fetch dispatch
# --------------------------------------------------------------------------- #
def fetch(endpoint: str, appid: int) -> tuple[bool, object]:
    """Return (ok, payload). ok=False -> tombstone (delisted / region-locked / miss)."""
    if endpoint == "appreviews":
        ok, data = http_get_json(APPREVIEWS_URL.format(appid=appid))
        if ok and isinstance(data, dict) and data.get("success") == 1:
            return True, data
        return False, None
    if endpoint == "steamspy":
        ok, data = http_get_json(STEAMSPY_URL.format(appid=appid))
        if ok and isinstance(data, dict) and data.get("name"):
            return True, data
        return False, None
    if endpoint == "appdetails":
        ok, data = http_get_json(APPDETAILS_URL.format(appid=appid))
        entry = (data or {}).get(str(appid)) if isinstance(data, dict) else None
        if ok and entry and entry.get("success") and entry.get("data"):
            return True, entry
        return False, None
    raise ValueError(f"unknown endpoint {endpoint}")


# --------------------------------------------------------------------------- #
# Passes
# --------------------------------------------------------------------------- #
def fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, _ = divmod(rem, 60)
    if h:
        return f"~{h}h {m}m"
    if m:
        return f"~{m}m"
    return "<1m"


def run_pass(conn, endpoint: str, appids: list[int], game_ids_by_appid, *, sleep: float) -> None:
    done_set = cached_appids(conn, endpoint)
    todo = [a for a in appids if a not in done_set]
    total = len(appids)
    already = total - len(todo)
    per_item = sleep + 0.6  # sleep + rough request time, for the ETA
    print(f"[{endpoint}] {already}/{total} already cached; {len(todo)} to fetch "
          f"(est {fmt_duration(len(todo) * per_item)}).")

    ok_count = tomb_count = skipped = 0
    for i, appid in enumerate(todo, start=1):
        try:
            ok, payload = fetch(endpoint, appid)
        except Exception as err:
            # A sustained error survived fetch()'s own retries. Don't crash the
            # whole crawl over one item — skip it (uncached, so it retries next
            # run) and keep going.
            print(f"  [{endpoint}] {appid} skipped after retries ({err}); "
                  "will retry next run", file=sys.stderr)
            skipped += 1
            time.sleep(sleep)
            continue
        cache_put(conn, appid, endpoint, ok, payload)
        if ok:
            derive_one(conn, appid, endpoint, payload, game_ids_by_appid.get(appid, []))
            ok_count += 1
        else:
            tomb_count += 1
        if i % 25 == 0 or i == len(todo):
            done = already + i
            eta = fmt_duration((len(todo) - i) * per_item)
            print(f"  [{endpoint}] {done}/{total}, {eta} remaining "
                  f"({ok_count} ok, {tomb_count} tombstoned)")
        time.sleep(sleep)
    tail = f", {skipped} skipped (retry next run)" if skipped else ""
    print(f"[{endpoint}] done: {ok_count} fetched, {tomb_count} tombstoned this run{tail}.")


def derive_all(conn, endpoint: str, game_ids_by_appid) -> None:
    """Rebuild corpus for one endpoint from the cache, no network."""
    rows = conn.execute(
        "SELECT appid, ok, payload FROM steam_cache WHERE endpoint = ?", (endpoint,)
    ).fetchall()
    n = 0
    for appid, ok, payload in rows:
        data = json.loads(payload) if (ok and payload) else None
        derive_one(conn, appid, endpoint, data, game_ids_by_appid.get(appid, []))
        n += 1
    conn.commit()
    print(f"[{endpoint}] re-derived corpus from {n} cached rows.")


# --------------------------------------------------------------------------- #
def main() -> None:
    parser = argparse.ArgumentParser(description="Resumable Steam augmentation crawl.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument(
        "--passes", default=",".join(PASS_ORDER),
        help="Comma list in run order (default: appreviews,steamspy,appdetails).",
    )
    parser.add_argument("--limit", type=int, default=None, help="Cap appids (smoke test).")
    parser.add_argument("--derive-only", action="store_true",
                        help="Rebuild corpus from the cache without any network calls.")
    for name, default in DEFAULT_SLEEP.items():
        parser.add_argument(f"--{name}-sleep", type=float, default=default,
                            help=f"Seconds between {name} requests (default {default}).")
    args = parser.parse_args()

    passes = [p.strip() for p in args.passes.split(",") if p.strip()]
    bad = [p for p in passes if p not in PASS_ORDER]
    if bad:
        sys.exit(f"unknown pass(es): {bad}; valid: {PASS_ORDER}")

    db_path = Path(args.db)
    if not db_path.exists():
        sys.exit(f"{db_path} not found — run init_db.py + ingest_igdb.py first.")

    conn = sqlite3.connect(db_path)
    try:
        ensure_schema(conn)
        game_ids_by_appid = appid_to_game_ids(conn)
        appids = all_appids(conn, args.limit)
        if not appids:
            sys.exit("No games with a steam_appid — run ingest_igdb.py first.")
        print(f"{len(appids)} appids with Steam ids.")

        if args.derive_only:
            for endpoint in passes:
                derive_all(conn, endpoint, game_ids_by_appid)
            return

        sleeps = {name: getattr(args, f"{name}_sleep") for name in DEFAULT_SLEEP}
        for endpoint in passes:
            run_pass(conn, endpoint, appids, game_ids_by_appid, sleep=sleeps[endpoint])
    finally:
        conn.close()


if __name__ == "__main__":
    main()
