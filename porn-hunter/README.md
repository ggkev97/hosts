# porn-hunter

A tool that builds a **semantic search index** over video thumbnails from Pornhub, xVideos and xHamster, lets
you search it in plain English, downloads the matches with **yt-dlp**, and keeps itself up to date with a
**cron-style scheduler**. Use it from the **command line** or from a **local web app**; both share one
`config.yaml` and one index.

```
 search pages ──requests+BeautifulSoup──▶ thumbnails ──open_clip ViT-B-32──▶ embeddings ──▶ FAISS (cosine)
                                                                                     │
 "blonde in fishnets on a couch" ──CLIP text tower──▶ query vector ──────────────────┘ ranked results
                                                                                     │
                                                              queue ──yt-dlp──▶ output_dir/
```

> **Use responsibly.** You must be an adult (18+) and comply with the laws where you live. Scraping and
> downloading may violate a site's terms of service; the polite delays in `config.yaml` are there to keep load low,
> so don't turn them off. Only download content you are entitled to download.

## Requirements

- Python **3.11+** (pinned NumPy 2.x needs it)
- [ffmpeg](https://ffmpeg.org/) on your `PATH` (yt-dlp uses it to merge separate video and audio streams)
- ~1 GB disk for PyTorch and the CLIP weights (roughly 600 MB, downloaded from Hugging Face on first use), plus space
  for thumbnails and videos
- A GPU is optional (`model.device: auto` uses CUDA or Apple MPS if present, otherwise CPU).

## Setup

```bash
cd porn-hunter
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt      # add -r requirements-dev.txt (or install it instead) to run the tests
```

Edit `config.yaml` (see [Configuration](#configuration)). At minimum set `paths.output_dir` and
`index.queries`. Every command reads `./config.yaml`; use `-c /path/to/config.yaml` to point elsewhere.

The first command that embeds something (`index` or `search`) downloads the model weights once and caches them
(set `model.cache_dir` to choose where).

## Usage

All commands are `python -m porn_hunter [-c config.yaml] <command>`.

### 1. Index

```bash
python -m porn_hunter index                              # use index.queries from config.yaml
python -m porn_hunter index -q "red dress" -q "beach"    # ad-hoc queries
python -m porn_hunter index --site xvideos --pages 5     # one site, deeper crawl
```

For every site × query it fetches `pages_per_query` search-result pages, extracts each video's URL, title and
thumbnail, downloads the thumbnails, embeds them with `ViT-B-32` / `laion2b_s34b_b79k`, and adds them to the
index. Videos already indexed are skipped, so re-running is cheap and only adds what is new. A site that is down
or blocking you is logged and skipped; the other sites carry on.

### 2. Search

```bash
python -m porn_hunter search "blonde in fishnets on a couch"
python -m porn_hunter search "red lingerie" -k 50 --min-score 0.22 --site xvideos --new-only
python -m porn_hunter search "outdoor, sunset" --json
```

The query is embedded with CLIP's text encoder and compared with the stored thumbnail embeddings by **cosine
similarity** (vectors are L2-normalised and searched with a FAISS inner-product index). Each result shows its
score, the database id, site, title, URL and download status.

Notes on scores: CLIP text-to-image cosine similarities are small numbers (well below 1.0 even for good
matches), so calibrate `--min-score` on your own index by looking at a few searches, then use it as a relevance
floor and `-k` as a cap. Only the thumbnail is embedded, so queries work best for things that are visible in a
still frame.

### 3. Download

```bash
python -m porn_hunter queue add "blonde in fishnets on a couch" -k 5    # queue the top 5 new matches
python -m porn_hunter queue list
python -m porn_hunter download --limit 3 --quality 720p                 # work through the queue
python -m porn_hunter download --query "red dress" -k 3                 # search + queue + download
python -m porn_hunter download --id 12 40                               # specific ids from `search`
python -m porn_hunter download --url "https://..."                      # any URL yt-dlp supports
```

- **Quality:** `download.quality` or `--quality`: `best`, `1080p`, `720p` (any `NNNp` works). A cap picks the best
  stream at or below that height; if nothing that small exists it takes the smallest stream available instead of
  the largest.
- **Format:** `download.format` or `--format`: `mp4` (default), `mkv` or `webm`. For mp4 it prefers mp4 video
  plus m4a audio so merging needs no re-encode.
- **Output:** `paths.output_dir`, laid out by `download.filename_template` (a yt-dlp output template).
- **Rate limiting:** a random pause drawn from `[min_delay, max_delay]` seconds separates downloads. Failed
  downloads are retried with exponential backoff (`retries`, `retry_backoff`). If a site answers *429 / too many
  requests* the downloader waits `rate_limit_backoff` (growing on each retry) and, if it still fails, skips that
  site's remaining videos for the run. Those videos stay queued, and the throttling does not count against them.
  Videos that are gone (404, removed, private) fail immediately, and after `max_attempts` failed runs a video is
  marked `failed`. `queue add-id <id>` puts it back in the queue.

### 4. Schedule

```bash
python -m porn_hunter schedule run         # foreground loop (Ctrl-C / SIGTERM to stop cleanly)
python -m porn_hunter schedule next        # show when each job will next run
python -m porn_hunter schedule once index  # run one job right now (for system cron)
python -m porn_hunter schedule once download
```

`schedule run` evaluates two standard 5-field cron expressions in local time:

| job        | default        | what it does                                                                         |
|------------|----------------|--------------------------------------------------------------------------------------|
| `index`    | `0 3 * * *`    | re-indexes `index.queries` (new videos only), then runs any `scheduler.auto_queue` searches and queues their best new matches |
| `download` | `*/30 * * * *` | downloads up to `download.max_per_run` videos from the queue                          |

A failing job is logged with its traceback and the loop carries on. A job that overruns its interval does not
trigger a burst of catch-up runs. Jobs share a lock file (`paths.lock_file`), so a scheduled run, a
cron-launched run and a manual `index` / `download` never overlap. The loser logs a warning and (for the CLI) exits
with status 75.

Prefer your system's cron or a systemd timer? Skip the loop and call the one-shot commands:

```cron
0 3 * * *     cd /path/to/porn-hunter && .venv/bin/python -m porn_hunter schedule once index
*/30 * * * *  cd /path/to/porn-hunter && .venv/bin/python -m porn_hunter schedule once download
```

Auto-queueing example (`config.yaml`):

```yaml
scheduler:
  auto_queue:
    - {query: "red dress on a balcony", top_k: 5, min_score: 0.25}
```

### 5. Web app

```bash
python -m porn_hunter web                 # http://127.0.0.1:8765/
python -m porn_hunter web --scheduler     # same, and also run the cron scheduler inside this process
```

A browser UI over the same index and download queue:

- **Search**: a natural-language box with filters (result count, minimum score, sites, hide downloaded) and a
  thumbnail grid. Each card shows the similarity score, site, duration and download status, with **Queue**,
  **Download** and **Source** buttons.
- **Queue**: queued, failed (with the error) and downloaded videos; remove or retry individual items, or
  **Download queue now**.
- **Status**: counts per site and status, a form to start an **index job** (queries, pages, sites), the
  schedule with next run times, a job history, and the tail of the log.
- Index and download jobs run in the background (one at a time) and show progress in a banner. They share the lock
  file with the CLI and scheduler, so a web-started job never overlaps one started elsewhere.
- Thumbnails are **blurred until hovered** by default (`web.blur_thumbnails`; the **Blur** button toggles and
  remembers your choice). It has a light and a dark theme and works on a phone.

The first start loads the CLIP model (and downloads the weights once). If that fails, for example offline, the
pages other than search still work and the log says why.

**Security model.** The app can start downloads and scrape sites, so it is locked down by default:

- It listens on **127.0.0.1 only**. Binding any other address (`--host 0.0.0.0` or `web.host`) is **refused unless
  you set a token** (`PORN_HUNTER_WEB_TOKEN` or `web.auth_token`, at least 12 characters). With a token you get a
  login page, and scripts can send `Authorization: Bearer <token>`. Failed logins are throttled.
- Plain HTTP is used, so over a network the token travels unencrypted. Put a TLS reverse proxy (Caddy, nginx) in
  front of it, and add its hostname to `web.allowed_hosts` if you run without a token behind it.
- Every state-changing request needs a CSRF token and `SameSite=Strict` cookies are used. Requests with an
  unexpected `Host` header are rejected (DNS-rebinding defence). A strict Content-Security-Policy is sent, there is
  no inline script or style, scraped titles are HTML-escaped, and only `http(s)` source links are rendered.
- It is a single-user tool served by Werkzeug's threaded server, not a hardened multi-user service.

### Other commands

```bash
python -m porn_hunter stats      # counts by site and download status
python -m porn_hunter rebuild    # rebuild the FAISS index from the SQLite database
```

## Logging

Everything is logged with timestamps to `paths.log_file` (rotating, `logging.max_bytes` × `backup_count`) and to
stderr:

```
2026-10-03 03:00:01 INFO     porn_hunter.scheduler: job index: starting
2026-10-03 03:04:12 INFO     porn_hunter.indexer: index run finished: scraped=412 new=37 already_indexed=375 ...
2026-10-03 03:30:05 WARNING  porn_hunter.retry: download https://... failed (HTTP Error 429); retry 1/3 in 300.0s
```

## Configuration

`config.yaml` is the single source of settings and is validated on load. Typos in key names, wrong types, bad
cron expressions or an invalid quality all stop the program with a clear message. Relative paths resolve against
the config file's directory.

| section     | what it controls                                                                                          |
|-------------|-----------------------------------------------------------------------------------------------------------|
| `paths`     | data dir, thumbnails dir, **output dir**, log file, lock file                                             |
| `logging`   | level and rotation                                                                                        |
| `model`     | open_clip model/pretrained tag, device (`auto`/`cpu`/`cuda`/`mps`), batch size, weight cache              |
| `http`      | user agent, timeout, retries/backoff, polite delay between requests, thumbnail size limit, proxy          |
| `sites`     | **search endpoints** (`search_url` with `{query}` and `{page}`), first page number, cookies, enable/disable |
| `index`     | default queries, pages per query, max new videos per run                                                  |
| `search`    | default `top_k` and `min_score`                                                                           |
| `download`  | **quality, format**, filename template, delays, retries, backoff, attempts, per-run cap, rate limit, cookies file, raw yt-dlp options |
| `scheduler` | **cron expressions**, run-on-start flags, `auto_queue`                                                    |
| `web`       | web UI host/port, auth token, allowed hosts, thumbnail blur, in-process scheduler, results per page       |

## How it is stored

- `data/videos.db` (SQLite) is the source of truth: video metadata, each embedding, and download status.
- `data/index.faiss` is a derived FAISS index (`IndexIDMap2` over `IndexFlatIP`). If it is missing, corrupt, or
  out of step with the database, it is rebuilt automatically at startup, so a crash mid-run can't corrupt your data.
- The index remembers which model built it. Changing `model.name` / `model.pretrained` against an existing data
  dir is refused, because embeddings from different models are not comparable. Point `paths.data_dir` somewhere
  new to start over.

## Development and tests

```bash
pip install -r requirements-dev.txt
python -m pytest            # from the porn-hunter directory
```

The suite runs offline. The web app is tested through Flask's test client (escaping, CSRF, Host checks, auth,
job handling, path traversal) and over a real socket; its JavaScript was additionally checked by hand in headless
Chromium, but there is no automated browser test. Scrapers are tested against HTML fixtures in `tests/fixtures/`, HTTP is mocked with
`responses`, the downloader's format strings are checked against yt-dlp's real selector engine, and one test runs
real yt-dlp against a local HTTP server. Search and indexing run end to end with a deterministic colour-based fake
embedder (so "red" demonstrably retrieves the red thumbnail). The real ViT-B-32 graph is exercised with random
weights to verify preprocessing, tokenisation, batching and normalisation. One test (`-m model`) loads the real
pretrained weights and **skips itself when they can't be downloaded**.

## Troubleshooting

- **No results parsed from a site (`no results parsed for ...` in the log).** Sites change their markup. The
  per-site parsers are in `porn_hunter/scrapers/`. Each is a short function with selector fallbacks, and the
  fixtures in `tests/fixtures/` show the structure each expects. Some pages also sit behind a bot check or age gate
  that plain `requests` cannot pass; `sites.<name>.cookies` and `http.proxy` can help.
- **HTTP 403/429 while indexing.** Increase `http.min_delay`/`max_delay` and lower `pages_per_query`.
- **Weights fail to download.** The first run needs access to Hugging Face. Retry, set `HF_HOME`/`model.cache_dir`
  to a writable location, or pre-download the weights on a connected machine.
- **yt-dlp starts failing with extractor errors.** Sites change, so upgrade it: `pip install -U yt-dlp` (then
  update the pin in `requirements.txt`).
