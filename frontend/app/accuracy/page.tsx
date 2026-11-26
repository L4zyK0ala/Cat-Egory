import type { Metadata } from "next";
import Link from "next/link";
import { CatCheck } from "@/components/CatScene";
import { api } from "@/lib/api";
import { categoryName, formatWhen, intl, type Locale } from "@/lib/i18n";
import { getT } from "@/lib/locale";
import type { Category } from "@/lib/types";

export const dynamic = "force-dynamic";

type Share = { n: number; correct: number; rejected: number; accuracy: number | null; low: number | null; high: number | null };
type Accuracy = {
  generated_at: string;
  published_ai: Share & { categories: (Share & { category: string })[]; min_per_category: number };
  hard_cases: Share;
  threats: { total: number; active: number; withdrawn: number; expired: number; withdrawn_share: number | null;
             median_hours_to_withdraw: number | null; withdrawals_timed: number };
  published_by_origin: Record<string, number>;
};

const pct = (locale: Locale, x: number | null) => x == null ? "–"
  : locale === "tr" ? `%${(x * 100).toFixed(1).replace(".", ",")}` : `${(x * 100).toFixed(1)}%`;
const RELIABLE = 30;

export async function generateMetadata(): Promise<Metadata> {
  const { t } = await getT();
  return { title: t("Accuracy", "Doğruluk"),
           description: t("How right Cat-Egory's data is, measured against people.", "Cat-Egory verisinin ne kadar doğru olduğu, insanlarla ölçülmüş hâli.") };
}

