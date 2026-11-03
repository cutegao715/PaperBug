# 演示产物与版本
presentation_build 接受 title 和 slides；每页有 title、points、notes、sources，可选 figure_id，生成离线 presentation.html、可编辑 slides.json 和 manifest.json。sources 使用既有 ProjectSource：draft、material、evidence、user_note；来源关联不等于命题核验。
HTML 可用方向键阅读、展开讲稿并打印。当前工具不生成 PPTX，也不自动运行排版渲染。结构检查不等于文字无溢出或答辩时间合格，最终交付需按实际载体查看。
research_output_check 比较当前稿件、材料、证据、图表和产物文件；依据变化时标记 needs_review，不自动修改现实阶段或旧产物。已有版本保持可下载，每次生成创建独立产物目录。
根据本次请求执行必要的修订；用户仅需局部讲稿时直接写草稿，无需生成整套演示或启用 Workflow。
