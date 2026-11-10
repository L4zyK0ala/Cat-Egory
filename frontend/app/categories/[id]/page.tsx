import Link from "next/link";
import { api, ApiError, publicApiUrl } from "@/lib/api";
import type { Category } from "@/lib/types";
import { categoryDescription, categoryName } from "@/lib/i18n";
import { getT } from "@/lib/locale";

export const dynamic = "force-dynamic";

type Page = { category: string; items: { domain: string; primary?: boolean }[]; next_cursor: number | null };

export default async function CategoryPage({ params, searchParams }: {
  params: Promise<{ id: string }>; searchParams: Promise<{ cursor?: string }>;
}) {
  const { id } = await params;
  const { locale, t } = await getT();
  const { cursor } = await searchParams;
  let category: Category | null = null;
  let page: Page | null = null;
  try {
    category = await api<Category>(`/api/v1/categories/${encodeURIComponent(id)}`, 3600);
    const qs = new URLSearchParams({ limit: "100", ...(cursor && /^\d+$/.test(cursor) ? { cursor } : {}) });
    page = await api<Page>(`/api/v1/categories/${encodeURIComponent(id)}/domains?${qs}`, 120);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return <div className="notice">{t("Unknown category.", "Bilinmeyen kategori.")}</div>;
    return <div className="notice error">{t("The API is not reachable right now.", "API'ye şu an ulaşılamıyor.")}</div>;
  }
  const base = publicApiUrl();
  return (
    <>
      <p><Link href="/categories">← {t("Categories", "Kategoriler")}</Link></p>
      <h1>{categoryName(locale, category.id, category.name)}</h1>
      <p className="muted">{categoryDescription(locale, category.id, category.description)}</p>
      <div className="row" style={{ fontSize: 14 }}>
        {t("Download", "İndir")}: {["txt", "hosts", "rpz"].map((ext) => (
          <a key={ext} href={`${base}/api/v1/feeds/${category!.id}.${ext}`}>{category!.id}.{ext}</a>
        ))}
      </div>
      <h2>{t("Domains", "Alan Adları")}</h2>
      <div className="panel">
        {page.items.length === 0 ? <p className="muted">{t("No published domains in this category yet.", "Bu kategoride henüz yayımlanmış alan adı yok.")}</p> : (
          <table><tbody>
            {page.items.map((d) => (
              <tr key={d.domain}><td><Link href={`/domain/${d.domain}`}>{d.domain}</Link></td>
                <td className="muted">{d.primary === undefined ? "" : d.primary ? t("primary", "ana") : t("secondary", "yan")}</td></tr>
            ))}
          </tbody></table>
        )}
      </div>
      {page.next_cursor !== null && <p><Link href={`/categories/${id}?cursor=${page.next_cursor}`}>{t("Next page →", "Sonraki sayfa →")}</Link></p>}
    </>
  );
}
