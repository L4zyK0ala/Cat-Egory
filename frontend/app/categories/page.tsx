import Link from "next/link";
import { api } from "@/lib/api";
import type { Category } from "@/lib/types";
import { CatNap } from "@/components/CatScene";
import { categoryDescription, categoryName } from "@/lib/i18n";
import { getT } from "@/lib/locale";

export const dynamic = "force-dynamic";

export default async function Categories() {
  const { locale, t } = await getT();
  let data: { taxonomy_version: string; items: Category[] } | null = null;
  try {
    data = await api("/api/v1/categories", 3600);
  } catch {
    data = null;
  }
  if (!data) return <div className="notice error">{t("The API is not reachable right now.", "API'ye şu an ulaşılamıyor.")}</div>;
  const groups = data.items.filter((c) => c.is_group);
  return (
    <>
      <div className="page-head">
        <div>
          <h1>{t("Categories", "Kategoriler")}</h1>
          <p className="muted">{t(`Taxonomy ${data.taxonomy_version}. Security threats are a separate risk classification.`,
                                  `Taksonomi ${data.taxonomy_version}. Güvenlik tehditleri ayrı bir risk sınıflandırmasıdır.`)}</p>
        </div>
        <CatNap />
      </div>
      {groups.map((g) => (
        <section key={g.id}>
          <h2>{categoryName(locale, g.id, g.name)} {g.kind === "risk" && <span className="badge danger">risk</span>}</h2>
          <div className="panel">
            <table><tbody>
              {data!.items.filter((c) => c.parent_id === g.id && !c.deprecated_in).map((c) => (
                <tr key={c.id}><td style={{ width: 240 }}><Link href={`/categories/${c.id}`}>{categoryName(locale, c.id, c.name)}</Link></td>
                  <td className="muted">{categoryDescription(locale, c.id, c.description)}</td><td className="mono muted">{c.id}</td></tr>
              ))}
            </tbody></table>
          </div>
        </section>
      ))}
    </>
  );
}
