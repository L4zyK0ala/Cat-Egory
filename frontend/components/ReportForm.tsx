"use client";
import { useState } from "react";
import { call } from "@/lib/client";
import { useT } from "./LocaleProvider";

type Option = { id: string; name: string };

export default function ReportForm({ api, domain, categories, current, risks = [], currentRisks = [] }: {
  api: string; domain: string; categories: Option[]; current: string[];
  risks?: Option[]; currentRisks?: string[];  // security-threat categories, and the flags this domain has now
}) {
  const t = useT();
  const [type, setType] = useState("false_positive");
  const [category, setCategory] = useState(current[0] ?? currentRisks[0] ?? "");
  const label = (id: string) => [...categories, ...risks].find((x) => x.id === id)?.name ?? id;
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
    setStatus(r.ok ? { ok: true, text: t("Thank you. A moderator will review your report.", "Teşekkürler. Bildiriminizi bir moderatör inceleyecek.") }
                   : { ok: false, text: `${r.message} (${r.code})` });
  }

  return (
    <form className="panel stack" onSubmit={submit}>
      <div className="row">
        <label>{t("Report type", "Bildirim türü")}{" "}
          <select value={type} onChange={(e) => {
            setType(e.target.value);
            // a threat can only be reported as missing; the main category must be a content category
            if (e.target.value === "category_correction" && risks.some((r) => r.id === proposed)) setProposed(categories[0]?.id ?? "");
          }}>
            <option value="false_positive">{t("A category is wrong", "Bir kategori yanlış")}</option>
            <option value="false_negative">{t("A category is missing", "Bir kategori eksik")}</option>
            <option value="category_correction">{t("The main category should be…", "Ana kategori şu olmalı…")}</option>
            <option value="verification">{t("The classification is correct", "Sınıflandırma doğru")}</option>
          </select>
        </label>
        {type === "false_positive" && (
          <select value={category} onChange={(e) => setCategory(e.target.value)} aria-label={t("Wrong category", "Yanlış kategori")}>
            {current.map((c) => <option key={c} value={c}>{label(c)}</option>)}
            {currentRisks.length > 0 && (
              <optgroup label={t("Security warnings", "Güvenlik uyarıları")}>
                {currentRisks.map((c) => <option key={c} value={c}>{label(c)}</option>)}
              </optgroup>
            )}
          </select>
        )}
        {(type === "false_negative" || type === "category_correction") && (
          <select value={proposed} onChange={(e) => setProposed(e.target.value)} aria-label={t("Category", "Kategori")}>
            {categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            {type === "false_negative" && risks.length > 0 && (
              <optgroup label={t("Security threat (the site is dangerous)", "Güvenlik tehdidi (site tehlikeli)")}>
                {risks.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </optgroup>
            )}
          </select>
        )}
      </div>
      <textarea required minLength={10} maxLength={2000} placeholder={t("What on the site shows this? (min. 10 characters)", "Sitede bunu gösteren ne? (en az 10 karakter)")}
                value={reason} onChange={(e) => setReason(e.target.value)} />
      <input type="text" placeholder={t("Evidence URLs (optional, space separated, max 5)", "Kanıt bağlantıları (isteğe bağlı, boşlukla ayrılmış, en fazla 5)")} value={evidence}
             onChange={(e) => setEvidence(e.target.value)} />
      <div className="row">
        <button type="submit" disabled={busy}>{t("Send Report", "Bildir")}</button>
        {status && <span className={`notice ${status.ok ? "ok" : "error"}`}>{status.text}</span>}
      </div>
    </form>
  );
}
