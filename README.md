# SkillStars

A static catalog of every GitHub repo about **AI agent skills** with **10,000+ stars**: Claude Code skills and
Agent Skills (`SKILL.md`), MCP servers, agent frameworks, coding-agent rules (`.cursorrules`, `AGENTS.md`),
prompt libraries, RAG/memory, browser and computer use, evals, and the `awesome-*` lists about them.
It groups repos into 10 categories, shows weekly star gains, and refreshes itself every Monday.

Same architecture as HackerPapers: **Python pipeline → static JSON → single-page static site on GitHub Pages,
refreshed by GitHub Actions.** No server, no database, no framework.

```
discover.py ──► data/candidates.json        GitHub Search API, star-range splitting, dedupe by repo id, seeds.txt
enrich.py   ──► data/repos.jsonl            metadata + first 3 KB of README (GraphQL with a token, raw files without)
classify.py ──► data/labels/labels.json     Claude Haiku via Batches API (cached by id + README hash) or keyword rules
snapshot.py ──► data/history/stars.jsonl    one star-count snapshot per ISO week
build_site_data.py ──► site_dist/           index.json, data/r/*.json, categories.json, stats.json, feed.xml, zip
site/index.html                             the whole app (vanilla JS, relative paths, works under /SkillStars/)
```

## Setup

Python 3.10+ (CI uses 3.12) and one dependency:

```bash
make install                      # pip install -r requirements.txt  (just `requests`)
export GITHUB_TOKEN=$(gh auth token)   # optional locally; 30 searches/min instead of 10, and GraphQL
export ANTHROPIC_API_KEY=sk-ant-...    # optional; without it, keyword rules label repos
```

## Run locally

```bash
make update && make serve         # refresh everything, then open http://localhost:8000/
```

`make update` runs `deploy/update.sh` (discover → enrich → classify → snapshot → validation build) and then
`make build`. Without a token, discovery takes about 8 minutes because of the 10 searches/minute limit; search
pages are cached in `.cache/search/` for 20 hours, so an interrupted run resumes where it stopped
(`python3 pipeline/discover.py --max-seconds 120` stops early on purpose).

Other targets: `make build` (rebuild `site_dist/` from `data/` only), `make test`, and the single steps
`make discover | enrich | classify | snapshot`. `python3 pipeline/classify.py --dry-run` prints the prompt and a
cost estimate without calling the API. `python3 pipeline/build.py` runs snapshot + build in one go.

## Deploy (GitHub Pages)

1. Create a GitHub repo named `SkillStars` and push this folder to `main`.
2. **Settings → Pages → Source: GitHub Actions.**
3. Optional: **Settings → Secrets and variables → Actions → New secret** `ANTHROPIC_API_KEY`.
   `GITHUB_TOKEN` is built in; nothing to add.
4. Run the workflow once from the **Actions** tab (*Refresh data and deploy Pages → Run workflow*).

`.github/workflows/pages.yml` deploys on every push to `main`, runs the full pipeline every **Monday at 03:30 UTC**
and on manual dispatch. The scheduled run tests the code, refreshes `data/`, commits it as `github-actions[bot]`,
builds the site and deploys it. Pushes by the bot don't trigger another run.

### When something fails, the old data stays

- `discover.py` refuses to overwrite `data/candidates.json` if the new list is under 70% of the old one
  (an outage or bad token shouldn't wipe the catalog).
- Every data file is written atomically (temp file + rename).
- If discovery or enrichment fails, `deploy/update.sh` puts `candidates.json` and `repos.jsonl` back to their
  pre-run versions (so the two always match) and skips the snapshot, so weekly gains are never computed from stale
  numbers. If the final validation build fails, all of `data/` is restored.
- `data/labels/` (paid LLM labels and the id of an in-flight Anthropic batch) is never rolled back, locally or in
  CI, so a failed run can't orphan a batch or pay for the same labels twice.
- `classify.py` has one time budget per run (`SS_CLASSIFY_MAX_WAIT`, 30 min in CI) shared by collecting last week's
  batch and waiting on a new one. A batch that outlives it is saved and collected next week; expired, deleted or
  repeatedly unreadable batches are dropped and resubmitted. API calls retry on 429/5xx/529.
- In CI the refresh step has a 55-minute timeout and `continue-on-error`; the commit step retries its push three
  times (replaying the bot's `data/` commit on top of any new commits) and a failed push only logs a warning.
  Either way the deploy still runs.
- `build_site_data.py` builds into `site_dist.tmp/`, validates the schema (including that every link is http/https),
  and only then replaces `site_dist/`. If the job fails before deploying, Pages keeps the last good deployment.

### Links from GitHub metadata

Repo homepages are free text set by owners. The pipeline and the page both accept only `http(s)` URLs, add
`https://` to bare domains (`flashml.ai`), and drop anything else (`javascript:`, `data:`, ...).

## Scope and taxonomy

"AI skill repo" = a repo that gives agents or LLM coding tools reusable capabilities. Inclusion rule:
`stargazers_count >= 10000`, not archived, not a fork (`SS_MIN_STARS` overrides the threshold).

Categories are fixed in `pipeline/taxonomy.py`: Claude/Agent Skills, MCP Servers, Agent Frameworks, Coding Agents
and IDE Rules, Prompt Libraries, RAG and Memory, Browser/Computer Use, Evals and Observability, Awesome Lists, Other.
Bump `TAXONOMY_VERSION` after changing them to relabel everything.

With `ANTHROPIC_API_KEY`, Claude Haiku decides scope, category, up to 3 tags and a one-line summary. Without it,
keyword rules do: name/description hits weigh 5, topics 1, README at most 2, and a scope gate that ignores padded
topics. The rules are deliberately conservative but noticeably rougher than the LLM; each repo page shows which
method labeled it. Add repos that searches miss to `pipeline/seeds.txt` (seeds are always in scope).

## Costs

Target: **$0** apart from a few cents of Haiku.

| Item | Cost |
|---|---|
| GitHub Actions (public repo), GitHub Pages, GitHub API with the built-in token | free |
| Claude Haiku, first full labeling (~1,050 candidates, ~1.6M input tokens at Batches API prices) | ~$1, once |
| Claude Haiku, weekly (only repos that are new or whose README excerpt changed) | typically $0.05–0.25 |
| No `ANTHROPIC_API_KEY` | $0 (keyword rules) |

`classify.py` prints the estimate before submitting. `SS_LABEL_MODEL` switches the model.

## Data and license

The site's data is published as `download/skillstars-data.zip` (CSV + JSON + full star history) under **CC0 1.0**.
Repo names, descriptions and README excerpts belong to their authors.

## Tests

```bash
make test        # python3 -m unittest discover -s pipeline/tests
```

Covers discovery dedupe and star-range splitting, the classification fallback and label cache retention, snapshot
idempotence, and the build output schema (also enforced at build time by `build_site_data.validate()`).
CI runs them before every deploy.
