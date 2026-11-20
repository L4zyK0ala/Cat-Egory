import { api } from "@/lib/api";
import type { Category, Stats } from "@/lib/types";
import SearchBox from "@/components/SearchBox";
import Mascot from "@/components/Mascot";
import Link from "next/link";
import Hint from "@/components/Hint";
import { categoryName, intl, type Locale } from "@/lib/i18n";
import { getT } from "@/lib/locale";

export const dynamic = "force-dynamic";

function Tile({ label, value, hint, locale }: { label: string; value: string | number; hint: string; locale: Locale }) {
  return (
    <div className="panel tile">
      <div className="label">{label} <Hint text={hint} /></div>
      <div className="value">{typeof value === "number" ? value.toLocaleString(intl(locale)) : value}</div>
    </div>
  );
}

export default async function Dashboard() {
  const { locale, t } = await getT();
  let stats: Stats | null = null;
  let names: Record<string, string> = {};
  try {
    const [s, c] = await Promise.all([api<Stats>("/api/v1/stats", 30), api<{ items: Category[] }>("/api/v1/categories", 3600)]);
    stats = s;
    names = Object.fromEntries(c.items.map((x) => [x.id, categoryName(locale, x.id, x.name)]));
  } catch {
    stats = null;
  }
  const rate = stats?.ai_valid_answer_rate_30d;
  const maxDist = Math.max(1, ...(stats?.category_distribution ?? []).map((d) => d.domains));
  const days = Object.entries(stats?.daily_scans ?? {}).sort(([a], [b]) => (a < b ? 1 : -1));
  return (
    <>
      <section className="hero">
        <div className="hero-text">
          {locale === "tr" ? <h1>Her Web Sitesi, <em>Merakla</em> Kategorilendirilir.</h1>
            : <h1>Every Website, <em>Curiously</em> Categorized.</h1>}
          <p className="lead">{t("Look up how a website is categorized, download category lists, or help correct the data.",
                                 "Bir sitenin hangi kategoride olduğuna bakın, kategori listelerini indirin ya da verinin düzeltilmesine yardım edin.")}</p>
          <SearchBox />
          <p className="muted hero-more" style={{ fontSize: 14 }}>
            {t("Searches the sites we have already scanned and categorized; it does not scan a site on the spot.",
               "Daha önce taranıp sınıflandırılmış siteler arasında arar; siteyi o anda taramaz.")}{" "}
            <Link href="/lookup">{t("Many domains? Bulk lookup →", "Birden çok alan adı mı? Toplu sorgu →")}</Link></p>
        </div>
        <Mascot />
      </section>
      <section className="why" aria-labelledby="why-title">
        <h2 id="why-title">{t("Why Cat-Egory?", "Neden Cat-Egory?")}</h2>
        <div className="why-cards">
          <div className="panel"><h3>{t("Open Data", "Açık Veri")}</h3>
            <p className="muted">{t("Categories you can download, check and build on, under CC BY 4.0.",
                                    "CC BY 4.0 lisansıyla indirebileceğiniz, denetleyebileceğiniz ve üzerine inşa edebileceğiniz kategoriler.")}</p></div>
          <div className="panel"><h3>{t("Checked by People", "İnsanlar Denetler")}</h3>
            <p className="muted">{t("The AI reads first; unsure answers, look-alikes and threats are decided by a person.",
                                    "Önce yapay zekâ okur; emin olmadığı cevaplara, taklit sitelere ve tehditlere bir kişi karar verir.")}</p></div>
          <div className="panel"><h3>{t("Every Label Has a Source", "Her Etiketin Bir Kaynağı Var")}</h3>
            <p className="muted">{t("See whether a category came from the AI, a reviewer, the community or a rule.",
                                    "Bir kategorinin yapay zekâdan mı, bir inceleyiciden mi, topluluktan mı yoksa bir kuraldan mı geldiğini görün.")}</p></div>
        </div>
        <p><Link href="/about">{t("About Us →", "Hakkımızda →")}</Link></p>
      </section>
      {!stats ? (
        <div className="notice error">{t("The API is not reachable right now.", "API'ye şu an ulaşılamıyor.")}</div>
      ) : (
        <>
          <h2>{t("Overview", "Genel Bakış")}</h2>
          <div className="tiles">
            <Tile locale={locale} label={t("Total domains", "Toplam alan adı")} value={stats.total_domains}
                  hint={t("Every domain Cat-Egory knows: added by hand, found in public top-site lists, caught by the certificate watch or suggested by the community. Most are waiting for their first scan.",
                          "Cat-Egory'nin bildiği tüm alan adları: elle eklenenler, popüler site listelerinden gelenler, sertifika izlemenin yakaladıkları ve topluluğun önerdikleri. Çoğu ilk taramasını bekliyor.")} />
            <Tile locale={locale} label={t("Categorized", "Kategorilendirilmiş")} value={stats.categorized_domains}
                  hint={t("Domains with a published category: from the AI (at least 90% sure, or two models agree), a reviewer, the community or a registry rule such as .gov.tr.",
                          "Kategorisi yayımlanmış alan adları: yapay zekâdan (en az %90 emin ya da iki model aynı fikirde), bir inceleyiciden, topluluktan veya .gov.tr gibi bir tahsis kuralından.")} />
            <Tile locale={locale} label={t("Verified", "Doğrulanmış")} value={stats.verified_domains}
                  hint={t("Categorized domains whose category a person confirmed: a reviewer, or an accepted community report.",
                          "Kategorisini bir kişinin onayladığı alan adları: bir inceleyici ya da kabul edilmiş bir topluluk bildirimi.")} />
            <Tile locale={locale} label={t("Pending reviews", "Bekleyen incelemeler")} value={stats.pending_reviews}
                  hint={t("Category questions waiting for a reviewer: the AI was unsure or two models disagreed. Until then nothing new is published for these domains.",
                          "İnceleyici bekleyen kategori soruları: yapay zekâ emin değildi ya da iki model farklı düşündü. O zamana kadar bu alan adları için yeni bir şey yayımlanmaz.")} />
            <Tile locale={locale} label={t("Threat candidates", "Tehdit adayları")} value={stats.pending_threat_candidates ?? 0}
                  hint={t("Possible look-alikes of brands and popular sites, or pages the AI thinks are dangerous, waiting for a reviewer. Nothing is flagged until a person confirms it.",
                          "Markaları ve popüler siteleri taklit ediyor olabilecek ya da yapay zekânın tehlikeli bulduğu, inceleyici bekleyen sayfalar. Bir kişi onaylamadan hiçbiri işaretlenmez.")} />
            <Tile locale={locale} label={t("Scanned today", "Bugün taranan")} value={stats.domains_scanned_today}
                  hint={t("Domains whose page was loaded and stored successfully today (Türkiye time; resets at midnight). Failed and refused scans are not counted.",
                          "Sayfası bugün başarıyla yüklenip kaydedilen alan adları (Türkiye saati; gece yarısı sıfırlanır). Başarısız ve reddedilen taramalar sayılmaz.")} />
            <Tile locale={locale} label={t("Screenshots", "Ekran görüntüleri")} value={stats.total_screenshots}
                  hint={t("Page screenshots kept so reviewers can see what the AI saw. Private: never published.",
                          "İnceleyicilerin yapay zekânın gördüğünü görebilmesi için saklanan ekran görüntüleri. Gizlidir, hiç yayımlanmaz.")} />
            <Tile locale={locale} label={t("AI valid answers (30d)", "Geçerli YZ cevabı (30 gün)")} value={rate == null ? "–" : locale === "tr" ? `%${(rate * 100).toFixed(1).replace(".", ",")}` : `${(rate * 100).toFixed(1)}%`}
                  hint={t("Share of AI answers in the last 30 days that were complete and used real category names. This is not accuracy: whether a category is right is checked by reviewers.",
                          "Son 30 günde eksiksiz olan ve gerçek kategori adlarını kullanan yapay zekâ cevaplarının oranı. Bu doğruluk değildir: kategorinin doğru olup olmadığını inceleyiciler denetler.")} />
            <Tile locale={locale} label={t("AI predictions (30d)", "YZ tahmini (30 gün)")} value={stats.ai_predictions_30d}
                  hint={t("AI answers in the last 30 days, second opinions included. One domain can have several (rescans, second model).",
                          "Son 30 gündeki yapay zekâ cevapları, ikinci görüşler dahil. Bir alan adının birden fazla cevabı olabilir (yeniden tarama, ikinci model).")} />
          </div>
          <div className="grid2 equal-panels">
            <section>
              <h2>{t("Category Distribution", "Kategori Dağılımı")} <Hint text={t("The 15 most common main categories among published domains. A domain counts once, under its main category. Domains flagged as a threat are listed under Security Threats instead.",
                                                                  "Yayımlanan alan adlarında en sık görülen 15 ana kategori. Her alan adı bir kez, ana kategorisinde sayılır. Tehdit olarak işaretlenenler bunun yerine Güvenlik Tehditleri altında yer alır.")} /></h2>
              <div className="panel">
                {stats.category_distribution.length === 0 && <p className="muted">{t("No published classifications yet.", "Henüz yayımlanmış sınıflandırma yok.")}</p>}
                {stats.category_distribution.slice(0, 15).map((d) => (
                  <div className="bar" key={d.category}>
                    <Link href={`/categories/${d.category}`}>{names[d.category] ?? d.category}</Link>
                    <div className="track"><div className="fill" style={{ width: `${(d.domains / maxDist) * 100}%` }} /></div>
                    <span className="num">{d.domains}</span>
                  </div>
                ))}
              </div>
            </section>
            <section>
              <h2>{t("Security Threats", "Güvenlik Tehditleri")} <Hint end text={t("Threats a reviewer confirmed or a licensed threat feed reports, on published domains. An AI's suspicion alone never appears here.",
                                                                       "Bir inceleyicinin onayladığı ya da lisanslı bir tehdit kaynağının bildirdiği tehditler. Yapay zekânın şüphesi tek başına burada görünmez.")} /></h2>
              <div className="panel">
                {Object.keys(stats.threats).length === 0 && Object.keys(stats.threat_urls ?? {}).length === 0 ? (
                  <p className="muted">{t("No confirmed threats yet. A threat appears here only after a reviewer confirms it or a licensed threat feed reports it; an AI's claim alone never becomes a threat flag.",
                                            "Henüz onaylanmış tehdit yok. Bir tehdit ancak bir inceleyici onayladıktan ya da lisanslı bir tehdit kaynağı bildirdikten sonra burada görünür; yapay zekânın iddiası tek başına tehdit işaretine dönüşmez.")}</p>
                ) : (
                  <table>
                    <thead><tr><th>{t("Threat", "Tehdit")}</th>
                      <th className="num">{t("Domains", "Alan adı")} <Hint end text={t("Whole domains flagged: every page on them, e.g. a look-alike of a bank.", "Tamamı işaretlenen alan adları: tüm sayfaları, örneğin bir bankanın taklidi.")} /></th>
                      <th className="num">{t("Single pages", "Tek sayfa")} <Hint end text={t("One page flagged on an otherwise legitimate site, usually a hacked one. The site itself is not flagged.", "Genel olarak meşru bir sitede işaretlenen tek bir sayfa, genelde hacklenmiş bir site. Sitenin kendisi işaretlenmez.")} /></th></tr></thead>
                    <tbody>
                      {[...new Set([...Object.keys(stats.threats), ...Object.keys(stats.threat_urls ?? {})])].map((k) => (
                        <tr key={k}><td><Link href={`/categories/${k}`}>{names[k] ?? k}</Link></td>
                          <td className="num">{stats.threats[k] ?? 0}</td><td className="num">{stats.threat_urls?.[k] ?? 0}</td></tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </section>
          </div>
          <h2>{t("Daily Scanning (Last 14 Days)", "Günlük Tarama (Son 14 Gün)")} <Hint text={t("Finished scans per day, Türkiye time. Succeeded: the page was read. Blocked: robots.txt or our safety rules refused it. Failed: the site did not answer, or our scanner had a problem (retried within hours).", "Türkiye saatine göre günlük biten taramalar. Başarılı: sayfa okundu. Engellendi: robots.txt ya da güvenlik kurallarımız izin vermedi. Başarısız: site yanıt vermedi veya tarayıcımızda bir sorun oldu (birkaç saat içinde yeniden denenir).")} /></h2>
          <div className="panel">
            {days.length === 0 ? <p className="muted">{t("No finished scans yet.", "Henüz biten tarama yok.")}</p> : (
              <table>
                <thead><tr><th>{t("Day", "Gün")}</th><th className="num">{t("Succeeded", "Başarılı")}</th><th className="num">{t("Blocked", "Engellendi")}</th><th className="num">{t("Failed", "Başarısız")}</th></tr></thead>
                <tbody>
                  {days.map(([day, counts]) => (
                    <tr key={day}><td>{day}</td><td className="num">{counts.succeeded ?? 0}</td>
                      <td className="num">{counts.blocked ?? 0}</td><td className="num">{counts.failed ?? 0}</td></tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          <p className="muted" style={{ fontSize: 13 }}>{t("Updated", "Güncelleme:")} {new Date(stats.generated_at).toLocaleString(intl(locale), { timeZone: "Europe/Istanbul" })} (GMT+3)</p>
        </>
      )}
    </>
  );
}
