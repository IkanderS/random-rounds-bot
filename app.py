import asyncio
import json
import logging
import random
import aiofiles
import re
import os  # ← ДОБАВЛЕНО для чтения переменных окружения
from datetime import datetime
from collections import defaultdict

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice,
    PreCheckoutQuery
)
from aiogram.utils.deep_linking import create_start_link

# ==================== ЗАГРУЗКА КОНФИГУРАЦИИ ИЗ ОКРУЖЕНИЯ ====================
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("❌ Переменная TELEGRAM_BOT_TOKEN не установлена!")

# Админы — можно указать через запятую в переменной ADMIN_IDS
ADMIN_IDS_STR = os.environ.get("ADMIN_IDS", "1281307220")
ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_STR.split(",")]

# ==================== НАСТРОЙКИ ====================
logging.basicConfig(level=logging.INFO)

FREE_LIMIT = 3
STARS_PER_PACK = 5
BONUS_ROUNDS_PACK = 10
REFERRAL_BONUS = 1

# ==================== БОТ И ДИСПЕТЧЕР ====================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ==================== ХРАНИЛИЩА ====================
pending_rounds = []
moderation_queue = []

user_stats = defaultdict(lambda: {
    "sent_today": 0,
    "last_reset": datetime.now().date(),
    "unlimited": False,
    "paid_rounds": 0,
    "referral_count": 0,
    "referred_by": None,
    "total_sent": 0
})

referrals = defaultdict(list)

STORAGE_FILE = "user_data.json"
PROMO_FILE = "promo_activated.json"
MODERATION_FILE = "pending_moderation.json"
REFERRAL_FILE = "referrals.json"

# ==================== ПРОМОКОДЫ ====================
active_promos = {}
unlimited_users = set()
activated_promos = {}

VALID_PROMOS = {
    "FRIEND2024": "unlimited",
    "TESTMODE": "unlimited",
    "VIPACCESS": "unlimited",
}

# ==================== ПРЕМОДЕРАЦИЯ ====================
BANNED_WORDS = [
    "порно", "секс", "наркотик", "наркота", "травка", "героин", "кокаин",
    "порнуха", "эротика", "интим", "шлюха", "проститутка", "член", "пизда",
    "porn", "sex", "drugs", "cocaine", "heroin", "xxx", "adult"
]

REPLACEMENTS = {
    "0": "о", "1": "и", "3": "е", "4": "ч", "5": "с", "6": "б", "7": "т",
    "8": "в", "9": "д", "@": "а", "$": "с", "!": "и"
}

def normalize_text(text: str) -> str:
    text = text.lower()
    for num, letter in REPLACEMENTS.items():
        text = text.replace(num, letter)
    return text

def contains_banned_words(text: str) -> bool:
    if not text:
        return False
    normalized = normalize_text(text)
    for word in BANNED_WORDS:
        pattern = r'[._\-*\s]*'.join(list(word))
        if re.search(pattern, normalized):
            return True
    return False

# ==================== КЛАВИАТУРЫ ====================
main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🎬 Отправить кружок")],
        [KeyboardButton(text="📦 Очередь"), KeyboardButton(text="📊 Статистика")],
        [KeyboardButton(text="🛒 Купить кружки"), KeyboardButton(text="🎟 Промокод")],
        [KeyboardButton(text="🔗 Реферальная ссылка"), KeyboardButton(text="❓ Помощь")]
    ],
    resize_keyboard=True,
    input_field_placeholder="Выбери действие..."
)

admin_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📋 На модерацию"), KeyboardButton(text="✅ Одобрить все")],
        [KeyboardButton(text="📊 Стата бота"), KeyboardButton(text="👥 Топ рефереров")],
        [KeyboardButton(text="👤 Все пользователи"), KeyboardButton(text="👤 Обычное меню")]
    ],
    resize_keyboard=True
)

# ==================== ЗАГРУЗКА/СОХРАНЕНИЕ ====================
async def load_data():
    global pending_rounds, moderation_queue, referrals, user_stats
    try:
        async with aiofiles.open(STORAGE_FILE, 'r', encoding='utf-8') as f:
            data = json.loads(await f.read())
            pending_rounds = data.get("pending", [])
    except:
        pass

    try:
        async with aiofiles.open(MODERATION_FILE, 'r', encoding='utf-8') as f:
            moderation_queue = json.loads(await f.read())
    except:
        pass

    try:
        async with aiofiles.open(REFERRAL_FILE, 'r', encoding='utf-8') as f:
            data = json.loads(await f.read())
            referrals = defaultdict(list, data.get("referrals", {}))
            for inviter_id, invited_list in referrals.items():
                user_stats[int(inviter_id)]["referral_count"] = len(invited_list)
                for invited_id in invited_list:
                    user_stats[int(invited_id)]["referred_by"] = int(inviter_id)
    except:
        pass

