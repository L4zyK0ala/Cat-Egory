import Link from "next/link";
import { api, ApiError, publicApiUrl } from "@/lib/api";
import type { Category } from "@/lib/types";

export const dynamic = "force-dynamic";

type Page = { category: string; items: { domain: string; primary?: boolean }[]; next_cursor: number | null };

export default async function CategoryPage({ params, searchParams }: {
  params: Promise<{ id: string }>; searchParams: Promise<{ cursor?: string }>;
}) {
  const { id } = await params;
  const { cursor } = await searchParams;
  let category: Category | null = null;
  let page: Page | null = null;
  try {
    category = await api<Category>(`/api/v1/categories/${encodeURIComponent(id)}`, 3600);
    const qs = new URLSearchParams({ limit: "100", ...(cursor && /^\d+$/.test(cursor) ? { cursor } : {}) });
    page = await api<Page>(`/api/v1/categories/${encodeURIComponent(id)}/domains?${qs}`, 120);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return <div className="notice">Unknown category.</div>;
    return <div className="notice error">The API is not reachable right now.</div>;
  }
  const base = publicApiUrl();
  return (
    <>
      <p><Link href="/categories">← Categories</Link></p>
      <h1>{category.name}</h1>
      <p className="muted">{category.description}</p>
      <div className="row" style={{ fontSize: 14 }}>
        Download: {["txt", "hosts", "rpz"].map((ext) => (
          <a key={ext} href={`${base}/api/v1/feeds/${category!.id}.${ext}`}>{category!.id}.{ext}</a>
        ))}
      </div>
      <h2>Domains</h2>
      <div className="panel">
        {page.items.length === 0 ? <p className="muted">No published domains in this category yet.</p> : (
          <table><tbody>
            {page.items.map((d) => (
              <tr key={d.domain}><td><Link href={`/domain/${d.domain}`}>{d.domain}</Link></td>
                <td className="muted">{d.primary === undefined ? "" : d.primary ? "primary" : "secondary"}</td></tr>
            ))}
          </tbody></table>
        )}
      </div>
      {page.next_cursor !== null && <p><Link href={`/categories/${id}?cursor=${page.next_cursor}`}>Next page →</Link></p>}
    </>
  );
}
