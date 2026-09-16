---
trigger: always_on
---

# code_wiki 维护规则（llm_wiki）

> **唯一 owner 声明**：代码 Wiki（默认 `code_wiki/`）的页面 Schema、
> 三操作（compile / update / lint）与粒度决策只在本文件定义。
> 权威依据：Karpathy LLM Wiki（三层架构与 index/log 纪律）+ RepoDoc（diff 影响传播 + 双校验）。

## 1. 定位与治理边界

- code_wiki 是**代码解释层**：类/接线/控件/函数的"文本化编译"产物，描述"代码是什么"。
- 与规格/设计文档是**两个层面，零互链**：
  - 类页**不得**出现指向规格文档的可点击链接（`[[...]]` / `[..](../..)`）；
  - 涉及相关知识只允许纯文本提及文件名，frontmatter `upstream_docs` 记纯路径（agent 检索用元数据）；
  - 类页**不得复述**领域知识——知识只在权威源。
- vault 根 = 输出目录（默认 `<repo>/code_wiki`，Obsidian "Open folder as vault" 指向此目录）。

## 2. 数据流与产物

```
代码(只读) ──① tools/scan_signal_graph.py──▶ signal_graph.json（图谱 SSOT，确定性幂等）
              └ 可选断言：--assert-no-on（on_ 槽/字符串引用 = 0）
signal_graph.json ──② tools/render_wiki.py──▶ signals/*.md（整页 derived 禁手改）
                                        ├──▶ components/<Class>.md 的 derived 区块（增量替换）
                                        ├──▶ signals/missing-comments.md（缺注释清单）
                                        └──▶ index.md 重建
类页 narrative 区（职责/分组导读/生命周期）──③ session 填写与更新，人审（抽检制）
```

- 图谱 schema：`tools/schemas/signal_graph.schema.json`（v1.0，含 compile_meta）。
- 未解析项：`scan_unresolved.txt` 人工补录，属预期机制非缺陷。

## 3. 页面 Schema v1.1

### 3.1 frontmatter（全页面必填，lint 校验）

| 字段 | 取值 | 必填 | 语义 |
|------|------|------|------|
| type | index / log / signals / component / concept / analysis | ✓ | 页面类型 |
| authority | derived / narrative | ✓ | derived=脚本生成禁手改；narrative=agent 写+人审 |
| source | 源文件路径数组 | component/signals | 页面对应 .h/.cpp/.ui |
| upstream_docs | 纯路径数组 | 可选 | 关联规格文档元数据（零互链） |
| updated / updated_by | YYYY-MM-DD / script·session·human | ✓ | 漂移检测 |
| verified | pending / true | narrative 必填 | 抽检制载体；derived 页不适用 |
| derived_hash | sha256 前 8 位 | 含 derived 区块的页 | 防手改校验 |
| class / module | 类名 / 模块名 | component | 检索用 |

### 3.2 类页内容结构（九区块，按读者提问链；narrative + derived 混排）

1. `## 职责`（narrative：一句话定义 + 3~5 句展开——是什么/边界/协作对象）
2. `## 内部结构 · 分组导读`（narrative：函数按功能分组说明；**明细表是 derived**）
3. `## 生命周期`（narrative：构造入口/所属线程/析构清理/跨线程边界一句话）
4. `## 数据区（derived，勿手改）`内依次：文件构成 → 接线图(mermaid) → 信号表 → 槽表 → 内部结构·函数清单 → 关键成员 →（UI 类追加）控件→功能表
   - 所有表带"语义"列（来自代码注释，缺失标 🔴，汇总于 signals/missing-comments.md）
   - UI 类判定：存在同名 .ui 文件

### 3.3 其余页面类型

- `index.md`（derived，渲染器重建）
- `log.md`（derived 追加）：统一前缀 `## [YYYY-MM-DD] 操作 | 标题`（compile/update/lint），可 grep，只追加不改历史
- `signals/<模块>.md`（derived 整页禁手改）：模块接线 mermaid + connect 明细
- `signals/missing-comments.md`（derived）：缺注释清单
- `concepts/<名称>.md`（narrative）：跨类代码概念（线程边界、信号命名约定等，纯代码层）
- `analyses/<名称>.md`（narrative）：查询/排查好答案回填

### 3.4 derived 区块防手改标记

```markdown
<!-- DERIVED:start hash=<sha256 前 8 位> -->
（渲染器内容）
<!-- DERIVED:end -->
```

lint 重算内容 hash 比对 frontmatter `derived_hash` 与标记内 hash，不一致即报"被手改"。
**修复手改的唯一正确路径**：改代码或改渲染器后重跑，不手改区块。

### 3.5 粒度决策

- 图谱层：函数级全量节点；**变量不做节点**（成员变量是边的 provenance 属性）。
- 页面层：类级一页（Q_OBJECT）；函数/信号/槽为页内表格行 + 锚点，**不建独立页**。
- ui_widget 作节点（控件→功能映射）；模块按 `modules.json` 分组，未命中落"未分组"。

## 4. 三操作

### 4.1 compile（手动触发）