async def save_data():
    async with aiofiles.open(STORAGE_FILE, 'w', encoding='utf-8') as f:
        await f.write(json.dumps(pending_rounds, ensure_ascii=False))
    async with aiofiles.open(MODERATION_FILE, 'w', encoding='utf-8') as f:
        await f.write(json.dumps(moderation_queue, ensure_ascii=False))
    ref_data = {"referrals": dict(referrals)}
    async with aiofiles.open(REFERRAL_FILE, 'w', encoding='utf-8') as f:
        await f.write(json.dumps(ref_data, ensure_ascii=False))

async def load_promos():
    global activated_promos, unlimited_users
    try:
        async with aiofiles.open(PROMO_FILE, 'r', encoding='utf-8') as f:
            data = json.loads(await f.read())
            activated_promos = data.get("activated", {})
            unlimited_users = set(data.get("unlimited", []))
            for uid in unlimited_users:
                user_stats[int(uid)]["unlimited"] = True
    except:
        pass

async def save_promos():
    data = {"activated": activated_promos, "unlimited": list(unlimited_users)}
    async with aiofiles.open(PROMO_FILE, 'w', encoding='utf-8') as f:
        await f.write(json.dumps(data, ensure_ascii=False, indent=2))

# ==================== КОМАНДЫ ====================
@dp.message(Command("start"))
async def cmd_start(message: Message, command: CommandObject):
    user_id = message.from_user.id
    args = command.args

    if args and args.startswith("ref_"):
        try:
            inviter_id = int(args.replace("ref_", ""))
            if inviter_id != user_id and user_id not in referrals.get(inviter_id, []):
                referrals[inviter_id].append(user_id)
                user_stats[inviter_id]["referral_count"] = len(referrals[inviter_id])
                user_stats[inviter_id]["paid_rounds"] += REFERRAL_BONUS
                user_stats[user_id]["referred_by"] = inviter_id
                await save_data()
                try:
                    await bot.send_message(
                        inviter_id,
                        f"🎉 По твоей ссылке присоединился новый пользователь!\n"
                        f"💰 Ты получил +{REFERRAL_BONUS} кружок!"
                    )
                except:
                    pass
        except ValueError:
            pass

    is_unlimited = user_stats[user_id]["unlimited"]
    status_text = "🌟 *Безлимитный доступ*" if is_unlimited else f"🆓 *Бесплатно:* {FREE_LIMIT} кружков/день"

    await message.answer(
        "🎙 *Кружочки по рандому*\n\n"
        "Отправь видео-кружок, и он улетит случайному человеку.\n"
        "Взамен получишь чужой кружок из очереди!\n\n"
        f"{status_text}\n"
        f"💰 *Платные:* {STARS_PER_PACK} ⭐ за {BONUS_ROUNDS_PACK} кружков\n"
        f"🔗 *Реферал:* +{REFERRAL_BONUS} кружок за друга\n\n"
        "👇 Используй кнопки ниже",
        parse_mode="Markdown",
        reply_markup=main_keyboard
    )

@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id in ADMIN_IDS:
        await message.answer("🔧 Админ-панель", reply_markup=admin_keyboard)

@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "❓ *Помощь*\n\n"
        "1️⃣ Отправь кружок — получишь случайный в ответ\n"
        f"2️⃣ Лимит: {FREE_LIMIT} бесплатных в день\n"
        f"3️⃣ Платные: {STARS_PER_PACK} ⭐ за {BONUS_ROUNDS_PACK} кружков\n"
        "4️⃣ Промокод — безлимит навсегда\n"
        f"5️⃣ Реферал: +{REFERRAL_BONUS} кружок за друга\n\n"
        "⚠️ Запрещён 18+ контент — бот проверяет!",
        parse_mode="Markdown"
    )

# ==================== КНОПКИ МЕНЮ ====================
@dp.message(F.text == "🎬 Отправить кружок")
async def button_send(message: Message):
    await message.answer("🎬 Запиши и отправь мне видео-кружок!")

