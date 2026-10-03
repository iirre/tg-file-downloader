# TG 文件离线下载

Telegram 文件离线下载 Web 面板：粘贴 t.me 链接，服务器用已登录的 Telegram 账号把文件下载到本地，再通过浏览器取回。适合收藏频道文件、批量备份相册合集。

## 功能

- 📥 粘贴 t.me 文件链接下载（公开频道/群、`t.me/s` 预览链接、私密 `t.me/c` 链接，`?single` 参数自动兼容）
- 🖼️ 媒体合集（相册/九宫格）自动识别整组下载
- ⚡ 多文件 4 路并行下载（Telegram 单连接限速约 0.8 MB/s，4 路可达 3 MB/s）
- 📊 实时下载速度显示
- 🔗 每条记录保留 Telegram 原链接文本，方便找回重下
- 👤 多账号管理：右上角下拉框一键切换，每个账号独立 session
- 🔒 api_id / api_hash / 手机号默认隐藏，可点 👁️ 查看
- 🔄 刷新/关闭网页不中断下载（任务在服务器后台跑）

## 准备工作

开始之前，你需要准备好这几样东西：

| 准备项 | 说明 |
|---|---|
| ☁️ 一台云服务器 | Oracle Cloud（有免费套餐）、腾讯云、阿里云等都可以；系统推荐 Ubuntu 20.04 / 22.04 / 24.04；1 核 1G 就够用 |
| 📱 一个 Telegram 账号 | **强烈建议用小号**（频繁下载可能触发风控）；大号有被限制风险 |
| 🔑 api_id / api_hash | 去 https://my.telegram.org  登录 → API development tools → 创建应用，即可获得 |
| 🌐 域名（可选） | 如果想用 `https://` 访问并配证书，需要一个域名并把 A 记录指到服务器 IP；只内网/裸 IP 用可跳过 |

## 部署步骤

### 1. 登录服务器，安装 Python

```bash
# Ubuntu / Debian
sudo apt update && sudo apt install -y python3 python3-venv git

# 确认版本（需要 Python 3.8+）
python3 --version
```

### 2. 拉取代码

```bash
git clone https://github.com/iirre/tg-file-downloader.git
cd tg-file-downloader
```

> 仓库是私密的，`git clone` 会提示登录 GitHub，按提示完成授权即可。

### 3. 创建虚拟环境并安装依赖

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 4. 设置面板登录密码

面板本身有一层 Basic Auth 登录（防陌生人访问），用环境变量设置：

```bash
export PANEL_USER=admin
export PANEL_PASS=你自己设一个强密码
```

> 建议把这两行写进 `~/.bashrc` 或 systemd 配置里，免得每次重开终端都要设。

### 5. 首次启动（测试）

```bash
source venv/bin/activate
PANEL_USER=admin PANEL_PASS=你的密码 uvicorn app:app --host 0.0.0.0 --port 8280
```

浏览器打开 `http://服务器IP:8280`，输入上面设的账号密码，能看到面板即成功。`Ctrl+C` 停掉，进入下一步做常驻。

> 云服务器注意放行 8280 端口：Oracle Cloud 要在控制台安全列表加 TCP 8280 入站规则，同时服务器上 `sudo iptables` / `ufw` 也要放行。

### 6. 用 systemd 常驻（推荐）

```bash
sudo tee /etc/systemd/system/tg-downloader.service > /dev/null <<'EOF'
[Unit]
Description=TG File Downloader
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/tg-file-downloader
Environment=PANEL_USER=admin
Environment=PANEL_PASS=你的密码
ExecStart=/home/ubuntu/tg-file-downloader/venv/bin/uvicorn app:app --host 127.0.0.1 --port 8280
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now tg-downloader
sudo systemctl status tg-downloader   # 看到 active 即正常
```

> 注意把 `User=`、`WorkingDirectory=`、`ExecStart=` 里的路径换成你实际的用户名和代码路径。

### 7.（可选）nginx 反代 + HTTPS

如果你有域名，想用 `https://tg.example.com` 访问：

```bash
sudo apt install -y nginx certbot python3-certbot-nginx
```

nginx 配置（`/etc/nginx/sites-available/tg-downloader`）：

```nginx
server {
    server_name tg.example.com;
    location / {
        proxy_pass http://127.0.0.1:8280;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/tg-downloader /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
# 申请证书（按提示输入邮箱）
sudo certbot --nginx -d tg.example.com
```

证书会自动续期。之后都用 `https://tg.example.com` 访问。

> Cloudflare 用户：DNS 设为「仅 DNS」（灰云），让 Let's Encrypt 能直接验证到源站。

## 使用说明

### 登录 Telegram

1. 打开面板，在「设置与 Telegram 登录」填入 `api_id` / `api_hash`，点 **保存**
2. 填手机号（带国家区号，如 `+86`），点 **发送验证码**
3. 去 Telegram 查收验证码填入；如果开了两步验证，再输入密码
4. 顶部显示「已登录 xxx」即成功

### 下载文件

1. 在 Telegram 里复制文件消息链接（形如 `https://t.me/频道名/123`）
2. 粘贴到面板下载框，点 **开始下载**
3. 相册/九宫格会自动把整组下下来，每个文件单独一个 💾 按钮
4. 进度条实时显示速度（`⚡ x.x MB/s`）；下载中刷新/关闭网页不影响，后台会继续下

### 多账号切换

- 右上角下拉框列出已保存的账号，点选即切换（无需重新输验证码）
- **＋保存**：把当前登录的号存入列表
- **＋ 添加新账号**：新建空槽位，再走上面的登录流程登新号
- **删**：删除选中账号（含它的 session 文件）

### 下载记录

每条记录下方显示：
- 📁 服务器存放路径
- 🔗 当初粘贴的 Telegram 原链接（纯文本，点击可全选复制，方便以后重下）

## 常见问题

**Q: 下载速度慢？**
A: Telegram 对单连接限速（约 0.8 MB/s）。本面板多文件已做 4 路并行；单文件仍是单路，这是 Telegram 侧限制。

**Q: 提示 "文件已经存在"？**
A: 目标目录已有同名文件。删掉旧文件或改名后再下。

**Q: 验证码收不到？**
A: 确认手机号带国家区号且是该账号绑定的号码；频繁操作可能被限流，等几分钟再试。

**Q: 想换 Telegram 账号？**
A: 右上角下拉框 → ＋ 添加新账号 → 重新走登录流程。旧号点 ＋保存 留着随时切回。

## 目录结构

```
.
├── app.py              # FastAPI 后端（含下载、账号管理 API）
├── requirements.txt
└── templates/
    └── index.html      # 前端单页
```

运行时数据（`data/`：session 文件、账号列表、下载的文件）默认不进 git，见 `.gitignore`。

## 安全提醒

- 面板 Basic Auth 密码设复杂一点；公网部署务必配 HTTPS
- Telegram 请用小号，大号有风控风险
- `data/` 目录含登录 session，备份服务器时注意保管，别公开
