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
VOCAB_PATH = REPO_ROOT / "data" / "vocabulary.json"
PUBLIC_DIR = REPO_ROOT / "public"        # search-index.json is a served static asset
TOP_TRAITS_PER_GAME = 15                  # weight vector per game in the search index
WEIGHT_QUANT = 255                        # weights quantized to 1 byte (0..255) in the index

# Hand-tuned umbrella search terms -> vocabulary trait ids. No single trait is
# named "rpg" or "shooter", so these map a genre/feeling word to the trait
# CLUSTER that defines it. Merged into each trait's alias list at build time
# (alongside the deterministic name + steam_tag_hints). Unknown ids are dropped
# with a warning, so a vocab rename can't silently break search.
GENRE_ALIASES = {
    # genres
    "rpg": ["struct_xp_levels", "mech_character_builds", "mech_class_system",
            "mech_party_management", "struct_skill_tree"],
    "role-playing": ["struct_xp_levels", "mech_character_builds", "mech_class_system"],
    "jrpg": ["struct_xp_levels", "mech_turn_based_combat", "mech_party_management", "aes_anime"],
    "crpg": ["mech_dialogue_trees", "mech_party_management", "mech_skill_checks", "aes_isometric"],
    "shooter": ["mech_gunplay", "mech_cover_shooter", "mech_twin_stick", "mech_bullet_hell"],
    "fps": ["mech_gunplay", "aes_first_person_view"],
    "platformer": ["mech_platforming", "mech_precision_platforming"],
    "metroidvania": ["struct_metroidvania"],
    "roguelike": ["struct_run_based", "struct_meta_progression", "mech_permadeath"],
    "roguelite": ["struct_run_based", "struct_meta_progression"],
    "deckbuilder": ["mech_deckbuilding", "mech_card_battler"],
    "deckbuilding": ["mech_deckbuilding", "mech_card_battler"],
    "strategy": ["mech_rts", "mech_4x", "mech_grand_strategy", "mech_tactical_combat"],
    "rts": ["mech_rts"],
    "4x": ["mech_4x"],
    "tactics": ["mech_tactical_combat", "struct_grid_map"],
    "tactical": ["mech_tactical_combat"],
    "fighting": ["mech_fighting_combos"],
    "fighter": ["mech_fighting_combos"],
    "racing": ["mech_vehicular"],
    "driving": ["mech_vehicular"],
    "survival": ["mech_survival_needs", "theme_survival"],
    "puzzle": ["mech_environmental_puzzles", "mech_logic_puzzles", "mech_physics_puzzles",
               "mech_spatial_puzzles"],
    "stealth": ["mech_stealth", "mech_social_stealth"],
    "sandbox": ["struct_sandbox", "mech_freeform_building"],
    "open world": ["struct_open_world"],
    "openworld": ["struct_open_world"],
    "soulslike": ["mech_soulslike_combat"],
    "souls": ["mech_soulslike_combat"],
    "hack and slash": ["mech_hack_and_slash"],
    "battle royale": ["struct_battle_royale"],
    "mmo": ["struct_mmo"],
    "farming": ["mech_farming"],
    "farm": ["mech_farming"],
    "city builder": ["mech_city_building"],
    "tower defense": ["mech_tower_defense"],
    "rhythm": ["mech_rhythm"],
    "sports": ["theme_sports"],
    "horror": ["theme_horror", "theme_psychological_horror", "tone_horrific", "tone_dread"],
    "detective": ["theme_detective"],
    "mystery": ["theme_mystery", "theme_detective"],
    "co-op": ["struct_online_coop", "struct_local_coop", "struct_coop_campaign"],
    "coop": ["struct_online_coop", "struct_local_coop", "struct_coop_campaign"],
    "multiplayer": ["struct_competitive_pvp", "struct_online_coop", "struct_team_based"],
    "pvp": ["struct_competitive_pvp"],
    # aesthetics / retro
    "retro": ["aes_pixel_art", "aes_retro_3d", "aes_chiptune"],
    "pixel": ["aes_pixel_art"],
    "pixel art": ["aes_pixel_art"],
    "8-bit": ["aes_pixel_art", "aes_chiptune"],
    "low poly": ["aes_low_poly"],
    "hand drawn": ["aes_hand_drawn"],
    "anime": ["aes_anime"],
    "cartoon": ["aes_cel_shaded"],
    "realistic": ["aes_photorealistic"],
    "noir": ["aes_noir"],
    "cyberpunk": ["theme_cyberpunk"],
    "steampunk": ["theme_steampunk"],
    # feelings / tone
    "melancholy": ["tone_melancholic"],
    "melancholic": ["tone_melancholic"],
    "sad": ["tone_melancholic", "tone_somber", "tone_bittersweet"],
    "cozy": ["tone_cozy", "tone_tender"],
    "comfy": ["tone_cozy"],
    "wholesome": ["tone_cozy", "tone_tender"],
    "relaxing": ["tone_serene", "tone_cozy"],
    "chill": ["tone_serene", "tone_cozy"],
    "tense": ["tone_tense"],
    "scary": ["tone_dread", "tone_horrific", "tone_uncanny"],
    "spooky": ["tone_dread", "tone_uncanny"],
    "funny": ["tone_absurdist", "tone_irreverent", "tone_playful"],
    "comedy": ["tone_absurdist", "tone_irreverent"],
    "hopeful": ["tone_hopeful"],
    "dark": ["tone_grim", "tone_bleak", "aes_gritty"],
    "grim": ["tone_grim", "tone_bleak"],
    "epic": ["tone_triumphant", "tone_sublime"],
    "lonely": ["tone_lonely", "theme_isolation"],
    "atmospheric": ["tone_contemplative", "tone_lonely", "aes_ambient_score"],
    "beautiful": ["aes_painterly", "aes_watercolor"],
    # themes
    "sci-fi": ["theme_science_fiction", "theme_space_opera"],
    "scifi": ["theme_science_fiction", "theme_space_opera"],
    "science fiction": ["theme_science_fiction"],
    "fantasy": ["theme_high_fantasy", "theme_dark_fantasy"],
    "medieval": ["theme_medieval"],
    "post-apocalyptic": ["theme_post_apocalyptic"],
    "apocalypse": ["theme_post_apocalyptic"],
    "zombie": ["theme_zombie"],
    "war": ["theme_military", "theme_war_cost"],
    "space": ["theme_space_opera", "theme_hard_sci_fi"],
    "historical": ["theme_historical"],
    "mythology": ["theme_mythology"],
    "pirate": ["theme_pirate"],
    "western": ["theme_western"],
}

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


