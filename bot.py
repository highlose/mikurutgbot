import os
import re
import asyncio
import sqlite3
import secrets
import string
from datetime import datetime, timedelta, timezone
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import LabeledPrice, PreCheckoutQuery

# === НАСТРОЙКИ ИЗ ПЕРЕМЕННЫХ RAILWAY ===
BOT_TOKEN  = os.getenv("BOT_TOKEN", "")
ADMIN_IDS  = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "1908250518").split(",") if x.strip()}
API_PORT   = int(os.getenv("PORT", 8080))
STAR_PRICE = int(os.getenv("STAR_PRICE", "5"))   # Цена в Telegram Stars
SUB_DAYS   = int(os.getenv("SUB_DAYS", "30"))    # Срок подписки при покупке

DATA_DIR   = os.getenv("DATA_DIR", ".")
DB_PATH    = os.path.join(DATA_DIR, "quantium.db")
AVATAR_DIR = os.path.join(DATA_DIR, "avatars")

os.makedirs(AVATAR_DIR, exist_ok=True)

if not BOT_TOKEN:
    raise RuntimeError("Не задана переменная окружения BOT_TOKEN!")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# === БАЗА ДАННЫХ (SQLite + авто-миграция) ===
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER UNIQUE,
            username TEXT UNIQUE,
            license_key TEXT UNIQUE,
            hwid TEXT DEFAULT '',
            role TEXT DEFAULT 'User',
            banned INTEGER DEFAULT 0,
            ban_reason TEXT DEFAULT '',
            expires_at TEXT
        )
    """)
    # Добавляем колонку tg_id, если база уже существовала от старой версии
    c.execute("PRAGMA table_info(users)")
    cols = [row[1] for row in c.fetchall()]
    if "tg_id" not in cols:
        c.execute("ALTER TABLE users ADD COLUMN tg_id INTEGER")
    conn.commit()
    conn.close()

def gen_key():
    alphabet = string.ascii_uppercase + string.digits
    parts = [''.join(secrets.choice(alphabet) for _ in range(4)) for _ in range(4)]
    return "-".join(parts)

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

# === КОМАНДЫ ДЛЯ ВСЕХ ИГРОКОВ ===

@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    uid = msg.from_user.id
    text = (
        "<b>Quantium Project</b>\n\n"
        "👤 <b>Команды игрока:</b>\n"
        "📝 <code>/register [ник]</code> — зарегистрировать профиль в чите\n"
        "👤 <code>/profile</code> — посмотреть свой профиль и ключ\n"
        f"⭐ <code>/buy</code> — купить ключ на {SUB_DAYS} дн. за {STAR_PRICE} Stars\n"
        "🆘 <code>/support [текст]</code> — написать в техподдержку\n"
    )
    if is_admin(uid):
        text += (
            "\n🛠 <b>Админ-панель:</b>\n"
            "🔑 <code>/gen [ник] [дней]</code> — создать аккаунт и ключ\n"
            "👑 <code>/setrole [ник] [User/Admin]</code> — сменить статус игрока\n"
            "🖼 <b>Фото с подписью</b> <code>/setava [ник]</code> — поставить аватарку\n"
            "🔨 <code>/ban [ник/ключ] [причина]</code> — забанить\n"
            "✅ <code>/unban [ник/ключ]</code> — разбанить\n"
            "🔄 <code>/resethwid [ник]</code> — сбросить HWID\n"
            "💬 <code>/reply [tg_id] [ответ]</code> — ответить в поддержку\n"
            "📋 <code>/users</code> — список всех аккаунтов"
        )
    await msg.answer(text, parse_mode="HTML")

@dp.message(Command("register"))
async def cmd_register(msg: types.Message):
    args = msg.text.split()
    if len(args) < 2:
        return await msg.answer("Использование: <code>/register твой_ник</code>", parse_mode="HTML")

    username = args[1].strip()
    if not re.match(r"^[A-Za-z0-9_]{3,20}$", username):
        return await msg.answer("❌ Ник должен содержать от 3 до 20 символов (латиница, цифры, <code>_</code>).", parse_mode="HTML")

    tg_id = msg.from_user.id
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("SELECT username FROM users WHERE tg_id = ?", (tg_id,))
    existing = c.fetchone()
    if existing:
        conn.close()
        return await msg.answer(f"ℹ️ У вас уже зарегистрирован профиль: <b>{existing[0]}</b>", parse_mode="HTML")

    try:
        c.execute(
            "INSERT INTO users (tg_id, username, role) VALUES (?, ?, 'User')",
            (tg_id, username)
        )
        conn.commit()
        await msg.answer(
            f"✅ <b>Профиль зарегистрирован!</b>\n"
            f"👤 Ник: <code>{username}</code>\n"
            f"🏷 Статус: <code>User</code>\n\n"
            f"Теперь вы можете приобрести лицензионный ключ командой <code>/buy</code> ({STAR_PRICE} ⭐).",
            parse_mode="HTML"
        )
    except sqlite3.IntegrityError:
        await msg.answer("❌ Этот ник уже занят другим игроком. Выберите другой.")
    finally:
        conn.close()

@dp.message(Command("profile"))
async def cmd_profile(msg: types.Message):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT username, license_key, role, banned, ban_reason, expires_at FROM users WHERE tg_id = ?", (msg.from_user.id,))
    row = c.fetchone()
    conn.close()

    if not row:
        return await msg.answer("❌ У вас не зарегистрирован аккаунт. Сначала зарегистрируйте аккаунт: <code>/register [ник]</code>", parse_mode="HTML")

    uname, key, role, banned, ban_reason, expires_at = row
    if banned:
        status_str = f"🔴 Забанен ({ban_reason})"
    elif key and expires_at:
        status_str = f"🟢 Активна до <code>{expires_at} UTC</code>"
    else:
        status_str = "⚪ Нет активного ключа (купить: /buy)"

    key_str = f"<code>{key}</code>" if key else "<i>отсутствует</i>"
    await msg.answer(
        f"👤 <b>Профиль:</b> <code>{uname}</code>\n"
        f"🏷 <b>Статус:</b> <code>{role}</code>\n"
        f"🔑 <b>Ключ:</b> {key_str}\n"
        f"⏳ <b>Подписка:</b> {status_str}",
        parse_mode="HTML"
    )

# === ОПЛАТА ЗА 5 TELEGRAM STARS ===

@dp.message(Command("buy"))
async def cmd_buy(msg: types.Message):
    tg_id = msg.from_user.id
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT username, banned FROM users WHERE tg_id = ?", (tg_id,))
    row = c.fetchone()
    conn.close()

    # Проверка регистрации профиля перед оплатой
    if not row:
        return await msg.answer(
            "❌ <b>У вас не зарегистрирован аккаунт!</b>\n"
            "Сначала зарегистрируйте аккаунт в чите командой:\n"
            "<code>/register ваш_ник</code>",
            parse_mode="HTML"
        )

    username, banned = row
    if banned:
        return await msg.answer("⛔ Ваш аккаунт заблокирован.")

    await bot.send_invoice(
        chat_id=msg.chat.id,
        title="Quantium Project — License Key",
        description=f"Лицензионный ключ на {SUB_DAYS} дней для профиля {username}",
        payload=f"buy_key:{tg_id}:{username}",
        provider_token="",  # Для Telegram Stars обязательно пустая строка
        currency="XTR",
        prices=[LabeledPrice(label=f"Подписка ({SUB_DAYS} дн.)", amount=STAR_PRICE)]
    )

@dp.pre_checkout_query()
async def process_pre_checkout(query: PreCheckoutQuery):
    # Повторно проверяем, что аккаунт существует перед списанием звёзд
    tg_id = query.from_user.id
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id FROM users WHERE tg_id = ?", (tg_id,))
    exists = c.fetchone()
    conn.close()

    if not exists:
        await query.answer(ok=False, error_message="У вас не зарегистрирован аккаунт! Сначала выполните /register [ник].")
    else:
        await query.answer(ok=True)

@dp.message(F.successful_payment)
async def process_successful_payment(msg: types.Message):
    tg_id = msg.from_user.id
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT username, license_key, expires_at FROM users WHERE tg_id = ?", (tg_id,))
    row = c.fetchone()

    if not row:
        conn.close()
        return await msg.answer("❌ Ошибка: профиль не найден. Обратитесь в /support.")

    username, old_key, old_exp = row
    now = datetime.now(timezone.utc)

    # Если ключ уже был и ещё не истёк — продлеваем его, иначе генерируем новый
    if old_key and old_exp:
        exp_dt = datetime.strptime(old_exp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        base_dt = exp_dt if exp_dt > now else now
        new_exp = (base_dt + timedelta(days=SUB_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
        final_key = old_key
    else:
        new_exp = (now + timedelta(days=SUB_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
        final_key = gen_key()

    c.execute(
        "UPDATE users SET license_key = ?, expires_at = ? WHERE tg_id = ?",
        (final_key, new_exp, tg_id)
    )
    conn.commit()
    conn.close()

    await msg.answer(
        f"🎉 <b>Оплата прошла успешно!</b>\n\n"
        f"👤 Профиль: <code>{username}</code>\n"
        f"🔑 Ваш ключ: <code>{final_key}</code>\n"
        f"⏳ Действует до: <code>{new_exp} UTC</code>\n\n"
        f"Скопируйте ключ и вставьте его в окне входа в чите.",
        parse_mode="HTML"
    )

# === ТЕХПОДДЕРЖКА ===

@dp.message(Command("support"))
async def cmd_support(msg: types.Message):
    args = msg.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip():
        return await msg.answer("Напишите ваш вопрос после команды:\n<code>/support Не получается активировать ключ</code>", parse_mode="HTML")

    user_text = args[1].strip()
    tg_id = msg.from_user.id
    tg_tag = f"@{msg.from_user.username}" if msg.from_user.username else "без юзернейма"

    # Проверяем, есть ли у игрока профиль в чите
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT username, role FROM users WHERE tg_id = ?", (tg_id,))
    prof = c.fetchone()
    conn.close()

    prof_info = f"{prof[0]} ({prof[1]})" if prof else "Не зарегистрирован"

    admin_msg = (
        f"🆘 <b>Новое обращение в поддержку</b>\n"
        f"👤 Игрок: {msg.from_user.full_name} ({tg_tag})\n"
        f"🆔 TG ID: <code>{tg_id}</code>\n"
        f"🎮 Профиль в чите: <code>{prof_info}</code>\n\n"
        f"💬 <b>Сообщение:</b>\n{user_text}\n\n"
        f"↩️ Ответить: <code>/reply {tg_id} текст_ответа</code>"
    )

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, admin_msg, parse_mode="HTML")
        except Exception:
            pass

    await msg.answer("✅ Ваше сообщение отправлено администрации. Ожидайте ответа здесь.")

@dp.message(Command("reply"))
async def cmd_reply(msg: types.Message):
    if not is_admin(msg.from_user.id):
        return
    args = msg.text.split(maxsplit=2)
    if len(args) < 3:
        return await msg.answer("Использование: <code>/reply [tg_id] [текст]</code>", parse_mode="HTML")

    try:
        target_id = int(args[1])
    except ValueError:
        return await msg.answer("❌ TG ID должен быть числом.")

    reply_text = args[2]
    try:
        await bot.send_message(
            target_id,
            f"💬 <b>Ответ от техподдержки Quantium Project:</b>\n\n{reply_text}",
            parse_mode="HTML"
        )
        await msg.answer(f"✅ Ответ отправлен пользователю <code>{target_id}</code>.", parse_mode="HTML")
    except Exception as e:
        await msg.answer(f"❌ Не удалось отправить сообщение: {e}")

# === АДМИН-КОМАНДЫ ===

@dp.message(Command("setrole"))
async def cmd_setrole(msg: types.Message):
    if not is_admin(msg.from_user.id):
        return
    args = msg.text.split()
    if len(args) < 3:
        return await msg.answer("Использование: <code>/setrole [ник] [User или Admin]</code>", parse_mode="HTML")

    username = args[1]
    raw_role = args[2].lower()
    if raw_role in ("admin", "админ"):
        new_role = "Admin"
    elif raw_role in ("user", "users", "юзер"):
        new_role = "User"
    else:
        return await msg.answer("❌ Доступные статусы: <code>User</code> или <code>Admin</code>", parse_mode="HTML")

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE users SET role = ? WHERE username = ?", (new_role, username))
    changed = c.rowcount
    conn.commit()
    conn.close()

    if changed:
        await msg.answer(f"✅ Статус игрока <b>{username}</b> изменён на <code>{new_role}</code>.", parse_mode="HTML")
    else:
        await msg.answer(f"❌ Профиль <code>{username}</code> не найден.", parse_mode="HTML")

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
    c.execute("SELECT id FROM users WHERE username = ?", (username,))
    row = c.fetchone()

    if row:
        # Если игрок уже сам зарегистрировался через /register — выдаём ему ключ
        c.execute("UPDATE users SET license_key = ?, expires_at = ? WHERE username = ?", (key, expires, username))
    else:
        c.execute("INSERT INTO users (username, license_key, expires_at) VALUES (?, ?, ?)", (username, key, expires))

    conn.commit()
    conn.close()

    await msg.answer(
        f"✅ <b>Ключ выдан</b>\n"
        f"👤 Ник: <code>{username}</code>\n"
        f"🔑 Ключ: <code>{key}</code>\n"
        f"⏳ Истекает: <code>{expires} UTC</code>",
        parse_mode="HTML"
    )

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
    c.execute("SELECT username, license_key, role, banned, expires_at FROM users")
    rows = c.fetchall()
    conn.close()

    if not rows:
        return await msg.answer("База пуста.")

    lines = ["<b>Список аккаунтов:</b>\n"]
    for u, k, r, b, exp in rows:
        st = "🔴 BAN" if b else ("🟢 OK" if k else "⚪ NO KEY")
        has_ava = "🖼" if os.path.exists(os.path.join(AVATAR_DIR, f"{u}.jpg")) else "👤"
        key_txt = f"<code>{k}</code>" if k else "без ключа"
        exp_txt = f"до {exp[:10]}" if exp else "нет подписки"
        lines.append(f"{st} {has_ava} | <b>{u}</b> [{r}] | {key_txt} | {exp_txt}")
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

    if not key:
        return web.Response(text="ERR|Empty license key")

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

    if not expires_at:
        conn.close()
        return web.Response(text="ERR|Subscription inactive")

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
