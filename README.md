# qt-code-wiki-graph

**中文** | [English](README.en.md)

以 **Qt 信号槽连接为核心** 的代码 Wiki 生成器：把一个 C++/Qt Widgets 仓库编译成
Obsidian 可读的代码图谱 vault（类页 / 模块接线页 / 分层交互 HTML 图），并配套
治理规则（derived 防手改 / narrative 人写 / lint 校验 / commit 锚点增量维护）。

> 从生产项目（91 Q_OBJECT 类 / 703 connect 边）验证后抽出的通用工具链。
> 纯 Python 3 标准库，零第三方依赖；目标仓库只要是 git 仓库即可。
> **Agent 接入请先读 [`AGENTS.md`](AGENTS.md)。** 仓库简介见 [`ABOUT.md`](ABOUT.md)。

## 架构（三层，代码是唯一事实源）

```
代码(只读) ──① tools/scan_signal_graph.py──▶ signal_graph.json（图谱 SSOT，确定性幂等）
                                              └ 可选：--assert-no-on
signal_graph.json ──② tools/render_wiki.py──▶ code_wiki/
                      ├── signals/<模块>.md        整页 derived 禁手改（mermaid 接线+明细表）
                      ├── components/<Class>.md    类页 = narrative 三区块(人/LLM 写)
                      │                              + derived 数据区(函数/信号/槽/控件表,hash 防手改)
                      ├── interactive/*.html        分层力导向图(无 CDN,离线可用,双击 drill-down)
                      ├── signals/missing-comments.md  缺注释清单
                      └── index.md / log.md
类页 narrative ──③ LLM 子代理按批填写 + 独立抽检（规则见 rules/llm_wiki.md）
```

核心设计决策：

| 决策 | 内容 |
|------|------|
| derived/narrative 分离 | 机器生成区带 `<!-- DERIVED:start hash=xxx -->` 标记，lint 重算 hash 抓手改；人/LLM 只写 narrative 区，重渲染增量保留 |
| 确定性编译 | 同代码同 HEAD 两次编译逐字节一致（sort_keys、无时间戳），diff 即真实变化 |
| 编译基线 = git tracked | 默认只编译已提交文件，并行在途文件自动隔离（`--include-untracked` 可覆盖） |
| commit 锚点增量 | 每次编译把 HEAD 写入 `compile_meta.compiled_at_commit`，上次锚点保留为 `prev`；update 时 `git diff <prev锚点>` 机械圈受影响类页 |
| 粒度 | 图谱函数级节点、页面类级一页；模块图只画项目类↔项目类（控件剔除） |
| 注释即语义 | derived 表的"语义"列取自 .h 功能注释（首行 brief），缺注释进 missing-comments |

## 快速上手

```bash
# 前提：Python 3.8+（纯标准库）；目标仓库是 git 仓库
# 将本仓库 tools/ 放到任意位置，用 --repo 指向目标 Qt 工程

# ① 扫描 → signal_graph.json
python tools/scan_signal_graph.py --repo /path/to/your-qt-project

# ② 渲染 vault + 交互图
python tools/render_wiki.py --repo /path/to/your-qt-project --html all

# ③ L0 校验
python tools/render_wiki.py --repo /path/to/your-qt-project --lint

# 用 Obsidian "Open folder as vault" 打开 <repo>/code_wiki 即可浏览
```

本地 demo（仓库自带，无需装 Qt）：

```bash
python tools/scan_signal_graph.py --repo examples/demo-qt-app --include-untracked
python tools/render_wiki.py --repo examples/demo-qt-app --html all
python tools/render_wiki.py --repo examples/demo-qt-app --lint
```

增量维护：compile → `git diff --name-only <prev锚点>` 圈受影响类页 →
LLM 子代理只更新这些页的 narrative → log.md 追加 → 按批 commit。
完整流程见 `rules/llm_wiki.md` §4/§7。

## 配置（可选）

目标仓库根可放：

