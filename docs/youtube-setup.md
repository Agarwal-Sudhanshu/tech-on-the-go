# YouTube publishing: one-time Google setup

You do this once, on your own Mac. Afterwards the daily GitHub Actions job uploads videos by itself.

Use the Google account that **owns the YouTube channel**. Google renamed parts of the Cloud console in 2025 (the old "OAuth consent screen" is now called **Google Auth Platform**, with the pages Branding, Audience, Clients, Data Access). If a menu looks slightly different from this guide, look for those names.

## 1. Create a Google Cloud project

1. Open <https://console.cloud.google.com/> and sign in with the channel-owning Google account. Accept the terms if asked.
2. Click the project picker at the top left (next to the "Google Cloud" logo), then **New project**.
3. Name it e.g. `ai-it-daily-brief`. Leave Organization/Location as is. Click **Create**.
4. Make sure the new project is selected in the project picker.

## 2. Enable the YouTube Data API v3

1. Open the left menu (three lines) > **APIs & Services** > **Library**.
2. Search for `YouTube Data API v3`, open it, click **Enable**.

## 3. Configure the consent screen (Google Auth Platform)

1. Left menu > **APIs & Services** > **OAuth consent screen** (this opens **Google Auth Platform**). If you see **Get started**, click it.
2. **App information**: app name (e.g. `Daily Brief Uploader`), user support email (your email). Next.
3. **Audience**: choose **External**. Next.
4. **Contact information**: your email. Next.
5. Tick the agreement checkbox, **Continue**, **Create**.
6. (Optional, not required for uploads) Under **Data Access** you may add scopes, but this is not needed: the helper requests them at sign-in.

### 3a. Move the app from "Testing" to "In production" (important)

While the publishing status is **Testing**, Google expires refresh tokens after about 7 days. The daily job would then start failing a week after setup (error `invalid_grant`). So:

1. In Google Auth Platform, open **Audience**.
2. Under **Publishing status** click **Publish app**, then **Confirm**. The status must read **In production**.
3. You may see a warning that the app is "unverified". That is fine: you are the only user. You do not need to submit it for verification. When you sign in later you will see a "Google hasn't verified this app" screen: click **Advanced** > **Go to ... (unsafe)**. It is your own app.

Do this **before** running the helper in section 5, otherwise the token you get will already be a 7-day token.

## 4. Create the OAuth client and download the JSON

1. Google Auth Platform > **Clients** > **Create client**.
2. Application type: **Desktop app**. Name: anything, e.g. `uploader`. Click **Create**.
3. In the dialog click **Download JSON** (the client secret is only fully shown at creation, so download it now). Save it somewhere outside the project, e.g. `~/Downloads/client_secret_....json`.

## 5. Run the helper

```bash
cd /Users/sudhanshuagarwal/Workspace/PersonalProjects/YouTube
python3 publish/youtube_auth.py --client-secrets ~/Downloads/client_secret_XXXX.json --set-github-secrets
```

- Add `--playlist` if you will fill `youtube.playlist_id` in `config/channel.json` (adding to a playlist needs a broader permission than upload).
- Add `--repo OWNER/REPO` if you run it outside the repo folder.
- Your browser opens. Choose the account/channel, click through the "unverified" warning, and approve.
- The script prints only a success line and **the channel name**. Check it is the right channel.
- Secrets are saved to `.secrets/youtube.json` (git-ignored, mode 600) and, with `--set-github-secrets`, to the GitHub repo secrets `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`. Nothing is printed.

Test without uploading: `python3 publish/youtube_upload.py --dir out/demo --dry-run`.

## 6. API compliance audit (needed for public videos)

Google's rule: videos uploaded through the API by an **unaudited** API project can be restricted to **private**, whatever privacy you request. Until your project passes a YouTube API Services compliance audit, expect uploads to land private. A private video can still be made public by hand in YouTube Studio.

To request the audit:

1. Open the official form: <https://support.google.com/youtube/contact/yt_api_form> ("Audit and Quota Extension Form").
2. Fill in your details, the API client (name, privacy policy and homepage URL, use case) and your Google Cloud **project number** (Cloud console > project picker or Dashboard).
3. Submit. A member of the YouTube API Services team contacts you by email.
4. Note: videos already locked private by this restriction generally cannot be unlocked later; re-upload them after approval.

The form is aimed at businesses, so prepare a simple privacy policy page and homepage for the channel. Default quota is small (see below), enough for one upload a day.

## What I could and could not confirm

Confirmed in Google's official docs:
- `videos.insert` endpoint, resumable upload protocol, `status.containsSyntheticMedia` field, title (100 chars) and description (5000 bytes) limits.
- `youtube.upload` is accepted by videos.insert; `playlistItems.insert` does **not** accept it (needs `youtube` or `youtube.force-ssl`), hence the `--playlist` option.
- Testing-status refresh tokens expire after 7 days; **Publish app** moves to In production.
- Loopback redirect + PKCE flow; the audit form URL; default quota of 100 `videos.insert` calls per day.

Could not confirm / conflicting:
- The "unaudited projects are locked to private" rule: the current `videos.insert` reference page text I retrieved says unverified projects are *not* restricted, while Google's help text and other sources say they are (for projects created after 28 July 2020). Treat it as likely true; check the first upload's privacy in YouTube Studio.
- Exact current button labels in the Cloud console (I could not view the console itself; names come from Google's help pages). Also whether "Download JSON" appears exactly so in the creation dialog.
