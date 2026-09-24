#!/usr/bin/env python3
"""Step 1 诊断：列出 Python 项目中最长 / 分支最多的函数。只读，不修改任何文件。

有效行数 = 函数体内含代码 token 的行（不含空行、纯注释行、docstring）。
引用数 = 函数名在其他 .py 文件中以单词形式出现的次数（近似值，仅供排序参考）。

用法:
    python scan_long_functions.py [ROOT] [--top 15] [--max-lines 40] [--include-tests]
"""
import argparse
import ast
import io
import os
import re
import sys
import tokenize

EXCLUDE_DIRS = {
    ".git", ".hg", ".venv", "venv", "env", ".env", "node_modules", "__pycache__",
    "build", "dist", "site-packages", ".tox", ".nox", ".mypy_cache", ".pytest_cache",
    "migrations", "alembic", "vendor", "third_party",
}
SKIP_TOKENS = {
    tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
    tokenize.DEDENT, tokenize.ENCODING, tokenize.ENDMARKER,
}
BRANCH_NODES = tuple(
    getattr(ast, n) for n in
    ("If", "For", "AsyncFor", "While", "ExceptHandler", "IfExp", "BoolOp", "match_case")
    if hasattr(ast, n)
)
FUNC_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)


def iter_py_files(root, include_tests):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS and not d.startswith(".")]
        if not include_tests and re.search(r"(^|[\\/])tests?([\\/]|$)", os.path.relpath(dirpath, root)):
            continue
        for f in filenames:
            if not f.endswith(".py"):
                continue
            if not include_tests and (f.startswith("test_") or f.endswith("_test.py") or f == "conftest.py"):
                continue
            yield os.path.join(dirpath, f)


def code_lines(source):
    lines = set()
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type not in SKIP_TOKENS:
            lines.update(range(tok.start[0], tok.end[0] + 1))
    return lines


def docstring_lines(node):
    body = node.body
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
            and isinstance(body[0].value.value, str):
        return set(range(body[0].lineno, body[0].end_lineno + 1))
    return set()


def collect_functions(tree, prefix=""):
    """Yield (qualname, node) for every function, including methods and nested functions."""
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, FUNC_NODES):
            qual = f"{prefix}{node.name}"
            yield qual, node
            yield from collect_functions(node, qual + ".<locals>.")
        elif isinstance(node, ast.ClassDef):
            yield from collect_functions(node, f"{prefix}{node.name}.")


def analyze_file(path, root):
    try:
        with open(path, encoding="utf-8-sig") as fh:
            source = fh.read()
        tree = ast.parse(source)
        lines = code_lines(source)
    except (SyntaxError, UnicodeDecodeError, tokenize.TokenError) as exc:
        print(f"[skip] {path}: {exc.__class__.__name__}", file=sys.stderr)
        return []
    rel = os.path.relpath(path, root)
    results = []
    for qual, node in collect_functions(tree):
        # 从 def 行开始计（装饰器不计），函数签名计入
        span = set(range(node.lineno, node.end_lineno + 1))
        effective = len((span & lines) - docstring_lines(node))
        branches = sum(isinstance(n, BRANCH_NODES) for n in ast.walk(node))
        results.append({"file": rel, "name": node.name, "qual": qual, "line": node.lineno,
                        "lines": effective, "branches": branches})
    return results


def count_references(root, candidates, include_tests):
    texts = {}
    for path in iter_py_files(root, include_tests=True):
        try:
            with open(path, encoding="utf-8-sig") as fh:
                texts[os.path.relpath(path, root)] = fh.read()
        except UnicodeDecodeError:
            continue
    for c in candidates:
        pattern = re.compile(rf"\b{re.escape(c['name'])}\b")
        c["refs"] = sum(len(pattern.findall(t)) for f, t in texts.items() if f != c["file"])


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台默认 cp1252/gbk
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--max-lines", type=int, default=40, help="超标阈值（默认 40，以项目规则为准）")
    ap.add_argument("--include-tests", action="store_true")
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    funcs = [r for p in iter_py_files(root, args.include_tests) for r in analyze_file(p, root)]
    over = sorted((f for f in funcs if f["lines"] > args.max_lines),
                  key=lambda f: (f["lines"], f["branches"]), reverse=True)
    top = over[: args.top]
    count_references(root, top, args.include_tests)

    print(f"扫描 {len(funcs)} 个函数，超过 {args.max_lines} 行的有 {len(over)} 个（显示前 {len(top)} 个）\n")
    print("| 排名 | 文件:行 | 函数 | 有效行数 | 分支数 | 外部引用(近似) |")
    print("|---|---|---|---|---|---|")
    for i, f in enumerate(top, 1):
        print(f"| {i} | {f['file']}:{f['line']} | `{f['qual']}` | {f['lines']} | {f['branches']} | {f['refs']} |")


if __name__ == "__main__":
    main()
