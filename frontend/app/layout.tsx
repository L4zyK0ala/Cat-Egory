import type { Metadata } from "next";
import Link from "next/link";
import LanguageSwitch from "@/components/LanguageSwitch";
import MobileMenu from "@/components/MobileMenu";
import { LocaleProvider } from "@/components/LocaleProvider";
import { getT } from "@/lib/locale";
import { siteUrl } from "@/lib/site";
import "./globals.css";

// Read at request time: the same image runs as the public site and, with UI_ADMIN=true, as the admin UI.
export const dynamic = "force-dynamic";

export async function generateMetadata(): Promise<Metadata> {
  const { t } = await getT();
  const site = siteUrl();
  return {
    ...(site ? { metadataBase: site } : {}),
    title: { default: t("Cat-Egory — open web categorization", "Cat-Egory — açık web kategorilendirme"),
             template: "%s · Cat-Egory" },
    description: t("Open database of website categories, built by AI classification and community review.",
                   "Yapay zekâ sınıflandırması ve topluluk incelemesiyle oluşturulan açık web sitesi kategori veri tabanı."),
    // the admin UI is on the home network only, but say it anyway
    ...(process.env.UI_ADMIN === "true" ? { robots: { index: false, follow: false } } : {}),
  };
}

function Brand({ size = 30 }: { size?: number }) {
  return (
    <>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src="/logo.svg" alt="" width={size} height={size} />
      <span>Cat-<b>Egory</b></span>
    </>
  );
}

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const adminUi = process.env.UI_ADMIN === "true";
  const { locale, t } = await getT();
  const links = [
    { href: "/", label: t("Home", "Ana Sayfa") }, { href: "/categories", label: t("Categories", "Kategoriler") },
    { href: "/accuracy", label: t("Accuracy", "Doğruluk") }, { href: "/feeds", label: t("Feeds", "Listeler") },
    { href: "/submit", label: t("Suggest a Site", "Site Öner") }, { href: "/about", label: t("About", "Hakkında") },
  ];
  const footer = [
    { href: "/developers", label: "API" }, { href: "/business", label: t("For Companies", "Kurumsal") },
    { href: "/changes", label: t("Changes", "Değişiklikler") }, { href: "/privacy", label: t("Privacy", "Gizlilik") },
    { href: "/contact", label: t("Contact", "İletişim") },
  ];
  return (
    <html lang={locale}>
      <body>
        <LocaleProvider locale={locale}>
        <header className="top">
          <div className="inner">
            <Link href={adminUi ? "/admin" : "/"} className="brand" aria-label={t("Cat-Egory home", "Cat-Egory ana sayfa")}><Brand />{adminUi && <span className="badge pending">admin</span>}</Link>
            {!adminUi && (
              <nav className="main-nav" aria-label={t("Main", "Ana menü")}>
                {links.map((l) => <Link key={l.href} href={l.href}>{l.label}</Link>)}
              </nav>
            )}
            <span className="desktop-lang"><LanguageSwitch /></span>
            {!adminUi && <MobileMenu links={links} footer={footer} />}
          </div>
        </header>
        <main>{children}</main>
        <footer>
          <div className="inner">
            <span className="brand"><Brand size={22} /></span>
            <span>{t("Code Apache-2.0 · Classification data CC BY 4.0 (Cat-Egory contributors)",
                     "Kod Apache-2.0 · Sınıflandırma verisi CC BY 4.0 (Cat-Egory katkıcıları)")}</span>
            <span>{t("AI classifications are machine estimates; verified entries were confirmed by a person.",
                     "Yapay zekâ sınıflandırmaları makine tahminidir; doğrulanmış kayıtları bir kişi onaylamıştır.")}</span>
            {!adminUi && <span>{footer.map((l, i) => <span key={l.href}>{i > 0 && " · "}<Link href={l.href}>{l.label}</Link></span>)}</span>}
          </div>
        </footer>
        </LocaleProvider>
      </body>
    </html>
  );
}
