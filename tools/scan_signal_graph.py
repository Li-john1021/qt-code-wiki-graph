#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scan_signal_graph.py — code_wiki 确定性编译器

从目标 Qt 工程全量提取信号槽接线与类结构，输出图谱 SSOT：
  <out-root>/signal_graph.json（确定性：sort_keys、无时间戳）
  <out-root>/scan_unresolved.txt（未解析项人工补录清单）

数据源：
  a. 显式 connect（复用 gen_connections_manifest 的区域状态机/括号配平/参数切分）
  b. on_ 暗连接断言（可选 --assert-no-on，默认关闭；源仓红线非 Qt 通识）
  c. .ui 控件树（复用 gen_ui_manifest 的 FORMS 解析与控件遍历）
  d. .ui Designer <connections>

用法：
  python tools/scan_signal_graph.py --repo /path/to/qt-project
  python tools/scan_signal_graph.py --check
  --check 仅执行断言与一致性检查（lint L0 的数据部分），不写产物

配置（可选）：目标仓库根 qt_code_wiki.json + modules.json
  CLI > 配置文件 > 内置默认。详见 tools/wiki_config.py

设计约束：
  - 仅 Python3 标准库 + 仓库内 gen_* 工具复用（只读 import，不改其本体）
  - 幂等：同代码两次运行 JSON 逐字节一致
  - 不修改任何生产代码
