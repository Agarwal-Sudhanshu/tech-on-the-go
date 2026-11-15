# Podcast setup (one-time)

The daily job adds one episode to a static site. You only do the steps below once.

## 1. Fill in the config

Edit `config/channel.json`:

- `author`: your name or the show's publisher name (shown in podcast apps).
- `podcast.owner_email`: an inbox you can read. **This address is public** (it is written into `feed.xml`, which anyone can open). Spotify sends a verification code to it to prove you own the show. Use a dedicated address if you do not want your personal one exposed.
- `podcast.base_url`: the public address of the site, **no trailing slash**. For GitHub Pages this is `https://<your-github-username>.github.io/<repo-name>` (for example `https://jane.github.io/ai-brief`). If you later move to your own website, change this value; the feed is rebuilt on the next run. (Existing subscribers only follow if the old feed URL redirects, so decide on the final home early.)
- `podcast.category` / `subcategory` / `explicit`: defaults are Technology / none / false. Category names are case-sensitive and must match Apple's list.

## 2. Make the cover art

Run once (and again whenever you rename the show):

    docker run --rm -v "$PWD:/proj" briefstudio publish/cover.py --out site/cover.jpg

It is 3000x3000 px, RGB JPEG, under 500 KB. (Apple asks for a square image between 1400x1400 and 3000x3000 px, JPEG or PNG, RGB. The ~500 KB limit comes from third-party guides, not Apple's page, so it is a safe target rather than a confirmed rule.)

## 3. Turn on GitHub Pages

1. Make sure the daily job publishes the `site/` folder (for example to a `gh-pages` branch or a Pages artifact; whichever the workflow is set up for).
2. On GitHub open the repo, then **Settings > Pages**.
3. Under **Build and deployment > Source**, pick the option that matches the workflow (**GitHub Actions**, or **Deploy from a branch** and choose the branch and `/ (root)`).
4. Save. After a minute the page shows "Your site is live at ...". Note: a free account needs a public repo for Pages.
5. Open that address in a browser. You should see the episode list.

## 4. Your feed URL

    <base_url>/feed.xml

For example `https://jane.github.io/ai-brief/feed.xml`. Open it in a browser to check it loads, and paste it into a feed validator such as Podbase or Cast Feed Validator if you want a second opinion. Apple requires the feed to be publicly reachable with at least one episode and artwork, so run the job once before submitting.

## 5. Submit to Spotify

1. Go to <https://creators.spotify.com> and log in with any Spotify account.
2. Choose **Find an existing show** (wording may vary), then **Somewhere else**, and paste the feed URL.
3. Spotify emails an 8-digit code to the `owner_email` in the feed. Enter it to verify ownership.
4. Fill in the show details it asks for (language, country, category) and submit.

Spotify's help pages describe the claim flow and the email check (confirmed). I could not retrieve Spotify's own spec page for artwork or audio limits, so the exact numbers (third-party guides quote MP3 at 96-320 kbps) are unconfirmed. Our episodes are 96 kbps mono MP3.

## 6. Submit to Apple Podcasts

1. Create an Apple Account (if needed) and sign in at <https://podcastsconnect.apple.com>.
2. Add a new show, choose to add it with an RSS feed, and paste the feed URL.
3. Fix anything the validator reports, then submit. Review can take days.

Apple's requirements page (confirmed): RSS 2.0 with the iTunes namespace, publicly reachable, host must support HEAD and byte-range requests (GitHub Pages does), each episode has an enclosure with url/length/type and a never-changing GUID, RFC 2822 dates, ASCII-only file names. I could not load Apple's exact on-screen Podcasts Connect steps or its required-tags and artwork pages (they need JavaScript), so the button names above are unconfirmed.

## 7. Other directories

- **YouTube Music:** YouTube accepts podcasts by RSS feed through YouTube Studio (create a podcast from an RSS feed). Not verified here; also note the daily video is already uploaded separately.
- **Amazon Music / Audible:** has a podcast submission portal that takes an RSS feed. Not verified here.
- Many others (Pocket Casts, Overcast, Podcast Addict, Castbox) pick up shows automatically from Apple's directory or accept the feed URL directly.

## After setup

Nothing else is needed: each run updates `feed.xml`, and the directories re-check your feed on their own, usually within hours.