def load_trait_hints() -> dict[str, list[str]]:
    """trait id -> steam_tag_hints — search aliases so a 'feeling' term like
    'roguelike' matches Run-Based games even though no trait is named that."""
    vocab = json.loads(VOCAB_PATH.read_text(encoding="utf-8"))
    return {t["id"]: t.get("steam_tag_hints", [])
            for group in vocab["groups"].values() for t in group["traits"]}


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


def top_game_ids(conn, n: int) -> set:
    """The n most-reviewed enriched games — the publishable subset."""
    rows = conn.execute(
        "SELECT g.igdb_id FROM games g JOIN (SELECT appid, "
        "json_extract(payload, '$.query_summary.total_reviews') AS rc "
        "FROM steam_cache WHERE endpoint = 'appreviews' AND ok = 1) r "
        "ON r.appid = g.steam_appid WHERE EXISTS "
        "(SELECT 1 FROM game_characteristics gc WHERE gc.game_id = g.igdb_id) "
        "ORDER BY CAST(rc AS INTEGER) DESC, g.igdb_id LIMIT ?",
        (n,),
    ).fetchall()
    return {r[0] for r in rows}


def build_search_index(enriched, traits_by_id, catalog, hints) -> dict:
    """Two-tier search index: a trait table (id, name, aliases for token->trait
    mapping) and per-game sparse weight vectors (top-15 traits, quantized to a
    byte). Aliases = trait name + steam_tag_hints + hand-tuned umbrella terms."""
    genre_by_trait: dict[str, list[str]] = {}
    dropped = set()
    for term, ids in GENRE_ALIASES.items():
        for tid in ids:
            if tid in catalog:
                genre_by_trait.setdefault(tid, []).append(term)
            else:
                dropped.add(tid)
    if dropped:
        print(f"  warning: {len(dropped)} alias trait ids not in vocab: {sorted(dropped)}")

    used = sorted({t["id"] for r in enriched for t in traits_by_id[r["igdb_id"]]})
    idx_of = {tid: i for i, tid in enumerate(used)}
    traits_out = []
    for tid in used:
        name = catalog[tid][0]
        aliases = {name.lower()}
        aliases.update(h.lower() for h in hints.get(tid, []))
        aliases.update(genre_by_trait.get(tid, []))
        traits_out.append({"id": tid, "name": name, "aliases": sorted(aliases)})

    games_out = []
    for r in enriched:
        ts = traits_by_id[r["igdb_id"]][:TOP_TRAITS_PER_GAME]  # already weight desc
        games_out.append({
            "slug": r["slug"],
            "title": r["title"],
            "t": [idx_of[t["id"]] for t in ts],
            "w": [max(1, round(t["weight"] * WEIGHT_QUANT)) for t in ts],
        })
    games_out.sort(key=lambda g: g["slug"])
    return {"traits": traits_out, "games": games_out}


def main() -> None:
    parser = argparse.ArgumentParser(description="Export gde.sqlite to data/export/*.json.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--top", type=int, default=None,
                        help="Publish only the top-N most-reviewed games (kin filtered "
                             "to the same set so every link resolves).")
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

        # Publishable set: all enriched games, or the top-N by review count.
        published = top_game_ids(conn, args.top) if args.top else set(traits_by_id)

        # Enriched + published games, deterministic order (popularity desc, id asc).
        games_by_id = {r["igdb_id"]: r for r in conn.execute("SELECT * FROM games")}
        enriched = [games_by_id[gid] for gid in traits_by_id
                    if gid in games_by_id and gid in published]
        enriched.sort(key=lambda r: (-(signal.get(r["igdb_id"], (0, 0))[1]), r["igdb_id"]))

        games = [game_object(r, traits_by_id[r["igdb_id"]], signal) for r in enriched]

        # Kin, split by kind, deterministic by rank. Both endpoints must be
        # published so every link resolves.
        kin: dict[str, list] = {}
        hidden: dict[str, list] = {}
        has_kin = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='kin'")}
        if has_kin:
            for r in conn.execute("SELECT * FROM kin ORDER BY game_id, kind, rank"):
                if r["game_id"] not in published or r["kin_game_id"] not in published:
                    continue
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

        search_index = build_search_index(enriched, traits_by_id, catalog, load_trait_hints())
        PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
        write_json(PUBLIC_DIR / "search-index.json", search_index)

        print(f"Exported {len(games)} games, "
              f"{sum(len(v) for v in kin.values())} match + "
              f"{sum(len(v) for v in hidden.values())} hidden-gem kin edges to {out_dir}; "
              f"search index: {len(search_index['traits'])} traits, "
              f"{len(search_index['games'])} games -> {PUBLIC_DIR / 'search-index.json'}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
