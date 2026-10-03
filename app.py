#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TG 转发器 Web 面板"""
import asyncio
import json
import logging
import os
import re
import secrets
import time
from pathlib import Path
from typing import Dict, Optional

from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError, FloodWaitError

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("webpanel")

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
DATA.mkdir(exist_ok=True)
JOBS_DIR = DATA / "jobs"
JOBS_DIR.mkdir(exist_ok=True)
SESSION_PATH = str(DATA / "tg_session")
APP_CONF = DATA / "app_config.json"
ACCOUNTS_FILE = DATA / "accounts.json"
SESSIONS_DIR = DATA / "sessions"
SESSIONS_DIR.mkdir(exist_ok=True)

PANEL_USER = os.environ.get("PANEL_USER", "admin")
PANEL_PASS = os.environ.get("PANEL_PASS", "changeme")

app = FastAPI(title="TG 转发器")
security = HTTPBasic()


def auth(c: HTTPBasicCredentials = Depends(security)):
    ok = secrets.compare_digest(c.username, PANEL_USER) and \
        secrets.compare_digest(c.password, PANEL_PASS)
    if not ok:
        raise HTTPException(401, "需要登录", headers={"WWW-Authenticate": "Basic"})
    return c.username


settings = {"api_id": "", "api_hash": ""}
if APP_CONF.exists():
    try:
        settings.update(json.loads(APP_CONF.read_text(encoding="utf-8")))
    except Exception:
        pass

tg_client: Optional[TelegramClient] = None
_cli_lock = asyncio.Lock()
_pending_code: Dict[str, str] = {}


def _load_accounts():
    """返回 {"accounts": [...], "active_id": ...}"""
    if ACCOUNTS_FILE.exists():
        try:
            d = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
            if isinstance(d, dict) and "accounts" in d:
                return d
        except Exception:
            pass
    return {"accounts": [], "active_id": None}


def _save_accounts(d):
    ACCOUNTS_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def _get_active_account():
    d = _load_accounts()
    aid = d.get("active_id")
    for acc in d.get("accounts", []):
        if acc.get("id") == aid:
            return acc
    return None


def _account_session_path(acc):
    if acc.get("session"):
        return acc["session"]
    return str(SESSIONS_DIR / f"tg_session_{acc['id']}")


_tg_client_aid = "__none__"


async def get_client() -> Optional[TelegramClient]:
    global tg_client, _tg_client_aid
    async with _cli_lock:
        acc = _get_active_account()
        aid = acc["id"] if acc else None
        if tg_client is not None and _tg_client_aid != aid:
            try:
                await tg_client.disconnect()
            except Exception:
                pass
            tg_client = None
        if tg_client is None:
            if acc:
                sess = _account_session_path(acc)
                api_id, api_hash = acc.get("api_id"), acc.get("api_hash")
            else:
                api_id, api_hash = settings.get("api_id"), settings.get("api_hash")
                sess = SESSION_PATH
            if not api_id or not api_hash:
                return None
            tg_client = TelegramClient(sess, int(api_id), str(api_hash))
            _tg_client_aid = aid
            try:
                await tg_client.connect()
            except Exception as e:
                logger.error("TG 连接失败: %s", e)
                tg_client = None
                _tg_client_aid = "__none__"
                return None
        elif not tg_client.is_connected():
            try:
                await tg_client.connect()
            except Exception:
                pass
        return tg_client


async def tg_logged_in() -> bool:
    c = await get_client()
    if c is None:
        return False
    try:
        return await c.is_user_authorized()
    except Exception:
        return False


# ---------- 页面 ----------

@app.get("/", response_class=HTMLResponse)
async def index(user=Depends(auth)):
    return (BASE / "templates" / "index.html").read_text(encoding="utf-8")


# ---------- 设置 ----------

class SettingsIn(BaseModel):
    api_id: str
    api_hash: str


