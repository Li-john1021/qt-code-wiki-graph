#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_ui_manifest.py — .pro FORMS + .ui 解析库

用途:
  - 独立运行：生成 UI manifest JSON / stdout 摘要
  - 被 scan_signal_graph 复用：parse_pro_forms / parse_ui_file

用法:
  python tools/gen_ui_manifest.py --pro path/to/App.pro [--out manifest.json] [--quiet]

设计约束:
  - 仅 Python3 标准库（xml.etree.ElementTree）
  - JSON 确定性: sort_keys=True、widget 顺序=XML 文档序、JSON 内不含时间戳
  - 只读 .ui/.pro，不修改任何文件
"""

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter, OrderedDict
from pathlib import Path

SCHEMA_VERSION = 1

# 控件关键属性白名单（任务合同列出项 + 基线对照必需的 geometry/title/placeholderText/checkable）
PROP_WHITELIST = {
    "text", "checked", "checkable", "currentIndex",
    "value", "minimum", "maximum", "decimals", "singleStep",
    "enabled", "visible", "readOnly", "editable",
    "toolTip", "whatsThis", "statusTip", "buddy",
    "shortcut", "placeholderText",
    "geometry", "sizePolicy", "minimumSize", "maximumSize",
    "title",  # QGroupBox/QMenu 标题属性
}

# ---------------------------------------------------------------- .pro 解析

def strip_pro_comment(line):
    """去掉 .pro 行内注释（# 之后）。FORMS 区无引号包裹的 #，简单切分即可。"""
    idx = line.find("#")
    return line[:idx] if idx >= 0 else line


def parse_pro_forms(pro_path):
    """解析 .pro 文件 FORMS 变量，返回 .ui 文件名列表（按文件内顺序）。"""
    text = Path(pro_path).read_text(encoding="utf-8")
    lines = [strip_pro_comment(l.rstrip("\n")) for l in text.splitlines()]
    forms = []
    i = 0
    n = len(lines)
    while i < n:
        m = re.match(r"^\s*FORMS\s*(\+=|=)\s*(.*)$", lines[i])
        if not m:
            i += 1
            continue
        rest = m.group(2).strip()
        buf = rest[:-1].strip() if rest.endswith("\\") else rest
        while rest.endswith("\\") and i + 1 < n:
            i += 1
            rest = lines[i].strip()
            buf += " " + (rest[:-1].strip() if rest.endswith("\\") else rest)
        forms.extend(tok for tok in buf.split() if tok)
        i += 1
    return forms


# ---------------------------------------------------------------- .ui 属性解析

def _int_or_0(txt):
    try:
        return int(txt)
    except (TypeError, ValueError):
        return 0


def typed_value(prop_elem):
    """把 <property> 的第一个子元素转成 Python 值；未知类型原样返回文本。"""
    child = None
    for c in prop_elem:
        child = c
        break
    if child is None:
        return None
    tag = child.tag
    if tag in ("string", "cstring", "enum", "set", "cursorShape"):
        return child.text or ""
    if tag == "bool":
        return (child.text or "").strip() == "true"
    if tag == "number":
        return _int_or_0((child.text or "").strip())
    if tag in ("double", "float"):
        try:
            return float((child.text or "").strip())
        except ValueError:
            return 0.0
    if tag in ("rect", "size", "point", "pointf", "sizef"):
        return {sub.tag: _int_or_0((sub.text or "").strip()) for sub in child}
    if tag == "sizepolicy":
        rec = {
            "hsizetype": child.get("hsizetype"),
            "vsizetype": child.get("vsizetype"),
            "horstretch": 0,
            "verstretch": 0,
        }
        for sub in child:
            if sub.tag == "horstretch":
                rec["horstretch"] = _int_or_0((sub.text or "").strip())
            elif sub.tag == "verstretch":
                rec["verstretch"] = _int_or_0((sub.text or "").strip())
        return rec
    if tag == "font":
        return None  # 字体不属于基线关键属性，跳过
    return (child.text or "").strip()


def parse_props(elem, whitelist):
    """提取 elem 直接子 <property> 中白名单内的属性。buddy 值为 cstring。"""
    props = {}
    for child in elem:
        if child.tag != "property":
            continue
        pname = child.get("name")
        if pname not in whitelist:
            continue
        val = typed_value(child)
        if val is None:
            continue
        props[pname] = val
    return props


# ---------------------------------------------------------------- .ui 结构解析

class FileState:
    def __init__(self):
        self.widgets = []          # 文档序 widget 记录
        self.layouts = []          # layout 记录
        self.by_name = {}          # objectName -> widget 记录（首次出现）
        self.duplicates = Counter()


