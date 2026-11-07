"use client";
// Browser-side API calls go straight to the public API, so rate limits apply to the real client address.
// Admin keys are kept in sessionStorage (tab-scoped, never sent as cookies => no CSRF surface).

export type ApiResult<T> = { ok: true; data: T } | { ok: false; status: number; code: string; message: string };

const KEY = "category.apiKey";

export function getKey(): string {
  try {
    return sessionStorage.getItem(KEY) ?? "";
  } catch {
    return "";
  }
}

export function setKey(value: string) {
  try {
    // Keep only the key itself: pasted text may carry invisible control characters.
    const match = value.match(/cat_[a-z0-9]{10}_[A-Za-z0-9_-]{43}/);
    if (match) sessionStorage.setItem(KEY, match[0]);
    else sessionStorage.removeItem(KEY);
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
      return { ok: false, status: res.status, code: body?.error?.code ?? "error", message: body?.error?.message ?? res.statusText };
    }
    return { ok: true, data: body as T };
  } catch (e) {
    return { ok: false, status: 0, code: "network", message: e instanceof Error ? e.message : "request failed" };
  }
}
