// Who runs this site, from the container's environment (CATEGORY_CONTACT_EMAIL, CATEGORY_OPERATOR_NAME).
import "server-only";

export const contactEmail = () => process.env.CONTACT_EMAIL?.trim() || null;
export const operatorName = () => process.env.OPERATOR_NAME?.trim() || null;

/** The public site's own address (PUBLIC_SITE_URL, https://cat-egory.org), for canonical links, robots.txt and the
 *  sitemap; null on the admin UI (home network only) or when unset. */
export function siteUrl(): URL | null {
  if (process.env.UI_ADMIN === "true") return null;
  try {
    return new URL(process.env.PUBLIC_SITE_URL || "");
  } catch {
    return null;
  }
}
