/**
 * Ambient types for the Cloudflare Pages Functions in this directory.
 *
 * Kept self-contained (no @cloudflare/workers-types dependency) — only the
 * surface the Functions actually use. This directory is excluded from the
 * Next.js tsconfig; Cloudflare compiles it at deploy time.
 */

/** Minimal Workers KV binding surface used by functions/api/claim.ts. */
interface KVNamespace {
  get(key: string): Promise<string | null>;
  put(
    key: string,
    value: string,
    options?: { expirationTtl?: number; metadata?: Record<string, unknown> }
  ): Promise<void>;
  delete(key: string): Promise<void>;
  list(options?: { prefix?: string; limit?: number; cursor?: string }): Promise<{
    keys: { name: string }[];
    list_complete: boolean;
    cursor?: string;
  }>;
}

/** Bindings configured on the Pages project (Settings → Variables/Bindings). */
interface Env {
  /** Turnstile secret (encrypted variable), read at runtime. */
  TURNSTILE_SECRET_KEY: string;
  /** KV binding named CLAIMS_KV, mapped to the GDE_CLAIMS namespace. */
  CLAIMS_KV: KVNamespace;
}

/** Cloudflare Pages Function handler signature (subset). */
type PagesFunction<E = unknown> = (context: {
  request: Request;
  env: E;
  params: Record<string, string | string[]>;
  waitUntil: (promise: Promise<unknown>) => void;
  next: (input?: Request | string, init?: RequestInit) => Promise<Response>;
  data: Record<string, unknown>;
}) => Response | Promise<Response>;
