#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_moc.py — 用 moc 产物对账扫描器的信号/槽清单（可选校验层）

定位（不是事实源）：
  - 事实源仍是 .h/.cpp 源码扫描（scan_signal_graph.py）
  - moc_*.cpp 是构建产物，只作 oracle：抓「声明漏收 / 多收」
  - 无 build 目录或无 moc 文件时跳过，不阻塞编译

解析策略（跨 Qt5/Qt6 尽量稳）：
  1. 从 QT_MOC_LITERAL 行尾 // "Name" 注释提字符串池
  2. 解析 qt_meta_data_<Class>[] 数组（header + method 表）
  3. method type: (flags>>2)&3 → 0 method / 1 signal / 2 slot / 3 ctor

用法：
  python tools/verify_moc.py --repo /path/to/qt-project --build build
  python tools/scan_signal_graph.py --repo ... --verify-moc build
"""

import argparse
import os
import re
import sys
from collections import defaultdict


def read_text(path):
    raw = open(path, 'rb').read()
    if raw.startswith(b'\xef\xbb\xbf'):
        return raw.decode('utf-8-sig')
    for enc in ('utf-8', 'gbk'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='replace')


# QT_MOC_LITERAL(idx, offset, size) // "Name"
LITERAL_RE = re.compile(
    r'QT_MOC_LITERAL\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)\s*'
    r'(?:,\s*)?//\s*"([^"]*)"')

# static const uint qt_meta_data_ClassName[] = { ... };
META_DATA_RE = re.compile(
    r'(?:static\s+)?(?:const\s+)?uint\s+qt_meta_data_(\w+)\s*\[\s*\]\s*=\s*\{',
    re.M)

UINT_TOKEN_RE = re.compile(r'0x[0-9a-fA-F]+|\d+')


def parse_string_pool(text):
    """index → 字符串（来自 QT_MOC_LITERAL 注释）。"""
    pool = {}
    for m in LITERAL_RE.finditer(text):
        pool[int(m.group(1))] = m.group(4)
    return pool


def extract_meta_data_arrays(text):
    """产出 (class_name, [uint, ...]) 列表。"""
    out = []
    for m in META_DATA_RE.finditer(text):
        cls = m.group(1)
        start = m.end()
        # 找到匹配的 } ; （数组字面量内无嵌套 {}，Qt6 偶发 QMetaType::Void 等标识符）
        end = text.find('};', start)
        if end < 0:
            continue
        body = text[start:end]
        # 只取独立数字 token；跳过注释
        body_nc = re.sub(r'//[^\n]*', '', body)
        body_nc = re.sub(r'/\*.*?\*/', '', body_nc, flags=re.S)
        nums = []
        for tok in UINT_TOKEN_RE.findall(body_nc):
            nums.append(int(tok, 0))
        if nums:
            out.append((cls, nums))
    return out


def parse_methods_from_meta(cls, nums, pool):
    """
    Qt5: header 14 uints；method 5 uints（name,argc,params,tag,flags）
    Qt6: header 可能 14+；部分版本 method 6 uints（多 revision）
    用 revision 启发式：rev>=10 可能 6 字段。
    """
    if len(nums) < 14:
        return {'signals': [], 'slots': [], 'methods': [], 'class': cls}

    revision = nums[0]
    methods_count = nums[4]
    methods_index = nums[5]
    signal_count = nums[13]

    entry_size = 6 if revision >= 10 else 5
    # 校验：若按 5 解析越界且 6 能整除则改 6
    need = methods_index + methods_count * entry_size
    if need > len(nums) and entry_size == 5:
        alt = methods_index + methods_count * 6
        if alt <= len(nums):
            entry_size = 6

    signals, slots, plain = [], [], []
    for i in range(methods_count):
        base = methods_index + i * entry_size
        if base + 4 >= len(nums):
            break
        name_idx = nums[base]
        argc = nums[base + 1]
        flags = nums[base + 4]
        name = pool.get(name_idx, f'__idx_{name_idx}')
        mtype = (flags >> 2) & 0x3
        rec = {'name': name, 'argc': argc, 'flags': flags}
        if mtype == 1:
            signals.append(rec)
        elif mtype == 2:
            slots.append(rec)
        elif mtype != 3:
            plain.append(rec)

    # signalCount 交叉校验（不强制一致，仅记录）
    return {
        'class': cls,
        'revision': revision,
        'signalCount_meta': signal_count,
        'signals': signals,
        'slots': slots,
        'methods': plain,
    }


def find_moc_files(build_dir):
    """递归找 moc_*.cpp（排除 moc_predefs）。"""
    found = []
    if not build_dir or not os.path.isdir(build_dir):
        return found
    for root, dirs, files in os.walk(build_dir):
        # 常见无用目录
        dirs[:] = [d for d in dirs if d not in {'.git', 'CMakeFiles', '.qmake.stash'}]
        for fn in files:
            if fn.startswith('moc_') and fn.endswith('.cpp') and fn != 'moc_predefs.h':
                found.append(os.path.join(root, fn))
    return sorted(found)


def load_moc_index(build_dir):
    """class → moc 解析结果。同名类多文件时合并（取并集信号/槽名）。"""
    by_class = {}
    files = find_moc_files(build_dir)
    for fp in files:
        text = read_text(fp)
        pool = parse_string_pool(text)
        arrays = extract_meta_data_arrays(text)
        if not arrays:
            # 文件名回退：moc_Foo.cpp
            bn = os.path.basename(fp)
            stem = bn[4:-4] if bn.startswith('moc_') and bn.endswith('.cpp') else ''
            if not stem:
                continue
            arrays = [(stem, [])]
        for cls, nums in arrays:
            if not nums:
                continue
            parsed = parse_methods_from_meta(cls, nums, pool)
            if cls not in by_class:
                by_class[cls] = parsed
                by_class[cls]['moc_files'] = [fp]
            else:
                by_class[cls]['moc_files'].append(fp)
                # 并集（按 name）
                have_s = {x['name'] for x in by_class[cls]['signals']}
                have_l = {x['name'] for x in by_class[cls]['slots']}
                for s in parsed['signals']:
                    if s['name'] not in have_s:
                        by_class[cls]['signals'].append(s)
                for s in parsed['slots']:
                    if s['name'] not in have_l:
                        by_class[cls]['slots'].append(s)
    return by_class, files


def _norm_names(decls):
    """扫描器 funcDecl 列表 → name 集合。"""
    return {d.get('name') for d in decls or [] if d.get('name')}


def compare_class(cls, scanned_signals, scanned_slots, moc_entry):
    """
    返回 issues 列表。键用方法名（跨源签名格式不一致时 name 更稳）。
    约定：扫描清单 ⊆ moc 清单 → 扫描漏收；moc ⊄ 扫描 → 扫描漏声明或多行漏收。
    """
    issues = []
    sig_scan = _norm_names(scanned_signals)
    slot_scan = _norm_names(scanned_slots)
    sig_moc = {x['name'] for x in moc_entry.get('signals', [])}
    slot_moc = {x['name'] for x in moc_entry.get('slots', [])}

    # 扫描有、moc 无（信号）：可能解析过度（把非 signal 收进 signal 区）
    for n in sorted(sig_scan - sig_moc):
        issues.append({
            'kind': 'scan_only_signal', 'class': cls, 'name': n,
            'detail': '扫描器有该 signal，moc 无（疑解析过度或 moc 过期）',
        })
    # moc 有、扫描无（信号）：多行声明/宏拼接漏收
    for n in sorted(sig_moc - sig_scan):
        issues.append({
            'kind': 'moc_only_signal', 'class': cls, 'name': n,
            'detail': 'moc 有该 signal，扫描器漏收（多行声明/宏？）',
        })
    for n in sorted(slot_scan - slot_moc):
        issues.append({
            'kind': 'scan_only_slot', 'class': cls, 'name': n,
            'detail': '扫描器有该 slot，moc 无（疑解析过度或 moc 过期）',
        })
    for n in sorted(slot_moc - slot_scan):
        issues.append({
            'kind': 'moc_only_slot', 'class': cls, 'name': n,
            'detail': 'moc 有该 slot，扫描器漏收（多行声明/宏？）',
        })
    return issues


def verify_against_graph(graph, build_dir, classes=None):
    """
    graph: signal_graph.json dict
    build_dir: 含 moc_*.cpp 的目录
    classes: 可选，只校验这些类
    返回 dict: {ok, moc_files, checked, issues, skipped}
    """
    by_class, files = load_moc_index(build_dir)
    if not files:
        return {
            'ok': True, 'skipped': True,
            'reason': f'no moc_*.cpp under {build_dir}',
            'moc_files': [], 'checked': 0, 'issues': [],
        }

    details = graph.get('class_details') or {}
    qobject = {
        c['name'] for c in (graph.get('nodes') or {}).get('classes', [])
        if c.get('qobject')
    }
    target = set(classes) if classes else set(details.keys()) & qobject

    issues = []
    checked = 0
    for cls in sorted(target):
        if cls not in by_class:
            # 有源码类但 build 里无对应 moc：可能未构建该 TU，跳过不报警
            continue
        checked += 1
        det = details.get(cls) or {}
        issues.extend(compare_class(
            cls, det.get('signals'), det.get('slots'), by_class[cls]))

    return {
        'ok': len(issues) == 0,
        'skipped': False,
        'moc_files': files,
        'moc_classes': sorted(by_class.keys()),
        'checked': checked,
        'issues': issues,
    }


def print_report(result):
    if result.get('skipped'):
        print(f'[verify-moc] skip: {result.get("reason")}')
        return
    print(f'[verify-moc] moc_files={len(result["moc_files"])} '
          f'classes_in_moc={len(result.get("moc_classes") or [])} '
          f'checked={result["checked"]} issues={len(result["issues"])}')
    by_kind = defaultdict(list)
    for it in result['issues']:
        by_kind[it['kind']].append(it)
    for kind in sorted(by_kind):
        items = by_kind[kind]
        print(f'  [{kind}] x{len(items)}')
        for it in items[:20]:
            print(f'    - {it["class"]}::{it["name"]}  {it["detail"]}')
        if len(items) > 20:
            print(f'    ... 另有 {len(items) - 20} 条')
    if result['ok']:
        print('[verify-moc] PASS（扫描清单与 moc 对账一致）')
    else:
        print('[verify-moc] FAIL（存在差集，见上；事实源仍以源码扫描为准）')


def main():
    ap = argparse.ArgumentParser(description='moc 产物对账扫描器信号/槽清单')
    ap.add_argument('--repo', default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument('--build', default=None, help='含 moc_*.cpp 的构建目录')
    ap.add_argument('--graph', default=None, help='signal_graph.json 路径（默认 <repo>/code_wiki/...）')
    ap.add_argument('--class', dest='only', default='', help='只校验指定类，逗号分隔')
    ns = ap.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    import json
    graph_path = ns.graph or os.path.join(ns.repo, 'code_wiki', 'signal_graph.json')
    if not os.path.exists(graph_path):
        print(f'[verify-moc] graph 不存在: {graph_path}（请先 scan）', file=sys.stderr)
        sys.exit(2)
    if not ns.build:
        print('[verify-moc] 需要 --build <dir>', file=sys.stderr)
        sys.exit(2)

    with open(graph_path, encoding='utf-8') as f:
        graph = json.load(f)
    only = [c.strip() for c in ns.only.split(',') if c.strip()] or None
    result = verify_against_graph(graph, ns.build, classes=only)
    print_report(result)
    sys.exit(0 if result['ok'] or result.get('skipped') else 1)


if __name__ == '__main__':
    main()
