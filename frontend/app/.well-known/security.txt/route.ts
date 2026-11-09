import { contactEmail } from "@/lib/site";

export const dynamic = "force-dynamic";

/** RFC 9116. Expires must stay under a year away, so it is computed. No contact address, no file. */
export function GET() {
  const email = contactEmail();
  if (!email) return new Response("Not found\n", { status: 404 });
  const expires = new Date(Date.now() + 180 * 86_400_000).toISOString();
  return new Response(`Contact: mailto:${email}\nExpires: ${expires}\nPreferred-Languages: tr, en\n`,
    { headers: { "Content-Type": "text/plain; charset=utf-8" } });
}
