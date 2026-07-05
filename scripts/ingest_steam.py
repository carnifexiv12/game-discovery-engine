#!/usr/bin/env python3
"""Enrich game records with Steam data and store text into data/gde.sqlite.

For games that have a steam_appid, pulls store metadata and review text via the
Steam storefront/reviews endpoints. Writes review and description text into the
`corpus` table (source = "steam_reviews" / "steam_description") for later
enrichment, and may backfill steam_appid on `games` by matching titles.

Stub: not yet implemented. See scripts/init_db.py for the target schema.
"""

raise NotImplementedError("ingest_steam.py is a stub")
