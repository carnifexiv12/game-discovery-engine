#!/usr/bin/env python3
"""Assign weighted vocabulary traits to each game via the Claude Batch API.

For every game with corpus text (IGDB summary + genres/themes, and Steam
description/tags/reviews once ingest_steam.py has run), this asks Claude to
classify the game against the 323-trait controlled vocabulary and emit a SPARSE
set of {trait id, weight 0..1, one-line rationale}, following the weight rubric
baked into data/vocabulary.json. Results land in the game_characteristics table.

Why Batch: one request per game, processed asynchronously at half price. Results
come back in any order and are keyed by custom_id (the igdb_id).

Structured output: the response is constrained to a JSON schema whose trait `id`
is an enum of all 323 vocabulary ids — so the model cannot invent a trait, and
every row is a valid foreign key. Weights are clamped to [0,1] and rows below a
threshold are dropped (sparse: ~8-20 traits per game).

Resumability: submitted batch ids are recorded in an enrich_batch table; a
re-run first polls/collects any unfinished batches, then enqueues only games
that still lack game_characteristics rows. So an interrupted run resumes for
free, and --collect-only just drains in-flight batches.

Auth: needs an Anthropic API key — ANTHROPIC_API_KEY in the environment or the
gitignored .env, or an `ant auth login` profile. Requires the `anthropic` SDK
(pip install anthropic; see requirements.txt).

Usage
-----
    python scripts/enrich_batch.py --limit 100      # calibration batch first
    python scripts/enrich_batch.py                  # everything not yet enriched
    python scripts/enrich_batch.py --collect-only    # just drain in-flight batches
    python scripts/enrich_batch.py --force           # re-enrich even if rows exist
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = REPO_ROOT / "data" / "gde.sqlite"
VOCAB_PATH = REPO_ROOT / "data" / "vocabulary.json"
ENV_PATH = REPO_ROOT / ".env"

MODEL = "claude-haiku-4-5"  # right tier for rubric classification; --model to upgrade
MAX_TOKENS = 8192
WEIGHT_THRESHOLD = 0.25      # below this, omit (sparser, sharper profiles)
BATCH_CHUNK = 1000           # games per batch; sequential chunks keep the cache warm
POLL_SECONDS = 30

# Haiku 4.5 batch $/MTok: base in 1.00 / out 5.00; batch = 50%; cache-write(1h) 2x
# base, cache-read 0.1x base. Used only for a spend estimate in the run summary.
PRICE = {"input": 0.50, "cache_write": 1.00, "cache_read": 0.05, "output": 2.50}

GROUP_TITLES = {
    "tone": "TONE (emotional register)",
    "theme": "THEME (subject / setting)",
    "mechanics": "MECHANICS (interactive systems)",
    "aesthetic": "AESTHETIC (audiovisual style)",
    "structure": "STRUCTURE (how play is organized)",
}


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


# --------------------------------------------------------------------------- #
# Vocabulary -> prompt + schema
# --------------------------------------------------------------------------- #
def load_vocab() -> dict:
    return json.loads(VOCAB_PATH.read_text(encoding="utf-8"))


def all_trait_ids(vocab: dict) -> list[str]:
    ids: list[str] = []
    for group in vocab["groups"].values():
        ids.extend(t["id"] for t in group["traits"])
    return ids


def render_vocabulary(vocab: dict) -> str:
    """One compact line per trait, grouped by family — the classification menu."""
    lines: list[str] = []
    for key, group in vocab["groups"].items():
        lines.append(f"\n## {GROUP_TITLES.get(key, key.upper())}")
        for t in group["traits"]:
            line = f"- {t['id']} — {t['name']} [{t['scale']}]: {t['definition']}"
            if t.get("distinct_from"):
                line += f" (not to be confused with: {', '.join(t['distinct_from'])})"
            lines.append(line)
    return "\n".join(lines)


def build_schema(ids: list[str]) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "traits": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "id": {"type": "string", "enum": ids},
                        "weight": {"type": "number"},
                        "rationale": {"type": "string"},
                    },
                    "required": ["id", "weight", "rationale"],
                },
            }
        },
        "required": ["traits"],
    }


def system_prompt(vocab: dict) -> str:
    rubric = vocab.get("weight_rubric", {}).get("bands", {})
    bands = "\n".join(f"  {band}: {meaning}" for band, meaning in rubric.items())
    return (
        "You classify a video game against a fixed vocabulary of characteristics.\n"
        "For the game described by the user, return ONLY the traits that genuinely "
        "apply — a sparse set, typically 8 to 20, never all of them. For each, give "
        "a weight in [0,1] measuring how CENTRAL the trait is to the experience "
        "(not how good the game is), and a one-sentence rationale grounded in the "
        "provided text.\n\n"
        "Weight bands:\n" + bands + "\n\n"
        "Rules: emit a trait only if its weight is at least 0.25 (omit anything "
        "fainter). Prefer a tight, high-signal set over a long one. Use the exact "
        "trait id from the menu. 'graded' traits are "
        "intensity spectrums; 'binary' traits are present/absent — weight a binary "
        "trait by how central its system is. Respect the 'not to be confused with' "
        "notes. Do not invent traits.\n\n"
        "THE VOCABULARY (id — name [scale]: definition):\n" + render_vocabulary(vocab)
    )


# --------------------------------------------------------------------------- #
# Game -> user content
# --------------------------------------------------------------------------- #
def corpus_for(conn: sqlite3.Connection, game_id: int) -> dict[str, str]:
    rows = conn.execute(
        "SELECT source, text FROM corpus WHERE game_id = ?", (game_id,)
    ).fetchall()
    return {source: text for source, text in rows if text}


def user_content(game: sqlite3.Row, corpus: dict[str, str]) -> str:
    parts = [
        f"TITLE: {game['title']}",
        f"YEAR: {game['year'] or 'unknown'}",
        f"DEVELOPER: {game['developer'] or 'unknown'}",
        f"PLATFORMS: {', '.join(json.loads(game['platforms'] or '[]')) or 'unknown'}",
    ]
    labels = [
        ("igdb_summary", "SUMMARY"),
        ("igdb_tags", "IGDB GENRES/THEMES"),
        ("steam_description", "STEAM DESCRIPTION"),
        ("steam_tags", "STEAM USER TAGS (crowd hints — context only, not the answer)"),
        ("steam_reviews", "STEAM REVIEWS"),
    ]
    for source, label in labels:
        if corpus.get(source):
            parts.append(f"{label}: {corpus[source]}")
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# DB helpers
# --------------------------------------------------------------------------- #
BATCH_DDL = """
CREATE TABLE IF NOT EXISTS enrich_batch (
    batch_id    TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    collected   INTEGER NOT NULL DEFAULT 0
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(BATCH_DDL)
    conn.commit()


def games_to_enrich(conn: sqlite3.Connection, *, force: bool, limit: int | None):
    where = "WHERE EXISTS (SELECT 1 FROM corpus c WHERE c.game_id = g.igdb_id)"
    if not force:
        where += (
            " AND NOT EXISTS (SELECT 1 FROM game_characteristics gc "
            "WHERE gc.game_id = g.igdb_id)"
        )
    # Enrich the most-reviewed games first: recognizable and corpus-rich, so a
    # --limit calibration batch lands on titles you can eyeball. Order-neutral
    # for the full run. Falls back to id order before the Steam crawl exists.
    has_steam = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='steam_cache'"
    ).fetchone()
    if has_steam:
        join = (
            "LEFT JOIN (SELECT appid, "
            "json_extract(payload, '$.query_summary.total_reviews') AS rc "
            "FROM steam_cache WHERE endpoint = 'appreviews' AND ok = 1) r "
            "ON r.appid = g.steam_appid "
        )
        sql = f"SELECT g.* FROM games g {join}{where} ORDER BY COALESCE(r.rc, 0) DESC, g.igdb_id"
    else:
        sql = f"SELECT g.* FROM games g {where} ORDER BY g.igdb_id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql).fetchall()


