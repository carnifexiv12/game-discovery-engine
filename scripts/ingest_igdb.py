#!/usr/bin/env python3
"""Ingest game records from the IGDB API into data/gde.sqlite.

Authenticates against IGDB (Twitch OAuth: IGDB_CLIENT_ID / IGDB_CLIENT_SECRET),
pulls games matching a target list or query, and upserts them into the `games`
table (title, slug, year, developer, platforms, summary, cover_image_id,
screenshot_ids). Cover and screenshot image ids are stored raw so the export
step can build CDN URLs.

Stub: not yet implemented. See scripts/init_db.py for the target schema.
"""

raise NotImplementedError("ingest_igdb.py is a stub")
