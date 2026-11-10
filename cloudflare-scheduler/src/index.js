// Cron independent of GitHub Actions schedule. Never log secrets.
const API = "https://api.github.com";
const REPO = "programaciongoone/oracle-always-free-retry";
const WORKFLOW = "oracle-a1.yml";
const SUCCESS_ISSUE = "Oracle A1: solicitud de instancia aceptada";
const MIN_INTERVAL_MS = 25 * 60 * 1000;
const headers = (token) => ({
  "Accept": "application/vnd.github+json",
  "X-GitHub-Api-Version": "2022-11-28",
  "User-Agent": "oracle-a1-cloudflare-cron",
  ...(token ? { Authorization: `Bearer ${token}` } : {}),
});
async function jsonOrThrow(url, token) {
  const response = await fetch(url, { headers: headers(token) });
  if (!response.ok) throw new Error(`GitHub GET failed: HTTP ${response.status}`);
  return response.json();
}
async function tick(env) {
  if (!env.GITHUB_TOKEN) throw new Error("Missing GITHUB_TOKEN secret");
  // Public repository; all checks authenticated to avoid anonymous API rate limits.
  const [runs, issues] = await Promise.all([
    jsonOrThrow(`${API}/repos/${REPO}/actions/workflows/${WORKFLOW}/runs?per_page=5`, env.GITHUB_TOKEN),
    jsonOrThrow(`${API}/repos/${REPO}/issues?state=all&per_page=100`, env.GITHUB_TOKEN),
  ]);
  if ((issues || []).some(issue => issue.title === SUCCESS_ISSUE)) {
    console.log("Success issue found: no more Oracle attempts");
    return;
  }
  const recent = runs.workflow_runs?.[0];
  if (recent) {
    const age = Date.now() - Date.parse(recent.created_at);
    if (!Number.isFinite(age) || age < MIN_INTERVAL_MS) {
      console.log("Recent workflow run found; skipping duplicate");
      return;
    }
  }
  const response = await fetch(
    `${API}/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`,
    {
      method: "POST",
      headers: { ...headers(env.GITHUB_TOKEN), "Content-Type": "application/json" },
      body: JSON.stringify({ ref: "main", inputs: { dry_run: "false" } }),
    },
  );
  if (response.status !== 204 && response.status !== 200) {
    throw new Error(`GitHub dispatch failed: HTTP ${response.status}`);
  }
  console.log("Oracle A1 workflow dispatched successfully");
}
export default {
  async scheduled(_controller, env, ctx) {
    ctx.waitUntil(tick(env));
  },
};
