import type { Metadata } from "next";
import Link from "next/link";
import CopyText from "@/components/CopyText";
import { publicApiUrl } from "@/lib/api";
import { categoryName } from "@/lib/i18n";
import { getT } from "@/lib/locale";

export const dynamic = "force-dynamic";

/** Must match feeds/builder.BUSINESS_CATEGORIES. */
const BUSINESS = ["adult-content", "gambling", "illegal-gambling", "illegal-drugs", "violence", "weapons",
                  "hate-and-discrimination", "proxy-services", "piracy"];
const REPO = "https://github.com/L4zyK0ala/Cat-Egory";
const UPDATER = "https://raw.githubusercontent.com/L4zyK0ala/Cat-Egory/main/dns/updater/cat-egory-dns-update";

export async function generateMetadata(): Promise<Metadata> {
  const { t } = await getT();
  return { title: t("For Companies", "Kurumsal"),
           description: t("Use Cat-Egory block lists in your company's own DNS servers: BIND, Unbound, PowerDNS and Windows Server. No query leaves your network.",
                          "Cat-Egory engelleme listelerini şirketinizin kendi DNS sunucularında kullanın: BIND, Unbound, PowerDNS ve Windows Server. Hiçbir sorgu ağınızdan çıkmaz.") };
}

export default async function Business() {
  const { locale, t } = await getT();
  const feed = (name: string) => `${publicApiUrl()}/api/v1/feeds/${name}`;
  const install = `curl -fsSLo /usr/local/bin/cat-egory-dns-update ${UPDATER}
chmod 755 /usr/local/bin/cat-egory-dns-update`;
  const allowZone = `$TTL 300
$ORIGIN allow.local.
@ SOA localhost. hostmaster.localhost. (1 3600 600 86400 300)
@ NS localhost.
partner.example CNAME rpz-passthru.
*.partner.example CNAME rpz-passthru.`;

  const bindList = "/var/lib/bind/cat-egory/profile-business.rpz";
  const bindConf = `options {
  // ...
  response-policy {
    zone "allow.local" policy passthru;
    zone "rpz.cat-egory";
  } qname-wait-recurse no;
};
zone "allow.local"   { type primary; file "/etc/bind/allow.rpz"; allow-query { none; }; };
zone "rpz.cat-egory" { type primary; file "${bindList}"; allow-query { none; }; };`;
  const bindCron = `# /etc/cron.d/cat-egory
17 * * * * root /usr/local/bin/cat-egory-dns-update --feed profile-business.rpz --out ${bindList} --check "named-checkzone -q rpz.cat-egory" --reload "rndc reload rpz.cat-egory"`;

  const unboundList = "/var/lib/unbound/profile-business.rpz";
  const unboundConf = `server:
  module-config: "respip validator iterator"
remote-control:
  control-enable: yes
  control-interface: /run/unbound.ctl
rpz:
  name: allow.local.
  zonefile: /etc/unbound/allow.rpz
rpz:
  name: rpz.cat-egory.
  zonefile: ${unboundList}`;
  const unboundCron = `17 * * * * root /usr/local/bin/cat-egory-dns-update --feed profile-business.rpz --out ${unboundList} --reload "unbound-control auth_zone_reload rpz.cat-egory."`;

  const pdnsList = "/etc/powerdns/cat-egory/profile-business.rpz";
  const pdnsConf = `# recursor.yml
recursor:
  lua_config_file: /etc/powerdns/rpz.lua

-- rpz.lua: checked in this order, the allow list first
rpzFile("/etc/powerdns/allow.rpz", {policyName="allow"})
rpzFile("${pdnsList}", {policyName="cat-egory"})`;
  const pdnsCron = `17 * * * * root /usr/local/bin/cat-egory-dns-update --feed profile-business.rpz --out ${pdnsList} --reload "rec_control reload-lua-config && rec_control wipe-cache '.\\$'"`;

  const image = `git clone ${REPO}.git && cd Cat-Egory
docker build -f dns/image/Dockerfile -t cat-egory-dns dns
docker run -d --name cat-egory-dns --restart unless-stopped \\
  -p 10.0.0.53:53:53/udp -p 10.0.0.53:53:53/tcp -e CATEGORY_PROFILE=profile-business \\
  -v cat-egory-dns:/var/lib/cat-egory-dns cat-egory-dns`;
  const windows = `# on each domain controller; 10.0.0.53 and 10.0.0.54 are two Cat-egory DNS hosts
Set-DnsServerForwarder -IPAddress 10.0.0.53, 10.0.0.54 -UseRootHint $false`;

  return (
    <div className="about">
      <h1>{t("For Companies", "Kurumsal")}</h1>
      <p className="lead">{t("Use Cat-egory lists in your company's own DNS servers. Your server downloads the list and decides locally: no query from your network ever reaches us.",
                             "Cat-egory listelerini şirketinizin kendi DNS sunucularında kullanın. Sunucunuz listeyi indirir ve kararı yerelde verir: ağınızdaki hiçbir sorgu bize ulaşmaz.")}</p>

      <h2>{t("How It Works", "Nasıl Çalışır")}</h2>
      <div className="panel stack">
        <ol>
          <li>{t("A small updater checks for a new list every hour and downloads it only when it changed.",
                 "Küçük bir güncelleyici saatte bir yeni liste olup olmadığına bakar; yalnızca değiştiyse indirir.")}</li>
          <li>{t("Before installing, it checks the checksum, the format of every line, the entry count and that the list did not suddenly shrink. Anything wrong: the last good list stays.",
                 "Kurmadan önce sağlama toplamını, her satırın biçimini, kayıt sayısını ve listenin birden küçülmediğini denetler. Bir sorun varsa son sağlam liste yerinde kalır.")}</li>
          <li>{t("Your DNS server answers \"does not exist\" (NXDOMAIN) for listed domains and their subdomains. Your own allow list is checked first.",
                 "DNS sunucunuz listedeki alan adları ve alt alan adları için \"yok\" (NXDOMAIN) yanıtı verir. Önce sizin izin listeniz denetlenir.")}</li>
        </ol>
        <p className="muted">{t("If our address is unreachable, nothing changes on your side: the filter never empties because of us.",
                                "Adresimize ulaşılamazsa sizin tarafınızda hiçbir şey değişmez: filtre bizim yüzümüzden asla boşalmaz.")}</p>
      </div>

      <h2>{t("Pick a List", "Liste Seçin")}</h2>
      <div className="panel">
        <table><tbody>
          <tr><th>{t("Business", "İş yeri")}</th><td>{t("Security, plus: ", "Güvenlik, artı: ")}{BUSINESS.map((c) => categoryName(locale, c, c)).join(", ")}.</td>
            <td className="mono">profile-business</td></tr>
          <tr><th>{t("Security", "Güvenlik")}</th><td>{t("Phishing, malware, scams and look-alikes confirmed by a person or Octryx.",
                                                       "Bir kişinin veya Octryx'in onayladığı oltalama, zararlı yazılım, dolandırıcılık ve taklit siteler.")}</td>
            <td className="mono">profile-security</td></tr>
          <tr><th>{t("Other", "Diğer")}</th><td>{t("The family profile or any single category, e.g. gambling. See ", "Aile profili ya da herhangi bir kategori, ör. kumar. Hepsi ")}
            <Link href="/feeds">{t("all lists", "tüm listeler")}</Link>{t(".", " sayfasında.")}</td><td className="mono">gambling</td></tr>
        </tbody></table>
      </div>
      <p className="muted">{t("The examples below use profile-business.rpz; replace the name to use another list. Every list exists in the RPZ format the servers below read.",
                              "Aşağıdaki örnekler profile-business.rpz kullanır; başka bir liste için adı değiştirin. Her liste, aşağıdaki sunucuların okuduğu RPZ biçiminde de vardır.")}{" "}
        <CopyText value={feed("profile-business.rpz")} /></p>

      <h2>{t("The Updater", "Güncelleyici")}</h2>
      <div className="panel stack">
        <p>{t("A POSIX shell script of about 160 lines; it needs curl and sha256sum, which every Linux server has. Read it before you run it:",
              "Yaklaşık 160 satırlık bir POSIX kabuk betiği; her Linux sunucusunda bulunan curl ve sha256sum yeterli. Çalıştırmadan önce okuyun:")}{" "}
          <a href={`${REPO}/blob/main/dns/updater/cat-egory-dns-update`} rel="noopener">dns/updater</a></p>
        <pre className="mono">{install}</pre>
        <p className="muted">{t("Run it once with --init before you start the DNS server: if the download fails, it installs an empty list so the server can start, and the next hourly run fills it. It exits 0 when the list is up to date and 1 when it kept the old one, so any monitoring can watch it.",
                                "DNS sunucusunu başlatmadan önce bir kez --init ile çalıştırın: indirme başarısız olursa sunucunun açılabilmesi için boş bir liste kurar, saatlik çalışma onu doldurur. Liste güncelse 0, eskisini koruduysa 1 ile çıkar; böylece izleme sisteminiz onu takip edebilir.")}</p>
      </div>

      <h2>{t("Local Allow List", "Yerel İzin Listesi")}</h2>
      <div className="panel stack">
        <p>{t("Domains you never want blocked, whatever the list says. One zone file, read before the Cat-egory list; the examples below call it allow.rpz:",
              "Liste ne derse desin hiç engellenmesini istemediğiniz alan adları. Cat-egory listesinden önce okunan tek bir bölge dosyası; aşağıdaki örneklerde adı allow.rpz:")}</p>
        <pre className="mono">{allowZone}</pre>
      </div>

      <h2>BIND 9</h2>
      <div className="panel stack">
        <p>{t("In named.conf:", "named.conf içine:")}</p>
        <pre className="mono">{bindConf}</pre>
        <p>{t("Every hour, as root:", "Saatte bir, root olarak:")}</p>
        <pre className="mono">{bindCron}</pre>
        <p className="muted">{t("BIND applies a reloaded policy zone within a minute (min-update-interval). On Debian and Ubuntu, AppArmor lets named read /var/lib/bind, hence the path.",
                                "BIND yeniden yüklenen politika bölgesini en geç bir dakika içinde uygular (min-update-interval). Debian ve Ubuntu'da AppArmor named'in /var/lib/bind'i okumasına izin verir; yol bu yüzden orada.")}</p>
      </div>

      <h2>Unbound</h2>
      <div className="panel stack">
        <p>{t("In unbound.conf:", "unbound.conf içine:")}</p>
        <pre className="mono">{unboundConf}</pre>
        <p>{t("Every hour, as root:", "Saatte bir, root olarak:")}</p>
        <pre className="mono">{unboundCron}</pre>
        <p className="muted">{t("Unbound can also download the list by itself (url:), as on the ", "Unbound listeyi kendisi de indirebilir (url:); ")}
          <Link href="/integrations">{t("router setup page", "router kurulum sayfasındaki gibi")}</Link>
          {t(". The updater adds the checks above.", ". Güncelleyici yukarıdaki denetimleri ekler.")}</p>
      </div>

      <h2>PowerDNS Recursor 5</h2>
      <div className="panel stack">
        <pre className="mono">{pdnsConf}</pre>
        <p>{t("Every hour, as root:", "Saatte bir, root olarak:")}</p>
        <pre className="mono">{pdnsCron}</pre>
        <p className="muted">{t("Recursor 5.4 reads only recursor.yml. wipe-cache drops answers cached before the new list, so a newly listed domain is blocked at once.",
                                "Recursor 5.4 yalnızca recursor.yml okur. wipe-cache yeni listeden önce önbelleğe alınmış yanıtları siler; böylece listeye yeni giren alan adı hemen engellenir.")}</p>
      </div>

      <h2>{t("Windows Server DNS (Active Directory)", "Windows Server DNS (Active Directory)")}</h2>
      <div className="panel stack">
        <p>{t("Windows DNS cannot load a policy zone. Keep it for your Active Directory zones and let it forward everything else to Cat-egory DNS: a ready-made resolver (Unbound with DNSSEC validation and the updater) that runs on any Linux host with Docker.",
              "Windows DNS politika bölgesi yükleyemez. Active Directory bölgeleriniz için onu kullanmaya devam edin, geri kalan her şeyi Cat-egory DNS'e yönlendirsin: Docker olan herhangi bir Linux sunucuda çalışan hazır bir çözümleyici (DNSSEC doğrulamalı Unbound ve güncelleyici).")}</p>
        <pre className="mono">{image}</pre>
        <p>{t("Then, in PowerShell on each domain controller:", "Ardından her etki alanı denetleyicisinde PowerShell'de:")}</p>
        <pre className="mono">{windows}</pre>
        <table><tbody>
          <tr><td className="mono">CATEGORY_PROFILE</td><td>{t("the list (default profile-security)", "liste (varsayılan profile-security)")}</td></tr>
          <tr><td className="mono">ALLOW_FROM</td><td>{t("who may ask, CIDRs separated by spaces (default the private ranges)", "kimin sorabileceği, boşlukla ayrılmış CIDR'ler (varsayılan özel ağlar)")}</td></tr>
          <tr><td className="mono">FORWARD_TO</td><td>{t("upstream resolvers; empty = it resolves from the root servers itself", "üst çözümleyiciler; boşsa kök sunuculardan kendisi çözer")}</td></tr>
          <tr><td className="mono">UPDATE_INTERVAL</td><td>{t("seconds between checks (default 3600)", "denetimler arası saniye (varsayılan 3600)")}</td></tr>
          <tr><td className="mono">LOG_BLOCKED</td><td>{t("yes = log every blocked query with the asking address (default no)", "yes = engellenen her sorguyu soran adresle kaydeder (varsayılan no)")}</td></tr>
          <tr><td className="mono">/etc/cat-egory/allow.txt</td><td>{t("optional file, one domain per line: never blocked", "isteğe bağlı dosya, her satırda bir alan adı: hiç engellenmez")}</td></tr>
        </tbody></table>
        <p className="muted">{t("10.0.0.53 stands for the host's own address: on Ubuntu, systemd-resolved already holds port 53 on 127.0.0.53. Run two copies on two hosts and list both as forwarders. The image is tested in our lab against broken and unreachable lists; our Windows Server test is next.",
                                "10.0.0.53 sunucunun kendi adresinin yerine geçer: Ubuntu'da 127.0.0.53'teki 53. portu systemd-resolved tutar. İki ayrı sunucuda iki kopya çalıştırın ve ikisini de yönlendirici olarak ekleyin. İmaj laboratuvarımızda bozuk ve ulaşılamayan listelere karşı test edildi; Windows Server testi sırada.")}</p>
      </div>

      <h2>{t("Privacy", "Gizlilik")}</h2>
      <div className="panel stack">
        <p>{t("Your DNS server asks us for one list, not for the names your people visit. The download is counted like any other list download (see ",
              "DNS sunucunuz bizden yalnızca listeyi ister, çalışanlarınızın ziyaret ettiği adları değil. İndirme diğer tüm liste indirmeleri gibi sayılır (bkz. ")}
          <Link href="/privacy">{t("Privacy", "Gizlilik")}</Link>
          {t("). Cat-egory DNS keeps no query log unless you set LOG_BLOCKED=yes; if you do, that log is yours and stays on your network.",
             "). Cat-egory DNS, LOG_BLOCKED=yes demediğiniz sürece sorgu kaydı tutmaz; derseniz o kayıt sizindir ve ağınızda kalır.")}</p>
      </div>

      <h2>{t("Blocked by Mistake?", "Yanlışlıkla mı Engellendi?")}</h2>
      <div className="panel stack">
        <p>{t("Add it to your allow list right away, and report it from the domain's page; a person checks every report, and once it is corrected the next list no longer contains it.",
              "Hemen izin listenize ekleyin ve alan adının sayfasından bildirin; her bildirime bir kişi bakar ve düzeltilirse sonraki listede yer almaz.")}</p>
      </div>

      <p className="muted">{t("Running Cat-egory in a company network, or planning to? We would like to hear how it goes: ",
                              "Cat-egory'yi bir şirket ağında kullanıyor ya da kullanmayı mı düşünüyorsunuz? Nasıl gittiğini duymak isteriz: ")}
        <Link href="/contact">{t("contact", "iletişim")}</Link>
        {t(". The business profile contains content categories and is CC BY 4.0: credit \"Cat-egory contributors\".",
           ". İş yeri profili içerik kategorileri içerdiği için CC BY 4.0 lisanslıdır: \"Cat-egory contributors\" diye kaynak gösterin.")}</p>
    </div>
  );
}
