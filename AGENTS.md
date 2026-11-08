# Project maintenance

Preserve the user's global research routing: Exa for discovery, Firecrawl for known
URLs, browser only for necessary interaction. Research is read-only unless the
user specifically authorizes a remote change. Never publish test patch events.

This project publishes two independent feeds of official Call of Duty content.
Keep MW4 news scoped to MW4. Patch documents belong only in the patch feed.
Keep state, past revision digests and published update pages: do not reinitialize
an established installation. Source/parser changes must preserve stable identities.

Run `python -m unittest discover -s tests -v`, `python scripts/refresh.py`, and
`python scripts/validate.py`, and `python scripts/check_privacy.py` before publication. A collection error must preserve
the complete last-good publication. Never stage the handoff file, virtualenv,
HTTP cache, credentials, or unrelated changes. Use explicit project paths in Git.
Only `public/` is a Pages artifact. No runtime AI, browser, paid service, or pings.

Use this repository's GitHub identity for commits: name `ember-kace`, email
`268197113+ember-kace@users.noreply.github.com`. Set both with repository-local
Git configuration; do not inherit a personal identity from global configuration.
Public files and commit metadata must not include personal email addresses,
personal hosting addresses, credentials, or private service/account information.
