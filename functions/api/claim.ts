/**
 * POST /api/claim — Cloudflare Pages Function.
 *
 * Verifies a Cloudflare Turnstile token server-side, then records the claim in
 * the CLAIMS_KV namespace. Called by components/ClaimForm.tsx.
 *
 * Local testing (Turnstile test keys, always-pass):
 *   1. npx next build            # produces ./out
 *   2. echo 'TURNSTILE_SECRET_KEY=1x0000000000000000000000000000000AA' > .dev.vars
 *   3. npx wrangler pages dev out --kv CLAIMS_KV
 *   The claim form's default site key (1x00000000000000000000AA) pairs with the
 *   test secret above, so submissions pass verification and land in the local KV.
 */

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const MAX_BYTES = 10 * 1024; // 10KB

interface ClaimBody {
  gameSlug?: string;
  name?: string;
  email?: string;
  proofUrl?: string;
  corrections?: string;
  turnstileToken?: string;
}

function json(
  body: Record<string, unknown>,
  status: number,
  extraHeaders: Record<string, string> = {}
): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...extraHeaders },
  });
}

export const onRequest: PagesFunction<Env> = async (context) => {
  const { request, env } = context;

  if (request.method !== "POST") {
    return json({ error: "Method not allowed" }, 405, { Allow: "POST" });
  }

  const raw = await request.text();
  if (raw.length > MAX_BYTES) {
    return json({ error: "Payload too large" }, 400);
  }

  let body: ClaimBody;
  try {
    body = JSON.parse(raw) as ClaimBody;
  } catch {
    return json({ error: "Invalid JSON" }, 400);
  }

  const gameSlug = (body.gameSlug || "").trim();
  const name = (body.name || "").trim();
  const email = (body.email || "").trim();
  const proofUrl = (body.proofUrl || "").trim();
  const corrections = (body.corrections || "").trim();
  const turnstileToken = body.turnstileToken || "";

  if (!gameSlug || !name || !email || !proofUrl || !turnstileToken) {
    return json({ error: "Missing required fields" }, 400);
  }
  if (!EMAIL_RE.test(email)) {
    return json({ error: "Invalid email address" }, 400);
  }

  const ip = request.headers.get("CF-Connecting-IP") || "";
  const userAgent = request.headers.get("User-Agent") || "";

  // Verify the Turnstile token server-side.
  const verifyRes = await fetch(
    "https://challenges.cloudflare.com/turnstile/v0/siteverify",
    {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        secret: env.TURNSTILE_SECRET_KEY,
        response: turnstileToken,
        remoteip: ip,
      }),
    }
  );
  const verify = (await verifyRes.json()) as { success?: boolean };
  if (!verify.success) {
    return json({ error: "Verification failed" }, 400);
  }

  // Record the claim.
  const timestamp = Date.now();
  const key = `claim:${gameSlug}:${timestamp}`;
  const record = {
    gameSlug,
    name,
    email,
    proofUrl,
    corrections,
    ip,
    userAgent,
    submittedAt: new Date(timestamp).toISOString(),
  };
  await env.CLAIMS_KV.put(key, JSON.stringify(record));

  return json({ ok: true }, 200);
};
