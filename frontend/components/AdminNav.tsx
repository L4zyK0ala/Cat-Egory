"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  ["/admin", "Overview"], ["/admin/reviews", "Review"], ["/admin/moderation", "Moderation"],
  ["/admin/threats", "Confirmed Threats"],
] as const;

/** One navigation bar for every admin page (admin UI only). */
export default function AdminNav() {
  const path = usePathname();
  return (
    <nav className="admin-nav" aria-label="Admin">
      {LINKS.map(([href, label]) => (
        <Link key={href} href={href} aria-current={path === href ? "page" : undefined}>{label}</Link>
      ))}
    </nav>
  );
}
