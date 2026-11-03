# PaperAgent 本地工作空间

在本目录创建虚拟环境后运行 `pip install .`，再执行 `paperagent-desktop`，访问 `http://127.0.0.1:8766`。此入口仅监听本机回环地址，界面资源随 Python 包安装。登录界面填写云端 FastAPI 地址：远程服务使用 HTTPS，本机开发可用 `http://127.0.0.1:8765`。

这是可单独安装的 Python 客户端；Windows 图形安装程序尚未制作。用户可预览 PDF、DOCX、TXT、Markdown 的有限文本；点击“确认发送并执行”后，才将预览内容发送到云端。操作细节、缓存位置与当前能力限制见 `docs/cloud_workspace_phase1.md`。
