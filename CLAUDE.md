# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository overview

This repo has two unrelated things in it:

1. **The hosts blocklist aggregator** (root of the repo) — a fork of [StevenBlack/hosts](https://github.com/StevenBlack/hosts). Python tooling that merges dozens of third-party blocklists (ads, malware, tracking, and optional categories like porn/gambling/social/fakenews) into unified `/etc/hosts`-format files.
2. **`shopify-theme/`** — an unrelated Shopify Online Store 2.0 theme for a clothing brand, built in this repo as a workspace of convenience. It has no connection to the hosts tooling; see `shopify-theme/SETUP.md` for its own docs. Nothing below in this file applies to it.

## Hosts aggregator: commands

```sh
pip install -r requirements.txt      # deps: requests, flake8

python testUpdateHostsFile.py        # run the full test suite (unittest)
python -m unittest testUpdateHostsFile.TestNormalizeRule -v   # run one test class
python -m unittest testUpdateHostsFile.TestNormalizeRule.test_valid_domains -v  # run one test method

flake8                                # lint (config in setup.cfg: max-line-length=135)

python updateHostsFile.py -a          # rebuild only the root unified `hosts` file (auto mode, no prompts)
python makeHosts.py                   # full build: unified hosts + every extension combo under alternates/, then regenerates all readmes
```

CI (`.github/workflows/`) runs, in order: `pip install -r requirements.txt`, `flake8`, `python makeHosts.py`, `python testUpdateHostsFile.py`, across Python 3.9–3.14 on Linux/macOS/Windows.

## Hosts aggregator: architecture

- **`data/<SourceName>/`** — one folder per upstream blocklist source (e.g. `data/StevenBlack`, `data/URLHaus`, `data/KADhosts`). Each contains `update.json` (metadata: name, description, source `url`, license) and a cached `hosts` file. These are always-included, "basic" sources.
- **`extensions/<category>/<SourceName>/`** — same shape as `data/`, but scoped to one of four opt-in categories: `fakenews`, `gambling`, `porn`, `social`. A source can appear once per category.
- **`updateHostsFile.py`** is the core engine: fetches/refreshes each source's `hosts` file, normalizes and merges rules, applies `blacklist`/`whitelist` (user-local additions/exclusions, gitignored — copy from `blacklist.example`/`whitelist.example`), and writes the merged result to `hosts` (or `-o <subfolder>` for extension builds). Key CLI flags: `-a` (auto/no-prompt), `-e <ext...>` (include extension categories), `-s` (skip static/basic hosts — used for "-only" extension variants), `--nounifiedhosts`, `-o <path>` (output subfolder).
- **`makeHosts.py`** orchestrates the full product matrix: builds the base unified hosts file, then recursively builds every combination of the four extension categories (16 combos × 2 variants — "with base hosts" and "-only") into **`alternates/<combo>[-only]/`**, then calls `updateReadme.py`.
- **`updateReadme.py`** + **`readme_template.md`** + **`readmeData.json`** — `readme.md` (and the per-alternate readmes) are generated, not hand-edited. Change `readme_template.md` to change the generated docs.
- **`testUpdateHostsFile.py`** — unittest suite covering `updateHostsFile.py`'s internals (rule normalization, exclusion matching, source updates, file writes) using mocked filesystem/network.
- Root **`hosts`** and everything under **`alternates/`** are build artifacts of the above scripts — don't hand-edit them; edit the source data or scripts instead.

See `codebase_structure.md` for the original upstream description of this layout and `contributing.md` for the (upstream) PR workflow.
