import Head from "next/head";
import Link from "next/link";
import React from "react";
import type { SiteMeta } from "../lib/data";

export interface LayoutProps {
  /** Site metadata, passed from getStaticProps (never import the fs loader here). */
  site: SiteMeta;
  title: string;
  description: string;
  /** Absolute-from-root path for canonical URL, e.g. "/game/hades". */
  path?: string;
  children: React.ReactNode;
  head?: React.ReactNode;
}

export default function Layout({
  site,
  title,
  description,
  path = "/",
  children,
  head,
}: LayoutProps) {
  const canonical = site.url.replace(/\/$/, "") + path;
  return (
    <>
      <Head>
        <title>{title}</title>
        <meta name="description" content={description} />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <link rel="canonical" href={canonical} />
        <meta property="og:type" content="website" />
        <meta property="og:site_name" content={site.name} />
        <meta property="og:title" content={title} />
        <meta property="og:description" content={description} />
        <meta property="og:url" content={canonical} />
        <meta name="twitter:card" content="summary" />
        {head}
      </Head>
      <header className="site-header">
        <div className="container">
          <Link href="/" className="brand">
            {site.name}
          </Link>
          <span style={{ color: "var(--text-faint)", fontSize: "0.85rem" }}>
            discovery by quality, not genre
          </span>
        </div>
      </header>
      <main className="container">{children}</main>
      <footer className="site-footer">
        <div className="container">
          {site.name} · a prototype · sample data
        </div>
      </footer>
    </>
  );
}
