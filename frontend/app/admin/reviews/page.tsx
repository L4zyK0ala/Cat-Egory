import Link from "next/link";
import { publicApiUrl } from "@/lib/api";
import ReviewQueue from "@/components/ReviewQueue";
import QuickReview from "@/components/QuickReview";
import { getT } from "@/lib/locale";

export const dynamic = "force-dynamic";

export default async function Reviews({ searchParams }: { searchParams: Promise<{ view?: string }> }) {
  const list = (await searchParams).view === "list";
  const { t } = await getT();
  return (<>
    <h1>{t("Review Queue", "İnceleme Kuyruğu")}</h1>
    <p className="muted">{t("Model output is a claim. Approve it, set the right category, or reject. Confirmed threats become public risk flags. Approved and corrected answers become human-verified data.",
                            "Model çıktısı bir iddiadır. Onaylayın, doğru kategoriyi seçin ya da reddedin. Onaylanan tehditler herkese açık risk işaretine dönüşür. Onaylanan ve düzeltilen cevaplar insan tarafından doğrulanmış veri olur.")}</p>
    <p className="row">{list ? <Link href="/admin/reviews">{t("Quick Mode (One at a Time, Keyboard)", "Hızlı Mod (Tek Tek, Klavyeyle)")}</Link>
                             : <Link href="/admin/reviews?view=list">{t("List View", "Liste Görünümü")}</Link>}</p>
    {list ? <ReviewQueue api={publicApiUrl()} /> : <QuickReview api={publicApiUrl()} />}
  </>);
}
