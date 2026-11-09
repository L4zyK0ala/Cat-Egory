import { publicApiUrl } from "@/lib/api";
import SgbReport from "@/components/SgbReport";

export const dynamic = "force-dynamic";

export default function Threats() {
  return (<><h1>Confirmed Threats</h1>
    <p className="muted">
      Threats a reviewer confirmed. <b>Withdraw</b> takes a flag back (a mistake, or the site was cleaned); it is kept
      in the history and the audit log. Each domain is checked once a day against the malicious-address list of
      T.C. Siber Güvenlik Başkanlığı (USOM): <b>not on the list</b> are candidates to report through the İhbar Formu on{" "}
      <a href="https://siberguvenlik.gov.tr" target="_blank" rel="noopener noreferrer">siberguvenlik.gov.tr</a>.
      Internal only: the list&apos;s terms do not allow republishing it without written permission.
    </p>
    <SgbReport api={publicApiUrl()} /></>);
}
