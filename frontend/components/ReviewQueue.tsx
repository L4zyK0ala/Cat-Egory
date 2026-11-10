"use client";
import { useCallback, useEffect, useState } from "react";
import { call, getKey, postConfirmed } from "@/lib/client";
import { categoryName } from "@/lib/i18n";
import KeyGate from "./KeyGate";
import { useLocale, useT } from "./LocaleProvider";

type Review = { id: number; domain: string; reason: string; current_primary: string | null; page_title: string | null;
  final_url: string | null; http_status: number | null; has_screenshot: boolean;
  prediction: { provider: string; model: string; primary_category: string | null; secondary_categories: string[];
    risk_categories: string[]; confidence: number | null; description: string | null;
    evidence: { type: string; observation: string }[] } | null };
type Cat = { id: string; name: string; kind: string; is_group: boolean };

export function Screenshot({ api, domain }: { api: string; domain: string }) {
  const t = useT();
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    let url: string | null = null;
    fetch(`${api}/api/v1/admin/domains/${domain}/screenshot`, { headers: { Authorization: `Bearer ${getKey()}` } })
      .then((r) => (r.ok ? r.blob() : null)).then((b) => { if (b) { url = URL.createObjectURL(b); setSrc(url); } })
      .catch(() => undefined);
    return () => { if (url) URL.revokeObjectURL(url); };
  }, [api, domain]);
  return src ? <img className="shot" src={src} alt={t(`Screenshot of ${domain}`, `${domain} ekran görüntüsü`)} /> : <p className="muted">{t("Loading screenshot…", "Ekran görüntüsü yükleniyor…")}</p>;
}

function Item({ api, r, cats, onDone }: { api: string; r: Review; cats: Cat[]; onDone: () => void }) {
  const t = useT();
  const locale = useLocale();
  const content = cats.filter((c) => c.kind === "content" && !c.is_group);
  const risks = cats.filter((c) => c.kind === "risk" && !c.is_group);
  const [primary, setPrimary] = useState(r.prediction?.primary_category ?? content[0]?.id ?? "");
  const [risk, setRisk] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  async function decide(action: "approve" | "set" | "reject") {
    const body = { action, ...(action === "set" ? { primary, secondary: [] } : {}), risk, note: note || null };
    const res = await postConfirmed(api, `/api/v1/admin/reviews/${r.id}/decision`, body);
    if (res.ok) onDone(); else setError(`${res.message} (${res.code})`);
  }
  const p = r.prediction;
  const name = (id: string) => categoryName(locale, id, cats.find((c) => c.id === id)?.name);
  return (
    <div className="panel stack">
      <div className="row"><h2 style={{ margin: 0 }}>{r.domain}</h2><span className="badge pending">{r.reason.replaceAll("_", " ")}</span>
        <span className="muted">{r.page_title}</span></div>
      <div className="grid2">
        <div>{r.has_screenshot ? <Screenshot api={api} domain={r.domain} /> : <p className="muted">{t("No screenshot.", "Ekran görüntüsü yok.")}</p>}</div>
        <div className="stack">
          <div className="mono muted">{r.final_url} · HTTP {r.http_status ?? "–"}</div>
          {p ? (<>
            <div>{t("Model", "Model")} <span className="mono">{p.provider}/{p.model}</span> {t("suggests", "öneriyor:")}{" "}
              <span className="chip primary">{p.primary_category ? name(p.primary_category) : t("nothing valid", "geçerli bir şey yok")}</span>
              {p.secondary_categories.map((c) => <span key={c} className="chip">{name(c)}</span>)}
              {p.confidence != null && <span className="muted"> · {t("confidence", "güven")} {(p.confidence * 100).toFixed(0)}%</span>}</div>
            {p.risk_categories.length > 0 && <div className="notice error">{t("Model claims", "Model iddiası")}: {p.risk_categories.map(name).join(", ")} {t("(unverified)", "(doğrulanmadı)")}</div>}
            {p.description && <div className="muted">“{p.description}” <i>{t("(model claim)", "(model iddiası)")}</i></div>}
            <ul style={{ margin: 0 }}>{p.evidence.map((e, i) => <li key={i}><span className="mono">{e.type}</span>: {e.observation}</li>)}</ul>
          </>) : <p className="muted">{t("No model prediction.", "Model tahmini yok.")}</p>}
          <div className="row">
            <select value={primary} onChange={(e) => setPrimary(e.target.value)} aria-label={t("Primary category", "Ana kategori")}>
              {content.map((c) => <option key={c.id} value={c.id}>{name(c.id)}</option>)}
            </select>
            {risks.map((c) => (
              <label key={c.id} style={{ fontSize: 13 }}><input type="checkbox" checked={risk.includes(c.id)}
                onChange={(e) => setRisk(e.target.checked ? [...risk, c.id] : risk.filter((x) => x !== c.id))} /> {name(c.id)}</label>
            ))}
          </div>
          <input type="text" placeholder={t("Note (optional)", "Not (isteğe bağlı)")} value={note} onChange={(e) => setNote(e.target.value)} />
          <div className="row">
            <button disabled={!p?.primary_category} onClick={() => decide("approve")}>{t("Approve Model", "Modeli Onayla")}</button>
            <button className="secondary" onClick={() => decide("set")}>{t("Set Selected Category", "Seçili Kategoriyi Ata")}</button>
            <button className="danger" onClick={() => decide("reject")}>{t("Reject", "Reddet")}</button>
          </div>
          {error && <div className="notice error">{error}</div>}
        </div>
      </div>
    </div>
  );
}

function Queue({ api }: { api: string }) {
  const t = useT();
  const [items, setItems] = useState<Review[]>([]);
  const [cats, setCats] = useState<Cat[]>([]);
  const load = useCallback(async () => {
    const [r, c] = await Promise.all([call<{ items: Review[] }>(api, "/api/v1/admin/reviews?limit=10"),
                                      call<{ items: Cat[] }>(api, "/api/v1/categories", { auth: false })]);
    if (r.ok) setItems(r.data.items);
    if (c.ok) setCats(c.data.items);
  }, [api]);
  useEffect(() => { void load(); }, [load]);
  if (items.length === 0) return <div className="notice">{t("The review queue is empty.", "İnceleme kuyruğu boş.")}</div>;
  return <div className="stack">{items.map((r) => <Item key={r.id} api={api} r={r} cats={cats} onDone={load} />)}</div>;
}

export default function ReviewQueue({ api }: { api: string }) {
  return <KeyGate api={api}>{() => <Queue api={api} />}</KeyGate>;
}
