import type { Metadata } from "next";
import Link from "next/link";
import CopyText from "@/components/CopyText";
import { api, publicApiUrl, UNCACHED } from "@/lib/api";
import { categoryName, formatWhen } from "@/lib/i18n";
import { getT } from "@/lib/locale";
import type { Category } from "@/lib/types";

export const dynamic = "force-dynamic";

type Change = { at: string; domain: string; kind: "category" | "threat_added" | "threat_removed";
                old: string | null; new: string | null; reason: "withdrawn" | "expired" | null };
type Changes = { since: string; until: string; more: boolean; items: Change[] };

const DAYS = 7;
const SHOWN = 300;

export async function generateMetadata(): Promise<Metadata> {
  const { t } = await getT();
  return { title: t("Changes", "Değişiklikler"),
           description: t("Which sites changed category or threat status recently.", "Hangi sitelerin kategorisi ya da tehdit durumu yakın zamanda değişti.") };
}

export default async function ChangesPage() {
  const { locale, t } = await getT();
  const since = new Date(Date.now() - DAYS * 86400_000).toISOString();
  let data: Changes | null = null;
  let names: Record<string, string> = {};
  try {
    const [c, cats] = await Promise.all([api<Changes>(`/api/v1/changes?since=${encodeURIComponent(since)}&limit=${SHOWN}&newest=true`, UNCACHED),  // "since" differs on every request
                                         api<{ items: Category[] }>("/api/v1/categories", 3600)]);
    data = c;
    names = Object.fromEntries(cats.items.map((x) => [x.id, categoryName(locale, x.id, x.name)]));
  } catch {
    data = null;
  }
  const base = publicApiUrl();
  const name = (id: string | null) => (id == null ? "" : names[id] ?? categoryName(locale, id, id));
  // The latest changes, newest first. (Reversing the oldest-first feed showed only the oldest 1,000 of a busy week.)
  const items = data?.items ?? [];

  const event = (c: Change) => {
    if (c.kind === "category") {
      return c.old == null ? <><span className="badge ok">{t("New", "Yeni")}</span> {name(c.new)}</>
        : <><span className="badge">{t("Category", "Kategori")}</span> {name(c.old)} → {name(c.new)}</>;
    }
    if (c.kind === "threat_added") return <><span className="badge danger">{t("Threat", "Tehdit")}</span> {name(c.new)}</>;
    return <><span className="badge ok">{t("Cleared", "Kaldırıldı")}</span> {name(c.new)} · {c.reason === "expired"
      ? t("no longer seen", "artık görülmüyor") : t("withdrawn after review", "inceleme sonrası geri alındı")}</>;
  };

  return (
    <div className="about">
      <h1>{t("Changes", "Değişiklikler")}</h1>
      <p className="lead">{t("Sites whose main category or threat status changed in the last 7 days. Filters and scripts can fetch only these changes instead of reloading whole lists.",
                             "Son 7 günde ana kategorisi ya da tehdit durumu değişen siteler. Filtreler ve betikler tüm listeleri yeniden indirmek yerine yalnız bu değişiklikleri alabilir.")}</p>

      <div className="panel stack">
        {!data ? <div className="notice error">{t("The API is not reachable right now.", "API'ye şu an ulaşılamıyor.")}</div>
          : items.length === 0 ? <div className="notice">{t("Nothing changed in the last 7 days.", "Son 7 günde değişiklik yok.")}</div> : (<>
            <table>
              <thead><tr><th>{t("When", "Zaman")}</th><th>{t("Site", "Site")}</th><th>{t("Change", "Değişiklik")}</th></tr></thead>
              <tbody>{items.slice(0, SHOWN).map((c) => (
                <tr key={`${c.at}-${c.domain}-${c.kind}`}>
                  <td className="muted">{formatWhen(locale, c.at)}</td>
                  <td className="mono"><Link href={`/domain/${c.domain}`}>{c.domain}</Link></td>
                  <td>{event(c)}</td>
                </tr>))}
              </tbody>
            </table>
            {(items.length > SHOWN || data.more) && <p className="muted">{t("More changes are available through the API below.", "Daha fazla değişiklik aşağıdaki API ile alınabilir.")}</p>}
          </>)}
      </div>

      <h2>{t("Follow Changes Automatically", "Değişiklikleri Otomatik İzleyin")}</h2>
      <div className="panel stack">
        <p>{t("Ask for the changes since your last check, oldest first. Keep the until value of the answer and send it as since next time; while more is true, ask again straight away. Up to 30 days back.",
              "Son kontrolünüzden bu yana olan değişiklikleri eskiden yeniye isteyin. Cevaptaki until değerini saklayıp bir sonraki seferde since olarak gönderin; more true olduğu sürece hemen tekrar isteyin. En fazla 30 gün geriye gidilebilir.")}</p>
        <CopyText value={`${base}/api/v1/changes?since=2026-11-01T00:00:00Z`} />
        <p>{t("A list filter can apply only what changed since the version it already has: lines starting with + were added, lines starting with - were removed.",
              "Bir liste filtresi, elindeki sürümden bu yana yalnız değişeni uygulayabilir: + ile başlayan satırlar eklendi, - ile başlayanlar çıkarıldı.")}</p>
        <CopyText value={`${base}/api/v1/feeds/profile-security.txt/diff?from=2026.11.01.1`} />
        <p className="muted">{t("The version you have is in the X-Feed-Version header and at the top of every list. See ", "Elinizdeki sürüm X-Feed-Version başlığında ve her listenin başında yazar. Ayrıntılar: ")}
          <Link href="/developers">API</Link> · <Link href="/feeds">{t("Feeds", "Listeler")}</Link></p>
      </div>
    </div>
  );
}
