// Server-side API access (React Server Components). Uses the internal API URL and, if configured, a service
// API key so page renders are not throttled by the anonymous per-IP limit. Never imported by client components.
import "server-only";

const INTERNAL = (process.env.API_INTERNAL_URL || "http://127.0.0.1:8110").replace(/\/$/, "");

export function publicApiUrl(): string {
  return (process.env.PUBLIC_API_URL || INTERNAL).replace(/\/$/, "");
}

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
  }
}

/** For any call whose URL contains something the visitor chose (a domain, a link, a page cursor) or the time.
 *  Next.js keeps one cache file per distinct URL and never deletes them; with visitor-chosen URLs that cache grows
 *  until the container runs out of memory (the cache directory is a tmpfs). Only fixed URLs may be cached. */
export const UNCACHED = 0;

/** Demand counts (backend/app/demand.py): ``visitor`` is the visitor's address as Cloudflare states it, so the API
 *  can tell visitors apart although every render uses one key (it hashes the address and keeps no record of it);
 *  ``secondary`` marks a further request of the same page view, so one search counts once. */
export type DemandHint = { visitor?: string | null; secondary?: boolean };

export async function api<T>(path: string, revalidate = 60, demand: DemandHint = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (process.env.CATEGORY_FRONTEND_API_KEY) headers.Authorization = `Bearer ${process.env.CATEGORY_FRONTEND_API_KEY}`;
  if (demand.visitor) headers["X-Category-Visitor"] = demand.visitor;
  if (demand.secondary) headers["X-Category-Demand"] = "skip";
  const res = await fetch(`${INTERNAL}${path}`, revalidate > 0 ? { headers, next: { revalidate } } : { headers, cache: "no-store" });
  if (!res.ok) {
    let code = "error";
    let message = res.statusText;
    try {
      const body = await res.json();
      code = body?.error?.code ?? code;
      message = body?.error?.message ?? message;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, code, message);
  }
  return (await res.json()) as T;
}
