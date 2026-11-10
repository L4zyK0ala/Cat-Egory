"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { call } from "@/lib/client";
import { categoryName } from "@/lib/i18n";
import { useLocale, useT } from "./LocaleProvider";

type Item = { input: string; domain: string | null; status: "found" | "registrable_domain" | "not_found" | "invalid";
  matched: string | null; primary: string | null; secondary: string[]; threats: string[]; classification_status: string | null };
type Cat = { id: string; name: string };

const MAX = 1000;
const BATCH = 100;  // the API's limit without a key

/** One name per line; from a CSV the first column. Comments and blank lines are skipped. */
export function parseNames(text: string): string[] {
  return text.split(/\r?\n/).map((line) => line.split(/[,;\t]/)[0].trim().replace(/^"|"$/g, ""))
    .filter((v) => v && !v.startsWith("#") && v.toLowerCase() !== "domain");
}

const csvCell = (v: string) => (/[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);

export default function BulkLookup({ api }: { api: string }) {
  const t = useT();
  const locale = useLocale();
  const [text, setText] = useState("");
  const [items, setItems] = useState<Item[] | null>(null);
  const [names, setNames] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  useEffect(() => {
    void call<{ items: Cat[] }>(api, "/api/v1/categories", { auth: false }).then((r) => {
      if (r.ok) setNames(Object.fromEntries(r.data.items.map((c) => [c.id, c.name])));
    });
  }, [api]);
  const name = (id: string) => categoryName(locale, id, names[id]);
  const statusText: Record<Item["status"], string> = {
    found: t("found", "bulundu"), registrable_domain: t("via its main domain", "üst alan adından"),
    not_found: t("not in the database", "veri tabanında yok"), invalid: t("not a domain", "alan adı değil"),
  };

  async function run() {
    const all = parseNames(text);
    if (all.length === 0) return;
    const list = all.slice(0, MAX);
    setBusy(true);
    setMsg(all.length > MAX ? t(`Only the first ${MAX} lines are checked.`, `Yalnızca ilk ${MAX} satıra bakıldı.`) : "");
    const out: Item[] = [];
    for (let i = 0; i < list.length; i += BATCH) {
      const r = await call<{ items: Item[] }>(api, "/api/v1/lookup",
        { method: "POST", auth: false, body: JSON.stringify({ domains: list.slice(i, i + BATCH) }) });
      if (!r.ok) {
        setMsg(r.code === "rate_limited" ? t("Too many requests; wait a minute and try again.", "Çok fazla istek; bir dakika bekleyip tekrar deneyin.")
                                          : `${r.message} (${r.code})`);
        break;
      }
      out.push(...r.data.items);
      setItems([...out]);
    }
    setBusy(false);
  }

  function download() {
    if (!items) return;
    const rows = [["input", "domain", "status", "matched", "primary", "secondary", "threats"],
      ...items.map((i) => [i.input, i.domain ?? "", i.status, i.matched ?? "", i.primary ?? "", i.secondary.join("|"), i.threats.join("|")])];
    const blob = new Blob([rows.map((r) => r.map(csvCell).join(",")).join("\n") + "\n"], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "cat-egory-lookup.csv";
    a.click();
    URL.revokeObjectURL(a.href);
  }

  const count = (s: Item["status"]) => items?.filter((i) => i.status === s).length ?? 0;
  const flagged = items?.filter((i) => i.threats.length > 0).length ?? 0;
  return (
    <div className="stack">
      <div className="panel stack">
        <textarea rows={8} value={text} onChange={(e) => setText(e.target.value)}
                  placeholder={t("One domain or URL per line, e.g.\nexample.com\nhttps://shop.example.org/basket",
                                 "Her satıra bir alan adı ya da URL, ör.\nexample.com\nhttps://magaza.example.org/sepet")} />
        <div className="row">
          <button type="button" disabled={busy || !text.trim()} onClick={run}>{busy ? t("Checking…", "Bakılıyor…") : t("Look Up All", "Hepsini Sorgula")}</button>
          <label className="muted">{t("or open a file (.txt, .csv):", "ya da dosya açın (.txt, .csv):")}{" "}
            <input type="file" accept=".txt,.csv,.list,.hosts" onChange={async (e) => {
              const file = e.target.files?.[0];
              if (file) setText((await file.text()).slice(0, 2_000_000));
            }} /></label>
        </div>
        <p className="muted" style={{ fontSize: 13 }}>{t(`Up to ${MAX} names. Nothing you enter is stored or scanned.`, `En fazla ${MAX} ad. Girdiğiniz hiçbir şey kaydedilmez ya da taranmaz.`)}{" "}
          <Link href="/developers">{t("For larger or automatic checks, use the API →", "Daha büyük ya da otomatik sorgular için API →")}</Link></p>
        {msg && <div className="notice" role="status">{msg}</div>}
      </div>
      {items && (
        <div className="panel stack">
          <div className="row">
            <span><b>{items.length}</b> {t("checked", "sorgulandı")} · <b>{count("found") + count("registrable_domain")}</b> {t("known", "biliniyor")} · <b>{count("not_found")}</b> {t("unknown", "bilinmiyor")}
              {flagged > 0 && <> · <b className="danger-text">{flagged}</b> {t("flagged as a threat", "tehdit olarak işaretli")}</>}</span>
            <button type="button" className="secondary" onClick={download}>{t("Download CSV", "CSV İndir")}</button>
          </div>
          <table>
            <thead><tr><th>{t("Name", "Ad")}</th><th>{t("Main category", "Ana kategori")}</th><th>{t("Also", "Ayrıca")}</th><th>{t("Security", "Güvenlik")}</th><th>{t("Status", "Durum")}</th></tr></thead>
            <tbody>{items.map((i, n) => (
              <tr key={`${n}-${i.input}`}>
                <td className="mono">{i.matched ? <Link href={`/domain/${i.matched}`}>{i.domain ?? i.input}</Link> : (i.domain ?? i.input)}</td>
                <td>{i.primary ? name(i.primary) : "–"}</td>
                <td className="muted">{i.secondary.map(name).join(", ")}</td>
                <td>{i.threats.map((x) => <span key={x} className="badge danger" style={{ marginRight: 4 }}>{name(x)}</span>)}</td>
                <td className="muted">{statusText[i.status]}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </div>
  );
}
