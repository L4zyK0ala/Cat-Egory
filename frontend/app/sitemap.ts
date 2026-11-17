import type { MetadataRoute } from "next";
import { api, UNCACHED } from "@/lib/api";
import { siteUrl } from "@/lib/site";
import type { Category } from "@/lib/types";

export const dynamic = "force-dynamic";

type Dump = { domains: { domain: string; status: string; last_updated: string | null }[] };

const PAGES = ["", "/categories", "/feeds", "/integrations", "/accuracy", "/developers", "/lookup", "/changes",
               "/submit", "/about", "/privacy", "/contact"];

/** The public pages, every category and every published domain (classified or verified): the same list as the
 *  full download, from the internal API. Not kept in Next's cache: the list outgrows its 2 MB per entry, and search
 *  engines ask for a sitemap about once a day. A sitemap holds up to 50,000 addresses. */
export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const site = siteUrl();
  if (!site) return [];
  const at = (path: string) => new URL(path || "/", site).toString();
  const [dump, taxonomy] = await Promise.all([
    // not a download: the demand count (Yönelim) must not count the sitemap
    api<Dump>("/api/v1/feeds/all.json", UNCACHED, { secondary: true }).catch(() => ({ domains: [] }) as Dump),
    api<{ items: Category[] }>("/api/v1/categories", 3600).catch(() => ({ items: [] as Category[] })),
  ]);
  const domains = dump.domains
    .filter((d) => d.status === "classified" || d.status === "verified")
    .slice(0, 50_000 - PAGES.length - taxonomy.items.length)
    .map((d) => ({ url: at(`/domain/${d.domain}`), ...(d.last_updated ? { lastModified: new Date(d.last_updated) } : {}),
                   changeFrequency: "monthly" as const, priority: d.status === "verified" ? 0.6 : 0.5 }));
  return [
    ...PAGES.map((p) => ({ url: at(p), changeFrequency: "daily" as const, priority: p === "" ? 1 : 0.8 })),
    ...taxonomy.items.filter((c) => !c.is_group && !c.deprecated_in)
      .map((c) => ({ url: at(`/categories/${c.id}`), changeFrequency: "daily" as const, priority: 0.7 })),
    ...domains,
  ];
}
