"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { call, postConfirmed } from "@/lib/client";
import KeyGate from "./KeyGate";
import { Screenshot } from "./ReviewQueue";
import VtBadge, { vtEngines, type Vt } from "./VtBadge";
import { categoryName, intl, type T } from "@/lib/i18n";
import { useLocale, useT } from "./LocaleProvider";

type Prediction = { provider: string; model: string; primary_category: string | null; secondary_categories: string[];
  risk_categories: string[]; confidence: number | null; description: string | null;
  evidence: { type: string; observation: string }[] };
type SgbMatch = { value: string; category: string | null; category_name: string | null; criticality: number | null;
  date: string | null; scope: string; link: string | null };
type Review = { id: number; domain: string; reason: string; page_title: string | null; final_url: string | null;
  http_status: number | null; has_screenshot: boolean; prediction: Prediction | null; lookalike_of?: string | null;
  sgb?: { listed: boolean; checked_at: string; matches: SgbMatch[] } | null; vt?: Vt | null };
type Cat = { id: string; name: string; kind: string; is_group: boolean };
type Action = "approve" | "set" | "reject" | "withdraw";

function reasonText(t: T, reason: string, lookalikeOf?: string | null) {
  const reasons: Record<string, string> = {
    verification_sample: t("published by the AI — verify", "yapay zekâ yayımladı — doğrulayın"),
    verification_category: t("published by the AI — verify (category-balanced sample)", "yapay zekâ yayımladı — doğrulayın (kategoriye dengeli örnek)"),
    brand_lookalike: t("look-alike from the certificate watch — check for phishing", "sertifika izlemesinden taklit — oltalama mı bakın"),
    low_confidence: t("model unsure", "model emin değil"), model_requested_review: t("model asked for a person", "model bir kişiye sordu"),
    needs_extra_validation: t("needs a second opinion", "ikinci görüş gerekiyor"), model_disagreement: t("two models disagree", "iki model farklı düşünüyor"),
    prompt_injection: t("page tries to instruct the model", "sayfa modeli yönlendirmeye çalışıyor"), risk_claim: t("model claims a threat", "model tehdit iddia ediyor"),
    conflicts_with_human_decision: t("conflicts with a human decision", "bir insan kararıyla çelişiyor"),
    threat_returned: t("was flagged, went offline and is back — check again", "işaretlenmişti, kapanıp geri geldi — yeniden bakın"),
    threat_page_changed: t("flagged, and the page changed — cleaned?", "işaretli, sayfası değişti — temizlendi mi?"),
    robots_blocked: t("closed to bots (robots.txt): the model's suggestion from the name only — page not visited",
                      "botlara kapalı (robots.txt): sayfa açılmadı, modelin yalnız alan adından önerisi"),
    guard_sexual: t("EVREN's content-safety model sees sexual content — adult content?",
                    "EVREN içerik güvenliği modeli cinsel içerik görüyor — yetişkin içerik mi?"),
  };
  const target = reason.startsWith("lookalike_of:") ? reason.slice(13) : reason === "brand_lookalike" ? lookalikeOf : null;
  if (target) return t(`looks like ${target} — check for phishing`, `${target} taklidi gibi — oltalama mı bakın`);
  return reasons[reason] ?? reason.replaceAll("_", " ");
}

/** Is the domain on the national SGB/USOM list (checked hourly in batches; internal information)? */
function UsomBadge({ sgb }: { sgb?: Review["sgb"] }) {
  const t = useT();
  const locale = useLocale();
  if (sgb === undefined) return null;
  if (sgb === null) return <span className="badge" title={t("Checked within a few hours", "Birkaç saat içinde kontrol edilir")}>{t("USOM: not checked yet", "USOM: henüz bakılmadı")}</span>;
  return sgb.listed ? <span className="badge danger">{t("USOM: on the list", "USOM: listede")}</span>
    : <span className="badge" title={`${t("checked", "kontrol")} ${new Date(sgb.checked_at).toLocaleString(intl(locale))}`}>{t("USOM: not on the list", "USOM: listede değil")}</span>;
}

/** Close categories a reviewer often has to swap between: one click instead of typing (S). */
const SWAP: Record<string, string> = { gambling: "illegal-gambling", "illegal-gambling": "gambling",
  "streaming-media": "piracy", video: "piracy", "file-sharing": "piracy", "software-downloads": "piracy",
  piracy: "streaming-media" };

const isLookalike = (reason: string) => reason === "brand_lookalike" || reason.startsWith("lookalike_of:");

function typing(target: EventTarget | null) {
  const el = target as HTMLElement | null;
  return !!el && (["INPUT", "TEXTAREA", "SELECT", "SUMMARY"].includes(el.tagName) || el.isContentEditable);
}

