import type { Metadata } from "next";
import Link from "next/link";
import { CatRoll } from "@/components/CatScene";

export const metadata: Metadata = {
  title: "About",
  description: "Who builds Cat-Egory, why an open categorisation of the web matters, and the rules we follow.",
};

const PRINCIPLES: [string, string][] = [
  ["Open by Default",
   "Our own categorisations and the taxonomy are published under CC BY 4.0 and the code under Apache-2.0. Use the data, check it, build on it."],
  ["People Decide When It Matters",
   "A language model is a fast first reader, not the final word. Unsure answers, possible look-alikes of brands and every security threat go to a person. The AI never overrides a human decision, and a model's claim never becomes a public threat flag."],
  ["Every Label Has a Source",
   "Each category says whether it came from the AI, a reviewer, the community, a registry rule or a licensed list, and its history is kept. Nothing is relabelled silently."],
  ["Respect for Websites",
   "Our crawler says it is a bot, obeys robots.txt and runs in a sandbox. Screenshots and HTML of websites are never published, and data from other projects keeps its own licence."],
];

export default function About() {
  return (
    <div className="about">
      <section className="hero">
        <div>
          <h1>About <em>Cat-Egory</em></h1>
          <p className="lead">An independent, open project that sorts the world&apos;s websites into an open, versioned set of
            categories — and shows its work.</p>
        </div>
        <figure className="kostum">
          <CatRoll />
          <figcaption className="cat-name">Mr Kostüm</figcaption>
        </figure>
      </section>

      <h2>Our Mission</h2>
      <div className="panel stack">
        <p>Knowing what a website is matters every day: DNS filters at schools and homes, security teams triaging alerts,
          researchers studying the web. The lists that answer this today are mostly closed and paid. You cannot see why
          a site got its label, and you cannot fix it when it is wrong.</p>
        <p><b>Cat-Egory is the open alternative:</b> a categorisation that anyone can use, audit and correct, with a
          clear record of where every label came from.</p>
      </div>

      <h2>What We Stand For</h2>
      <ul className="principles">
        {PRINCIPLES.map(([title, text]) => (
          <li key={title} className="panel"><h3>{title}</h3><p className="muted">{text}</p></li>
        ))}
      </ul>

      <h2>Who We Are</h2>
      <div className="panel stack">
        <p>Cat-Egory is an independent open-source project started in Türkiye by <b>Ahmet Çetin</b>
          (<a href="https://github.com/L4zyK0ala" rel="noopener">@L4zyK0ala</a> on GitHub), who also maintains it. It pays
          special attention to Turkish websites and to an early warning for phishing sites that imitate Turkish banks and
          public services.</p>
        <p className="muted">Classification uses language models served by the EVREN platform. Cat-Egory is not
          affiliated with or endorsed by EVREN.</p>
      </div>

      <h2>How You Can Help</h2>
      <div className="grid2">
        <div className="panel stack">
          <h3>Fix the Data</h3>
          <p className="muted">Look up a site and report a wrong category from its page, or suggest a site we have not
            seen yet.</p>
          <p className="row"><Link href="/">Look Up a Site →</Link><Link href="/submit">Suggest a Site →</Link></p>
        </div>
        <div className="panel stack">
          <h3>Use the Data</h3>
          <p className="muted">Download categories as TXT, hosts, CSV, JSON or DNS RPZ files, or query the REST API.
            Please credit &ldquo;Cat-Egory contributors&rdquo;.</p>
          <p className="row"><Link href="/feeds">Feeds →</Link><Link href="/categories">Categories →</Link></p>
        </div>
      </div>
    </div>
  );
}
