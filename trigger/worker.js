// Cloudflare Worker that starts the daily GitHub Actions run on time.
// GitHub's own schedules are best-effort and have been hours late or skipped;
// Cloudflare cron triggers fire reliably, so this Worker asks GitHub to start
// the workflow. The workflow itself skips the run if today's episode exists,
// so the fallback slot never produces a duplicate.

const REPO = "Agarwal-Sudhanshu/tech-on-the-go";
const WORKFLOW = "daily.yml";

export default {
  async scheduled(event, env, ctx) {
    const res = await fetch(
      `https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${env.GITHUB_TOKEN}`,
          Accept: "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
          "User-Agent": "daily-brief-trigger",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ ref: "main", inputs: { publish: "true" } }),
      }
    );
    if (res.status !== 204) {
      throw new Error(`GitHub dispatch failed: HTTP ${res.status} ${await res.text()}`);
    }
    console.log(`dispatched ${WORKFLOW} for cron ${event.cron}`);
  },
};
