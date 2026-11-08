# Verified sources

Presentation reference rechecked on November 8, 2026: the previously hosted news and patch feeds returned HTTP 200 with raw RSS. Their shared-item titles, summaries, HTML, creator fields, product categories, and Media RSS attributes were compared directly. Publisher article Open Graph images preserve the exact `imgs.callofduty.com` URLs used there. MIME types were confirmed from HTTPS headers or a bounded image signature read when the CDN returned `application/octet-stream`; no extension-derived MIME values are published. The former feeds are presentation references only; official indexes remain the runtime content source.

Checked on **November 8, 2026** using live public requests with TLS verification. The initial MW4 index was also read through Firecrawl, and official current news URLs were independently discovered through Exa. Feed data comes from direct publisher HTML, never search snippets. Raw inspection captures are local, ignored `.scratch/` files and are not published.

| Source | Live result | Role / observation |
| --- | --- | --- |
| [Call of Duty Blog](https://www.callofduty.com/blog) | HTTP 200, HTML | Broad catch-up discovery; real article cards have their own product tags and dates. |
| [Modern Warfare 4 Blog](https://www.callofduty.com/blog/modernwarfare4) | HTTP 200, HTML | Primary MW4 discovery. Contains recent news and patch documents; these require separate classification. Forty cards were present, with dated blog/patch URLs deduplicated before use. |
| [Warzone Blog](https://www.callofduty.com/blog/warzone) | HTTP 200, HTML | Cross-game and Warzone discovery; historical and Mobile content must be excluded. |
| [Patch Notes index](https://www.callofduty.com/patchnotes) | HTTP 200, HTML | Current three cards: MW4 Season 1, Warzone Season 1, and Black Ops 7 Season 06. Season numbers are not a chronology; BO7 is outside this project's patch scope. |
| [MW4 Season 1 Patch Notes](https://www.callofduty.com/patchnotes/2026/11/call-of-duty-modern-warfare-4-season-1-patch-notes) | HTTP 200, HTML | Visible studio: Infinity Ward. Dated November 7 and November 6 headings; separate November 5 additional-update and launch identities. Real anchors: `nov7`, `nov6`, `addnov5`, `nov5`. |
| [Warzone Season 1 Patch Notes](https://www.callofduty.com/patchnotes/2026/11/call-of-duty-mw4-warzone-season-1-patch-notes) | HTTP 200, HTML | Visible studio: Raven Software. Real dated release headings/anchors `nov7`, `nov5`. Current document integrates MW4; unrelated earlier Warzone eras remain excluded. |
| [robots.txt](https://www.callofduty.com/robots.txt) | HTTP 200, text/plain | `User-agent: *`, `Allow: /`; advertises the sitemap. Runtime access checks still apply on subsequent fetches. |
| [sitemap.xml](https://www.callofduty.com/sitemap.xml) | HTTP 200, application/xml | Discovery fallback only. `lastmod` is never used as an article or hotfix publication timestamp. |
| [YourBot Social Feeds](https://yourbot.gg/docs/social-feeds) | HTTP 200, live Firecrawl read | Confirms Generic RSS / Atom, 900-second RSS default polling, initial newest-item seeding, image/embed settings, and that Test posts a real message. Documentation does not establish whether RSS identity uses GUID or URL. |

No publisher-provided RSS link was advertised by the inspected index/article `link[rel=alternate]` elements. This is an observation about those representations, not a claim that no publisher RSS endpoint exists anywhere. No guessed endpoint is used.

## Revalidated extraction rules

Index links use `.title a` **inside `.card-inner`**, with `.news-published[data-date]`, `.game-list .game-tile`, `.tag-list .tag-item[data-value]`, and `img.blog-image` (`data-src` or `src`). Product evidence must remain local to each card. The wide blog indexes include many older articles; their DOM position is not a publication timestamp.

Articles use `main#main-content.article-layout-content`, the header's `h1.title`, `.dateline`, `.byline`, `.tags`, `.article-header-container .feature-img img`, and `.article-body-container .body-content`. Substantive content is in `.article-rich-text`. Side navigation, CTAs, recommendations, scripts, style blocks, and site navigation are excluded. A site-wide “Get MW4” link is not relevance evidence.

Populated article JSON-LD supplies exact timezone-bearing dates when available; otherwise visible source dates and verified card dates are used. Current Warzone patch JSON-LD has empty date/description values, and generic JSON-LD authors can conflict with visible studio bylines. Visible specific bylines take precedence. Date-only source values remain date-only in state and are normalized to 00:00:00 UTC solely for RSS syntax, not represented as exact publisher timestamps.

## Dated patch sections

An update continues through GLOBAL, MULTIPLAYER, DMZ, and other headings until the **next dated update**, regardless of heading level. Current Warzone headings omit the year; the visible article date and canonical year's context establish 2026. MW4's launch introduction has the actual publisher anchor `nov5` and a corresponding “Season 1 Intro” table-of-contents entry rather than a printed dated heading. Its source date comes from that publisher-created November 5 anchor in this November 2026 document. It is separate from the printed additional November 5 update (`addnov5`).

Stable update keys use real anchors when available and otherwise source date plus normalized update label. Tables retain ordered rows/cells and numeric signs, percentages, decimals, and units in semantic digests. Presentation tags, classes, and styles do not enter digests. If no reliable dated sections exist, the whole substantive article is a documented fallback baseline, not an invented update history.

## YourBot compatibility decision

Because current public documentation does not prove GUID-based deduplication, each patch event uses its own deterministic static project permalink, including genuine revision events. Each page retains the exact official source/section link, source date, and feed observation date. Standard RSS GUIDs remain present. No Discord subscription, test action, or message was created during endpoint verification. RSS validity and image metadata do not establish Discord-side delivery or rendering.

The documentation uses Services → Social & Patches, then New Feed → All sources → Generic RSS / Atom. “Send an embed” and its item text/image switches control rendering. The initial check marks the newest entry as seen rather than posting a backlog. “Test (posts for real)” must be left to the user. The page has conflicting unrelated quota/history/Kick details; this project does not depend on those claims.

## Publisher attribution

Official article text, studios, images, source dates, and links belong to their respective publisher. This project republishes short excerpts and metadata with prominent source links. It is independently maintained and is not operated or endorsed by Activision.
