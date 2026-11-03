# 引文外部核验与 Radar 数据源

默认保持离线核验：保存、渲染、finish、CLI validate 会生成 `unchecked`，不会新增外部请求。

在启动应用前设置 `PAPERAGENT_REFERENCE_PROVIDER=crossref` 可开启 Crossref 查询。会发送 DOI 或文献标题，不上传稿件、证据全文；每秒最多一次（单进程），请求超时、限流和无法解析均标记 unchecked。设置 `PAPERAGENT_REFERENCE_STRICT=1` 后，仅被正文引用且 DOI 在 Crossref 明确返回 404 时阻断完成/渲染；这表示当前策略要求未满足，不证明文献虚构。其他注册机构的 DOI 也可能未被 Crossref 收录。

比对归一化标题词集合、第一作者姓氏和出版年份。标题 Jaccard 至少 0.85，线上/印刷年份任一匹配均可；作者或年份缺失时不宣布 verified。名称格式差异仍可能产生 warning，结果不证明原文支持论断。项目 `.verification_cache.json` 按 DOI、标题、作者、年份、provider、策略版本缓存 30 天；临时 unchecked 为 60 秒。原始元数据不被 provider 内容覆盖。

Radar 默认仍使用 literature_search。可设置：

```text
PAPERAGENT_RADAR_SOURCES=literature_search,arxiv_rss,openalex_incremental
PAPERAGENT_OPENALEX_API_KEY=<可选 OpenAlex API key>
```

OpenAlex 初次查询最近 30 天，之后每 tick 每主题一页，持久保存 cursor，完成一轮后扫描最近 7 天以覆盖常见迟到记录。深分页需要 API key；缺失时服务错误被隔离，游标保留，不把失败当作空结果。按发表日期过滤不等于更新日志，超过窗口的迟到记录可能遗漏。API key 不写入游标和错误记录。

arXiv RSS 映射常见主题（机器学习、LLM、人工智能、量子、统计）及显式分类（如 cs.LG），没有分类映射则跳过该源。它只能覆盖当前 feed，无法补齐断线期间的历史。覆盖说明与故障类型保存在根目录 `domain/radar_sources.json`。合并后复用既有去重与匹配，候选仍需作者审阅。offline_stub 不会被当作真实论文保存。

默认 pytest 排除 network 标记。显式在线探测：`python -m pytest tests/test_radar_sources.py -m network -o addopts=''`；该命令访问公共服务，可能受当前服务状态影响。常规测试使用固定响应，不消耗真实 API 配额。

变异语料位于 tests/mutations，验证 DOI、年份、PDF quote 和缺失证据卡。正文数字没有通用语义映射，目前列为能力缺口，未声称可自动检测。
