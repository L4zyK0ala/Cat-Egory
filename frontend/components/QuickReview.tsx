"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { call } from "@/lib/client";
import KeyGate from "./KeyGate";
import { Screenshot } from "./ReviewQueue";

type Prediction = { provider: string; model: string; primary_category: string | null; secondary_categories: string[];
  risk_categories: string[]; confidence: number | null; description: string | null;
  evidence: { type: string; observation: string }[] };
type Review = { id: number; domain: string; reason: string; page_title: string | null; final_url: string | null;
  http_status: number | null; has_screenshot: boolean; prediction: Prediction | null };
type Cat = { id: string; name: string; kind: string; is_group: boolean };
type Action = "approve" | "set" | "reject";

const REASONS: Record<string, string> = {
  verification_sample: "published by the AI — verify",
  low_confidence: "model unsure", model_requested_review: "model asked for a person",
  needs_extra_validation: "needs a second opinion", model_disagreement: "two models disagree",
  prompt_injection: "page tries to instruct the model", risk_claim: "model claims a threat",
  conflicts_with_human_decision: "conflicts with a human decision",
};

function typing(target: EventTarget | null) {
  const el = target as HTMLElement | null;
  return !!el && (["INPUT", "TEXTAREA", "SELECT", "SUMMARY"].includes(el.tagName) || el.isContentEditable);
}

/** One review at a time, keyboard first: A approve, S choose a category, R reject, N skip. Every decision is
 *  still a person's: nothing is approved without a key press or click on this exact item. */