export default async function AccuracyPage() {
  const { locale, t } = await getT();
  let data: Accuracy | null = null;
  let names: Record<string, string> = {};
  try {
    const [a, c] = await Promise.all([api<Accuracy>("/api/v1/accuracy", 300), api<{ items: Category[] }>("/api/v1/categories", 3600)]);
    data = a;
    names = Object.fromEntries(c.items.map((x) => [x.id, categoryName(locale, x.id, x.name)]));
  } catch {
    data = null;
  }
  if (!data) return <div className="notice error">{t("The API is not reachable right now.", "API'ye şu an ulaşılamıyor.")}</div>;
  const p = data.published_ai;
  const interval = (s: Share) => s.low == null ? "" : t(`95% interval ${pct(locale, s.low)}–${pct(locale, s.high)}`,
                                                       `%95 aralık ${pct(locale, s.low)}–${pct(locale, s.high)}`);
  const origins = Object.entries(data.published_by_origin).sort(([, a], [, b]) => b - a);
  const totalPublished = origins.reduce((sum, [, n]) => sum + n, 0);
  const originLabel: Record<string, string> = { ai: t("AI", "Yapay zekâ"), human: t("A person", "Bir kişi"),
    community: t("Community", "Topluluk"), rule: t("Registry rule", "Tahsis kuralı"), source: t("Licensed list", "Lisanslı liste") };
  const th = data.threats;
  return (
    <div className="about">
      <div className="page-head">
        <div>
          <h1>{t("Accuracy", "Doğruluk")}</h1>
          <p className="lead">{t("How right is the data? Measured against people, not against the model itself, and published with how much it can be trusted.",
                                 "Veri ne kadar doğru? Modelin kendisiyle değil insanlarla ölçülür ve ne kadar güvenilebileceğiyle birlikte yayımlanır.")}</p>
        </div>
        <CatCheck />
      </div>

      <h2>{t("Published AI Answers", "Yayımlanan Yapay Zekâ Cevapları")}</h2>
      <div className="panel stack">
        <p className="muted">{t("Confident AI answers are published without a person. Reviewers check a random sample of them; the share they kept unchanged is the accuracy of what you see from the AI.",
                                "Emin olunan yapay zekâ cevapları bir kişiye sorulmadan yayımlanır. İnceleyiciler bunlardan rastgele bir örneği denetler; değiştirmeden bıraktıkları oran, sitede gördüğünüz yapay zekâ cevaplarının doğruluğudur.")}</p>
        {p.n === 0 ? <div className="notice">{t("No sample checked yet.", "Henüz denetlenmiş örnek yok.")}</div> : (<>
          <div className="tiles">
            <div className="panel tile"><div className="label">{t("Kept by reviewers", "İnceleyicinin değiştirmediği")}</div>
              <div className="value">{pct(locale, p.accuracy)}</div><div className="muted" style={{ fontSize: 13 }}>{interval(p)}</div></div>
            <div className="panel tile"><div className="label">{t("Answers checked", "Denetlenen cevap")}</div>
              <div className="value">{p.n.toLocaleString(intl(locale))}</div>
              <div className="muted" style={{ fontSize: 13 }}>{t(`${p.n - p.correct} corrected or rejected`, `${p.n - p.correct} düzeltildi veya reddedildi`)}</div></div>
          </div>
          {p.n < RELIABLE && <div className="notice">{t(`The sample is still small (${p.n}); the interval shows how far the true figure may be from it.`,
                                                       `Örnek henüz küçük (${p.n}); aralık, gerçek değerin bundan ne kadar uzak olabileceğini gösterir.`)}</div>}
          {p.categories.length > 0 && (
            <table>
              <thead><tr><th>{t("Main category the AI gave", "Yapay zekânın verdiği ana kategori")}</th><th className="num">{t("Checked", "Denetlenen")}</th>
                <th className="num">{t("Kept", "Değişmeyen")}</th><th>{t("95% interval", "%95 aralık")}</th></tr></thead>
              <tbody>{p.categories.map((c) => (
                <tr key={c.category}><td><Link href={`/categories/${c.category}`}>{names[c.category] ?? c.category}</Link></td>
                  <td className="num">{c.n}</td><td className="num">{pct(locale, c.accuracy)}</td>
                  <td className="muted">{pct(locale, c.low)}–{pct(locale, c.high)}</td></tr>
              ))}</tbody>
            </table>
          )}
          <p className="muted" style={{ fontSize: 13 }}>{t(`Categories with fewer than ${p.min_per_category} checked answers are not listed.`,
                                                           `${p.min_per_category} denetimden az olan kategoriler listelenmez.`)}</p>
        </>)}
      </div>

      <h2>{t("Hard Cases", "Zor Vakalar")}</h2>
      <div className="panel stack">
        <p className="muted">{t("Answers the model was unsure about, or where two models disagreed, are never published without a person. How often the model was still right on these is shown apart: it is lower by design and is not the accuracy of the published data.",
                                "Modelin emin olmadığı ya da iki modelin farklı düşündüğü cevaplar hiçbir zaman bir kişi görmeden yayımlanmaz. Modelin bunlarda yine de ne sıklıkla haklı çıktığı ayrıca gösterilir: tasarım gereği daha düşüktür ve yayımlanan verinin doğruluğu değildir.")}</p>
        {data.hard_cases.n === 0 ? <div className="notice">{t("None decided yet.", "Henüz karara bağlanan yok.")}</div> : (
          <p><b>{pct(locale, data.hard_cases.accuracy)}</b> · {t(`${data.hard_cases.n} decided`, `${data.hard_cases.n} karar`)} · <span className="muted">{interval(data.hard_cases)}</span></p>
        )}
      </div>

      <h2>{t("Threat Flags", "Tehdit İşaretleri")}</h2>
      <div className="panel stack">
        <div className="tiles">
          <div className="panel tile"><div className="label">{t("Active", "Aktif")}</div><div className="value">{th.active}</div></div>
          <div className="panel tile"><div className="label">{t("Taken back", "Geri çekilen")}</div><div className="value">{th.withdrawn}</div>
            <div className="muted" style={{ fontSize: 13 }}>{t(`${pct(locale, th.withdrawn_share)} of all flags`, `tüm işaretlere oranı ${pct(locale, th.withdrawn_share)}`)}</div></div>
          <div className="panel tile"><div className="label">{t("Expired (site gone)", "Süresi dolan (site kapandı)")}</div><div className="value">{th.expired}</div></div>
          <div className="panel tile"><div className="label">{t("Median time to take back", "Geri çekme süresi (ortanca)")}</div>
            <div className="value">{th.median_hours_to_withdraw == null ? "–" : th.median_hours_to_withdraw < 48
              ? t(`${th.median_hours_to_withdraw.toFixed(0)} h`, `${th.median_hours_to_withdraw.toFixed(0)} sa`)
              : t(`${(th.median_hours_to_withdraw / 24).toFixed(0)} d`, `${(th.median_hours_to_withdraw / 24).toFixed(0)} gün`)}</div></div>
        </div>
        <p className="muted" style={{ fontSize: 13 }}>{t("A flag is taken back after a mistake or when the site was cleaned; this count cannot tell the two apart. Anyone can dispute a flag from the domain's page.",
                                                         "Bir işaret bir hatadan sonra ya da site temizlendiğinde geri çekilir; bu sayı ikisini ayırt edemez. Herkes bir işarete alan adının sayfasından itiraz edebilir.")}</p>
      </div>

      <h2>{t("Who Decided", "Kim Karar Verdi")}</h2>
      <div className="panel">
        {origins.map(([origin, n]) => (
          <div className="bar" key={origin}>
            <span>{originLabel[origin] ?? origin}</span>
            <div className="track"><div className="fill" style={{ width: `${(n / Math.max(1, totalPublished)) * 100}%` }} /></div>
            <span className="num">{n.toLocaleString(intl(locale))}</span>
          </div>
        ))}
        <p className="muted" style={{ fontSize: 13 }}>{t("Published domains by who set their main category.", "Yayımlanan alan adları, ana kategorisini kimin belirlediğine göre.")}</p>
      </div>

      <p className="muted" style={{ fontSize: 13 }}>{t("Updated", "Güncelleme:")} {formatWhen(locale, data.generated_at)} (GMT+3)</p>
    </div>
  );
}
