#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
موقع مسلسل ليل بنفسج — النسخة الاحترافية مع قاعدة بيانات SQLite
ملف واحد - بدون Flask - Python القياسي فقط
التشغيل: python app.py
"""

import http.server
import socketserver
import json
import os
import hashlib
import secrets
import urllib.parse
import time
import uuid
import shutil
import mimetypes
import re
import sqlite3
from http.cookies import SimpleCookie
from datetime import datetime
from collections import defaultdict

# ==================== الإعدادات ====================
PORT = int(os.environ.get("PORT", 8000))
HOST = os.environ.get("HOST", "0.0.0.0")
DATA_FILE = "data.json"          # للتوافق مع النسخة القديمة (استيراد)
DB_FILE = "data.db"              # قاعدة البيانات الجديدة
UPLOAD_DIR = "uploads"
SESSION_HOURS = 24
MAX_UPLOAD_MB = 500

SESSIONS = {}
LOGIN_ATTEMPTS = defaultdict(list)
VIEWS = {"total": 0, "episodes": {}}
VIEWED_UNIQUE = set()

os.makedirs(UPLOAD_DIR, exist_ok=True)

# ==================== قاعدة البيانات (SQLite) ====================
def get_db():
    """فتح اتصال SQLite"""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """إنشاء الجداول إن لم تكن موجودة"""
    with get_db() as conn:
        c = conn.cursor()
        # جدول الإعدادات (key-value)
        c.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        # جدول الأدمن
        c.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                pass_hash TEXT NOT NULL,
                created TEXT
            )
        """)
        # جدول الحلقات
        c.execute("""
            CREATE TABLE IF NOT EXISTS episodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                url TEXT,
                date TEXT,
                thumb TEXT
            )
        """)
        # جدول الأخبار
        c.execute("""
            CREATE TABLE IF NOT EXISTS news (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content TEXT,
                date TEXT
            )
        """)
        # جدول الحظر
        c.execute("""
            CREATE TABLE IF NOT EXISTS blocked (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ip TEXT UNIQUE NOT NULL,
                reason TEXT,
                date TEXT
            )
        """)
        # جدول المشاهدات
        c.execute("""
            CREATE TABLE IF NOT EXISTS views (
                episode_id INTEGER PRIMARY KEY,
                count INTEGER DEFAULT 0
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS stats (
                key TEXT PRIMARY KEY,
                value INTEGER DEFAULT 0
            )
        """)
        conn.commit()

def db_get_setting(key, default=None):
    with get_db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

def db_set_setting(key, value):
    with get_db() as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        conn.commit()

def db_get_stat(key):
    with get_db() as conn:
        row = conn.execute("SELECT value FROM stats WHERE key=?", (key,)).fetchone()
        return row["value"] if row else 0

def db_inc_stat(key):
    with get_db() as conn:
        conn.execute("INSERT INTO stats (key, value) VALUES (?, 1) ON CONFLICT(key) DO UPDATE SET value=value+1", (key,))
        conn.commit()

def db_inc_episode_view(eid):
    with get_db() as conn:
        conn.execute("INSERT INTO views (episode_id, count) VALUES (?, 1) ON CONFLICT(episode_id) DO UPDATE SET count=count+1", (eid,))
        conn.commit()

def db_get_episode_views():
    with get_db() as conn:
        rows = conn.execute("SELECT episode_id, count FROM views").fetchall()
        return {str(r["episode_id"]): r["count"] for r in rows}

# ==================== البيانات (واجهة متوافقة مع القديمة) ====================
def default_data():
    return {
        "site": {
            "title": "ليل بنفسج",
            "subtitle": "مسلسل عراقي - دراما، غموض، وتشويق",
            "about": "مسلسل عراقي يسلط الضوء على قصص إنسانية معقدة في إطار من الغموض والتشويق.",
            "poster": "",
            "announcement": "",
            "support_telegram": "Yb_xY",
        },
        "admins": [
            {"username": "SOST", "pass_hash": hashlib.sha256("SOST2009".encode()).hexdigest(),
             "created": datetime.now().strftime("%Y-%m-%d")}
        ],
        "episodes": [],
        "news": [],
        "blocked": [],
    }

def migrate_from_json():
    """استيراد بيانات data.json القديمة إن وجدت وقاعدة البيانات فارغة"""
    if not os.path.exists(DATA_FILE):
        return False
    try:
        with get_db() as conn:
            admins_count = conn.execute("SELECT COUNT(*) as c FROM admins").fetchone()["c"]
        if admins_count > 0:
            return False  # قاعدة البيانات فيها بيانات مسبقاً
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            old = json.load(f)
        save_data(old)
        print("✅ تم استيراد البيانات من data.json إلى data.db")
        return True
    except Exception as e:
        print(f"⚠️ فشل استيراد data.json: {e}")
        return False

def load_data():
    """قراءة كل البيانات من SQLite بصيغة dict (متوافقة مع القديم)"""
    init_db()
    with get_db() as conn:
        # site
        site = {}
        for row in conn.execute("SELECT key, value FROM settings").fetchall():
            site[row["key"]] = row["value"]
        if not site.get("title"):
            # أول تشغيل: تهيئة البيانات الافتراضية
            d = default_data()
            save_data(d)
            return d
        # admins
        admins = []
        for row in conn.execute("SELECT username, pass_hash, created FROM admins ORDER BY id").fetchall():
            admins.append({"username": row["username"], "pass_hash": row["pass_hash"], "created": row["created"]})
        # episodes
        episodes = []
        for row in conn.execute("SELECT id, title, description, url, date, thumb FROM episodes ORDER BY id").fetchall():
            episodes.append({
                "id": row["id"], "title": row["title"], "desc": row["description"] or "",
                "url": row["url"] or "", "date": row["date"] or "", "thumb": row["thumb"] or ""
            })
        # news
        news = []
        for row in conn.execute("SELECT id, title, content, date FROM news ORDER BY id").fetchall():
            news.append({"id": row["id"], "title": row["title"], "content": row["content"] or "", "date": row["date"] or ""})
        # blocked
        blocked = []
        for row in conn.execute("SELECT ip, reason, date FROM blocked ORDER BY id").fetchall():
            blocked.append({"ip": row["ip"], "reason": row["reason"] or "", "date": row["date"] or ""})
    return {
        "site": site,
        "admins": admins,
        "episodes": episodes,
        "news": news,
        "blocked": blocked,
    }

def save_data(data):
    """حفظ البيانات بالكامل إلى SQLite (متوافق مع الكود القديم)"""
    init_db()
    with get_db() as conn:
        # site settings
        for k, v in (data.get("site") or {}).items():
            conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v if v is not None else ""))
        # admins (حذف الكل وإعادة إدراج - بسيط وموثوق)
        conn.execute("DELETE FROM admins")
        for a in (data.get("admins") or []):
            conn.execute("INSERT INTO admins (username, pass_hash, created) VALUES (?, ?, ?)",
                         (a["username"], a["pass_hash"], a.get("created", "")))
        # episodes
        conn.execute("DELETE FROM episodes")
        for e in (data.get("episodes") or []):
            conn.execute("INSERT INTO episodes (id, title, description, url, date, thumb) VALUES (?, ?, ?, ?, ?, ?)",
                         (e["id"], e.get("title", ""), e.get("desc", ""), e.get("url", ""), e.get("date", ""), e.get("thumb", "")))
        # news
        conn.execute("DELETE FROM news")
        for n in (data.get("news") or []):
            conn.execute("INSERT INTO news (id, title, content, date) VALUES (?, ?, ?, ?)",
                         (n["id"], n.get("title", ""), n.get("content", ""), n.get("date", "")))
        # blocked
        conn.execute("DELETE FROM blocked")
        for b in (data.get("blocked") or []):
            conn.execute("INSERT INTO blocked (ip, reason, date) VALUES (?, ?, ?)",
                         (b["ip"], b.get("reason", ""), b.get("date", "")))
        conn.commit()

def esc(s):
    if s is None:
        return ""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))

def hash_pass(p):
    return hashlib.sha256(p.encode()).hexdigest()

def extract_yt_id(url):
    try:
        if "youtu.be/" in url:
            return url.split("youtu.be/")[1].split("?")[0]
        if "v=" in url:
            return url.split("v=")[1].split("&")[0]
        if "/embed/" in url:
            return url.split("/embed/")[1].split("?")[0]
    except Exception:
        pass
    return ""