@dp.message(F.text == "📦 Очередь")
async def button_queue(message: Message):
    await message.answer(
        f"📦 В очереди: *{len(pending_rounds)}*\n"
        f"⏳ На модерации: *{len(moderation_queue)}*",
        parse_mode="Markdown"
    )

@dp.message(F.text == "📊 Статистика")
async def button_stats(message: Message):
    user_id = message.from_user.id
    stats = user_stats[user_id]
    today = datetime.now().date()
    if stats["last_reset"] < today:
        stats["sent_today"] = 0
        stats["last_reset"] = today

    if stats["unlimited"]:
        text = (f"🌟 *Безлимит*\n"
                f"🎬 Отправлено сегодня: {stats['sent_today']}\n"
                f"📤 Всего отправлено: {stats.get('total_sent', 0)}\n"
                f"👥 Приглашено друзей: {stats['referral_count']}")
    else:
        free_left = max(0, FREE_LIMIT - stats["sent_today"])
        text = (f"🆓 Бесплатно: {free_left}/{FREE_LIMIT}\n"
                f"💰 Куплено: {stats['paid_rounds']}\n"
                f"👥 Приглашено друзей: {stats['referral_count']}\n"
                f"📤 Всего отправлено: {stats.get('total_sent', 0)}")

    await message.answer(f"📊 *Статистика*\n\n{text}", parse_mode="Markdown")

@dp.message(F.text == "🔗 Реферальная ссылка")
async def button_referral(message: Message):
    user_id = message.from_user.id
    ref_link = await create_start_link(bot, f"ref_{user_id}", encode=True)

    await message.answer(
        f"🔗 *Твоя реферальная ссылка:*\n\n"
        f"`{ref_link}`\n\n"
        f"Отправь её другу. Когда он запустит бота по этой ссылке — ты получишь *+{REFERRAL_BONUS}* кружок!\n\n"
        f"👥 Ты уже пригласил: *{user_stats[user_id]['referral_count']}*",
        parse_mode="Markdown"
    )

@dp.message(F.text == "❓ Помощь")
async def button_help(message: Message):
    await cmd_help(message)

@dp.message(F.text == "🎟 Промокод")
async def button_promo(message: Message):
    await message.answer(
        "🎟 Отправь промокод в ответном сообщении.\nНапример: `2026`",
        parse_mode="Markdown"
    )

# ==================== ПОКУПКА КРУЖКОВ ====================
@dp.message(F.text == "🛒 Купить кружки")
async def button_buy(message: Message):
    prices = [LabeledPrice(label=f"{BONUS_ROUNDS_PACK} кружков", amount=STARS_PER_PACK)]

    await message.answer_invoice(
        title="🎬 Кружки для обмена",
        description=f"Пакет из {BONUS_ROUNDS_PACK} кружков",
        payload="buy_rounds_pack",
        provider_token="",
        currency="XTR",
        prices=prices,
        start_parameter="buy_rounds",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"💫 Купить за {STARS_PER_PACK} ⭐", pay=True)]
        ])
    )

@dp.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def process_successful_payment(message: Message):
    user_id = message.from_user.id
    user_stats[user_id]["paid_rounds"] += BONUS_ROUNDS_PACK

    await message.answer(
        f"✅ *Оплата прошла успешно!*\n\n"
        f"➕ Получено *{BONUS_ROUNDS_PACK}* кружков!\n"
        f"💰 Баланс купленных: *{user_stats[user_id]['paid_rounds']}*",
        parse_mode="Markdown"
    )

# ==================== ПРОМОКОДЫ ====================
@dp.message(F.text)
async def handle_promo_input(message: Message):
    promo = message.text.upper()
    if promo not in VALID_PROMOS:
        return

    user_id = message.from_user.id
    if str(user_id) in activated_promos:
        await message.answer("⚠️ У тебя уже активирован промокод!", reply_markup=main_keyboard)
        return

    if promo in active_promos:
        await message.answer("❌ Этот промокод уже использован!", reply_markup=main_keyboard)
        return

    active_promos[promo] = {"activated_by": user_id, "activated_at": datetime.now().isoformat()}
    activated_promos[str(user_id)] = {"promo": promo, "activated_at": datetime.now().isoformat()}
    unlimited_users.add(user_id)
    user_stats[user_id]["unlimited"] = True
    await save_promos()

    await message.answer(
        "✅ *Промокод активирован!*\n🎉 У тебя безлимитный доступ навсегда!",
        parse_mode="Markdown",
        reply_markup=main_keyboard
    )

