import type { Metadata } from "next";
import Link from "next/link";
import { api, ApiError, publicApiUrl, UNCACHED } from "@/lib/api";
import type { Category } from "@/lib/types";
import { categoryDescription, categoryName } from "@/lib/i18n";
import { getT } from "@/lib/locale";

export const dynamic = "force-dynamic";

type Page = { category: string; items: { domain: string; primary?: boolean }[]; next_cursor: number | null };

export async function generateMetadata({ params, searchParams }: {
  params: Promise<{ id: string }>; searchParams: Promise<{ cursor?: string }>;
}): Promise<Metadata> {
  const { id } = await params;
  const { locale, t } = await getT();
  const all = await api<{ items: Category[] }>("/api/v1/categories", 3600).catch(() => ({ items: [] as Category[] }));
  const category = all.items.find((c) => c.id === id);
  if (!category) return { robots: { index: false, follow: true } };
  const name = categoryName(locale, category.id, category.name);
  return {
    title: t(`${name} websites — list and DNS block list`, `${name} siteleri — liste ve DNS engelleme listesi`),
    description: categoryDescription(locale, category.id, category.description),
    alternates: { canonical: `/categories/${category.id}` },
    // later pages of the same list are for browsing, not for search results
    robots: { index: !(await searchParams).cursor, follow: true },
  };
}

export default async function CategoryPage({ params, searchParams }: {
  params: Promise<{ id: string }>; searchParams: Promise<{ cursor?: string }>;
}) {
  const { id } = await params;
  const { locale, t } = await getT();
  const { cursor } = await searchParams;
  let category: Category | null = null;
  let page: Page | null = null;
  try {
    // The category comes from the (cached) list, so an id nobody defined never becomes a request or a cache entry.
    const all = await api<{ items: Category[] }>("/api/v1/categories", 3600);
    category = all.items.find((c) => c.id === id) ?? null;
    if (!category) return <div className="notice">{t("Unknown category.", "Bilinmeyen kategori.")}</div>;
    const qs = new URLSearchParams({ limit: "100", ...(cursor && /^\d+$/.test(cursor) ? { cursor } : {}) });
    page = await api<Page>(`/api/v1/categories/${encodeURIComponent(category.id)}/domains?${qs}`, UNCACHED);
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
      {category.kind === "risk" && <div className="notice error" role="note">
        {t("These addresses are dangerous: do not visit them. They are listed so that firewalls and DNS filters can block them.",
           "Bu adresler tehlikelidir, ziyaret etmeyin. Güvenlik duvarları ve DNS filtreleri engelleyebilsin diye listeleniyor.")}
      </div>}
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
