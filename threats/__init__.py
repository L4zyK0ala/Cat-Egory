"""Security-threat module: confirmed threats (phishing, malware, look-alikes, ...) on domains and single URLs.

Kept apart from content categorisation on purpose. A threat record is either *domain-scoped* (``url_id`` is
NULL: the whole host is malicious) or *URL-scoped* (one page on a host that is otherwise legitimate, e.g. a
hacked WordPress site). Only domain-scoped records may flag a whole domain, appear in domain blocklists or count
as a "threat domain". Design notes and roadmap: documentation/THREATS.md.
"""
