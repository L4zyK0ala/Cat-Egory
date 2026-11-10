import type { Metadata } from "next";
import BulkLookup from "@/components/BulkLookup";
import { publicApiUrl } from "@/lib/api";
import { getT } from "@/lib/locale";

export const dynamic = "force-dynamic";

export async function generateMetadata(): Promise<Metadata> {
  const { t } = await getT();
  return { title: t("Bulk Lookup", "Toplu Sorgu"),
           description: t("Check the categories and threat flags of many domains at once.", "Birçok alan adının kategorisine ve tehdit işaretine tek seferde bakın.") };
}

export default async function Lookup() {
  const { t } = await getT();
  return (
    <div className="about">
      <h1>{t("Bulk Lookup", "Toplu Sorgu")}</h1>
      <p className="lead">{t("Paste a list or open a file: you get each name's category and any confirmed threat, and can download the result as CSV.",
                             "Bir liste yapıştırın ya da dosya açın: her adın kategorisini ve onaylanmış bir tehdit varsa onu görün, sonucu CSV olarak indirin.")}</p>
      <BulkLookup api={publicApiUrl()} />
    </div>
  );
}