# ==================== القالب المشترك ====================
BASE_CSS = """
*{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{height:100%;overflow-x:hidden}
body{font-family:'Segoe UI',Tahoma,sans-serif;background:linear-gradient(135deg,#1a0b2e 0%,#2d1b4e 50%,#0f0518 100%);color:#f0e6ff;min-height:100vh;padding-bottom:95px}
body::before{content:'';position:fixed;inset:0;background:radial-gradient(circle at 20% 20%,rgba(147,51,234,.18) 0%,transparent 55%),radial-gradient(circle at 80% 80%,rgba(88,28,135,.18) 0%,transparent 55%);pointer-events:none;z-index:-1}

.top-header{background:rgba(20,10,40,.85);backdrop-filter:blur(14px);padding:14px 20px;display:flex;justify-content:space-between;align-items:center;position:sticky;top:0;z-index:100;border-bottom:1px solid rgba(168,85,247,.35)}
.top-logo{font-size:22px;font-weight:900;letter-spacing:1px;background:linear-gradient(90deg,#d8b4fe,#a855f7,#c084fc);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;filter:drop-shadow(0 0 12px rgba(168,85,247,.5))}
.top-support{background:linear-gradient(90deg,#0088cc,#00aaff);padding:8px 14px;border-radius:20px;color:#fff;text-decoration:none;font-weight:800;font-size:12px;display:flex;align-items:center;gap:6px;transition:.3s;box-shadow:0 4px 15px rgba(0,136,204,.5)}
.top-support:hover{transform:scale(1.05);box-shadow:0 6px 25px rgba(0,170,255,.7)}

.announcement{background:linear-gradient(90deg,#7e22ce,#a855f7,#7e22ce);padding:11px 18px;text-align:center;font-weight:800;color:#fff;font-size:14px;animation:glow 3s ease infinite;position:relative;z-index:50}
@keyframes glow{0%,100%{box-shadow:0 0 18px rgba(168,85,247,.6)}50%{box-shadow:0 0 38px rgba(168,85,247,.9)}}

.hero{text-align:center;padding:45px 20px 30px;animation:fadeIn 1s ease}
@keyframes fadeIn{from{opacity:0;transform:translateY(20px)}to{opacity:1;transform:none}}
.hero h1{font-size:clamp(42px,11vw,76px);margin-bottom:10px;background:linear-gradient(90deg,#e9d5ff,#a855f7,#c084fc);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;filter:drop-shadow(0 0 30px rgba(168,85,247,.65));letter-spacing:3px}
.hero p{font-size:clamp(14px,3.5vw,18px);color:#c4b5fd;margin-bottom:22px}
.poster{width:min(260px,68vw);aspect-ratio:2/3;object-fit:cover;border-radius:22px;box-shadow:0 25px 70px rgba(147,51,234,.55);margin:0 auto;display:block;transition:.4s;cursor:pointer;border:2px solid rgba(168,85,247,.5)}
.poster:hover{transform:scale(1.04) rotate(-1deg);box-shadow:0 30px 90px rgba(168,85,247,.8)}

.section{max-width:1300px;margin:0 auto;padding:26px 16px}
.section h2{font-size:clamp(22px,5vw,34px);margin-bottom:22px;text-align:center;background:linear-gradient(90deg,#c084fc,#a855f7);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}

.ep-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:16px}
.ep-card{position:relative;background:linear-gradient(160deg,rgba(60,30,100,.7),rgba(30,10,60,.9));border:1px solid rgba(168,85,247,.35);border-radius:16px;overflow:hidden;cursor:pointer;transition:.35s;aspect-ratio:16/10}
.ep-card:hover{transform:translateY(-6px) scale(1.02);box-shadow:0 20px 50px rgba(168,85,247,.55);border-color:#c084fc}
.ep-thumb{width:100%;height:100%;object-fit:cover;display:block;transition:.4s;background:#1a0b2e}
.ep-card:hover .ep-thumb{transform:scale(1.08)}
.ep-thumb.placeholder{display:flex;align-items:center;justify-content:center;font-size:60px;background:linear-gradient(135deg,#3b1a63,#1a0b2e)}
.ep-overlay{position:absolute;inset:0;background:linear-gradient(to top,rgba(15,5,24,.98) 0%,rgba(15,5,24,.5) 50%,transparent 100%);display:flex;flex-direction:column;justify-content:flex-end;padding:14px;gap:4px}
.ep-title{font-size:15px;font-weight:800;color:#fff;text-shadow:0 2px 8px #000}
.ep-date{font-size:11px;color:#c4b5fd}
.ep-play{background:linear-gradient(90deg,#7e22ce,#c084fc);border:none;color:#fff;padding:7px 14px;border-radius:9px;font-weight:800;font-size:12px;cursor:pointer;margin-top:6px;width:fit-content;transition:.3s}
.ep-card:hover .ep-play{background:linear-gradient(90deg,#c084fc,#e9d5ff);color:#1a0b2e;transform:scale(1.05)}
.empty{text-align:center;color:#9f7aea;grid-column:1/-1;padding:20px;font-size:15px}

.news-item{background:linear-gradient(135deg,rgba(60,30,100,.5),rgba(30,10,60,.6));border-right:4px solid #a855f7;padding:16px 18px;margin-bottom:12px;border-radius:12px;transition:.3s}
.news-item:hover{transform:translateX(-4px);border-right-color:#c084fc}
.news-item h3{color:#e9d5ff;margin-bottom:5px;font-size:17px}
.news-item .date{font-size:12px;color:#9f7aea;margin-bottom:7px}
.news-item p{color:#d8c4e8;line-height:1.7;font-size:14px}

.about{background:rgba(45,27,78,.5);padding:24px;border-radius:16px;border:1px solid rgba(168,85,247,.3);line-height:1.9;font-size:15px;color:#e0d0f0;backdrop-filter:blur(8px)}

.bottom-nav{position:fixed;bottom:0;left:0;right:0;background:rgba(15,5,24,.96);backdrop-filter:blur(20px);border-top:1px solid rgba(168,85,247,.4);display:flex;justify-content:space-around;padding:8px 4px calc(8px + env(safe-area-inset-bottom,0px));z-index:500;box-shadow:0 -10px 40px rgba(0,0,0,.6)}
.nav-btn{flex:1;display:flex;flex-direction:column;align-items:center;gap:3px;background:none;border:none;color:#9f7aea;font-family:inherit;font-size:10px;font-weight:800;cursor:pointer;padding:8px 4px;border-radius:12px;transition:.25s;text-decoration:none}
.nav-btn .ico{font-size:21px;transition:.25s}
.nav-btn.active,.nav-btn:hover{color:#e9d5ff;background:rgba(168,85,247,.18)}
.nav-btn.active .ico{transform:translateY(-3px) scale(1.15);filter:drop-shadow(0 0 8px #a855f7)}

.player-modal{position:fixed;inset:0;background:rgba(0,0,0,.97);z-index:1000;display:none;flex-direction:column}
.player-modal.active{display:flex}
.player-header{display:flex;justify-content:space-between;align-items:center;padding:12px 16px;background:linear-gradient(180deg,rgba(30,10,60,.95),transparent);z-index:10}
.player-header h2{font-size:15px;color:#e9d5ff;font-weight:800;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:70vw}
.player-close{background:rgba(168,85,247,.3);border:1px solid rgba(168,85,247,.5);color:#fff;width:40px;height:40px;border-radius:50%;font-size:18px;cursor:pointer;transition:.3s;display:flex;align-items:center;justify-content:center}
.player-close:hover{background:#a855f7;transform:rotate(90deg)}
.player-stage{flex:1;display:flex;align-items:center;justify-content:center;padding:0 8px;position:relative;background:#000}
.player-stage iframe,.player-stage video{width:100%;height:100%;max-width:1400px;max-height:calc(100vh - 280px);border:none;border-radius:12px;box-shadow:0 0 60px rgba(168,85,247,.4);background:#000;object-fit:contain}
.player-info{padding:10px 18px;color:#c4b5fd;font-size:13px;text-align:center;max-height:50px;overflow:auto}
.player-eps{display:flex;gap:8px;overflow-x:auto;padding:8px 12px;scrollbar-width:thin;background:rgba(15,5,24,.75)}
.player-eps::-webkit-scrollbar{height:5px}
.player-eps::-webkit-scrollbar-thumb{background:#a855f7;border-radius:5px}
.pep{flex-shrink:0;background:rgba(60,30,100,.6);border:1px solid rgba(168,85,247,.3);color:#d8b4fe;padding:7px 13px;border-radius:9px;font-size:11px;font-weight:800;cursor:pointer;white-space:nowrap;transition:.2s;font-family:inherit}
.pep.active,.pep:hover{background:linear-gradient(90deg,#7e22ce,#a855f7);color:#fff;border-color:#c084fc}
.player-controls{display:flex;gap:8px;justify-content:center;padding:10px 12px calc(12px + env(safe-area-inset-bottom,0px));flex-wrap:wrap;background:linear-gradient(0deg,rgba(30,10,60,.98),transparent)}
.pc-btn{background:linear-gradient(135deg,rgba(126,34,206,.85),rgba(168,85,247,.85));border:1px solid rgba(192,132,252,.5);color:#fff;padding:10px 16px;border-radius:12px;font-weight:800;font-size:13px;cursor:pointer;transition:.3s;font-family:inherit;display:flex;align-items:center;gap:5px;min-width:88px;justify-content:center}
.pc-btn:hover:not(:disabled){transform:translateY(-3px);box-shadow:0 10px 30px rgba(168,85,247,.6);background:linear-gradient(135deg,#a855f7,#c084fc)}
.pc-btn:disabled{opacity:.35;cursor:not-allowed}
.pc-btn.wide{min-width:120px;background:linear-gradient(135deg,#7e22ce,#c084fc)}

.toast{position:fixed;top:18px;left:50%;transform:translateX(-50%) translateY(-120px);background:linear-gradient(90deg,#7e22ce,#a855f7);padding:12px 24px;border-radius:12px;color:#fff;font-weight:800;transition:.4s cubic-bezier(.34,1.56,.64,1);z-index:9999;box-shadow:0 10px 40px rgba(168,85,247,.6);font-size:13px}
.toast.show{transform:translateX(-50%) translateY(0)}

footer{text-align:center;padding:24px 16px 8px;color:#8b6bb8;border-top:1px solid rgba(168,85,247,.2);margin-top:30px;font-size:12px}
"""

