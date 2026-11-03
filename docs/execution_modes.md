# 本地执行与云端执行边界

本地会话以项目目录中的 conversation checkpoint、事件日志、稿件和引用库为真相源。重启和“继续”只读取本地状态，不依赖云端连接或 agent_context。生命周期同步不会将云任务转换成本地会话，也不会恢复或推进模型任务。

云端任务由 agent_tasks、agent_context、agent_operations 及其结果共同构成。advance 必须验证登录用户所属空间、活跃设备、file_ref、sha256、workflow_id、skill_id 与剩余步骤；等待本地操作时仍执行这些校验。agent_context 保存研究上下文和步骤计数，是执行依赖，不只是展示记录。设备 claim/result 的凭据、租约和材料摘要校验保持不变。

绑定与同步只复制生命周期状态和变更原因。默认 local_only；用户主动调用云对话、云 Agent、LLM 或文献查询是独立操作，不受后台同步开关禁止。未来统一迁移执行模式需要单独设计，包含在途任务、claim/result 协议及前端兼容。
