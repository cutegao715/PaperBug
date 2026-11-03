# M1-5 至 M1-11 执行记录

范围：只执行本轮七项，不推进 M2。工作区无 Git，修改前快照在 `.test-tmp/m1-5-11/backup/`，指纹在 `before.json`；保留这些文件用于比较和恢复。

- M1-5：仅在同层、同作用域、未过期、活跃候选中计算 IDF；保留 pinned、importance、时间与各层数量限制。稀有词排序测试先失败后通过。阶段完整回归 372 passed, 1 skipped。
- M1-6：按段分配可裁剪预算；当前请求、决策、最近消息等最低保留状态先计入保护额度；分段诊断提供截断量与按需读取提示；保护状态过大时报错。定向回归 40 passed。
- M1-7：旧数据 trust 默认 model_transient；采纳记录关联 user_adopted 记忆，修订有替换链、撤回失效；模型不可覆盖已采纳记忆，预算不可静默移除。新增三项测试先失败后通过。
- M1-8：配置按环境变量 > 外置 JSON > 包内 JSON > 默认值合并；拒绝未知项与非法预算比例，保留显式构造参数优先。工具禁用同时阻止发现和直接执行。两项配置测试先失败后通过。
- M1-9：fast 独立端点、模型、协议及密钥；全未配置回退 primary，半配置报错。实际请求使用 fake HTTP 验证无主密钥泄漏；目前不存在 LLM 摘要路径，规则 Radar 保持原行为。
- M1-10：按工作区缓存 stdio 会话，单协程拥有连接；发现分页、白名单权限、参数校验、失败隔离、超时终止和配置撤销；服务退出及 CLI 退出关闭。真实 fake MCP 子进程测试 4 passed。
- M1-11：keyring 优先、环境回退，固定的应用键名目录（LLM_API_KEY/FAST_LLM_API_KEY）；隐藏输入的 secrets set，list 不输出值；明文 .env 只提示迁移。隔离 fake keyring 验证保存后无需环境密钥即可启用服务，不修改用户真实凭据。

Ruling: 当前目录无 Git，保留快照与验证日志而不执行逐项 commit — 避免创建用户未请求的仓库 — 无 Git 回滚历史，需要用快照恢复。
Ruling: SDK 使用 >=2.1,<3，工具 ID 对 server/name 分别 hex 编码 — v2 当前 API 可实测且避免命名碰撞 — 自定义 MCP 工具 ID 较长，第三方 v1-only 适配需要升级。
Ruling: 凭据目录仅支持当前两个 LLM 键名 — 不依赖后端枚举，不引入密钥目录文件 — 新连接器凭据须将名称加入目录。

官方 API 核对：[MCP SDK](https://github.com/modelcontextprotocol/python-sdk)、[v2 client](https://py.sdk.modelcontextprotocol.io/client/)、[迁移说明](https://py.sdk.modelcontextprotocol.io/migration/)、[keyring](https://keyring.readthedocs.io/en/latest/)。实际开发环境 MCP 2.1.1；Python >=3.10。

## 最终独立审查与修复

独立 reviewer 发现四项问题，均复现后修复：

1. 旧版本采纳覆盖与撤回后复活：所有来源记录保留最高同步版本，撤回写版本墓碑，拒绝迟到版本。
2. MCP 超时后旧会话仍留在缓存：检查会话状态并在下次发现时有界重连；close 同时失效配置指纹。
3. MCP 非对象配置/读取错误：隔离 TypeError、OSError、UnicodeError，返回空外部工具集。
4. 过大 last_result 挤占其他段：先生成延迟读取引用，再计算分段可用预算。

`review-red.log`：6 failed, 9 passed；修复后 `review-green.log`：38 passed。旧技能测试显式设置宽预算，保留其全部技能/作用域/续接断言；独立预算测试覆盖默认容量与截断行为。

Ruling: 分段比例作用于扣除保护状态与诊断后的可分配预算 — 现有工具目录及协议已超过总预算的 5%，不能按计划示例只预留 5% — 默认观察中的技能列表会更短，完整目录仍可通过 skill_catalog 按需读取。

## 使用配置

非敏感设置可通过 `PAPERAGENT_PRODUCT_CONFIG` 指向 JSON；未指定则使用包内 `product.json`。主模型原有 `PAPERAGENT_LLM_*` 环境变量继续优先；fast 对应 `PAPERAGENT_FAST_LLM_BASE_URL/MODEL/PROTOCOL`。凭据分别使用 `paperagent secrets set LLM_API_KEY` 和 `paperagent secrets set FAST_LLM_API_KEY`。`secrets list` 只列已配置键名，不输出值。

MCP 为可选依赖，安装 `paperagent[mcp]` 后在 `<workspace_root>/.paperagent/mcp.json` 配置：

```json
{"servers":{"example":{"command":"your-mcp-server","args":[],"env":{},"allow_tools":{"search":"network_read"},"timeout_s":30}}}
```

仅 `allow_tools` 中明确配置权限的工具进入目录；工具权限通过现有 PermissionGate 再次检查。远端声明不授予权限。不配置 MCP 时不启动子进程。更改配置会关闭旧连接并重新发现；失败启动缓存在当前配置下，不反复拉起；修改配置可重试，已建立会话的调用超时后下一次发现可恢复。

## 最终验证

- 开发环境双包导入路径指向本工作区。
- 离线 demo 通过，日志 `.test-tmp/m1-5-11/demo.log`。
- wheel 构建、独立虚拟环境安装通过；包内 product.json 存在，未安装 MCP extras 的服务导入与工具目录正常，双包均来自 site-packages。
- 独立 wheel 的离线 demo 通过，日志 `wheel-demo.log`。
- 未测试真实外部 MCP 服务或真实模型请求；MCP 使用真实 SDK + 本机 fake 子进程，LLM 使用 fake HTTP，凭据使用隔离 fake keyring。未自动迁移真实 .env。
- 最后完整回归：**398 passed, 1 skipped, 3 warnings，154.36s**；日志 `.test-tmp/m1-5-11/final-regression.log`。1 个跳过项为原有条件跳过；3 个警告为明文 .env 迁移提示和两个既有 FastAPI 生命周期弃用提示。
- 最终状态：M1-5、M1-6、M1-7、M1-8、M1-9、M1-10、M1-11 完成；没有推进 M2，没有提交、推送或更改真实凭据。
- Final: fixed 四项独立审查问题 — 对应复现测试 RED→GREEN，完整回归 398 passed, 1 skipped。无延期 minor。

