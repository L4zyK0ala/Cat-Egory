"use client";
import { useCallback, useEffect, useState } from "react";
import { call } from "@/lib/client";
import KeyGate from "./KeyGate";

type Match = { id: number | string | null; value: string; type: string; category: string | null;
  category_name: string | null; criticality: number | null; date: string | null; scope: string; link: string | null };
type Item = { domain: string; types: string[]; first_seen: string; status: "listed" | "missing" | "unchecked";
  sgb: { listed: boolean; checked_at: string; matches: Match[] } | null };
type Report = { counts: Record<string, number>; items: Item[] };
type Status = "missing" | "listed" | "unchecked" | "all";

const LABEL: Record<Item["status"], string> = { listed: "On the list", missing: "Not on the list", unchecked: "Not checked yet" };
const day = (v: string) => new Date(v).toLocaleDateString("en", { dateStyle: "medium" });

function Report({ api }: { api: string }) {
  const [status, setStatus] = useState<Status>("missing");
  const [data, setData] = useState<Report | null>(null);
  const [msg, setMsg] = useState("");
  const load = useCallback(async () => {
    const r = await call<Report>(api, `/api/v1/admin/threats/sgb-report?status=${status}`);
    if (r.ok) setData(r.data); else setMsg(`${r.message} (${r.code})`);
  }, [api, status]);
  useEffect(() => { void load(); }, [load]);

  async function copy() {
    const text = (data?.items ?? []).map((i) => i.domain).join("\n");
    try { await navigator.clipboard.writeText(text); setMsg(`${data?.items.length ?? 0} domains copied.`); }
    catch { setMsg("Copying is not allowed in this browser; select the table instead."); }
  }

  if (!data) return <p className="muted">{msg || "Loading…"}</p>;
  return (
    <div className="stack">
      <div className="row">
        <select value={status} aria-label="Show" onChange={(e) => setStatus(e.target.value as Status)}>
          <option value="missing">Not on the list ({data.counts.missing})</option>
          <option value="listed">On the list ({data.counts.listed})</option>
          <option value="unchecked">Not checked yet ({data.counts.unchecked})</option>
          <option value="all">All</option>
        </select>
        {status === "missing" && data.items.length > 0 && <button className="secondary" onClick={copy}>Copy Domains</button>}
        {msg && <span className="muted">{msg}</span>}
      </div>
      {data.items.length === 0 ? <div className="notice">Nothing here.</div> : (
        <div className="panel">
          <table>
            <thead><tr><th>Domain</th><th>Our finding</th><th>Confirmed</th><th>National list (SGB/USOM)</th></tr></thead>
            <tbody>{data.items.map((i) => (
              <tr key={i.domain}>
                <td className="mono">{i.domain}</td>
                <td>{i.types.join(", ")}</td>
                <td>{day(i.first_seen)}</td>
                <td>
                  <span className={`badge ${i.status === "listed" ? "ok" : i.status === "missing" ? "danger" : "pending"}`}>{LABEL[i.status]}</span>
                  {i.sgb && <span className="muted" style={{ fontSize: 12 }}> · checked {day(i.sgb.checked_at)}</span>}
                  {i.sgb?.matches.map((m) => (
                    <div key={`${m.id}-${m.value}`} style={{ fontSize: 13 }}>
                      {m.link ? <a href={m.link} target="_blank" rel="noopener noreferrer">{m.category_name ?? m.category}</a>
                        : (m.category_name ?? m.category)}
                      {m.criticality != null && ` · criticality ${m.criticality}`}{m.date && ` · ${m.date.slice(0, 10)}`}
                      {m.scope !== "exact" && <span className="muted"> · {m.scope}: <span className="mono">{m.value}</span></span>}
                    </div>
                  ))}
                </td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default function SgbReport({ api }: { api: string }) {
  return <KeyGate api={api}>{() => <Report api={api} />}</KeyGate>;
}
