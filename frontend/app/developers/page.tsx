import type { Metadata } from "next";
import Link from "next/link";
import DeveloperKeys from "@/components/DeveloperKeys";
import { publicApiUrl } from "@/lib/api";
import { getT } from "@/lib/locale";

export const dynamic = "force-dynamic";

export async function generateMetadata(): Promise<Metadata> {
  const { t } = await getT();
  return { title: t("API for Developers", "Geliştiriciler için API"),
           description: t("Take a free read-only API key and query Cat-Egory from your own programs.",
                          "Ücretsiz, salt okunur bir API anahtarı alın ve Cat-Egory'yi kendi programlarınızdan sorgulayın.") };
}

export default async function Developers() {
  const { t } = await getT();
  const base = publicApiUrl();
  const example = `curl -H "Authorization: Bearer cat_…" ${base}/api/v1/domain/example.com`;
  return (
    <div className="about">
      <h1>{t("API for Developers", "Geliştiriciler için API")}</h1>
      <p className="lead">{t("Query categories and threat flags from your own programs. Without a key you get 60 requests a minute; a free key raises that and gives you a daily quota of your own.",
                             "Kategorileri ve tehdit işaretlerini kendi programlarınızdan sorgulayın. Anahtarsız dakikada 60 istek yapabilirsiniz; ücretsiz bir anahtar bu sınırı yükseltir ve size ayrı bir günlük kota verir.")}</p>
      <div className="grid2">
        <div className="panel stack">
          <h3 style={{ margin: 0 }}>{t("No account, no personal data", "Hesap yok, kişisel veri yok")}</h3>
          <p className="muted">{t("We do not ask your name or e-mail. The key is shown once and only its fingerprint is stored. It is read-only: it opens nothing beyond the public API.",
                                  "Adınızı ya da e-postanızı sormuyoruz. Anahtar bir kez gösterilir, yalnızca parmak izi saklanır. Salt okunurdur: herkese açık API'nin ötesinde hiçbir şeyi açmaz.")}</p>
        </div>
        <div className="panel stack">
          <h3 style={{ margin: 0 }}>{t("Use it", "Kullanın")}</h3>
          <pre className="mono">{example}</pre>
          <p className="muted">{t("Every endpoint is described in the ", "Tüm uç noktalar ")}<a href={`${base}/api/docs`}>{t("API documentation", "API belgesinde")}</a>
            {t(". Lists for routers are on ", " anlatılıyor. Router listeleri için ")}<Link href="/integrations">{t("Router and DNS Setup", "Router ve DNS Kurulumu")}</Link>{t(".", ".")}</p>
        </div>
      </div>
      <h2>{t("Your Key", "Anahtarınız")}</h2>
      <DeveloperKeys api={base} />
    </div>
  );
}
