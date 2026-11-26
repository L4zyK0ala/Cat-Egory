"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import LanguageSwitch from "./LanguageSwitch";
import { useLocale } from "./LocaleProvider";

/** Phones: the main menu and the language switch behind one button (the wrapped row of links took half the screen).
 *  Closes when a link is followed or Escape is pressed. Wider screens show the plain nav instead (globals.css). */
export default function MobileMenu({ links, footer }: { links: { href: string; label: string }[]; footer: { href: string; label: string }[] }) {
  const [open, setOpen] = useState(false);
  const path = usePathname();
  const tr = useLocale() === "tr";
  useEffect(() => setOpen(false), [path]);
  useEffect(() => {
    if (!open) return;
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [open]);
  return (
    <div className="mobile-menu">
      <button type="button" className="menu-button" aria-expanded={open} aria-controls="mobile-menu-panel"
              aria-label={open ? (tr ? "Menüyü kapat" : "Close menu") : (tr ? "Menüyü aç" : "Open menu")}
              onClick={() => setOpen((v) => !v)}>
        <svg aria-hidden="true" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          {open ? <path d="M6 6l12 12M18 6 6 18" /> : <path d="M4 7h16M4 12h16M4 17h16" />}
        </svg>
      </button>
      {open && (
        <div id="mobile-menu-panel" className="menu-panel">
          <nav aria-label={tr ? "Ana menü" : "Main"}>
            {links.map((l) => (
              <Link key={l.href} href={l.href} aria-current={l.href === path ? "page" : undefined}
                    onClick={() => setOpen(false)}>{l.label}</Link>
            ))}
          </nav>
          <div className="menu-lang"><span>{tr ? "Dil" : "Language"}</span><LanguageSwitch long /></div>
          <div className="menu-foot">
            {footer.map((l) => <Link key={l.href} href={l.href} onClick={() => setOpen(false)}>{l.label}</Link>)}
          </div>
        </div>
      )}
    </div>
  );
}
