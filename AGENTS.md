# AGENTS.md — 给 Agent / 助手的接入说明

本仓库是 **Qt Widgets 代码 Wiki 生成器**（纯 Python 标准库）。  
Agent 读完本文件 + `README.md` 即可直接操作，无需猜测。

## 你要做的事（典型）

用户说「给这个 Qt 工程生成/更新代码 wiki」时：

```bash
# 1) 扫描（只读目标工程源码，不改生产代码）
python tools/scan_signal_graph.py --repo <目标工程绝对路径>

# 2) 渲染 vault
python tools/render_wiki.py --repo <目标工程绝对路径> --html all

# 3) L0 校验，必须 0 问题
python tools/render_wiki.py --repo <目标工程绝对路径> --lint
```

本地自测（本仓自带 demo，无需装 Qt）：

```bash
python tools/scan_signal_graph.py --repo examples/demo-qt-app --include-untracked
python tools/render_wiki.py --repo examples/demo-qt-app --html all
python tools/render_wiki.py --repo examples/demo-qt-app --lint
```

## 事实源与红线

| 产物 | 谁可以改 |
|------|----------|
| 目标工程 `.h/.cpp/.ui` | **永不改**（本工具只读） |
| `signal_graph.json` | 只许扫描器写，禁手编 |
| vault 里 `<!-- DERIVED:start ... -->` 区块 | 只许渲染器写，禁手改 |
| 类页 narrative（职责/分组/生命周期） | 人 / LLM 写，改后 `verified` 字段要更新 |

手改 derived 的修复路径：改源码或改渲染逻辑后 **重跑 compile**，不要直接编辑标记内文本。

## 分层（避免误判「重复」）

1. **信号/槽清单** ← 解析 `.h` 的 `signals:` / `slots:`（invokeMethod、直接调用也在此）
2. **接线边** ← `connect()` + `on_*` 隐式 + Designer `<connections>`
3. **可选对账** ← `--verify-moc <build_dir>` 用 `moc_*.cpp` 抓漏收（无 moc 则跳过）

同一槽出现在「槽表」和多条「边」上是正常的，不要去重。

## 配置（目标工程根，可选）

- `qt_code_wiki.json`：排除目录、输出路径、`assert_no_on` 等（见 `qt_code_wiki.example.json`）
- `modules.json`：类 → 模块（见 `modules.example.json`）；未命中 →「未分组」

CLI 优先于配置文件。完整参数：`python tools/scan_signal_graph.py -h`

## 治理与增量维护

页面 Schema、compile/update/lint、派工护栏、增量锚点流程：

→ 通读 `rules/llm_wiki.md`

要点：

- compile 幂等：同代码同 HEAD 两次结果逐字节一致
- `compile_meta.compiled_at_commit` / `prev_compiled_at_commit` 圈增量
- narrative 只在功能定型点更新；开发中间态只跑 compile

## 目录地图

```
tools/          扫描 / 渲染 / 配置 / moc 对账
tools/schemas/  signal_graph.json 契约
rules/          wiki 治理规则（agent 写 narrative 时必读）
examples/       demo 工程 + 合成 moc（验证用）
```

## 不要做的事

- 不要给本工具加「顺手格式化」目标源码的逻辑
- 不要把目标工程业务代码或密钥写进本仓库
- 不要在未 `--lint` 通过时宣称 wiki 已更新
- 非 Qt / QML 工程：明确告知未覆盖，不要硬跑

## 已知限制（如实告知用户）

- 多行函数声明 / 类内 inline 可能漏收
- `#ifdef` 内 connect 仍会入图
- 链式 sender（`a->b()`）不建边
- 仅 `.pro` Widgets；无 CMake/QML