| 文件 | 作用 |
|------|------|
| `qt_code_wiki.json` | 输出路径、额外排除目录、自由函数排除前缀、`--assert-no-on` 等 |
| `modules.json` | 类名 → 模块名（或模块 → 类列表）；未命中落「未分组」 |

模板见仓库根 `qt_code_wiki.example.json` / `modules.example.json`。
CLI 参数优先于配置文件；详见 `python tools/scan_signal_graph.py -h`。

常用参数：

```
--repo PATH            目标工程根（默认 tools/ 的上一级）
--out-root PATH        vault 输出根（默认 <repo>/code_wiki）
--pro PATH             .pro 文件（默认自动发现根目录 *.pro）
--modules PATH         模块映射 JSON
--exclude-dir NAME     追加排除目录（可多次）
--include-untracked    扫描未提交文件
--assert-no-on         启用 on_ 自动连接 = 0 断言（默认关）
--verify-moc BUILD_DIR 可选：用 moc_*.cpp 对账信号/槽清单（见下）
--check                scan 只断言不写产物
```

### 可选：`--verify-moc` 对账 oracle

事实源仍是 **.h/.cpp 源码扫描**（不依赖构建，clone 即扫）。  
`moc_*.cpp` 是构建产物，**只做第二源交叉验证**，抓「多行声明漏收 / 解析过度」：

```bash
# 已有构建目录（qmake/moc 或 CMake 生成的 moc_*.cpp）
python tools/scan_signal_graph.py --repo /path/to/qt-project --verify-moc /path/to/build

# 或独立跑
python tools/verify_moc.py --repo /path/to/qt-project --build /path/to/build
```

- 无 moc 文件 → 跳过，不阻塞
- 差集非空 → 打印 `moc_only_*` / `scan_only_*` 并退出码 1
- 比对键为 **方法名**（跨 Qt 版本签名格式不稳）；重载同名视为一条
- 不替代 `slots:` 声明扫描：invokeMethod / 直接调用的槽不会出现在 connect 里，仍以声明为准

## 目录

```
AGENTS.md                     Agent/助手接入说明（clone 后优先读）
tools/
  wiki_config.py                共享配置加载（CLI / qt_code_wiki.json / modules.json）
  scan_signal_graph.py          确定性扫描器（类/信号/槽/connect/自由函数/ui 控件树）
  render_wiki.py                vault 渲染器（模块页/类页/交互 HTML/lint/DERIVED hash）
  verify_moc.py                 可选：moc 产物对账 oracle（--verify-moc）
  gen_connections_manifest.py   connect 提取纯函数库（scan 依赖其分段/配平/拆参）
  gen_ui_manifest.py            .pro FORMS + .ui 解析纯函数库（scan 依赖）
  schemas/signal_graph.schema.json   图谱 JSON v1.0 契约（含 compile_meta）
rules/
  llm_wiki.md                   治理规则模板（页面 Schema/三操作/派工护栏/增量维护）
examples/demo-qt-app/           端到端 demo（4 个 Q_OBJECT 类 + .ui + PMF/lambda/跨线程 connect）
```

## 适用边界

- ✅ C++ / Qt Widgets 工程（.pro 工程、PMF 语法 connect、uic 控件树）
- ✅ 源码可分布在子目录（递归扫描，配置排除 thirdparty/build 等）
- ❌ 非 Qt 项目、QML 主导项目（未覆盖）
- 注释语义列的价值依赖目标仓库有 .h 功能注释；没有的话先跑 missing-comments 清单驱动补齐

## 已知限制（与源仓行为一致，欢迎提 issue）

- 多行函数声明 / 类内 inline 定义可能漏收
- `#ifdef` 块内 connect 仍会解析进图谱
- 链式表达式 sender（`m_x->sub()`）不解析成边（渲染层约定：出边看信号表）
- `ui_*` / `moc_*` / `qrc_*` 生成文件已排除（否则 `Ui::X` 会覆盖真类）

## License

MIT
