"use client";
import { useT } from "./LocaleProvider";

export type Vt = { known: boolean; flagged: boolean; malicious: number; suspicious: number; total: number;
  last_analysis: string | null; engines: { name: string; category: string; result: string }[];
  checked_at: string; link: string };

/** VirusTotal's last answer (internal; admin UI only). Shows nothing until a check has run or without a key. */
export default function VtBadge({ vt }: { vt?: Vt | null }) {
  const t = useT();
  if (!vt) return null;
  if (!vt.known) {
    return <span className="badge" title={t("VirusTotal has no analysis of this domain. That says nothing about whether it is safe.",
                                             "VirusTotal bu alan adını analiz etmemiş. Bu, güvenli olup olmadığı hakkında bir şey söylemez.")}>
      {t("VT: no record", "VT: kayıt yok")}</span>;
  }
  const title = vt.flagged ? t("Vendors flagging this domain. One vendor alone can be a false alarm.",
                               "Bu alan adını işaretleyen güvenlik firmaları. Tek bir firma yanlış alarm olabilir.")
    : t("No vendor flags it. That does not mean safe: new domains are often not seen yet.",
        "Hiçbir firma işaretlemiyor. Bu güvenli demek değil: yeni alan adları çoğu zaman henüz görülmemiştir.");
  return <a className={`badge ${vt.flagged ? "danger" : ""}`} href={vt.link} target="_blank" rel="noopener noreferrer"
            title={title}>VT: {vt.malicious + vt.suspicious}/{vt.total}</a>;
}

export function vtEngines(vt?: Vt | null) {
  return (vt?.engines ?? []).map((e) => `${e.name} (${e.result || e.category})`).join(", ");
}
