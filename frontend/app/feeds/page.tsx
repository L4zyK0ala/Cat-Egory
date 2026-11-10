import { api, publicApiUrl } from "@/lib/api";
import type { Feed } from "@/lib/types";
import { CatChase } from "@/components/CatScene";
import { getT } from "@/lib/locale";

export const dynamic = "force-dynamic";

export default async function Feeds() {
  const { locale, t } = await getT();
  let feeds: Feed[] | null = null;
  try {
    feeds = await api<Feed[]>("/api/v1/feeds", 300);
  } catch {
    feeds = null;
  }
  const base = publicApiUrl();
  const shown = (feeds ?? []).filter((f) => f.records > 0 || f.name.startsWith("all.") || f.name === "security.rpz");
  return (
    <>
      <h1>{t("Feeds", "Listeler")}</h1>
      {locale === "tr" ? (
        <p className="muted">
          Saatte bir yeniden oluşturulan, kullanıma hazır listeler. <span className="mono">.txt</span> = her satırda bir alan
          adı, <span className="mono">.hosts</span> = hosts dosyası / Pi-hole, <span className="mono">.rpz</span> = DNS Response
          Policy Zone (BIND, Unbound, PowerDNS). <span className="mono">{base}/api/v1/feeds/rpz</span> tüm güvenlik tehditlerini
          engeller. Her dosyada sürüm, tarih ve lisans yazar (CC BY 4.0 — lütfen &quot;Cat-egory contributors&quot; diye
          kaynak gösterin).
        </p>
      ) : (
        <p className="muted">
          Ready-to-use lists, rebuilt hourly. <span className="mono">.txt</span> = one domain per line,{" "}
          <span className="mono">.hosts</span> = hosts-file / Pi-hole, <span className="mono">.rpz</span> = DNS Response Policy
          Zone (BIND, Unbound, PowerDNS). <span className="mono">{base}/api/v1/feeds/rpz</span> blocks all security threats.
          Each file states its version, date and licence (CC BY 4.0 — please credit &quot;Cat-egory contributors&quot;).
        </p>
      )}
      <CatChase />
      {feeds === null ? <div className="notice error">{t("The API is not reachable right now.", "API'ye şu an ulaşılamıyor.")}</div> : shown.length === 0 ?
        <div className="notice">{t("No feeds have been built yet.", "Henüz liste oluşturulmadı.")}</div> : (
        <div className="panel">
          <table>
            <thead><tr><th>{t("Feed", "Liste")}</th><th className="num">{t("Entries", "Kayıt")}</th><th>{t("Version", "Sürüm")}</th><th>{t("Licence", "Lisans")}</th></tr></thead>
            <tbody>{shown.map((f) => (
              <tr key={f.name}><td><a href={`${base}${f.url}`}>{f.name}</a></td><td className="num">{f.records}</td>
                <td className="mono">{f.version}</td><td>{f.license}</td></tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </>
  );
}