def open_batches(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT batch_id FROM enrich_batch WHERE collected = 0"
    ).fetchall()]


def is_credit_error(obj) -> bool:
    """True if an exception or a batch result error looks like a billing/credit halt."""
    if obj is None:
        return False
    parts = [str(obj)]
    for attr in ("message", "type"):
        value = getattr(obj, attr, None)
        if value:
            parts.append(str(value))
    text = " ".join(parts).lower()
    return any(k in text for k in ("credit", "billing", "insufficient", "quota", "payment"))


def estimate_cost(usage: dict) -> float:
    return (
        usage["input"] * PRICE["input"]
        + usage["cache_write"] * PRICE["cache_write"]
        + usage["cache_read"] * PRICE["cache_read"]
        + usage["output"] * PRICE["output"]
    ) / 1e6


def write_results(conn: sqlite3.Connection, valid_ids: set[str], results):
    """Returns (games, chars, usage, credit_hit). Writes succeeded games; a
    credit-related error on any errored result flips credit_hit."""
    games = chars = 0
    usage = {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0}
    credit_hit = False
    for entry in results:
        if entry.result.type != "succeeded":
            if is_credit_error(getattr(entry.result, "error", None)):
                credit_hit = True
            continue
        u = entry.result.message.usage
        usage["input"] += u.input_tokens
        usage["output"] += u.output_tokens
        usage["cache_write"] += getattr(u, "cache_creation_input_tokens", 0) or 0
        usage["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0
        try:
            game_id = int(entry.custom_id)
        except ValueError:
            continue
        text = "".join(
            b.text for b in entry.result.message.content if getattr(b, "type", "") == "text"
        )
        try:
            traits = json.loads(text).get("traits", [])
        except json.JSONDecodeError:
            continue
        conn.execute("DELETE FROM game_characteristics WHERE game_id = ?", (game_id,))
        rows = []
        for t in traits:
            tid = t.get("id")
            if tid not in valid_ids:
                continue
            weight = max(0.0, min(1.0, float(t.get("weight", 0))))
            if weight < WEIGHT_THRESHOLD:
                continue
            rows.append((game_id, tid, weight, (t.get("rationale") or "").strip()))
        conn.executemany(
            "INSERT OR REPLACE INTO game_characteristics "
            "(game_id, characteristic_id, weight, rationale) VALUES (?, ?, ?, ?)",
            rows,
        )
        games += 1
        chars += len(rows)
    conn.commit()
    return games, chars, usage, credit_hit


def collect_batch(conn, client, valid_ids: set[str], batch_id: str):
    """Poll a batch to completion, write its results. Returns (usage, credit_hit)."""
    while True:
        batch = client.messages.batches.retrieve(batch_id)
        if batch.processing_status == "ended":
            break
        counts = batch.request_counts
        print(f"  [{batch_id}] {batch.processing_status} "
              f"(done {counts.succeeded + counts.errored}/{counts.processing + counts.succeeded + counts.errored})")
        time.sleep(POLL_SECONDS)
    games, chars, usage, credit_hit = write_results(
        conn, valid_ids, client.messages.batches.results(batch_id))
    conn.execute("UPDATE enrich_batch SET collected = 1 WHERE batch_id = ?", (batch_id,))
    conn.commit()
    print(f"  [{batch_id}] collected: {games} games, {chars} trait assignments"
          f"{' (credit error seen)' if credit_hit else ''}.")
    return usage, credit_hit


def submit_batch(conn, client, games, system, schema, model) -> str:
    from datetime import datetime, timezone

    requests = [
        {
            "custom_id": str(g["igdb_id"]),
            "params": {
                "model": model,
                "max_tokens": MAX_TOKENS,
                "system": [
                    # 1h TTL keeps the ~14k-token vocabulary prefix cached across
                    # the sequential chunks (default 5min would expire between them).
                    {"type": "text", "text": system,
                     "cache_control": {"type": "ephemeral", "ttl": "1h"}}
                ],
                "messages": [{"role": "user", "content": user_content(g, corpus_for(conn, g["igdb_id"]))}],
                # Structured output only — portable across tiers. No thinking/
                # effort: they 400 on Haiku 4.5 (the default), and rubric
                # classification against a fixed menu doesn't need them.
                "output_config": {"format": {"type": "json_schema", "schema": schema}},
            },
        }
        for g in games
    ]
    batch = client.messages.batches.create(requests=requests)
    conn.execute(
        "INSERT INTO enrich_batch (batch_id, created_at, collected) VALUES (?, ?, 0)",
        (batch.id, datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )
    conn.commit()
    print(f"Submitted batch {batch.id} with {len(requests)} games.")
    return batch.id


def chunked(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def main() -> None:
    parser = argparse.ArgumentParser(description="Enrich games with vocabulary traits (Claude Batch API).")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--limit", type=int, default=None, help="Cap games this run (calibration batch).")
    parser.add_argument("--force", action="store_true", help="Re-enrich games that already have rows.")
    parser.add_argument("--collect-only", action="store_true", help="Only drain in-flight batches; submit nothing.")
    parser.add_argument(
        "--model", default=MODEL,
        help=f"Claude model (default {MODEL}, the right tier for rubric "
             "classification). Upgrade with claude-sonnet-5 or claude-opus-4-8 "
             "if calibration shows the classification needs it.",
    )
    parser.add_argument("--chunk-size", type=int, default=BATCH_CHUNK,
                        help=f"Games per batch (default {BATCH_CHUNK}); sequential chunks keep the cache warm.")
    args = parser.parse_args()

    load_env(ENV_PATH)
    try:
        from anthropic import Anthropic
    except ImportError:
        sys.exit("The `anthropic` package is required: pip install anthropic (see requirements.txt).")

    db_path = Path(args.db)
    if not db_path.exists():
        sys.exit(f"{db_path} not found — run init_db.py + ingest_igdb.py first.")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        from anthropic import APIError

        ensure_schema(conn)
        vocab = load_vocab()
        valid_ids = set(all_trait_ids(vocab))
        # Extra SDK retries so a transient 5xx (e.g. a 502 gateway blip) doesn't
        # abort a long run; a persistent failure still halts gracefully below.
        client = Anthropic(max_retries=8)

        # Harmonize any earlier-threshold rows (e.g. a 0.15 calibration) to the
        # current threshold — free, keeps every game on the same emit bar.
        conn.execute("DELETE FROM game_characteristics WHERE weight < ?", (WEIGHT_THRESHOLD,))
        conn.commit()

        total_usage = {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0}
        halted = False
        halt_reason = None

        def add(u):
            for k in total_usage:
                total_usage[k] += u[k]

        # 1. Drain any batches from a prior interrupted run (resume, no re-billing).
        pending = open_batches(conn)
        if pending:
            print(f"Collecting {len(pending)} in-flight batch(es)…")
            for batch_id in pending:
                u, hit = collect_batch(conn, client, valid_ids, batch_id)
                add(u)
                if hit:
                    halted, halt_reason = True, "insufficient credit"

        if not args.collect_only and not halted:
            # 2. Enqueue games that still need enrichment.
            games = games_to_enrich(conn, force=args.force, limit=args.limit)
            if not games:
                print("Nothing to enrich.")
            else:
                print(f"Enriching {len(games)} games with {args.model}, "
                      f"{args.chunk_size}/chunk (sequential, batch)…")
                system = system_prompt(vocab)
                schema = build_schema(sorted(valid_ids))
                try:
                    for chunk in chunked(games, args.chunk_size):
                        batch_id = submit_batch(conn, client, chunk, system, schema, args.model)
                        u, hit = collect_batch(conn, client, valid_ids, batch_id)
                        add(u)
                        if hit:
                            halted, halt_reason = True, "insufficient credit"
                            break
                except APIError as exc:
                    # Any API error (credit, or a transient 5xx that survived the
                    # retries) is a graceful, resumable halt — the in-flight chunk
                    # was already collected above; stop submitting more.
                    halted = True
                    halt_reason = ("insufficient credit" if is_credit_error(exc)
                                   else f"an API error ({type(exc).__name__})")
                    print(f"Halting on {halt_reason}: {str(exc)[:160]}", file=sys.stderr)

        # 3. Summary — enriched vs remaining, dollars consumed this run.
        enrichable = conn.execute(
            "SELECT count(*) FROM games g WHERE EXISTS "
            "(SELECT 1 FROM corpus c WHERE c.game_id = g.igdb_id)"
        ).fetchone()[0]
        enriched = conn.execute(
            "SELECT count(DISTINCT game_id) FROM game_characteristics"
        ).fetchone()[0]
        chars = conn.execute("SELECT count(*) FROM game_characteristics").fetchone()[0]
        print(f"\nEnriched {enriched}/{enrichable} games "
              f"({enrichable - enriched} remaining), {chars} trait assignments; "
              f"~${estimate_cost(total_usage):.2f} consumed this run.")
        if halted:
            print(f"HALTED on {halt_reason}. Re-run the same command to resume "
                  "from the enrich_batch table — already-enriched games are "
                  "skipped (zero re-billing). Top up credit first if that was the cause.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
