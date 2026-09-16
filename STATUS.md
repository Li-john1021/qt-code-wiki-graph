# STATUS — 工具链就绪度（公开）

> 配置化剥离与去项目化已完成；可对任意 qmake / Qt Widgets 工程使用。

## 已验证

| 项 | 结果 |
|----|------|
| demo 端到端 scan → render → lint | 通过（sender/receiver 100%，lint 0） |
| JSON 幂等（同代码两次 scan） | 通过 |
| `--verify-moc` PASS / FAIL 路径 | 通过 |
| 配置化（repo/out/modules/exclude） | 完成 |

## 生产就绪判断

- **开源发布 v0.1**：可以
- **大仓日常使用**：建议在真实工程上回归一次解析率与模块映射
- **非目标**：CMake、QML、非 Widgets

## 建议上传范围（GitHub）

**上传**

- `tools/`（含 `schemas/`）
- `rules/`
- `examples/demo-qt-app/`（源码 + `modules.json` + `qt_code_wiki.json` + `build/moc/moc_MainWindow.cpp` 合成样例）
- `README.md` / `AGENTS.md` / `LICENSE` / `.gitignore`
- `qt_code_wiki.example.json` / `modules.example.json`
- 本文件（可选）

**不要上传**

- 任何真实业务仓源码、`code_wiki/` 生成物
- 密钥、内部变更单号、未公开项目名
- `tools/__pycache__/`

## 已知限制

见 `README.md`「已知限制」。
