"use client";
// Browser-side API calls go straight to the public API, so rate limits apply to the real client address.
// Admin keys (or the short-lived key a password sign-in returns) are kept in sessionStorage (this tab), or in
// localStorage when "keep me signed in" was chosen; never in cookies => no CSRF surface.

export type ApiResult<T> = { ok: true; data: T } | { ok: false; status: number; code: string; message: string };

const KEY = "category.apiKey";
const KEY_RE = /cat_[a-z0-9]{10}_[A-Za-z0-9_-]{43}/;

export function getKey(): string {
  try {
    return sessionStorage.getItem(KEY) ?? localStorage.getItem(KEY) ?? "";
  } catch {
    return "";
  }
}

export function setKey(value: string, remember = false) {
  try {
    sessionStorage.removeItem(KEY);
    localStorage.removeItem(KEY);
    // Keep only the key itself: pasted text may carry invisible control characters.
    const match = value.match(KEY_RE);
    if (match) (remember ? localStorage : sessionStorage).setItem(KEY, match[0]);
  } catch {
    /* storage unavailable */
  }
}

export async function call<T>(base: string, path: string, init: RequestInit & { auth?: boolean } = {}): Promise<ApiResult<T>> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body && typeof init.body === "string") headers.set("Content-Type", "application/json");
  if (init.auth !== false && getKey()) headers.set("Authorization", `Bearer ${getKey()}`);
  try {
    const res = await fetch(`${base}${path}`, { ...init, headers });
    const text = await res.text();
    const body = text ? JSON.parse(text) : null;
    if (!res.ok) {
      // A validation error names the fields that failed (never the submitted values): show them with the message.
      const details: { loc?: (string | number)[]; msg?: string }[] = Array.isArray(body?.error?.details) ? body.error.details : [];
      const why = details.slice(0, 3).map((d) => `${(d.loc ?? []).slice(1).join(".")}: ${d.msg ?? ""}`).join("; ");
      return { ok: false, status: res.status, code: body?.error?.code ?? "error",
               message: `${body?.error?.message ?? res.statusText}${why ? ` (${why})` : ""}` };
    }
    return { ok: true, data: body as T };
  } catch (e) {
    return { ok: false, status: 0, code: "network", message: e instanceof Error ? e.message : "request failed" };
  }
}

/** POST a decision that may need the reviewer's confirmation: the API answers 409 ``protected_domain`` when a
 *  threat would be flagged on a popular or official site (threats/guard.py). Ask, then resend confirmed. */
export async function postConfirmed<T>(base: string, path: string, body: Record<string, unknown>): Promise<ApiResult<T>> {
  const first = await call<T>(base, path, { method: "POST", body: JSON.stringify(body) });
  if (first.ok || first.code !== "protected_domain") return first;
  if (!window.confirm(`${first.message}\n\nFlag it anyway?`)) {
    return { ok: false, status: 409, code: "cancelled", message: "Not flagged: popular or official site." };
  }
  return call<T>(base, path, { method: "POST", body: JSON.stringify({ ...body, confirm_protected: true }) });
}
