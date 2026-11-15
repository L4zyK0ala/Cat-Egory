export const dynamic = "force-dynamic";

/** Liveness for Prometheus (job cat-egory-site): it scrapes this address and alerts when there is no answer.
 *  The body is the exposition format so the scrape counts as a success; it says nothing about the system. */
export function GET() {
  return new Response("# TYPE category_site_up gauge\ncategory_site_up 1\n",
    { headers: { "Content-Type": "text/plain; version=0.0.4; charset=utf-8", "Cache-Control": "no-store" } });
}
