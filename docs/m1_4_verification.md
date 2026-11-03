# M1-4 证据相关性预览执行与验证记录

日期：2026-10-31。仅完成 M1-4，未推进 M1-5 或其他后续项。

## 实施

- `harness/context_assembler.py` 复用 memory_manager.tokens，按完整证据 quote 与关联 reference.title 的目标 token 交集计分；不在截断后的文本上计算。
- 使用已有续接逻辑解析出的 selection_goal，避免把“继续”当成研究主题。
- 有命中时按分数降序、created_at 的 UTC 时间降序、追加位置降序选前 10。旧的无时区时间按 UTC 解释；无法解析的时间置末，并用追加位置稳定打破平局。
- 预览仍只展示前 80 字。有命中时新增 relevance_score，用于预算裁剪；无命中/空目标时保持最近 10 条的原顺序和原字段形状。
- `harness/context_budget.py` 对带有效分数的预览优先驱逐最低分项，同分先驱逐后排项；无分数或全零的旧预览保持从头驱逐。其他段的驱逐顺序和保护字段规则不变。
- 不修改存储模型，不写回 evidence.jsonl，不新增网络或模型调用。

## 验证

新增 `tests/test_evidence_relevance.py` 共 7 项：20 条证据中较早的 5 条主题证据进入前 10；标题/截断外关键词；续接请求；空目标和零命中；带时区同分排序与异常时间；相关性及旧式预览的预算驱逐。

- 首轮 RED：相关性、时间与预算断言失败；标题/续接测试夹具误用了 Reference，后改为 save_reference 接受的 ReferenceCandidate，并将修正测试运行于备份中的基线 assembler，确认仍因原有选择行为失败。
- 新测试：7 passed。
- 最终全量 `python -m pytest -q`：**371 passed, 1 skipped, 2 warnings，107.96 秒**。
- `paperagent demo --root .test-tmp/m1-4/demo`：8 次迭代 finished，kernel passed=True。
- `paperagent validate --root .test-tmp/m1-4/demo --project demo --draft draft.md`：通过，保留两条原有证据卡不足 warning。
- 与 M0-3 demo 比较，draft.md 和 draft.rendered.md 逐字节相同；不把包含临时绝对路径的控制台日志宣称为逐字节相同。
- 双包导入通过，路径指向 E:/PaperBug 当前源码。
- 独立只读审查未发现阻断或重要问题，无新增延期事项。

## 范围与记录

生产改动只有两个 harness 文件；增加测试、计划文件范围说明及本记录。备份、diff、测试日志位于 `.test-tmp/m1-4/`。首次含错误测试夹具的全量运行已停止，不作为验收证据；以 regression-final.log 为准。

当前目录仍无 Git 仓库，未提交或初始化。所有 demo 与测试使用临时工作区；没有修改历史用户证据，也没有实施 M1-5 的 IDF 或 M1-6 的分段预算。前端及打包配置未变，本次未重复浏览器或 wheel 验证。
