# M0-2 执行与验证记录

日期：2026-10-31。范围仅内置技能库归一，不推进 M0-3。

## 实施

- 逐文件核对云端 82 个技能/资源文件与本地完全一致，删除云端副本；共享库原有文件的 SHA-256 全部保持不变。
- 云端 loader/catalog/design 保留旧模块入口，以模块转发方式复用 paperagent.skills.loader 和 paperagent.workflows 的实现。catalog 仅原 import 不同，design 与 loader 无功能差异。
- 将三处相同的路由 catalog 归一到 src/paperagent/skills/skill_catalog.yaml；本地 workflow 通过 importlib.resources 读取。
- 更新两包 package-data 与技能安装脚本目的地，避免以后生成到旧位置。
- 运行时统一读取共享 legacy_aliases.json；云端同名 JSON 为直接文件读取者保留至 0.1.x 兼容窗口结束，退役须配合后续版本说明，当前不删除。测试保证其内容与共享映射一致。
- capabilities 保留云端编排与资源来源隐藏逻辑；共享和项目私有技能覆盖规则不变。

## 验证

新增 tests/test_shared_skills.py：生产源按技能 id 去重、旧模块身份、共享 catalog 与旧 JSON、全部资源/旧键、技能市场 API 与用户覆盖。

修改前新增测试：3 failed, 2 passed（重复物理位置、独立模块实现、共享 catalog 尚不存在）。
修改后定向测试：47 passed。

干净源码 staging 构建两个 wheel，并在上一项建立的独立 wheel-env 中强制替换安装（该环境无 system-site-packages）。使用 python -I 验证：35 个技能、39 处资源引用、全部旧键、共享模块身份通过；旧云 library 和旧 catalog 不在 wheel 内；pip check 通过。包内仅共享 skills/skill_catalog.yaml 一份路由资源。

离线 demo：8 次迭代后 finished，kernel passed=True。validate 通过，保留两个既有弱证据 warning。双包从当前源码目录导入通过。

全量 `python -m pytest -q`：**361 passed, 1 skipped, 6 warnings，153.54 秒**。相对 M0-1 新增 5 项通过测试，既有跳过项与警告数量不变。

技能市场通过 /api/skills 的实际 TestClient 响应验证 ID 集合与 35 个云端能力一致；未将其表述为已完成浏览器全功能手工点击。上一项发现的旧手稿 UI 脚本选择器问题未改动。

## 执行边界

没有修改用户 workspace、归档来源库或旧 build 生成目录。备份、哈希、RED/定向/全量日志与 wheel 位于 .test-tmp/m0-2/。干净构建继续避免旧 build/lib 混入发行物。

E:/PaperBug 仍非 Git 仓库，因此未建立 worktree 或提交；沿用 M0-1 的备份与独立测试环境。本次范围扩展到安装脚本和 workflows/catalog.py，是为了消除目录迁移后的失效读写点，已事先补入计划。


## 独立审查

只读审查未发现阻断或重要问题。保留既有问题：技能安装脚本失败回滚只恢复 library，不恢复别名/catalog 元数据。该行为在基线已存在，本项仅修正目的地，未扩展修复；风险是未来执行安装时若中途失败可能留下不同步的元数据。当前共享库迁移和验证没有运行该安装脚本，也未触发此风险。
