import type { Metadata } from "next";
import Link from "next/link";
import { api, publicApiUrl } from "@/lib/api";
import type { Category, Feed } from "@/lib/types";
import { CatChase } from "@/components/CatScene";
import FeedBrowser, { type FeedList } from "@/components/FeedBrowser";
import { categoryDescription, categoryName, intl } from "@/lib/i18n";
import { getT } from "@/lib/locale";

export const dynamic = "force-dynamic";

export async function generateMetadata(): Promise<Metadata> {
  const { t } = await getT();
  return { title: t("Free DNS block lists", "Ücretsiz DNS engelleme listeleri"),
           description: t("Free, open block lists by category for Pi-hole, AdGuard Home, routers and DNS (hosts, adblock, RPZ): gambling, adult content, phishing and more.", "Pi-hole, AdGuard Home, router ve DNS için kategoriye göre ücretsiz, açık engelleme listeleri (hosts, adblock, RPZ): kumar, yetişkin içerik, oltalama ve daha fazlası.") };
}

const FORMATS = ["txt", "hosts", "rpz", "adblock"] as const;

export default async function Feeds() {
  const { locale, t } = await getT();
  let feeds: Feed[] | null = null;
  let cats: Category[] = [];
  try {
    const [f, c] = await Promise.all([api<Feed[]>("/api/v1/feeds", 300), api<{ items: Category[] }>("/api/v1/categories", 3600)]);
    feeds = f;
    cats = c.items;
  } catch {
    feeds = null;
  }
  const base = publicApiUrl();
  const byName = new Map((feeds ?? []).map((f) => [f.name, f]));

  /** One list (a category or a profile) with its four formats. */
  const list = (id: string, name: string, description: string): FeedList | null => {
    const files = FORMATS.map((fmt) => byName.get(`${id}.${fmt}`)).filter((f): f is Feed => !!f);
    if (files.length === 0) return null;
    return { id, name, description, records: files[0].records, license: files[0].license,
             formats: files.map((f) => ({ format: f.name.split(".").pop()!, url: `${base}${f.url}` })) };
  };

  const profiles = [
    list("profile-security", t("Security", "Güvenlik"),
         t("Every confirmed threat: phishing, malware, scams. A safe start for any network.",
           "Onaylanmış bütün tehditler: oltalama, zararlı yazılım, dolandırıcılık. Her ağ için güvenli bir başlangıç.")),
    list("profile-family", t("Family", "Aile"),
         t("All threats plus adult content, gambling, drugs, violence, weapons, hate and proxies.",
           "Bütün tehditler artı yetişkin içerik, kumar, uyuşturucu, şiddet, silah, nefret ve proxy siteleri.")),
    list("profile-business", t("Business", "İş yeri"),
         t("All threats plus adult content, gambling, drugs, violence, weapons, hate, piracy and proxies: for company networks.",
           "Bütün tehditler artı yetişkin içerik, kumar, uyuşturucu, şiddet, silah, nefret, korsan içerik ve proxy siteleri: şirket ağları için.")),
  ].filter((x): x is FeedList => !!x);

  const name = (c: Category) => categoryName(locale, c.id, c.name);
  const desc = (c: Category) => categoryDescription(locale, c.id, c.description);
  const threats = cats.filter((c) => c.kind === "risk" && !c.is_group)
    .map((c) => list(c.id, name(c), desc(c))).filter((x): x is FeedList => !!x);
  const groups = cats.filter((c) => c.kind === "content" && c.is_group).map((g) => ({
    id: g.id, name: name(g),
    lists: cats.filter((c) => c.parent_id === g.id && !c.is_group).map((c) => list(c.id, name(c), desc(c)))
      .filter((x): x is FeedList => !!x),
  })).filter((g) => g.lists.length > 0);

  const security = byName.get("security.rpz");
  const data = [
    { file: "all.csv", label: t("All categories (CSV)", "Tüm kategoriler (CSV)"), note: t("Every published site with its main and secondary categories.", "Yayımlanan her site, ana ve ikincil kategorileriyle.") },
    { file: "all.json", label: t("All categories (JSON)", "Tüm kategoriler (JSON)"), note: t("The same, for programs.", "Aynısı, programlar için.") },
    { file: "threats.csv", label: t("Threat dataset (CSV)", "Tehdit veri seti (CSV)"), note: t("Our confirmed threats with dates and history.", "Onayladığımız tehditler, tarihleri ve geçmişiyle.") },
    { file: "threats.json", label: t("Threat dataset (JSON)", "Tehdit veri seti (JSON)"), note: t("The same, for programs.", "Aynısı, programlar için.") },
  ].flatMap((d) => { const f = byName.get(d.file); return f ? [{ ...d, url: `${base}${f.url}`, records: f.records, license: f.license }] : []; });

  const newest = (feeds ?? []).reduce<Feed | null>((a, f) => (!a || f.generated_at > a.generated_at ? f : a), null);
  return (
    <>
      <h1>{t("Feeds", "Listeler")}</h1>
      <p className="lead feeds-lead">{t("Ready-made block and allow lists for routers, DNS filters and proxies, rebuilt every hour. Pick a profile, or a single category below.",
                                         "Router, DNS filtresi ve proxy'ler için hazır engelleme ve izin listeleri; saatte bir yenilenir. Bir profil seçin ya da aşağıdan tek bir kategori alın.")}</p>
      <CatChase />
      {feeds === null ? <div className="notice error">{t("The API is not reachable right now.", "API'ye şu an ulaşılamıyor.")}</div>
        : feeds.length === 0 ? <div className="notice">{t("No feeds have been built yet.", "Henüz liste oluşturulmadı.")}</div> : (<>
          <dl className="fmt-legend">
            <div><dt>TXT</dt><dd>{t("one domain per line", "her satırda bir alan adı")}</dd></div>
            <div><dt>HOSTS</dt><dd>{t("hosts file, Pi-hole", "hosts dosyası, Pi-hole")}</dd></div>
            <div><dt>RPZ</dt><dd>BIND, Unbound, PowerDNS</dd></div>
            <div><dt>ADBLOCK</dt><dd>{t("AdGuard Home, uBlock (covers subdomains)", "AdGuard Home, uBlock (alt alan adları dahil)")}</dd></div>
          </dl>
          <FeedBrowser profiles={profiles} threats={threats} groups={groups}
                       allThreats={security ? { url: `${base}${security.url}`, records: security.records } : null} />

          <h2>{t("All Data", "Tüm Veri")}</h2>
          <div className="feed-data">
            {data.map((d) => (
              <a key={d.file} className="panel feed-file" href={d.url}>
                <b>{d.label}</b>
                <span className="muted">{d.note}</span>
                <span className="feed-meta">{d.records.toLocaleString(intl(locale))} {t("entries", "kayıt")} · {d.license}</span>
              </a>
            ))}
          </div>

          <p className="muted feeds-foot">
            {newest && <>{t("Version", "Sürüm")} <span className="mono">{newest.version}</span> · {t("built", "oluşturma")} {new Date(newest.generated_at).toLocaleString(intl(locale), { dateStyle: "medium", timeStyle: "short" })}. </>}
            {t("Content lists are CC BY 4.0 (credit \"Cat-egory contributors\"); threat lists and the threat dataset are public domain (CC0). Lists use each site's main category. Follow changes on ",
               "İçerik listeleri CC BY 4.0'dır (\"Cat-egory contributors\" diye kaynak gösterin); tehdit listeleri ve tehdit veri seti kamu malıdır (CC0). Listeler her sitenin ana kategorisini kullanır. Değişiklikleri ")}
            <Link href="/changes">{t("the changes page", "Değişiklikler sayfasından")}</Link>{t(", set up a router on ", " izleyin, router kurulumu için ")}
            <Link href="/integrations">{t("Integrations", "Entegrasyonlar")}</Link>{t(".", " sayfasına bakın.")}
          </p>
        </>)}
    </>
  );
}