@app.get("/api/settings")
async def get_settings(user=Depends(auth)):
    return {"api_id": settings.get("api_id", ""), "api_hash": settings.get("api_hash", ""),
            "api_hash_set": bool(settings.get("api_hash"))}


@app.post("/api/settings")
async def save_settings(body: SettingsIn, user=Depends(auth)):
    global tg_client
    settings["api_id"] = body.api_id.strip()
    settings["api_hash"] = body.api_hash.strip()
    APP_CONF.write_text(json.dumps(settings, ensure_ascii=False), encoding="utf-8")
    acc = _get_active_account()
    if acc:
        d = _load_accounts()
        for a in d["accounts"]:
            if a["id"] == acc["id"]:
                a["api_id"] = settings["api_id"]
                a["api_hash"] = settings["api_hash"]
                break
        _save_accounts(d)
    if tg_client is not None:
        try:
            await tg_client.disconnect()
        except Exception:
            pass
        tg_client = None
    return {"ok": True}


# ---------- Telegram 登录 ----------

class SendCodeIn(BaseModel):
    phone: str


class VerifyIn(BaseModel):
    phone: str
    code: str = ""
    password: str = ""


@app.get("/api/auth/status")
async def auth_status(user=Depends(auth)):
    c = await get_client()
    if c is None:
        return {"configured": False, "logged_in": False, "me": None}
    ok = await tg_logged_in()
    me = None
    if ok:
        try:
            m = await c.get_me()
            name = " ".join(x for x in [m.first_name, m.last_name] if x).strip()
            me = {"name": name, "username": m.username, "phone": m.phone}
        except Exception:
            pass
    return {"configured": True, "logged_in": ok, "me": me}


@app.post("/api/auth/send-code")
async def send_code(body: SendCodeIn, user=Depends(auth)):
    c = await get_client()
    if c is None:
        raise HTTPException(400, "请先填写 api_id / api_hash 并保存")
    phone = body.phone.strip()
    if not phone:
        raise HTTPException(400, "请填写手机号（带国家区号，如 +86）")
    try:
        sent = await c.send_code_request(phone)
    except Exception as e:
        raise HTTPException(400, f"发送验证码失败: {e}")
    _pending_code[phone] = sent.phone_code_hash
    return {"ok": True}


@app.post("/api/auth/verify")
async def verify(body: VerifyIn, user=Depends(auth)):
    c = await get_client()
    if c is None:
        raise HTTPException(400, "请先填写 api_id / api_hash 并保存")
    phone = body.phone.strip()
    code = body.code.strip()
    try:
        if phone in _pending_code:
            await c.sign_in(phone, code, phone_code_hash=_pending_code[phone])
        else:
            await c.sign_in(phone, code)
    except SessionPasswordNeededError:
        if not body.password:
            return {"need_password": True}
        try:
            await c.sign_in(password=body.password)
        except Exception as e:
            raise HTTPException(400, f"两步验证密码错误: {e}")
    except Exception as e:
        raise HTTPException(400, f"登录失败: {e}")
    _pending_code.pop(phone, None)
    m = await c.get_me()
    name = " ".join(x for x in [m.first_name, m.last_name] if x).strip()
    acc = _get_active_account()
    if acc:
        d = _load_accounts()
        for a in d["accounts"]:
            if a["id"] == acc["id"]:
                a["phone"] = m.phone or a.get("phone", "")
                a["name"] = name
                a["username"] = m.username or ""
                break
        _save_accounts(d)
    return {"ok": True, "me": {"name": name, "username": m.username}}


@app.post("/api/auth/logout")
async def logout(user=Depends(auth)):
    global tg_client
    if tg_client is not None:
        try:
            await tg_client.log_out()
        except Exception:
            pass
        try:
            await tg_client.disconnect()
        except Exception:
            pass
        tg_client = None
    return {"ok": True}


# ---------- 多账号管理 ----------

