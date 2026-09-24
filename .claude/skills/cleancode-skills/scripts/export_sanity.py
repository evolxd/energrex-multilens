#!/usr/bin/env python3
"""Export Sanity：扫描全项目（含 main.py、routers/、tests/、配置文件），找出目标模块中
被外部引用、重新导出（re-export）、打桩（mock.patch）或动态调用的名字。只读，不修改任何文件。

输出的"对外契约名单"中的每个名字，重构后必须以同名、同签名留在原模块中。

用法:
    python export_sanity.py TARGET_FILE [--root .] [--module pkg.mod] [--json OUT.json]
    python export_sanity.py TARGET_FILE --compare BEFORE.json   # 重构后执行；有名字丢失则退出码 1

重构前用 --json 保存基线，重构后用 --compare 对比：任何公开名 / 契约名消失都视为破坏兼容。
"""
import argparse
import ast
import json
import os
import re
import sys

EXCLUDE_DIRS = {
    ".git", ".hg", ".venv", "venv", "env", "node_modules", "__pycache__", "build", "dist",
    "site-packages", ".tox", ".nox", ".mypy_cache", ".pytest_cache",
}
CONFIG_EXTS = {".toml", ".ini", ".cfg", ".yaml", ".yml", ".json", ".env", ".conf"}
CONFIG_NAMES = {"Procfile", "Dockerfile", "Makefile"}


def module_name(path, root):
    rel = os.path.splitext(os.path.relpath(path, root))[0].replace(os.sep, ".").replace("/", ".")
    return rel[: -len(".__init__")] if rel.endswith(".__init__") else rel


def module_candidates(target, root, override):
    if override:
        return {override}
    name = module_name(target, root)
    cands = {name}
    for prefix in ("src.", "app.", "lib."):
        if name.startswith(prefix):
            cands.add(name[len(prefix):])
    return cands


def top_level_defs(tree):
    defs, dunder_all = {}, None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defs[node.name] = node.lineno
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name):
                    defs[t.id] = node.lineno
                    if t.id == "__all__" and isinstance(node.value, (ast.List, ast.Tuple)):
                        dunder_all = [e.value for e in node.value.elts if isinstance(e, ast.Constant)]
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                if a.name != "*":
                    defs[(a.asname or a.name).split(".")[0]] = node.lineno
    return defs, dunder_all


def resolve_from(node, file_mod, is_pkg):
    if node.level == 0:
        return node.module or ""
    parts = file_mod.split(".")
    base = parts if is_pkg else parts[:-1]
    base = base[: len(base) - (node.level - 1)] if node.level > 1 else base
    return ".".join(base + ([node.module] if node.module else []))


def dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        inner = dotted(node.value)
        return f"{inner}.{node.attr}" if inner else None
    return None


def iter_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS and not d.startswith(".")]
        for f in filenames:
            yield os.path.join(dirpath, f)


def scan_python(path, root, mods, target_abs, hits, warnings):
    try:
        with open(path, encoding="utf-8-sig") as fh:
            tree = ast.parse(fh.read())
    except (SyntaxError, UnicodeDecodeError) as exc:
        # 绝不静默跳过：漏扫一个调用方 = 兼容检查假绿
        warnings.append(f"{os.path.relpath(path, root)} 无法解析（{exc.__class__.__name__}），"
                        "其中的引用未被扫描，必须人工 Grep 确认")
        return
    rel = os.path.relpath(path, root)
    file_mod = module_name(path, root)
    is_pkg = os.path.basename(path) == "__init__.py"
    is_self = os.path.abspath(path) == target_abs
    aliases = {}  # 本文件中指向目标模块的本地名 → 用于匹配 alias.name

    def add(name, kind, node):
        hits.setdefault(name, []).append({"where": f"{rel}:{node.lineno}", "kind": kind})

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and not is_self:
            src = resolve_from(node, file_mod, is_pkg)
            if src in mods:
                for a in node.names:
                    if a.name == "*":
                        warnings.append(f"{rel}:{node.lineno} 使用 `from {src} import *`：目标模块所有非 _ 开头的名字都是对外契约")
                        add("*", "star-import", node)
                    else:
                        add(a.name, "re-export" if is_pkg else "import", node)
            else:
                # from pkg import mod  →  mod 是目标模块
                for a in node.names:
                    if f"{src}.{a.name}" in mods:
                        aliases[a.asname or a.name] = True
        elif isinstance(node, ast.Import) and not is_self:
            for a in node.names:
                if a.name in mods:
                    aliases[a.asname or a.name] = True
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            for m in mods:
                for match in re.finditer(rf"(?<![\w.]){re.escape(m)}[.:](\w+)", node.value):
                    add(match.group(1), "string-ref(mock.patch/路由/任务名)", node)
                if node.value == m:
                    warnings.append(f"{rel}:{node.lineno} 以字符串引用模块 '{m}'（importlib / 动态加载），需人工确认调用了哪些名字")

    if aliases or not is_self:
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                owner = dotted(node.value)
                if owner and (owner in aliases or owner in mods):
                    add(node.attr, "attribute", node)
            elif isinstance(node, ast.Call) and getattr(node.func, "id", None) in ("getattr", "hasattr", "setattr"):
                if node.args and dotted(node.args[0]) in set(aliases) | mods:
                    arg = node.args[1] if len(node.args) > 1 else None
                    if isinstance(arg, ast.Constant):
                        add(arg.value, f"dynamic-{node.func.id}", node)
                    else:
                        warnings.append(f"{rel}:{node.lineno} 对目标模块使用非常量 {node.func.id}()，无法静态确定被调用的名字")


