import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

// Read at request time: the same image runs as the public site and, with UI_ADMIN=true, as the admin UI.
export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: { default: "Cat-Egory — open web categorization", template: "%s · Cat-Egory" },
  description: "Open database of website categories, built by AI classification and community review.",
};

function Brand({ size = 30 }: { size?: number }) {
  return (
    <>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src="/logo.svg" alt="" width={size} height={size} />
      <span>Cat-<b>Egory</b></span>
    </>
  );
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const adminUi = process.env.UI_ADMIN === "true";
  return (
    <html lang="en">
      <body>
        <header className="top">
          <div className="inner">
            <Link href="/" className="brand" aria-label="Cat-Egory home"><Brand />{adminUi && <span className="badge pending">admin</span>}</Link>
            <nav aria-label="Main">
              <Link href="/">Dashboard</Link>
              <Link href="/categories">Categories</Link>
              <Link href="/feeds">Feeds</Link>
              <Link href="/submit">Suggest a site</Link>
              {adminUi && <Link href="/admin" className="admin">Admin</Link>}
            </nav>
          </div>
        </header>
        <main>{children}</main>
        <footer>
          <div className="inner">
            <span className="brand"><Brand size={22} /></span>
            <span>Code Apache-2.0 · Classification data CC BY 4.0 (Cat-Egory contributors)</span>
            <span>AI classifications are machine estimates; verified entries were confirmed by a person.</span>
          </div>
        </footer>
      </body>
    </html>
  );
}
