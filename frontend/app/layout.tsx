import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Cat-egory — open web categorization",
  description: "Open database of website categories, built by AI classification and community review.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="top">
          <div className="inner">
            <Link href="/" className="brand">Cat-egory</Link>
            <nav>
              <Link href="/">Dashboard</Link>
              <Link href="/categories">Categories</Link>
              <Link href="/feeds">Feeds</Link>
              <Link href="/submit">Suggest a site</Link>
              <Link href="/admin">Admin</Link>
            </nav>
          </div>
        </header>
        <main>{children}</main>
        <footer>
          Code: Apache-2.0 · Classification data: CC BY 4.0 (Cat-egory contributors) · AI classifications are machine
          estimates; verified entries were confirmed by a person.
        </footer>
      </body>
    </html>
  );
}
