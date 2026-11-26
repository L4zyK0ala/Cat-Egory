import type { Metadata } from "next";
import Link from "next/link";
import { cache } from "react";
import { headers } from "next/headers";
import { api, ApiError, UNCACHED, publicApiUrl } from "@/lib/api";
import type { Category, DomainInfo, HistoryItem, Risk, UrlLookup } from "@/lib/types";
import SearchBox from "@/components/SearchBox";
import ReportForm from "@/components/ReportForm";
import { categoryName, formatWhen, type Locale, type T } from "@/lib/i18n";
import { getT } from "@/lib/locale";

export const dynamic = "force-dynamic";

const RISK_IDS: Record<string, string> = {
  malware: "malware", phishing: "phishing", scam: "scam", fraud: "fraud", command_and_control: "command-and-control",
  malicious_downloads: "malicious-downloads", suspicious: "suspicious", lookalike_domain: "lookalike-domain",
};
const RISK_EN: Record<string, string> = {
  malware: "Malware", phishing: "Phishing", scam: "Scam", fraud: "Fraud", command_and_control: "Command & control",
  malicious_downloads: "Malicious downloads", suspicious: "Suspicious", lookalike_domain: "Look-alike domain",
};

function when(locale: Locale, value: string | null) {
  return formatWhen(locale, value);
}

/** Who stands behind a label, in words: the slug of a data source, or an origin when the source is unknown. */
function sourceLabel(t: T, source: string | null, origin?: string): string {
  const labels: Record<string, string> = {
    "cat-egory-review": t("Confirmed by a reviewer", "İnceleyici onayladı"),
    "cat-egory-ai": t("AI estimate", "Yapay zekâ tahmini"),
    "cat-egory-registry-rules": t("Registry rule", "Tahsis kuralı"),
    octryx: t("Proven on the page (Octryx)", "Sayfada kanıtlandı (Octryx)"),
    "cat-egory-detector": t("Proven on the page", "Sayfada kanıtlandı"),
  };
  if (source && labels[source]) return labels[source];
  if (origin === "community") return t("Community report", "Topluluk bildirimi");
  if (origin === "human") return labels["cat-egory-review"];
  return source ? t(`Threat feed: ${source}`, `Tehdit kaynağı: ${source}`) : "–";
}

