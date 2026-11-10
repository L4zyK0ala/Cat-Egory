import { publicApiUrl } from "@/lib/api";
import ModerationQueue from "@/components/ModerationQueue";
import { getT } from "@/lib/locale";

export const dynamic = "force-dynamic";

export default async function Moderation() {
  const { t } = await getT();
  return (<><h1>{t("Moderation", "Moderasyon")}</h1>
    <p className="muted">{t("Community reports and suggestions. Evidence URLs are shown as text; open them only in an isolated browser.",
                            "Topluluk bildirimleri ve önerileri. Kanıt bağlantıları metin olarak gösterilir; yalnızca yalıtılmış bir tarayıcıda açın.")}</p>
    <ModerationQueue api={publicApiUrl()} /></>);
}
