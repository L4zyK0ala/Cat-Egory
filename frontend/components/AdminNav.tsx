"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useT } from "./LocaleProvider";

const LINKS = [
  ["/admin", "Overview", "Genel Bakış"], ["/admin/reviews", "Review", "İnceleme"], ["/admin/moderation", "Moderation", "Moderasyon"],
  ["/admin/threats", "Confirmed Threats", "Onaylanmış Tehditler"],
  ["/admin/trends", "Trends", "Yönelim"], ["/admin/infographic", "Infographic", "İnfografik"], ["/admin/yasmama", "Yaş Mama", "Yaş Mama"],
  ["/admin/detector", "Laser Pointer", "Lazer Pointer"],
] as const;

/** One navigation bar for every admin page (admin UI only). */
export default function AdminNav() {
  const path = usePathname();
  const t = useT();
  return (
    <nav className="admin-nav" aria-label={t("Admin", "Yönetim")}>
      {LINKS.map(([href, en, tr]) => (
        <Link key={href} href={href} aria-current={path === href ? "page" : undefined}>{t(en, tr)}</Link>
      ))}
    </nav>
  );
}