BOTTOM_NAV = """
<nav class="bottom-nav">
  <a href="/" class="nav-btn {active_home}"><span class="ico">🏠</span><span>الرئيسية</span></a>
  <a href="/episodes" class="nav-btn {active_episodes}"><span class="ico">🎬</span><span>الحلقات</span></a>
  <a href="/news" class="nav-btn {active_news}"><span class="ico">📰</span><span>الأخبار</span></a>
  <a href="/support" class="nav-btn {active_support}"><span class="ico">🎧</span><span>الدعم</span></a>
  <a href="/admin" class="nav-btn {active_admin}"><span class="ico">🔐</span><span>الأدمن</span></a>
</nav>
"""

def page_shell(title, body_content, active="home", data=None, extra_js=""):
    s = data["site"] if data else {}
    ann = (s.get("announcement") or "").strip()
    ann_html = f'<div class="announcement">📢 {esc(ann)}</div>' if ann else ""
    support_user = s.get("support_telegram", "Yb_xY")
    nav = BOTTOM_NAV.format(
        active_home="active" if active == "home" else "",
        active_episodes="active" if active == "episodes" else "",
        active_news="active" if active == "news" else "",
        active_support="active" if active == "support" else "",
        active_admin="active" if active == "admin" else "",
    )
    return f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<meta name="theme-color" content="#1a0b2e">
<title>{esc(title)}</title>
<style>{BASE_CSS}</style>
</head>
<body>
<header class="top-header">
  <div class="top-logo">🌙 ليل بنفسج</div>
  <a href="https://t.me/{esc(support_user)}" target="_blank" class="top-support">💬 الدعم الفني</a>
</header>
{ann_html}
{body_content}
{nav}
<div class="toast" id="toast"></div>
<script>
function toast(msg){{
  const t = document.getElementById('toast');
  if(!t) return;
  t.textContent = msg; t.classList.add('show');
  setTimeout(()=>t.classList.remove('show'), 2200);
}}
function escapeHtml(s){{
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}}
{extra_js}
</script>
</body>
</html>"""

# ==================== الصفحة الرئيسية ====================
def render_home(data):
    s = data["site"]
    year = datetime.now().year
    eps = data["episodes"]
    latest = sorted(eps, key=lambda e: e.get("date", ""), reverse=True)[:3]
    cards = ""
    for ep in latest:
        thumb = ep.get("thumb") or (f"https://img.youtube.com/vi/{extract_yt_id(ep['url'])}/hqdefault.jpg" if "youtube" in ep.get("url", "") else "")
        thumb_html = f'<img src="{esc(thumb)}" class="ep-thumb" onerror="this.style.display=\'none\'">' if thumb else '<div class="ep-thumb placeholder">🎬</div>'
        cards += f"""
        <div class="ep-card" onclick="openPlayer({ep['id']})">
          {thumb_html}
          <div class="ep-overlay">
            <div class="ep-title">{esc(ep['title'])}</div>
            <div class="ep-date">📅 {esc(ep.get('date',''))}</div>
            <button class="ep-play">▶ شاهد</button>
          </div>
        </div>"""
    if not cards:
        cards = '<p class="empty">لا توجد حلقات بعد</p>'

    poster = s.get("poster", "")
    poster_html = f'<img src="{esc(poster)}" class="poster" onclick="window.location.href=\'/episodes\'" alt="بوستر">' if poster else ""

    body = f"""
<section class="hero">
  <h1>{esc(s['title'])}</h1>
  <p>{esc(s['subtitle'])}</p>
  {poster_html}
</section>

<section class="section">
  <h2>✨ أحدث الحلقات</h2>
  <div class="ep-grid">{cards}</div>
</section>

<section class="section">
  <h2>📖 عن المسلسل</h2>
  <div class="about">{esc(s['about'])}</div>
</section>

<footer>© {year} مسلسل ليل بنفسج</footer>

{render_player_modal(data)}
"""
    extra = get_player_js(data)
    return page_shell(f"{s['title']} - الرئيسية", body, "home", data, extra)

# ==================== صفحة الحلقات ====================
def render_episodes_page(data):
    s = data["site"]
    year = datetime.now().year
    eps = data["episodes"]
    cards = ""
    for ep in eps:
        thumb = ep.get("thumb") or (f"https://img.youtube.com/vi/{extract_yt_id(ep['url'])}/hqdefault.jpg" if "youtube" in ep.get("url", "") else "")
        thumb_html = f'<img src="{esc(thumb)}" class="ep-thumb" onerror="this.style.display=\'none\'">' if thumb else '<div class="ep-thumb placeholder">🎬</div>'
        cards += f"""
        <div class="ep-card" onclick="openPlayer({ep['id']})">
          {thumb_html}
          <div class="ep-overlay">
            <div class="ep-title">{esc(ep['title'])}</div>
            <div class="ep-date">📅 {esc(ep.get('date',''))}</div>
            <button class="ep-play">▶ شاهد</button>
          </div>
        </div>"""
    if not cards:
        cards = '<p class="empty">لا توجد حلقات بعد</p>'

    body = f"""
<section class="section" style="padding-top:30px">
  <h2>🎬 جميع الحلقات ({len(eps)})</h2>
  <div class="ep-grid">{cards}</div>
</section>
<footer>© {year} مسلسل ليل بنفسج</footer>
{render_player_modal(data)}
"""
    return page_shell(f"{s['title']} - الحلقات", body, "episodes", data, get_player_js(data))

# ==================== صفحة الأخبار ====================
def render_news_page(data):
    s = data["site"]
    year = datetime.now().year
    news = data["news"]
    news_html = ""
    for n in news:
        news_html += f"""
        <div class="news-item">
          <h3>{esc(n['title'])}</h3>
          <div class="date">📅 {esc(n.get('date',''))}</div>
          <p>{esc(n.get('content',''))}</p>
        </div>"""
    if not news_html:
        news_html = '<p class="empty">لا توجد أخبار</p>'

    body = f"""
<section class="section" style="padding-top:30px">
  <h2>📰 الأخبار</h2>
  {news_html}
</section>
<footer>© {year} مسلسل ليل بنفسج</footer>
"""
    return page_shell(f"{s['title']} - الأخبار", body, "news", data)

# ==================== صفحة الدعم ====================
def render_support_page(data):
    s = data["site"]
    year = datetime.now().year
    tg = s.get("support_telegram", "Yb_xY")
    body = f"""
<section class="section" style="padding-top:40px">
  <h2>🎧 الدعم الفني</h2>
  <div class="about" style="text-align:center">
    <p style="font-size:18px;margin-bottom:20px">إذا واجهت أي مشكلة في الموقع أو المشغل، تواصل معنا فوراً على Telegram:</p>
    <a href="https://t.me/{esc(tg)}" target="_blank" style="display:inline-block;background:linear-gradient(90deg,#0088cc,#00aaff);padding:16px 40px;border-radius:14px;color:#fff;text-decoration:none;font-weight:900;font-size:20px;box-shadow:0 10px 40px rgba(0,170,255,.5);transition:.3s;margin:10px">
      💬 @{esc(tg)}
    </a>
    <p style="margin-top:25px;font-size:14px;color:#9f7aea">استجابة سريعة خلال دقائق ⚡</p>
  </div>
</section>
<footer>© {year} مسلسل ليل بنفسج</footer>
"""
    return page_shell(f"{s['title']} - الدعم", body, "support", data)

# ==================== المشغل ====================
def render_player_modal(data):
    return """
