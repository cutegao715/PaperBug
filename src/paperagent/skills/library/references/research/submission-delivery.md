# 投稿包制作与核对
submission_package 使用已有 Markdown 草稿 filename，可选 companions（如 Cover Letter、回复信）、figure_ids 和 requirement_sources。生成渲染后的 Markdown、DOCX、SVG、图注、内部 manifest 与 ZIP；不加入未声明的外部文件，不上传账号。
先解决引用完整性的确定性错误；警告与未执行检查要随交付说明。DOCX 使用现有导出器，尚未通过视觉版式或完整刊会合规验收，不称为可直接提交的认证文件。
内部 manifest 留在空间，不打入 ZIP；分发包仅含 package-manifest 文件清单，避免混入内部来源路径和备注。内部 manifest 复用既有来源快照，关联稿件、指南、图表与产物哈希。research_output_check 可在中断续接或稿件变化后检查当前性；修改后按需重新生成新包，不覆盖历史 ZIP。
包内论文正文与图表需由作者复核。没有自动打包来源文献全文；来源文件的使用与分发授权另行判断。
