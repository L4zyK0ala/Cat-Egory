"use client";
import { useCallback, useEffect, useState } from "react";
import { call } from "@/lib/client";
import KeyGate from "./KeyGate";

type Sub = { id: number; type: string; domain: string; proposed_categories: string[]; score: number; comment: string | null;
  category: string | null; evidence: string[]; reporters_for_domain: number; created_at: string };

function Queue({ api }: { api: string }) {
  const [items, setItems] = useState<Sub[]>([]);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    const r = await call<Sub[]>(api, "/api/v1/admin/submissions");
    if (r.ok) setItems(r.data); else setError(`${r.message} (${r.code})`);
  }, [api]);
  useEffect(() => { void load(); }, [load]);
  async function act(id: number, action: string) {
    const r = await call(api, `/api/v1/admin/submissions/${id}/moderate`, { method: "POST", body: JSON.stringify({ action }) });
    if (!r.ok) setError(`${r.message} (${r.code})`);
    void load();
  }
  return (
    <div className="stack">
      {error && <div className="notice error">{error}</div>}
      {items.length === 0 ? <div className="notice">Nothing to moderate.</div> : items.map((s) => (
        <div key={s.id} className="panel stack">
          <div className="row"><b>{s.domain}</b><span className="badge pending">{s.type.replaceAll("_", " ")}</span>
            <span className="muted">score {s.score} · reporters for domain {s.reporters_for_domain}</span></div>
          {s.category && <div>Category in question: <span className="chip">{s.category}</span></div>}
          {s.proposed_categories.length > 0 && <div>Proposed: {s.proposed_categories.map((c) => <span key={c} className="chip">{c}</span>)}</div>}
          {s.comment && <div style={{ whiteSpace: "pre-wrap" }}>{s.comment}</div>}
          {s.evidence.length > 0 && <ul>{s.evidence.map((u) => <li key={u} className="mono">{u}</li>)}</ul>}
          <div className="row"><button onClick={() => act(s.id, "accept")}>Accept</button>
            <button className="secondary" onClick={() => act(s.id, "reject")}>Reject</button>
            <button className="danger" onClick={() => act(s.id, "spam")}>Spam</button></div>
        </div>
      ))}
    </div>
  );
}

export default function ModerationQueue({ api }: { api: string }) {
  return <KeyGate api={api}>{() => <Queue api={api} />}</KeyGate>;
}
