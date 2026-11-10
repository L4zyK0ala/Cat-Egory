import { publicApiUrl } from "@/lib/api";
import AbuseReports from "@/components/AbuseReports";
import { getT } from "@/lib/locale";

export const dynamic = "force-dynamic";

export default async function Abuse() {
  const { t } = await getT();
  return (<><h1>{t("Abuse Reports", "Kötüye Kullanım Bildirimleri")}</h1>
    <p className="muted">{t(
      "Drafts for our confirmed threats, addressed to the registrar's and the hosting network's abuse contact from their public RDAP records. Prepared hourly; Cat-Egory never sends them. Read the draft, open it in your mail program, send it yourself, then mark it sent. Reports contain only what we saw ourselves and link the public domain page; national-list and VirusTotal results never go into them.",
      "Onaylanmış tehditlerimiz için taslaklar; kayıt firmasının ve barındırma ağının herkese açık RDAP kayıtlarındaki abuse adreslerine yazılır. Saatte bir hazırlanır; Cat-Egory bunları hiçbir zaman göndermez. Taslağı okuyun, posta programınızda açın, kendiniz gönderin, sonra gönderildi olarak işaretleyin. Bildirimlerde yalnızca kendi gördüklerimiz ve alan adının herkese açık sayfası bulunur; ulusal liste ve VirusTotal sonuçları asla yer almaz.")}</p>
    <AbuseReports api={publicApiUrl()} /></>);
}
