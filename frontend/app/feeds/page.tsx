import { api, publicApiUrl } from "@/lib/api";
import type { Feed } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function Feeds() {
  let feeds: Feed[] | null = null;
  try {
    feeds = await api<Feed[]>("/api/v1/feeds", 300);
  } catch {
    feeds = null;
  }
  const base = publicApiUrl();
  const shown = (feeds ?? []).filter((f) => f.records > 0 || f.name.startsWith("all.") || f.name === "security.rpz");
  return (
    <>
      <h1>Feeds</h1>
      <p className="muted">
        Ready-to-use lists, rebuilt hourly. <span className="mono">.txt</span> = one domain per line,{" "}
        <span className="mono">.hosts</span> = hosts-file / Pi-hole, <span className="mono">.rpz</span> = DNS Response Policy
        Zone (BIND, Unbound, PowerDNS). <span className="mono">{base}/api/v1/feeds/rpz</span> blocks all security threats.
        Each file states its version, date and licence (CC BY 4.0 — please credit &quot;Cat-egory contributors&quot;).
      </p>
      {feeds === null ? <div className="notice error">The API is not reachable right now.</div> : shown.length === 0 ?
        <div className="notice">No feeds have been built yet.</div> : (
        <div className="panel">
          <table>
            <thead><tr><th>Feed</th><th className="num">Entries</th><th>Version</th><th>Licence</th></tr></thead>
            <tbody>{shown.map((f) => (
              <tr key={f.name}><td><a href={`${base}${f.url}`}>{f.name}</a></td><td className="num">{f.records}</td>
                <td className="mono">{f.version}</td><td>{f.license}</td></tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </>
  );
}
