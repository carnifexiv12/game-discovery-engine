/**
 * Claim-game flow. Rendered by the claim strip on /game/[slug].
 *
 * The site is a static export, so this is a pure client component: it renders a
 * Cloudflare Turnstile widget (explicit mode), then submits as JSON via fetch to
 * the Pages Function at /api/claim. No <form> POST semantics — JS submit only.
 *
 * The Turnstile site key comes from NEXT_PUBLIC_TURNSTILE_SITE_KEY at build
 * time; it falls back to Turnstile's always-pass test key so local builds and
 * `wrangler pages dev` work without the real key wired in.
 */

import React, { useCallback, useEffect, useRef, useState } from "react";
import type { Game } from "../lib/data";

const SITE_KEY =
  process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY || "1x00000000000000000000AA";
const TURNSTILE_SRC =
  "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";

type Status = "idle" | "submitting" | "success" | "error";

// Minimal shape of the global injected by Turnstile's api.js.
interface TurnstileApi {
  render: (
    el: HTMLElement,
    opts: {
      sitekey: string;
      callback: (token: string) => void;
      "expired-callback"?: () => void;
      "error-callback"?: () => void;
    }
  ) => string;
  reset: (widgetId?: string) => void;
}

function useTurnstileScript(): boolean {
  const [ready, setReady] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined") return;
    if ((window as unknown as { turnstile?: TurnstileApi }).turnstile) {
      setReady(true);
      return;
    }
    const existing = document.querySelector<HTMLScriptElement>(
      `script[src="${TURNSTILE_SRC}"]`
    );
    const onload = () => setReady(true);
    if (existing) {
      existing.addEventListener("load", onload);
      return () => existing.removeEventListener("load", onload);
    }
    const s = document.createElement("script");
    s.src = TURNSTILE_SRC;
    s.async = true;
    s.defer = true;
    s.addEventListener("load", onload);
    document.head.appendChild(s);
    return () => s.removeEventListener("load", onload);
  }, []);
  return ready;
}

export default function ClaimForm({ game }: { game: Game }) {
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<Status>("idle");
  const [message, setMessage] = useState<string>("");
  const [token, setToken] = useState<string>("");
  const widgetRef = useRef<HTMLDivElement>(null);
  const widgetIdRef = useRef<string | null>(null);
  const scriptReady = useTurnstileScript();

  // Render the Turnstile widget once the form is open and the script is ready.
  useEffect(() => {
    if (!open || !scriptReady || !widgetRef.current) return;
    const turnstile = (window as unknown as { turnstile?: TurnstileApi })
      .turnstile;
    if (!turnstile || widgetIdRef.current) return;
    widgetIdRef.current = turnstile.render(widgetRef.current, {
      sitekey: SITE_KEY,
      callback: (t) => setToken(t),
      "expired-callback": () => setToken(""),
      "error-callback": () => setToken(""),
    });
  }, [open, scriptReady]);

  const resetWidget = useCallback(() => {
    const turnstile = (window as unknown as { turnstile?: TurnstileApi })
      .turnstile;
    if (turnstile && widgetIdRef.current) {
      turnstile.reset(widgetIdRef.current);
    }
    setToken("");
  }, []);

  const onSubmit = useCallback(
    async (e: React.FormEvent<HTMLFormElement>) => {
      e.preventDefault();
      if (status === "submitting") return;
      const form = e.currentTarget;
      const data = new FormData(form);
      const payload = {
        gameSlug: game.slug,
        name: String(data.get("name") || "").trim(),
        email: String(data.get("email") || "").trim(),
        proofUrl: String(data.get("proofUrl") || "").trim(),
        corrections: String(data.get("corrections") || "").trim(),
        turnstileToken: token,
      };

      if (!payload.name || !payload.email || !payload.proofUrl) {
        setStatus("error");
        setMessage("Please fill in your name, email, and a proof-of-ownership link.");
        return;
      }
      if (!token) {
        setStatus("error");
        setMessage("Please complete the verification challenge.");
        return;
      }

      setStatus("submitting");
      setMessage("");
      try {
        const res = await fetch("/api/claim", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (res.ok) {
          setStatus("success");
          setMessage("Thanks — your claim was received. We'll be in touch.");
          form.reset();
          resetWidget();
        } else {
          const body = (await res.json().catch(() => ({}))) as {
            error?: string;
          };
          setStatus("error");
          setMessage(body.error || `Submission failed (${res.status}).`);
          resetWidget();
        }
      } catch {
        setStatus("error");
        setMessage("Network error — please try again.");
        resetWidget();
      }
    },
    [game.slug, token, status, resetWidget]
  );

  if (status === "success") {
    return (
      <div className="claim-strip" role="status">
        <div className="claim-text">
          <strong>Claim received for {game.title}</strong>
          <span>{message}</span>
        </div>
      </div>
    );
  }

  return (
    <div className="claim-strip claim-strip--form">
      <div className="claim-text">
        <strong>Work on {game.title}?</strong>
        <span>Claim this page to correct details or add missing context.</span>
      </div>

      {!open ? (
        <button className="btn" onClick={() => setOpen(true)}>
          Claim this game
        </button>
      ) : (
        <form className="claim-form" onSubmit={onSubmit} noValidate>
          <label>
            Name / studio
            <input name="name" type="text" autoComplete="organization" required />
          </label>
          <label>
            Contact email
            <input name="email" type="email" autoComplete="email" required />
          </label>
          <label>
            Proof of ownership (store page or official site)
            <input name="proofUrl" type="url" placeholder="https://…" required />
          </label>
          <label>
            Corrections or context (optional)
            <textarea name="corrections" rows={4} />
          </label>

          <div className="turnstile" ref={widgetRef} aria-label="Verification" />

          {message && (
            <p
              className={status === "error" ? "form-error" : "form-note"}
              role="alert"
            >
              {message}
            </p>
          )}

          <div className="claim-actions">
            <button
              type="submit"
              className="btn primary"
              disabled={status === "submitting"}
            >
              {status === "submitting" ? "Submitting…" : "Submit claim"}
            </button>
            <button
              type="button"
              className="btn"
              onClick={() => setOpen(false)}
              disabled={status === "submitting"}
            >
              Cancel
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
