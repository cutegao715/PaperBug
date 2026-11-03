# M0-1 执行与验证记录

完成记录日期：2026-10-31。范围仅 M0-1；未推进 M0-2 或后续项。

## 改动

- backend/pyproject.toml 声明 paperagent==0.1.0。
- 云端 app 使用 paperagent.harness.llm.LLMClient；research/original_agent 适配器使用 paperagent.agent.llm_agent.LLMAgent。
- 删除 original/ 下 12 个源文件及重复 llm.py。逐文件归一化 import 比较后，除包说明外无行为差异，因此无需修改共享研究实现。
- 保留 paperagent.cloud.app 旧入口及模块身份；移除 sys.path 注入。缺少云包时提示安装，其余 ModuleNotFoundError 原样抛出。
- 新增 tests/test_shared_kernel.py，覆盖共享类身份、旧入口身份与路径不变、缺少云包时本地核心仍可导入。

## 验证证据

日志与变更前备份位于 `.test-tmp/m0-1/`。

| 检查 | 结果 |
|---|---|
| 修改前全量 pytest | 353 passed, 1 skipped, 6 warnings |
| 新增测试修改前 RED | 2 failed, 1 passed；共享实现身份及安装提示未满足 |
| 共享内核、云端任务、兼容入口、独立 HTTP 服务等定向测试 | 20 passed |
| 修改后全量 pytest | 356 passed, 1 skipped, 6 warnings，111.17 秒 |
| editable 环境双包导入 | 指向本工作目录 src 与 backend/src |
| 离线 demo | 8 次迭代后 finished，kernel passed=True |
| demo 草稿 validate | passed=True；保留两条既有缺少证据卡 warning |
| 两个 wheel 干净构建 | 成功；包内无 original/ 或 paperagent_cloud/llm.py |
| 全新 venv 安装 cloud wheel 及依赖 | 自动解析本地核心 wheel，成功 |
| wheel 环境 python -I 导入及共享类/旧入口身份 | 通过，路径均为独立 venv/site-packages |
| wheel 环境 pip check | No broken requirements found |
| wheel 环境离线 demo | finished，kernel passed=True |
| 生产源码旧导入及 sys.path.insert 搜索 | 零命中 |
| 变更前后源码哈希比较 | 仅四个既有文件改变、13 个副本文件删除；其他既有 Python 代码及测试不变 |

独立只读审查未发现 M0-1 阻断问题。

## 未通过的补充验证及边界

`tests/verify_manuscript_ui.py` 在等待 `#newPit` 时超时。脚本访问 `/`，当前服务返回 `web/index.html`；该选择器只存在于 `web/classic.html`。相关页面、server 和脚本的哈希与修改前一致。这项补充 UI 验证不算通过，本次未修复或宣称完成全功能手工点击验收。

既有 pytest 弃用警告保持不变，跳过项仍为原有一项。

## 安装、构建与执行决策

开发安装：`python -m pip install -e . -e ./backend`。本次在 `.test-tmp/m0-1/dev-env` 安装，未更改系统现有 editable 安装（该安装原指向 E:/PaperAgent）。验证环境复用系统测试依赖；wheel-env 则为全新、无 system-site-packages 的环境，验证正式依赖安装。

工作目录原有 `build/` 与 `backend/build/` 是旧生成物。发行时必须在干净目录构建，不能复用旧 build/lib。此次只复制 pyproject.toml 与 src（排除 __pycache__、egg-info）到 `.test-tmp/m0-1/wheel-source/` 后构建，且检查了 wheel 内容。未修改归档构建产物。

当前 E:/PaperBug 不是 Git 仓库，无法建立 worktree 或提交；未擅自初始化仓库。变更前副本和哈希保存在 `.test-tmp/m0-1/backup/` 与 `before.json`，以便审查和恢复。此决策的限制是没有 Git commit 可用于追踪或回滚，后续应在实际版本库中纳入本次变更。

一次混合修改与递归删除命令被工具策略拒绝，未执行。随后将源码删除改为逐文件补丁，并在核实绝对路径后清理 original 目录内残留缓存，均成功；不需要额外授权。

所有 demo 使用独立临时工作区，没有迁移历史用户数据。M0-2 的 skill loader、catalog 和 library 保持原状。
