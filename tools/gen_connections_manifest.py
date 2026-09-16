#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_connections_manifest.py — 显式 connect(...) 清单生成器

用途: 解析指定源文件，提取全部显式 connect(...) 调用，
      输出 UTF-8 BOM CSV + stdout 统计。
scan_signal_graph 复用其纯函数：segment_regions / match_paren / split_args。

仅用 Python3 标准库。不修改任何生产代码。

用法:
  python tools/gen_connections_manifest.py --source path/to/file.cpp --out out.csv
"""

import argparse
import csv
import os
import re
import sys

CONNECT_RE = re.compile(r'\bconnect\s*\(')

# 扫描状态机产出每个字符的区域类型
CODE, LINE_COMMENT, BLOCK_COMMENT, STRING_LIT, CHAR_LIT = range(5)


def segment_regions(text):
    """返回与 text 等长的 region 列表（每个字符的区域类型）。"""
    regions = [CODE] * len(text)
    i, n = 0, len(text)
    state = CODE
    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ''
        if state == CODE:
            if c == '/' and nxt == '/':
                state = LINE_COMMENT
                regions[i] = LINE_COMMENT
                i += 1
                regions[i] = LINE_COMMENT
            elif c == '/' and nxt == '*':
                state = BLOCK_COMMENT
                regions[i] = BLOCK_COMMENT
                i += 1
                regions[i] = BLOCK_COMMENT
            elif c == '"':
                state = STRING_LIT
                regions[i] = STRING_LIT
            elif c == "'":
                state = CHAR_LIT
                regions[i] = CHAR_LIT
            else:
                regions[i] = CODE
        elif state == LINE_COMMENT:
            regions[i] = LINE_COMMENT
            if c == '\n':
                state = CODE
        elif state == BLOCK_COMMENT:
            regions[i] = BLOCK_COMMENT
            if c == '*' and nxt == '/':
                regions[i + 1] = BLOCK_COMMENT
                i += 1
                state = CODE
        elif state == STRING_LIT:
            regions[i] = STRING_LIT
            if c == '\\':
                i += 1
                if i < n:
                    regions[i] = STRING_LIT
            elif c == '"':
                state = CODE
        elif state == CHAR_LIT:
            regions[i] = CHAR_LIT
            if c == '\\':
                i += 1
                if i < n:
                    regions[i] = CHAR_LIT
            elif c == "'":
                state = CODE
        i += 1
    return regions


def line_of(text, idx):
    return text.count('\n', 0, idx) + 1


def match_paren(text, regions, open_idx):
    """从 open_idx 的 '(' 出发配平，返回闭括号下标；失败返回 -1。
    跳过字符串/字符字面量与注释内的括号。"""
    depth = 0
    i, n = open_idx, len(text)
    while i < n:
        r = regions[i]
        if r == CODE:
            c = text[i]
            if c == '(':
                depth += 1
            elif c == ')':
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    return -1


def split_args(argtext, regions=None, base=0):
    """按 () {} [] 深度 0 的逗号切分参数。regions 提供时跳过非 CODE 字符内容判断。"""
    args = []
    depth = 0
    cur = []
    for off, c in enumerate(argtext):
        if c in '([{':
            depth += 1
        elif c in ')]}':
            depth -= 1
        if c == ',' and depth == 0:
            args.append(''.join(cur).strip())
            cur = []
        else:
            cur.append(c)
    tail = ''.join(cur).strip()
    if tail:
        args.append(tail)
    return args


def collapse(s, limit=160):
    s = re.sub(r'\s+', ' ', s).strip()
    return s if len(s) <= limit else s[:limit] + '...'


def classify_conn_type(args):
    for a in args:
        if 'Qt::BlockingQueuedConnection' in a:
            return 'BlockingQueued'
        if 'Qt::QueuedConnection' in a:
            return 'Queued'
        if 'Qt::DirectConnection' in a:
            return 'Direct'
        if 'Qt::AutoConnection' in a:
            return 'Auto'
        if 'Qt::UniqueConnection' in a:
            return 'Unique'
    return 'default'


def parse_connect(text, regions, start, commented):
    """解析一个 connect 调用。start 指向 'connect' 标识符起点。"""
    line = line_of(text, start)
    open_idx = text.index('(', start)
    if not commented:
        close_idx = match_paren(text, regions, open_idx)
        if close_idx < 0:
            return None
        argtext = text[open_idx + 1:close_idx]
    else:
        # 注释内：取到注释段结尾（行注释到行尾），尝试在其中配平
        seg_end = open_idx
        r = regions[open_idx]
        while seg_end < len(text) and regions[seg_end] == r:
            seg_end += 1
        seg = text[open_idx:seg_end]
        depth = 0
        close_off = -1
        for off, c in enumerate(seg):
            if c == '(':
                depth += 1
            elif c == ')':
                depth -= 1
                if depth == 0:
                    close_off = off
                    break
        if close_off < 0:
            return {
                'line': line, 'sender': collapse(seg[1:]), 'signal': '',
                'receiver': '', 'slot_summary': '[未配平-注释截断]',
                'conn_type': 'default', 'commented': 'yes', 'slot_kind': 'unparsed',
            }
        argtext = seg[1:close_off]
    # 注释代码里每行可能带 '//' 前缀（本仓库单行注释 connect 不受影响，保险处理）
    argtext = re.sub(r'//[^\n]*', '', argtext) if commented else argtext

    args = split_args(argtext)
    row = {
        'line': line, 'sender': '', 'signal': '', 'receiver': '',
        'slot_summary': '', 'conn_type': 'default',
        'commented': 'yes' if commented else 'no', 'slot_kind': '',
    }
    if not args:
        row['slot_summary'] = '[空参数]'
        row['slot_kind'] = 'unparsed'
        return row

    if args[1].strip().startswith('SIGNAL('):
        # 旧式字符串语法
        row['sender'] = collapse(args[0])
        row['signal'] = collapse(args[1])
        row['receiver'] = collapse(args[2]) if len(args) > 2 else ''
        row['slot_summary'] = collapse(args[3]) if len(args) > 3 else ''
        row['slot_kind'] = 'SIGNAL/SLOT'
        row['conn_type'] = classify_conn_type(args[4:])
        return row

    row['sender'] = collapse(args[0])
    row['signal'] = collapse(args[1]) if len(args) > 1 else ''
    if len(args) >= 4:
        row['receiver'] = collapse(args[2])
        slot = args[3]
        rest = args[4:]
    elif len(args) == 3:
        row['receiver'] = ''
        slot = args[2]
        rest = []
    else:
        slot = ''
        rest = []
    if slot.lstrip().startswith('['):
        row['slot_kind'] = 'lambda'
        row['slot_summary'] = collapse(slot)
    elif slot:
        row['slot_kind'] = 'member'
        row['slot_summary'] = collapse(slot)
    row['conn_type'] = classify_conn_type(rest)
    return row


def main():
    ap = argparse.ArgumentParser(description='提取显式 connect(...) 清单')
    ap.add_argument('--source', default=None,
                    help='要解析的 .cpp 文件路径（必填）')
    ap.add_argument('--out', default='connections_baseline.csv',
                    help='CSV 输出路径（默认当前目录 connections_baseline.csv）')
    opts = ap.parse_args()
    if not opts.source:
        ap.error('--source 是必填项（本工具独立运行时需指定源文件）')

    with open(opts.source, 'r', encoding='utf-8-sig') as f:
        text = f.read()
    regions = segment_regions(text)

    rows = []
    for m in CONNECT_RE.finditer(text):
        start = m.start()
        commented = regions[start] in (LINE_COMMENT, BLOCK_COMMENT)
        row = parse_connect(text, regions, start, commented)
        if row:
            rows.append(row)

    rows.sort(key=lambda r: r['line'])

    fieldnames = ['line', 'sender', 'signal', 'receiver',
                  'slot_summary', 'slot_kind', 'conn_type', 'commented']
    out_dir = os.path.dirname(opts.out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(opts.out, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    total = len(rows)
    active = [r for r in rows if r['commented'] == 'no']
    commented = total - len(active)
    by_type = {}
    for r in active:
        by_type[r['conn_type']] = by_type.get(r['conn_type'], 0) + 1
    lambdas = len([r for r in active if r['slot_kind'] == 'lambda'])
    senders = {}
    for r in active:
        senders[r['sender']] = senders.get(r['sender'], 0) + 1

    print('== gen_connections_manifest ==')
    print('source   : %s' % opts.source)
    print('out      : %s' % opts.out)
    print('total    : %d (active=%d, commented_out=%d)' % (total, len(active), commented))
    print('by_type(active): %s' % ', '.join('%s=%d' % kv for kv in sorted(by_type.items())))
    print('lambdas(active): %d' % lambdas)
    top = sorted(senders.items(), key=lambda kv: -kv[1])[:8]
    print('top_senders  : %s' % ', '.join('%s=%d' % kv for kv in top))


if __name__ == '__main__':
    main()