def parse_layout(layout_elem, owner_name, widget_path, state, page_scope, tab_scope, order_out):
    """解析 <layout>；递归其 <item> 内的 widget/嵌套 layout。

    widget_path: layout 内控件的父链（= 拥有该 layout 的 widget 的路径）。
    order_out: 拥有者 widget 的 childOrder 列表，layout 内控件按 item 顺序追加。
    返回 (layout 记录, layout 子树内 widget 数)。
    """
    rec = OrderedDict()
    rec["objectName"] = layout_elem.get("name")
    rec["class"] = layout_elem.get("class")
    rec["owner"] = owner_name

    props = {}
    children_meta = []
    desc = 0

    def walk_item(item_elem):
        nonlocal desc
        for c in item_elem:
            if c.tag == "widget":
                cname = c.get("name") or ""
                children_meta.append({"type": "widget", "name": cname})
                order_out.append(cname)
                desc += 1 + parse_widget(c, widget_path, state, page_scope, tab_scope)
            elif c.tag == "spacer":
                sp = {"type": "spacer", "name": c.get("name")}
                for p in c:
                    if p.tag == "property" and p.get("name") == "orientation":
                        v = typed_value(p)
                        if v is not None:
                            sp["orientation"] = v
                children_meta.append(sp)
                order_out.append(sp["name"] or "<spacer>")
            elif c.tag == "layout":
                nested, nd = parse_layout(c, owner_name, widget_path, state,
                                          page_scope, tab_scope, order_out)
                children_meta.append({"type": "layout", "name": nested["objectName"]})
                state.layouts.append(nested)
                desc += nd

    for child in layout_elem:
        if child.tag == "property":
            pname = child.get("name")
            val = typed_value(child)
            if val is not None:
                props[pname] = val
        elif child.tag == "item":
            walk_item(child)

    if props:
        rec["props"] = props
    rec["children"] = children_meta
    return rec, desc


def parse_widget(elem, path, state, page_scope, tab_scope):
    """递归解析一个 <widget> 元素；返回该 widget 的后代 widget 数（不含自身）。

    path: 祖先 widget objectName 列表（不含自身）。layout 内嵌控件的 Qt 父对象
    仍是拥有 layout 的 widget，因此 layout 内控件沿用同一 path。
    """
    name = elem.get("name") or ""
    cls = elem.get("class") or ""
    state.duplicates[name] += 1

    rec = OrderedDict()
    rec["objectName"] = name
    rec["class"] = cls
    rec["path"] = "/".join(path)
    if page_scope:
        rec["page"] = page_scope
    if tab_scope:
        rec["tab"] = tab_scope

    state.widgets.append(rec)
    state.by_name.setdefault(name, rec)

    props = parse_props(elem, PROP_WHITELIST)
    desc = 0
    order = []

    next_page = name if name.startswith("page_") else page_scope
    next_tab = name if name.startswith("tab_") else tab_scope

    for child in elem:
        if child.tag == "attribute" and child.get("name") == "title":
            s = child.find("string")
            rec["tabTitle"] = (s.text if s is not None and s.text else "")
        elif child.tag == "widget":
            cname = child.get("name") or ""
            order.append(cname)
            desc += 1 + parse_widget(child, path + [name], state, next_page, next_tab)
        elif child.tag == "layout":
            lrec, nd = parse_layout(child, name, path + [name], state,
                                    next_page, next_tab, order)
            state.layouts.append(lrec)
            order.append("<layout:%s>" % lrec["objectName"])
            desc += nd

    if props:
        rec["props"] = props
    if order:
        rec["childOrder"] = order
    rec["descendantWidgets"] = desc
    return desc


def parse_actions(ui_root):
    """Designer 将 <action> 挂在根 widget 下；全树收集，保持文档序。"""
    actions = []
    for a in ui_root.iter("action"):
        rec = {"objectName": a.get("name")}
        props = parse_props(a, PROP_WHITELIST | {"iconText", "menuRole"})
        if props:
            rec["props"] = props
        actions.append(rec)
    return actions


def parse_menus(root_widget):
    """menubar 与各 QMenu 的 addaction 顺序。返回 {menuName: [addaction 名,...]}。"""
    menus = OrderedDict()

    def scan(elem):
        for child in elem:
            if child.tag == "widget":
                if child.get("class") in ("QMenuBar", "QMenu"):
                    order = []
                    for c in child:
                        if c.tag == "addaction":
                            order.append(c.get("name"))
                    menus[child.get("name")] = order
                scan(child)
            elif child.tag in ("layout", "item"):
                scan(child)

    scan(root_widget)
    return menus


