"use client";
import { useState } from "react";
import { call } from "@/lib/client";
import { useT } from "./LocaleProvider";

export default function SuggestForm({ api, initial, categories }: {
  api: string; initial: string; categories: { id: string; name: string }[];
}) {
  const t = useT();
  const [domain, setDomain] = useState(initial);
  const [category, setCategory] = useState("");
  const [comment, setComment] = useState("");
  const [status, setStatus] = useState<{ ok: boolean; text: string } | null>(null);
  return (
    <form className="panel stack" onSubmit={async (e) => {
      e.preventDefault();
      const r = await call(api, "/api/v1/community/submit", { method: "POST", auth: false,
        body: JSON.stringify({ domain, proposed_categories: category ? [category] : [], comment: comment || null }) });
      setStatus(r.ok ? { ok: true, text: t("Thanks! The domain will be scanned once a moderator accepts it.", "Teşekkürler! Bir moderatör kabul edince alan adı taranacak.") }
                     : { ok: false, text: `${r.message} (${r.code})` });
    }}>
      <input type="text" required placeholder="example.com" value={domain} onChange={(e) => setDomain(e.target.value)} />
      <select value={category} onChange={(e) => setCategory(e.target.value)} aria-label={t("Suggested category", "Önerilen kategori")}>
        <option value="">{t("Suggested category (optional)", "Önerilen kategori (isteğe bağlı)")}</option>
        {categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
      </select>
      <textarea maxLength={2000} placeholder={t("Comment (optional)", "Açıklama (isteğe bağlı)")} value={comment} onChange={(e) => setComment(e.target.value)} />
      <div className="row"><button type="submit">{t("Suggest", "Öner")}</button>
        {status && <span className={`notice ${status.ok ? "ok" : "error"}`}>{status.text}</span>}</div>
    </form>
  );
}
