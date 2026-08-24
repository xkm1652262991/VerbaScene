# 本地内容目录

默认 `PERSISTENCE_MODE=local` 时，后端会在这里创建 `content.sqlite3`，保存项目、章节、剧本、实体、分镜、任务、候选、资产元数据、质检和导出记录。

图片、视频、音频和导出文件仍保存在 `storage/projects/`、`storage/assets/` 和 `storage/exports/`。任务审计记录只保留引用与追溯元数据，内联 base64 二进制会被摘要替代，避免同一媒体在本地文件中重复存储。

- `content.sqlite3` 是运行数据，已被 `.gitignore` 忽略。
- 新环境首次启动 API 会自动创建空文件和当前表结构。
- 从已配置 PostgreSQL 复制内容：`cd apps/api && .venv/bin/python scripts/copy_database_to_local.py`。
- 备份本地项目时，应整体备份 `storage/`，不要只复制这个 SQLite 文件。
- 本地模式适合单机、低并发联调；共享和并发部署应切换 PostgreSQL。

模型管理页直接输入的 Provider 密钥不保存在本目录，也不会随普通 `storage/` 备份复制。它默认保存在 `apps/api/.runtime/provider-secrets.json`，文件权限为 `0600`；迁移机器时应通过受控方式单独迁移或重新录入，不能把它作为项目资产分发。
