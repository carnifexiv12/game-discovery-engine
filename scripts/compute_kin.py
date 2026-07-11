#!/usr/bin/env python3
"""Compute kindred-game edges from weighted characteristic profiles.

Builds a weighted similarity graph over the enriched games (their
game_characteristics trait vectors), then writes two kinds of kin edge per game
into the `kin` table:

  match       — the top-N most similar games overall (weighted cosine).
  hidden_gem  — the most similar games that are ALSO hidden gems: a high Steam
                positive rating with a low review count (the anti-popularity-
                gravity slot). Review signal comes from the steam_cache
                appreviews payloads captured by ingest_steam.py.

Similarity is weighted cosine over the sparse trait vectors, computed via an
inverted trait index (only games sharing a trait are compared) and assembled
into a NetworkX graph. Output is deterministic: ties break by game id, so a
re-run against the same data produces identical rows.

Runs against a partially-enriched DB — games without characteristics simply
have no edges (correctness gates here, not coverage; kin sharpen as enrichment
lands).

Requires networkx (see requirements.txt). No API key needed.

Usage
-----
    python scripts/compute_kin.py                  # defaults
    python scripts/compute_kin.py --top-n 12 --gems 3
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = REPO_ROOT / "data" / "gde.sqlite"

KIN_DDL = """
CREATE TABLE kin (
    game_id      INTEGER NOT NULL REFERENCES games(igdb_id),
    kin_game_id  INTEGER NOT NULL REFERENCES games(igdb_id),
    kind         TEXT    NOT NULL DEFAULT 'match',   -- 'match' | 'hidden_gem'
    rank         INTEGER NOT NULL DEFAULT 0,
    score        REAL    NOT NULL DEFAULT 0.0,
    blurb        TEXT,
    PRIMARY KEY (game_id, kin_game_id, kind)
);
"""


def load_vectors(conn: sqlite3.Connection) -> dict[int, dict[str, float]]:
    vectors: dict[int, dict[str, float]] = defaultdict(dict)
    for gid, cid, weight in conn.execute(
        "SELECT game_id, characteristic_id, weight FROM game_characteristics"
    ):
        vectors[gid][cid] = weight
    return dict(vectors)


def filter_common(vectors, max_df: int):
    """Drop traits present on more than max_df games from the similarity vectors.

    Ubiquitous traits (e.g. Single-Player) carry almost no discriminative signal
    and are the source of the O(df^2) pairwise blow-up. Removing them makes the
    graph both tractable and sharper. Returns (sim_vectors, dropped_trait_ids)."""
    df: dict[str, int] = defaultdict(int)
    for vec in vectors.values():
        for trait in vec:
            df[trait] += 1
    common = {t for t, c in df.items() if c > max_df}
    sim = {g: {t: w for t, w in vec.items() if t not in common} for g, vec in vectors.items()}
    sim = {g: v for g, v in sim.items() if v}  # drop games left with no distinctive trait
    return sim, common


def load_review_signal(conn: sqlite3.Connection) -> dict[int, tuple[float, int]]:
    """Map igdb game_id -> (positive_pct, review_count) from cached appreviews.

    Returns {} if the Steam crawl (which creates steam_cache) hasn't run yet."""
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='steam_cache'"
    ).fetchone():
        return {}
    appid_to_game: dict[int, int] = {}
    for gid, appid in conn.execute(
        "SELECT igdb_id, steam_appid FROM games WHERE steam_appid IS NOT NULL"
    ):
        appid_to_game[appid] = gid
    signal: dict[int, tuple[float, int]] = {}
    for appid, payload in conn.execute(
        "SELECT appid, payload FROM steam_cache WHERE endpoint = 'appreviews' AND ok = 1"
    ):
        gid = appid_to_game.get(appid)
        if gid is None or not payload:
            continue
        summary = (json.loads(payload) or {}).get("query_summary") or {}
        total = summary.get("total_reviews") or 0
        positive = summary.get("total_positive") or 0
        if total:
            signal[gid] = (positive / total, int(total))
    return signal


def trait_names(conn: sqlite3.Connection) -> dict[str, str]:
    return dict(conn.execute("SELECT id, name FROM characteristics"))


def top_game_ids(conn: sqlite3.Connection, n: int) -> set[int]:
    """The n most-reviewed enriched games (the publishable subset)."""
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


def similarity_edges(vectors: dict[int, dict[str, float]], min_sim: float):
    """Yield (a, b, cosine) for every pair sharing >=1 trait, above min_sim."""
    norms = {g: math.sqrt(sum(w * w for w in vec.values())) for g, vec in vectors.items()}
    inverted: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for gid, vec in vectors.items():
        for trait, weight in vec.items():
            inverted[trait].append((gid, weight))

    dot: dict[tuple[int, int], float] = defaultdict(float)
    for postings in inverted.values():
        for i in range(len(postings)):
            gi, wi = postings[i]
            for j in range(i + 1, len(postings)):
                gj, wj = postings[j]
                key = (gi, gj) if gi < gj else (gj, gi)
                dot[key] += wi * wj

    for (a, b), d in dot.items():
        denom = norms[a] * norms[b]
        if denom == 0:
            continue
        sim = d / denom
        if sim >= min_sim:
            yield a, b, sim


