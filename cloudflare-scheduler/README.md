# Independent 30-minute Oracle A1 retry scheduler

The Cloudflare Worker is a **separate scheduler** for the existing GitHub Actions
workflow. Its cron runs at minutes 05 and 35 **UTC**. It checks the most recent
Oracle workflow run and skips dispatch when the last run is under 25 minutes old.
It also stops when the repository has an issue named
`Oracle A1: solicitud de instancia aceptada`.

## One-time deployment (requires your own Cloudflare + GitHub authorization)

1. Create a **GitHub fine-grained personal access token** limited to
   `programaciongoone/oracle-always-free-retry`, with repository permissions:
   **Actions: Read and write**, **Issues: Read-only**, **Metadata: Read-only**.
   Set an expiration and rotate as needed. Do **not** commit or share this token.
2. Install Node.js and run:
   ```bash
   cd cloudflare-scheduler
   npx wrangler login
   npx wrangler secret put GITHUB_TOKEN
   npx wrangler deploy
   ```
   Paste the token privately when `wrangler secret put` prompts.
3. Check the Worker under Cloudflare **Workers & Pages → your Worker → Triggers**
   and verify that cron is `5,35 * * * *` (UTC).
4. Inspect Cloudflare's Worker logs and GitHub Actions history after the
   scheduled trigger. It may take several minutes for the trigger to propagate.

## Operation

- Existing GitHub Actions cron may remain as a secondary source of attempts.
- The Worker prevents rapid duplicated dispatches, and the Oracle Python script
  checks for an existing A1 before creating one. Parallel launches from different
  triggers are still theoretically possible without an external lock.
- If the Oracle creation succeeded but the GitHub issue notification failed,
  the Worker can continue dispatching; `oracle_a1.py` will detect the VM.
- To stop: remove Cloudflare's Cron Trigger (or delete Worker) and disable
  the GitHub Actions workflows.
- GitHub token must have **Actions: read and write** to fetch runs and
  trigger `workflow_dispatch`.
- **This directory does not deploy itself.** Deployment must be completed
  with authenticated access to Cloudflare and GitHub.

Cloudflare Cron docs: https://developers.cloudflare.com/workers/configuration/cron-triggers/
GitHub workflow dispatch docs: https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event
