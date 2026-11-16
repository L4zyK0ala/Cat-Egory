import { publicApiUrl } from "@/lib/api";
import Trends from "@/components/Trends";
import { getT } from "@/lib/locale";

export const dynamic = "force-dynamic";

export default async function TrendsPage() {
  const { t } = await getT();
  return (<><h1>{t("Trends", "Yönelim")}</h1>
    <p className="muted">{t(
      "What visitors look up and which lists they download, as daily totals. Nothing here says who asked: visitors are only counted, as distinct hashed addresses, and the count is approximate. Days appear after midnight (Türkiye time). Unknown names that enough different visitors looked up on one day are scanned before the popularity lists.",
      "Ziyaretçilerin neyi sorguladığı ve hangi listeleri indirdiği, günlük toplamlar olarak. Burada kimin sorduğu yoktur: ziyaretçiler yalnızca, anahtarlı özetleri farklı olanlar olarak sayılır ve sayı yaklaşıktır. Günler gece yarısından sonra görünür (Türkiye saati). Aynı gün yeterince farklı ziyaretçinin sorguladığı bilinmeyen adlar, popülerlik listelerinden önce taranır.")}</p>
    <Trends api={publicApiUrl()} /></>);
}
