import { api } from "@/lib/api";
import type { Category, Stats } from "@/lib/types";
import SearchBox from "@/components/SearchBox";
import Mascot from "@/components/Mascot";
import Link from "next/link";
import Hint from "@/components/Hint";

export const dynamic = "force-dynamic";

function Tile({ label, value, hint }: { label: string; value: string | number; hint: string }) {
  return (
    <div className="panel tile">
      <div className="label">{label} <Hint text={hint} /></div>
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
            <Tile label="Total domains" value={stats.total_domains}
                  hint="Every domain Cat-Egory knows: added by hand, found in public top-site lists, caught by the certificate watch or suggested by the community. Most are waiting for their first scan." />
            <Tile label="Categorized" value={stats.categorized_domains}
                  hint="Domains with a published category: from the AI (at least 90% sure, or two models agree), a reviewer, the community or a registry rule such as .gov.tr." />
            <Tile label="Verified" value={stats.verified_domains}
                  hint="Categorized domains whose category a person confirmed: a reviewer, or an accepted community report." />
            <Tile label="Pending reviews" value={stats.pending_reviews}
                  hint="Category questions waiting for a reviewer: the AI was unsure or two models disagreed. Until then nothing new is published for these domains." />
            <Tile label="Threat candidates" value={stats.pending_threat_candidates ?? 0}
                  hint="Possible look-alikes of brands and popular sites, or pages the AI thinks are dangerous, waiting for a reviewer. Nothing is flagged until a person confirms it." />
            <Tile label="Scanned today" value={stats.domains_scanned_today}
                  hint="Domains whose page was loaded and stored successfully today (UTC day; resets at 03:00 Türkiye time). Failed and refused scans are not counted." />
            <Tile label="Screenshots" value={stats.total_screenshots}
                  hint="Page screenshots kept so reviewers can see what the AI saw. Private: never published." />
            <Tile label="AI valid answers (30d)" value={rate == null ? "–" : `${(rate * 100).toFixed(1)}%`}
                  hint="Share of AI answers in the last 30 days that were complete and used real category names. This is not accuracy: whether a category is right is checked by reviewers." />
            <Tile label="AI predictions (30d)" value={stats.ai_predictions_30d}
                  hint="AI answers in the last 30 days, second opinions included. One domain can have several (rescans, second model)." />
          </div>
          <div className="grid2 equal-panels">
            <section>
              <h2>Category Distribution <Hint text="The 15 most common main categories among published domains. A domain counts once, under its main category." /></h2>
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
              <h2>Security Threats <Hint end text="Threats a reviewer confirmed or a licensed threat feed reports, on published domains. An AI's suspicion alone never appears here." /></h2>
              <div className="panel">
                {Object.keys(stats.threats).length === 0 && Object.keys(stats.threat_urls ?? {}).length === 0 ? (
                  <p className="muted">No confirmed threats yet. A threat appears here only after a reviewer confirms it or
                    a licensed threat feed reports it; an AI&apos;s claim alone never becomes a threat flag.</p>
                ) : (
                  <table>
                    <thead><tr><th>Threat</th>
                      <th className="num">Domains <Hint end text="Whole domains flagged: every page on them, e.g. a look-alike of a bank." /></th>
                      <th className="num">Single pages <Hint end text="One page flagged on an otherwise legitimate site, usually a hacked one. The site itself is not flagged." /></th></tr></thead>
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
          <h2>Daily Scanning (Last 14 Days) <Hint text="Finished scans per UTC day. Succeeded: the page was read. Blocked: robots.txt or our safety rules refused it. Failed: the site did not answer, or our scanner had a problem (retried within hours)." /></h2>
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
