# About / 简介

> 用于 GitHub 仓库 About 与首页介绍。可直接复制。

---

## GitHub About（短描述，建议英文为主）

**EN（≈340 chars）**

```
Qt Widgets code Wiki generator: scan signal/slot wiring into an Obsidian graph vault (class pages, mermaid, offline HTML). Deterministic, hash-guarded derived blocks, optional moc cross-check. Pure Python stdlib.
```

**中文（备选短描述）**

```
Qt Widgets 代码 Wiki 生成器：扫描信号槽接线，生成 Obsidian 图谱 vault（类页/接线图/离线交互图）。确定性编译、derived 防手改、可选 moc 对账。纯 Python 标准库。
```

---

## 仓库首页短文案（双语，可放 README 顶或社交预览）

**English**

Turn a Qt Widgets codebase into a living architecture wiki.  
`scan_signal_graph.py` extracts classes, signals, slots, and every `connect` edge; `render_wiki.py` builds an Obsidian vault with mermaid wiring diagrams, per-class pages, and offline interactive graphs. Derived regions are hash-guarded so agents can fill narrative without breaking machine data.

**中文**

把 Qt Widgets 代码库编译成可维护的架构 Wiki。  
扫描器提取类 / 信号 / 槽 / 全部 `connect` 边；渲染器产出 Obsidian vault（mermaid 接线、类页、离线交互图）。derived 区带 hash 防手改，Agent 只写 narrative，不破坏机器数据。

---

## Topics（GitHub 建议标签）

```
qt, qt-widgets, code-wiki, obsidian, signal-slot, static-analysis, moc, documentation, llm, python
```

## 一句话定位

| | |
|--|--|
| **What** | Deterministic Qt signal/slot → Obsidian wiki compiler |
| **Why** | Keep architecture docs in sync with code; safe for LLM co-maintenance |
| **How** | Source scan as SSOT + optional moc oracle + hash-guarded derived blocks |