/** "İnceleyici onayladı · 15 Kas 2026": where a label came from and since when. A person's decision wears a tick. */
function Stamp({ t, locale, label, since, person }: { t: T; locale: Locale; label: string; since: string | null; person: boolean }) {
  return (
    <span className={`stamp ${person ? "person" : ""}`}>
      {person && <svg aria-hidden="true" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m5 12 5 5 9-10" /></svg>}
      {label}{since && <span className="since">{formatWhen(locale, since, false)}</span>}
    </span>
  );
}

function flagged(locale: Locale, risk: Risk) {
  return Object.keys(RISK_EN).filter((k) => risk[k as keyof Risk] === true)
    .map((k) => [k, categoryName(locale, RISK_IDS[k], RISK_EN[k])] as const);
}

const DOWNLOADS = new Set(["malware", "malicious_downloads", "command_and_control"]);

/** First thing on the page when the site or the searched link is a confirmed threat: most visitors arrive with a
 *  link from an SMS or a mail and ask "is this safe?". What not to do, what to do if it already happened, where to
 *  report it. The listed name is never a link out (see tests/unit/test_frontend_links.py). */
function ThreatWarning({ t, locale, risks, link, sources }: {
  t: T; locale: Locale; risks: readonly (readonly [string, string])[]; link: boolean; sources: Risk["sources"];
}) {
  const labels = risks.map(([, label]) => label).join(", ");
  const files = risks.some(([k]) => DOWNLOADS.has(k));
  const kinds = new Set(risks.map(([k]) => k));
  const backing = sources.filter((s) => kinds.has(s.type)).sort((a, b) => a.first_seen.localeCompare(b.first_seen));
  const who = [...new Set(backing.map((s) => sourceLabel(t, s.source)))].join(" · ");
  return (
    <div className="threat-warning" role="alert">
      <svg className="icon" aria-hidden="true" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor"
           strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 4 2.5 20h19L12 4Z" /><path d="M12 10v4M12 17h.01" /></svg>
      <div>
      <strong>{t("Dangerous", "Tehlikeli")}: {labels.toLocaleLowerCase(locale === "tr" ? "tr-TR" : "en-GB")}</strong>
      <p><b>{link ? t("Do not open this link.", "Bu bağlantıyı açmayın.") : t("Do not visit this site.", "Bu siteye girmeyin.")}</b>{" "}
        {link ? t(`This link is confirmed as: ${labels}.`, `Bu bağlantı şu olarak onaylandı: ${labels}.`)
               : t(`This site is confirmed as: ${labels}.`, `Bu site şu olarak onaylandı: ${labels}.`)}{" "}
        {t("Do not enter a password, card number, ID number or verification code there, and do not download anything from it.",
           "Orada şifre, kart numarası, kimlik numarası ya da doğrulama kodu girmeyin; oradan bir şey indirmeyin.")}</p>
      <p>{t("If you already did: change that password, and the same password everywhere else you use it; if you gave card or bank details, call your bank now.",
            "Girdiyseniz: o şifreyi ve aynı şifreyi kullandığınız diğer hesaplardaki şifreleri hemen değiştirin; kart ya da banka bilgisi verdiyseniz bankanızı hemen arayın.")}
        {files && <>{" "}{t("If you downloaded a file, do not open it, and scan the device with security software.",
                            "Bir dosya indirdiyseniz açmayın ve cihazı bir güvenlik yazılımıyla tarayın.")}</>}</p>
      <p className="muted">{t("You can report it to the national cyber security centre through the İhbar Formu on ",
                              "Ulusal siber güvenlik merkezine (USOM) bildirmek için ")}
        <a href="https://siberguvenlik.gov.tr" target="_blank" rel="noopener noreferrer">siberguvenlik.gov.tr</a>
        {t(".", " üzerindeki İhbar Formu'nu kullanabilirsiniz.")}</p>
      {backing.length > 0 && <p className="stamp-line">{who} · {formatWhen(locale, backing[0].first_seen, false)}</p>}
      </div>
    </div>
  );
}

function originLabel(t: T, origin: string) {
  const labels: Record<string, string> = { human: t("human", "insan"), ai: t("AI", "yapay zekâ"),
    community: t("community", "topluluk"), rule: t("rule", "kural"), source: t("source", "kaynak") };
  return labels[origin] ?? origin;
}

function statusLabel(t: T, status: string) {
  const labels: Record<string, string> = {
    verified: t("verified", "doğrulandı"), classified: t("classified", "sınıflandırıldı"),
    needs_review: t("needs review", "incelemede"), reanalyze: t("reanalyse", "yeniden incelenecek"),
    unclassified: t("unclassified", "sınıflandırılmadı"), unreachable: t("unreachable", "erişilemiyor"),
    disputed: t("disputed", "itiraz edildi"),
  };
  return labels[status] ?? status.replace("_", " ");
}

type Found = { info: DomainInfo | null; error: ApiError | null; viaParent: boolean };

/** The domain as the page shows it. Wrapped in React's cache: the page and its metadata (title, description for
 *  search engines) ask once per request, so the visitor is counted once and the API is called once. */
const findDomain = cache(async (name: string, visitor: string | null): Promise<Found> => {
  try {
    return { info: await api<DomainInfo>(`/api/v1/domain/${encodeURIComponent(name)}`, UNCACHED, { visitor }),
             error: null, viaParent: false };
  } catch (e) {
    const error = e instanceof ApiError ? e : new ApiError(0, "network", "API unreachable");
    if (error.status === 404) {
      // e.g. www.example.com is not stored but example.com is: show the registrable domain, and say so.
      try {
        const r = await api<{ match: string; result: DomainInfo }>(`/api/v1/url?url=${encodeURIComponent(`http://${name}/`)}`,
          UNCACHED, { secondary: true });
        if (r.match === "registrable_domain") return { info: r.result, error: null, viaParent: true };
      } catch {
        /* keep the 404 */
      }
    }
    return { info: null, error, viaParent: false };
  }
});

function decoded(domain: string): string {
  try {
    return decodeURIComponent(domain);
  } catch {
    return domain; // a broken %-escape: the API will refuse the name as it is
  }
}

/** What a search result shows: the domain, its main category and whether it is flagged. Only classified and
 *  verified domains may be indexed; unknown, pending and borrowed (www.x -> x) pages are not worth a search hit. */
export async function generateMetadata({ params }: { params: Promise<{ domain: string }> }): Promise<Metadata> {
  const name = decoded((await params).domain);
  const { locale, t } = await getT();
  const { info, viaParent } = await findDomain(name, (await headers()).get("cf-connecting-ip"));
  if (!info) return { title: name, robots: { index: false, follow: true } };
  const main = info.categories.find((c) => c.primary) ?? info.categories[0];
  const category = main ? categoryName(locale, main.id, main.name) : t("not categorised yet", "henüz kategorisi yok");
  const risks = flagged(locale, info.risk).map(([, label]) => label);
  const shown = info.display_domain;
  const title = risks.length
    ? t(`${shown}: ${risks.join(", ")} warning`, `${shown}: ${risks.join(", ")} uyarısı`)
    : t(`${shown}: ${category} — website category`, `${shown}: ${category} — site kategorisi`);
  const others = info.categories.filter((c) => c !== main).map((c) => categoryName(locale, c.id, c.name));
  const description = risks.length
    ? t(`${shown} is flagged in Cat-Egory as ${risks.join(", ")}. Main category: ${category}. Open data, free DNS lists.`,
        `${shown}, Cat-Egory'de ${risks.join(", ")} olarak işaretli. Ana kategori: ${category}. Açık veri, ücretsiz DNS listeleri.`)
    : t(`${shown} is categorised as ${category}${others.length ? ` (also ${others.join(", ")})` : ""}. `
        + "No security warning. Open, free website category data and DNS block lists.",
        `${shown} sitesinin kategorisi: ${category}${others.length ? ` (ayrıca ${others.join(", ")})` : ""}. `
        + "Güvenlik uyarısı yok. Açık ve ücretsiz site kategorisi verisi, DNS engelleme listeleri.");
  const indexable = !viaParent && (info.classification_status === "classified" || info.classification_status === "verified");
  return {
    title: { absolute: `${title} · Cat-Egory` },
    description,
    alternates: { canonical: `/domain/${info.domain}` },
    robots: { index: indexable, follow: true },
    openGraph: { title, description, type: "website" },
  };
}

export default async function DomainPage({ params, searchParams }: {
  params: Promise<{ domain: string }>; searchParams: Promise<{ url?: string | string[] }>;
}) {
  const name = decoded((await params).domain);
  const { locale, t } = await getT();
  const link = (v => (Array.isArray(v) ? v[0] : v))((await searchParams).url)?.slice(0, 2000);
  // A full link was searched: also check that exact page (a phishing page can sit on an otherwise fine site).
  // Set by the Cloudflare tunnel only; absent when the site is opened on the home network.
  const visitor = (await headers()).get("cf-connecting-ip");
  const lookup = link
    ? await api<UrlLookup>(`/api/v1/url?url=${encodeURIComponent(link)}`, UNCACHED, { secondary: true }).catch(() => null)
    : null;
  const { info, error, viaParent } = await findDomain(name, visitor);
  if (!info) {
    return (
      <>
        <SearchBox initial={name} />
        <h1>{name}</h1>
        <div className={`notice ${error?.status === 404 ? "" : "error"}`}>
          {error?.status === 404 ? <>{t("This domain is not in the database yet. Sites are not scanned on the spot: ",
                                      "Bu alan adı henüz veri tabanında yok. Siteler anında taranmaz: ")}
            <Link href={`/submit?domain=${encodeURIComponent(name)}`}>{t("suggest it", "önerebilirsiniz")}</Link>
            {t(", and a site several different visitors look up on the same day is queued for a scan the next day by itself.",
               "; aynı gün birkaç farklı ziyaretçinin aradığı siteler de ertesi gün kendiliğinden tarama sırasına alınır.")}</>
            : error?.status === 422 ? `${t("Not a valid public domain", "Geçerli bir alan adı değil")}: ${error.message}`
            : t("The API is not reachable right now.", "API'ye şu an ulaşılamıyor.")}
        </div>
      </>
    );
  }
  const [history, taxonomy] = await Promise.all([
    api<{ history: HistoryItem[] }>(`/api/v1/history/${encodeURIComponent(info.domain)}`, UNCACHED).catch(() => ({ history: [] })),
    api<{ items: Category[] }>("/api/v1/categories", 3600).catch(() => ({ items: [] as Category[] })),
  ]);
  const names = Object.fromEntries(taxonomy.items.map((c) => [c.id, categoryName(locale, c.id, c.name)]));
  const risks = flagged(locale, info.risk);
  const pick = (kind: Category["kind"]) => taxonomy.items
    .filter((c) => c.kind === kind && !c.is_group && !c.deprecated_in).map((c) => ({ id: c.id, name: categoryName(locale, c.id, c.name) }));
  const linkRisks = lookup ? flagged(locale, lookup.url_risk) : [];
  const main = info.categories.find((c) => c.primary);
  const mainSince = history.history.find((h) => h.primary && !h.valid_to && h.category === main?.id)?.valid_from ?? info.last_updated;
  return (
    <>
      <SearchBox initial={info.display_domain} />
      <div className="row"><h1>{info.display_domain}</h1>
        <span className={`badge ${info.classification_status}`}>{statusLabel(t, info.classification_status)}</span></div>
      {info.display_domain !== info.domain && <div className="mono muted">{info.domain}</div>}
      {(risks.length > 0 || linkRisks.length > 0) &&
        <ThreatWarning t={t} locale={locale} risks={linkRisks.length ? linkRisks : risks} link={linkRisks.length > 0}
                       sources={linkRisks.length && lookup ? lookup.url_risk.sources : info.risk.sources} />}
      {viaParent && <div className="notice">{t(`${name} is not listed itself; showing its registrable domain ${info.domain}.`,
                                             `${name} kendisi listede yok; kayıtlı üst alan adı ${info.domain} gösteriliyor.`)}</div>}
      {lookup && (
        <div className={`notice ${linkRisks.length ? "error" : ""}`}>
          <div className="mono" style={{ overflowWrap: "anywhere", fontSize: 13 }}>{lookup.normalized_url}</div>
          {linkRisks.length ? <>{t("This exact link is confirmed as:", "Bu bağlantı şu olarak onaylandı:")}{" "}
            {linkRisks.map(([k, label]) => <span key={k} className="badge danger" style={{ marginRight: 6 }}>{label}</span>)}</>
            : t("No confirmed threat report for this exact link. The site's own result is below.",
                "Bu bağlantı için onaylanmış bir tehdit kaydı yok. Sitenin kendi sonucu aşağıda.")}
        </div>
      )}
      <div className="grid2" style={{ marginTop: 16 }}>
        <section className="panel">
          <h2 style={{ marginTop: 0 }}>{t("Categories", "Kategoriler")}</h2>
          {info.categories.length === 0 ? <p className="muted">{t("Not classified yet.", "Henüz sınıflandırılmadı.")}</p> : info.categories.map((c) => (
            <span key={c.id} className={`chip ${c.primary ? "primary" : ""}`} title={`${t("origin", "kaynak")}: ${c.origin}`}>
              <Link href={`/categories/${c.id}`}>{categoryName(locale, c.id, c.name)}</Link>{c.confidence != null && ` · ${locale === "tr" ? "%" : ""}${(c.confidence * 100).toFixed(0)}${locale === "tr" ? "" : "%"}`}
            </span>
          ))}
          {main && <Stamp t={t} locale={locale} since={mainSince}
                          label={sourceLabel(t, info.data_source, main.origin)}
                          person={info.data_source === "cat-egory-review" || main.origin === "human"} />}
          {info.categories.some((c) => c.confidence != null) &&
            <p className="muted" style={{ fontSize: 13 }}>{t("Percentages are model confidence, not measured accuracy.",
                                                              "Yüzdeler model güvenidir, ölçülmüş doğruluk değildir.")}</p>}
          <h2>{t("Security", "Güvenlik")}</h2>
          {risks.length === 0 ? <p className="muted">{t("No confirmed threats for this domain (from reviewers or licensed threat feeds; AI claims are not shown as threats).",
                                                        "Bu alan adı için onaylanmış tehdit yok (inceleyicilerden veya lisanslı tehdit kaynaklarından; yapay zekâ iddiaları tehdit olarak gösterilmez).")}</p> :
            risks.map(([k, label]) => <span key={k} className="badge danger" style={{ marginRight: 6 }}>{label}</span>)}
          {info.risk.reported_urls > 0 && (
            <p className="notice" style={{ fontSize: 13 }}>
              {locale === "tr"
                ? `Bu sitede ${info.risk.reported_urls === 1 ? "bir sayfa" : `${info.risk.reported_urls} sayfa`} tehdit olarak onaylandı. Sitenin kendisi işaretli değil; bu genelde sitenin hacklendiği anlamına gelir. O sayfayı denetlemek için tam bağlantıyı arama kutusuna yapıştırın.`
                : `${info.risk.reported_urls === 1 ? "One page" : `${info.risk.reported_urls} pages`} on this site ${info.risk.reported_urls === 1 ? "is" : "are"} confirmed as a threat. The site itself is not flagged; this usually means it was hacked. Paste a full link into the search box to check that page.`}
            </p>
          )}
        </section>
        <section className="panel">
          <table><tbody>
            <tr><th>{t("Registrable domain", "Kayıtlı alan adı")}</th><td className="mono">{info.registrable_domain}</td></tr>
            <tr><th>{t("First seen", "İlk görülme")}</th><td>{when(locale, info.first_seen)}</td></tr>
            <tr><th>{t("Last scanned", "Son tarama")}</th><td>{when(locale, info.last_scanned)}</td></tr>
            <tr><th>{t("Last updated", "Son güncelleme")}</th><td>{when(locale, info.last_updated)}</td></tr>
            <tr><th>{t("Taxonomy", "Taksonomi")}</th><td>{info.taxonomy_version}</td></tr>
            <tr><th>{t("Licence", "Lisans")}</th><td>{info.license} · {info.attribution}</td></tr>
          </tbody></table>
        </section>
      </div>
      <h2>{t("History", "Geçmiş")}</h2>
      <div className="panel">
        {history.history.length === 0 ? <p className="muted">{t("No category changes recorded.", "Kayıtlı kategori değişikliği yok.")}</p> : (
          <table>
            <thead><tr><th>{t("Category", "Kategori")}</th><th>{t("Role", "Rol")}</th><th>{t("Origin", "Kaynak")}</th>
              <th>{t("From", "Başlangıç")}</th><th>{t("Until", "Bitiş")}</th></tr></thead>
            <tbody>{history.history.map((h, i) => (
              <tr key={i}><td>{names[h.category] ?? h.category}</td><td>{h.primary ? t("primary", "ana") : t("secondary", "yan")}</td>
                <td>{originLabel(t, h.origin)}</td><td>{when(locale, h.valid_from)}</td><td>{h.valid_to ? when(locale, h.valid_to) : t("current", "güncel")}</td></tr>
            ))}</tbody>
          </table>
        )}
      </div>
      <h2>{t("Is Something Wrong?", "Bir Yanlışlık mı Var?")}</h2>
      <ReportForm api={publicApiUrl()} domain={info.domain} current={info.categories.map((c) => c.id)}
                  categories={pick("content")} risks={pick("risk")}
                  currentRisks={[...new Set(info.risk.sources.map((s) => s.type))]} />
    </>
  );
}
