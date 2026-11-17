import type { MetadataRoute } from "next";
import { siteUrl } from "@/lib/site";

export const dynamic = "force-dynamic";

/** Everything public may be crawled; the sitemap lists the domains worth a search hit. The admin UI says no. */
export default function robots(): MetadataRoute.Robots {
  const site = siteUrl();
  if (!site) return { rules: { userAgent: "*", disallow: "/" } };
  return {
    rules: { userAgent: "*", allow: "/", disallow: "/admin" },
    sitemap: new URL("/sitemap.xml", site).toString(),
  };
}