/** One review at a time, keyboard first: A approve, S choose a category, R reject, N skip. Every decision is
 *  still a person's: nothing is approved without a key press or click on this exact item. */
function Quick({ api }: { api: string }) {
  const t = useT();
  const locale = useLocale();
  const [items, setItems] = useState<Review[]>([]);
  const [cats, setCats] = useState<Cat[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [done, setDone] = useState(0);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [choice, setChoice] = useState("");
  const [risk, setRisk] = useState<string[]>([]);
  // Two queues: "which category?" first; look-alikes and threat claims ("is this dangerous?") apart.
  const [queue, setQueue] = useState<"categories" | "threats">("categories");
  const picker = useRef<HTMLInputElement>(null);

  const content = useMemo(() => cats.filter((c) => c.kind === "content" && !c.is_group), [cats]);
  const risks = useMemo(() => cats.filter((c) => c.kind === "risk" && !c.is_group), [cats]);
  const name = useCallback((id: string) => categoryName(locale, id, cats.find((c) => c.id === id)?.name), [cats, locale]);
  const current = items[0];
  const next = items[1];
  const changed = current?.reason === "threat_page_changed";  // flagged site, other page: withdraw or keep the flag

  const load = useCallback(async () => {
    const [r, c] = await Promise.all([call<{ items: Review[] }>(api, `/api/v1/admin/reviews?limit=50&queue=${queue}`),
                                      call<{ items: Cat[] }>(api, "/api/v1/categories", { auth: false })]);
    if (r.ok) setItems(r.data.items); else setMsg({ ok: false, text: `${r.message} (${r.code})` });
    if (c.ok) setCats(c.data.items);
    setLoaded(true);
  }, [api, queue]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { setChoice(""); setRisk([]); }, [current?.id]);

  const advance = useCallback(() => setItems((xs) => xs.slice(1)), []);

  const decide = useCallback(async (requested: Action, preset?: string) => {
    if (!current || busy) return;
    let action = requested;
    // No valid model category: Approve takes the category typed in the box (= Set), or, with none typed, confirms
    // only the ticked threats and leaves the categories as they are.
    if (action === "approve" && !current.prediction?.primary_category) {
      if (choice.trim()) action = "set";
      else if (risk.length === 0) {
        setMsg({ ok: false, text: t("The model gave no valid category: choose one (S), tick a threat, or reject (R).",
                                    "Model geçerli bir kategori vermedi: bir kategori seçin (S), bir tehdit işaretleyin ya da reddedin (R).") });
        picker.current?.focus(); return;
      }
    }
    let primary: string | undefined = preset;
    if (action === "set" && !primary) {
      // Both sides through the same rule: a plain toLowerCase() turns Turkish "İ" into "i" + a combining dot, so
      // "İş Dünyası" typed in the box never matched the Turkish-lowercased name. Both rules are tried, so an English
      // name typed in lower case ("it" for "IT") still matches on a Turkish panel.
      const folds = (s: string) => { const v = s.normalize("NFC").trim(); return [v.toLocaleLowerCase(intl(locale)), v.toLowerCase()]; };
      const [typedLocal, typedPlain] = folds(choice);
      const same = (s: string) => { const [l, p] = folds(s); return l === typedLocal || p === typedPlain; };
      primary = content.find((c) => same(c.id) || same(c.name) || same(name(c.id)))?.id;
      if (!primary) { setMsg({ ok: false, text: t("Choose a category from the list first (press S).", "Önce listeden bir kategori seçin (S tuşu).") }); picker.current?.focus(); return; }
    }
    setBusy(true);
    // Reject and withdraw never carry threats: ticked boxes are ignored there (a "not a threat" must not flag one).
    const body = { action, ...(primary ? { primary, secondary: [] } : {}),
                   risk: action === "reject" || action === "withdraw" ? [] : risk };
    const res = await postConfirmed(api, `/api/v1/admin/reviews/${current.id}/decision`, body);
    setBusy(false);
    if (res.ok || res.code === "already_decided") {
      const flags = risk.length && action !== "reject" ? ` · ${t("threat", "tehdit")}: ${risk.map(name).join(", ")}` : "";
      const verb = action === "approve" ? (primary || current.prediction?.primary_category ? t("approved", "onaylandı")
                                           : t("threat confirmed", "tehdit onaylandı")) : action === "set" ? t(`set to ${name(primary!)}`, `${name(primary!)} olarak atandı`)
        : action === "withdraw" ? t("threat flag withdrawn", "tehdit işareti kaldırıldı")
        : changed ? t("flag kept", "işaret bırakıldı") : t("rejected", "reddedildi");
      setMsg({ ok: true, text: `${current.domain}: ${verb}${action === "withdraw" ? "" : flags}` });
      setDone((d) => d + 1);
      picker.current?.blur();  // keyboard shortcuts work again on the next item
      advance();
    } else setMsg({ ok: false, text: `${res.message} (${res.code})` });
  }, [advance, api, busy, changed, choice, content, current, locale, name, risk, t]);

  const sample = useCallback(async (byCategory = false) => {
    const r = await call<{ created: number }>(api, "/api/v1/admin/reviews/sample",
      { method: "POST", body: JSON.stringify({ n: 20, by_category: byCategory }) });
    setMsg(r.ok ? { ok: true, text: t(`${r.data.created} published AI answers added for verification.`, `Doğrulama için ${r.data.created} yayımlanmış YZ cevabı eklendi.`) }
                : { ok: false, text: `${r.message} (${r.code})` });
    void load();
  }, [api, load, t]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey || typing(e.target) || !current) return;
      const k = e.key.toLowerCase();
      // No Enter shortcut: Enter on a focused button would also approve.
      if (k === "a") { e.preventDefault(); void decide("approve"); }
      else if (k === "r") { e.preventDefault(); void decide("reject"); }
      else if (k === "w" && changed) { e.preventDefault(); void decide("withdraw"); }
      else if (k === "n") { e.preventDefault(); advance(); setMsg(null); }
      else if (k === "s") { e.preventDefault(); picker.current?.focus(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [advance, changed, current, decide]);

  if (!loaded) return <p className="muted">{t("Loading the queue…", "Kuyruk yükleniyor…")}</p>;
  const p = current?.prediction;
  return (
    <div className="stack quick">
      <div className="row quick-bar">
        <span className="row">
          <select value={queue} aria-label={t("Queue", "Kuyruk")} onChange={(e) => { setQueue(e.target.value as typeof queue); setMsg(null); }}>
            <option value="categories">{t("Category reviews", "Kategori incelemeleri")}</option>
            <option value="threats">{t("Threat candidates", "Tehdit adayları")}</option>
          </select>
          <span><b>{done}</b> {t("decided", "karar")} · <b>{items.length}</b> {t("left", "kaldı")}</span>
        </span>
        <button className="secondary" onClick={() => void sample()}>{t("Add 20 Published AI Answers to Verify", "Doğrulamak İçin 20 YZ Cevabı Ekle")}</button>
        <button className="secondary" title={t("From the categories with the fewest checks so far: measures each category, not the overall figure",
                                                "Şimdiye kadar en az kontrol edilen kategorilerden: her kategoriyi ölçer, genel oranı değil")}
                onClick={() => void sample(true)}>{t("20, Balanced by Category", "20, Kategoriye Dengeli")}</button>
        <span className="muted keys"><kbd>A</kbd> {t("approve", "onayla")} · <kbd>S</kbd> {t("category", "kategori")} · <kbd>R</kbd> {t("reject", "reddet")} · <kbd>N</kbd> {t("skip", "atla")}</span>
      </div>
      {msg && <div className={`notice ${msg.ok ? "ok" : "error"}`} role="status">{msg.text}</div>}
      {!current ? (
        <div className="panel"><p>{queue === "threats" ? t("No threat candidates waiting. Parked look-alikes are watched automatically and come back here when their page changes.",
                                                  "Bekleyen tehdit adayı yok. Park edilmiş taklitler otomatik izlenir, sayfaları değişince buraya geri gelir.")
          : t("Nothing left to review. Add a sample of published AI answers to verify them.", "İncelenecek bir şey kalmadı. Doğrulamak için yayımlanmış YZ cevaplarından örnek ekleyin.")}</p></div>
      ) : (
        <div className="panel quick-card">
          <div className="row">
            <h2 className="quick-domain">{current.domain}</h2>
            <span className="badge pending">{reasonText(t, current.reason, current.lookalike_of)}</span>
            <UsomBadge sgb={current.sgb} />
            <VtBadge vt={current.vt} />
          </div>
          {current.sgb?.listed && current.sgb.matches.map((m) => (
            <div key={m.value} className="muted" style={{ fontSize: 13 }}>
              {t("National list", "Ulusal liste")}: {m.link ? <a href={m.link} target="_blank" rel="noopener noreferrer">{m.category_name ?? m.category}</a>
                : (m.category_name ?? m.category)}
              {m.criticality != null && ` · ${t("criticality", "kritiklik")} ${m.criticality}`}{m.date && ` · ${t("since", "başlangıç")} ${m.date.slice(0, 10)}`}
              {m.scope !== "exact" && <> · {m.scope}: <span className="mono">{m.value}</span></>}
            </div>
          ))}
          {current.vt?.flagged && <div className="muted" style={{ fontSize: 13 }}>VirusTotal: {vtEngines(current.vt)}</div>}
          {current.page_title && <p className="muted quick-title">“{current.page_title}”</p>}
          <div className="grid2">
            <div>{current.has_screenshot ? <Screenshot key={current.domain} api={api} domain={current.domain} />
                                         : <p className="muted">{t("No screenshot.", "Ekran görüntüsü yok.")}</p>}</div>
            <div className="stack">
              <div className="mono muted">{current.final_url} · HTTP {current.http_status ?? "–"}</div>
              {p ? (<>
                <div>
                  <span className="chip primary quick-main">{p.primary_category ? name(p.primary_category) : t("nothing valid", "geçerli bir şey yok")}</span>
                  {p.secondary_categories.map((c) => <span key={c} className="chip">{name(c)}</span>)}
                </div>
                <div className="muted">
                  {p.confidence != null && <>{t("confidence", "güven")} <b>{(p.confidence * 100).toFixed(0)}%</b> · </>}
                  <span className="mono">{p.provider}/{p.model}</span>
                </div>
                {p.risk_categories.length > 0 &&
                  <div className="notice error">{t("Model claims a threat", "Model tehdit iddia ediyor")}: {p.risk_categories.map(name).join(", ")} {t("(unverified)", "(doğrulanmadı)")}</div>}
                {p.description && <div className="muted">“{p.description}” <i>{t("(model claim)", "(model iddiası)")}</i></div>}
                <ul className="evidence">{p.evidence.map((e, i) => <li key={i}><span className="mono">{e.type}</span> {e.observation}</li>)}</ul>
              </>) : <p className="muted">{t("No model prediction.", "Model tahmini yok.")}</p>}

              <form className="row" onSubmit={(e) => { e.preventDefault(); void decide("set"); }}>
                <input ref={picker} list="quick-cats" value={choice} onChange={(e) => setChoice(e.target.value)}
                       placeholder={t("S — type a category, Enter to set", "S — kategori yazın, Enter ile atayın")} aria-label={t("Category", "Kategori")} className="quick-picker"
                       onKeyDown={(e) => { if (e.key === "Escape") (e.target as HTMLInputElement).blur(); }} />
                <datalist id="quick-cats">{content.map((c) => <option key={c.id} value={name(c.id)}>{c.id}</option>)}</datalist>
              </form>
              {changed && <div className="notice">{t("This site is flagged as a threat, and its page is clearly different from the previous scan. If the dangerous content is gone, withdraw the flag; phishing kits also show harmless pages to some visitors, so keep it when in doubt.",
                                                     "Bu site tehdit olarak işaretli ve sayfası önceki taramadan belirgin biçimde farklı. Tehlikeli içerik kalktıysa işareti kaldırın; oltalama kitleri bazı ziyaretçilere zararsız sayfa da gösterir, emin değilseniz işaret kalsın.")}</div>}
              <details key={current.id} open={isLookalike(current.reason) || current.reason === "threat_returned"}>
                <summary className="muted">{t("Confirm a Security Threat (Only with Evidence)", "Güvenlik Tehdidini Onayla (Yalnızca Kanıtla)")}</summary>
                <div className="row">{risks.map((c) => (
                  <label key={c.id} className="muted"><input type="checkbox" checked={risk.includes(c.id)}
                    onChange={(e) => setRisk(e.target.checked ? [...risk, c.id] : risk.filter((x) => x !== c.id))} /> {name(c.id)}</label>
                ))}</div>
              </details>
              <div className="row">
                <button disabled={busy || (!p?.primary_category && !choice.trim() && risk.length === 0)} onClick={() => decide("approve")}>{t("Approve", "Onayla")} <kbd>A</kbd></button>
                <button className="secondary" disabled={busy} onClick={() => decide("set")}>{t("Set Category", "Kategori Ata")} <kbd>S</kbd></button>
                {p?.primary_category && SWAP[p.primary_category] && (
                  <button className="secondary" disabled={busy} onClick={() => decide("set", SWAP[p.primary_category!])}>
                    {t(`Set as ${name(SWAP[p.primary_category])}`, `${name(SWAP[p.primary_category])} Olarak Ata`)}
                  </button>
                )}
                {changed && <button className="danger" disabled={busy} onClick={() => decide("withdraw")}>{t("Withdraw Flag", "İşareti Kaldır")} <kbd>W</kbd></button>}
                <button className="secondary" disabled={busy} onClick={() => decide("reject")}>{changed ? t("Keep Flag", "İşaret Kalsın") : t("Reject", "Reddet")} <kbd>R</kbd></button>
                <button className="secondary" disabled={busy} onClick={() => { advance(); setMsg(null); }}>{t("Skip", "Atla")} <kbd>N</kbd></button>
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
