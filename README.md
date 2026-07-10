# Game Discovery Engine

Find your next game by the qualities that actually matter — tone, mechanics,
aesthetic, and structure — not by genre tags.

Next.js 14 (Pages Router), static export (`output: 'export'`). Deploys on
Cloudflare Pages; production builds from the `phantom` branch, any other branch
gets a preview deploy.

## Status

Scaffold with **sample data**. All three routes render from a hand-authored
export in `data/export/`. The real data pipeline (scripts below) is stubbed.

## Getting started

```bash
npm install
npm run dev        # http://localhost:3000
npm run build      # static export to ./out
```

`prebuild`/`predev` run `scripts/gen_seo.mjs` to regenerate `public/sitemap.xml`
and `public/robots.txt` from the export. (Both files are committed so a bare
`npx next build` still ships them.)

## Layout

```
data/
  vocabulary.json     Controlled characteristic vocabulary (5 groups, 323 traits)
  gde.sqlite          Local SQLite db — GITIGNORED build artifact (init_db.py)
  export/             Static JSON consumed at build time — COMMITTED pipeline output
    games.json
    kin.json
scripts/
  init_db.py          Builds the gde.sqlite schema + seeds vocab (implemented)
  gen_seo.mjs         Generates sitemap.xml + robots.txt (implemented)
  ingest_igdb.py      Pull games from IGDB              (implemented)
  ingest_steam.py     Pull Steam metadata + reviews     (implemented, resumable)
  enrich_batch.py     Assign weighted characteristics   (implemented, Batch API)
  compute_kin.py      Compute kindred-game edges        (stub)
  export_json.py      SQLite -> data/export/*.json      (stub)
lib/data.ts           Typed build-time loader for the export
components/           Layout + presentational UI
pages/
  index.js            Home: search stub + sample game grid
  game/[slug].js      Game page: prose sections, trait bars, kin, claim strip
  games-like/[slug].js Results: source traits + ranked kin
```

## Data pipeline

```
ingest_igdb / ingest_steam  ->  gde.sqlite (games, corpus)   [LOCAL, gitignored]
enrich_batch                ->  game_characteristics          [LOCAL]
compute_kin                 ->  kin                            [LOCAL]
export_json                 ->  data/export/*.json  ->  next build   [COMMITTED]
```

**The clean line: DB local, exports committed, Pages builds from exports.**
`gde.sqlite` is a deterministic build artifact — regenerable from the scripts
plus IGDB/Steam credentials — so it is **gitignored, never committed**. The only
pipeline product that enters git is the export (`data/export/*.json`), which the
templates read and Cloudflare Pages builds from. If you want a safety copy of the
DB, that is a file in your backups, not a commit.

Credentials for the pipeline live in a gitignored `.env` at the repo root
(`IGDB_CLIENT_ID`, `IGDB_CLIENT_SECRET`); the ingest scripts load it automatically.

Build the schema + seed the vocabulary, then ingest:

```bash
python scripts/init_db.py --seed-vocab
python scripts/ingest_igdb.py --count-only          # preview corpus size
python scripts/ingest_igdb.py --min-rating-count 8  # ~10k main games
```

## Next steps

- ~~Vocabulary design~~ — done (5 groups, 323 traits).
- ~~Implement `ingest_igdb.py`~~ — done.
- ~~Wire the Turnstile-gated claim form~~ — done (`functions/api/claim.ts`).
- ~~Implement `ingest_steam.py`~~ — done (appdetails / appreviews / SteamSpy, resumable).
- ~~Implement `enrich_batch.py`~~ — done (Claude Batch API, structured outputs).
- Run the full Steam crawl, then a calibration enrichment batch (`--limit 100`), review, then the full run.
- Implement `compute_kin.py` + `export_json.py`, then regenerate `data/export/`.
