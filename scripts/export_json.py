#!/usr/bin/env python3
"""Export the SQLite database to static JSON for the Next.js build.

Reads data/gde.sqlite and writes the committed export the site builds from:

  data/export/games.json       one denormalized record per enriched game
                               (profile + synthesized prose sections + store
                               links + Steam review signal).
  data/export/kin.json         { kin, hidden_gems } — each maps a game slug to
                               its ranked kindred games (match) and its
                               hidden-gem kin, in the lib/data.ts shape.
  data/export/search-index.json  slim [{slug, title, traits}] for client search.

Only games that have been enriched (>=1 game_characteristics row) are exported —
they're the ones with real content. Prose sections and kin blurbs/reasoning are
synthesized deterministically from the trait rationales and shared traits (no
LLM here), so re-exports against the same DB produce byte-identical files.

The DB is a local build artifact; THIS export is what gets committed and what
Cloudflare Pages builds from (see README → Data pipeline).

Stdlib only. No API key needed.

Usage
-----
    python scripts/export_json.py
    python scripts/export_json.py --out data/export
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = REPO_ROOT / "data" / "gde.sqlite"
DEFAULT_OUT = REPO_ROOT / "data" / "export"

SITE = {
    "name": "Game Discovery Engine",
    "url": "https://gamediscoveryengine.com",
    "description": (
        "Find your next game by the qualities that actually matter — tone, "
        "mechanics, aesthetic, and structure — not by genre tags."
    ),
}

# Group -> (order, human section heading) for the synthesized prose sections.
GROUP_SECTIONS = {
    "tone": (0, "What it feels like"),
    "theme": (1, "What it's about"),
    "mechanics": (2, "How it plays"),
    "aesthetic": (3, "How it looks and sounds"),
    "structure": (4, "How it's structured"),
}


def first_sentence(text: str, cap: int = 140) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    end = text.find(". ")
    sentence = text[: end + 1] if end != -1 else text
    return sentence[:cap].rstrip()


def load_catalog(conn):
    """trait id -> (name, group_name)."""
    return {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT id, name, group_name FROM characteristics"
    )}


def load_game_traits(conn, catalog):
    """game_id -> list of {id, name, group, weight, rationale}, weight desc."""
    per_game: dict[int, list[dict]] = {}
    for gid, cid, weight, rationale in conn.execute(
        "SELECT game_id, characteristic_id, weight, rationale FROM game_characteristics"
    ):
        name, group = catalog.get(cid, (cid, "unknown"))
        per_game.setdefault(gid, []).append(
            {"id": cid, "name": name, "group": group, "weight": round(weight, 3),
             "rationale": rationale or ""}
        )
    for traits in per_game.values():
        traits.sort(key=lambda t: (-t["weight"], t["id"]))
    return per_game


def load_review_signal(conn):
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='steam_cache'"
    ).fetchone():
        return {}
    appid_to_game = {appid: gid for gid, appid in conn.execute(
        "SELECT igdb_id, steam_appid FROM games WHERE steam_appid IS NOT NULL"
    )}
    signal: dict[int, tuple[float, int]] = {}
    for appid, payload in conn.execute(
        "SELECT appid, payload FROM steam_cache WHERE endpoint = 'appreviews' AND ok = 1"
    ):
        gid = appid_to_game.get(appid)
        if gid is None or not payload:
            continue
        s = (json.loads(payload) or {}).get("query_summary") or {}
        total, pos = s.get("total_reviews") or 0, s.get("total_positive") or 0
        if total:
            signal[gid] = (round(pos / total, 3), int(total))
    return signal


def build_sections(traits: list[dict]) -> list[dict]:
    by_group: dict[str, list[dict]] = {}
    for t in traits:
        by_group.setdefault(t["group"], []).append(t)
    sections = []
    for group, (_, heading) in sorted(GROUP_SECTIONS.items(), key=lambda kv: kv[1][0]):
        group_traits = by_group.get(group)
        if not group_traits:
            continue
        rationales = [t["rationale"] for t in group_traits[:3] if t["rationale"]]
        prose = " ".join(rationales) or (
            "Defined by " + ", ".join(t["name"] for t in group_traits[:3]) + "."
        )
        sections.append({
            "heading": heading,
            "prose": prose,
            "traits": [{"id": t["id"], "name": t["name"], "weight": t["weight"]}
                       for t in group_traits],
        })
    return sections


def game_object(row, traits, signal) -> dict:
    appid = row["steam_appid"]
    store = {}
    if appid:
        store["steam"] = f"https://store.steampowered.com/app/{appid}/"
    pct, count = signal.get(row["igdb_id"], (None, None))
    return {
        "slug": row["slug"],
        "igdb_id": row["igdb_id"],
        "steam_appid": appid,
        "title": row["title"],
        "year": row["year"],
        "developer": row["developer"],
        "platforms": json.loads(row["platforms"] or "[]"),
        "summary": row["summary"] or "",
        "cover_image_id": row["cover_image_id"],
        "cover_url": None,
        "screenshot_ids": json.loads(row["screenshot_ids"] or "[]"),
        "store_links": store,
        "tagline": first_sentence(row["summary"]),
        "steam_review_pct": pct,
        "steam_review_count": count,
        "characteristics": [
            {"id": t["id"], "name": t["name"], "group": t["group"],
             "weight": t["weight"], "rationale": t["rationale"]}
            for t in traits
        ],
        "sections": build_sections(traits),
    }


def shared_traits(a_traits, b_traits, limit=4):
    """Traits present in both games, ranked by combined weight (for reasoning)."""
    a_by = {t["id"]: t for t in a_traits}
    shared = [t for t in b_traits if t["id"] in a_by]
    shared.sort(key=lambda t: -(t["weight"] + a_by[t["id"]]["weight"]))
    return [{"id": t["id"], "name": t["name"], "weight": t["weight"]} for t in shared[:limit]]


def reasoning_line(shared: list[dict]) -> str:
    if not shared:
        return "Kindred by their overall characteristic profile."
    names = ", ".join(t["name"] for t in shared)
    return f"Both lean into {names}."


def kin_entry(kin_row, games_by_id, traits_by_id, source_id, signal, *, gem=False) -> dict | None:
    kid = kin_row["kin_game_id"]
    target = games_by_id.get(kid)
    if target is None:
        return None
    shared = shared_traits(traits_by_id.get(source_id, []), traits_by_id.get(kid, []))
    entry = {
        "slug": target["slug"],
        "title": target["title"],
        "score": round(kin_row["score"], 3),
        "match_pct": round(kin_row["score"] * 100),
        "blurb": kin_row["blurb"] or "",
        "reasoning": reasoning_line(shared),
        "traits": shared,
    }
    if gem:
        pct, count = signal.get(kid, (None, None))
        entry["steam_review_pct"] = pct
        entry["steam_review_count"] = count
    return entry


def write_json(path: Path, data) -> None:
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Export gde.sqlite to data/export/*.json.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    db_path, out_dir = Path(args.db), Path(args.out)
    if not db_path.exists():
        sys.exit(f"{db_path} not found — run the pipeline first.")
    out_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        catalog = load_catalog(conn)
        traits_by_id = load_game_traits(conn, catalog)
        signal = load_review_signal(conn)

        # Enriched games only, deterministic order (popularity desc, id asc).
        games_by_id = {r["igdb_id"]: r for r in conn.execute("SELECT * FROM games")}
        enriched = [games_by_id[gid] for gid in traits_by_id if gid in games_by_id]
        enriched.sort(key=lambda r: (-(signal.get(r["igdb_id"], (0, 0))[1]), r["igdb_id"]))

        games = [game_object(r, traits_by_id[r["igdb_id"]], signal) for r in enriched]

        # Kin, split by kind, deterministic by rank.
        kin: dict[str, list] = {}
        hidden: dict[str, list] = {}
        has_kin = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='kin'")}
        if has_kin:
            for r in conn.execute("SELECT * FROM kin ORDER BY game_id, kind, rank"):
                src = games_by_id.get(r["game_id"])
                if src is None:
                    continue
                bucket = hidden if r["kind"] == "hidden_gem" else kin
                entry = kin_entry(r, games_by_id, traits_by_id, r["game_id"], signal,
                                  gem=(r["kind"] == "hidden_gem"))
                if entry:
                    bucket.setdefault(src["slug"], []).append(entry)

        write_json(out_dir / "games.json", {"site": SITE, "games": games})
        write_json(out_dir / "kin.json", {"kin": kin, "hidden_gems": hidden})

        index = sorted(
            (
                {"slug": r["slug"], "title": r["title"],
                 "traits": [t["name"] for t in traits_by_id[r["igdb_id"]][:5]]}
                for r in enriched
            ),
            key=lambda e: e["slug"],
        )
        write_json(out_dir / "search-index.json", index)

        print(f"Exported {len(games)} games, "
              f"{sum(len(v) for v in kin.values())} match + "
              f"{sum(len(v) for v in hidden.values())} hidden-gem kin edges, "
              f"search index of {len(index)} -> {out_dir}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
