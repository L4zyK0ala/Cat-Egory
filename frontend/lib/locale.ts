// The visitor's language: their choice (cookie set by the switch), else the browser's, else Turkish.
import "server-only";
import { cookies, headers } from "next/headers";
import { LANG_COOKIE, type Locale, makeT } from "./i18n";

export async function getLocale(): Promise<Locale> {
  const chosen = (await cookies()).get(LANG_COOKIE)?.value;
  if (chosen === "tr" || chosen === "en") return chosen;
  const accepted = (await headers()).get("accept-language") ?? "";
  if (!accepted) return "tr";
  const first = accepted.split(",").map((p) => p.trim().slice(0, 2).toLowerCase()).find((p) => p === "tr" || p === "en");
  return first === "en" ? "en" : first === "tr" ? "tr" : "en";
}

export async function getT() {
  const locale = await getLocale();
  return { locale, t: makeT(locale) };
}
