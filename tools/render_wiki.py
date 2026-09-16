#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
render_wiki.py — signal_graph → code_wiki vault 渲染器

消费 <out-root>/signal_graph.json，产出/更新：
  signals/<模块>.md        模块接线图页（整页 derived，禁手改）
  signals/missing-comments.md  缺失注释清单（derived）
  components/<ClassName>.md    类页（derived 区块增量替换，narrative 区保留）
  index.md                    目录重建（头部简介可配置）

机制：
  - DERIVED 区块以 HTML 注释标记包裹，sha256 前 8 位存 frontmatter derived_hash；
    重渲染只替换标记之间的内容，页面其余（narrative）不动。
  - lint L0（--lint）：frontmatter 必填、derived_hash 一致、孤儿页、stale。

用法：
  python tools/render_wiki.py --repo /path/to/qt-project
  python tools/render_wiki.py --lint
  python tools/render_wiki.py --only MainWindow,Worker
  python tools/render_wiki.py --html all
"""

import argparse
import hashlib
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wiki_config import add_common_args, config_from_args, default_repo_root  # noqa: E402

CFG = None
REPO = default_repo_root()
VAULT = os.path.join(REPO, 'code_wiki')
GRAPH = os.path.join(VAULT, 'signal_graph.json')
MODULE_HINT = {}
MODULE_HINT_ALL = ['未分组']
VAULT_TITLE = '代码 Wiki 索引'

TODAY = None  # 渲染日期由 --date 或当日填充（只进 derived 区）

INDEX_HEADER = '''---
type: index
authority: derived
updated: {date}
updated_by: script
---

# {title}

> 本页是 vault 内容目录：每页一行「链接 + 一句话摘要」，按类别组织。
> 页面 schema、维护规则（compile / update / lint）见仓库 `rules/llm_wiki.md`。
> 治理边界：本 vault 是代码解释层（类/接线/控件），与规格/设计文档分层，零互链——
> 类页不复述领域知识，相关文档仅在 frontmatter `upstream_docs` 记纯路径。
> 数据源：`signal_graph.json`（tools/scan_signal_graph.py 确定性产出）。图谱快照：{edges} 连接 / {classes} 类。
'''


def apply_config(cfg):
    global CFG, REPO, VAULT, GRAPH, MODULE_HINT, MODULE_HINT_ALL, VAULT_TITLE
    CFG = cfg
    REPO = cfg.repo
    VAULT = cfg.out_root
    GRAPH = cfg.graph_path
    MODULE_HINT = cfg.module_hint
    MODULE_HINT_ALL = list(MODULE_HINT.keys()) + ['未分组']
    VAULT_TITLE = os.path.basename(cfg.repo.rstrip('\\/')) + ' 代码 Wiki 索引'


def dhash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:8]


def wrap_derived(inner):
    h = dhash(inner)
    return f'<!-- DERIVED:start hash={h} -->\n{inner}\n<!-- DERIVED:end -->', h


def mmd_label(s):
    """mermaid 节点 label（引号内）：HTML 实体转义，杜绝 `><&"` 触发解析错误。"""
    return (str(s or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            .replace('"', '&quot;').replace('[', '&#91;').replace(']', '&#93;'))


def mermaid_module_graph(edges, project_classes):
    """模块页用：类对聚合视图——同一对 sender→receiver 的多条信号合并为一条边，
    label 标信号数（≤3 个直接列名）。只画**项目类↔项目类**通信（用户指正 2026-09-15：
    控件经局部变量绑定会以 QPushButton/局部变量名混入，属类内部 UI 细节，剔除）；
    完整明细在页面下方表格。"""
    lines = ['graph LR']
    ids = {}

    def nid(name):
        if name not in ids:
            ids[name] = f'n{len(ids)}'
        return ids[name]

    pairs = {}
    order = []
    for e in edges:
        if 'uiFile' in e:
            continue
        if e.get('senderKind') == 'ui_widget' or e.get('receiverKind') == 'ui_widget':
            continue
        s = e.get('sender') or e.get('senderExpr', '?')
        r = e.get('receiver') or e.get('receiverExpr', '?')
        # 只留两端都是本项目类的边（控件类型名/局部变量名/QTimer 等基础设施出局）
        if s not in project_classes or r not in project_classes:
            continue
        k = (s, r)
        if k not in pairs:
            pairs[k] = set()
            order.append(k)
        pairs[k].add(e.get('signal') or '?')
    for (s, r) in order:
        sigs = pairs[(s, r)]
        label = '、'.join(sorted(sigs)) if len(sigs) <= 3 else f'{len(sigs)} 信号'
        lines.append(f'    {nid(s)}["{mmd_label(s)}"] -->|"{mmd_label(label)}"| {nid(r)}["{mmd_label(r)}"]')
    if not order:
        lines.append('    empty["（无信号槽连接）"]')
    return '\n'.join(lines), len(order)


def mermaid_for_edges(edges, title_ok=True):
    """节点用编号 ID（n0/n1…），显示名放 label——ID 永远合法（review CP3 修复：
    原 ID 直接用 sender/receiver 名，含 ->/<>/( ) 时 mermaid parse error）。"""
    lines = ['graph LR']
    ids = {}

    def nid(name):
        if name not in ids:
            ids[name] = f'n{len(ids)}'
        return ids[name]

    seen = set()
    for e in edges:
        if 'uiFile' in e:      # designer 边不进图（仅 1 条，明细表呈现）
            continue
        s = e.get('sender') or e.get('senderExpr', '?')
        r = e.get('receiver') or e.get('receiverExpr', '?')
        if e.get('senderKind') == 'ui_widget':
            s = f'ui_{s}'
        if e.get('receiverKind') == 'ui_widget':
            r = f'ui_{r}'
        sig = e.get('signal') or '?'
        key = (s, r, sig)
        if key in seen:
            continue
        seen.add(key)
        lines.append(f'    {nid(s)}["{mmd_label(s)}"] -->|"{mmd_label(sig)}"| {nid(r)}["{mmd_label(r)}"]')
    if not seen:
        lines.append('    empty["（无信号槽连接）"]')
    return '\n'.join(lines)


# ---------------------------------------------------------------- 数据整理

def load_graph():
    with io.open(GRAPH, encoding='utf-8') as f:
        return json.load(f)


def edges_by_module(g):
    """connect 边按模块分组（依 sender/receiver 类的 module；unknown 归'未分组'）。
    MODULE_HINT 全模块补齐——无边的模块也生成页（review CP3 修复：'标定'悬空）。"""
    mod_of = {c['name']: c['module'] for c in g['nodes']['classes']}
    out = {m: [] for m in MODULE_HINT_ALL}
    for e in g['edges']['signal_connection']:
        m = mod_of.get(e['sender']) or mod_of.get(e['receiver']) or '未分组'
        out.setdefault(m, []).append(e)
    for e in g['edges']['designer_connect']:
        out.setdefault('未分组', []).append(e)
    return out


def class_ui_map(g):
    """类名 → .ui 文件（该类是 UI 类时）。"""
    out = {}
    for c in g['nodes']['classes']:
        stem = os.path.splitext(os.path.basename(c['header']))[0]
        ui = stem + '.ui'
        if os.path.exists(os.path.join(REPO, ui)):
            out[c['name']] = ui
    return out


# ---------------------------------------------------------------- signals 页

def render_signal_pages(g, mods):
    sig_dir = os.path.join(VAULT, 'signals')
    os.makedirs(sig_dir, exist_ok=True)
    written = []
    for mod, edges in sorted(mods.items()):
        if mod == '未分组' and not edges:
            continue
        graph, pair_count = mermaid_module_graph(edges, {c['name'] for c in g['nodes']['classes']})
        mod_html = render_module_html(g, mod, edges)
        html_link = (f'\n> 🔍 交互式模块图（缩放/点边看槽功能注释/双击类节点进入类图）：'
                     f'[interactive/{mod}.html](../interactive/{mod}.html)\n' if mod_html else '')
        body = [f'## {mod} 接线图（{len(edges)} 条连接，聚合为 {pair_count} 个类对；'
                '同对类的多条信号合并为一条边，完整明细见下表）', '',
                html_link,
                '```mermaid',
                graph, '```', '',
                f'## {mod} connect 明细（{len(edges)} 条）', '',
                '| # | sender | signal | receiver | slot | 语法 | 连接类型 | 源码 |',
                '|---|--------|--------|----------|------|------|---------|------|']
        for i, e in enumerate(edges, 1):
            if 'uiFile' in e:      # designer 边
                body.append(f"| {i} | {e.get('sender')} | {e.get('signal')} | {e.get('receiver')} | {e.get('slot')} | Designer | - | {e['uiFile']} |")
                continue
            body.append(
                f"| {i} | {e.get('sender') or e.get('senderExpr')} | {e.get('signal')} | "
                f"{e.get('receiver') or e.get('receiverExpr')} | {e.get('slot') or '(lambda)'} | "
                f"{e.get('syntax')} | {e.get('connType') or 'Auto'} | {e['file']}:{e['line']} |")
        inner = '\n'.join(body)
        wrapped, h = wrap_derived(inner)
        content = (
            f'---\ntype: signals\nauthority: derived\nmodule: {mod}\n'
            f'source: [signal_graph.json]\nupdated: {TODAY}\nupdated_by: script\n'
            f'derived_hash: {h}\n---\n\n# {mod} · 接线图\n\n'
            f'> 本页整页 derived（脚本生成，**禁手改**）。重新生成：`python tools/render_wiki.py`\n\n'
            + wrapped + '\n')
        p = os.path.join(sig_dir, f'{mod}.md')
        with io.open(p, 'w', encoding='utf-8', newline='\n') as f:
            f.write(content)
        written.append(f'signals/{mod}.md')
    # 缺注释清单（类成员 + 类外自由函数 + lambda 连接，全量对账）
    missing = []
    for name, det in sorted(g['class_details'].items()):
        for bucket in ('signals', 'slots', 'functions'):
            for it in det[bucket]:
                if not it.get('comment'):
                    missing.append((name, bucket, it['name'], f'{name}.h:{it.get("line")}'))
    for ff in g.get('free_functions', []):
        if not ff.get('comment'):
            missing.append((ff['file'], '自由函数', ff['name'], f'{ff["file"]}:{ff["line"]}'))
    for e in g['edges']['signal_connection']:
        if not e.get('slot') and not e.get('comment'):
            missing.append((e['file'], 'lambda连接', f"{e.get('sender')}→{e.get('signal')}",
                            f"{e['file']}:{e['line']}"))
    rows = '\n'.join(
        f'| {c} | {b} | {n} | {ln} |' for c, b, n, ln in missing)
    inner = (f'## 缺失注释清单（{len(missing)} 项 = 类成员 {sum(1 for x in missing if x[1] != "自由函数" and x[1] != "lambda连接")}'
             f' + 自由函数 {sum(1 for x in missing if x[1] == "自由函数")}'
             f' + lambda 连接 {sum(1 for x in missing if x[1] == "lambda连接")}）\n\n'
             '| 类/文件 | 类别 | 名称 | 锚点 |\n|----|------|------|------|\n' + rows)
    wrapped, h = wrap_derived(inner)
    p = os.path.join(sig_dir, 'missing-comments.md')
    with io.open(p, 'w', encoding='utf-8', newline='\n') as f:
        f.write(
            f'---\ntype: signals\nauthority: derived\nsource: [signal_graph.json]\n'
            f'updated: {TODAY}\nupdated_by: script\nderived_hash: {h}\n---\n\n'
            f'# 缺失注释清单\n\n> derived（脚本生成）。补齐走独立代码变更任务。\n\n'
            + wrapped + '\n')
    written.append('signals/missing-comments.md')
    return written, len(missing)


# ---------------------------------------------------------------- 交互式 HTML 图

HTML_TEMPLATE = r'''<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>__TITLE__ · 交互式接线图</title>
<style>
  html,body{margin:0;height:100%;overflow:hidden;background:#1a1a1a;color:#ddd;font:14px system-ui,sans-serif}
  #c{display:block;width:100%;height:100%;cursor:grab}
  #bar{position:fixed;top:10px;left:10px;background:#222c;padding:8px 12px;border-radius:8px;border:1px solid #444}
  #bar b{color:#7fd}
  #panel{position:fixed;top:52px;left:10px;max-width:420px;max-height:60%;overflow:auto;background:#222e;
    padding:10px 14px;border-radius:8px;border:1px solid #444;display:none}
  #panel h4{margin:2px 0 6px;color:#7fd}
  #panel table{border-collapse:collapse;font-size:12px}
  #panel td,#panel th{border:1px solid #444;padding:2px 6px;vertical-align:top}
  #hint{position:fixed;bottom:10px;left:10px;background:#222c;padding:6px 12px;border-radius:8px;border:1px solid #444;font-size:12px;color:#999}
  .tag{display:inline-block;padding:0 6px;border-radius:4px;font-size:11px;margin-right:4px}
</style>
</head>
<body>
<canvas id="c"></canvas>
<div id="bar"><b>__TITLE__</b> · 类间信号槽通信（聚合边，点边看信号明细__MODEHINT__）</div>
<div id="panel"></div>
<div id="hint">拖拽节点 · 滚轮缩放 · 点节点高亮邻居 · 点边看信号明细 · 双击空白重置__HINT2__</div>
<script>
const DATA = __DATA__;
const cv = document.getElementById('c'), ctx = cv.getContext('2d');
const panel = document.getElementById('panel');
let W, H, scale = 1, ox = 0, oy = 0;function resize(){ W = cv.width = innerWidth; H = cv.height = innerHeight; }
addEventListener('resize', resize); resize();

const KIND_COLOR = {class:'#4aa3ff', ui_widget:'#ffb454', target:'#7fddaa', unknown:'#999'};
const nodes = DATA.nodes.map(n => ({...n,
  x: W/2 + (Math.random()-0.5)*400, y: H/2 + (Math.random()-0.5)*400, vx:0, vy:0,
  r: n.kind==='target'?34:(n.kind==='ui_widget'?16:24)}));
const idx = {}; nodes.forEach((n,i)=>idx[n.name]=i);

function tick(){
  // 斥力
  for(let i=0;i<nodes.length;i++) for(let j=i+1;j<nodes.length;j++){
    const a=nodes[i], b=nodes[j];
    let dx=a.x-b.x, dy=a.y-b.y, d2=dx*dx+dy*dy+80, f=2600/d2;
    const d=Math.sqrt(d2); dx/=d; dy/=d;
    a.vx+=dx*f; a.vy+=dy*f; b.vx-=dx*f; b.vy-=dy*f;
  }
  // 弹簧 + 向心力
  for(const e of DATA.pairs){
    const a=nodes[idx[e.s]], b=nodes[idx[e.r]];
    let dx=b.x-a.x, dy=b.y-a.y, d=Math.sqrt(dx*dx+dy*dy)||1, f=(d-170)*0.004;
    dx/=d; dy/=d;
    a.vx+=dx*f*d*0.02; a.vy+=dy*f*d*0.02; b.vx-=dx*f*d*0.02; b.vy-=dy*f*d*0.02;
  }
  for(const n of nodes){
    n.vx+=(W/2-n.x)*0.0006; n.vy+=(H/2-n.y)*0.0006;
    if(!n.fixed){ n.x+=n.vx*0.5; n.y+=n.vy*0.5; }
    n.vx*=0.82; n.vy*=0.82;
  }
}
function draw(hi){
  ctx.setTransform(1,0,0,1,0,0);
  ctx.fillStyle='#1a1a1a'; ctx.fillRect(0,0,W,H);
  ctx.setTransform(scale,0,0,scale,ox,oy);
  for(const e of DATA.pairs){
    const a=nodes[idx[e.s]], b=nodes[idx[e.r]];
    const dim = hi && !(hi.set.has(e.s)||hi.set.has(e.r));
    ctx.strokeStyle = dim?'#333':(hi&&(hi.e===e)?'#7fd':'#567');
    ctx.lineWidth = (hi&&hi.e===e)?3:1.5;
    ctx.beginPath(); ctx.moveTo(a.x,a.y); ctx.lineTo(b.x,b.y); ctx.stroke();
    const mx=(a.x+b.x)/2, my=(a.y+b.y)/2;
    ctx.fillStyle = dim?'#444':'#9ab'; ctx.font='11px system-ui';
    ctx.textAlign='center';
    const lb = e.signals.length<=2 ? e.signals.join(' / ') : e.signals.length+' 信号';
    ctx.fillText(lb, mx, my-4);
  }
  for(const n of nodes){
    const dim = hi && !hi.set.has(n.name);
    ctx.globalAlpha = dim?0.25:1;
    ctx.beginPath(); ctx.arc(n.x,n.y,n.r,0,7);
    ctx.fillStyle = n.kind==='target'?'#245c44':(n.kind==='ui_widget'?'#5c4423':'#1e3a5c');
    ctx.fill();
    ctx.strokeStyle = KIND_COLOR[n.kind]||'#888'; ctx.lineWidth = n.kind==='target'?3:2; ctx.stroke();
    ctx.fillStyle = '#eee'; ctx.font = (n.r>20?'bold 13px':'11px')+' system-ui';
    ctx.textAlign='center';
    const label = n.name.length>18 ? n.name.slice(0,17)+'…' : n.name;
    ctx.fillText(label, n.x, n.y+n.r+14);
    ctx.globalAlpha = 1;
  }
}
let frames=0;
function loop(){ tick(); draw(sel); if(++frames<400) requestAnimationFrame(loop); }

let sel=null, drag=null, down=null;
function pos(ev){ const r=cv.getBoundingClientRect(); return {x:(ev.clientX-r.left-ox)/scale, y:(ev.clientY-r.top-oy)/scale}; }
cv.addEventListener('mousedown', ev=>{
  const p=pos(ev); down={x:ev.clientX,y:ev.clientY};
  for(let i=nodes.length-1;i>=0;i--){ const n=nodes[i];
    if((p.x-n.x)**2+(p.y-n.y)**2 < (n.r+6)**2){ drag=n; n.fixed=true; return; } }
  drag=null;
});
cv.addEventListener('mousemove', ev=>{
  if(drag){ const p=pos(ev); drag.x=p.x; drag.y=p.y; drag.vx=drag.vy=0; draw(sel); }
});
cv.addEventListener('mouseup', ()=>{ if(drag){drag.fixed=false; drag=null;} });
cv.addEventListener('dblclick', ev=>{
  const p=pos(ev);
  // 模块模式：双击类节点 → 进入该类接线图（分层 drill-down）
  if(DATA.mode==='module'){
    for(let i=nodes.length-1;i>=0;i--){ const n=nodes[i];
      if((p.x-n.x)**2+(p.y-n.y)**2 < (n.r+6)**2 && n.kind!=='ui_widget'){
        location.href = encodeURIComponent(n.name) + '.html'; return; } }
  }
  sel=null; panel.style.display='none'; scale=1; ox=oy=0; draw(null);
});
cv.addEventListener('wheel', ev=>{
  ev.preventDefault();
  const k = ev.deltaY<0?1.12:0.89, r=cv.getBoundingClientRect();
  const mx=ev.clientX-r.left, my=ev.clientY-r.top;
  ox = mx-(mx-ox)*k; oy = my-(my-oy)*k; scale*=k; draw(sel);
}, {passive:false});
cv.addEventListener('click', ev=>{
  const p=pos(ev);
  // 节点？
  for(let i=nodes.length-1;i>=0;i--){ const n=nodes[i];
    if((p.x-n.x)**2+(p.y-n.y)**2 < (n.r+6)**2){
      const set=new Set([n.name]);
      for(const e of DATA.pairs){ if(e.s===n.name) set.add(e.r); if(e.r===n.name) set.add(e.s); }
      sel={set, e:null}; panel.style.display='none'; draw(sel); return; } }
  // 边？（点到线段距离）
  for(const e of DATA.pairs){
    const a=nodes[idx[e.s]], b=nodes[idx[e.r]];
    const dx=b.x-a.x, dy=b.y-a.y, L2=dx*dx+dy*dy||1;
    let t=((p.x-a.x)*dx+(p.y-a.y)*dy)/L2; t=Math.max(0,Math.min(1,t));
    const d=Math.hypot(p.x-(a.x+t*dx), p.y-(a.y+t*dy));
    if(d<8){ sel={set:new Set([e.s,e.r]), e};
      let rows='';
      for(const s of e.detail){
        rows += `<tr><td>${s.sig}</td><td>${s.slot||'(lambda)'}</td><td>${s.note||'🔴 缺注释'}</td></tr>`;
      }
      panel.innerHTML = `<h4>${e.s} → ${e.r}</h4>
        <table><tr><th>信号</th><th>槽</th><th>功能说明（槽注释）</th></tr>${rows}</table>`;
      panel.style.display='block'; draw(sel); return; } }
  sel=null; panel.style.display='none'; draw(null);
});
loop();
</script>
</body>
</html>
'''


def build_comment_lookup(g):
    """(类名, 成员名) → 注释。槽/函数/信号全收（用户令 2026-09-15：明细表
    显示函数功能注释而非源码行号）。"""
    out = {}
    for cls, det in g['class_details'].items():
        for bucket in ('signals', 'slots', 'functions'):
            for it in det[bucket]:
                if it.get('comment'):
                    out[(cls, it['name'])] = it['comment']
    return out


def _pair_data(g, edges, comments):
    """公共：边聚合为类对 + 每条的槽注释 note。"""
    nodes = {}
    pairs = {}
    for e in edges:
        s, sk = e.get('sender') or e.get('senderExpr'), e.get('senderKind')
        r, rk = e.get('receiver') or e.get('receiverExpr'), e.get('receiverKind')
        if sk == 'ui_widget':
            s = f'ui:{s}'
        if rk == 'ui_widget':
            r = f'ui:{r}'
        for name, kind in ((s, sk), (r, rk)):
            nodes.setdefault(name, 'ui_widget' if kind == 'ui_widget' else 'class')
        k = (s, r)
        pairs.setdefault(k, {'signals': [], 'detail': []})
        sig = e.get('signal') or '?'
        if sig not in pairs[k]['signals']:
            pairs[k]['signals'].append(sig)
        slot = e.get('slot')
        note = None
        if slot:
            owner = e.get('slotOwner') or e.get('receiver')
            note = comments.get((owner, slot)) or comments.get((e.get('signalOwner'), slot))
        else:
            # lambda/函数对象接收端：消费 connect 上方注释（22 规范 §5）
            note = e.get('brief') or e.get('comment')
        pairs[k]['detail'].append({'sig': sig, 'slot': slot, 'note': note})
    return nodes, pairs


def render_interactive_html(g, cls_name):
    """类级交互图（叶子层）。自包含 vanilla 力导向（无 CDN，工业离线可用）。"""
    edges = [e for e in g['edges']['signal_connection']
             if cls_name in (e.get('sender'), e.get('receiver'))]
    comments = build_comment_lookup(g)
    nodes, pairs = _pair_data(g, edges, comments)
    nodes[cls_name] = 'target'
    data = {
        'mode': 'class', 'target': cls_name,
        'nodes': [{'name': n, 'kind': k} for n, k in sorted(nodes.items())],
        'pairs': [{'s': s, 'r': r, 'signals': v['signals'], 'detail': v['detail']}
                  for (s, r), v in sorted(pairs.items())],
    }
    return _write_html(cls_name, data), len(edges), len(pairs)


def render_module_html(g, mod, edges):
    """模块级交互图（上层）：项目类对聚合；双击类节点 drill-down 到该类图（用户令
    2026-09-15：不做全库单图，分层节省渲染）。只收项目类↔项目类边（控件经局部
    变量绑定的边属类内部细节，剔除——与 mermaid 聚合同口径）。"""
    project_classes = {c['name'] for c in g['nodes']['classes']}
    edges = [e for e in edges
             if e.get('senderKind') != 'ui_widget' and e.get('receiverKind') != 'ui_widget'
             and (e.get('sender') in project_classes)
             and (e.get('receiver') in project_classes)]
    if not edges:
        return None
    comments = build_comment_lookup(g)
    nodes, pairs = _pair_data(g, edges, comments)
    data = {
        'mode': 'module', 'target': mod,
        'nodes': [{'name': n, 'kind': k} for n, k in sorted(nodes.items())],
        'pairs': [{'s': s, 'r': r, 'signals': v['signals'], 'detail': v['detail']}
                  for (s, r), v in sorted(pairs.items())],
    }
    return _write_html(mod, data)


def _write_html(title, data):
    html = (HTML_TEMPLATE.replace('__TITLE__', title)
            .replace('__MODEHINT__', '，双击类节点进入该类接线图' if data['mode'] == 'module' else '')
            .replace('__HINT2__', ' · 双击类节点进入类图' if data['mode'] == 'module' else '')
            .replace('__DATA__', json.dumps(data, ensure_ascii=False)))
    out_dir = os.path.join(VAULT, 'interactive')
    os.makedirs(out_dir, exist_ok=True)
    p = os.path.join(out_dir, f'{title}.html')
    with io.open(p, 'w', encoding='utf-8', newline='\n') as f:
        f.write(html)
    return p


# ---------------------------------------------------------------- 类页

def render_class_page(g, cls, ui_map, only=None):
    name = cls['name']
    if only and name not in only:
        return None
    det = g['class_details'].get(name, {'signals': [], 'slots': [], 'functions': [], 'members': []})
    p = os.path.join(VAULT, 'components', f'{name}.md')
    is_ui = name in ui_map

    # 文件构成
    stem = os.path.splitext(os.path.basename(cls['header']))[0]
    files = [(cls['header'], '声明')]
    cpp = f'{stem}.cpp'
    if os.path.exists(os.path.join(REPO, cpp)):
        files.append((cpp, '实现'))
    if is_ui:
        files.append((ui_map[name], 'Designer 界面'))
        files.append((f'ui_{stem}.h', 'uic 生成（只读参考）'))
    file_rows = '\n'.join(
        f'| {fn} | {role} | {sum(1 for _ in io.open(os.path.join(REPO, fn), encoding="utf-8", errors="ignore"))} |'
        for fn, role in files if os.path.exists(os.path.join(REPO, fn)))

    # 接线（该类相关的边）
    related = [e for e in g['edges']['signal_connection']
               if name in (e.get('sender'), e.get('receiver'))]
    wiring = mermaid_for_edges(related)

    def table(items, cols_fn):
        rows = [cols_fn(it) for it in items]
        return '\n'.join(rows) if rows else '| — | — | — | — |'

    sig_rows = table(det['signals'],
                     lambda it: f"| {it['name']} | {it.get('brief') or it.get('comment') or '🔴'} | {it.get('listeners') or listeners_of(g, name, it['name'])} | {os.path.basename(cls['header'])}:{it['line']} |")
    slot_rows = table(det['slots'],
                      lambda it: f"| {it['name']} | {it.get('brief') or it.get('comment') or '🔴'} | {senders_of(g, name, it['name']) or '—'} | {os.path.basename(cls['header'])}:{it['line']} |")
    func_rows = table(det['functions'],
                      lambda it: f"| {it['name']} | {it.get('brief') or it.get('comment') or '🔴'} | {it.get('ret', '')} | {os.path.basename(cls['header'])}:{it['line']} |")

    # 关键成员：出现在接线表达式里的成员
    exprs = set()
    for e in related:
        exprs.add(e.get('senderExpr', ''))
    member_names = {m['name'] for m in det['members']}
    key_members = [m for m in det['members'] if m['name'] in exprs or m['name'] in ('ui',)]
    mem_rows = '\n'.join(
        f"| {m['name']} | {m['type']} | {m.get('comment') or '—'} |" for m in key_members
    ) or '| — | — | — |'

    # 控件 → 功能（UI 类）：sender 为 ui->xxx 且归属本类主 cpp 的 connect
    ctl_rows = ''
    if is_ui:
        stem_l = stem
        ce = [e for e in g['edges']['signal_connection']
              if e['senderKind'] == 'ui_widget' and os.path.splitext(os.path.basename(e['file']))[0] == stem_l]
        ctl_rows = '\n'.join(
            f"| {e['sender']} | — | — | {e.get('signal')} | {e.get('slot') or '(lambda)'} | {e['file']}:{e['line']} |"
            for e in ce) or '| — | — | — | — | — | — |'

    # derived 区块组装（整块替换）
    # 交互式 HTML 引用行（存在时；试点阶段按文件存在判断）
    interactive_line = ''
    ih = os.path.join(VAULT, 'interactive', f'{name}.html')
    if os.path.exists(ih):
        interactive_line = (f'\n 🔍 交互式大图（缩放/拖拽/点边看信号明细，浏览器打开）：'
                            f'[interactive/{name}.html](interactive/{name}.html)\n')
    inner_parts = [f'#### 文件构成\n\n| 文件 | 角色 | 行数 |\n|------|------|------|\n{file_rows}',
                   f'#### 接线图（{len(related)} 条相关连接；图按 sender×signal×receiver 去重）\n{interactive_line}\n```mermaid\n{wiring}\n```',
                   f'#### 信号（{len(det["signals"])}）\n\n| 信号 | 语义 | 监听者 | 源码 |\n|------|------|--------|------|\n{sig_rows}',
                   f'#### 槽（{len(det["slots"])}）\n\n| 槽 | 语义 | 触发者 | 源码 |\n|-----|------|--------|------|\n{slot_rows}',
                   f'#### 内部结构 · 函数清单（{len(det["functions"])}，分组见上）\n\n| 函数 | 语义 | 返回 | 源码 |\n|------|------|------|------|\n{func_rows}',
                   f'#### 关键成员（接线相关）\n\n| 变量 | 类型 | 语义 |\n|------|------|------|\n{mem_rows}']
    if is_ui:
        inner_parts.append(f'#### 控件 → 功能\n\n| 控件 | 类型 | 所在页签 | 信号 | 处理槽 | 源码 |\n|------|------|---------|------|--------|------|\n{ctl_rows}')
    inner = '\n\n'.join(inner_parts)
    wrapped, h = wrap_derived(inner)

    existing = None
    if os.path.exists(p):
        existing = io.open(p, encoding='utf-8').read()

    front = (f'---\ntype: component\nauthority: narrative\nsource: [{cls["header"]}'
             + (f', {cpp}' if os.path.exists(os.path.join(REPO, cpp)) else '')
             + (f', {ui_map[name]}' if is_ui else '')
             + f']\nupstream_docs: []\nupdated: {TODAY}\nupdated_by: script\nverified: pending\n'
               f'derived_hash: {h}\nclass: {name}\nmodule: {cls["module"]}\n---\n')

    if existing:
        # 仅替换 DERIVED 区块 + 更新 frontmatter 的 derived_hash/updated 行
        new_page = replace_derived(existing, wrapped)
        new_page = re.sub(r'^derived_hash: .*$', f'derived_hash: {h}', new_page, count=1, flags=re.M)
        return p, new_page
    # 新页：模板骨架 + narrative 占位
    narrative = (f'# {name}\n\n## 职责\n\n'
                 '（待 session 填写：一句话定义 + 3~5 句展开——是什么 / 边界 / 协作对象）\n\n'
                 '## 内部结构 · 分组导读\n\n'
                 '（待 session 填写：函数按功能分组说明，如 初始化 / 业务流程 / UI 交互 / 查询；'
                 '明细表在下方 derived 区块）\n\n'
                 '## 生命周期\n\n'
                 '（待 session 填写：构造入口 / 所属线程 / 析构清理 / 跨线程边界一句话）\n\n')
    ui_note = ('\n> UI 类：控件 → 功能映射见下方 derived 区块末尾。\n' if is_ui else '')
    page = front + '\n' + narrative + ui_note + '\n## 数据区（derived，勿手改）\n\n' + wrapped + '\n'
    return p, page


def replace_derived(page, new_wrapped):
    pat = re.compile(r'<!-- DERIVED:start hash=[0-9a-f]+ -->.*?<!-- DERIVED:end -->', re.S)
    if not pat.search(page):
        return page + '\n\n## 数据区（derived，勿手改）\n\n' + new_wrapped + '\n'
    return pat.sub(lambda m: new_wrapped, page, count=1)


def listeners_of(g, cls, sig):
    names = sorted({e['receiver'] for e in g['edges']['signal_connection']
                    if e.get('signalOwner') == cls and e.get('signal') == sig and e.get('receiver')})
    return '、'.join(names) or '—'


def senders_of(g, cls, slot):
    names = sorted({e['sender'] for e in g['edges']['signal_connection']
                    if e.get('slot') == slot and e.get('slotOwner') == cls and e.get('sender')})
    return '、'.join(names) or '—'


# ---------------------------------------------------------------- index

def render_index(g, mods, missing_count, sig_pages):
    lines = [INDEX_HEADER.format(date=TODAY, title=VAULT_TITLE,
                                 edges=len(g['edges']['signal_connection']),
                                 classes=len(g['nodes']['classes']))]
    lines.append('\n## 接线图 signals/（脚本生成，禁手改）\n')
    for sp in sig_pages:
        base = os.path.splitext(os.path.basename(sp))[0]
        if 'missing' in sp:
            lines.append(f'- [[{base}]] — 缺失注释清单（补齐另立任务）')
        else:
            lines.append(f'- [[{base}]] — 接线图与 connect 明细')
    lines.append('\n## 类页 components/\n')
    for c in sorted(g['nodes']['classes'], key=lambda x: x['name']):
        if not c['qobject']:
            continue
        lines.append(f"- [[{c['name']}]] — {c['module']}；信号 {c['signalCount']} / 槽 {c['slotCount']}"
                     f"（narrative 待填：职责/分组/生命周期）")
    lines.append('\n## 概念页 concepts/（跨类代码概念）\n\n- （空，待沉淀）')
    lines.append('\n## 分析页 analyses/（查询/排查回填）\n\n- （空，待沉淀）')
    p = os.path.join(VAULT, 'index.md')
    with io.open(p, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(lines) + '\n')
    return p


# ---------------------------------------------------------------- lint L0

def lint(g):
    problems = []
    comp_dir = os.path.join(VAULT, 'components')
    known = {c['name'] for c in g['nodes']['classes'] if c['qobject']}
    on_disk = {os.path.splitext(f)[0] for f in os.listdir(comp_dir) if f.endswith('.md')}
    for name in sorted(known - on_disk):
        problems.append(f'缺类页: {name}')
    for name in sorted(on_disk - known):
        problems.append(f'多余页面（图谱无此类/非 Q_OBJECT）: {name}')
    # frontmatter + derived_hash 校验
    for fn in sorted(on_disk):
        p = os.path.join(comp_dir, fn + '.md')
        text = io.open(p, encoding='utf-8').read()
        is_narrative = 'authority: narrative' in text
        for field in ('type:', 'authority:', 'updated:', 'updated_by:'):
            if field not in text:
                problems.append(f'{fn}.md 缺 frontmatter 字段 {field}')
        if is_narrative and 'verified:' not in text:
            problems.append(f'{fn}.md 缺 frontmatter 字段 verified:')
        m = re.search(r'^derived_hash: ([0-9a-f]+)', text, re.M)
        dm = re.search(r'<!-- DERIVED:start hash=([0-9a-f]+) -->\n(.*?)\n<!-- DERIVED:end -->', text, re.S)
        if m and dm:
            if m.group(1) != dm.group(1):
                problems.append(f'{fn}.md derived_hash 不一致（手改过 derived 区块或渲染未完成）')
            if dhash(dm.group(2)) != dm.group(1):
                problems.append(f'{fn}.md derived 区块内容与 hash 不符（被手改）')
        if 'verified: pending' in text and '（待 session 填写' not in text:
            pass  # 已填叙事但未审：正常状态，抽检制覆盖
    return problems


def main():
    global TODAY
    import datetime
    ap = argparse.ArgumentParser(description='signal_graph → Obsidian vault 渲染器')
    add_common_args(ap)
    ap.add_argument('--lint', action='store_true')
    ap.add_argument('--only', default='')
    ap.add_argument('--html', default='',
                    help='生成指定类的交互式接线图 HTML（--html all 或 --html Foo,Bar）')
    ap.add_argument('--date', default=datetime.date.today().isoformat())
    ns = ap.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    TODAY = ns.date

    apply_config(config_from_args(ns))
    print(f'[render] repo={REPO} vault={VAULT}')

    g = load_graph()
    if ns.lint:
        probs = lint(g)
        print(f'[lint L0] {len(probs)} 个问题')
        for x in probs:
            print('  -', x)
        sys.exit(1 if probs else 0)

    if ns.html:
        names = ([c['name'] for c in g['nodes']['classes'] if c['qobject']]
                 if ns.html.strip().lower() == 'all' else ns.html.split(','))
        for name in names:
            name = name.strip()
            if name:
                p, ec, pc = render_interactive_html(g, name)
                print(f'[html] {os.path.relpath(p, REPO)}（{ec} 条连接聚合为 {pc} 个类对）')

    mods = edges_by_module(g)
    ui_map = class_ui_map(g)
    sig_pages, missing = render_signal_pages(g, mods)
    only = set(ns.only.split(',')) if ns.only else None
    pages = 0
    for cls in sorted(g['nodes']['classes'], key=lambda c: c['name']):
        if not cls['qobject']:
            continue
        r = render_class_page(g, cls, ui_map, only)
        if r:
            p, content = r
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with io.open(p, 'w', encoding='utf-8', newline='\n') as f:
                f.write(content)
            pages += 1
    render_index(g, mods, missing, sig_pages)
    print(f'[render] signals 页 {len(sig_pages)}（含缺注释清单 {missing} 项）| 类页 {pages} | index 重建')
    probs = lint(g)
    print(f'[lint L0] 渲染后自检: {len(probs)} 个问题')
    for x in probs[:10]:
        print('  -', x)


if __name__ == '__main__':
    main()
