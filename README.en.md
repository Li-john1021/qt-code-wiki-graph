# qt-code-wiki-graph

[中文](README.md) | **English**

A **Qt signal/slot–centric code wiki generator**: compile a C++/Qt Widgets repository into an Obsidian-friendly vault (class pages, module wiring pages, offline interactive HTML graphs), with governance rules (derived anti-tamper hashes, human/LLM narrative, lint, commit-anchor incremental updates).

> Extracted and generalized from a production codebase (91 `Q_OBJECT` classes / 703 `connect` edges).  
> Pure Python 3 stdlib, zero third-party deps. The target only needs to be a git repo.  
> **Agents: read [`AGENTS.md`](AGENTS.md) first.**

## Architecture (code is the single source of truth)

```
Code (read-only) ──① tools/scan_signal_graph.py──▶ signal_graph.json (SSOT, deterministic & idempotent)
                                                   └ optional: --assert-no-on
signal_graph.json ──② tools/render_wiki.py──▶ code_wiki/
                      ├── signals/<module>.md      full-page derived (mermaid wiring + tables)
                      ├── components/<Class>.md    class page = 3 narrative sections (human/LLM)
                      │                              + derived data (functions/signals/slots/widgets, hash-guarded)
                      ├── interactive/*.html       layered force graph (no CDN, offline, drill-down)
                      ├── signals/missing-comments.md
                      └── index.md / log.md
Class-page narrative ──③ LLM subagents in batches + spot-check (see rules/llm_wiki.md)
```

Key design decisions:

| Decision | What it means |
|----------|----------------|
| derived / narrative split | Machine blocks wrapped in `<!-- DERIVED:start hash=xxx -->`; lint recomputes the hash to catch hand-edits. Humans/LLMs only write narrative; re-render preserves it. |
| Deterministic compile | Same code + same HEAD → byte-identical JSON/pages (`sort_keys`, no timestamps). Diff = real change. |
| Baseline = git tracked | Default: only committed files; in-flight parallel work is isolated (`--include-untracked` overrides). |
| Commit-anchor increments | Each compile writes HEAD into `compile_meta.compiled_at_commit` and keeps the previous anchor; update uses `git diff <prev>` to scope affected class pages. |
| Granularity | Graph nodes at function level; one page per class. Module graphs show project-class↔project-class only (widgets filtered out). |
| Comments as semantics | The “semantics” column in derived tables comes from `.h` brief comments; gaps go to `missing-comments`. |

## Quick start

```bash
# Prerequisites: Python 3.8+ (stdlib only); target is a git repo
# Keep this repo's tools/ anywhere and pass --repo

# ① Scan → signal_graph.json
python tools/scan_signal_graph.py --repo /path/to/your-qt-project

# ② Render vault + interactive HTML
python tools/render_wiki.py --repo /path/to/your-qt-project --html all

# ③ L0 lint
python tools/render_wiki.py --repo /path/to/your-qt-project --lint

# Open <repo>/code_wiki in Obsidian ("Open folder as vault")
```

Bundled demo (no Qt install required):

```bash
python tools/scan_signal_graph.py --repo examples/demo-qt-app --include-untracked
python tools/render_wiki.py --repo examples/demo-qt-app --html all
python tools/render_wiki.py --repo examples/demo-qt-app --lint
```

Incremental maintenance: compile → `git diff --name-only <prev_anchor>` to scope affected class pages → LLM updates only those narratives → append `log.md` → commit in batches. Full flow: `rules/llm_wiki.md` §4/§7.

## Configuration (optional)

Place at the **target** repo root:

| File | Purpose |
|------|---------|
| `qt_code_wiki.json` | Output paths, extra exclude dirs, free-function exclude prefixes, `assert_no_on`, etc. |
| `modules.json` | Class → module (or module → class list); unmatched → “Ungrouped” |

Templates: `qt_code_wiki.example.json` / `modules.example.json`.  
CLI flags override config files. See `python tools/scan_signal_graph.py -h`.

Common flags:

```
--repo PATH            Target project root (default: parent of tools/)
--out-root PATH        Vault root (default: <repo>/code_wiki)
--pro PATH             .pro file (default: auto-discover root *.pro)
--modules PATH         Module mapping JSON
--exclude-dir NAME     Extra exclude directory (repeatable)
--include-untracked    Scan uncommitted files
--assert-no-on         Fail if on_ auto-connect slots/strings ≠ 0 (default off)
--verify-moc BUILD_DIR Optional: reconcile signal/slot lists against moc_*.cpp
--check                Assert-only scan (no artifacts)
```

### Optional: `--verify-moc` oracle

The **source of truth remains .h/.cpp scanning** (no build required).  
`moc_*.cpp` is a build artifact used only as a **second-source check** for missed/over-parsed declarations:

```bash
python tools/scan_signal_graph.py --repo /path/to/qt-project --verify-moc /path/to/build
# or
python tools/verify_moc.py --repo /path/to/qt-project --build /path/to/build
```

- No moc files → skip, non-blocking  
- Non-empty set difference → print `moc_only_*` / `scan_only_*`, exit code 1  
- Compare by **method name** (signatures vary across Qt versions)  
- Does **not** replace `slots:` parsing: `invokeMethod` / direct calls never appear in `connect`

## Layout

```
AGENTS.md                     Agent onboarding (read first after clone)
tools/
  wiki_config.py              Shared config (CLI / qt_code_wiki.json / modules.json)
  scan_signal_graph.py        Deterministic scanner (classes/signals/slots/connect/widgets)
  render_wiki.py              Vault renderer (modules/classes/HTML/lint/DERIVED hash)
  verify_moc.py               Optional moc reconciliation oracle
  gen_connections_manifest.py connect parsing helpers used by scan
  gen_ui_manifest.py          .pro FORMS + .ui parsers used by scan
  schemas/signal_graph.schema.json   Graph JSON v1.0 contract
rules/
  llm_wiki.md                 Wiki governance (page schema / ops / dispatch / increments)
examples/demo-qt-app/         End-to-end demo (4 Q_OBJECT classes + .ui + PMF/lambda/cross-thread)
```

## Scope

- ✅ C++ / Qt Widgets (`.pro`, PMF `connect`, uic widget trees)  
- ✅ Sources in subdirectories (recursive scan; exclude thirdparty/build via config)  
- ❌ Non-Qt projects, QML-first apps (not covered)  
- Semantic columns depend on `.h` brief comments; otherwise drive fixes via `missing-comments`

## Known limitations (issues welcome)

- Multi-line declarations / in-class inline definitions may be missed  
- `connect` inside `#ifdef` still enters the graph  
- Chained senders (`m_x->sub()`) are not edges (use the signal table for out-edges)  
- `ui_*` / `moc_*` / `qrc_*` generated files are excluded (they would shadow real classes)

## License

MIT
