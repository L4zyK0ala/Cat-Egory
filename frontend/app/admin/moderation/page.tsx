import { publicApiUrl } from "@/lib/api";
import ModerationQueue from "@/components/ModerationQueue";

export const dynamic = "force-dynamic";

export default function Moderation() {
  return (<><h1>Moderation</h1>
    <p className="muted">Community reports and suggestions. Evidence URLs are shown as text; open them only in an isolated browser.</p>
    <ModerationQueue api={publicApiUrl()} /></>);
}