def scan_config(path, root, mods, hits):
    try:
        with open(path, encoding="utf-8-sig") as fh:
            text = fh.read()
    except (UnicodeDecodeError, OSError):
        return  # 二进制或非 UTF-8 配置文件，跳过
    rel = os.path.relpath(path, root)
    for m in mods:
        for match in re.finditer(rf"(?<![\w.]){re.escape(m)}[.:](\w+)", text):
            line = text.count("\n", 0, match.start()) + 1
            hits.setdefault(match.group(1), []).append({"where": f"{rel}:{line}", "kind": "config-ref"})


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台默认 cp1252/gbk
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target")
    ap.add_argument("--root", default=".")
    ap.add_argument("--module", help="目标模块的导入路径（自动推断不准时手动指定）")
    ap.add_argument("--json", help="把结果写入 JSON 文件，作为重构前基线")
    ap.add_argument("--compare", help="与重构前基线 JSON 对比，公开名或契约名丢失则退出码 1")
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    target_abs = os.path.abspath(args.target)
    with open(target_abs, encoding="utf-8-sig") as fh:
        defs, dunder_all = top_level_defs(ast.parse(fh.read()))
    mods = module_candidates(target_abs, root, args.module)

    hits, warnings = {}, []
    for path in iter_files(root):
        name = os.path.basename(path)
        ext = os.path.splitext(name)[1]
        if ext == ".py":
            scan_python(path, root, mods, target_abs, hits, warnings)
        elif ext in CONFIG_EXTS or name in CONFIG_NAMES:
            scan_config(path, root, mods, hits)

    public_defs = sorted(n for n in defs if not n.startswith("_") or n == "__all__")
    contract = sorted(n for n in hits if n in defs or n == "*")
    unknown = sorted(n for n in hits if n not in defs and n != "*")
    if dunder_all:
        contract = sorted(set(contract) | set(dunder_all))
    if "*" in hits:
        contract = sorted(set(contract) | {n for n in public_defs if n != "__all__"})

    print(f"目标模块: {', '.join(sorted(mods))}")
    print(f"顶层公开名 {len(public_defs)} 个；__all__ = {dunder_all}\n")
    print("## 对外契约名单（重构后必须同名、同签名保留在原模块）\n")
    print("| 名字 | 引用位置 | 引用方式 |")
    print("|---|---|---|")
    for n in contract:
        refs = hits.get(n) or [{"where": "__all__ / star-import", "kind": "export"}]
        for r in refs:
            print(f"| `{n}` | {r['where']} | {r['kind']} |")
    if unknown:
        print("\n## 外部引用了但目标模块顶层未定义的名字（可能来自类属性、已删除的旧名或别名链，需人工确认）\n")
        for n in unknown:
            print(f"- `{n}`: " + ", ".join(r["where"] for r in hits[n][:5]))
    if warnings:
        print("\n## 需人工确认的警告\n")
        for w in sorted(set(warnings)):
            print(f"- {w}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"modules": sorted(mods), "public_defs": public_defs, "__all__": dunder_all,
                       "contract": contract, "refs": hits, "warnings": sorted(set(warnings))},
                      fh, ensure_ascii=False, indent=2)
        print(f"\n已写入 {args.json}")

    if args.compare:
        with open(args.compare, encoding="utf-8") as fh:
            before = json.load(fh)
        lost_public = sorted(set(before["public_defs"]) - set(public_defs))
        lost_contract = sorted(set(before["contract"]) - set(contract))
        added_public = sorted(set(public_defs) - set(before["public_defs"]))
        print("\n## 与基线对比\n")
        failed = False
        if added_public:
            print(f"- ❌ 新增公开名（辅助函数须以 _ 开头，否则会改变 star-import 导出面）: {added_public}")
            failed = True
        if lost_public or lost_contract:
            print(f"- ❌ 丢失公开名: {lost_public}")
            print(f"- ❌ 丢失契约名: {lost_contract}")
            print("- 必须恢复原名，或在原模块添加兼容别名 `old_name = new_name`")
            failed = True
        print("- 结论: " + ("破坏兼容，禁止提交" if failed else "✅ 导出面与契约名完全一致"))
        return 1 if failed else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
