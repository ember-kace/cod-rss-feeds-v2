# Call of Duty RSS feeds

Independently maintained RSS feeds of official Call of Duty content. Activision does not operate or endorse this project. Only short excerpts, metadata, images supplied by the publisher, and links are republished.

- **Modern Warfare 4 — Official Blog:** <https://ember-kace.github.io/cod-rss-feeds-v2/feeds/modern-warfare-4.xml>
- **MW4 & Warzone — Patch Updates:** <https://ember-kace.github.io/cod-rss-feeds-v2/feeds/patch-updates.xml>
- [Landing page](https://ember-kace.github.io/cod-rss-feeds-v2/) · [Collection status](https://ember-kace.github.io/cod-rss-feeds-v2/status.json) · [Actions runs](https://github.com/ember-kace/cod-rss-feeds-v2/actions/workflows/refresh.yml)

The news feed stays about Modern Warfare 4, including cross-game articles only when their own metadata or article content establishes relevance. Patch documents take precedence and appear only in the patch feed. The patch feed covers MW4 and current mainline Warzone, excluding Mobile and historical Warzone eras. Future seasons are discovered automatically; a new annual title does not rename or repurpose the MW4 feed.

## How it works

Python makes bounded, conditional HTTPS requests to official indexes and new news articles, plus current patch pages and recently superseded pages for a 14-day overlap. Beautiful Soup extracts metadata and dated patch sections from the article body. Section boundaries are the next dated heading, rather than arbitrary subheadings. Table cells, numbers, signs, percentages, and units remain part of semantic change detection.

The versioned ledger in `data/state.json` is authoritative. It retains canonical identities, stable section keys, previously observed semantic digests, immutable first-observed/event times, and identity tombstones after feed retention pruning. News edits keep their original GUID. A new patch section or a substantive section revision creates a deterministic event GUID. Cosmetic edits and reverted, already-seen revisions do not replay. A section without a usable date is quarantined; a patch article with no reliably separated dated sections uses a documented whole-article baseline/revision fallback.

Initialization selects up to 20 genuine entries per feed from the previous 30 days. Subsequent publications retain the latest 60 per feed. Initial patch baselines use the source date; later discoveries and revisions use the immutable feed observation time, with the official date explicitly retained. Date-only publisher fields normalize to midnight UTC for RSS and remain labeled as calendar-date precision; that convention does not claim a publisher-supplied release time.

RSS 2.0 is serialized as UTF-8 XML and independently checked with feedparser. Each patch event has a permanent page under `updates/` linking prominently to the exact official article/real section anchor. This protects consumers that deduplicate using item links: YourBot's public documentation does not establish its exact GUID-versus-URL identity algorithm. Existing event pages remain published even after they leave the feed. Images use Media RSS and sanitized HTML; reader support varies. No full article copies, AI service, browser runtime, or paid feed service are required.

The presentation matches the previous Call of Duty Feed Desk feeds: a short summary, the publisher's original CDN image, and “Read the official update,” with matching CDATA descriptions, concise dated patch titles, bylines, product categories, and verified Media RSS image types. Source/detection details stay on permanent patch event pages, which also provide the correct image in Open Graph previews. This repository preserves the event GUIDs, source and observation timestamps, semantic digests, and revision history. Patch permalinks and Atom self-links use this repository's Pages base URL. It retains the current feed scopes and separate same-day updates rather than copying old Mobile/historical entries or merging distinct updates. Use `scripts/refresh.py --refresh-metadata` when repairing current article metadata; this refetches current news without reinitializing identities.

The entire candidate publication is staged and both feeds are validated before tracked output changes. Invalid XML, partial source outages, challenge/error pages, corrupt state, or suspicious extraction loss fail the run and leave the last valid public feeds intact. `public/manifest.json` hashes served content, excluding itself, operational status, and the deployment-only `.nojekyll` marker. Every successful build compares the served manifest and feed/event bytes, so a failed deployment is retried even if the valid state/output commit already succeeded. Post-deployment verification checks the landing page and every retained event page against the complete manifest, using the exact commit that produced the uploaded artifact.

## Run locally

This repository starts with fresh Git history and includes the initialized durable ledger and event records. Cloning it requires a normal refresh, not initialization. Earlier commits and local working files are not part of this repository.

Use Python 3.13 and the tested versions in `requirements.txt`. For Windows/PowerShell with uv:

```powershell
git config --local user.name ember-kace
git config --local user.email '268197113+ember-kace@users.noreply.github.com'
uv venv --python 3.13
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe scripts/check_privacy.py
.venv\Scripts\python.exe scripts/refresh.py
.venv\Scripts\python.exe scripts/validate.py
.venv\Scripts\python.exe scripts/verify_deployment.py --full-manifest
```

On Linux/macOS, create a virtual environment with `python3.13 -m venv .venv`, install using `.venv/bin/python -m pip install -r requirements.txt`, and use `.venv/bin/python` for the same commands. A fresh installation requires an explicit `scripts/refresh.py --initialize`; an established installation must restore its ledger rather than initialize again. No credentials are needed for source fetching or endpoint verification. GitHub publishing uses the existing GitHub CLI login or the repository's scoped Actions token; never store access tokens in project files.

Configuration is in `config/feeds.json`: official source allowlist, product aliases, discovery URLs, overlap and retention limits, summary length, and the Pages base URL. See [SOURCES.md](SOURCES.md) for observed selectors and source verification, and [YOURBOT_SETUP.md](YOURBOT_SETUP.md) for subscriptions.

## Operations and recovery

The production workflow targets `7,22,37,52 * * * *` in UTC: every 15 minutes at minutes 7, 22, 37, and 52. RSS `ttl=15` is a cache hint. GitHub scheduling may be delayed or dropped, and YourBot polls on its own schedule. Runs use a fixed concurrency group, do not cancel an active publication, read the latest default-branch state, and never force-push. Generation, validation, persistence, artifact upload, and deployment run in one workflow; a bot-token state commit is not expected to trigger another workflow.

Dispatch, inspect, or re-enable the actual repository workflow:

```powershell
gh workflow run refresh.yml -R ember-kace/cod-rss-feeds-v2 --ref main
gh run list -R ember-kace/cod-rss-feeds-v2 --workflow refresh.yml --limit 5
$rssRunId = gh run list -R ember-kace/cod-rss-feeds-v2 --workflow refresh.yml --limit 1 --json databaseId --jq '.[0].databaseId'
gh run view $rssRunId -R ember-kace/cod-rss-feeds-v2 --log-failed
gh workflow enable refresh.yml -R ember-kace/cod-rss-feeds-v2
```

The log command selects the latest workflow run. If a public repository has no activity for 60 days, GitHub can disable its scheduled workflows. Re-enable and manually dispatch with the commands above. A genuine daily successful-collection checkpoint records observed source checks and feed counts, with bounded history; empty keep-alive commits are not used. Review Actions for finer failure diagnostics. The published status file describes the last successfully deployed checkpoint, not independently monitored live health; a failed run cannot update the public status file.

Before modifying parsers or filters, run the fixture suite and inspect a live staged refresh. Preserve existing canonical/section aliases and known digests during migrations; do not clear the ledger, change the parser version to force replay, invent anchors, or synthesize public patch events. New selectors must operate inside actual article/card containers, never global navigation. If a migration cannot reconcile identities safely, fail and repair it before publication.

Git history backs up the state and validated output. If an established ledger is missing/corrupt, recover the last valid `data/state.json` from Git history; the refresher attempts this safely before failing. To recover manually, inspect `git log -- data/state.json`, restore the chosen valid state with `git restore --source=COMMIT -- data/state.json`, validate it, then refresh and commit the repair. Restore matching `public/` output as needed and retain newer ledger identities so already published events cannot replay. Never run initialization to repair lost state.

For a bad deployment, preserve a backup of the current ledger and revert the faulty source/config commit. Restore the last valid publication and reconcile all already-published GUIDs/digests into the ledger before refreshing. Validate locally, push the repair, and dispatch again. A deployment failure after a valid state commit needs only another dispatch: live-hash comparison will publish the pending valid output without creating duplicate events.

## Hosting

This runs on GitHub independently of ChatGPT and your computer. Standard public-repository Ubuntu runners and public GitHub Pages are available without a paid plan under the terms verified for this project. Artifact storage has separate allowances; only `public/` is uploaded, artifacts expire after one day, and no Actions cache or larger runner is used. No billing setting, domain purchase, or paid subscription is required or configured. Current Pages limits include a 1 GB published site, soft 100 GB/month bandwidth, and a 10-minute deployment timeout. Custom Actions publishing is exempt from the soft 10-builds/hour limit. Provider policies, free offerings, suspension rules, and source access can change. See [hosting verification](docs/hosting-verification.md) for primary sources and pinned action releases.

Endpoint validation is separate from Discord delivery. This project does not send Discord messages or invoke YourBot's real-posting Test action.

## Deployment verification

The [deployment verification record](docs/deployment-verification.json) records observed results for this repository's production endpoints. Verification checks anonymous HTTPS access, XML content type, independent RSS parsing, self-links, GUIDs, images, feed hashes, and referenced patch pages. The [presentation comparison](docs/presentation-verification.json) records matching images, summaries, titles, bylines, categories, and description markup. Workflow history shows actual publishing and repeat-refresh results; a configured schedule does not establish that a scheduled run has occurred.

The privacy check rejects credentials, personal email and hosting references, private tracked files, and unexpected Git author identities, including removed historical content and commit messages. GitHub Actions runs it before collection, on staged generated output before committing, and on the final commit before pushing. Use the repository-local handle and no-reply identity shown above for human commits.

Parser migrations retain digest aliases so an existing update keeps its GUID and dates when date-heading presentation changes. Corrupt or incomplete section histories fail validation and can recover from the last complete Git version rather than replaying updates. Same-day sections with identical displayed labels remain distinct when they have different real source anchors.
