import Link from "next/link";
import { api, ApiError, publicApiUrl } from "@/lib/api";
import type { Category, DomainInfo, HistoryItem } from "@/lib/types";
import SearchBox from "@/components/SearchBox";
import ReportForm from "@/components/ReportForm";

export const dynamic = "force-dynamic";

const RISK_LABELS: Record<string, string> = {
  malware: "Malware", phishing: "Phishing", scam: "Scam", fraud: "Fraud", command_and_control: "Command & control",
  malicious_downloads: "Malicious downloads", suspicious: "Suspicious",
};

function when(value: string | null) {
  return value ? new Date(value).toLocaleString("en", { dateStyle: "medium", timeStyle: "short" }) : "–";
}

export default async function DomainPage({ params }: { params: Promise<{ domain: string }> }) {
  const { domain } = await params;
  const name = decodeURIComponent(domain);
  let info: DomainInfo | null = null;
  let error: ApiError | null = null;
  let viaParent = false;
  try {
    info = await api<DomainInfo>(`/api/v1/domain/${encodeURIComponent(name)}`, 60);
  } catch (e) {
    error = e instanceof ApiError ? e : new ApiError(0, "network", "API unreachable");
  }
  if (!info && error?.status === 404) {
    // e.g. www.example.com is not stored but example.com is: show the registrable domain, and say so.
    try {
      const r = await api<{ match: string; result: DomainInfo }>(`/api/v1/url?url=${encodeURIComponent(`http://${name}/`)}`, 60);
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
          {error?.status === 404 ? <>This domain is not in the database yet. <Link href={`/submit?domain=${encodeURIComponent(name)}`}>Suggest it</Link>.</>
            : error?.status === 422 ? `Not a valid public domain: ${error.message}` : "The API is not reachable right now."}
        </div>
      </>
    );
  }
  const [history, taxonomy] = await Promise.all([
    api<{ history: HistoryItem[] }>(`/api/v1/history/${encodeURIComponent(info.domain)}`, 60).catch(() => ({ history: [] })),
    api<{ items: Category[] }>("/api/v1/categories?kind=content", 3600).catch(() => ({ items: [] as Category[] })),
  ]);
  const names = Object.fromEntries(taxonomy.items.map((c) => [c.id, c.name]));
  const risks = Object.entries(RISK_LABELS).filter(([k]) => info!.risk[k as keyof typeof info.risk] === true);
  return (
    <>
      <SearchBox initial={info.display_domain} />
      <div className="row"><h1>{info.display_domain}</h1>
        <span className={`badge ${info.classification_status}`}>{info.classification_status.replace("_", " ")}</span></div>
      {info.display_domain !== info.domain && <div className="mono muted">{info.domain}</div>}
      {viaParent && <div className="notice">{name} is not listed itself; showing its registrable domain {info.domain}.</div>}
      <div className="grid2" style={{ marginTop: 16 }}>
        <section className="panel">
          <h2 style={{ marginTop: 0 }}>Categories</h2>
          {info.categories.length === 0 ? <p className="muted">Not classified yet.</p> : info.categories.map((c) => (
            <span key={c.id} className={`chip ${c.primary ? "primary" : ""}`} title={`origin: ${c.origin}`}>
              <Link href={`/categories/${c.id}`}>{c.name}</Link>{c.confidence != null && ` · ${(c.confidence * 100).toFixed(0)}%`}
            </span>
          ))}
          <p className="muted" style={{ fontSize: 13 }}>
            Source: {info.data_source ?? "–"}. Percentages are model confidence, not measured accuracy.
          </p>
          <h2>Security</h2>
          {risks.length === 0 ? <p className="muted">No active threat records.</p> :
            risks.map(([k, label]) => <span key={k} className="badge danger" style={{ marginRight: 6 }}>{label}</span>)}
        </section>
        <section className="panel">
          <table><tbody>
            <tr><th>Registrable domain</th><td className="mono">{info.registrable_domain}</td></tr>
            <tr><th>First seen</th><td>{when(info.first_seen)}</td></tr>
            <tr><th>Last scanned</th><td>{when(info.last_scanned)}</td></tr>
            <tr><th>Last updated</th><td>{when(info.last_updated)}</td></tr>
            <tr><th>Taxonomy</th><td>{info.taxonomy_version}</td></tr>
            <tr><th>Licence</th><td>{info.license} · {info.attribution}</td></tr>
          </tbody></table>
        </section>
      </div>
      <h2>History</h2>
      <div className="panel">
        {history.history.length === 0 ? <p className="muted">No category changes recorded.</p> : (
          <table>
            <thead><tr><th>Category</th><th>Role</th><th>Origin</th><th>From</th><th>Until</th></tr></thead>
            <tbody>{history.history.map((h, i) => (
              <tr key={i}><td>{names[h.category] ?? h.category}</td><td>{h.primary ? "primary" : "secondary"}</td>
                <td>{h.origin}</td><td>{when(h.valid_from)}</td><td>{h.valid_to ? when(h.valid_to) : "current"}</td></tr>
            ))}</tbody>
          </table>
        )}
      </div>
      <h2>Is something wrong?</h2>
      <ReportForm api={publicApiUrl()} domain={info.domain} current={info.categories.map((c) => c.id)}
                  categories={taxonomy.items.filter((c) => !c.is_group && !c.deprecated_in).map((c) => ({ id: c.id, name: c.name }))} />
    </>
  );
}
