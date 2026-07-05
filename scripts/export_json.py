#!/usr/bin/env python3
"""Export the SQLite database to static JSON for the Next.js build.

Reads data/gde.sqlite and writes data/export/games.json and
data/export/kin.json in the shape consumed by lib/data.ts at build time. Each
game record is denormalized to include its characteristic profile, prose
sections, and store links; kin.json maps each game slug to its ranked kindred
games with scores, reasoning, and trait pills.

Stub: not yet implemented. The sample files already in data/export/ define the
target shape and let the site build before real data exists.
"""

raise NotImplementedError("export_json.py is a stub")
