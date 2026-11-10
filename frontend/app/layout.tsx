import type { Metadata } from "next";
import Link from "next/link";
import LanguageSwitch from "@/components/LanguageSwitch";
import { LocaleProvider } from "@/components/LocaleProvider";
import { getT } from "@/lib/locale";
import "./globals.css";

// Read at request time: the same image runs as the public site and, with UI_ADMIN=true, as the admin UI.
export const dynamic = "force-dynamic";

export async function generateMetadata(): Promise<Metadata> {
  const { t } = await getT();
  return {
    title: { default: t("Cat-Egory — open web categorization", "Cat-Egory — açık web kategorilendirme"),
             template: "%s · Cat-Egory" },
    description: t("Open database of website categories, built by AI classification and community review.",
                   "Yapay zekâ sınıflandırması ve topluluk incelemesiyle oluşturulan açık web sitesi kategori veri tabanı."),
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
  return (
    <html lang={locale}>
      <body>
        <LocaleProvider locale={locale}>
        <header className="top">
          <div className="inner">
            <Link href={adminUi ? "/admin" : "/"} className="brand" aria-label={t("Cat-Egory home", "Cat-Egory ana sayfa")}><Brand />{adminUi && <span className="badge pending">admin</span>}</Link>
            {!adminUi && (
              <nav aria-label={t("Main", "Ana menü")}>
                <Link href="/">{t("Home", "Ana Sayfa")}</Link>
                <Link href="/categories">{t("Categories", "Kategoriler")}</Link>
                <Link href="/accuracy">{t("Accuracy", "Doğruluk")}</Link>
                <Link href="/feeds">{t("Feeds", "Listeler")}</Link>
                <Link href="/submit">{t("Suggest a Site", "Site Öner")}</Link>
                <Link href="/about">{t("About", "Hakkında")}</Link>
              </nav>
            )}
            <LanguageSwitch />
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
            {!adminUi && <span><Link href="/developers">API</Link> · <Link href="/changes">{t("Changes", "Değişiklikler")}</Link> · <Link href="/privacy">{t("Privacy", "Gizlilik")}</Link> · <Link href="/contact">{t("Contact", "İletişim")}</Link></span>}
          </div>
        </footer>
        </LocaleProvider>
      </body>
    </html>
  );
}