async def _auto_migrate_legacy():
    """如果账号列表为空但老 session 已登录，自动迁移为第一个账号"""
    d = _load_accounts()
    if d["accounts"]:
        return d
    import uuid, shutil
    c = await get_client()
    if c is None:
        return d
    try:
        if not await c.is_user_authorized():
            return d
        m = await c.get_me()
    except Exception:
        return d
    aid = "acc_" + uuid.uuid4().hex[:8]
    new_sess = str(SESSIONS_DIR / f"tg_session_{aid}")
    old_sess_file = SESSION_PATH + ".session"
    try:
        if Path(old_sess_file).exists() and not Path(new_sess + ".session").exists():
            shutil.copy2(old_sess_file, new_sess + ".session")
    except Exception:
        pass
    name = " ".join(x for x in [m.first_name, m.last_name] if x).strip()
    acc = {"id": aid, "phone": m.phone or "",
           "api_id": str(settings.get("api_id", "")),
           "api_hash": str(settings.get("api_hash", "")),
           "name": name, "username": m.username or ""}
    d["accounts"].append(acc)
    d["active_id"] = aid
    _save_accounts(d)
    global tg_client
    if tg_client is not None:
        try:
            await tg_client.disconnect()
        except Exception:
            pass
        tg_client = None
    logger.info("自动迁移老账号: %s (%s)", name, m.phone)
    return d


@app.get("/api/accounts")
async def list_accounts(user=Depends(auth)):
    d = await _auto_migrate_legacy()
    accounts = []
    for acc in d.get("accounts", []):
        a = {k: v for k, v in acc.items() if k not in ("api_hash", "session")}
        sess = _account_session_path(acc)
        a["has_session"] = Path(sess + ".session").exists()
        try:
            a["logged_in"] = False
            if a["has_session"] and d.get("active_id") == acc["id"]:
                c = await get_client()
                if c is not None:
                    a["logged_in"] = await c.is_user_authorized()
        except Exception:
            pass
        accounts.append(a)
    return {"accounts": accounts, "active_id": d.get("active_id")}


@app.post("/api/accounts/save-current")
async def save_current_account(user=Depends(auth)):
    """把当前登录的账号存入列表"""
    import uuid, shutil
    c = await get_client()
    if c is None or not await c.is_user_authorized():
        raise HTTPException(400, "当前未登录 Telegram，无法保存")
    m = await c.get_me()
    phone = m.phone or ""
    d = _load_accounts()
    for acc in d["accounts"]:
        if acc.get("phone") == phone and phone:
            d["active_id"] = acc["id"]
            _save_accounts(d)
            return {"ok": True, "id": acc["id"], "exists": True}
    aid = "acc_" + uuid.uuid4().hex[:8]
    new_sess = str(SESSIONS_DIR / f"tg_session_{aid}")
    old_sess_file = SESSION_PATH + ".session"
    if Path(old_sess_file).exists() and not Path(new_sess + ".session").exists():
        shutil.copy2(old_sess_file, new_sess + ".session")
    name = " ".join(x for x in [m.first_name, m.last_name] if x).strip()
    acc = {"id": aid, "phone": phone,
           "api_id": str(settings.get("api_id", "")),
           "api_hash": str(settings.get("api_hash", "")),
           "name": name, "username": m.username or ""}
    d["accounts"].append(acc)
    d["active_id"] = aid
    _save_accounts(d)
    global tg_client
    if tg_client is not None:
        try:
            await tg_client.disconnect()
        except Exception:
            pass
        tg_client = None
    return {"ok": True, "id": aid}


@app.post("/api/accounts/new")
async def new_account(user=Depends(auth)):
    """新建空账号槽位并设为当前，用于登录新号"""
    import uuid
    d = _load_accounts()
    aid = "acc_" + uuid.uuid4().hex[:8]
    acc = {"id": aid, "phone": "",
           "api_id": str(settings.get("api_id", "")),
           "api_hash": str(settings.get("api_hash", "")),
           "name": "", "username": ""}
    d["accounts"].append(acc)
    d["active_id"] = aid
    _save_accounts(d)
    global tg_client
    if tg_client is not None:
        try:
            await tg_client.disconnect()
        except Exception:
            pass
        tg_client = None
    return {"ok": True, "id": aid}


