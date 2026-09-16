#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wiki_config.py — qt-code-wiki-graph 共享配置加载

目标项目根目录可放一份 `qt_code_wiki.json`（可选）与 `modules.json`（可选）。
CLI 参数优先于配置文件；配置文件优先于内置默认值。
"""

import json
import os
from pathlib import Path

# 通用排除目录（Qt/C++ 工程常见构建与工具产物；项目特有目录进配置）
DEFAULT_EXCLUDE_DIRS = {
    'build', 'build-*', 'cmake-build-*', 'Temp', 'tmp', 'debug', 'release',
    'debug_build', 'release_build', 'generated', 'out', 'dist',
    '.git', '.svn', '.hg', '.vs', '.vscode', '.idea', '.clangd',
    '.codegraph', '.agent', '.claude', '.pi', '.qoder',
    '__pycache__', 'node_modules',
}

# uic/moc/qrc 生成文件名前缀（必须排除，否则 Ui::X 会覆盖真类）
GENERATED_FILE_PREFIXES = ('ui_', 'moc_', 'qrc_', 'moc_predefs')

DEFAULT_CONFIG_NAME = 'qt_code_wiki.json'
DEFAULT_MODULES_NAME = 'modules.json'


def default_tools_dir():
    return os.path.dirname(os.path.abspath(__file__))


def default_repo_root():
    """tools/ 的上一级（工具与目标仓库同仓时的缺省）。"""
    return os.path.dirname(default_tools_dir())


def load_json(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def discover_pro(repo):
    """在仓库根发现 .pro；多个时优先非 *.pri 且名字更短/更像主工程。"""
    pros = sorted(Path(repo).glob('*.pro'))
    if not pros:
        return ''
    # 优先含 QT += widgets 的；否则取第一个
    for p in pros:
        try:
            text = p.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        if 'QT' in text and ('widgets' in text or 'gui' in text):
            return str(p)
    return str(pros[0])


def load_modules(repo, modules_path=None):
    """
    加载 类名→模块名 映射。
    支持两种格式：
      A. {"MainWindow": "UI 层", "Worker": "Worker 层"}          # 推荐
      B. {"UI 层": ["MainWindow", ...]}                          # 兼容旧结构
    未命中类统一落 "未分组"。
    """
    path = modules_path
    if not path:
        cand = os.path.join(repo, DEFAULT_MODULES_NAME)
        path = cand if os.path.exists(cand) else None
    if not path or not os.path.exists(path):
        return {}

    data = load_json(path)
    mapping = {}
    if not isinstance(data, dict):
        return mapping
    # 判断格式：值是 list → 模块→类列表；值是 str → 类→模块
    sample_val = next(iter(data.values()), None)
    if isinstance(sample_val, list):
        for mod, classes in data.items():
            for cls in classes:
                mapping[str(cls)] = str(mod)
    elif isinstance(sample_val, str):
        mapping = {str(k): str(v) for k, v in data.items()}
    return mapping


class ProjectConfig:
    """解析后的项目配置（scan / render 共用）。"""

    def __init__(self, repo, out_root=None, config_path=None, modules_path=None,
                 pro=None, exclude_dirs=None, exclude_dir_extra=None,
                 free_function_exclude_prefixes=None,
                 file_class_overrides=None,
                 assert_no_on=False,
                 include_untracked=False,
                 unresolved_path=None):
        self.repo = os.path.abspath(repo)
        cfg = {}
        if config_path is None:
            auto = os.path.join(self.repo, DEFAULT_CONFIG_NAME)
            config_path = auto if os.path.exists(auto) else None
        if config_path and os.path.exists(config_path):
            cfg = load_json(config_path)
            self.config_path = os.path.abspath(config_path)
        else:
            self.config_path = None

        # CLI/配置中的相对路径一律相对 repo 解析（勿用 abspath('') → CWD）
        def _abs(p, default):
            raw = p or default
            if not raw:
                return ''
            if os.path.isabs(raw):
                return os.path.normpath(raw)
            return os.path.normpath(os.path.join(self.repo, raw))

        self.out_root = _abs(out_root or cfg.get('out_root'), 'code_wiki')
        raw_unres = unresolved_path or cfg.get('unresolved_path')
        if raw_unres:
            self.unresolved_path = _abs(raw_unres, '')
        else:
            self.unresolved_path = os.path.join(self.out_root, 'scan_unresolved.txt')
        self.graph_path = os.path.join(self.out_root, 'signal_graph.json')

        raw_pro = pro or cfg.get('pro') or ''
        if raw_pro:
            self.pro = raw_pro if os.path.isabs(raw_pro) else os.path.abspath(
                os.path.join(self.repo, raw_pro))
        else:
            self.pro = discover_pro(self.repo)

        excludes = set(DEFAULT_EXCLUDE_DIRS)
        for d in (cfg.get('exclude_dirs') or []):
            excludes.add(d)
        for d in (exclude_dir_extra or []):
            excludes.add(d)
        # CLI 若显式给了集合则再并集（不替换通用项）
        if exclude_dirs:
            excludes |= set(exclude_dirs)
        self.exclude_dirs = excludes

        self.free_function_exclude_prefixes = list(
            free_function_exclude_prefixes
            if free_function_exclude_prefixes is not None
            else cfg.get('free_function_exclude_prefixes') or [])
        self.file_class_overrides = dict(
            file_class_overrides
            if file_class_overrides is not None
            else cfg.get('file_class_overrides') or {})

        self.assert_no_on = bool(
            assert_no_on if assert_no_on else cfg.get('assert_no_on', False))
        self.include_untracked = include_untracked

        self.modules_path = modules_path or cfg.get('modules_file') or None
        if self.modules_path and not os.path.isabs(self.modules_path):
            self.modules_path = os.path.join(self.repo, self.modules_path)
        self.module_of = load_modules(self.repo, self.modules_path)
        # 单一来源：module → [class, ...]（渲染器兼容旧接口）
        self.module_hint = {}
        for cls, mod in self.module_of.items():
            self.module_hint.setdefault(mod, []).append(cls)
        for mod in self.module_hint:
            self.module_hint[mod] = sorted(self.module_hint[mod])
        self.module_names = sorted(self.module_hint.keys()) + ['未分组']

    def module_for(self, cls_name):
        return self.module_of.get(cls_name, '未分组')


def add_common_args(ap):
    """两个工具共用的 CLI 参数。"""
    ap.add_argument('--repo', default=None,
                    help='目标 Qt 工程根目录（默认 tools/ 的上一级）')
    ap.add_argument('--config', default=None,
                    help=f'配置文件路径（默认 <repo>/{DEFAULT_CONFIG_NAME}，若存在）')
    ap.add_argument('--out-root', default=None,
                    help='输出 vault 根目录（默认 <repo>/code_wiki）')
    ap.add_argument('--modules', default=None,
                    help=f'类→模块映射 JSON（默认 <repo>/{DEFAULT_MODULES_NAME}，若存在）')
    ap.add_argument('--pro', default=None, help='.pro 工程文件（默认自动发现）')
    return ap


def config_from_args(ns):
    repo = ns.repo or default_repo_root()
    return ProjectConfig(
        repo=repo,
        out_root=getattr(ns, 'out_root', None),
        config_path=getattr(ns, 'config', None),
        modules_path=getattr(ns, 'modules', None),
        pro=getattr(ns, 'pro', None),
        exclude_dir_extra=getattr(ns, 'exclude_dir', None),
        assert_no_on=getattr(ns, 'assert_no_on', False),
        include_untracked=getattr(ns, 'include_untracked', False),
        unresolved_path=getattr(ns, 'unresolved', None),
    )
