# #18–#20 执行记录（2026-11-01）

计划：docs/fix_roadmap_v1.md。#18、#19、#20 已完成，最终全量回归见下文。

Ruling: 沿用已批准的修订计划，无 Git 时保留 `.test-tmp/remaining/backup` 与测试日志，不初始化仓库。
Ruling: 外环检查在保存、render、finish、CLI validate 接入；默认 offline（unchecked），通过 PAPERAGENT_REFERENCE_PROVIDER=crossref 显式开启外部查询，PAPERAGENT_REFERENCE_STRICT=1 开启明确 DOI 查无阻断。避免给既有离线保存行为新增隐式外传；代价是需要配置才能获得在线核验。
Ruling: Crossref 单库查无仅描述该库结果；严格策略的阻断也不是文献虚构判定。线上/印刷年份均可匹配；缺少作者或年份不能 verified。
Pre-flight: #19 消费 #18 的核验结果及严格策略；#20 仅接 Radar source fetch，保留后续匹配/去重。RSS 记录当前窗口，OpenAlex 按发表日期重叠扫描，不宣称更新时间全量同步。

执行清单：#18 complete；#19 complete；#20 complete；独立审查修复和最终完整回归 complete。

独立审查发现两项 important：畸形 Crossref JSON 逃逸、并发工作区突破限速；均复现 2 failed 后修复（21 passed 定向回归）。审查标记的测试隔离 minor 按本计划“无真实 HTTP”要求重新定为 important：在 crossref 环境下、fixture 设置阶段也禁止真实请求；已复现 8 个 setup error，再用 autouse 离线 fixture 修复。
Final: Ruling: 测试隔离项由 minor 升为 important — 用户配置在线时也必须遵守默认语料无网络契约 — 增加环境覆盖 fixture。
独立审查未评估的 live API 已由官方资料及 arXiv 单独在线探测补足；不宣称 OpenAlex/Crossref 实网端到端验证。跨进程并发、科学论断真伪及历史窗口外完整性按文档边界保留，不扩大本轮范围。

基线：429 passed、1 failed、1 skipped。失败是 test_server_v2 固定 2026-11-01 截止日期在当前时间已经到期，today 不再返回未来事项。改为 utcnow()+2 天，保持原测试含义；定向 suite 已通过。

#18 RED：6 failed（缺模块）；#20 RED：3 failed（缺源接口）。接入后 #18/#19/#20 与既有 Radar/server 定向回归 26 passed、1 network deselected。

Ruling: 复用已有 state_lock（进程内锁），不宣称支持多个独立进程并发编辑同一工作区；元数据结果刷新采用原子替换并保留未知字段。核验成功/不匹配缓存 30 天，暂时无法核验只缓存 60 秒。
Ruling: Radar 多源通过 PAPERAGENT_RADAR_SOURCES 配置，默认兼容 literature_search；每源/主题每 tick 一页，故障保留游标并隔离，完成扫描后重叠 7 天。代价：首次仅 30 天，晚于重叠窗口才收录的旧论文不保证发现。

官方接口核实：
- https://info.arxiv.org/help/rss.html ：HTTPS RSS 地址，按分类订阅，当前 feed 而非历史接口。
- https://help.openalex.org/api/paging/ ：每页最多 100，cursor 深分页需要 API key。
- https://help.openalex.org/api/filtering/ ：from_publication_date 为发表日期过滤。
- https://www.crossref.org/documentation/retrieve-metadata/rest-api/tips-for-using-the-crossref-rest-api/ ：按 DOI / 标题查询；未将 provider 查无当作虚构证据。

## 最终验证

- 完整回归：**451 passed、1 skipped、1 deselected、3 warnings，132.27 秒**，`.test-tmp/remaining/final-regression.log`。deselected 为默认排除的真实网络测试；skip 及警告延续既有条件/弃用提示。
- 一次独立审查，两项重要问题及重新定级的测试隔离问题均完成 RED→GREEN；无延期 minor。畸形 JSON 转 unchecked、每 host 限速锁覆盖跨工作区线程、语料 fixture 在保存前强制离线。
- 独立开启 arXiv 实际 RSS 探测：1 passed、3 deselected；未携带用户资料或凭据。OpenAlex/Crossref 查询行为使用确定性 fixture 验证，不宣称其生产端到端连通。
- V6 浏览器回归 `V6_BROWSER_OK`；沿用旧手稿 UI 脚本的历史边界，本轮不宣称所有历史 UI 脚本通过。
- 修复后两个 wheel 从干净 staging 构建、独立安装及导入通过，新模块 smoke `FINAL_WHEEL_SMOKE_OK`；安装版/源码 demo 均 finished after 8 iterations，源码 CLI validate passed=True。
- 额外在线环境隔离测试：预设 crossref 且禁止任何 HTTP，变异语料 8 passed。
- 代码与资料留在当前工作区，没有 Git 提交、推送或用户数据迁移；快照和日志保留 `.test-tmp/remaining/`。

配置说明见 `docs/reference_and_radar.md`。触发式 backlog 未启动；正文数字通用语义检测、跨进程并发编辑、RSS 历史补齐与超出 OpenAlex 日期窗口的迟到论文完整性不在完成声明内。
