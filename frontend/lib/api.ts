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

export async function api<T>(path: string, revalidate = 60): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (process.env.CATEGORY_FRONTEND_API_KEY) headers.Authorization = `Bearer ${process.env.CATEGORY_FRONTEND_API_KEY}`;
  const res = await fetch(`${INTERNAL}${path}`, { headers, next: { revalidate } });
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
