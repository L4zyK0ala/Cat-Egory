import Link from "next/link";
import { api, ApiError, UNCACHED, publicApiUrl } from "@/lib/api";
import type { Category, DomainInfo, HistoryItem, Risk, UrlLookup } from "@/lib/types";
import SearchBox from "@/components/SearchBox";
import ReportForm from "@/components/ReportForm";
import { categoryName, intl, type Locale, type T } from "@/lib/i18n";
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
  return value ? new Date(value).toLocaleString(intl(locale), { dateStyle: "medium", timeStyle: "short", timeZone: "Europe/Istanbul" }) : "–";
}

function flagged(locale: Locale, risk: Risk) {
  return Object.keys(RISK_EN).filter((k) => risk[k as keyof Risk] === true)
    .map((k) => [k, categoryName(locale, RISK_IDS[k], RISK_EN[k])] as const);
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

export default async function DomainPage({ params, searchParams }: {
  params: Promise<{ domain: string }>; searchParams: Promise<{ url?: string | string[] }>;
}) {
  const { domain } = await params;
  const { locale, t } = await getT();
  let name = domain;
  try {
    name = decodeURIComponent(domain);
  } catch {
    /* a broken %-escape: the API will refuse the name as it is */
  }
  const link = (v => (Array.isArray(v) ? v[0] : v))((await searchParams).url)?.slice(0, 2000);
  // A full link was searched: also check that exact page (a phishing page can sit on an otherwise fine site).
  const lookup = link
    ? await api<UrlLookup>(`/api/v1/url?url=${encodeURIComponent(link)}`, UNCACHED).catch(() => null)
    : null;
  let info: DomainInfo | null = null;
  let error: ApiError | null = null;
  let viaParent = false;
  try {
    info = await api<DomainInfo>(`/api/v1/domain/${encodeURIComponent(name)}`, UNCACHED);
  } catch (e) {
    error = e instanceof ApiError ? e : new ApiError(0, "network", "API unreachable");
  }
  if (!info && error?.status === 404) {
    // e.g. www.example.com is not stored but example.com is: show the registrable domain, and say so.
    try {
      const r = await api<{ match: string; result: DomainInfo }>(`/api/v1/url?url=${encodeURIComponent(`http://${name}/`)}`, UNCACHED);
      if (r.match === "registrable_domain") {
        info = r.result;
        viaParent = true;
      }
    } catch {
      /* keep the 404 */
    }
  }
  if (!info) {
    return (
      <>
        <SearchBox initial={name} />
        <h1>{name}</h1>
        <div className={`notice ${error?.status === 404 ? "" : "error"}`}>
          {error?.status === 404 ? <>{t("This domain is not in the database yet.", "Bu alan adı henüz veri tabanında yok.")}{" "}
            <Link href={`/submit?domain=${encodeURIComponent(name)}`}>{t("Suggest it", "Önerin")}</Link>.</>
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
  return (
    <>
      <SearchBox initial={info.display_domain} />
      <div className="row"><h1>{info.display_domain}</h1>
        <span className={`badge ${info.classification_status}`}>{statusLabel(t, info.classification_status)}</span></div>
      {info.display_domain !== info.domain && <div className="mono muted">{info.domain}</div>}
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
          <p className="muted" style={{ fontSize: 13 }}>
            {t("Source", "Kaynak")}: {info.data_source ?? "–"}. {t("Percentages are model confidence, not measured accuracy.",
                                                                       "Yüzdeler model güvenidir, ölçülmüş doğruluk değildir.")}
          </p>
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