# ==================== ОБРАБОТКА КРУЖКОВ ====================
@dp.message(F.video_note)
async def handle_round(message: Message):
    user_id = message.from_user.id
    file_id = message.video_note.file_id
    caption = message.caption or ""
    today = datetime.now().date()

    stats = user_stats[user_id]
    if stats["last_reset"] < today:
        stats["sent_today"] = 0
        stats["last_reset"] = today

    if contains_banned_words(caption):
        await message.answer("⚠️ Твой кружок отправлен на модерацию.")
        moderation_queue.append({"file_id": file_id, "from_user": user_id, "message_id": message.message_id})
        await save_data()
        for admin_id in ADMIN_IDS:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve_{len(moderation_queue) - 1}"),
                 InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{len(moderation_queue) - 1}")]
            ])
            try:
                await bot.send_video_note(admin_id, file_id, caption=f"⚠️ Модерация\nОт: {user_id}", reply_markup=kb)
            except:
                pass
        return

    if not stats["unlimited"]:
        if stats["sent_today"] >= FREE_LIMIT:
            if stats["paid_rounds"] <= 0:
                await message.answer(
                    f"⚠️ Бесплатный лимит исчерпан ({FREE_LIMIT}/день).\n"
                    f"🛒 Купи кружки, пригласи друга или активируй промокод!",
                    reply_markup=main_keyboard
                )
                return
            else:
                stats["paid_rounds"] -= 1
        else:
            stats["sent_today"] += 1
    else:
        stats["sent_today"] += 1

    stats["total_sent"] = stats.get("total_sent", 0) + 1

    user_rounds = [r for r in pending_rounds if r["from_user"] == user_id]
    max_in_queue = 5 if stats["unlimited"] else 3
    if len(user_rounds) >= max_in_queue:
        await message.answer(f"⚠️ У тебя уже {max_in_queue} кружков в очереди. Дождись!")
        return

    round_data = {"file_id": file_id, "from_user": user_id}
    available_rounds = [r for r in pending_rounds if r["from_user"] != user_id]

    if available_rounds:
        random_round = random.choice(available_rounds)
        pending_rounds.remove(random_round)
        await message.answer_video_note(random_round["file_id"], caption="🎁 Вот кружок для тебя!")
        pending_rounds.append(round_data)
        await save_data()

        remaining = FREE_LIMIT - stats["sent_today"] if not stats["unlimited"] else "∞"
        paid_info = f"\n💰 Купленных: {stats['paid_rounds']}" if stats["paid_rounds"] > 0 else ""
        await message.answer(f"✅ Принято!\n🆓 Осталось: {remaining}{paid_info}", parse_mode="Markdown")
    else:
        pending_rounds.append(round_data)
        await save_data()
        await message.answer("📥 Принято! Жди ответный кружок.")

# ==================== МОДЕРАЦИЯ ====================
@dp.callback_query(F.data.startswith("approve_"))
async def approve_round(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Нет доступа")
        return

    idx = int(callback.data.split("_")[1])
    if idx < len(moderation_queue):
        item = moderation_queue.pop(idx)
        pending_rounds.append({"file_id": item["file_id"], "from_user": item["from_user"]})
        await save_data()
        await callback.message.edit_caption(caption="✅ Одобрено!")
        try:
            await bot.send_message(item["from_user"], "✅ Ваш кружок прошёл модерацию!")
        except:
            pass
    await callback.answer("✅ Одобрено")

@dp.callback_query(F.data.startswith("reject_"))
async def reject_round(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Нет доступа")
        return

    idx = int(callback.data.split("_")[1])
    if idx < len(moderation_queue):
        item = moderation_queue.pop(idx)
        await save_data()
        await callback.message.edit_caption(caption="❌ Отклонено!")
        try:
            await bot.send_message(item["from_user"], "❌ Ваш кружок отклонён.")
        except:
            pass
    await callback.answer("❌ Отклонено")

# ==================== АДМИН-КОМАНДЫ ====================
@dp.message(F.text == "📋 На модерацию")
async def admin_moderation_list(message: Message):
    print(f"🔍 Кнопка 'На модерацию' нажата пользователем {message.from_user.id}")
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ У вас нет доступа к админ-панели")
        return
    
    if not moderation_queue:
        await message.answer("✅ Нет кружков на модерации")
    else:
        await message.answer(f"📋 На модерации: {len(moderation_queue)} кружков")
        for idx, item in enumerate(moderation_queue[:5]):
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve_{idx}"),
                 InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{idx}")]
            ])
            try:
                await message.answer_video_note(
                    item["file_id"], 
                    caption=f"От пользователя: {item['from_user']}", 
                    reply_markup=kb
                )
            except Exception as e:
                await message.answer(f"❌ Ошибка при отправке кружка: {e}")

