# Reliable daily start (Cloudflare Worker)

GitHub's built-in schedule has fired hours late or not at all, so a free
Cloudflare Worker starts the daily run instead. One-time setup, about 10 minutes.

## 1. Create a GitHub token that can only start this workflow

1. Open https://github.com/settings/personal-access-tokens/new (fine-grained token).
2. Name: `tech-on-the-go trigger`. Expiration: 1 year (set a reminder to renew it).
3. Repository access: **Only select repositories** → `tech-on-the-go`.
4. Permissions → Repository permissions → **Actions: Read and write**. Leave everything else as "No access".
5. Generate, and copy the token (starts with `github_pat_`). Don't paste it into chat.

## 2. Deploy the Worker

In a terminal, from the project folder:

```sh
cd trigger
npx wrangler login
npx wrangler deploy
npx wrangler secret put GITHUB_TOKEN
```

- `wrangler login` opens the browser: sign in to the Cloudflare account that holds your domain and approve.
- `secret put` asks for the value: paste the token from step 1 and press Enter.

The Worker has no web address (`workers_dev = false`); it only runs on its schedule.
The free Workers plan covers this (two runs a day).

## 3. Check it

The next morning, the run on
https://github.com/Agarwal-Sudhanshu/tech-on-the-go/actions should show
"workflow_dispatch" at about 04:53 India time. Cloudflare's dashboard shows each
cron run under Workers & Pages → tech-on-the-go-trigger → Logs.
