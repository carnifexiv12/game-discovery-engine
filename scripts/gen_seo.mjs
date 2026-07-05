/**
 * Generate public/sitemap.xml and public/robots.txt from the static export.
 *
 * Runs as the `prebuild` / `predev` npm hook. The output is also committed so a
 * bare `npx next build` (which skips npm lifecycle scripts) still ships a valid
 * sitemap. Regenerate any time the export changes with `node scripts/gen_seo.mjs`.
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const gamesPath = path.join(root, "data", "export", "games.json");
const publicDir = path.join(root, "public");

function main() {
  const { site, games } = JSON.parse(fs.readFileSync(gamesPath, "utf-8"));
  const base = site.url.replace(/\/$/, "");

  const urls = [
    { loc: `${base}/`, priority: "1.0" },
    ...games.flatMap((g) => [
      { loc: `${base}/game/${g.slug}`, priority: "0.8" },
      { loc: `${base}/games-like/${g.slug}`, priority: "0.6" },
    ]),
  ];

  const sitemap =
    `<?xml version="1.0" encoding="UTF-8"?>\n` +
    `<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n` +
    urls
      .map(
        (u) =>
          `  <url><loc>${u.loc}</loc><changefreq>weekly</changefreq><priority>${u.priority}</priority></url>`
      )
      .join("\n") +
    `\n</urlset>\n`;

  const robots =
    `User-agent: *\n` +
    `Allow: /\n\n` +
    `Sitemap: ${base}/sitemap.xml\n`;

  fs.mkdirSync(publicDir, { recursive: true });
  fs.writeFileSync(path.join(publicDir, "sitemap.xml"), sitemap);
  fs.writeFileSync(path.join(publicDir, "robots.txt"), robots);
  console.log(`Wrote sitemap.xml (${urls.length} urls) and robots.txt`);
}

main();
