"""The site lists phishing and malware addresses: none of them may ever become a link a visitor can click to open it.
A listed name or a scanned URL only links to our own pages (/domain/..., /categories/...)."""
import re
from pathlib import Path

FRONTEND = Path(__file__).parents[2] / "frontend"
HREF = re.compile(r"href=\{([^}]*)\}")
# fields that hold a listed name or a scanned/searched address
LISTED = re.compile(r"\b(domain|display_domain|registrable_domain|normalized_domain|final_url|normalized_url|matched|"
                    r"lookup|link_url|url_text)\b")
INTERNAL = ("`/", '"/', "'/")


def hrefs():
    for f in sorted(list((FRONTEND / "app").rglob("*.tsx")) + list((FRONTEND / "components").rglob("*.tsx"))):
        for n, line in enumerate(f.read_text().splitlines(), 1):
            for m in HREF.finditer(line):
                yield f"{f.relative_to(FRONTEND)}:{n}", m.group(1).strip()


def test_found_the_links():
    assert sum(1 for _ in hrefs()) > 20  # the pattern still matches the code


def test_listed_names_and_addresses_are_never_links_out():
    bad = [(where, expr) for where, expr in hrefs()
           if not expr.startswith(INTERNAL) and (LISTED.search(expr) or re.search(r"https?://\$\{|`//", expr))]
    assert not bad, f"a listed name or address would open the site itself; link to /domain/... instead: {bad}"
