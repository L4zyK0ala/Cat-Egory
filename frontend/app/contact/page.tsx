import type { Metadata } from "next";
import Link from "next/link";
import { contactEmail } from "@/lib/site";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Contact", description: "How to reach Cat-Egory, correct a category or report a security issue." };

export default function Contact() {
  const email = contactEmail();
  const address = email ? <a href={`mailto:${email}`}>{email}</a> : <span className="muted">(address coming soon)</span>;
  return (
    <div className="about">
      <h1>Contact</h1>
      <div className="panel stack">
        <h3>A wrong category, or a site flagged by mistake</h3>
        <p>Open the domain&apos;s page and use <b>Is something wrong?</b>, or <Link href="/submit">suggest a site</Link>.
          A person looks at every report. You do not need to write personal information.</p>
      </div>
      <h2>Personal Data</h2>
      <div className="panel stack">
        <p>Questions and requests about your data (KVKK): {address}. See the <Link href="/privacy">privacy notice</Link>.</p>
      </div>
      <h2>Security</h2>
      <div className="panel stack">
        <p>Found a vulnerability in Cat-Egory? Please tell us privately first: {address}. Machine-readable details are in{" "}
          <a href="/.well-known/security.txt">security.txt</a>.</p>
      </div>
    </div>
  );
}
