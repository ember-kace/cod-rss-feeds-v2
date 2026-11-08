# Add the two feeds to YourBot

The current [Social Feeds documentation](https://yourbot.gg/docs/social-feeds) was checked on November 8, 2026. RSS endpoints are a separate service from Discord delivery; no Discord message was sent as part of implementation.

1. Open the server dashboard, go to **Services**, and enable **Social & Patches** / **Social Feeds**.
2. Open **New Feed**, then **All sources**, and choose **Generic RSS / Atom**. Create these two subscriptions separately:

   | Display name | Feed URL |
   | --- | --- |
   | Modern Warfare 4 — Official Blog | https://ember-kace.github.io/cod-rss-feeds-v2/feeds/modern-warfare-4.xml |
   | MW4 & Warzone — Patch Updates | https://ember-kace.github.io/cod-rss-feeds-v2/feeds/patch-updates.xml |

When replacing an existing subscription, update its feed URL to the corresponding URL above or disable the previous subscription before creating a replacement. Do not leave both versions enabled for the same destination. Event GUIDs and dates are preserved; patch project links use this repository's Pages base.

3. Set **Channel, thread or forum** to your intended COD updates destination. Set **Poll interval (seconds)** to **900**. Keep **Send an embed** enabled, showing the item text and item image. Leave **Ping roles (optional)** empty and **Allow @everyone and @here** off. An optional display icon is your choice; article images come from the official item when available.
4. Ensure YourBot has **View Channel**, **Send Messages**, and **Embed Links** in that destination. **Manage Webhooks** is needed only if you choose webhook posting. Use **Create Subscription** for each feed.
5. The first check marks the newest item as seen and starts posting with subsequent new items; it does not post the initial backlog. GitHub source refresh and YourBot polling are independent, so 900 seconds is a target poll interval rather than an end-to-end delivery guarantee.
6. Use the nonposting form preview to review presentation. **Test (posts for real)** sends an actual Discord message immediately; that action remains for you. **History** shows delivered items. After five consecutive check failures YourBot pauses the subscription; fix the displayed cause and use **Reset**. **Skip to current** seeds the newest item without posting backlog.

YourBot says ordinary source edits do not update previously sent notifications. Each detected substantive patch revision therefore has both a stable RSS GUID and its own project permalink, with official source/anchor and source/detection dates shown. Public docs do not specify whether Generic RSS identity uses GUID, URL, or a combination, so this project does not rely on GUID-only behavior. Image selection and embeds depend on YourBot's parser and Discord settings; XML/Media RSS validity does not prove a particular rendered Discord image. No actual Discord delivery is claimed until you test it yourself.

The feeds are independently maintained summaries of official Call of Duty content. Activision does not operate or endorse this hosting project.
