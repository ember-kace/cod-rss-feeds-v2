# Hosting and consumer verification

Checked November 8, 2026 using official documentation and the GitHub REST API. The repository is `ember-kace/cod-rss-feeds-v2`; Pages configuration returned `build_type=workflow`, HTTPS enforcement, and the authoritative URL `https://ember-kace.github.io/cod-rss-feeds-v2/`.

| Check | Observed provider behavior | Official source |
| --- | --- | --- |
| Pages availability | Public repositories may use Pages on GitHub Free. | [What is GitHub Pages?](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages) |
| Runner compute and storage | Standard public-repository runners remain free. Larger runners are billed. Artifact storage and caches have separate allowances; the Free artifact allowance is 500 MB shared with Packages. This project uses standard Ubuntu runners, no cache, small Pages artifacts, and one-day retention. | [GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions) |
| Pages limits | Published site limit 1 GB; deployment timeout 10 minutes; soft bandwidth limit 100 GB/month; custom Actions publishing is exempt from the soft 10-builds/hour limit. Rate limiting can still apply. | [Pages limits](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits) |
| Schedule | Runs can be delayed or dropped under load. Inactive public-repository schedules can be disabled after 60 days. Target minute offsets avoid the start of the hour. | [Schedule event](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule) |
| Deployment security | Deployment job requires Pages write, OIDC token write, an environment, and dependency on a successful build. Only the state-persisting job gets contents write. | [Custom Pages workflows](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages) |
| Bot-token pushes | A normal push made with `GITHUB_TOKEN` does not start a second push-triggered workflow. This workflow persists and deploys within the same run. | [Triggering a workflow](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow) |
| Pages API | Project Pages configuration is created with `build_type=workflow`; authoritative `html_url` supplies the base URL. | [Pages REST API](https://docs.github.com/en/rest/pages/pages) |

## Official action release pins

The latest release tag and its commit object were read from `GET /repos/{owner}/{repo}/releases/latest` and `GET /repos/{owner}/{repo}/git/ref/tags/{tag}`. All returned commit objects, requiring no annotated-tag dereference. These are verified pins, not inferred version hashes.

| Action | Verified release | Commit |
| --- | --- | --- |
| [actions/checkout](https://github.com/actions/checkout/releases/tag/v7.0.1) | v7.0.1 | `3d3c42e5aac5ba805825da76410c181273ba90b1` |
| [actions/setup-python](https://github.com/actions/setup-python/releases/tag/v7.1.0) | v7.1.0 | `6d4fd22b20465ccc0f7354476588e8df7c9ca0e0` |
| [actions/configure-pages](https://github.com/actions/configure-pages/releases/tag/v6.0.0) | v6.0.0 | `45bfe0192ca1faeb007ade9deae92b16b8254a0d` |
| [actions/upload-pages-artifact](https://github.com/actions/upload-pages-artifact/releases/tag/v5.0.0) | v5.0.0 | `fc324d3547104276b827a68afc52ff2a11cc49c9` |
| [actions/deploy-pages](https://github.com/actions/deploy-pages/releases/tag/v5.0.1) | v5.0.1 | `368f82528645a54fb793d4d04e342629a3f51346` |

## YourBot

[Social Feeds documentation](https://yourbot.gg/docs/social-feeds) returned HTTP 200. It documents the Services card as Social & Patches, the New Feed / All sources / Generic RSS / Atom flow, a default RSS polling interval of 900 seconds, embeds and per-item images, initial newest-item seeding without backlog, and a real-posting Test action. It also states that ordinary source edits do not edit posted messages. Its public documentation does not disclose the exact Generic RSS deduplication key. The implemented strategy therefore supplies a deterministic project permalink per patch event in addition to a standard stable GUID; each page preserves the official source URL and real anchor. No posting action, bot subscription, or Discord delivery test was performed.

Some unrelated limits on the YourBot page disagree between prose and generated tables; this project uses only two subscriptions and does not rely on a plan-cap figure. Parser image preferences are undocumented, so valid Media RSS and HTML image metadata do not establish the actual Discord rendering.

No spending, paid runner, domain purchase, or subscription was enabled. These observations do not guarantee permanent free-provider availability.