@app.post("/api/accounts/{aid}/activate")
async def activate_account(aid: str, user=Depends(auth)):
    d = _load_accounts()
    if not any(a["id"] == aid for a in d["accounts"]):
        raise HTTPException(404, "账号不存在")
    d["active_id"] = aid
    _save_accounts(d)
    global tg_client
    if tg_client is not None:
        try:
            await tg_client.disconnect()
        except Exception:
            pass
        tg_client = None
    return {"ok": True}


@app.delete("/api/accounts/{aid}")
async def delete_account(aid: str, user=Depends(auth)):
    d = _load_accounts()
    acc = next((a for a in d["accounts"] if a["id"] == aid), None)
    if not acc:
        raise HTTPException(404, "账号不存在")
    d["accounts"] = [a for a in d["accounts"] if a["id"] != aid]
    if d.get("active_id") == aid:
        d["active_id"] = None
        global tg_client
        if tg_client is not None:
            try:
                await tg_client.disconnect()
            except Exception:
                pass
            tg_client = None
    sess = _account_session_path(acc)
    for ext in (".session", ".session-journal"):
        try:
            Path(sess + ext).unlink(missing_ok=True)
        except Exception:
            pass
    _save_accounts(d)
    return {"ok": True}


# ---------- Telegram 文件离线下载 ----------

DL_DIR = DATA / "downloads"
DL_DIR.mkdir(exist_ok=True)
DL_CONF = DATA / "downloads.json"

_downloads: Dict[str, dict] = {}


def _new_job_id() -> str:
    return time.strftime("%Y%m%d%H%M%S") + os.urandom(2).hex()


def _load_downloads():
    if DL_CONF.exists():
        try:
            for d in json.loads(DL_CONF.read_text(encoding="utf-8")):
                if d.get("status") == "downloading":
                    d["status"] = "stopped"
                    d["error"] = "服务重启，下载中断，可删除后重下"
                _downloads[d["id"]] = d
        except Exception as e:
            logger.warning("载入下载记录失败: %s", e)


