# M0-3 API 分层执行与验证记录

日期：2026-10-31。范围仅 M0-3，未启动 M1。

## 结果与结构

- 本地与云端均同时挂载 `/api/v1` 和 `/api`。旧接口继续直接处理请求，无重定向；ASGI 中间件给旧路径加 Deprecation 头，不缓存 SSE body。
- 本地 `server.py` 负责 app 组装。`api/v1/` 按 project、lifecycle、manuscript、review、research、connections、conversation、runtime 提供 APIRouter；已有闭包路由搬入对应领域模块。原服务的共享运行状态和业务处理保持在 `api/control.py`，未重写业务逻辑。
- 原 `*_api.register_*_routes(app, ...)` 保留为兼容工厂，并返回 router。server 的旧对象读取通过 `__getattr__` 保留；内部测试需要替换运行实现时，明确 patch `api.control`。不承诺通过对 server 属性重新赋值修改 control。
- `/projects/{project_id}/context` 是上下文预算诊断，放入 `/internal`，保留 `/api` 旧别名；不提供稳定 v1 别名。配置、工具发现、任务、SSE 都是已有产品调用，继续作为稳定接口。
- 云端全部用户接口继续保留原鉴权、设备/材料校验和 CORS；没有新增免鉴权诊断。
- `web/`（含 classic、V6）与 desktop 云 API 客户端更新前缀。桌面 `/local/*` 属于独立本机授权协议，保持不变。两个返回报告链接的生成点也改为 v1，字段不变。
- 原重复 `/api/tools` 两个处理器返回完全相同数据，领域路由只注册原先实际命中的第一个，避免重复声明。

## 验证过程

备份、原 OpenAPI、日志与 wheel 位于 `.test-tmp/m0-3/`。

1. 新版本路径测试修改前 2 项失败，修改后通过；加入旧/新 SSE 内容、结束标记及弃用头断言。
2. 对照原 OpenAPI：本地 91 个 API 路径、云端 15 个 API 路径迁移后请求/响应定义与全部 components 一致；比较排除 operationId、summary、deprecated 等文档元数据。
3. 原有功能测试迁到 v1（诊断迁到 internal），独立新增测试保留旧路径兼容覆盖。完整测试结果见末尾。
4. 审查发现两个动态报告链接漏迁移：先新增断言并看到 2 项失败，再修复；版本/SSE/链接相关 5 项通过。
5. 原 LLM provider 地址不属于本项目 API 迁移，保持原测试文件；额外运行 10 项 LLM 测试通过。
6. V6 浏览器验证通过：真实创建项目、上传、编辑/重载、对话运行、资料证据页、嵌入高级页、生命周期阶段保存、35 个技能入口和方法详情、桌面/移动端布局及图片。收集 API HTTP 错误，技能/生命周期链路检查为空。
7. 干净源码目录构建两个 wheel，在无 system-site-packages 的 wheel-env 安装；`python -I` 验证本地/云端 v1 与 internal OpenAPI 可生成。
8. 离线 demo 8 次迭代 finished，kernel passed=True；validate passed=True（保留原有两条证据卡不足 warning）；双包导入指向当前源码。

## 补充验证限制

旧 `verify_manuscript_ui.py` 原访问新版首页却寻找 classic 的 #newPit。临时诊断转到 classic 后，又发现旧 publication_management ID、旧能力说明和旧技能名称等断言已经不匹配当前目录。没有为使测试通过改变产品或删减断言，最终只保留其 API 路径迁移，其余恢复原脚本。该旧脚本不算通过；本次浏览器验收以增强后的 `verify_v6_browser.py` 为依据。

## 审查与执行决策

独立只读审查核对了原 server/control 的函数 AST、资源路径、路由顺序、SSE、CORS 和客户端调用。报告两处动态链接问题，已以失败到通过的测试修复；其余未发现重要问题。

保持控制服务业务实现集中在 control，以限制纯路径迁移的行为风险；路由所有权按领域拆分。后续若需要进一步拆业务服务，应单独规划，不在 M0-3 顺手重写。

E:/PaperBug 非 Git 仓库，未初始化、未建立 worktree、未提交；备份与 manifest 可用于审查/恢复。测试运行于临时工作区，没有迁移历史用户数据。发行使用干净 staging，未复用旧 build/lib。


## 最终回归结果

`python -m pytest -q`：**364 passed, 1 skipped, 2 warnings，212.37 秒**。新增版本兼容测试 3 项；旧测试已覆盖新 API 路径。两项既有 FastAPI 云端启动弃用警告未改变实现。本地原 on_event 注册改为在 app 组装时注册，故相关旧警告不再产生。额外原样 LLM 测试 10 passed。
