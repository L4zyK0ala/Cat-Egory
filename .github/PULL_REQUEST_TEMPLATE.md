## What and why

## Checklist

- [ ] `ruff check .` and `pytest` pass (integration tests with `CATEGORY_TEST_DATABASE_URL`)
- [ ] Tests added or updated for new behaviour
- [ ] Schema change → Alembic migration included
- [ ] Taxonomy change → no id/code renamed or reused, `version` bumped
- [ ] No secrets, third-party screenshots/HTML, or data whose licence does not allow it
- [ ] Commits signed off (`git commit -s`, [DCO](https://developercertificate.org/))