def parse_connections(ui_root):
    """返回 (连接列表, 是否存在 <connections> 块)。"""
    conns = []
    has_block = False
    for c in ui_root:
        if c.tag != "connections":
            continue
        has_block = True
        for conn in c:
            if conn.tag != "connection":
                continue
            rec = {}
            for field in conn:
                if field.tag in ("sender", "signal", "receiver", "slot"):
                    rec[field.tag] = (field.text or "").strip()
            conns.append(rec)
    return conns, has_block


def parse_tabstops(ui_root):
    for t in ui_root:
        if t.tag == "tabstops":
            return [(ts.text or "").strip() for ts in t if ts.tag == "tabstop"]
    return None


def parse_customwidgets(ui_root):
    out = []
    for cw_block in ui_root:
        if cw_block.tag != "customwidgets":
            continue
        for cw in cw_block:
            if cw.tag != "customwidget":
                continue
            rec = {}
            for field in cw:
                if field.tag in ("class", "extends", "header"):
                    rec[field.tag] = (field.text or "").strip()
            out.append(rec)
    return out


def parse_ui_file(path):
    tree = ET.parse(str(path))
    ui_root = tree.getroot()
    state = FileState()

    root_widget = None
    design_class = None
    for child in ui_root:
        if child.tag == "class":
            design_class = (child.text or "").strip()
        elif child.tag == "widget" and root_widget is None:
            root_widget = child

    result = OrderedDict()
    result["file"] = path.name
    result["designClass"] = design_class
    if root_widget is None:
        result["error"] = "no root widget"
        return result

    state.root_name = root_widget.get("name")
    parse_widget(root_widget, [], state, "", "")

    result["root"] = state.root_name
    result["widgets"] = state.widgets
    result["layouts"] = state.layouts
    result["actions"] = parse_actions(ui_root)
    result["menus"] = parse_menus(root_widget)
    conns, has_conn_block = parse_connections(ui_root)
    result["connections"] = conns
    result["hasConnectionsBlock"] = has_conn_block
    tabstops = parse_tabstops(ui_root)
    result["tabstops"] = tabstops if tabstops is not None else []
    result["hasTabstopsBlock"] = tabstops is not None
    result["customwidgets"] = parse_customwidgets(ui_root)

    by_class = Counter(w["class"] for w in state.widgets)
    result["totals"] = {
        "widgets": len(state.widgets),
        "byClass": OrderedDict(sorted(by_class.items())),
        "layouts": len(state.layouts),
        "actions": len(result["actions"]),
        "connections": len(conns),
        "tabstops": len(result["tabstops"]),
    }

    # page_* / tab_* 后代统计
    container_stats = OrderedDict()
    for w in state.widgets:
        n = w["objectName"]
        if n.startswith("page_") or n.startswith("tab_"):
            container_stats[n] = w["descendantWidgets"]
    result["containerStats"] = container_stats

    # 异常检查（只记录事实，不做规范化处理；REQ-013）
    anomalies = []
    dups = sorted(n for n, c in state.duplicates.items() if c > 1)
    if dups:
        anomalies.append({"type": "duplicateObjectName", "names": dups})
    if tabstops is None:
        anomalies.append({
            "type": "missingTabstopsBlock",
            "note": "无 <tabstops> 块；Qt 运行期 tab 顺序 = Designer 创建顺序（XML 文档序）",
        })
    if not conns:
        anomalies.append({
            "type": "noDesignerConnections",
            "note": ("<connections> 块为空或不存在；连接全部来自代码"
                     "（connectSlotsByName + 显式 connect）"),
            "blockPresent": has_conn_block,
        })
    empty_menus = sorted(m for m, acts in result["menus"].items() if not acts and m != "menubar")
    if empty_menus:
        anomalies.append({"type": "emptyMenu", "menus": empty_menus})
    referenced = set()
    for acts in result["menus"].values():
        referenced.update(acts)
    unreferenced = sorted(
        a["objectName"] for a in result["actions"] if a["objectName"] not in referenced
    )
    if unreferenced:
        anomalies.append({
            "type": "actionNotInMenu",
            "names": unreferenced,
            "note": "这些 QAction 未出现在任何菜单 addaction 中；是否被代码使用需另行查证，此处只记录事实",
        })
    result["anomalies"] = anomalies
    return result


