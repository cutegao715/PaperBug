# PaperAgent 云端后台

在本目录创建虚拟环境后运行 `pip install .`，再执行 `paperagent-cloud`。默认监听 `127.0.0.1:8765`，也可以通过 `uvicorn paperagent_cloud.app:app` 配置进程管理与监听地址。模型环境变量参见 `.env.example`；程序从启动目录读取 `.env`。云端数据库默认为启动目录下 `cloud-data/cloud.sqlite3`，可用 `PAPERAGENT_CLOUD_DB` 指定已有数据库的绝对路径。

本服务目前是单实例开发原型，不适合直接开放互联网注册。生产部署还需要 HTTPS、用户身份防滥用、生产数据库、持久任务、备份与安全评估。
