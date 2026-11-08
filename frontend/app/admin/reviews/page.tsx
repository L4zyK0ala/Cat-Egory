import Link from "next/link";
import { publicApiUrl } from "@/lib/api";
import ReviewQueue from "@/components/ReviewQueue";
import QuickReview from "@/components/QuickReview";

export const dynamic = "force-dynamic";

export default async function Reviews({ searchParams }: { searchParams: Promise<{ view?: string }> }) {
  const list = (await searchParams).view === "list";
  return (<>
    <h1>Review queue</h1>
    <p className="muted">Model output is a claim. Approve it, set the right category, or reject. Confirmed threats become
      public risk flags. Approved and corrected answers become human-verified data.</p>
    <p className="row">{list ? <Link href="/admin/reviews">Quick mode (one at a time, keyboard)</Link>
                             : <Link href="/admin/reviews?view=list">List view</Link>}</p>
    {list ? <ReviewQueue api={publicApiUrl()} /> : <QuickReview api={publicApiUrl()} />}
  </>);
}
