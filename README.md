# AI 探究思维助手

面向初中学生问题提出能力培养的元认知提示 AI 原型系统。

## 安全提示

历史版本曾把模型服务密钥直接写在 `app.py` 中。部署前必须在相应服务商后台撤销旧密钥并创建新密钥；仅删除源码中的密钥不能使旧密钥失效。

正式实验涉及未成年人数据。部署者应完成伦理审批、知情同意、最小化采集、去标识化、访问控制、备份与保留期限设置，并使用 HTTPS。

## 安装

建议使用 Python 3.11 或 3.12：

```bash
python -m venv .venv
# Windows
.venv\Scripts\python -m pip install -r requirements.txt
# Linux/macOS
.venv/bin/python -m pip install -r requirements.txt
```

把 `.env.example` 中的变量配置到操作系统、进程管理器或容器环境。应用本身不会自动读取 `.env` 文件，避免开发配置被误当作生产配置。

生产环境必须设置：

- `APP_ENV=production`
- 至少 32 字符的随机 `FLASK_SECRET_KEY`
- 首次部署时至少 12 字符的 `INITIAL_ADMIN_PASSWORD`
- 批量导入账号使用的至少 10 字符 `DEFAULT_USER_PASSWORD`
- 新生成的 `ARK_API_KEY`
- 若启用向量记忆，则设置 `EMBEDDING_API_KEY`
- HTTPS 部署时设置 `SESSION_COOKIE_SECURE=1`
- 建议将 `APP_DATA_DIR` 指向源码目录外的受保护持久化目录

## 启动

本地 Windows 桌面模式：

```powershell
$env:APP_ENV = "development"
$env:FLASK_SECRET_KEY = "replace-with-a-random-secret-of-at-least-32-characters"
$env:ARK_API_KEY = "replace-with-a-new-key"
.venv\Scripts\python.exe app.py
```

Linux 服务器：

```bash
APP_ENV=production gunicorn -c gunicorn_conf.py app:app
```

不要使用 Flask 调试服务器承载正式实验。反向代理应启用 HTTPS，并关闭对 `/chat` 的响应缓冲。

## 数据与升级

- 用户、聊天和任务数据默认保存在 `instance/`。
- 旧版 `task_config.json` 只会在数据库仍为默认任务时迁移一次。
- 旧版明文密码会在用户成功登录时自动转换为安全哈希。
- 提交代码或发布安装包前，不要包含 `instance/`、日志、`.env` 或真实实验导出文件。

## 测试

```bash
python -m unittest discover -s tests -v
```

## 许可

本项目采用双重许可。使用者可以选择：

- GNU Affero General Public License v3.0 only；或
- Apache License 2.0。

详见 `LICENSE`、`LICENSES/AGPL-3.0-only.txt` 和
`LICENSES/Apache-2.0.txt`。