```text
python tools/scan_signal_graph.py --repo /path/to/qt-project
python tools/render_wiki.py --repo /path/to/qt-project
```

- 时机：代码有变更后、需要最新接线视图时；显式动作，不自动化。
- 幂等：同代码同 HEAD 两次运行 JSON 与页面 derived 区块逐字节一致。
- **编译锚点**：每次编译把当时 HEAD 写入 `signal_graph.json` 的
  `compile_meta.compiled_at_commit`（git 不可用时为空串不阻塞）；它是 update/L1
  增量圈范围的机械依据，属幂等输入状态的一部分。
- `--assert-no-on`：启用后 on_ 非零即退出非零（项目若合法使用 connectSlotsByName 则保持默认关闭）。

### 4.2 update（compile 后按需）

- compile 后用锚点机械圈受影响类：`git diff --name-only <compile_meta.compiled_at_commit> -- '*.h' '*.cpp'`
  （锚点→工作树，天然覆盖已提交与未提交变更）→ 变更文件映射到类 → 图上直接邻居一层；
- session 修改对应类页 narrative 区（职责/分组/生命周期）；**derived 区块禁止手改**；
- 完成后在 `log.md` 追加 `## [日期] update | <类名/主题>`。

### 4.3 lint

- **L0（compile 内置 + `render_wiki.py --lint` 独立可跑，零成本）**：
  frontmatter 必填、derived_hash 一致性（防手改）、
  类页与图谱对账（缺页/多余页）、渲染后自检。
- **L1（每周定时，可派低参数模型）**：
  叙事与代码矛盾抽查（对照 derived 表核 narrative 说法）、过期声明、
  被引用未建页的 [[链接]]、index 与实际页面一致性；报告追加 `log.md`。

## 5. 派工护栏

- 类页 narrative 填充与 L1 lint 可派低参数模型（输入=结构化 derived 表 + 本规则，
  输出可被 lint 机器校验）；扫描器/渲染器开发与调试用主力模型。
- session 写类页时：只动 narrative 区与 frontmatter 的 verified/updated 字段；
  碰 derived 区块即违规（lint 会抓）。
- 每批 2~3 类，抽检 1 页：抽检通过后批量 `verified: true`。

## 6. 纪律清单（违者 lint 或 review 拦截）

1. derived 区块/derived 页禁手改——改代码或改渲染器后重编译。
2. 不新增 .ui Designer `<connections>`（若项目启用 on_ 门禁则也不新增 on_ 槽）。
3. 类页不复述、不链接规格层知识。
4. log.md 只追加。
5. 不为函数/变量建独立页（粒度决策）。
6. signal_graph.json 不手编——它是扫描器产物。

## 7. 代码变更后的增量维护

> 回答"代码动了，wiki 怎么跟"。compile 是机械廉价操作可随时跑；narrative
> update 用 `prev_compiled_at_commit` 锚点机械圈范围，只动受影响页。

### 7.1 触发节奏与消耗分档

| 档 | 触发时机 | 动作 | 消耗 | 谁执行 |
|----|---------|------|------|--------|
| A | 开发中随手 | compile 一条龙（§4.1） | 机器 <1 分钟，**0 LLM token** | 任意模型/人工 |
| B | 功能完成/提交点 | compile + 锚点圈范围 + 受影响页 narrative update | 每页约 10~30 万 subagent tokens（含读源码 diff），2~3 页一批 | 低参数写（§5 护栏）+ 编排层抽检 |
| C | 大重构（圈出 >5 页或新类/新模块页） | 按 §5 分批派发 | 按页数线性放大 | 编排层统一调度 |

- 档位判定：`git diff --name-only <prev锚点>` 圈出类页 ≤5 且无新 Q_OBJECT 类 → B；否则 C。
- **开发中间态注意**：compile 编译 tracked 文件的工作树现状（未提交 M 状态也进 derived），
  这对档 A 无害；narrative 只在功能定型点更新（档 B/C），避免中间态白写。

### 7.2 档 B 机械操作序列（PM 可直接照做）

1. `python tools/scan_signal_graph.py --repo <repo> && python tools/render_wiki.py --repo <repo> --html all`
2. `python tools/render_wiki.py --repo <repo> --lint`——L0 零问题才继续
3. 圈范围：`git diff --name-only <prev_compiled_at_commit> -- '*.h' '*.cpp'`
   → 变更文件映射类页（同名 .h/.cpp → 类）→ 图上直接邻居一层
4. 逐页判断 narrative 是否真被触及：只改了函数内部实现、职责/分组/生命周期断言未变
   → 该页跳过
5. 需更新的页派子代理（每批 2~3 页）：prompt = 读页 + `git diff <prev锚点> -- <该类源文件>`
   + 只改三区块；批内 ≥2 页抽检 1 页
6. `log.md` 追加 `## [日期] update | <类名列表>`，按批 commit（显式列文件）

### 7.3 与工作流的挂接

- 变更触及信号槽/类结构即触发本节。
- 纯算法内部优化、UI 文案类不改接口的变更：档 A 即可，不强制 narrative update。