def _save_downloads():
    try:
        DL_CONF.write_text(
            json.dumps(list(_downloads.values()), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning("保存下载记录失败: %s", e)


_load_downloads()


def _parse_tg_link(url: str):
    """解析 t.me 文件链接 -> (chat, msg_id)。

    支持：
      https://t.me/频道用户名/123
      https://t.me/s/频道用户名/123
      https://t.me/c/1234567890/123   （私密频道/群，自动转成 -100 开头）
      频道用户名/123                  （裸粘贴也行）
    """
    u = url.strip()
    u = u.split("?", 1)[0].split("#", 1)[0]  # 去掉 ?query / #fragment（如 ?single）
    if "://" in u:
        u = u.split("://", 1)[1]
    if u.lower().startswith("t.me/"):
        u = u[5:]
    parts = [p for p in u.split("/") if p]
    if parts and parts[0] == "s":
        parts = parts[1:]
    if len(parts) < 2:
        raise ValueError("链接格式不对，要像这样：https://t.me/频道名/123")
    if parts[0] == "c":
        if len(parts) < 3 or not parts[1].isdigit() or not parts[2].isdigit():
            raise ValueError("私密链接格式不对，要像这样：https://t.me/c/1234567890/123")
        return int("-100" + parts[1]), int(parts[2])
    if parts[0].startswith("+") or parts[0].startswith("joinchat"):
        raise ValueError("暂不支持邀请链接，请先加入该频道/群组再用普通消息链接")
    if not parts[1].isdigit():
        raise ValueError("链接格式不对，要像这样：https://t.me/频道名/123")
    return parts[0], int(parts[1])


def _safe_name(name: str, fallback: str) -> str:
    name = (name or "").strip() or fallback
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .")
    return (name[:120] or fallback)


class DlIn(BaseModel):
    url: str


async def _run_download(did: str):
    rec = _downloads[did]
    try:
        rec["stage"] = "连接 Telegram…"
        c = await get_client()
        if c is None or not await c.is_user_authorized():
            raise RuntimeError("Telegram 未登录，请先在面板完成登录")
        rec["stage"] = "解析链接…"
        chat, msg_id = _parse_tg_link(rec["url"])
        rec["stage"] = "查找频道/群组…"
        try:
            entity = await c.get_entity(chat)
        except Exception as e:
            raise RuntimeError(f"找不到该频道/群组（可能没加入或链接有误）: {e}")
        rec["stage"] = "获取文件信息…"
        msg = await c.get_messages(entity, ids=msg_id)
        if msg is None:
            raise RuntimeError("找不到这条消息（可能已删除）")
        rec["chat_title"] = getattr(entity, "title", "") or str(chat)

        # 媒体合集（相册/九宫格）：把同组的消息一次性都下下来
        targets = [msg]
        if msg.grouped_id:
            rec["stage"] = "检测到媒体合集，获取组内全部消息…"
            try:
                near = await c.get_messages(entity, ids=list(range(max(1, msg_id - 10), msg_id + 11)))
                grp = [m for m in near if m and m.grouped_id == msg.grouped_id and m.media]
                grp.sort(key=lambda m: m.id)
                if grp:
                    targets = grp
            except Exception as e:
                logger.warning("获取合集消息失败，退回单条下载: %s", e)
        multi = len(targets) > 1
        rec["file_count"] = len(targets)
        rec["files"] = []
        if multi:
            rec["disp_name"] = f"媒体合集（{len(targets)}个文件）"
            rec["server_path"] = str(DL_DIR)

        # 准备所有文件任务（串行，速度快）
        jobs = []
        for idx, m in enumerate(targets, 1):
            mf = m.file
            if not mf:
                continue
            if mf.name:
                disp = _safe_name(mf.name, f"tg_{m.id}")
            else:
                disp = f"tg_{m.id}{(mf.ext or '.bin')}"
            if multi:
                disp = f"{idx:02d}_{disp}"
            else:
                rec["disp_name"] = disp
            target = DL_DIR / f"{did}_{disp}"
            if not multi:
                rec["server_path"] = str(target)
            jobs.append({"idx": idx, "msg": m, "disp": disp,
                         "target": target, "size": mf.size or 0})
        if not jobs:
            raise RuntimeError("这条消息没有可下载的文件（可能是纯文字）")

        # 并行下载：Telegram 对单连接限速，多路并发可线性提速
        _PARALLEL = 4
        rec["status"] = "downloading"
        rec["stage"] = f"并行下载中…（{_PARALLEL}路）" if multi else "下载中…"
        _save_downloads()
        _sem = asyncio.Semaphore(_PARALLEL)
        _prog = {j["idx"]: 0 for j in jobs}
        _total_sz = sum(j["size"] for j in jobs)
        _spd = {"t": time.time(), "bytes": 0, "v": 0}

        async def _dl_one(j):
            async with _sem:
                _idx = j["idx"]
                def _cb(cur, total):
                    _prog[_idx] = cur
                    _agg = sum(_prog.values())
                    rec["downloaded"] = _agg
                    rec["total"] = _total_sz or total
                    rec["file_index"] = _idx
                    _now = time.time()
                    _dt = _now - _spd["t"]
                    if _dt >= 1.0:
                        _spd["v"] = (_agg - _spd["bytes"]) / _dt if _dt > 0 else 0
                        _spd["t"] = _now
                        _spd["bytes"] = _agg
                    rec["speed"] = _spd["v"]
                try:
                    final = await c.download_media(j["msg"], file=str(j["target"]),
                                                   progress_callback=_cb)
                except FloodWaitError as e:
                    raise RuntimeError(f"下载第 {_idx} 个文件时触发限流，需等待约 {e.seconds} 秒")
                p = Path(final)
                return {"idx": _idx, "file": p.name, "disp_name": j["disp"],
                        "size": p.stat().st_size}

        _results = await asyncio.gather(*[_dl_one(j) for j in jobs],
                                        return_exceptions=True)
        for _r in _results:
            if isinstance(_r, Exception):
                raise _r
        for _r in sorted(_results, key=lambda x: x["idx"]):
            rec["files"].append({"file": _r["file"], "disp_name": _r["disp_name"],
                                 "size": _r["size"]})
            if not multi:
                rec["file"] = _r["file"]
        rec["status"] = "done"
        rec["size"] = sum(x["size"] for x in rec["files"])
        rec["finished_at"] = time.time()
        rec["error"] = ""
        logger.info("下载完成 %s (%d 个文件)", did, len(rec["files"]))
    except FloodWaitError as e:
        rec["status"] = "failed"
        rec["error"] = f"触发 Telegram 限流，需等待约 {e.seconds} 秒后再试"
    except Exception as e:
        logger.exception("下载失败")
        rec["status"] = "failed"
        rec["error"] = str(e)[:200]
    finally:
        _save_downloads()


@app.post("/api/downloads")
async def create_download(body: DlIn, user=Depends(auth)):
    if not await tg_logged_in():
        raise HTTPException(400, "Telegram 未登录，请先在面板完成登录")
    url = body.url.strip()
    if not url:
        raise HTTPException(400, "请粘贴 t.me 文件链接")
    try:
        _parse_tg_link(url)
    except ValueError as e:
        raise HTTPException(400, str(e))
    did = _new_job_id()
    rec = {
        "id": did, "url": url, "chat_title": "", "disp_name": "",
        "status": "queued", "stage": "排队中…", "server_path": "",
        "downloaded": 0, "total": 0, "file_index": 0, "file_count": 0,
        "files": [],
        "file": "", "size": 0, "error": "", "speed": 0,
        "created_at": time.time(), "finished_at": 0,
    }
    _downloads[did] = rec
    _save_downloads()
    asyncio.create_task(_run_download(did))
    return {"ok": True, "id": did}


@app.get("/api/downloads")
async def list_downloads(user=Depends(auth)):
    return sorted(_downloads.values(), key=lambda x: x["created_at"], reverse=True)


@app.get("/api/downloads/{did}/file")
async def download_file(did: str, user=Depends(auth)):
    return await download_file_at(did, 0, user)


@app.get("/api/downloads/{did}/files/{fidx}")
async def download_file_at(did: str, fidx: int, user=Depends(auth)):
    rec = _downloads.get(did)
    if not rec:
        raise HTTPException(404, "记录不存在")
    files = rec.get("files") or []
    if not files and rec.get("file"):
        files = [{"file": rec["file"], "disp_name": rec.get("disp_name") or rec["file"],
                  "size": rec.get("size", 0)}]
    if fidx < 0 or fidx >= len(files):
        raise HTTPException(404, "文件不存在")
    f = files[fidx]
    p = DL_DIR / f["file"]
    if not p.exists():
        raise HTTPException(404, "文件已被删除")
    return FileResponse(str(p), filename=f.get("disp_name") or p.name)


@app.delete("/api/downloads/{did}")
async def delete_download(did: str, user=Depends(auth)):
    rec = _downloads.get(did)
    if not rec:
        raise HTTPException(404, "记录不存在")
    if rec.get("status") == "downloading":
        raise HTTPException(400, "正在下载中，请等它下完再删")
    for f in rec.get("files") or []:
        try:
            (DL_DIR / f["file"]).unlink()
        except Exception:
            pass
    if rec.get("file"):
        try:
            (DL_DIR / rec["file"]).unlink()
        except Exception:
            pass
    _downloads.pop(did)
    _save_downloads()
    return {"ok": True}
