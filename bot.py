import os
import asyncio
import sqlite3
import secrets
import string
from datetime import datetime, timedelta, timezone
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command

# === НАСТРОЙКИ ИЗ ПЕРЕМЕННЫХ RAILWAY ===
BOT_TOKEN  = os.getenv("BOT_TOKEN", "")
ADMIN_IDS  = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "1908250518").split(",") if x.strip()}
API_PORT   = int(os.getenv("PORT", 8080))

# На Railway примонтируй Volume в /data и задай переменную DATA_DIR=/data
DATA_DIR   = os.getenv("DATA_DIR", ".")
DB_PATH    = os.path.join(DATA_DIR, "quantium.db")
AVATAR_DIR = os.path.join(DATA_DIR, "avatars")

os.makedirs(AVATAR_DIR, exist_ok=True)

if not BOT_TOKEN:
    raise RuntimeError("Не задана переменная окружения BOT_TOKEN!")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# === БАЗА ДАННЫХ (SQLite) ===
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            license_key TEXT UNIQUE,
            hwid TEXT DEFAULT '',
            role TEXT DEFAULT 'User',
            banned INTEGER DEFAULT 0,
            ban_reason TEXT DEFAULT '',
            expires_at TEXT
        )
    """)
    conn.commit()
    conn.close()

def gen_key():
    alphabet = string.ascii_uppercase + string.digits
    parts = [''.join(secrets.choice(alphabet) for _ in range(4)) for _ in range(4)]
    return "-".join(parts)

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

# === КОМАНДЫ TELEGRAM БОТА ===

@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    if not is_admin(msg.from_user.id):
        return await msg.answer("⛔ Нет доступа.")
    text = (
        "<b>Quantium Project — Admin Panel</b>\n\n"
        "🔑 <code>/gen [ник] [дней]</code> — создать аккаунт и ключ\n"
        "🖼 <b>Отправь фото с подписью</b> <code>/setava [ник]</code> — поставить аватарку\n"
        "🔨 <code>/ban [ник или ключ] [причина]</code> — забанить профиль\n"
        "✅ <code>/unban [ник или ключ]</code> — разбанить профиль\n"
        "🔄 <code>/resethwid [ник]</code> — сбросить привязку HWID\n"
        "📋 <code>/users</code> — список всех аккаунтов"
    )
    await msg.answer(text, parse_mode="HTML")

@dp.message(Command("gen"))
async def cmd_gen(msg: types.Message):
    if not is_admin(msg.from_user.id):
        return
    args = msg.text.split()
    if len(args) < 3:
        return await msg.answer("Использование: <code>/gen highlose 30</code>", parse_mode="HTML")

    username = args[1]
    try:
        days = int(args[2])
    except ValueError:
        return await msg.answer("Количество дней должно быть числом.")

    key = gen_key()
    expires = (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute(
            "INSERT INTO users (username, license_key, expires_at) VALUES (?, ?, ?)",
            (username, key, expires)
        )
        conn.commit()
        await msg.answer(
            f"✅ <b>Аккаунт создан</b>\n"
            f"👤 Ник: <code>{username}</code>\n"
            f"🔑 Ключ: <code>{key}</code>\n"
            f"⏳ Истекает: <code>{expires} UTC</code>",
            parse_mode="HTML"
        )
    except sqlite3.IntegrityError:
        await msg.answer("❌ Пользователь с таким ником уже существует.")
    finally:
        conn.close()

@dp.message(F.photo, F.caption.startswith("/setava"))
async def cmd_setava(msg: types.Message):
    if not is_admin(msg.from_user.id):
        return
    parts = msg.caption.strip().split()
    if len(parts) < 2:
        return await msg.answer("Укажи ник в подписи к фото: <code>/setava highlose</code>", parse_mode="HTML")

    username = parts[1]

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id FROM users WHERE username = ?", (username,))
    exists = c.fetchone()
    conn.close()

    if not exists:
        return await msg.answer(f"❌ Профиль <code>{username}</code> не найден в базе.", parse_mode="HTML")

    photo = msg.photo[-2] if len(msg.photo) >= 2 else msg.photo[-1]
    file_info = await bot.get_file(photo.file_id)

    save_path = os.path.join(AVATAR_DIR, f"{username}.jpg")
    await bot.download_file(file_info.file_path, save_path)

    await msg.answer(f"✅ Аватарка для <b>{username}</b> обновлена!", parse_mode="HTML")

@dp.message(Command("ban"))
async def cmd_ban(msg: types.Message):
    if not is_admin(msg.from_user.id):
        return
    args = msg.text.split(maxsplit=2)
    if len(args) < 2:
        return await msg.answer("Использование: <code>/ban [ник/ключ] [причина]</code>", parse_mode="HTML")

    target = args[1]
    reason = args[2] if len(args) > 2 else "Banned by Admin"

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "UPDATE users SET banned = 1, ban_reason = ? WHERE username = ? OR license_key = ?",
        (reason, target, target)
    )
    changed = c.rowcount
    conn.commit()
    conn.close()

    if changed:
        await msg.answer(f"🔨 Профиль <code>{target}</code> забанен.\nПричина: {reason}", parse_mode="HTML")
    else:
        await msg.answer("❌ Профиль или ключ не найден.")

@dp.message(Command("unban"))
async def cmd_unban(msg: types.Message):
    if not is_admin(msg.from_user.id):
        return
    args = msg.text.split()
    if len(args) < 2:
        return await msg.answer("Использование: <code>/unban [ник/ключ]</code>", parse_mode="HTML")

    target = args[1]
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "UPDATE users SET banned = 0, ban_reason = '' WHERE username = ? OR license_key = ?",
        (target, target)
    )
    changed = c.rowcount
    conn.commit()
    conn.close()

    if changed:
        await msg.answer(f"✅ Профиль <code>{target}</code> разбанен.", parse_mode="HTML")
    else:
        await msg.answer("❌ Профиль не найден.")

@dp.message(Command("resethwid"))
async def cmd_resethwid(msg: types.Message):
    if not is_admin(msg.from_user.id):
        return
    args = msg.text.split()
    if len(args) < 2:
        return await msg.answer("Использование: <code>/resethwid [ник]</code>", parse_mode="HTML")

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE users SET hwid = '' WHERE username = ?", (args[1],))
    changed = c.rowcount
    conn.commit()
    conn.close()

    await msg.answer("✅ HWID сброшен." if changed else "❌ Ник не найден.")

@dp.message(Command("users"))
async def cmd_users(msg: types.Message):
    if not is_admin(msg.from_user.id):
        return
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT username, license_key, banned, expires_at FROM users")
    rows = c.fetchall()
    conn.close()

    if not rows:
        return await msg.answer("База пуста.")

    lines = ["<b>Список аккаунтов:</b>\n"]
    for u, k, b, exp in rows:
        st = "🔴 BAN" if b else "🟢 OK"
        has_ava = "🖼" if os.path.exists(os.path.join(AVATAR_DIR, f"{u}.jpg")) else "👤"
        lines.append(f"{st} {has_ava} | <b>{u}</b> | <code>{k}</code> | до {exp[:10]}")
    await msg.answer("\n".join(lines), parse_mode="HTML")

# === HTTP API ДЛЯ C++ КЛИЕНТА ===
async def handle_health(request: web.Request):
    return web.Response(text="Quantium API is alive")

async def handle_auth(request: web.Request):
    try:
        data = await request.json()
    except Exception:
        return web.Response(text="ERR|Invalid JSON")

    key = data.get("key", "").strip()
    hwid = data.get("hwid", "").strip()

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT username, hwid, role, banned, ban_reason, expires_at FROM users WHERE license_key = ?", (key,))
    row = c.fetchone()

    if not row:
        conn.close()
        return web.Response(text="ERR|Invalid license key")

    username, db_hwid, role, banned, ban_reason, expires_at = row

    if banned:
        conn.close()
        return web.Response(text=f"ERR|Banned: {ban_reason}")

    exp_dt = datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > exp_dt:
        conn.close()
        return web.Response(text="ERR|Subscription expired")

    if not db_hwid:
        c.execute("UPDATE users SET hwid = ? WHERE license_key = ?", (hwid, key))
        conn.commit()
    elif db_hwid != hwid:
        conn.close()
        return web.Response(text="ERR|HWID mismatch")

    conn.close()
    exp_ts = int(exp_dt.timestamp())
    return web.Response(text=f"OK|{username}|{role}|{exp_ts}")

async def handle_avatar(request: web.Request):
    username = os.path.basename(request.match_info.get("username", ""))
    file_path = os.path.join(AVATAR_DIR, f"{username}.jpg")
    if not os.path.isfile(file_path):
        return web.Response(status=404, text="Not found")
    return web.FileResponse(file_path)

async def main():
    init_db()
    app = web.Application()
    app.router.add_get("/", handle_health)
    app.router.add_post("/api/auth", handle_auth)
    app.router.add_get("/api/avatar/{username}", handle_avatar)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", API_PORT)
    await site.start()

    print(f"[+] API запущен на порту {API_PORT}, бот активен...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
