# Can `codex exec` run headlessly on a ChatGPT plan (not per-token API)?

Research for marvin roadmap issue #712, open question #1. Date: 2026-07-10.

**Verdict: YES.** `codex exec` runs non-interactively under ChatGPT-account (plan) auth, and this machine is already configured that way. Empirically verified locally (see 2c). API keys are what OpenAI *recommends* for automation, but they are not *required*; plan auth works headlessly and bills against the ChatGPT plan's usage limits.

---

## 1. Local setup (evidence)

- `codex --version` -> `codex-cli 0.142.5`
- `codex login status` -> **"Logged in using ChatGPT"** (exact CLI output)
- `~/.codex/auth.json` (field NAMES only, values never inspected):
  - `auth_mode` (str)
  - `OPENAI_API_KEY` -> **null** (no API key stored)
  - `tokens.id_token`, `tokens.access_token`, `tokens.refresh_token`, `tokens.account_id` (OAuth token set = ChatGPT-account mode)
  - `last_refresh` (str) — evidence of automatic token refresh
- `~/.codex/config.toml`: `model = "gpt-5.5"`, `model_reasoning_effort = "medium"`, per-project `trust_level = "trusted"` entries (including `/Users/matthewdruhl/marvin`). No provider/API-key config.
- Claude Code codex plugin (`~/.claude/plugins/cache/openai-codex/codex/1.0.4/`): only auth note is in `commands/setup.md` line 37: "If Codex is installed but not authenticated, preserve the guidance to run `!codex login`." The plugin assumes whatever auth the CLI already has; no API-key requirement.

**Conclusion (a): this machine uses ChatGPT-account (plan) auth. No API key present.**

## 2. Does headless `codex exec` work under plan auth?

### 2a. CLI surface
`codex exec --help`: "Run Codex non-interactively." No auth flag exists on `exec` — it uses whatever credentials are in `$CODEX_HOME/auth.json` (note `--ignore-user-config` explicitly says "auth still uses `CODEX_HOME`"). `codex login --help` offers three routes: browser OAuth (default), `--device-auth` (headless device-code flow), `--with-api-key` (stdin). So auth mode is orthogonal to exec.

### 2b. Official docs
- README (github.com/openai/codex): "We recommend signing into your ChatGPT account to use Codex as part of your Plus, Pro, Business, Edu, or Enterprise plan." API key is the alternative "but this requires additional setup."
- Auth docs (developers.openai.com/codex/auth -> learn.chatgpt.com/docs/auth), headless-machine section lists three supported approaches for ChatGPT-account auth without a local browser:
  1. `codex login --device-auth` (device-code flow, no browser on the target machine)
  2. copy `~/.codex/auth.json` from an authenticated machine to the headless environment
  3. SSH port-forward `localhost:1455` for the OAuth callback
  and notes "API keys are still the recommended default for automation" — recommended, not required.
- Non-interactive docs (developers.openai.com/codex/noninteractive -> learn.chatgpt.com/docs/non-interactive-mode): `codex exec` is for "scripts and CI/CD pipelines"; `CODEX_API_KEY` is framed as "the right default for automation because they are simpler to provision and rotate," with an explicit **"advanced path for teams needing account-based access"** (i.e. plan auth in automation is documented, just discouraged for public/untrusted CI because `~/.codex/auth.json` must be handled "like a password: it contains access tokens").

### 2c. Empirical proof (this machine, 2026-07-10)
```
codex exec --sandbox read-only --skip-git-repo-check "Reply with exactly the word OK and nothing else."
```
Ran with NO `OPENAI_API_KEY`/`CODEX_API_KEY` in the environment and `OPENAI_API_KEY: null` in auth.json. Output header: `provider: openai`, `model: gpt-5.5`, `approval: never`, `sandbox: read-only`; agent replied `OK`; `tokens used 7,276`. This billed to the ChatGPT plan (the only credential available). **Headless-under-plan confirmed by direct execution, not just docs.**

**Conclusion (b): yes — `codex exec` works headlessly under ChatGPT-plan auth. Verified locally; documented via the auth docs' headless-machine section and the non-interactive docs' account-based "advanced path."**

## 3. Non-interactive / CI caveats (c)

- **Token refresh is automatic during use**: auth docs: "Codex refreshes tokens automatically during use before they expire, so active sessions usually continue without requiring another browser login." The `tokens.refresh_token` + `last_refresh` fields in auth.json are the mechanism. Caveat: a machine idle long enough for the refresh token to lapse would need a re-login (`--device-auth` works headlessly for that). Docs don't publish the refresh-token lifetime.
- **auth.json is a secret**: docs say to treat it like a password. If the harness copies it to another host/container, it must be protected and is per-account (single `account_id`).
- **Refresh writes back to auth.json**: the CLI updates `last_refresh`/tokens in place, so the file must be writable and not shared read-only across concurrent hosts if refresh races matter.
- **OpenAI's stated preference**: for public repos / untrusted CI, they push API keys or the official Codex GitHub Action. For a personal, single-user local harness (the marvin case) the plan-auth path has no stated functional restriction.
- Known bug class (community, unverified): "phantom limit" reports (openai/codex issue #19215, Apr 2026) where `/status` shows capacity but exec reports a limit hit. Watch for spurious limit errors in long harness runs.

## 4. Rate-limit implications (d)

- Plan auth bills against ChatGPT plan usage limits, not per-token: shared **5-hour rolling window** plus a **weekly cap**, credit-weighted (heavy reasoning burns faster). Limits are shared across ALL Codex surfaces (CLI local messages, IDE extension, Codex cloud) — harness usage competes with interactive usage.
- Published ranges (chatgpt.com/codex/pricing, as of mid-2026): Plus roughly 15-80 GPT-5.5 local messages / 5h (more for smaller models); Pro $100 ~5x Plus; Pro $200 ~20x.
- At the cap: wait for window reset, buy add-on credits, or fall back to an API key at standard per-token rates (exactly what the harness wants to avoid).
- `codex exec` has no separate limit; it draws from the same plan pool. `/status` in an interactive session (or the usage dashboard) shows remaining quota.
- Contrast with API-key mode: per-token billing through the Platform account, plus some ChatGPT-workspace/cloud features "limited or unavailable" (auth docs).

## 5. Bottom line for the harness

Parity with `claude -p` + `CLAUDE_CODE_OAUTH_TOKEN` exists: `codex exec` under ChatGPT login consumes the paid plan. Differences: Codex has no `setup-token`-style long-lived token command; its equivalent is the OAuth token set in `~/.codex/auth.json` (auto-refreshing) plus `codex login --device-auth` for headless (re)login. Budget harness throughput against the 5-hour/weekly plan windows, which are shared with Matt's interactive Codex use.

## Sources

- Local: `codex login status`, `~/.codex/auth.json` field names, `~/.codex/config.toml`, `codex exec --help`, `codex login --help`, live `codex exec` run (2026-07-10)
- https://github.com/openai/codex (README auth section)
- https://learn.chatgpt.com/docs/auth (via developers.openai.com/codex/auth; headless-machines + token refresh)
- https://learn.chatgpt.com/docs/non-interactive-mode (via developers.openai.com/codex/noninteractive; codex exec + CI guidance)
- https://chatgpt.com/codex/pricing/ and https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan (plan limits; help.openai.com returned 403 to direct fetch, figures via search results)
- https://simplemetrics.xyz/chatgpt-codex-limits-2026/, https://inventivehq.com/blog/codex-cli-usage-rate-limits (community limit breakdowns, secondary sources)
