#!/usr/bin/env python3
"""Assign weighted characteristics to games from their corpus text.

Reads each game's `corpus` rows plus the controlled vocabulary and produces
`game_characteristics` rows: for every trait that applies, a weight (0..1) and a
one-line rationale. This is the enrichment pass that turns raw text into the
structured trait profiles the site renders. Intended to run in batches (the
"calibration hundred" first) so output can be reviewed before scaling.

Stub: not yet implemented. See scripts/init_db.py for the target schema.
"""

raise NotImplementedError("enrich_batch.py is a stub")