def build_manifest(pro_path):
    """Build a deterministic manifest for every FORMS entry in a project."""
    pro_path = Path(pro_path)
    forms = parse_pro_forms(pro_path)
    if not forms:
        raise ValueError("%s contains no FORMS entries" % pro_path)
    files = OrderedDict()
    for ui_name in forms:
        ui_path = pro_path.parent / ui_name
        if not ui_path.exists():
            raise OSError("FORMS entry does not exist: %s" % ui_path)
        files[ui_name] = parse_ui_file(ui_path)
    return OrderedDict([
        ("meta", OrderedDict([
            ("generator", "tools/gen_ui_manifest.py"),
            ("schemaVersion", SCHEMA_VERSION),
            ("proFile", pro_path.name),
            ("uiFiles", forms),
            ("countingRule", "containerStats counts descendant widgets, excluding the container"),
            ("determinism", "sort_keys=True; widget order is XML document order; no timestamp"),
        ])),
        ("files", files),
    ])


# ---------------------------------------------------------------- 主流程

def build_summary(files):
    out = []
    for fname, fdata in files.items():
        out.append("=" * 72)
        out.append("UI 文件: %s（根控件 %s，设计类 %s）" % (fname, fdata.get("root"), fdata.get("designClass")))
        if "error" in fdata:
            out.append("ERROR: %s" % fdata["error"])
            continue
        t = fdata["totals"]
        out.append("控件总数: %d | layout: %d | QAction: %d | Designer连接: %d | tabstops: %d"
                   % (t["widgets"], t["layouts"], t["actions"], t["connections"], t["tabstops"]))
        out.append("按 class 分布:")
        for cls, cnt in t["byClass"].items():
            out.append("  %-22s %d" % (cls, cnt))
        if fdata["containerStats"]:
            out.append("page_* / tab_* 后代控件数:")
            for name, cnt in fdata["containerStats"].items():
                mark = ""
                if name in ADR_EXPECTED_PAGES:
                    mark = "  [ADR-0013 期望 %d -> %s]" % (
                        ADR_EXPECTED_PAGES[name],
                        "PASS" if cnt == ADR_EXPECTED_PAGES[name] else "MISMATCH")
                elif name in PLAN_EXPECTED_TABS:
                    mark = "  [Plan §4.3 期望 %d -> %s]" % (
                        PLAN_EXPECTED_TABS[name],
                        "PASS" if cnt == PLAN_EXPECTED_TABS[name] else "MISMATCH")
                out.append("  %-32s %d%s" % (name, cnt, mark))
        if fdata["anomalies"]:
            out.append("异常/注意:")
            for a in fdata["anomalies"]:
                detail = {k: v for k, v in a.items() if k != "type"}
                out.append("  - %s: %s" % (a["type"], detail))
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="生成 UI manifest 基线（只读 .pro/.ui，不改任何文件）")
    ap.add_argument("--pro", default=None,
                    help=".pro 工程文件路径（必填）")
    ap.add_argument("--out", default=None, help="JSON 输出路径；缺省只打印摘要")
    ap.add_argument("--quiet", action="store_true", help="不打印 stdout 摘要")
    args = ap.parse_args(argv)

    if not args.pro:
        print("ERROR: --pro 是必填项", file=sys.stderr)
        return 1
    pro_path = Path(args.pro)
    if not pro_path.exists():
        print("ERROR: 找不到 .pro 文件: %s" % pro_path, file=sys.stderr)
        return 1

    forms = parse_pro_forms(pro_path)
    if not forms:
        print("ERROR: %s 中未解析到 FORMS 条目" % pro_path, file=sys.stderr)
        return 1

    base = pro_path.parent
    files = OrderedDict()
    for ui_name in forms:
        ui_path = base / ui_name
        if not ui_path.exists():
            print("ERROR: FORMS 列出的 .ui 不存在: %s" % ui_path, file=sys.stderr)
            return 1
        files[ui_name] = parse_ui_file(ui_path)

    manifest = OrderedDict()
    manifest["meta"] = OrderedDict([
        ("generator", "tools/gen_ui_manifest.py"),
        ("schemaVersion", SCHEMA_VERSION),
        ("proFile", pro_path.name),
        ("uiFiles", forms),
        ("countingRule",
         "containerStats = 该容器 <widget> 元素的全部后代 widget 数"
         "（含 <layout>/<item> 内嵌套），不含容器自身"),
        ("determinism", "sort_keys=True；widget 顺序=XML 文档序；JSON 不含时间戳"),
    ])
    manifest["files"] = files

    if args.out:
        import json
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=1, sort_keys=True)
            f.write("\n")
        print("manifest 已写入: %s" % out_path)

    if not args.quiet:
        print(build_summary(files))

    return 0


if __name__ == "__main__":
    sys.exit(main())