@dp.message(F.text == "✅ Одобрить все")
async def admin_approve_all(message: Message):
    print(f"🔍 Кнопка 'Одобрить все' нажата пользователем {message.from_user.id}")
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ У вас нет доступа к админ-панели")
        return
    
    if not moderation_queue:
        await message.answer("✅ Нет кружков на модерации")
        return
    
    count = len(moderation_queue)
    for item in moderation_queue:
        pending_rounds.append({"file_id": item["file_id"], "from_user": item["from_user"]})
        try:
            await bot.send_message(item["from_user"], "✅ Ваш кружок прошёл модерацию!")
        except:
            pass
    
    moderation_queue.clear()
    await save_data()
    await message.answer(f"✅ Одобрено {count} кружков!")

@dp.message(F.text == "👤 Обычное меню")
async def admin_normal_menu(message: Message):
    print(f"🔍 Кнопка 'Обычное меню' нажата пользователем {message.from_user.id}")
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ У вас нет доступа к админ-панели")
        return
    
    await message.answer("👤 Переключено на обычное меню", reply_markup=main_keyboard)

@dp.message(F.text == "📊 Стата бота")
async def admin_bot_stats(message: Message):
    print(f"🔍 Кнопка 'Стата бота' нажата пользователем {message.from_user.id}")
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ У вас нет доступа к админ-панели")
        return

    total_users = len(user_stats)
    unlimited_count = len(unlimited_users)
    total_referrals = sum(len(v) for v in referrals.values())
    total_sent = sum(s.get("total_sent", 0) for s in user_stats.values())

    await message.answer(
        f"📊 *Статистика бота*\n\n"
        f"👥 Всего пользователей: {total_users}\n"
        f"🌟 Безлимитных: {unlimited_count}\n"
        f"🔗 Всего рефералов: {total_referrals}\n"
        f"📤 Всего отправлено: {total_sent}\n"
        f"📦 В очереди: {len(pending_rounds)}\n"
        f"⏳ На модерации: {len(moderation_queue)}",
        parse_mode="Markdown"
    )

@dp.message(F.text == "👥 Топ рефереров")
async def admin_top_referrers(message: Message):
    print(f"🔍 Кнопка 'Топ рефереров' нажата пользователем {message.from_user.id}")
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ У вас нет доступа к админ-панели")
        return

    sorted_users = sorted(user_stats.items(), key=lambda x: x[1]["referral_count"], reverse=True)[:10]

    text = "👥 *Топ-10 рефереров:*\n\n"
    has_refs = False
    for i, (user_id, stats) in enumerate(sorted_users, 1):
        if stats["referral_count"] > 0:
            text += f"{i}. `{user_id}` — {stats['referral_count']} приглашено\n"
            has_refs = True

    if not has_refs:
        text += "Пока никто никого не пригласил 🥲"

    await message.answer(text, parse_mode="Markdown")

@dp.message(F.text == "👤 Все пользователи")
async def admin_all_users(message: Message):
    print(f"🔍 Кнопка 'Все пользователи' нажата пользователем {message.from_user.id}")
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ У вас нет доступа к админ-панели")
        return

    if not user_stats:
        await message.answer("👥 Пока нет пользователей")
        return

    text = f"👥 *Всего пользователей: {len(user_stats)}*\n\n"
    for user_id, stats in list(user_stats.items())[:20]:
        status = "🌟" if stats["unlimited"] else "👤"
        text += f"{status} `{user_id}`: {stats.get('total_sent', 0)} отправлено\n"

    if len(user_stats) > 20:
        text += f"\n... и ещё {len(user_stats) - 20}"

    await message.answer(text, parse_mode="Markdown")

# ==================== ЗАПУСК ====================
async def main():
    print("🚀 Загружаем данные...")
    await load_data()
    await load_promos()
    print(f"✅ В очереди: {len(pending_rounds)}, на модерации: {len(moderation_queue)}")
    print(f"👥 Пользователей: {len(user_stats)}, рефералов: {sum(len(v) for v in referrals.values())}")
    print("🤖 Бот запущен!")

    try:
        await dp.start_polling(bot)
    except Exception as e:
        logging.error(f"Ошибка: {e}")

if __name__ == "__main__":
    asyncio.run(main())
