"use client";
import { useCallback, useEffect, useState } from "react";
import { call, postConfirmed } from "@/lib/client";
import { categoryName } from "@/lib/i18n";
import KeyGate from "./KeyGate";
import { useLocale, useT } from "./LocaleProvider";

type Sub = { id: number; type: string; domain: string; proposed_categories: string[]; score: number; comment: string | null;
  category: string | null; evidence: string[]; reporters_for_domain: number; created_at: string;
  submitter?: { account: boolean; accepted: number; rejected: number; spam: number; trusted: boolean } | null };

function Queue({ api }: { api: string }) {
  const t = useT();
  const locale = useLocale();
  const name = (id: string) => categoryName(locale, id, id);
  const types: Record<string, string> = { new_domain: t("new domain", "yeni alan adı"), false_positive: t("wrong category", "yanlış kategori"),
    false_negative: t("missing category", "eksik kategori"), category_correction: t("main category correction", "ana kategori düzeltmesi"),
    verification: t("confirmation", "onay") };
  const [items, setItems] = useState<Sub[]>([]);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    const r = await call<Sub[]>(api, "/api/v1/admin/submissions");
    if (r.ok) setItems(r.data); else setError(`${r.message} (${r.code})`);
  }, [api]);
  useEffect(() => { void load(); }, [load]);
  async function act(id: number, action: string) {
    const r = await postConfirmed(api, `/api/v1/admin/submissions/${id}/moderate`, { action });
    if (!r.ok) setError(r.code === "primary_required"
      ? t("The category reported as wrong is the site's main category: accepting would leave it without one. Reject the report, or set a new main category under Review.",
          "Yanlış olduğu bildirilen kategori sitenin ana kategorisi; kabul etmek siteyi ana kategorisiz bırakır. Bildirimi reddedin ya da yeni ana kategoriyi İnceleme'den verin.")
      : `${r.message} (${r.code})`);
    void load();
  }
  return (
    <div className="stack">
      {error && <div className="notice error">{error}</div>}
      {items.length === 0 ? <div className="notice">{t("Nothing to moderate.", "Moderasyon bekleyen bir şey yok.")}</div> : items.map((s) => (
        <div key={s.id} className="panel stack">
          <div className="row"><b>{s.domain}</b><span className="badge pending">{types[s.type] ?? s.type.replaceAll("_", " ")}</span>
            <span className="muted">{t(`score ${s.score} · reporters for domain ${s.reporters_for_domain}`, `puan ${s.score} · bu alan adını bildiren ${s.reporters_for_domain}`)}</span>
            {s.submitter && (s.submitter.account
              ? <span className={`badge ${s.submitter.trusted ? "verified" : s.submitter.spam ? "danger" : ""}`}
                      title={t("Moderator decisions on this account's earlier submissions", "Bu hesabın önceki gönderilerine verilen moderatör kararları")}>
                  {s.submitter.trusted ? t("trusted", "güvenilir") : t("account", "hesap")} · {t(`${s.submitter.accepted} accepted, ${s.submitter.rejected} rejected`, `${s.submitter.accepted} kabul, ${s.submitter.rejected} ret`)}
                  {s.submitter.spam > 0 && ` · ${s.submitter.spam} spam`}</span>
              : <span className="badge">{t("anonymous", "anonim")}</span>)}</div>
          {s.category && <div>{s.type === "false_positive" ? t("Reported as wrong", "Yanlış olduğu bildirilen") : t("Category in question", "Söz konusu kategori")}:{" "}
            <span className="chip">{name(s.category)}</span></div>}
          {s.type !== "false_positive" && s.proposed_categories.length > 0 && (
            <div>{t("Proposed", "Önerilen")}: {s.proposed_categories.map((c) => <span key={c} className="chip">{name(c)}</span>)}</div>)}
          {s.comment && <div style={{ whiteSpace: "pre-wrap" }}>{s.comment}</div>}
          {s.evidence.length > 0 && <ul>{s.evidence.map((u) => <li key={u} className="mono">{u}</li>)}</ul>}
          <div className="row"><button onClick={() => act(s.id, "accept")}>{t("Accept", "Kabul Et")}</button>
            <button className="secondary" onClick={() => act(s.id, "reject")}>{t("Reject", "Reddet")}</button>
            <button className="danger" onClick={() => act(s.id, "spam")}>{t("Spam", "Spam")}</button></div>
        </div>
      ))}
    </div>
  );
}

export default function ModerationQueue({ api }: { api: string }) {
  return <KeyGate api={api}>{() => <Queue api={api} />}</KeyGate>;
}
