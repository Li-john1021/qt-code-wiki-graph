# qt-code-wiki-graph

[中文](README.md) | **English**

A **signal/slot-centric code Wiki generator** for Qt Widgets: compile a C++/Qt repository into an Obsidian-readable graph vault (class pages, module wiring pages, offline interactive HTML), plus governance rules (derived anti-hand-edit hashes, human/LLM narrative, lint, commit-anchor incremental updates).

> Extracted from a production codebase (91 `Q_OBJECT` classes, 703 `connect` edges) and generalized.
> Pure Python 3 standard library — zero third-party deps. Target repo only needs to be a git repo.
> **Agents: read [`AGENTS.md`](AGENTS.md) first.** Repo blurb: [`ABOUT.md`](ABOUT.md).

## Architecture (code is the single source of truth)

```
Code (read-only) ──① tools/scan_signal_graph.py──▶ signal_graph.json (deterministic SSOT)
                                                    └ optional: --assert-no-on
signal_graph.json ──② tools/render_wiki.py──▶ code_wiki/
                      ├── signals/<Module>.md       full-page derived (mermaid + tables)
                      ├── components/<Class>.md     narrative blocks (human/LLM)
                      │                              + derived data (hash-guarded)
                      ├── interactive/*.html        force-directed graphs (offline)
                      ├── signals/missing-comments.md
                      └── index.md / log.md
Class narrative ──③ LLM subagents in batches + spot-check (rules/llm_wiki.md)
```

Key design decisions:

| Decision | What it means |
|----------|----------------|
| derived / narrative split | Machine blocks wrapped in `<!-- DERIVED:start hash=xxx -->`; lint recomputes hashes to catch hand-edits; humans/LLMs only write narrative |
| Deterministic compile | Two runs on the same code + HEAD produce byte-identical output |
| Baseline = git tracked | Only committed sources by default (`--include-untracked` to override) |
| Commit anchors | HEAD stored in `compile_meta.compiled_at_commit`; previous anchor kept as `prev` for incremental updates |
| Granularity | Function-level graph nodes; one page per class; module graphs show project-class edges only |
| Comments as semantics | “Semantics” columns come from `.h` briefs; missing ones listed in missing-comments |

## Quick start

```bash
# Prerequisites: Python 3.8+ (stdlib only); target project is a git repo
# Keep tools/ anywhere; point --repo at the Qt project

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

Incremental maintenance: compile → `git diff --name-only <prev_anchor>` to scope affected class pages → update only those narrative sections → append `log.md` → commit in batches. Full flow: `rules/llm_wiki.md` §4/§7.

## Configuration (optional)

Place at the **target** project root:

| File | Purpose |
|------|---------|
| `qt_code_wiki.json` | Output paths, extra exclude dirs, free-function exclude prefixes, `assert_no_on`, etc. |
| `modules.json` | Class → module (or module → class list); unmatched → “Ungrouped” |

Templates: `qt_code_wiki.example.json` / `modules.example.json`.  
CLI flags override config files. See `python tools/scan_signal_graph.py -h`.

Common flags:

```
--repo PATH            Target project root (default: parent of tools/)
--out-root PATH        Vault output root (default: <repo>/code_wiki)
--pro PATH             .pro file (default: auto-discover root *.pro)
--modules PATH         Module mapping JSON
--exclude-dir NAME     Extra exclude directory (repeatable)
--include-untracked    Include uncommitted sources
--assert-no-on         Assert on_ auto-connect slots = 0 (default: off)
--verify-moc BUILD_DIR Optional moc cross-check (see below)
--check                scan: assert only, write nothing
```

### Optional: `--verify-moc` oracle

The source of truth remains **.h/.cpp scanning** (no build required).  
`moc_*.cpp` is a build artifact used **only as a second source** to catch missed multi-line declarations / over-parsing:

```bash
python tools/scan_signal_graph.py --repo /path/to/qt-project --verify-moc /path/to/build
# or
python tools/verify_moc.py --repo /path/to/qt-project --build /path/to/build
```

- No moc files → skip (non-blocking)
- Non-empty set difference → print `moc_only_*` / `scan_only_*`, exit 1
- Compare by **method name** (signatures differ across Qt versions); overloads collapse to one name
- Does **not** replace `slots:` declaration scanning (invokeMethod / direct calls never appear in `connect`)

## Layout

```
AGENTS.md                     Onboarding for coding agents (read first)
tools/
  wiki_config.py              Shared config (CLI / qt_code_wiki.json / modules.json)
  scan_signal_graph.py        Deterministic scanner
  render_wiki.py              Vault renderer + lint + DERIVED hashes
  verify_moc.py               Optional moc oracle
  gen_connections_manifest.py connect parsing helpers (used by scan)
  gen_ui_manifest.py          .pro FORMS + .ui helpers (used by scan)
  schemas/signal_graph.schema.json
rules/
  llm_wiki.md                 Wiki governance template
examples/demo-qt-app/         End-to-end demo (4 Q_OBJECT classes, .ui, PMF/lambda/cross-thread)
```

## Scope

- ✅ C++ / Qt Widgets (`.pro`, PMF `connect`, uic widget trees)
- ✅ Sources in subdirectories (recursive scan; exclude thirdparty/build via config)
- ❌ Non-Qt projects; QML-first projects
- Comment semantics depend on `.h` briefs — otherwise drive work from `missing-comments.md`

## Known limitations (issues welcome)

- Multi-line function declarations / in-class inline definitions may be missed
- `connect` inside `#ifdef` still enters the graph
- Chained senders (`m_x->sub()`) are not edges (out-edges: use signal tables)
- `ui_*` / `moc_*` / `qrc_*` generated files are excluded (they would shadow real classes)

## License

MIT