function Quick({ api }: { api: string }) {
  const [items, setItems] = useState<Review[]>([]);
  const [cats, setCats] = useState<Cat[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [done, setDone] = useState(0);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [choice, setChoice] = useState("");
  const [risk, setRisk] = useState<string[]>([]);
  const picker = useRef<HTMLInputElement>(null);

  const content = useMemo(() => cats.filter((c) => c.kind === "content" && !c.is_group), [cats]);
  const risks = useMemo(() => cats.filter((c) => c.kind === "risk" && !c.is_group), [cats]);
  const name = useCallback((id: string) => cats.find((c) => c.id === id)?.name ?? id, [cats]);
  const current = items[0];
  const next = items[1];

  const load = useCallback(async () => {
    const [r, c] = await Promise.all([call<{ items: Review[] }>(api, "/api/v1/admin/reviews?limit=50"),
                                      call<{ items: Cat[] }>(api, "/api/v1/categories", { auth: false })]);
    if (r.ok) setItems(r.data.items); else setMsg({ ok: false, text: `${r.message} (${r.code})` });
    if (c.ok) setCats(c.data.items);
    setLoaded(true);
  }, [api]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { setChoice(""); setRisk([]); }, [current?.id]);

  const advance = useCallback(() => setItems((xs) => xs.slice(1)), []);

  const decide = useCallback(async (action: Action) => {
    if (!current || busy) return;
    let primary: string | undefined;
    if (action === "set") {
      const typed = choice.trim().toLowerCase();
      primary = content.find((c) => c.id === typed || c.name.toLowerCase() === typed)?.id;
      if (!primary) { setMsg({ ok: false, text: "Choose a category from the list first (press S)." }); picker.current?.focus(); return; }
    }
    if (action === "approve" && !current.prediction?.primary_category) {
      setMsg({ ok: false, text: "The model gave no valid category: choose one (S) or reject (R)." }); return;
    }
    setBusy(true);
    const body = { action, ...(primary ? { primary, secondary: [] } : {}), risk };
    const res = await call(api, `/api/v1/admin/reviews/${current.id}/decision`, { method: "POST", body: JSON.stringify(body) });
    setBusy(false);
    if (res.ok || res.code === "already_decided") {
      const verb = action === "approve" ? "approved" : action === "set" ? `set to ${name(primary!)}` : "rejected";
      setMsg({ ok: true, text: `${current.domain}: ${verb}` });
      setDone((d) => d + 1);
      picker.current?.blur();  // keyboard shortcuts work again on the next item
      advance();
    } else setMsg({ ok: false, text: `${res.message} (${res.code})` });
  }, [advance, api, busy, choice, content, current, name, risk]);

  const sample = useCallback(async () => {
    const r = await call<{ created: number }>(api, "/api/v1/admin/reviews/sample", { method: "POST", body: JSON.stringify({ n: 20 }) });
    setMsg(r.ok ? { ok: true, text: `${r.data.created} published AI answers added for verification.` }
                : { ok: false, text: `${r.message} (${r.code})` });
    void load();
  }, [api, load]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey || typing(e.target) || !current) return;
      const k = e.key.toLowerCase();
      // No Enter shortcut: Enter on a focused button would also approve.
      if (k === "a") { e.preventDefault(); void decide("approve"); }
      else if (k === "r") { e.preventDefault(); void decide("reject"); }
      else if (k === "n") { e.preventDefault(); advance(); setMsg(null); }
      else if (k === "s") { e.preventDefault(); picker.current?.focus(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [advance, current, decide]);

  if (!loaded) return <p className="muted">Loading the queue…</p>;
  const p = current?.prediction;
  return (
    <div className="stack quick">
      <div className="row quick-bar">
        <span><b>{done}</b> decided · <b>{items.length}</b> left</span>
        <button className="secondary" onClick={sample}>Add 20 published AI answers to verify</button>
        <span className="muted keys"><kbd>A</kbd> approve · <kbd>S</kbd> category · <kbd>R</kbd> reject · <kbd>N</kbd> skip</span>
      </div>
      {msg && <div className={`notice ${msg.ok ? "ok" : "error"}`} role="status">{msg.text}</div>}
      {!current ? (
        <div className="panel"><p>Nothing left to review. Add a sample of published AI answers to verify them.</p></div>
      ) : (
        <div className="panel quick-card">
          <div className="row">
            <h2 className="quick-domain">{current.domain}</h2>
            <span className="badge pending">{REASONS[current.reason] ?? current.reason.replaceAll("_", " ")}</span>
          </div>
          {current.page_title && <p className="muted quick-title">“{current.page_title}”</p>}
          <div className="grid2">
            <div>{current.has_screenshot ? <Screenshot key={current.domain} api={api} domain={current.domain} />
                                         : <p className="muted">No screenshot.</p>}</div>
            <div className="stack">
              <div className="mono muted">{current.final_url} · HTTP {current.http_status ?? "–"}</div>
              {p ? (<>
                <div>
                  <span className="chip primary quick-main">{p.primary_category ? name(p.primary_category) : "nothing valid"}</span>
                  {p.secondary_categories.map((c) => <span key={c} className="chip">{name(c)}</span>)}
                </div>
                <div className="muted">
                  {p.confidence != null && <>confidence <b>{(p.confidence * 100).toFixed(0)}%</b> · </>}
                  <span className="mono">{p.provider}/{p.model}</span>
                </div>
                {p.risk_categories.length > 0 &&
                  <div className="notice error">Model claims a threat: {p.risk_categories.map(name).join(", ")} (unverified)</div>}
                {p.description && <div className="muted">“{p.description}” <i>(model claim)</i></div>}
                <ul className="evidence">{p.evidence.map((e, i) => <li key={i}><span className="mono">{e.type}</span> {e.observation}</li>)}</ul>
              </>) : <p className="muted">No model prediction.</p>}

              <form className="row" onSubmit={(e) => { e.preventDefault(); void decide("set"); }}>
                <input ref={picker} list="quick-cats" value={choice} onChange={(e) => setChoice(e.target.value)}
                       placeholder="S — type a category, Enter to set" aria-label="Category" className="quick-picker"
                       onKeyDown={(e) => { if (e.key === "Escape") (e.target as HTMLInputElement).blur(); }} />
                <datalist id="quick-cats">{content.map((c) => <option key={c.id} value={c.name}>{c.id}</option>)}</datalist>
              </form>
              <details>
                <summary className="muted">Confirm a security threat (only with evidence)</summary>
                <div className="row">{risks.map((c) => (
                  <label key={c.id} className="muted"><input type="checkbox" checked={risk.includes(c.id)}
                    onChange={(e) => setRisk(e.target.checked ? [...risk, c.id] : risk.filter((x) => x !== c.id))} /> {c.name}</label>
                ))}</div>
              </details>
              <div className="row">
                <button disabled={busy || !p?.primary_category} onClick={() => decide("approve")}>Approve <kbd>A</kbd></button>
                <button className="secondary" disabled={busy} onClick={() => decide("set")}>Set category <kbd>S</kbd></button>
                <button className="secondary" disabled={busy} onClick={() => decide("reject")}>Reject <kbd>R</kbd></button>
                <button className="secondary" disabled={busy} onClick={() => { advance(); setMsg(null); }}>Skip <kbd>N</kbd></button>
              </div>
            </div>
          </div>
          {next?.has_screenshot && <div hidden aria-hidden="true"><Screenshot key={`pre-${next.domain}`} api={api} domain={next.domain} /></div>}
        </div>
      )}
    </div>
  );
}

export default function QuickReview({ api }: { api: string }) {
  return <KeyGate api={api}>{() => <Quick api={api} />}</KeyGate>;
}
