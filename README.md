# Heartbeat Monitor（短信网关心跳监控）

被动接收短信网关心跳、超时自动发钉钉告警的轻量监控系统。FastAPI + APScheduler + SQLite，单进程部署，适用于 Linux 服务器（systemd）。

> **⚠️ 上传 GitHub 前必读：请创建「私有（Private）」仓库。**
> `seed/hb.db` 里含有钉钉机器人 webhook 地址（等同于密钥），`.env` 里含后台密码（该文件已加入 `.gitignore`，不会上传）。
> 如果把仓库设为公开，webhook 会被泄露。若必须公开，请先替换 `seed/hb.db`（见 FAQ）。

---

## 一、系统梳理

### 1. 它做什么

```
短信网关/设备                         服务器（单个 uvicorn 进程）
    │  POST /api/heartbeat             ┌──────────────────────────────┐
    │  {"node_key":"xxx"}   ────────▶ │ FastAPI 心跳接收 API          │
    │                                  │   └─ 更新 last_seen + 写历史  │
    │                                  │                              │
    │                                  │ 管理 Web UI（登录/节点/历史） │
    │                                  │                              │
    │                                  │ APScheduler（进程内）         │
    │                                  │   ├─ 每 60s 扫描超时节点      │
    │                                  │   └─ 每天 03:00 清理历史      │
    │                                  └──────────┬───────────────────┘
    │                                             │
    │                         超时且过冷却期 ─────▶│ 钉钉群机器人告警
    ▼                                             ▼
（网关只管定时上报）                    SQLite: hb.db（monitor_nodes / heartbeat_events）
```

- **心跳接收**：`POST /api/heartbeat`，支持 JSON / form-urlencoded / 双重编码 JSON（兼容老设备）。要求 `node_key` 已在 Web UI 建好且启用；每次上报更新 `last_seen_at` 并写一条历史。
- **超时告警**：后台每 60s 扫描启用节点，`now(UTC) - last_seen_at > interval_min` 即判超时；检查 `alert_cooldown_min`（默认 30 分钟）冷却后向该节点独立的钉钉 webhook 发关键词文本消息。没有重试/升级机制，靠冷却期反复提醒。
- **防误报**：服务每次启动会把所有启用节点的 `last_seen_at` 刷新为当前时间并清空 `last_alert_at`，避免重启就告警。
- **管理 UI**：`/login` 登录后，节点增删改、启停、历史查看、测试钉钉；`/health` 为健康检查（无需登录）。
- **时间**：数据库统一存 naive UTC，页面展示 +8 小时。**与服务器时区设置无关**，不用调时区。

### 2. 进程模型（重要）

**只能起 1 个 uvicorn worker。** APScheduler 跑在 Web 进程内，多 worker 会导致每个进程各扫一遍 → 重复钉钉告警 + SQLite 写锁竞争。`install.sh` 生成的 systemd 单元已固定单进程，不要加 `--workers`。

### 3. 配置体系

| 层级 | 位置 | 说明 |
|---|---|---|
| 全局配置 | 仓库根 `.env`（由 `.env.example` 首次生成，已 gitignore） | 端口、后台密码、扫描间隔、默认值等 |
| 节点配置 | `hb.db` 的 `monitor_nodes` 表，通过 Web UI 维护 | node_key、钉钉 webhook/关键词、心跳间隔、冷却时间、是否启用 |

所有路径（模板目录、静态文件、SQLite 库、`.env`）都锚定到仓库根目录，**不依赖启动时的当前目录**，systemd 下不会跑偏。

### 4. 目录结构

```
heartbeat_monitor/
├── app/                     业务代码
│   ├── main.py              入口：全部路由 + 建表 + 调度器注册
│   ├── config.py            配置中心（APP_ROOT 路径锚定）
│   ├── tasks.py             超时扫描/告警、历史清理、启动防误报
│   ├── models.py            ORM：MonitorNode / HeartbeatEvent
│   ├── db.py                SQLAlchemy engine/session
│   └── dingtalk.py          钉钉 webhook 发送
├── templates/               Jinja2 页面（base/login/nodes/node_form/history）
├── static/css/              Bootstrap 5.3.3（已本地化，服务器无需访问 CDN）
├── seed/hb.db               初始数据库（现有 1 个节点 + 5 条历史）
├── deploy/
│   ├── install.sh           ★ 一键部署（生成 systemd 单元、装依赖、健康检查）
│   ├── manage.sh            运维：status/start/stop/restart/logs/follow/health
│   └── uninstall.sh         卸载服务
├── .env.example             配置模板
├── requirements.txt         依赖（已锁版本）
├── command.txt / start.sh / stop.sh / heartbeat-monitor.service   旧版部署备忘（已废弃，勿使用）
└── README.md                本文件
```

---

## 二、上传到 GitHub

