# TG 文件离线下载

Telegram 文件离线下载 Web 面板：粘贴 t.me 链接，服务器用已登录的 Telegram 账号把文件下载到本地，再通过浏览器取回。

## 功能

- 📥 粘贴 t.me 文件链接下载（支持公开频道/群、`t.me/s` 预览链接、私密 `t.me/c` 链接）
- 🖼️ 媒体合集（相册/九宫格）自动识别整组下载
- ⚡ 多文件并行下载（4 路并发）
- 📊 实时下载速度显示
- 🔗 每条记录保留 Telegram 原链接，方便找回重下
- 👤 多账号管理：右上角下拉框切换账号，每个账号独立 session
- 🔒 敏感字段（api_id / api_hash / 手机号）默认隐藏，可点眼睛查看

## 部署

```bash
python3 -m venv venv
source venv/bin/venv/bin/activate  # 或 venv/Scripts/activate (Windows)
pip install -r requirements.txt

# 设置面板登录账号（Basic Auth）
export PANEL_USER=admin
export PANEL_PASS=你的密码

# 启动
uvicorn app:app --host 0.0.0.0 --port 8280
```

用 systemd 常驻 + nginx 反代 + Let's Encrypt 可参考部署文档（略）。

## 使用

1. 浏览器打开面板，通过 Basic Auth 登录
2. 在「设置与 Telegram 登录」填入 `api_id` / `api_hash`（[my.telegram.org](https://my.telegram.org) 申请），点保存
3. 填手机号 → 发送验证码 → 输入验证码（开了两步验证再输密码）
4. 在下载区粘贴 t.me 文件链接 → 开始下载
5. 下载完成后点 💾 按钮把文件拖回本地

## 多账号

- 右上角下拉框可切换已保存的账号
- 「＋保存」把当前登录的号存入列表
- 「＋ 添加新账号」创建空槽位，再走正常登录流程
- 「删」删除选中账号（含 session 文件）

## 目录结构

```
.
├── app.py              # FastAPI 后端
├── requirements.txt
└── templates/
    └── index.html      # 前端单页
```

运行时数据（`data/`：session 文件、账号列表、下载文件）不会进入 git，见 `.gitignore`。

## 注意

- 请用小号登录（Telegram 风控）
- 下载目录默认为 `data/downloads/`，可在代码中修改 `DL_DIR`