<div class="player-modal" id="playerModal">
  <div class="player-header">
    <h2 id="pTitle">المشغل</h2>
    <button class="player-close" onclick="closePlayer()">✕</button>
  </div>
  <div class="player-eps" id="pEpList"></div>
  <div class="player-stage" id="pStage">
    <iframe id="pFrame" allow="autoplay; encrypted-media; picture-in-picture; fullscreen" allowfullscreen style="display:none"></iframe>
    <video id="pVideo" controls playsinline style="display:none"></video>
  </div>
  <div class="player-info" id="pInfo"></div>
  <div class="player-controls">
    <button class="pc-btn" id="btnPrev" onclick="prevEp()">⏮ السابق</button>
    <button class="pc-btn wide" id="btnPause" onclick="togglePause()">⏸ إيقاف</button>
    <button class="pc-btn" id="btnNext" onclick="nextEp()">التالي ⏭</button>
  </div>
  <div class="player-controls" style="padding-top:0">
    <button class="pc-btn" onclick="seek(-10)">⏪ -10ث</button>
    <button class="pc-btn" onclick="seek(10)">+10ث ⏩</button>
    <button class="pc-btn" onclick="goFullscreen()">⛶ تكبير</button>
  </div>
</div>
"""

def get_player_js(data):
    eps_json = json.dumps([{
        "id": e["id"], "title": e["title"], "desc": e.get("desc", ""),
        "url": e["url"], "date": e.get("date", "")
    } for e in data["episodes"]], ensure_ascii=False)
    return f"""
const EPISODES = {eps_json};
let currentEp = null;
let paused = false;
let isLocalVideo = false;

function openPlayer(id){{
  const ep = EPISODES.find(e=>e.id===id);
  if(!ep){{ toast('الحلقة غير موجودة'); return; }}
  currentEp = ep;
  paused = false;
  isLocalVideo = ep.url.startsWith('/uploads/') || ep.url.startsWith('/video/');

  const frame = document.getElementById('pFrame');
  const video = document.getElementById('pVideo');

  if(isLocalVideo){{
    frame.style.display = 'none';
    frame.src = '';
    video.style.display = 'block';
    video.src = ep.url;
    video.play().catch(()=>{{}});
  }} else {{
    video.style.display = 'none';
    video.pause();
    video.src = '';
    frame.style.display = 'block';
    frame.src = ep.url + (ep.url.includes('?') ? '&autoplay=1' : '?autoplay=1');
  }}

  document.getElementById('pTitle').textContent = ep.title;
  document.getElementById('pInfo').textContent = ep.desc || '';
  document.getElementById('btnPause').textContent = '⏸ إيقاف';

  fetch('/api/track-view', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{episode_id: ep.id}})}}).catch(()=>{{}});

  const list = document.getElementById('pEpList');
  list.innerHTML = EPISODES.map(e=>
    `<button class="pep ${{e.id===id?'active':''}}" onclick="openPlayer(${{e.id}})">${{escapeHtml(e.title)}}</button>`
  ).join('');

  document.getElementById('playerModal').classList.add('active');
  document.body.style.overflow = 'hidden';
  updateNavButtons();
}}

function closePlayer(){{
  document.getElementById('playerModal').classList.remove('active');
  document.getElementById('pFrame').src = '';
  const v = document.getElementById('pVideo');
  v.pause(); v.src = '';
  document.body.style.overflow = '';
  currentEp = null;
}}

function prevEp(){{
  if(!currentEp) return;
  const idx = EPISODES.findIndex(e=>e.id===currentEp.id);
  if(idx > 0) openPlayer(EPISODES[idx-1].id);
  else toast('هذه أول حلقة');
}}

function nextEp(){{
  if(!currentEp) return;
  const idx = EPISODES.findIndex(e=>e.id===currentEp.id);
  if(idx < EPISODES.length-1) openPlayer(EPISODES[idx+1].id);
  else toast('هذه آخر حلقة');
}}

function updateNavButtons(){{
  if(!currentEp) return;
  const idx = EPISODES.findIndex(e=>e.id===currentEp.id);
  document.getElementById('btnPrev').disabled = idx <= 0;
  document.getElementById('btnNext').disabled = idx >= EPISODES.length-1;
}}

function postYT(cmd, args){{
  const f = document.getElementById('pFrame');
  if(!f.contentWindow) return;
  f.contentWindow.postMessage(JSON.stringify({{event:'command', func:cmd, args:args||[]}}), '*');
}}

function togglePause(){{
  if(!currentEp) return;
  paused = !paused;
  if(isLocalVideo){{
    const v = document.getElementById('pVideo');
    if(paused) v.pause(); else v.play();
  }} else {{
    postYT(paused ? 'pauseVideo' : 'playVideo');
  }}
  document.getElementById('btnPause').textContent = paused ? '▶ تشغيل' : '⏸ إيقاف';
}}

function seek(sec){{
  if(!currentEp) return;
  if(isLocalVideo){{
    const v = document.getElementById('pVideo');
    v.currentTime = Math.max(0, v.currentTime + sec);
  }} else {{
    postYT('seekTo', [sec, true]);
  }}
}}

function goFullscreen(){{
  const el = isLocalVideo ? document.getElementById('pVideo') : document.getElementById('pFrame');
  if(el.requestFullscreen) el.requestFullscreen();
  else if(el.webkitFullscreenRequest) el.webkitFullscreenRequest();
}}

document.addEventListener('keydown', e=>{{
  if(e.key==='Escape' && currentEp) closePlayer();
}});
"""

# ==================== تسجيل الدخول ====================
def render_login(error=""):
    err_html = f'<div class="err">{esc(error)}</div>' if error else ""
    return f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>دخول الأدمن</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box;font-family:'Segoe UI',Tahoma,sans-serif}}
body{{background:linear-gradient(135deg,#1a0b2e,#2d1b4e,#0f0518);min-height:100vh;display:flex;justify-content:center;align-items:center;color:#f0e6ff;padding:20px}}
.box{{background:rgba(45,27,78,.75);padding:40px 30px;border-radius:20px;border:1px solid rgba(168,85,247,.45);width:100%;max-width:410px;backdrop-filter:blur(14px);box-shadow:0 25px 80px rgba(0,0,0,.65)}}
h1{{text-align:center;margin-bottom:24px;background:linear-gradient(90deg,#e9d5ff,#a855f7);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;font-size:28px}}
input{{width:100%;padding:13px;margin:8px 0;background:rgba(20,10,40,.8);border:1px solid rgba(168,85,247,.4);border-radius:11px;color:#fff;font-size:15px}}
input:focus{{outline:none;border-color:#a855f7;box-shadow:0 0 16px rgba(168,85,247,.45)}}
button{{width:100%;padding:13px;margin-top:16px;background:linear-gradient(90deg,#7e22ce,#a855f7);border:none;border-radius:11px;color:#fff;font-size:16px;font-weight:800;cursor:pointer;transition:.3s}}
button:hover{{transform:scale(1.02);box-shadow:0 12px 35px rgba(168,85,247,.6)}}
.err{{color:#ff6b6b;text-align:center;margin-top:12px;font-size:13px;background:rgba(255,0,0,.1);padding:10px;border-radius:8px}}
.back{{display:block;text-align:center;margin-top:16px;color:#c4b5fd;text-decoration:none;font-size:13px}}
.back:hover{{color:#fff}}
</style>
</head>
<body>
<div class="box">
  <h1>🔐 دخول الأدمن</h1>
  <form method="POST" action="/admin/login">
    <input type="text" name="username" placeholder="اسم المستخدم" required autofocus>
    <input type="password" name="password" placeholder="كلمة المرور" required>
    <button type="submit">دخول</button>
  </form>
  {err_html}
  <a href="/" class="back">← العودة للموقع</a>
</div>
</body>
</html>"""

# ==================== لوحة التحكم ====================
def render_admin(username=""):
    return f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>لوحة التحكم - ليل بنفسج</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box;font-family:'Segoe UI',Tahoma,sans-serif}}
