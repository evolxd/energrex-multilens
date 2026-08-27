# 归档说明

这批脚本挪进来的时间：2026-08-27。挪之前逐个 grep 过全仓库（`*.py` / `*.ps1` /
`*.bat` / `*.json`），确认没有任何存活代码 import 它们，也没有被
`.vscode/tasks.json`、Windows 计划任务、`.mcp.json` 引用——纯粹是历史调试/
一次性导入脚本，留在根目录会让人以为它们还在跑。

不是删除，是挪位置。git 历史完整保留（用的是 `git mv`），想找回原样直接
`git log --follow` 或者把文件挪回根目录即可。

同一批里 `_run_sync.py` / `_run_price_refresh.py` **没有**进这里——虽然名字长得像，
但它们被 `_sidebar.py` 用 `subprocess.Popen` 实际调用（"同步账户"/"更新行情"按钮），
而且 `tests/test_source_encoding_hygiene.py` 硬编码了它们在根目录的路径，挪了会
直接让测试报错。

如果以后要用这里的某个脚本，大概率不如照着当时的思路重写一份更快——数据结构和
`account/`、`scoring/` 的接口都已经往前走了不少。
