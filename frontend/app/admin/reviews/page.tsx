import { publicApiUrl } from "@/lib/api";
import ReviewQueue from "@/components/ReviewQueue";

export const dynamic = "force-dynamic";

export default function Reviews() {
  return (<><h1>Review queue</h1>
    <p className="muted">Model output is a claim. Approve it, set the right category, or reject. Confirmed threats become public risk flags.</p>
    <ReviewQueue api={publicApiUrl()} /></>);
}
