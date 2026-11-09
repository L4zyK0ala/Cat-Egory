"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { call } from "@/lib/client";
import KeyGate from "./KeyGate";

type Scan = { id: string; domain: string; status: string; reason: string; priority: number; attempts: number;
  error_code: string | null; queued_at: string; finished_at: string | null };
type AddResult = { received: number; valid: number; created: number; existing: number; duplicates_in_input: number;
  scans_queued: number; invalid: { row: number; value: string; code: string }[] };

function Console({ api, scopes }: { api: string; scopes: string[] }) {
  const [stats, setStats] = useState<Record<string, number>>({});
  const [provider, setProvider] = useState<Record<string, unknown> | null>(null);
  const [scans, setScans] = useState<Scan[]>([]);
  const [filter, setFilter] = useState("");
  const [text, setText] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [testing, setTesting] = useState(false);

  const refresh = useCallback(async () => {
    const [s, l, p] = await Promise.all([
      call<Record<string, number>>(api, "/api/v1/admin/scans/stats"),
      call<{ items: Scan[] }>(api, `/api/v1/admin/scans?limit=50${filter ? `&status=${filter}` : ""}`),
      scopes.includes("reviews:write") ? call<Record<string, unknown>>(api, "/api/v1/admin/providers") : Promise.resolve(null),
    ]);
    if (s.ok) setStats(s.data);
    if (l.ok) setScans(l.data.items);
    if (p && p.ok) setProvider(p.data);
  }, [api, filter, scopes]);
  useEffect(() => { void refresh(); }, [refresh]);

  const report = (r: AddResult) => setMsg({ ok: true, text:
    `received ${r.received}, created ${r.created}, existing ${r.existing}, duplicates ${r.duplicates_in_input}, ` +
    `invalid ${r.invalid.length}${r.invalid.length ? ` (first: row ${r.invalid[0].row} ${r.invalid[0].code})` : ""}, scans queued ${r.scans_queued}` });

  async function add(e: React.FormEvent) {
    e.preventDefault();
    const domains = text.split(/\s+/).filter(Boolean);
    const r = await call<AddResult>(api, "/api/v1/admin/domains", { method: "POST", body: JSON.stringify({ domains }) });
    if (r.ok) { report(r.data); setText(""); void refresh(); } else setMsg({ ok: false, text: `${r.message} (${r.code})` });
  }
  async function upload(file: File) {
    const form = new FormData();
    form.append("file", file);
    const r = await call<AddResult>(api, "/api/v1/admin/domains/import", { method: "POST", body: form });
    if (r.ok) { report(r.data); void refresh(); } else setMsg({ ok: false, text: `${r.message} (${r.code})` });
  }
  async function rescan(domain: string) {
    const r = await call(api, "/api/v1/admin/scans", { method: "POST", body: JSON.stringify({ domain, priority: 2 }) });
    setMsg(r.ok ? { ok: true, text: `${domain} queued` } : { ok: false, text: `${r.message} (${r.code})` });
    void refresh();
  }
  async function cancel(id: string) {
    await call(api, `/api/v1/admin/scans/${id}/cancel`, { method: "POST" });
    void refresh();
  }

  const active = provider?.active as { provider?: string; model?: string; status?: string; detail?: string } | undefined;
  return (
    <div className="stack">
      <div className="row"><Link href="/admin/reviews">Review Queue →</Link><Link href="/admin/moderation">Moderation →</Link><Link href="/admin/threats">Confirmed Threats →</Link>
        {scopes.includes("reviews:write") && (
          <button className="secondary" onClick={async () => {
            const r = await call(api, "/api/v1/admin/feeds/build", { method: "POST" });
            setMsg(r.ok ? { ok: true, text: "Feed rebuild queued; files appear under Feeds in a minute." }
                        : { ok: false, text: `${r.message} (${r.code})` });
          }}>Rebuild Feeds Now</button>)}</div>
      <div className="tiles">
        {["queued", "running", "succeeded", "blocked", "failed", "cancelled"].map((k) => (
          <div className="panel tile" key={k}><div className="label">{k}</div><div className="value">{stats[k] ?? 0}</div></div>
        ))}
      </div>
      {active && (
        <div className="panel">AI provider: <b>{active.provider}</b> / <span className="mono">{active.model}</span>{" "}
          <span className={`badge ${active.status === "ok" ? "verified" : "pending"}`}>{active.status}</span>{" "}
          <span className="muted">{active.detail}</span>{" "}
          <button className="secondary" disabled={testing} onClick={async () => {
            setTesting(true);
            const r = await call<Record<string, unknown>>(api, "/api/v1/admin/providers?probe=true");
            setTesting(false);
            if (r.ok) setProvider(r.data); else setMsg({ ok: false, text: `${r.message} (${r.code})` });
          }}>{testing ? "Testing… (can take a minute)" : "Test live"}</button></div>
      )}
      <h2>Add Domains</h2>
      <form className="panel stack" onSubmit={add}>
        <textarea placeholder="Domains or URLs, separated by spaces or new lines" value={text} onChange={(e) => setText(e.target.value)} />
        <div className="row">
          <button type="submit" disabled={!text.trim()}>Add and Scan</button>
          <label className="muted">or import a file (.csv .txt .hosts .json .xlsx, max 10 000):{" "}
            <input type="file" accept=".csv,.tsv,.txt,.hosts,.list,.json,.xlsx" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
          </label>
        </div>
        {msg && <div className={`notice ${msg.ok ? "ok" : "error"}`}>{msg.text}</div>}
      </form>
      <h2>Scan Queue</h2>
      <div className="row">
        <select value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Status filter">
          <option value="">all</option>
          {["queued", "running", "succeeded", "blocked", "failed", "cancelled"].map((s) => <option key={s}>{s}</option>)}
        </select>
        <button className="secondary" onClick={() => void refresh()}>Refresh</button>
      </div>
      <div className="panel">
        <table>
          <thead><tr><th>Domain</th><th>Status</th><th>Reason</th><th className="num">Tries</th><th>Error</th><th>Queued</th><th></th></tr></thead>
          <tbody>{scans.map((s) => (
            <tr key={s.id}>
              <td><Link href={`/domain/${s.domain}`}>{s.domain}</Link></td>
              <td><span className={`badge ${s.status}`}>{s.status}</span></td><td>{s.reason}</td>
              <td className="num">{s.attempts}</td><td className="mono">{s.error_code ?? ""}</td>
              <td>{new Date(s.queued_at).toLocaleString("en")}</td>
              <td>{s.status === "queued" ? <button className="secondary" onClick={() => cancel(s.id)}>Cancel</button>
                : ["failed", "blocked", "succeeded", "cancelled"].includes(s.status) ?
                  <button className="secondary" onClick={() => rescan(s.domain)}>Rescan</button> : null}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
    </div>
  );
}

export default function AdminConsole({ api }: { api: string }) {
  return <KeyGate api={api}>{(me) => <Console api={api} scopes={me.scopes} />}</KeyGate>;
}
