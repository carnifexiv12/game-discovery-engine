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
  vocabulary.json     Controlled characteristic vocabulary (4 groups, stub)
  gde.sqlite          Committed SQLite db (built by scripts/init_db.py)
  export/             Static JSON consumed at build time (SAMPLE data)
    games.json
    kin.json
scripts/
  init_db.py          Builds gde.sqlite (implemented)
  gen_seo.mjs         Generates sitemap.xml + robots.txt (implemented)
  ingest_igdb.py      Pull games from IGDB              (stub)
  ingest_steam.py     Pull Steam metadata + reviews     (stub)
  enrich_batch.py     Assign weighted characteristics   (stub)
  compute_kin.py      Compute kindred-game edges        (stub)
  export_json.py      SQLite -> data/export/*.json      (stub)
lib/data.ts           Typed build-time loader for the export
components/           Layout + presentational UI
pages/
  index.js            Home: search stub + sample game grid
  game/[slug].js      Game page: prose sections, trait bars, kin, claim strip
  games-like/[slug].js Results: source traits + ranked kin
```

## Data pipeline (intended)

```
ingest_igdb / ingest_steam  ->  gde.sqlite (games, corpus)
enrich_batch                ->  game_characteristics
compute_kin                 ->  kin
export_json                 ->  data/export/*.json  ->  next build
```

Rebuild the database schema at any time:

```bash
python scripts/init_db.py --seed-vocab
```

## Next steps

- Vocabulary design (expand `data/vocabulary.json` beyond the stub).
- Implement `ingest_igdb.py` against real IGDB credentials.
- Run the calibration batch through `enrich_batch.py`.
- Wire the Turnstile-gated claim form + `functions/api/claim.ts`.
