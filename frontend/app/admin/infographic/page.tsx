import { publicApiUrl } from "@/lib/api";
import Infographic from "@/components/Infographic";
import { getT } from "@/lib/locale";

export const dynamic = "force-dynamic";

function siteName(): string {
  try {
    return new URL(process.env.PUBLIC_SITE_URL || "").host || "cat-egory.org";
  } catch {
    return "cat-egory.org";
  }
}

export default async function InfographicPage() {
  const { t } = await getT();
  return (<><h1>{t("Infographic", "İnfografik")}</h1>
    <p className="muted">{t(
      "A month's numbers as 1080×1350 cards for LinkedIn and other social media, with a post text to go with them. Only what Cat-Egory publishes anyway: its categories, the threats on its public pages, and visitor lookups as category totals. Never a looked-up name, the national (USOM) list or VirusTotal answers.",
      "Bir ayın sayıları, LinkedIn ve diğer sosyal medya için 1080×1350 kartlar olarak; yanında gönderi metni. Yalnızca Cat-Egory'nin zaten yayımladıkları: kategoriler, herkese açık sayfalardaki tehditler ve ziyaretçi sorgularının kategori toplamları. Sorgulanan bir ad, ulusal (USOM) liste ya da VirusTotal cevapları asla yer almaz.")}</p>
    <Infographic api={publicApiUrl()} site={siteName()} /></>);
}