"""

import argparse
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gen_connections_manifest import (  # noqa: E402
    CONNECT_RE, segment_regions, match_paren, split_args, line_of, CODE,
)
from gen_ui_manifest import parse_pro_forms, parse_ui_file  # noqa: E402
from wiki_config import (  # noqa: E402
    ProjectConfig, add_common_args, config_from_args,
    default_repo_root, GENERATED_FILE_PREFIXES,
    is_windows_reserved, safe_relpath,
)
from verify_moc import verify_against_graph, print_report  # noqa: E402

# 由 apply_config() 在 main / 外部调用时填充
CFG = None
REPO = default_repo_root()
OUT_JSON = os.path.join(REPO, 'code_wiki', 'signal_graph.json')
OUT_UNRESOLVED = os.path.join(REPO, 'code_wiki', 'scan_unresolved.txt')
EXCLUDE_DIRS = set()
FILE_CLASS_OVERRIDES = {}
MODULE_HINT = {}          # module → [class, ...]（由 modules.json 填充）
MODULE_OF = {}            # class → module


def apply_config(cfg):
    """把 ProjectConfig 绑到本模块全局（保持既有函数体只读 REPO/EXCLUDE_DIRS 的写法）。"""
    global CFG, REPO, OUT_JSON, OUT_UNRESOLVED, EXCLUDE_DIRS
    global FILE_CLASS_OVERRIDES, MODULE_HINT, MODULE_OF
    CFG = cfg
    REPO = cfg.repo
    OUT_JSON = cfg.graph_path
    OUT_UNRESOLVED = cfg.unresolved_path
    EXCLUDE_DIRS = set(cfg.exclude_dirs)
    # vault 输出目录本身不进扫描
    try:
        out_name = os.path.basename(cfg.out_root)
        if out_name:
            EXCLUDE_DIRS.add(out_name)
    except Exception:
        pass
    FILE_CLASS_OVERRIDES = dict(cfg.file_class_overrides)
    MODULE_HINT = cfg.module_hint
    MODULE_OF = cfg.module_of


def read_text(path):
    """编码探测读取：UTF-8 BOM → UTF-8 → GBK。"""
    raw = open(path, 'rb').read()
    if raw.startswith(b'\xef\xbb\xbf'):
        return raw.decode('utf-8-sig')
    for enc in ('utf-8', 'gbk'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='replace')


def git_tracked_sources():
    """已入库的 cpp/h/ui 集合。wiki 只编译已提交代码基线（review CP3 决策：
    并行 agent 在途文件不入图谱，提交后下次 compile 自然进入）。"""
    try:
        out = subprocess.run(['git', 'ls-files'], cwd=REPO, capture_output=True,
                             text=True, encoding='utf-8', errors='replace').stdout
        return {l.strip().replace('/', os.sep) for l in out.splitlines() if l.strip()}
    except Exception:
        return None    # git 不可用时退化为全量


def _is_excluded_dir(name, exclude_dirs):
    if name in exclude_dirs:
        return True
    for pat in exclude_dirs:
        if pat.endswith('*') and name.startswith(pat[:-1]):
            return True
    return False


def list_root_sources(include_untracked=False):
    """递归收集 cpp/h（排除 EXCLUDE_DIRS、Windows 保留名、uic/moc 生成文件）。"""
    cpps, heads = [], []
    tracked = None if include_untracked else git_tracked_sources()
    skipped_bad = 0
    for root, dirs, files in os.walk(REPO):
        # 剪掉排除目录 + Windows 设备保留名（nul/con/aux…，REVIEW P0）
        kept_dirs = []
        for d in sorted(dirs):
            if is_windows_reserved(d) or _is_excluded_dir(d, EXCLUDE_DIRS) or d.startswith('.'):
                skipped_bad += 1
                continue
            kept_dirs.append(d)
        dirs[:] = kept_dirs
        for name in sorted(files):
            if name.startswith(GENERATED_FILE_PREFIXES):
                continue    # uic/moc 生成文件，其中 Ui::X 类会覆盖真类
            if is_windows_reserved(name):
                skipped_bad += 1
                continue
            p = os.path.join(root, name)
            rel = safe_relpath(p, REPO)
            if rel is None:
                skipped_bad += 1
                continue
            if tracked is not None and rel not in tracked:
                continue    # 未跟踪的在途文件不入图谱
            if name.endswith('.cpp'):
                cpps.append(p)
            elif name.endswith('.h') or name.endswith('.hpp'):
                heads.append(p)
    if skipped_bad:
        print(f'[scan] skipped unsafe/excluded path entries: {skipped_bad}')
    return cpps, heads


# ---------------------------------------------------------------- .h 类解析

ACCESS_RE = re.compile(r'^\s*(public|protected|private)\s+(slots|signals)\s*:\s*(.*)$')
PLAIN_ACCESS_RE = re.compile(r'^\s*(public|protected|private|signals)\s*:\s*$')
CLASS_RE = re.compile(r'^\s*class\s+([A-Za-z_]\w*)\s*(?::\s*(.+))?\s*\{?\s*$')
DECL_FUNC_RE = re.compile(
    r'^\s*(?:virtual\s+|static\s+|explicit\s+|inline\s+)*'
    r'([A-Za-z_][\w:<>,\s\*&]*)\s+~?([A-Za-z_]\w*)\s*\(([^;{)]*)\)\s*'
    r'(?:const\s*)?(?:override\s*)?(?:=\s*0\s*)?;\s*(?://.*)?$')
DECL_MEMBER_RE = re.compile(
    r'^\s*([A-Za-z_][\w:]*(?:<[^>]*>)?(?:\s*\*)*)\s+\*?([A-Za-z_]\w*)\s*(?:=\s*[^;]+)?;\s*(?://.*)?$')
QOBJECT_RE = re.compile(r'Q_OBJECT')


def parse_header(text, relpath):
    """解析一个头文件内的类结构（花括号深度跟踪，支持类内嵌套 struct/enum）。
    返回 [{name, line, signals, slots, functions, members, qobject}]"""
    lines = text.split('\n')
    classes = []
    cur = None
    region = None          # None | 'signals' | 'slots' | 'plain:<access>'
    depth = 0              # 类体相对深度（0=类直属层）
    class_open = False

    for i, raw in enumerate(lines):
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith('//') or stripped.startswith('/*') \
                or stripped.startswith('*') or stripped.startswith('#'):
            continue

        if cur is None:
            m = CLASS_RE.match(line)
            if m:
                cur = {'name': m.group(1), 'bases': (m.group(2) or '').strip(),
                       'line': i + 1, 'header': relpath, 'qobject': False,
                       'signals': [], 'slots': [], 'functions': [], 'members': []}
                region, depth, class_open = None, 0, False
            continue

        if not class_open:
            if '{' in stripped:
                class_open = True
                depth = 0          # 类体起始括号本身不算嵌套层
            continue
        if QOBJECT_RE.search(stripped):
            cur['qobject'] = True
            continue

        delta = stripped.count('{') - stripped.count('}')
        at_class_level = (depth == 0)

        # 类结束判定：配平闭合（}; 或 } 后跟宏/空）
        if at_class_level and stripped.startswith('}') and delta <= 0:
            classes.append(cur)
            cur, region, class_open = None, None, False
            continue

        if at_class_level:
            m = ACCESS_RE.match(line)
            if m:
                region = m.group(2)          # signals / slots
                trail = m.group(3).strip()
                if trail and not trail.startswith('//'):
                    item = _parse_decl(trail, i, region)
                    if item:
                        _attach(cur, region, item)
                depth += delta
                continue
            m = PLAIN_ACCESS_RE.match(line)
            if m:
                region = 'signals' if m.group(1) == 'signals' else 'plain:' + m.group(1)
                continue
            if stripped.startswith(('Q_PROPERTY', 'Q_ENUM', 'Q_FLAG', 'Q_DECLARE',
                                    'friend ', 'struct ', 'enum ', 'using ', 'typedef ',
                                    'operator', 'public ', 'protected ', 'private ')):
                depth += delta
                continue
            if '~' + cur['name'] in stripped and '(' in stripped:
                depth += delta          # 析构声明/定义
                continue
            # 多行声明累积：括号未配平则吸收后续行（review CP3 修复）
            seg = stripped
            seg_delta = delta
            j = i
            while '(' in seg and ')' not in seg and j + 1 < len(lines) and (j - i) < 8:
                j += 1
                nxt = lines[j].strip()
                if not nxt or nxt.startswith(('//', '/*', '*', '#', 'public', 'private', 'protected', 'signals')):
                    break
                seg += ' ' + nxt
                seg_delta += nxt.count('{') - nxt.count('}')
            item = _parse_decl(seg, i, region)
            if item:
                _attach(cur, region, item)
            delta = seg_delta
            i_is = j    # noqa: F841（主循环 i 由 for 控制，吸收行在下次迭代被跳过判定：无副作用，因它们已并入 seg）
        depth += delta
    if cur is not None and class_open:
        classes.append(cur)
    elif cur is not None:
        classes.append(cur)
    return classes


def _parse_decl(stripped, line_no, region):
    fm = DECL_FUNC_RE.match(stripped)
    if fm:
        return {'name': fm.group(2), 'ret': re.sub(r'\s+', ' ', fm.group(1)).strip(),
                'params': re.sub(r'\s+', ' ', fm.group(3)).strip(),
                'line': line_no + 1}
    mm = DECL_MEMBER_RE.match(stripped)
    if mm and region and region.startswith('plain:'):
        return {'name': mm.group(2), 'type': re.sub(r'\s+', ' ', mm.group(1)).strip(),
                'line': line_no + 1}
    return None


def _attach(cur, region, item):
    item.setdefault('comment', None)
    if region == 'signals':
        cur['signals'].append(item)
    elif region == 'slots':
        cur['slots'].append(item)
    elif region and region.startswith('plain:'):
        access = region.split(':')[1]
        if 'params' in item:
            item['access'] = access
            cur['functions'].append(item)
        else:
            item['access'] = access
            cur['members'].append(item)


# 注释归属第二遍：对 signals/slots/functions 用上方注释回填（parse_header 的
# pending_comment 已在 flush 前丢失，这里按行号回查，简化且确定）
def attach_comments(text, classes):
    lines = text.split('\n')
    for c in classes:
        for bucket in ('signals', 'slots', 'functions', 'members'):
            for item in c[bucket]:
                if item.get('comment'):
                    continue
                idx = item['line'] - 1
                cmt = _comment_above(lines, idx)
                if cmt:
                    item['comment'] = cmt['full']
                    item['brief'] = cmt['brief']


def _comment_above(lines, idx, max_up=6):
    """声明/connect 上方紧邻注释。返回 None 或 {'brief': 首行, 'full': 整段}——
    brief 是唯一进图表表格的行（22 规范 §1）。"""
    collected = []
    j = idx - 1
    while j >= 0 and (idx - j) <= max_up:
        s = lines[j].strip()
        if not s:
            break
        if s.startswith('//'):
            collected.insert(0, s[2:].strip())
            j -= 1
        elif s.endswith('*/'):
            # 块注释：从尾行向上收集到 /* 起始行（单行 /** xx */ 与多行均覆盖）
            k = j
            while k >= 0 and (idx - k) <= max_up + 8:
                t = lines[k].strip()
                body = t.rstrip('*').rstrip('/')
                body = re.sub(r'^/\*\*?|\*+/$', '', t).strip()
                body = body.replace('/*', '').replace('*/', '').strip('* ').strip()
                if body:
                    collected.insert(0, body)
                if t.startswith('/*'):
                    break
                k -= 1
            break
        else:
            break
    if not collected:
        return None
    def clean(x):
        x = re.sub(r'^[/*]+|[/*]+$', '', x).strip()
        return x
    brief = clean(collected[0]) if len(collected) == 1 else clean(collected[0])
    full = clean(' '.join(collected))
    return {'brief': brief or None, 'full': full or None}


# ---------------------------------------------------------------- connect 解析

PMF_RE = re.compile(r'&([A-Za-z_]\w*)::([A-Za-z_~]\w*)')
OVERLOAD_RE = re.compile(r'QOverload<[^>]*>::of\(\s*&([A-Za-z_]\w*)::([A-Za-z_~]\w*)')
SIGNAL_MACRO_RE = re.compile(r'(?:SIGNAL|SLOT)\(\s*"([^"]+)"')
UI_PTR_RE = re.compile(r'^ui\s*->\s*([A-Za-z_]\w*)$')


def is_lambda(arg):
    a = arg.strip()
    return a.startswith('[') and '](' in a


def main_class_for(filename, known_classes=None):
    """文件名 → 主类名。优先 overrides / 精确命中，其次对已知类大小写不敏感匹配
    （mainwindow.cpp → MainWindow），再次 snake_case→PascalCase。"""
    stem = os.path.splitext(os.path.basename(filename))[0]
    if stem in FILE_CLASS_OVERRIDES:
        return FILE_CLASS_OVERRIDES[stem]
    parts = stem.split('_')
    pascal = ''.join(p[:1].upper() + p[1:] for p in parts if p)
    if not known_classes:
        return pascal
    if stem in known_classes:
        return stem
    if pascal in known_classes:
        return pascal
    lower_map = {k.lower(): k for k in known_classes}
    if stem.lower() in lower_map:
        return lower_map[stem.lower()]
    if pascal.lower() in lower_map:
        return lower_map[pascal.lower()]
    return pascal


def extract_connects(text, relpath):
    regions = segment_regions(text)
    lines = text.split('\n')
    out = []
    for m in CONNECT_RE.finditer(text):
        open_idx = m.end() - 1
        if regions[open_idx] != CODE:
            continue
        close_idx = match_paren(text, regions, open_idx)
        if close_idx < 0:
            continue
        inner = text[open_idx + 1:close_idx]
        args = split_args(inner)
        line_no = line_of(text, m.start())
        cmt = _comment_above(lines, line_no - 1)
        out.append({
            'file': relpath, 'line': line_no,
            'args': [re.sub(r'\s+', ' ', a).strip() for a in args],
            'brief': cmt['brief'] if cmt else None,
            'comment': cmt['full'] if cmt else None,
        })
    return out


def resolve_expr(expr, owner_class, class_members, ui_widget_names, file_locals=None, depth=0):
    """sender/receiver 表达式 → (kind, name)。kind: class/ui_widget/this/unknown"""
    e = expr.strip()
    if not e:
        return ('unknown', '')
    if e == 'this':
        return ('class', owner_class)
    m = UI_PTR_RE.match(e)
    if m and owner_class in ui_widget_names:
        if m.group(1) in ui_widget_names[owner_class]:
            return ('ui_widget', m.group(1))
    if e.startswith('&') or '::' in e:
        pmf = PMF_RE.search(e)
        if pmf:
            return ('class', pmf.group(1))
    # 单例/静态工厂：Xxx::getInstance() → Xxx
    m = re.match(r'^([A-Za-z_]\w*)::\w+\(\)$', e)
    if m:
        return ('class', m.group(1))
    # 链式调用：obj->method() / obj.method() → 解析 obj（迭代调用本身作为 provenance 保留）
    if depth < 2 and e.endswith(')') and ('->' in e or '.' in e):
        base = re.split(r'->|\.', e)[0].strip()
        return resolve_expr(base, owner_class, class_members, ui_widget_names, file_locals, depth + 1)
    # 数组下标：arr[i] → 查 arr 的元素类型（局部数组）
    m = re.match(r'^([A-Za-z_]\w*)\s*\[\s*\w+\s*\]$', e)
    if m and file_locals and m.group(1) in file_locals:
        return ('class', file_locals[m.group(1)])
    if e in class_members.get(owner_class, {}):
        return ('class', class_members[owner_class][e])
    if file_locals and e in file_locals:
        return ('class', file_locals[e])
    if e.startswith(('qApp', 'QApplication::', '&QApplication')):
        return ('class', 'QApplication')
    return ('unknown', e)


def resolve_pmf_target(expr):
    """&Class::member → (class, member)；QOverload 包装同理。"""
    e = expr.strip()
    m = OVERLOAD_RE.search(e) or PMF_RE.search(e)
    if m:
        return m.group(1), m.group(2)
    return None, None


def collect_free_functions(heads):
    """类外自由/static 函数声明（项目自有；配置的 exclude 前缀与生成文件不收）。"""
    out = []
    prefixes = tuple(CFG.free_function_exclude_prefixes) if CFG else ()
    pat = re.compile(r'^(static\s+)?([A-Za-z_][\w:<>*&\s]*?)\s\*?([A-Za-z_]\w*)\s*\(([^;]*)\)\s*;\s*$')
    for h in heads:
        bn = os.path.basename(h)
        if prefixes and bn.startswith(prefixes):
            continue
        rel = os.path.relpath(h, REPO).replace('\\', '/')
        text = read_text(h)
        lines = text.split('\n')
        in_class = 0
        for i, line in enumerate(lines):
            s = line.strip()
            if s == '};' and in_class > 0:
                in_class -= 1
                continue
            if not s or s.startswith(('#', '//', '/*', '*', 'namespace', 'using', 'typedef',
                                      'friend', 'Q_', 'private', 'public', 'protected',
                                      'signals', 'slots', 'template', 'operator')):
                continue
            if (s.startswith('class ') or s.startswith('struct ')) and s.endswith('{'):
                in_class += 1
                continue
            if in_class == 0:
                m = pat.match(line)
                if m and m.group(3) not in ('if', 'for', 'while', 'switch', 'return'):
                    cmt = _comment_above(lines, i)
                    out.append({'name': m.group(3),
                                'ret': m.group(2).strip(),
                                'params': re.sub(r'\s+', ' ', m.group(4)).strip(),
                                'file': rel, 'line': i + 1,
                                'comment': cmt['full'] if cmt else None,
                                'brief': cmt['brief'] if cmt else None})
    return out


def build(cpps, heads, check_only=False, include_untracked=False):
    tracked = None if include_untracked else git_tracked_sources()
    # ---- .h：类结构 + 成员表 + on_ 断言素材
    all_classes = {}
    class_members = {}      # class -> {varname: TypeName}
    on_slot_hits = []
    header_texts = {}
    for h in heads:
        rel = os.path.relpath(h, REPO).replace('\\', '/')
        text = read_text(h)
        header_texts[rel] = text
        hclasses = parse_header(text, rel)
        attach_comments(text, hclasses)      # 注释回填（review CP3 修复：此前漏调用）
        for c in hclasses:
            all_classes[c['name']] = c
            class_members[c['name']] = {
                m['name']: m['type'].replace('*', '').strip() for m in c['members']
            }
            for s in c['signals'] + c['slots']:
                if s['name'].startswith('on_'):
                    on_slot_hits.append(f"{rel}:{s['line']} {c['name']}::{s['name']}")

    free_functions = collect_free_functions(heads)

    # ---- .ui：控件树（数据源 c）+ Designer 连接（数据源 d）
    pro = (CFG.pro if CFG and CFG.pro else '') or ''
    forms = parse_pro_forms(pro) if pro and os.path.exists(pro) else []
    ui_widget_names = {}    # 设计类名（Ui::X 对应宿主类近似） -> {objectName: desc}
    ui_widgets = []
    designer_edges = []
    for form in forms:
        fname = form.replace('/', os.sep)
        if tracked is not None and fname not in tracked:
            continue    # 未跟踪 .ui（在途文件）不入图谱
        fp = Path(os.path.join(REPO, form)) if not os.path.isabs(form) else Path(form)
        if not fp.exists():
            continue
        r = parse_ui_file(fp)
        design_class = r.get('designClass') or ''
        names = {}
        for w in r.get('widgets', []):
            rec = {
                'objectName': w['objectName'], 'widgetClass': w['class'],
                'uiFile': r['file'],
            }
            names[w['objectName']] = rec
            ui_widgets.append(rec)
        for a in r.get('actions', []):        # QAction 并入（review CP3 修复：menu/action 亦可作 sender）
            rec = {'objectName': a['objectName'], 'widgetClass': 'QAction', 'uiFile': r['file']}
            names.setdefault(a['objectName'], rec)
            ui_widgets.append(rec)
        # Ui::<designClass> 的宿主按文件名推主类
        host = main_class_for(str(fp), set(all_classes))
        ui_widget_names[host] = names
        ui_widget_names.setdefault(design_class, names)
        for cn in r.get('connections', []):
            designer_edges.append({
                'uiFile': r['file'], 'sender': cn.get('sender'),
                'signal': cn.get('signal'), 'receiver': cn.get('receiver'),
                'slot': cn.get('slot'),
            })

    # ---- .cpp：connect 边 + on_ 字符串断言素材
    edges = []
    unresolved = []
    invoke_on_hits = []
    LOCAL_NEW_RE = re.compile(
        r'\b(?:auto\s*\*?|([A-Za-z_][\w:<>\s]*?)\s*\*)\s*([A-Za-z_]\w*)\s*=\s*new\s+([A-Za-z_]\w*)')
    LOCAL_STACK_RE = re.compile(
        r'\b(QMessageBox|QTimer|QTcpServer|QTcpSocket|QEventLoop|QMenu|QFileDialog)'
        r'\s+([A-Za-z_]\w*)\s*[;=(]')
    LOCAL_ARRAY_RE = re.compile(r'\b([A-Za-z_]\w*)\s*\*?\s+([A-Za-z_]\w*)\s*\[\s*\]\s*=')
    # range-for 循环变量：for (QPushButton* b : list) —— b 的类型可解析（review 后续：
    # WorkPlaneModule 9 点按钮 connect(b,...) 此前显示成裸名 b）
    LOCAL_FOR_RE = re.compile(
        r'\bfor\s*\(\s*(?:const\s+)?([A-Za-z_][\w:<>\s]*?)\s*\*?\s+([A-Za-z_]\w*)\s*:')
    for cpp in cpps:
        rel = os.path.relpath(cpp, REPO).replace('\\', '/')
        text = read_text(cpp)
        owner = main_class_for(cpp, set(all_classes))
        # 文件级局部变量表（new 表达式 + 常见 Qt 栈对象 + 局部数组；同文件重名以
        # 最后声明为准，connect 场景下同名异型极罕见，可接受）
        locals_map = {}
        for lm in LOCAL_NEW_RE.finditer(text):
            locals_map[lm.group(2)] = lm.group(3)
        for lm in LOCAL_STACK_RE.finditer(text):
            locals_map.setdefault(lm.group(2), lm.group(1))
        for lm in LOCAL_ARRAY_RE.finditer(text):
            locals_map.setdefault(lm.group(2), lm.group(1))
        for lm in LOCAL_FOR_RE.finditer(text):
            locals_map[lm.group(2)] = lm.group(1).strip()
        for c in extract_connects(text, rel):
            args = c['args']
            if not args:
                continue
            s_expr, sig_expr = args[0], args[1] if len(args) > 1 else ''
            r_expr, slot_expr = (args[2], args[3]) if len(args) > 3 else ('this', args[2] if len(args) > 2 else '')
            conn_type = None
            for a in args:
                # 仅当该参数基本就是独立的连接类型表达式时提取（review CP3 修复：
                # 此前从 lambda 体尾巴误取到 'QueuedConnection); }' 等污染值）
                if re.search(r'^[\w:&<>\s]*Qt::\w+Connection\s*$', a.strip()):
                    m = re.search(r'Qt::(\w+Connection)', a)
                    conn_type = m.group(1)
                    break
            s_kind, s_name = resolve_expr(s_expr, owner, class_members, ui_widget_names, locals_map)
            r_kind, r_name = resolve_expr(r_expr, owner, class_members, ui_widget_names, locals_map)
            sig_c, sig_m = resolve_pmf_target(sig_expr)
            slot_c, slot_m = resolve_pmf_target(slot_expr)
            if 'SIGNAL(' in sig_expr:
                sm = SIGNAL_MACRO_RE.search(sig_expr)
                sig_m = sm.group(1) if sm else sig_expr
            if 'SLOT(' in (slot_expr or ''):
                sm = SIGNAL_MACRO_RE.search(slot_expr)
                slot_m = sm.group(1) if sm else slot_expr
            syntax = ('MACRO' if 'SIGNAL(' in sig_expr else
                      'LAMBDA' if is_lambda(r_expr) or is_lambda(slot_expr) else
                      'FUNCTOR' if (len(args) > 3 and slot_expr
                                    and not slot_expr.startswith(('&', 'SLOT('))
                                    and not resolve_pmf_target(slot_expr)[0]
                                    and not is_lambda(slot_expr)) else
                      'PMF4' if len(args) > 3 else 'PMF3')
            if s_kind == 'unknown' or r_kind == 'unknown':
                unresolved.append(f"{rel}:{c['line']} sender={s_expr} recv={r_expr} :: {' '.join(args)[:200]}")
            edges.append({
                'file': rel, 'line': c['line'], 'syntax': syntax,
                'connType': conn_type,
                'brief': c.get('brief'), 'comment': c.get('comment'),
                'senderExpr': s_expr, 'senderKind': s_kind, 'sender': s_name,
                'signal': sig_m or (sig_c and f'{sig_c}::{sig_m}') or sig_expr[:80],
                'signalOwner': sig_c,
                'receiverExpr': r_expr, 'receiverKind': r_kind, 'receiver': r_name,
                'slot': slot_m, 'slotOwner': slot_c,
            })
        for m in re.finditer(r'"(on_[A-Za-z0-9_]+)"', text):
            invoke_on_hits.append(f'{rel}:{line_of(text, m.start())} {m.group(1)}')

    return {
        'classes': all_classes, 'edges': edges, 'designerEdges': designer_edges,
        'uiWidgets': ui_widgets, 'uiWidgetNames': ui_widget_names,
        'onSlotHits': on_slot_hits, 'invokeOnHits': invoke_on_hits,
        'unresolved': unresolved, 'headers': header_texts,
        'freeFunctions': free_functions,
    }


def module_of(cls_name):
    if CFG:
        return CFG.module_for(cls_name)
    return MODULE_OF.get(cls_name, '未分组')


def git_head():
    """编译锚点：当前 HEAD（update/L1 用 git diff 锚点..工作树 圈受影响类）。
    git 不可用时返回空串，不阻塞编译。"""
    try:
        out = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=REPO,
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() if out.returncode == 0 else ''
    except Exception:
        return ''


def main():
    ap = argparse.ArgumentParser(
        description='Qt 信号槽图谱确定性扫描器（输出 signal_graph.json）')
    add_common_args(ap)
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--include-untracked', action='store_true',
                    help='包含未入库的在途源文件（默认只编译已提交基线）')
    ap.add_argument('--unresolved', default=None,
                    help='未解析项输出路径（默认 <out-root>/scan_unresolved.txt）')
    ap.add_argument('--assert-no-on', action='store_true',
                    help='启用 on_ 自动连接槽/字符串引用 = 0 断言（源仓红线，非 Qt 通识，默认关）')
    ap.add_argument('--exclude-dir', action='append', default=[],
                    help='追加排除目录名（可多次）')
    ap.add_argument('--verify-moc', default=None, metavar='BUILD_DIR',
                    help='可选：用 BUILD_DIR 下 moc_*.cpp 对账信号/槽清单'
                         '（无 moc 则跳过；差集非空退出码 1）')
    ns = ap.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    apply_config(config_from_args(ns))
    if CFG.pro:
        print(f'[scan] repo={REPO} pro={os.path.relpath(CFG.pro, REPO) if CFG.pro.startswith(REPO) else CFG.pro}')
    else:
        print(f'[scan] repo={REPO} pro=(未发现 .pro，跳过 ui 解析)')

    cpps, heads = list_root_sources(include_untracked=ns.include_untracked)
    data = build(cpps, heads, include_untracked=ns.include_untracked)

    # ---- 断言（lint L0 数据部分）
    fail = []
    if CFG.assert_no_on:
        if data['onSlotHits']:
            fail.append('on_ 槽声明非零: ' + '; '.join(data['onSlotHits'][:5]))
        if data['invokeOnHits']:
            fail.append('on_ 字符串引用非零: ' + '; '.join(data['invokeOnHits'][:5]))
    else:
        if data['onSlotHits'] or data['invokeOnHits']:
            print(f'[scan] on_ 提示（未断言）: 槽声明={len(data["onSlotHits"])} '
                  f'字符串引用={len(data["invokeOnHits"])}（需要门禁时加 --assert-no-on）')

    total = len(data['edges'])
    ok_send = sum(1 for e in data['edges'] if e['senderKind'] != 'unknown')
    ok_recv = sum(1 for e in data['edges'] if e['receiverKind'] != 'unknown')

    print(f'[scan] cpp={len(cpps)} h={len(heads)} classes={len(data["classes"])} '
          f'connect_edges={total} ui_widgets={len(data["uiWidgets"])} '
          f'designer_edges={len(data["designerEdges"])}')
    if total:
        print(f'[scan] sender解析率={ok_send / total * 100:.1f}% '
              f'receiver解析率={ok_recv / total * 100:.1f}% 未解析项={len(data["unresolved"])}')
    else:
        print('[scan] sender解析率=n/a receiver解析率=n/a 未解析项=0')
    print(f'[scan] on_: 槽声明={len(data["onSlotHits"])} 字符串引用={len(data["invokeOnHits"])} '
          f'assert={"on" if CFG.assert_no_on else "off"}')
    if fail:
        print('[ASSERT-FAIL]')
        for f in fail:
            print('  ' + f)
        sys.exit(1)
    if CFG.assert_no_on:
        print('[assert] on_ 零断言通过')

    if ns.check:
        print('[check] 断言模式，不写产物')
        if ns.verify_moc:
            tmp = {
                'nodes': {'classes': [
                    {'name': c['name'], 'qobject': c['qobject']}
                    for c in data['classes'].values()
                ]},
                'class_details': {
                    name: {'signals': c['signals'], 'slots': c['slots']}
                    for name, c in data['classes'].items()
                },
            }
            result = verify_against_graph(tmp, ns.verify_moc)
            print_report(result)
            if result.get('issues'):
                sys.exit(1)
        return

    # ---- 图谱 JSON（确定性输出：同代码同 HEAD 两次运行逐字节一致；
    # compile_meta 是 update/L1 的增量锚点，属输入状态的一部分。
    # prev_compiled_at_commit 保留上一次编译锚点，供增量圈范围——先读旧 JSON 再覆盖）
    prev_anchor = ''
    try:
        with io.open(OUT_JSON, 'r', encoding='utf-8') as f:
            prev_anchor = json.load(f).get('compile_meta', {}).get('compiled_at_commit', '')
    except Exception:
        pass
    graph = {
        'schema_version': '1.0',
        'compile_meta': {
            'compiled_at_commit': git_head(),
            'prev_compiled_at_commit': prev_anchor,
        },
        'nodes': {
            'classes': [
                {
                    'name': c['name'], 'header': c['header'], 'line': c['line'],
                    'qobject': c['qobject'], 'bases': c['bases'],
                    'module': module_of(c['name']),
                    'signalCount': len(c['signals']), 'slotCount': len(c['slots']),
                }
                for c in sorted(data['classes'].values(), key=lambda x: x['name'])
            ],
            'ui_widgets': sorted(data['uiWidgets'], key=lambda w: (w['uiFile'], w['objectName'])),
        },
        'edges': {
            'signal_connection': sorted(data['edges'], key=lambda e: (e['file'], e['line'])),
            'designer_connect': sorted(data['designerEdges'], key=lambda e: (e['uiFile'], str(e.get('sender')))),
        },
        'free_functions': sorted(data['freeFunctions'], key=lambda f: (f['file'], f['line'])),
        'class_details': {
            name: {
                'signals': c['signals'], 'slots': c['slots'],
                'functions': c['functions'], 'members': c['members'],
            }
            for name, c in sorted(data['classes'].items())
        },
        'assertions': {
            'on_slot_declarations': len(data['onSlotHits']),
            'on_string_references': len(data['invokeOnHits']),
            'assert_no_on_enabled': bool(CFG.assert_no_on),
        },
        'scan_stats': {
            'cpp_files': len(cpps), 'header_files': len(heads),
            'connect_edges': total,
            'sender_resolved': ok_send, 'receiver_resolved': ok_recv,
            'unresolved': len(data['unresolved']),
        },
    }
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with io.open(OUT_JSON, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(graph, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write('\n')

    os.makedirs(os.path.dirname(OUT_UNRESOLVED), exist_ok=True)
    with io.open(OUT_UNRESOLVED, 'w', encoding='utf-8', newline='\n') as f:
        f.write('# scan_signal_graph 未解析项（人工补录清单，重跑即重新生成）\n')
        for u in data['unresolved']:
            f.write(u + '\n')
    try:
        rel_json = os.path.relpath(OUT_JSON, REPO)
        rel_unres = os.path.relpath(OUT_UNRESOLVED, REPO)
    except ValueError:
        rel_json, rel_unres = OUT_JSON, OUT_UNRESOLVED
    print(f'[out] {rel_json} ({os.path.getsize(OUT_JSON)} bytes)')
    print(f'[out] {rel_unres} ({len(data["unresolved"])} 项)')

    # ---- 可选：moc 对账 oracle（事实源仍是源码扫描；无 moc 跳过）
    if ns.verify_moc:
        result = verify_against_graph(graph, ns.verify_moc)
        print_report(result)
        if result.get('issues'):
            sys.exit(1)


if __name__ == '__main__':
    main()
