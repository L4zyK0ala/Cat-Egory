import { publicApiUrl } from "@/lib/api";
import Detector from "@/components/Detector";
import { getT } from "@/lib/locale";

export const dynamic = "force-dynamic";

export default async function DetectorPage() {
  const { t } = await getT();
  return (<><h1>{t("Threat Detector", "Tehdit Dedektörü")}</h1>
    <p className="muted">{t(
      "Our own phishing rule: a page that asks for a password, card or SMS code, claims a Turkish brand, is not that brand's site, and whose name is suspect or certificate new. It is measured against people's decisions; it may flag by itself only once it has proven itself and the switch is on.",
      "Kendi oltalama kuralımız: şifre, kart ya da SMS kodu isteyen, bir Türk markası olduğunu söyleyen, o markanın sitesi olmayan, adı şüpheli ya da sertifikası yeni bir sayfa. İnsan kararlarıyla ölçülür; ancak kendini kanıtlayınca ve anahtar açıksa tek başına işaret koyabilir.")}</p>
    <Detector api={publicApiUrl()} /></>);
}