body{{background:#0f0518;color:#f0e6ff;min-height:100vh;padding-bottom:40px}}
header{{background:rgba(20,10,40,.95);padding:14px 20px;display:flex;justify-content:space-between;align-items:center;border-bottom:2px solid #a855f7;position:sticky;top:0;z-index:100;flex-wrap:wrap;gap:8px}}
.logo{{font-size:19px;font-weight:900;background:linear-gradient(90deg,#d8b4fe,#a855f7);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}}
.user-badge{{background:rgba(168,85,247,.2);padding:7px 13px;border-radius:18px;font-size:12px;color:#c4b5fd;border:1px solid rgba(168,85,247,.4)}}
.link-btn{{background:#7e22ce;padding:9px 15px;border-radius:9px;color:#fff;text-decoration:none;font-weight:800;transition:.3s;display:inline-block;margin:2px;font-size:13px;border:none;cursor:pointer}}
.link-btn:hover{{background:#a855f7;transform:translateY(-2px)}}
.container{{max-width:1300px;margin:0 auto;padding:20px 14px}}
.tabs{{display:flex;gap:8px;margin-bottom:20px;flex-wrap:wrap}}
.tab{{padding:10px 18px;background:rgba(45,27,78,.6);border:1px solid rgba(168,85,247,.3);border-radius:11px;color:#d8b4fe;cursor:pointer;font-weight:800;transition:.3s;font-size:13px;font-family:inherit}}
.tab.active,.tab:hover{{background:linear-gradient(90deg,#7e22ce,#a855f7);color:#fff;border-color:#a855f7}}
.panel{{display:none;background:rgba(45,27,78,.35);padding:20px;border-radius:14px;border:1px solid rgba(168,85,247,.25)}}
.panel.active{{display:block;animation:fadeIn .3s}}
@keyframes fadeIn{{from{{opacity:0;transform:translateY(10px)}}to{{opacity:1;transform:none}}}}
h2{{color:#e9d5ff;margin-bottom:15px;font-size:19px;display:flex;align-items:center;gap:7px}}
h3{{color:#d8b4fe;margin:20px 0 10px;font-size:16px}}
label{{display:block;margin:10px 0 4px;color:#c4b5fd;font-weight:700;font-size:13px}}
input,textarea,select{{width:100%;padding:10px 12px;background:rgba(20,10,40,.78);border:1px solid rgba(168,85,247,.3);border-radius:9px;color:#fff;font-size:13px;font-family:inherit}}
input:focus,textarea:focus{{outline:none;border-color:#a855f7;box-shadow:0 0 14px rgba(168,85,247,.35)}}
textarea{{resize:vertical;min-height:70px}}
input[type=file]{{padding:10px;background:rgba(20,10,40,.5);cursor:pointer}}
.btn{{background:linear-gradient(90deg,#7e22ce,#a855f7);padding:10px 22px;border:none;border-radius:9px;color:#fff;font-weight:800;cursor:pointer;font-size:13px;transition:.3s;margin-top:12px;font-family:inherit}}
.btn:hover{{transform:translateY(-2px);box-shadow:0 10px 30px rgba(168,85,247,.5)}}
.btn-danger{{background:linear-gradient(90deg,#b91c1c,#ef4444)}}
.btn-sm{{padding:6px 13px;font-size:12px;margin:2px}}
.list-item{{background:rgba(20,10,40,.65);padding:12px 14px;border-radius:11px;margin-bottom:9px;display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;border:1px solid rgba(168,85,247,.2)}}
.list-item .info{{flex:1;min-width:180px}}
.list-item h4{{color:#e9d5ff;margin-bottom:3px;font-size:14px}}
.list-item small{{color:#9f7aea;font-size:11px;display:block;margin-top:2px;word-break:break-all}}
.stats{{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px;margin-bottom:20px}}
.stat-card{{background:linear-gradient(135deg,rgba(126,34,206,.5),rgba(168,85,247,.3));border:1px solid rgba(192,132,252,.4);border-radius:12px;padding:16px;text-align:center;transition:.3s}}
.stat-card:hover{{transform:translateY(-4px);box-shadow:0 15px 40px rgba(168,85,247,.4)}}
.stat-card .num{{font-size:28px;font-weight:900;background:linear-gradient(90deg,#e9d5ff,#c084fc);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}}
.stat-card .lbl{{font-size:12px;color:#c4b5fd;margin-top:5px}}
.toast{{position:fixed;top:18px;left:50%;transform:translateX(-50%) translateY(-120px);background:linear-gradient(90deg,#7e22ce,#a855f7);padding:12px 24px;border-radius:11px;color:#fff;font-weight:800;transition:.4s cubic-bezier(.34,1.56,.64,1);z-index:9999;box-shadow:0 10px 40px rgba(168,85,247,.6);font-size:13px}}
.toast.show{{transform:translateX(-50%) translateY(0)}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
@media(max-width:600px){{.grid2{{grid-template-columns:1fr}}}}
.upload-progress{{width:100%;height:8px;background:rgba(20,10,40,.8);border-radius:5px;margin-top:8px;overflow:hidden;display:none}}
.upload-progress.active{{display:block}}
.upload-progress .bar{{height:100%;width:0%;background:linear-gradient(90deg,#7e22ce,#a855f7);transition:width .2s;border-radius:5px}}
.toggle-type{{display:flex;gap:8px;margin:10px 0}}
.toggle-type button{{flex:1;padding:10px;background:rgba(20,10,40,.7);border:1px solid rgba(168,85,247,.3);border-radius:9px;color:#c4b5fd;font-weight:800;cursor:pointer;font-size:13px;transition:.2s;font-family:inherit}}
.toggle-type button.active{{background:linear-gradient(90deg,#7e22ce,#a855f7);color:#fff;border-color:#a855f7}}
</style>
</head>
<body>
<header>
  <div class="logo">🎛️ لوحة تحكم ليل بنفسج</div>
  <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
    <span class="user-badge">👤 {esc(username)}</span>
    <a href="/" target="_blank" class="link-btn" style="background:#059669">👁 الموقع</a>
    <a href="/admin/logout" class="link-btn" style="background:#b91c1c">🚪 خروج</a>
  </div>
</header>

<div class="container">
  <div class="stats" id="statsBox"></div>

  <div class="tabs">
    <div class="tab active" onclick="switchTab('site',this)">⚙️ الموقع</div>
    <div class="tab" onclick="switchTab('episodes',this)">🎬 الفيديوهات</div>
    <div class="tab" onclick="switchTab('news',this)">📰 الأخبار</div>
    <div class="tab" onclick="switchTab('admins',this)">👥 الأدمن</div>
    <div class="tab" onclick="switchTab('block',this)">🚫 الحظر</div>
    <div class="tab" onclick="switchTab('announce',this)">📢 الإعلان</div>
  </div>

  <div class="panel active" id="panel-site">
    <h2>⚙️ إعدادات الموقع</h2>
    <label>عنوان المسلسل</label><input id="s-title">
    <label>العنوان الفرعي</label><input id="s-subtitle">
    <label>رابط البوستر (أو ارفع صورة)</label><input id="s-poster">
    <input type="file" id="s-poster-file" accept="image/*" onchange="uploadPoster()">
    <div class="upload-progress" id="posterProgress"><div class="bar"></div></div>
    <label>نبذة عن المسلسل</label><textarea id="s-about"></textarea>
    <label>معرّف الدعم على تيليجرام (بدون @)</label><input id="s-tg">
    <button class="btn" onclick="saveSite()">💾 حفظ</button>
  </div>

  <div class="panel" id="panel-episodes">
    <h2>🎬 إضافة فيديو</h2>
    <div class="toggle-type">
      <button class="active" id="typeUrl" onclick="setEpType('url')">🔗 رابط يوتيوب</button>
      <button id="typeFile" onclick="setEpType('file')">📤 رفع من الجهاز</button>
    </div>
    <label>عنوان الحلقة</label><input id="ep-title" placeholder="الحلقة الرابعة - ...">
    <label>التاريخ</label><input id="ep-date" type="date">
    <label>الوصف</label><textarea id="ep-desc"></textarea>

    <div id="epUrlBox">
      <label>رابط يوتيوب (embed)</label>
      <input id="ep-url" placeholder="https://www.youtube.com/embed/xxxxx">
    </div>
    <div id="epFileBox" style="display:none">
      <label>اختر ملف الفيديو (MP4/WebM — يُرفع بجودة أصلية 100%)</label>
      <input type="file" id="ep-file" accept="video/*">
      <div class="upload-progress" id="epProgress"><div class="bar"></div></div>
    </div>

    <label>صورة مصغرة (اختياري — أو ارفع صورة)</label>
    <input id="ep-thumb" placeholder="https://...">
    <input type="file" id="ep-thumb-file" accept="image/*" onchange="uploadThumb()">
    <div class="upload-progress" id="thumbProgress"><div class="bar"></div></div>

    <button class="btn" onclick="addEpisode()">➕ إضافة الحلقة</button>
    <h3>📋 الحلقات الحالية</h3>
    <div id="epList"></div>
  </div>

  <div class="panel" id="panel-news">
    <h2>📰 إضافة خبر</h2>
    <label>العنوان</label><input id="n-title">
    <label>التاريخ</label><input id="n-date" type="date">
    <label>المحتوى</label><textarea id="n-content"></textarea>
    <button class="btn" onclick="addNews()">➕ إضافة</button>
    <h3>📋 الأخبار</h3>
    <div id="nList"></div>
  </div>

  <div class="panel" id="panel-admins">
    <h2>👥 إدارة الأدمن</h2>
    <div class="grid2">
      <div><label>اسم المستخدم</label><input id="a-user" placeholder="manager"></div>
      <div><label>كلمة المرور</label><input id="a-pass" type="password"></div>
    </div>
    <button class="btn" onclick="addAdmin()">➕ إضافة أدمن</button>
    <h3>📋 قائمة الأدمن</h3>
    <div id="aList"></div>
  </div>

  <div class="panel" id="panel-block">
    <h2>🚫 حظر IP</h2>
    <label>عنوان IP</label><input id="b-ip" placeholder="192.168.1.10">
    <label>السبب</label><input id="b-reason" placeholder="سبب الحظر">
    <button class="btn btn-danger" onclick="blockIp()">🚫 حظر</button>
    <h3>📋 المحظورون</h3>
    <div id="bList"></div>
  </div>

  <div class="panel" id="panel-announce">
    <h2>📢 الإعلان الرسمي</h2>
    <label>نص الإعلان (فارغ = مخفي)</label>
    <textarea id="ann-text" placeholder="🎉 تم إطلاق الحلقة الجديدة!"></textarea>
    <button class="btn" onclick="saveAnnounce()">💾 حفظ الإعلان</button>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
let DATA = {{}};
let CURRENT_USER = "{esc(username)}";
let epType = "url";

function toast(msg){{
  const t = document.getElementById('toast');
  t.textContent = msg; t.classList.add('show');
  setTimeout(()=>t.classList.remove('show'), 2200);
}}
function escapeHtml(s){{
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}}
function switchTab(name, el){{
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  el.classList.add('active');
  document.getElementById('panel-'+name).classList.add('active');
}}
async function api(url, body){{
  const r = await fetch(url, {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify(body)}});
  return await r.json().catch(()=>({{ok:false}}));
}}

function uploadFile(file, endpoint, progressId, callback){{
  const xhr = new XMLHttpRequest();
  const prog = document.getElementById(progressId);
  if(prog){{ prog.classList.add('active'); prog.querySelector('.bar').style.width = '0%'; }}
  xhr.upload.onprogress = (e) => {{
    if(e.lengthComputable && prog){{
      const pct = (e.loaded / e.total) * 100;
      prog.querySelector('.bar').style.width = pct + '%';
    }}
  }};
  xhr.onload = () => {{
    if(prog) prog.classList.remove('active');
    try{{
      const res = JSON.parse(xhr.responseText);
      if(res.ok) callback(res);
      else toast('❌ ' + (res.error || 'فشل الرفع'));
    }}catch(e){{ toast('❌ خطأ في الرفع'); }}
  }};
  xhr.onerror = () => {{
    if(prog) prog.classList.remove('active');
    toast('❌ فشل الاتصال');
  }};
  const fd = new FormData();
  fd.append('file', file);
  xhr.open('POST', endpoint);
  xhr.send(fd);
}}

function uploadPoster(){{
  const f = document.getElementById('s-poster-file').files[0];
  if(!f) return;
  uploadFile(f, '/api/upload/image', 'posterProgress', (res)=>{{
    document.getElementById('s-poster').value = res.url;
    toast('✅ تم رفع البوستر');
  }});
}}

function uploadThumb(){{
  const f = document.getElementById('ep-thumb-file').files[0];
  if(!f) return;
  uploadFile(f, '/api/upload/image', 'thumbProgress', (res)=>{{
    document.getElementById('ep-thumb').value = res.url;
    toast('✅ تم رفع الصورة');
  }});
}}

function setEpType(t){{
  epType = t;
  document.getElementById('typeUrl').classList.toggle('active', t==='url');
  document.getElementById('typeFile').classList.toggle('active', t==='file');
  document.getElementById('epUrlBox').style.display = t==='url' ? 'block' : 'none';
  document.getElementById('epFileBox').style.display = t==='file' ? 'block' : 'none';
}}

async function loadData(){{
  try{{
    const r = await fetch('/api/data');
    DATA = await r.json();
  }}catch(e){{ DATA = {{}}; }}
  renderStats();
  renderSite();
  renderEpisodes();
  renderNews();
  renderAdmins();
  renderBlocked();
  renderAnnounce();
}}

function renderStats(){{
  const st = DATA.stats || {{}};
  document.getElementById('statsBox').innerHTML = `
    <div class="stat-card"><div class="num">${{st.total_views||0}}</div><div class="lbl">👁 المشاهدات</div></div>
    <div class="stat-card"><div class="num">${{st.unique_visitors||0}}</div><div class="lbl">👥 زوار فريدون</div></div>
    <div class="stat-card"><div class="num">${{(DATA.episodes||[]).length}}</div><div class="lbl">🎬 الفيديوهات</div></div>
    <div class="stat-card"><div class="num">${{(DATA.admins||[]).length}}</div><div class="lbl">👤 الأدمن</div></div>
    <div class="stat-card"><div class="num">${{(DATA.blocked||[]).length}}</div><div class="lbl">🚫 محظورون</div></div>
    <div class="stat-card"><div class="num">${{(DATA.news||[]).length}}</div><div class="lbl">📰 الأخبار</div></div>
  `;
}}

function renderSite(){{
  const s = DATA.site || {{}};
  document.getElementById('s-title').value = s.title || '';
  document.getElementById('s-subtitle').value = s.subtitle || '';
  document.getElementById('s-poster').value = s.poster || '';
  document.getElementById('s-about').value = s.about || '';
  document.getElementById('s-tg').value = s.support_telegram || 'Yb_xY';
}}
function renderAnnounce(){{
  document.getElementById('ann-text').value = (DATA.site && DATA.site.announcement) || '';
}}
function renderEpisodes(){{
  const box = document.getElementById('epList');
  const eps = DATA.episodes || [];
  if(!eps.length){{ box.innerHTML = '<p style="color:#9f7aea">لا توجد فيديوهات</p>'; return; }}
  box.innerHTML = eps.map(e=>`
    <div class="list-item">
      <div class="info">
        <h4>${{escapeHtml(e.title)}}</h4>
        <small>📅 ${{escapeHtml(e.date||'')}}</small>
        <small>🔗 ${{escapeHtml((e.url||'').slice(0,60))}}</small>
        <small>👁 المشاهدات: ${{(DATA.views_by_ep && DATA.views_by_ep[e.id]) || 0}}</small>
      </div>
      <div><button class="btn btn-danger btn-sm" onclick="delEp(${{e.id}})">🗑 حذف</button></div>
    </div>`).join('');
}}
function renderNews(){{
  const box = document.getElementById('nList');
  const ns = DATA.news || [];
  if(!ns.length){{ box.innerHTML = '<p style="color:#9f7aea">لا توجد أخبار</p>'; return; }}
  box.innerHTML = ns.map(n=>`
    <div class="list-item">
      <div class="info">
        <h4>${{escapeHtml(n.title)}}</h4>
        <small>📅 ${{escapeHtml(n.date||'')}}</small>
        <small>${{escapeHtml(n.content||'')}}</small>
      </div>
      <div><button class="btn btn-danger btn-sm" onclick="delNews(${{n.id}})">🗑 حذف</button></div>
    </div>`).join('');
}}
function renderAdmins(){{
  const box = document.getElementById('aList');
  const as = DATA.admins || [];
  box.innerHTML = as.map(a=>`
    <div class="list-item">
      <div class="info">
        <h4>👤 ${{escapeHtml(a.username)}} ${{a.username===CURRENT_USER?'<span style="color:#6ee7b7">(أنت)</span>':''}}</h4>
        <small>📅 ${{escapeHtml(a.created||'')}}</small>
      </div>
      <div>${{as.length>1 && a.username!==CURRENT_USER ? `<button class="btn btn-danger btn-sm" onclick="delAdmin('${{escapeHtml(a.username)}}')">🗑</button>` : ''}}</div>
    </div>`).join('');
}}
function renderBlocked(){{
  const box = document.getElementById('bList');
  const bs = DATA.blocked || [];
  if(!bs.length){{ box.innerHTML = '<p style="color:#9f7aea">لا يوجد محظورون</p>'; return; }}
  box.innerHTML = bs.map(b=>`
    <div class="list-item">
      <div class="info">
        <h4>🚫 ${{escapeHtml(b.ip)}}</h4>
        <small>السبب: ${{escapeHtml(b.reason||'—')}}</small>
        <small>📅 ${{escapeHtml(b.date||'')}}</small>
      </div>
      <div><button class="btn btn-sm" style="background:#059669" onclick="unblockIp('${{escapeHtml(b.ip)}}')">✅ فك</button></div>
    </div>`).join('');
}}

async function saveSite(){{
  const s = {{
    title: document.getElementById('s-title').value,
    subtitle: document.getElementById('s-subtitle').value,
    poster: document.getElementById('s-poster').value,
    about: document.getElementById('s-about').value,
    announcement: (DATA.site && DATA.site.announcement) || '',
    support_telegram: document.getElementById('s-tg').value || 'Yb_xY',
  }};
  const r = await api('/api/site', s);
  if(r.ok){{ toast('✅ تم الحفظ'); await loadData(); }} else toast('❌ فشل');
}}

async function saveAnnounce(){{
  const s = Object.assign({{}}, DATA.site || {{}});
  s.announcement = document.getElementById('ann-text').value;
  const r = await api('/api/site', s);
  if(r.ok){{ toast('✅ تم حفظ الإعلان'); await loadData(); }} else toast('❌ فشل');
}}

async function addEpisode(){{
  const ep = {{
    title: document.getElementById('ep-title').value.trim(),
    date: document.getElementById('ep-date').value || new Date().toISOString().slice(0,10),
    desc: document.getElementById('ep-desc').value.trim(),
    thumb: document.getElementById('ep-thumb').value.trim(),
    url: '',
  }};
  if(!ep.title){{ toast('❌ أدخل عنوان الحلقة'); return; }}

  if(epType === 'url'){{
    ep.url = document.getElementById('ep-url').value.trim();
    if(!ep.url){{ toast('❌ أدخل رابط الفيديو'); return; }}
    const r = await api('/api/episode/add', ep);
    if(r.ok){{
      toast('✅ تمت الإضافة');
      ['ep-title','ep-date','ep-desc','ep-thumb','ep-url'].forEach(id=>document.getElementById(id).value='');
      await loadData();
    }} else toast('❌ فشل');
  }} else {{
    const f = document.getElementById('ep-file').files[0];
    if(!f){{ toast('❌ اختر ملف الفيديو'); return; }}
    toast('⏳ جاري الرفع...');
    uploadFile(f, '/api/upload/video', 'epProgress', async (res)=>{{
      ep.url = res.url;
      const r = await api('/api/episode/add', ep);
      if(r.ok){{
        toast('✅ تمت الإضافة');
        ['ep-title','ep-date','ep-desc','ep-thumb'].forEach(id=>document.getElementById(id).value='');
        document.getElementById('ep-file').value = '';
        await loadData();
      }} else toast('❌ فشل الإضافة');
    }});
  }}
}}

async function delEp(id){{
  if(!confirm('تأكيد حذف الفيديو؟')) return;
  const r = await api('/api/episode/delete', {{id}});
  if(r.ok){{ toast('🗑 تم الحذف'); await loadData(); }}
}}

async function addNews(){{
  const n = {{
    title: document.getElementById('n-title').value.trim(),
    date: document.getElementById('n-date').value || new Date().toISOString().slice(0,10),
    content: document.getElementById('n-content').value.trim()
  }};
  if(!n.title){{ toast('❌ أدخل العنوان'); return; }}
  const r = await api('/api/news/add', n);
  if(r.ok){{
    toast('✅ تمت الإضافة');
    ['n-title','n-date','n-content'].forEach(id=>document.getElementById(id).value='');
    await loadData();
  }} else toast('❌ فشل');
}}

async function delNews(id){{
  if(!confirm('تأكيد الحذف؟')) return;
  const r = await api('/api/news/delete', {{id}});
  if(r.ok){{ toast('🗑 تم الحذف'); await loadData(); }}
}}

async function addAdmin(){{
  const u = document.getElementById('a-user').value.trim();
  const p = document.getElementById('a-pass').value;
  if(!u || !p){{ toast('❌ أدخل البيانات'); return; }}
  if(p.length < 4){{ toast('❌ كلمة المرور قصيرة'); return; }}
  const r = await api('/api/admin/add', {{username:u, password:p}});
  if(r.ok){{
    toast('✅ تمت الإضافة');
    document.getElementById('a-user').value = '';
    document.getElementById('a-pass').value = '';
    await loadData();
  }} else toast('❌ ' + (r.error || 'فشل'));
}}

async function delAdmin(u){{
  if(!confirm('تأكيد حذف ' + u + '؟')) return;
  const r = await api('/api/admin/delete', {{username:u}});
  if(r.ok){{ toast('🗑 تم الحذف'); await loadData(); }}
  else toast('❌ ' + (r.error || 'فشل'));
}}

async function blockIp(){{
  const ip = document.getElementById('b-ip').value.trim();
  const reason = document.getElementById('b-reason').value.trim();
  if(!ip){{ toast('❌ أدخل IP'); return; }}
  const r = await api('/api/block', {{ip, reason}});
  if(r.ok){{
    toast('🚫 تم الحظر');
    document.getElementById('b-ip').value = '';
    document.getElementById('b-reason').value = '';
    await loadData();
  }} else toast('❌ ' + (r.error || 'فشل'));
}}

async function unblockIp(ip){{
  const r = await api('/api/unblock', {{ip}});
  if(r.ok){{ toast('✅ تم فك الحظر'); await loadData(); }}
}}

loadData();
</script>
</body>
</html>"""

# ==================== معالج HTTP ====================
ALLOWED_VIDEO_EXT = {".mp4", ".webm", ".ogg", ".mov", ".m4v"}
ALLOWED_IMG_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"}

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def client_ip(self):
        fwd = self.headers.get("X-Forwarded-For")
        if fwd:
            return fwd.split(",")[0].strip()
        return self.client_address[0]

    def send_html(self, html, status=200, extra=None):
        b = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-cache")
        if extra:
            for k, v in extra:
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def send_json(self, obj, status=200):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def redirect(self, url, extra=None):
        self.send_response(302)
        self.send_header("Location", url)
        if extra:
            for k, v in extra:
                self.send_header(k, v)
        self.end_headers()

    def get_cookie(self, name):
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        c = SimpleCookie()
        try:
            c.load(raw)
            if name in c:
                return c[name].value
        except Exception:
            pass
        return None

    def current_user(self):
        token = self.get_cookie("session")
        if not token:
            return None
        sess = SESSIONS.get(token)
        if not sess:
            return None
        if sess["expires"] < time.time():
            del SESSIONS[token]
            return None
        return sess["username"]

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length > 0 else b"{}"
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def read_form(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length > 0 else b""
            q = urllib.parse.parse_qs(raw.decode("utf-8"))
            return {k: v[0] for k, v in q.items()}
        except Exception:
            return {}

    def parse_multipart(self):
        try:
            ctype = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in ctype:
                return None, None, None
            m = re.search(r'boundary=([^;]+)', ctype)
            if not m:
                return None, None, None
            boundary = m.group(1).strip().strip('"').encode()
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_UPLOAD_MB * 1024 * 1024:
                return None, None, "حجم الملف كبير جداً"
            raw = self.rfile.read(length)
            parts = raw.split(b"--" + boundary)
            for part in parts:
                if b"Content-Disposition" not in part:
                    continue
                header_end = part.find(b"\r\n\r\n")
                if header_end == -1:
                    continue
                headers_raw = part[:header_end].decode("utf-8", errors="ignore")
                filename = None
                fn_match = re.search(r'filename="([^"]*)"', headers_raw)
                if fn_match:
                    filename = fn_match.group(1)
                body = part[header_end+4:]
                if body.endswith(b"\r\n"):
                    body = body[:-2]
                if filename:
                    return filename, body, None
            return None, None, "لا يوجد ملف"
        except Exception as e:
            return None, None, str(e)

    def serve_file(self, filepath):
        if not os.path.exists(filepath):
            self.send_json({"error": "not_found"}, 404)
            return
        file_size = os.path.getsize(filepath)
        mime, _ = mimetypes.guess_type(filepath)
        mime = mime or "application/octet-stream"

        range_header = self.headers.get("Range")
        if range_header:
            m = re.match(r'bytes=(\d+)-(\d*)', range_header)
            if m:
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else file_size - 1
                end = min(end, file_size - 1)
                length = end - start + 1
                self.send_response(206)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(length))
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                with open(filepath, "rb") as f:
                    f.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk = f.read(min(65536, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                return
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(file_size))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        with open(filepath, "rb") as f:
            shutil.copyfileobj(f, self.wfile)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        ip = self.client_ip()
        data = load_data()

        if any(b["ip"] == ip for b in data.get("blocked", [])):
            self.send_html("<h1 style='color:#a855f7;text-align:center;margin-top:100px;font-family:sans-serif'>🚫 أنت محظور</h1>", 403)
            return

        if path.startswith("/uploads/"):
            safe_name = os.path.basename(path)
            filepath = os.path.join(UPLOAD_DIR, safe_name)
            if os.path.abspath(filepath).startswith(os.path.abspath(UPLOAD_DIR)):
                self.serve_file(filepath)
            else:
                self.send_json({"error": "forbidden"}, 403)
            return

        if path in ("/", ""):
            self.send_html(render_home(data))
            return
        if path == "/episodes":
            self.send_html(render_episodes_page(data))
            return
        if path == "/news":
            self.send_html(render_news_page(data))
            return
        if path == "/support":
            self.send_html(render_support_page(data))
            return

        if path == "/admin":
            user = self.current_user()
            if not user:
                self.redirect("/admin/login")
                return
            self.send_html(render_admin(user))
            return
        if path == "/admin/login":
            self.send_html(render_login())
            return
        if path == "/admin/logout":
            token = self.get_cookie("session")
            if token and token in SESSIONS:
                del SESSIONS[token]
            self.redirect("/admin/login", [("Set-Cookie", "session=; Path=/; Max-Age=0")])
            return

        if path == "/api/data":
            if not self.current_user():
                self.send_json({"error": "unauthorized"}, 401)
                return
            payload = {
                "site": data["site"],
                "episodes": data["episodes"],
                "news": data["news"],
                "admins": [{"username": a["username"], "created": a.get("created", "")} for a in data["admins"]],
                "blocked": data["blocked"],
                "stats": {"total_views": VIEWS["total"], "unique_visitors": len(VIEWED_UNIQUE)},
                "views_by_ep": VIEWS["episodes"],
            }
            self.send_json(payload)
            return

        self.send_html("<div style='text-align:center;margin-top:100px;font-family:sans-serif;color:#a855f7'><h1>404</h1><a href='/' style='color:#c084fc'>الرئيسية</a></div>", 404)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        ip = self.client_ip()
        data = load_data()

        if any(b["ip"] == ip for b in data.get("blocked", [])):
            self.send_json({"error": "blocked"}, 403)
            return

        if path in ("/api/upload/image", "/api/upload/video"):
            if not self.current_user():
                self.send_json({"ok": False, "error": "unauthorized"}, 401)
                return
            filename, filebody, err = self.parse_multipart()
            if err:
                self.send_json({"ok": False, "error": err})
                return
            if not filename or not filebody:
                self.send_json({"ok": False, "error": "لم يتم استلام الملف"})
                return
            ext = os.path.splitext(filename)[1].lower()
            is_video = path.endswith("/video")
            if is_video and ext not in ALLOWED_VIDEO_EXT:
                self.send_json({"ok": False, "error": "صيغة فيديو غير مدعومة"})
                return
            if not is_video and ext not in ALLOWED_IMG_EXT:
                self.send_json({"ok": False, "error": "صيغة صورة غير مدعومة"})
                return
            if len(filebody) > MAX_UPLOAD_MB * 1024 * 1024:
                self.send_json({"ok": False, "error": "الملف كبير جداً"})
                return
            unique_name = f"{uuid.uuid4().hex}{ext}"
            filepath = os.path.join(UPLOAD_DIR, unique_name)
            try:
                with open(filepath, "wb") as f:
                    f.write(filebody)
            except Exception:
                self.send_json({"ok": False, "error": "فشل حفظ الملف"})
                return
            self.send_json({"ok": True, "url": f"/uploads/{unique_name}"})
            return

        if path == "/admin/login":
            now = time.time()
            LOGIN_ATTEMPTS[ip] = [t for t in LOGIN_ATTEMPTS[ip] if now - t < 300]
            if len(LOGIN_ATTEMPTS[ip]) >= 10:
                self.send_html(render_login("⛔ محاولات كثيرة. انتظر 5 دقائق."), 429)
                return
            LOGIN_ATTEMPTS[ip].append(now)

            form = self.read_form()
            u = form.get("username", "")
            p = form.get("password", "")
            p_hash = hash_pass(p)
            match = next((a for a in data["admins"] if a["username"] == u and a["pass_hash"] == p_hash), None)
            if match:
                token = secrets.token_hex(32)
                SESSIONS[token] = {"username": u, "expires": time.time() + SESSION_HOURS*3600}
                self.redirect("/admin", [("Set-Cookie", f"session={token}; Path=/; HttpOnly; Max-Age={SESSION_HOURS*3600}")])
                return
            self.send_html(render_login("اسم المستخدم أو كلمة المرور غير صحيحة"), 401)
            return

        if path == "/api/track-view":
            payload = self.read_json()
            eid = payload.get("episode_id")
            if eid:
                VIEWS["total"] += 1
                VIEWS["episodes"][str(eid)] = VIEWS["episodes"].get(str(eid), 0) + 1
                VIEWED_UNIQUE.add(f"{ip}:{eid}")
            self.send_json({"ok": True})
            return

        if path.startswith("/api/"):
            if not self.current_user():
                self.send_json({"error": "unauthorized"}, 401)
                return
            payload = self.read_json()

            if path == "/api/site":
                data["site"] = {
                    "title": payload.get("title", ""),
                    "subtitle": payload.get("subtitle", ""),
                    "poster": payload.get("poster", ""),
                    "about": payload.get("about", ""),
                    "announcement": payload.get("announcement", ""),
                    "support_telegram": payload.get("support_telegram", "Yb_xY"),
                }
                save_data(data)
                self.send_json({"ok": True})
                return

            if path == "/api/episode/add":
                nid = max([e["id"] for e in data["episodes"]], default=0) + 1
                data["episodes"].append({
                    "id": nid,
                    "title": payload.get("title", ""),
                    "desc": payload.get("desc", ""),
                    "url": payload.get("url", ""),
                    "date": payload.get("date", ""),
                    "thumb": payload.get("thumb", ""),
                })
                save_data(data)
                self.send_json({"ok": True, "id": nid})
                return

            if path == "/api/episode/delete":
                eid = payload.get("id")
                for ep in data["episodes"]:
                    if ep["id"] == eid and ep.get("url", "").startswith("/uploads/"):
                        fp = os.path.join(UPLOAD_DIR, os.path.basename(ep["url"]))
                        if os.path.exists(fp):
                            try: os.remove(fp)
                            except: pass
                data["episodes"] = [e for e in data["episodes"] if e["id"] != eid]
                save_data(data)
                self.send_json({"ok": True})
                return

            if path == "/api/news/add":
                nid = max([n["id"] for n in data["news"]], default=0) + 1
                data["news"].append({
                    "id": nid,
                    "title": payload.get("title", ""),
                    "content": payload.get("content", ""),
                    "date": payload.get("date", ""),
                })
                save_data(data)
                self.send_json({"ok": True, "id": nid})
                return

            if path == "/api/news/delete":
                nid = payload.get("id")
                data["news"] = [n for n in data["news"] if n["id"] != nid]
                save_data(data)
                self.send_json({"ok": True})
                return

            if path == "/api/admin/add":
                u = (payload.get("username") or "").strip()
                p = payload.get("password") or ""
                if not u or not p:
                    self.send_json({"ok": False, "error": "بيانات ناقصة"})
                    return
                if any(a["username"] == u for a in data["admins"]):
                    self.send_json({"ok": False, "error": "الاسم موجود"})
                    return
                data["admins"].append({
                    "username": u,
                    "pass_hash": hash_pass(p),
                    "created": datetime.now().strftime("%Y-%m-%d")
                })
                save_data(data)
                self.send_json({"ok": True})
                return

            if path == "/api/admin/delete":
                u = payload.get("username")
                if len(data["admins"]) <= 1:
                    self.send_json({"ok": False, "error": "لا يمكن حذف آخر أدمن"})
                    return
                if u == self.current_user():
                    self.send_json({"ok": False, "error": "لا يمكنك حذف حسابك"})
                    return
                data["admins"] = [a for a in data["admins"] if a["username"] != u]
                save_data(data)
                self.send_json({"ok": True})
                return

            if path == "/api/block":
                ip_b = (payload.get("ip") or "").strip()
                reason = payload.get("reason", "")
                if not ip_b:
                    self.send_json({"ok": False, "error": "IP مطلوب"})
                    return
                if any(b["ip"] == ip_b for b in data["blocked"]):
                    self.send_json({"ok": False, "error": "محظور مسبقاً"})
                    return
                data["blocked"].append({"ip": ip_b, "reason": reason, "date": datetime.now().strftime("%Y-%m-%d")})
                save_data(data)
                self.send_json({"ok": True})
                return

            if path == "/api/unblock":
                ip_b = payload.get("ip")
                data["blocked"] = [b for b in data["blocked"] if b["ip"] != ip_b]
                save_data(data)
                self.send_json({"ok": True})
                return

            self.send_json({"error": "not_found"}, 404)
            return

        self.send_json({"error": "not_found"}, 404)


# ==================== التشغيل ====================
if __name__ == "__main__":
    init_db()
    migrate_from_json()
    load_data()
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    print("=" * 60)
    print("  🌙  مسلسل ليل بنفسج — مع قاعدة بيانات SQLite")
    print("=" * 60)
    print(f"  🗄️  قاعدة البيانات: {DB_FILE}")
    print(f"  🏠  الموقع:        http://127.0.0.1:{PORT}/")
    print(f"  🎬  الحلقات:       http://127.0.0.1:{PORT}/episodes")
    print(f"  📰  الأخبار:       http://127.0.0.1:{PORT}/news")
    print(f"  🎧  الدعم:         http://127.0.0.1:{PORT}/support")
    print(f"  🎛️   لوحة التحكم:  http://127.0.0.1:{PORT}/admin")
    print(f"  👤  المستخدم:     a")
    print(f"  🔑  كلمة المرور:  a")
    print("=" * 60)
    with socketserver.ThreadingTCPServer((HOST, PORT), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n👋 تم إيقاف السيرفر")