本仓库已完成 `git init` 与首次提交（`.env`、运行库 `hb.db`、`venv/` 等已被 `.gitignore` 排除）。你只需：

```bash
cd heartbeat_monitor

# 1. 在 GitHub 网页新建仓库，可见性务必选 Private
# 2. 绑定远程并推送
git remote add origin https://github.com/<你的账号>/heartbeat_monitor.git
git branch -M main
git push -u origin main
```

推送前自查：

```bash
git status                    # 应无 .env / hb.db / venv / logs
git ls-files | grep -E '\.env$|^hb\.db$'   # 应无输出（seed/hb.db 是特意入库的）
```

**另一种方式**：不想用 git 命令行时，可用本目录附带的 `heartbeat_monitor_github_*.zip`，在 GitHub 网页「Add file → Upload files」直接上传（同样先建私有仓库）。zip 内容与已提交的文件完全一致。

> 国内服务器拉 GitHub 慢时，可改用 Gitee / 阿里云 Codeup 私有仓库，部署命令不变，只换 clone 地址。

---

## 三、一键部署（Linux）

### 前置条件

- Linux 服务器（Ubuntu / Debian / CentOS / Rocky / 阿里云 Linux 均可），**systemd**（`systemctl` 可用）
- Python 3.8+（推荐 3.9–3.12；3.13+ 会自动改用不锁版本依赖）
- 能访问 pip 镜像源（脚本默认阿里云源，失败自动降级清华源 / 官方源）
- root / sudo 权限
- **在云厂商控制台放行 TCP 8000（阿里云：安全组 → 入方向规则）。这一步脚本无法代劳，不做的话外网访问不到。**

### 部署

```bash
# 1. 拉代码（示例路径 /opt，可换成任意无空格路径）
cd /opt
git clone https://github.com/<你的账号>/heartbeat_monitor.git
cd heartbeat_monitor

# 2. 一键安装（会装依赖、建 venv、生成 .env、初始化数据库、注册 systemd 服务、放行本机防火墙、做健康检查）
sudo bash deploy/install.sh

# 可选参数
sudo bash deploy/install.sh -p 8080                  # 改端口
sudo bash deploy/install.sh --admin-password 'xxx'  # 首次生成 .env 时指定后台密码
sudo bash deploy/install.sh -u heartbeat           # 见 FAQ：指定非 root 运行用户
```

脚本**幂等**：重复执行即为升级流程，不会覆盖已有 `.env` 和 `hb.db`。

### 验证

```bash
sudo bash deploy/manage.sh status          # 服务状态 + 健康检查
curl http://127.0.0.1:8000/health         # {"ok":true}
```

浏览器打开 `http://<服务器公网IP>:8000` → 用 `.env` 里的 `ADMIN_PASSWORD` 登录 → 节点列表应能看到已有节点（页面样式来自本地 Bootstrap，服务器无外网也正常）。

心跳上报示例：

```bash
curl -X POST http://127.0.0.1:8000/api/heartbeat \
  -H 'Content-Type: application/json' \
  -d '{"node_key":"MFG5-4K"}'
```

> `seed/hb.db` 里现有节点 `MFG5-4K` 默认是**停用**状态；确认钉钉 webhook 正确后，在 Web UI 点「启用」才会开始监控。

### 部署后必做

1. `sudo vi /opt/heartbeat_monitor/.env` → 改掉 `ADMIN_PASSWORD` 和 `SESSION_SECRET`（首次安装已随机生成 SESSION_SECRET，但密码默认是 `change_me`）
2. `sudo bash deploy/manage.sh restart`
3. 在 Web UI 里核对/修改每个节点的钉钉 webhook 与关键词，点「测试钉钉」确认能收到

---

## 四、配置说明（`.env`）

| 键 | 默认值 | 说明 |
|---|---|---|
| `PORT` | `8000` | 同时用于 systemd 启动命令与健康检查；改这里后重跑 `install.sh` |
| `DB_URL` | `sqlite:///./hb.db` | 相对路径自动锚定到仓库根；也可写绝对路径 |
| `ADMIN_PASSWORD` | `change_me` | **必改**，Web UI 登录密码 |
| `SESSION_SECRET` | 首次安装随机生成 | 会话签名密钥，改动只会让已登录会话失效 |
| `HOST` | `0.0.0.0` | 仅记录用，实际监听由 systemd 命令行决定 |
| `HEARTBEAT_TOKEN` | 空 | 预留；**当前代码未强制校验心跳 token**，见「已知限制」 |
| `SCAN_SECONDS` | `60` | 超时扫描周期 |
| `DEFAULT_INTERVAL_MIN` | `15` | 新建节点默认心跳间隔（分钟） |
| `DEFAULT_ALERT_COOLDOWN_MIN` | `30` | 新建节点默认告警冷却（分钟） |
| `HISTORY_RETENTION_DAYS` | `30` | 每天 03:00 清理更早的历史 |
| `DEFAULT_DING_KEYWORD` | `报警` | 新建节点默认钉钉安全关键词 |

