import { publicApiUrl } from "@/lib/api";
import SgbReport from "@/components/SgbReport";

export const dynamic = "force-dynamic";

export default function Threats() {
  return (<><h1>Threats vs the National List</h1>
    <p className="muted">
      Threats a reviewer confirmed, checked once a day against the malicious-address list of T.C. Siber Güvenlik
      Başkanlığı (USOM). <b>Not on the list</b> are candidates to report through the İhbar Formu on{" "}
      <a href="https://siberguvenlik.gov.tr" target="_blank" rel="noopener noreferrer">siberguvenlik.gov.tr</a>.
      Internal only: the list&apos;s terms do not allow republishing it without written permission.
    </p>
    <SgbReport api={publicApiUrl()} /></>);
}
