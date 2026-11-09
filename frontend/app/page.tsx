import { api } from "@/lib/api";
import type { Category, Stats } from "@/lib/types";
import SearchBox from "@/components/SearchBox";
import Mascot from "@/components/Mascot";
import Link from "next/link";

export const dynamic = "force-dynamic";

function Tile({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="panel tile">
      <div className="label">{label}</div>
      <div className="value">{typeof value === "number" ? value.toLocaleString("en") : value}</div>
    </div>
  );
}

export default async function Dashboard() {
  let stats: Stats | null = null;
  let names: Record<string, string> = {};
  try {
    const [s, c] = await Promise.all([api<Stats>("/api/v1/stats", 30), api<{ items: Category[] }>("/api/v1/categories", 3600)]);
    stats = s;
    names = Object.fromEntries(c.items.map((x) => [x.id, x.name]));
  } catch {
    stats = null;
  }
  const rate = stats?.ai_valid_answer_rate_30d;
  const maxDist = Math.max(1, ...(stats?.category_distribution ?? []).map((d) => d.domains));
  const days = Object.entries(stats?.daily_scans ?? {}).sort(([a], [b]) => (a < b ? 1 : -1));
  return (
    <>
      <section className="hero">
        <div>
          <h1>Every Website, <em>Curiously</em> Categorized.</h1>
          <p className="lead">Look up how a website is categorized, download category lists, or help correct the data.</p>
          <SearchBox />
        </div>
        <Mascot />
      </section>
      <section className="why" aria-labelledby="why-title">
        <h2 id="why-title">Why Cat-Egory?</h2>
        <div className="why-cards">
          <div className="panel"><h3>Open Data</h3>
            <p className="muted">Categories you can download, check and build on, under CC BY 4.0.</p></div>
          <div className="panel"><h3>Checked by People</h3>
            <p className="muted">The AI reads first; unsure answers, look-alikes and threats are decided by a person.</p></div>
          <div className="panel"><h3>Every Label Has a Source</h3>
            <p className="muted">See whether a category came from the AI, a reviewer, the community or a rule.</p></div>
        </div>
        <p><Link href="/about">About Us →</Link></p>
      </section>
      {!stats ? (
        <div className="notice error">The API is not reachable right now.</div>
      ) : (
        <>
          <h2>Overview</h2>
          <div className="tiles">
            <Tile label="Total domains" value={stats.total_domains} />
            <Tile label="Categorized" value={stats.categorized_domains} />
            <Tile label="Verified" value={stats.verified_domains} />
            <Tile label="Pending reviews" value={stats.pending_reviews} />
            <Tile label="Scanned today" value={stats.domains_scanned_today} />
            <Tile label="Screenshots" value={stats.total_screenshots} />
            <Tile label="AI valid answers (30d)" value={rate == null ? "–" : `${(rate * 100).toFixed(1)}%`} />
            <Tile label="AI predictions (30d)" value={stats.ai_predictions_30d} />
          </div>
          <div className="grid2">
            <section>
              <h2>Category Distribution</h2>
              <div className="panel">
                {stats.category_distribution.length === 0 && <p className="muted">No published classifications yet.</p>}
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
              <h2>Security Threats</h2>
              <div className="panel">
                {Object.keys(stats.threats).length === 0 && Object.keys(stats.threat_urls ?? {}).length === 0 ? (
                  <p className="muted">No confirmed threats yet. A threat appears here only after a reviewer confirms it or
                    a licensed threat feed reports it; an AI&apos;s claim alone never becomes a threat flag.</p>
                ) : (
                  <table>
                    <thead><tr><th>Threat</th><th className="num">Domains</th><th className="num">Single pages</th></tr></thead>
                    <tbody>
                      {[...new Set([...Object.keys(stats.threats), ...Object.keys(stats.threat_urls ?? {})])].map((t) => (
                        <tr key={t}><td><Link href={`/categories/${t}`}>{names[t] ?? t}</Link></td>
                          <td className="num">{stats.threats[t] ?? 0}</td><td className="num">{stats.threat_urls?.[t] ?? 0}</td></tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </section>
          </div>
          <h2>Daily Scanning (Last 14 Days)</h2>
          <div className="panel">
            {days.length === 0 ? <p className="muted">No finished scans yet.</p> : (
              <table>
                <thead><tr><th>Day</th><th className="num">Succeeded</th><th className="num">Blocked</th><th className="num">Failed</th></tr></thead>
                <tbody>
                  {days.map(([day, counts]) => (
                    <tr key={day}><td>{day}</td><td className="num">{counts.succeeded ?? 0}</td>
                      <td className="num">{counts.blocked ?? 0}</td><td className="num">{counts.failed ?? 0}</td></tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          <p className="muted" style={{ fontSize: 13 }}>Updated {new Date(stats.generated_at).toLocaleString("en")}</p>
        </>
      )}
    </>
  );
}