节点级参数（node_key / webhook / 关键词 / 间隔 / 冷却 / 启用）全部在 Web UI 维护，改完立即生效，无需重启。

---

## 五、日常运维

```bash
sudo bash deploy/manage.sh status      # 状态 + 健康检查
sudo bash deploy/manage.sh restart     # 改 .env 后重启
sudo bash deploy/manage.sh logs 200    # 最近 200 行日志
sudo bash deploy/manage.sh follow      # 实时日志
journalctl -u heartbeat-monitor -f    # 等价的系统日志命令
systemctl status heartbeat-monitor    # 原生 systemd 命令
```

- **日志**：走 journald，无单独日志文件；轮转由 journald 管理。
- **备份**：`manage.sh stop` → 拷贝仓库根的 `hb.db` → `manage.sh start`。
- **升级**：
  ```bash
  cd /opt/heartbeat_monitor
  git pull
  sudo bash deploy/install.sh     # 幂等：刷新依赖与 systemd 单元并重启，不覆盖 .env / hb.db
  ```
- **卸载**：`sudo bash deploy/uninstall.sh`（保留数据）；`--purge-data` 会删掉 `venv/`、`hb.db`、`.env`、`logs/`。

---

## 六、常见问题（FAQ）

**Q：页面能打开但没样式 / 很慢？**
A：Bootstrap 已放在 `static/css/`，不依赖外网。若仍异常，看 `journalctl -u heartbeat-monitor -n 50` 是否有 `/static` 相关报错。

**Q：服务起不来？**
A：`sudo bash deploy/manage.sh logs 100`。常见原因：端口被占用（`ss -lntp | grep 8000`）、依赖没装好、`.env` 格式错误、安装路径含空格。

**Q：外网访问不到，但本机 `/health` 正常？**
A：九成是云厂商**安全组**没放行 TCP 8000；其次检查 `ufw` / `firewalld`。

**Q：钉钉收不到告警？**
A：①节点是否启用；②Web UI 点「测试钉钉」；③关键词是否命中钉钉机器人的安全设置；④服务器能否出网（`curl -I https://oapi.dingtalk.com`）；⑤是否还在冷却期内。

**Q：想用非 root 用户跑服务？**
A：先建用户再装：
```bash
sudo useradd -r -s /sbin/nologin heartbeat
sudo bash deploy/install.sh -u heartbeat
```
脚本会把仓库目录归属改为该用户（SQLite 需要在库目录写 journal 文件）。注意后续 `git pull` 用 root 执行即可。

**Q：`seed/hb.db` 里有 webhook，怎么脱敏后再推公开仓库？**
A：用一份干净的空库替换即可（表结构会自动创建）：
```bash
python -c "import sqlite3; sqlite3.connect('seed/hb.db').close()"   # 生成空文件
```
或者用 SQLite 工具清空 `monitor_nodes` 表后再提交。替换后首次部署的节点列表为空，需在 Web UI 新建。

**Q：Python 3.13 装不上锁定的依赖？**
A：`install.sh` 会自动改用不锁版本重试；如仍失败，手动 `venv/bin/pip install -r requirements.txt` 看具体报错。长期建议把 `requirements.txt` 升级到支持该 Python 的版本。

**Q：时间显示不对？**
A：库里存 UTC、页面固定 +8 小时展示，与服务器本地时区无关，属预期行为。

**Q：旧文件 `start.sh` / `stop.sh` / `heartbeat-monitor.service` 还能用吗？**
A：**不能**，它们是早期版本、硬编码 `/opt/heartbeat_monitor` 且用 nohup/pid 文件管理，已废弃，仅为留档。请统一用 `deploy/install.sh` + systemd。

---

## 七、已知限制

- **心跳接口无鉴权**：`HEARTBEAT_TOKEN` 在配置里定义了但代码未校验；任何能访问 8000 端口的人都能伪造心跳。建议用安全组限制来源 IP，或后续在 `/api/heartbeat` 加校验。
- **单 worker 硬约束**：见「进程模型」。
- **告警无升级/重试**：仅按冷却周期重复提醒，不区分告警级别。
- **默认密码弱**：`.env.example` 里的 `ADMIN_PASSWORD=change_me` 仅作占位，部署后必须修改。

---

## 附：依赖与镜像

`requirements.txt` 已锁定版本（FastAPI 0.110 / Uvicorn 0.29 / SQLAlchemy 2.0 / Pydantic 2.6 / APScheduler 3.10 等）。`install.sh` 依次尝试：阿里云 pypi → 清华 pypi → 官方 pypi，可用 `PIP_INDEX_URL` 环境变量覆盖：

```bash
sudo PIP_INDEX_URL=https://pypi.org/simple bash deploy/install.sh
```
