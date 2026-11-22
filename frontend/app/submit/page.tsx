import type { Metadata } from "next";
import { api, publicApiUrl } from "@/lib/api";
import type { Category } from "@/lib/types";
import SuggestForm from "@/components/SuggestForm";
import { CatSit } from "@/components/CatScene";
import { categoryName } from "@/lib/i18n";
import { getT } from "@/lib/locale";

export const dynamic = "force-dynamic";

export async function generateMetadata(): Promise<Metadata> {
  const { t } = await getT();
  return { title: t("Suggest a site", "Site öner"),
           description: t("Suggest a website and, if you like, its category; a moderator checks it before it is scanned.",
                          "Bir web sitesini ve isterseniz kategorisini önerin; site taranmadan önce bir moderatör inceler.") };
}

export default async function Submit({ searchParams }: { searchParams: Promise<{ domain?: string }> }) {
  const { domain } = await searchParams;
  const { locale, t } = await getT();
  const taxonomy = await api<{ items: Category[] }>("/api/v1/categories?kind=content", 3600).catch(() => ({ items: [] as Category[] }));
  return (
    <>
      <div className="page-head">
        <div>
          <h1>{t("Suggest a Site", "Site Öner")}</h1>
          <p className="muted">{t("Suggestions are reviewed by a moderator before the site is scanned and classified.",
                                  "Öneriler, site taranıp sınıflandırılmadan önce bir moderatör tarafından incelenir.")}</p>
        </div>
        <CatSit />
      </div>
      <SuggestForm api={publicApiUrl()} initial={(domain ?? "").slice(0, 253)}
                   categories={taxonomy.items.filter((c) => !c.is_group).map((c) => ({ id: c.id, name: categoryName(locale, c.id, c.name) }))} />
    </>
  );
}
