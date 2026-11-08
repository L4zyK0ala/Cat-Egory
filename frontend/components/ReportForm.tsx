"use client";
import { useState } from "react";
import { call } from "@/lib/client";

type Option = { id: string; name: string };

export default function ReportForm({ api, domain, categories, current }: {
  api: string; domain: string; categories: Option[]; current: string[];
}) {
  const [type, setType] = useState("false_positive");
  const [category, setCategory] = useState(current[0] ?? "");
  const [proposed, setProposed] = useState(categories[0]?.id ?? "");
  const [reason, setReason] = useState("");
  const [evidence, setEvidence] = useState("");
  const [status, setStatus] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    const body: Record<string, unknown> = { domain, type, reason,
      evidence_urls: evidence.split(/\s+/).filter(Boolean).slice(0, 5) };
    if (type === "false_positive" || type === "false_negative") body.category = type === "false_positive" ? category : proposed;
    if (type === "category_correction") body.proposed_categories = [proposed];
    const r = await call(api, "/api/v1/community/report", { method: "POST", body: JSON.stringify(body), auth: false });
    setBusy(false);
    setStatus(r.ok ? { ok: true, text: "Thank you. A moderator will review your report." }
                   : { ok: false, text: `${r.message} (${r.code})` });
  }

  return (
    <form className="panel stack" onSubmit={submit}>
      <div className="row">
        <label>Report type{" "}
          <select value={type} onChange={(e) => setType(e.target.value)}>
            <option value="false_positive">A category is wrong</option>
            <option value="false_negative">A category is missing</option>
            <option value="category_correction">The main category should be…</option>
            <option value="verification">The classification is correct</option>
          </select>
        </label>
        {type === "false_positive" && (
          <select value={category} onChange={(e) => setCategory(e.target.value)} aria-label="Wrong category">
            {current.map((c) => <option key={c} value={c}>{categories.find((x) => x.id === c)?.name ?? c}</option>)}
          </select>
        )}
        {(type === "false_negative" || type === "category_correction") && (
          <select value={proposed} onChange={(e) => setProposed(e.target.value)} aria-label="Category">
            {categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        )}
      </div>
      <textarea required minLength={10} maxLength={2000} placeholder="What on the site shows this? (min. 10 characters)"
                value={reason} onChange={(e) => setReason(e.target.value)} />
      <input type="text" placeholder="Evidence URLs (optional, space separated, max 5)" value={evidence}
             onChange={(e) => setEvidence(e.target.value)} />
      <div className="row">
        <button type="submit" disabled={busy}>Send Report</button>
        {status && <span className={`notice ${status.ok ? "ok" : "error"}`}>{status.text}</span>}
      </div>
    </form>
  );
}