def build_graph(vectors, min_sim):
    import networkx as nx

    graph = nx.Graph()
    graph.add_nodes_from(vectors.keys())
    for a, b, sim in similarity_edges(vectors, min_sim):
        graph.add_edge(a, b, weight=sim)
    return graph


def shared_trait_names(vectors, names, a: int, b: int, limit: int = 4) -> list[str]:
    shared = set(vectors[a]) & set(vectors[b])
    ranked = sorted(shared, key=lambda t: -(vectors[a][t] + vectors[b][t]))
    return [names.get(t, t) for t in ranked[:limit]]


def match_blurb(shared: list[str]) -> str:
    if not shared:
        return "Kindred by overall profile."
    return "Shares " + ", ".join(shared) + "."


def gem_blurb(shared: list[str], pct: float, count: int) -> str:
    lead = "A lesser-known kindred"
    if shared:
        lead += " — " + ", ".join(shared)
    return f"{lead}. {round(pct * 100)}% positive across {count:,} Steam reviews."


def neighbours_by_similarity(graph, game: int) -> list[tuple[int, float]]:
    """Deterministic: highest similarity first, ties broken by kin game id."""
    edges = [(other, graph[game][other]["weight"]) for other in graph.neighbors(game)]
    return sorted(edges, key=lambda e: (-e[1], e[0]))


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute kindred-game edges.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--top-n", type=int, default=12, help="Closest matches per game.")
    parser.add_argument("--gems", type=int, default=3, help="Hidden-gem kin per game.")
    parser.add_argument("--min-sim", type=float, default=0.05, help="Minimum cosine to record an edge.")
    parser.add_argument("--max-df", type=int, default=1000,
                        help="Exclude traits on more than this many games from the "
                             "similarity graph (too common to discriminate).")
    parser.add_argument("--top", type=int, default=None,
                        help="Restrict to the top-N most-reviewed games so kin are "
                             "computed within the publishable subset (dense, resolvable).")
    parser.add_argument("--gem-pct", type=float, default=0.85, help="Min positive fraction for a hidden gem.")
    parser.add_argument("--gem-max-reviews", type=int, default=5000, help="Max review count for a hidden gem.")
    parser.add_argument("--gem-min-reviews", type=int, default=50, help="Min review count (filters noise).")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        sys.exit(f"{db_path} not found — run the ingest + enrich pipeline first.")

    conn = sqlite3.connect(db_path)
    try:
        vectors = load_vectors(conn)
        if not vectors:
            print("No game_characteristics yet — nothing to compute. Run enrich_batch.py first.")
        names = trait_names(conn)
        review = load_review_signal(conn)
        if args.top:
            keep = top_game_ids(conn, args.top)
            vectors = {g: v for g, v in vectors.items() if g in keep}
            print(f"Restricted to the top {len(vectors)} games by review count.")
        # Similarity runs on the discriminative sub-vectors (over-common traits
        # dropped); shared_trait_names below also uses these so blurbs highlight
        # distinctive overlap, not "Single-Player".
        sim_vectors, common = filter_common(vectors, args.max_df)
        graph = build_graph(sim_vectors, args.min_sim)
        print(f"{graph.number_of_nodes()} games, {graph.number_of_edges()} similarity "
              f"edges ({len(common)} over-common traits excluded).")

        def is_gem(g: int) -> bool:
            sig = review.get(g)
            return bool(sig and sig[0] >= args.gem_pct
                        and args.gem_min_reviews <= sig[1] <= args.gem_max_reviews)

        conn.execute("DROP TABLE IF EXISTS kin")
        conn.executescript(KIN_DDL)

        n_match = n_gem = 0
        for game in sorted(graph.nodes()):
            ranked = neighbours_by_similarity(graph, game)

            for rank, (other, sim) in enumerate(ranked[: args.top_n]):
                shared = shared_trait_names(sim_vectors, names, game, other)
                conn.execute(
                    "INSERT OR REPLACE INTO kin (game_id, kin_game_id, kind, rank, score, blurb) "
                    "VALUES (?, ?, 'match', ?, ?, ?)",
                    (game, other, rank, sim, match_blurb(shared)),
                )
                n_match += 1

            gems = [(o, s) for o, s in ranked if is_gem(o)][: args.gems]
            for rank, (other, sim) in enumerate(gems):
                shared = shared_trait_names(sim_vectors, names, game, other)
                pct, count = review[other]
                conn.execute(
                    "INSERT OR REPLACE INTO kin (game_id, kin_game_id, kind, rank, score, blurb) "
                    "VALUES (?, ?, 'hidden_gem', ?, ?, ?)",
                    (game, other, rank, sim, gem_blurb(shared, pct, count)),
                )
                n_gem += 1

        conn.commit()
        print(f"Wrote {n_match} match edges and {n_gem} hidden-gem edges.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
