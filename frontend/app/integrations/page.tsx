import type { Metadata } from "next";
import Link from "next/link";
import CopyText from "@/components/CopyText";
import { publicApiUrl } from "@/lib/api";
import { categoryName } from "@/lib/i18n";
import { getT } from "@/lib/locale";

export const dynamic = "force-dynamic";

const FAMILY = ["adult-content", "gambling", "illegal-gambling", "illegal-drugs", "violence", "weapons", "hate-and-discrimination", "proxy-services", "piracy"];

export async function generateMetadata(): Promise<Metadata> {
  const { t } = await getT();
  return { title: t("Router and DNS Setup", "Router ve DNS Kurulumu"),
           description: t("Use Cat-Egory lists in AdGuard Home, Pi-hole, OPNsense, pfSense, OpenWrt and Unbound.",
                          "Cat-Egory listelerini AdGuard Home, Pi-hole, OPNsense, pfSense, OpenWrt ve Unbound'da kullanın.") };
}

export default async function Integrations() {
  const { locale, t } = await getT();
  const feed = (name: string) => `${publicApiUrl()}/api/v1/feeds/${name}`;
  const unbound = `server:
  module-config: "respip validator iterator"

rpz:
  name: rpz.cat-egory.
  url: ${feed("profile-security.rpz")}
  rpz-action-override: nxdomain
  rpz-log: yes`;
  return (
    <div className="about">
      <h1>{t("Router and DNS Setup", "Router ve DNS Kurulumu")}</h1>
      <p className="lead">{t("Block threats or whole categories on your network with one address. The lists are rebuilt every hour; most devices fetch them once a day.",
                             "Tehditleri ya da kategorilerin tamamını ağınızda tek bir adresle engelleyin. Listeler saatte bir yenilenir; çoğu cihaz günde bir kez indirir.")}</p>

      <h2>{t("Pick a List", "Liste Seçin")}</h2>
      <div className="panel">
        <table><tbody>
          <tr><th>{t("Security", "Güvenlik")}</th><td>{t("Phishing, malware, scams and look-alikes confirmed by a person or Octryx. The safe default for any network.",
                                                       "Bir kişinin veya Octryx'in onayladığı oltalama, zararlı yazılım, dolandırıcılık ve taklit siteler. Her ağ için güvenli varsayılan.")}</td>
            <td className="mono">profile-security</td></tr>
          <tr><th>{t("Family", "Aile")}</th><td>{t("Security, plus: ", "Güvenlik, artı: ")}{FAMILY.map((c) => categoryName(locale, c, c)).join(", ")}.</td>
            <td className="mono">profile-family</td></tr>
          <tr><th>{t("One category", "Tek kategori")}</th><td>{t("Any category on its own, e.g. gambling. See ", "Herhangi bir kategori tek başına, ör. kumar. Hepsi ")}
            <Link href="/feeds">{t("all lists", "tüm listeler")}</Link>{t(".", " sayfasında.")}</td><td className="mono">gambling</td></tr>
        </tbody></table>
      </div>

      <h2>{t("Pick a Format", "Biçim Seçin")}</h2>
      <div className="panel">
        <table>
          <thead><tr><th>{t("Ending", "Uzantı")}</th><th>{t("Content", "İçerik")}</th><th>{t("Subdomains", "Alt alan adları")}</th></tr></thead>
          <tbody>
            <tr><td className="mono">.adblock</td><td className="mono">||example.com^</td><td>{t("blocked", "engellenir")}</td></tr>
            <tr><td className="mono">.rpz</td><td>{t("DNS Response Policy Zone", "DNS Response Policy Zone")}</td><td>{t("blocked", "engellenir")}</td></tr>
            <tr><td className="mono">.txt</td><td>{t("one domain per line", "her satırda bir alan adı")}</td><td>{t("depends on the device", "cihaza bağlı")}</td></tr>
            <tr><td className="mono">.hosts</td><td className="mono">0.0.0.0 example.com</td><td>{t("not blocked", "engellenmez")}</td></tr>
          </tbody>
        </table>
      </div>

      <h2>AdGuard Home</h2>
      <div className="panel stack">
        <ol>
          <li>{t("Filters → DNS blocklists → Add blocklist → Add a custom list.", "Filtreler → DNS engelleme listeleri → Engelleme listesi ekle → Özel liste ekle.")}</li>
          <li>{t("Paste the address and save:", "Adresi yapıştırıp kaydedin:")} <CopyText value={feed("profile-security.adblock")} /></li>
        </ol>
      </div>

      <h2>Pi-hole</h2>
      <div className="panel stack">
        <ol>
          <li>{t("Lists (Adlists) → add the address:", "Lists (Adlists) → adresi ekleyin:")} <CopyText value={feed("profile-security.txt")} /></li>
          <li>{t("Update Gravity (Tools → Update Gravity, or pihole -g).", "Gravity'yi güncelleyin (Tools → Update Gravity ya da pihole -g).")}</li>
        </ol>
        <p className="muted">{t("Pi-hole matches the exact names in a .txt list; it does not block their subdomains.",
                                "Pi-hole .txt listesindeki adları birebir eşler; alt alan adlarını engellemez.")}</p>
      </div>

      <h2>OPNsense</h2>
      <div className="panel stack">
        <ol>
          <li>{t("Services → Unbound DNS → Blocklist: enable it.", "Services → Unbound DNS → Blocklist: etkinleştirin.")}</li>
          <li>{t("Add the address to the list URLs and Apply:", "Adresi liste URL'lerine ekleyip Apply'a basın:")} <CopyText value={feed("profile-security.txt")} /></li>
        </ol>
      </div>

      <h2>pfSense (pfBlockerNG)</h2>
      <div className="panel stack">
        <ol>
          <li>{t("Firewall → pfBlockerNG → DNSBL → DNSBL Groups → Add.", "Firewall → pfBlockerNG → DNSBL → DNSBL Groups → Add.")}</li>
          <li>{t("Source definition: format Auto, state ON, source:", "Kaynak tanımı: biçim Auto, durum ON, kaynak:")} <CopyText value={feed("profile-security.txt")} /></li>
          <li>{t("Action Unbound, save, then Update → Force → Run.", "Action Unbound, kaydedin, ardından Update → Force → Run.")}</li>
        </ol>
        <p className="muted">{t("Turn on wildcard blocking in DNSBL settings to block subdomains as well.",
                                "Alt alan adlarını da engellemek için DNSBL ayarlarında wildcard engellemeyi açın.")}</p>
      </div>

      <h2>OpenWrt</h2>
      <div className="panel stack">
        <p>{t("With the adblock package (luci-app-adblock), add the address as a custom blocklist source:",
              "adblock paketiyle (luci-app-adblock) adresi özel engelleme listesi kaynağı olarak ekleyin:")}{" "}
          <CopyText value={feed("profile-security.txt")} /></p>
        <p className="muted">{t("Menu names differ between package versions.", "Menü adları paket sürümüne göre değişebilir.")}</p>
      </div>

      <h2>Unbound (RPZ)</h2>
      <div className="panel stack">
        <p>{t("Unbound downloads the zone itself and blocks subdomains too. In unbound.conf:",
              "Unbound bölgeyi kendisi indirir ve alt alan adlarını da engeller. unbound.conf içine:")}</p>
        <pre className="mono">{unbound}</pre>
      </div>

      <p className="muted">{t("The security profile is public domain (CC0); the family profile also contains content categories and is CC BY 4.0: credit \"Cat-egory contributors\". Something blocked that should not be? Report it from the domain's page; a person checks every report.",
                              "Güvenlik profili kamu malıdır (CC0); aile profili içerik kategorilerini de içerdiği için CC BY 4.0 lisanslıdır: \"Cat-egory contributors\" diye kaynak gösterin. Engellenmemesi gereken bir şey mi var? Alan adının sayfasından bildirin; her bildirime bir kişi bakar.")}</p>
    </div>
  );
}
