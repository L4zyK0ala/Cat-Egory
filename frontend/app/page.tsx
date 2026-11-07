import { api } from "@/lib/api";
import type { Category, Stats } from "@/lib/types";
import SearchBox from "@/components/SearchBox";
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
      <h1>Open web categorization</h1>
      <p className="muted">Look up how a website is categorized, download category lists, or help correct the data.</p>
      <SearchBox />
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
              <h2>Category distribution</h2>
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
              <h2>Security threats</h2>
              <div className="panel">
                {Object.keys(stats.threats).length === 0 ? <p className="muted">No active threat records.</p> : (
                  <table><tbody>
                    {Object.entries(stats.threats).map(([t, n]) => (
                      <tr key={t}><td><Link href={`/categories/${t}`}>{names[t] ?? t}</Link></td><td className="num">{n}</td></tr>
                    ))}
                  </tbody></table>
                )}
              </div>
            </section>
          </div>
          <h2>Daily scanning (last 14 days)</h2>
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
