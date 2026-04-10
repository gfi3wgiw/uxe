import asyncio
import logging
from aiogram import Bot, Dispatcher, F, types
from aiogram.types import Message, CallbackQuery, PreCheckoutQuery, LabeledPrice, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
import json
import os
from datetime import datetime

# ========== КОНФИГ ==========
TOKEN = "8782318331:AAFmqCNQESd1mffcPh5eolXmq98U1fw0cpI"
ADMIN_ID = 8423212939

USERS_FILE = "users.json"
BLOCKED_FILE = "blocked_users.json"

def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_users():
    with open(USERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(users, f, ensure_ascii=False, indent=2)

def load_blocked():
    if os.path.exists(BLOCKED_FILE):
        with open(BLOCKED_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_blocked():
    with open(BLOCKED_FILE, 'w', encoding='utf-8') as f:
        json.dump(blocked_users, f, ensure_ascii=False, indent=2)

users = load_users()
blocked_users = load_blocked()  # {user_id: {"username": "...", "blocked_at": "..."}}

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())
logging.basicConfig(level=logging.INFO)

# ========== FSM СОСТОЯНИЯ ДЛЯ АДМИНКИ ==========
class AdminStates(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_stars_amount = State()
    waiting_for_broadcast_text = State()
    waiting_for_send_text = State()

# ========== КНОПКИ АДМИН-ПАНЕЛИ ==========
def get_admin_keyboard():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Список пользователей", callback_data="admin_users")],
        [InlineKeyboardButton(text="⭐ Отправить запрос на оплату", callback_data="admin_donate")],
        [InlineKeyboardButton(text="💬 Отправить сообщение", callback_data="admin_send")],
        [InlineKeyboardButton(text="📢 Сделать рассылку", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="🚫 Заблокировавшие бота", callback_data="admin_blocked")]
    ])
    return keyboard

# ========== ОТПРАВКА АДМИНУ ==========
async def notify_admin(text: str, reply_markup=None):
    try:
        await bot.send_message(ADMIN_ID, text, reply_markup=reply_markup, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Ошибка отправки админу: {e}")

# ========== ПРОВЕРКА БЛОКИРОВКИ ПРИ ОТПРАВКЕ ==========
async def check_and_handle_block(user_id: int, username: str = None):
    """Проверяет, заблокирован ли бот пользователем (вызывается при ошибке отправки)"""
    user_id_str = str(user_id)
    
    # Если уже в списке заблокированных — не дублируем
    if user_id_str in blocked_users:
        return False
    
    # Добавляем в список заблокированных
    blocked_users[user_id_str] = {
        "id": user_id,
        "username": username or "неизвестно",
        "blocked_at": str(datetime.now()),
        "type": "блокировка"
    }
    save_blocked()
    
    # Уведомляем админа
    await notify_admin(
        f"🚫 **Пользователь заблокировал бота!**\n"
        f"ID: `{user_id}`\n"
        f"Username: @{username or 'нет'}\n"
        f"Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"⚠️ Бот не может отправлять сообщения этому пользователю."
    )
    return True

# ========== ФОНВОЯ ЗАДАЧА ДЛЯ ПРОВЕРКИ БЛОКИРОВКИ ==========
async def check_if_blocked_periodically():
    """Периодически проверяет, не заблокировали ли бота (раз в час)"""
    while True:
        await asyncio.sleep(3600)  # Каждый час
        for user_id_str, user_data in list(users.items()):
            user_id = int(user_id_str)
            try:
                # Пытаемся отправить тестовое сообщение (невидимое для пользователя)
                # Используем chat action, чтобы проверить, доступен ли пользователь
                await bot.send_chat_action(user_id, action="typing")
            except Exception as e:
                if "bot was blocked" in str(e).lower() or "user is deactivated" in str(e).lower():
                    # Пользователь заблокировал бота
                    if user_id_str not in blocked_users:
                        await check_and_handle_block(user_id, user_data.get("username"))
                elif "chat not found" in str(e).lower():
                    # Пользователь удалил чат с ботом
                    if user_id_str not in blocked_users:
                        blocked_users[user_id_str] = {
                            "id": user_id,
                            "username": user_data.get("username"),
                            "blocked_at": str(datetime.now()),
                            "type": "чат удалён"
                        }
                        save_blocked()
                        await notify_admin(
                            f"🗑️ **Пользователь удалил чат с ботом!**\n"
                            f"ID: `{user_id}`\n"
                            f"Username: @{user_data.get('username', 'нет')}"
                        )

# ========== ОБРАБОТЧИК ОШИБОК ОТПРАВКИ ==========
async def safe_send_message(chat_id: int, *args, **kwargs):
    """Безопасная отправка сообщения с обработкой блокировки"""
    try:
        return await bot.send_message(chat_id, *args, **kwargs)
    except Exception as e:
        if "bot was blocked" in str(e).lower():
            await check_and_handle_block(chat_id)
        elif "chat not found" in str(e).lower():
            await check_and_handle_block(chat_id)
        raise e

# ========== КОМАНДА /START ==========
@dp.message(Command("start"))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username or "нет"
    full_name = message.from_user.full_name
    
    # Если пользователь был в чёрном списке блокировки — удаляем оттуда (он разблокировал)
    user_id_str = str(user_id)
    if user_id_str in blocked_users:
        del blocked_users[user_id_str]
        save_blocked()
        await notify_admin(
            f"✅ **Пользователь разблокировал бота!**\n"
            f"ID: `{user_id}`\n"
            f"Username: @{username}\n"
            f"Он снова может получать сообщения."
        )
    
    if user_id_str not in users:
        users[user_id_str] = {
            "id": user_id,
            "username": username,
            "full_name": full_name,
            "first_seen": str(message.date),
            "last_active": str(message.date)
        }
        save_users()
    
    # Если это админ — показываем админ-панель
    if user_id == ADMIN_ID:
        await message.reply(
            "🔐 **Админ-панель бота**\n\nВыберите действие:",
            reply_markup=get_admin_keyboard(),
            parse_mode="Markdown"
        )
    else:
        # Обычный пользователь — тишина, но админу уведомление
        await notify_admin(
            f"🟢 **Новый пользователь запустил бота!**\n"
            f"ID: `{user_id}`\n"
            f"Username: @{username}\n"
            f"Имя: {full_name}"
        )
        # Пользователь ничего не получает

# ========== ОБРАБОТКА КНОПОК АДМИНКИ ==========
@dp.callback_query(F.data.startswith("admin_"))
async def admin_callback(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ Доступ запрещён", show_alert=True)
        return
    
    action = callback.data.split("_")[1]
    
    if action == "users":
        if not users:
            await callback.message.edit_text("📭 Нет пользователей", reply_markup=get_admin_keyboard())
        else:
            user_list = []
            for uid, data in list(users.items())[:20]:
                status = "🚫" if uid in blocked_users else "✅"
                user_list.append(f"{status} `{uid}` | @{data.get('username', 'нет')}")
            
            text = "📋 **Пользователи (первые 20):**\n✅ активные | 🚫 заблокировали\n\n" + "\n".join(user_list)
            await callback.message.edit_text(text, reply_markup=get_admin_keyboard(), parse_mode="Markdown")
    
    elif action == "blocked":
        if not blocked_users:
            await callback.message.edit_text("✅ Нет пользователей, которые заблокировали бота", reply_markup=get_admin_keyboard())
        else:
            blocked_list = []
            for uid, data in blocked_users.items():
                blocked_list.append(f"🚫 `{uid}` | @{data.get('username', 'нет')} | {data.get('blocked_at', '')[:16]}")
            
            text = "🚫 **Пользователи, заблокировавшие бота:**\n\n" + "\n".join(blocked_list)
            await callback.message.edit_text(text, reply_markup=get_admin_keyboard(), parse_mode="Markdown")
    
    elif action == "donate":
        await callback.message.edit_text(
            "💰 **Отправка запроса на оплату звёздами**\n\n"
            "Введите ID пользователя (число) или @username:\n\n"
            "Пример: `8423212939` или `@durov`",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")]
            ]),
            parse_mode="Markdown"
        )
        await state.set_state(AdminStates.waiting_for_user_id)
        await state.update_data(action="donate")
    
    elif action == "send":
        await callback.message.edit_text(
            "✉️ **Отправка сообщения**\n\n"
            "Введите ID пользователя (число) или @username:\n\n"
            "Пример: `8423212939` или `@durov`",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")]
            ])
        )
        await state.set_state(AdminStates.waiting_for_user_id)
        await state.update_data(action="send")
    
    elif action == "broadcast":
        await callback.message.edit_text(
            "📢 **Рассылка всем пользователям**\n\n"
            "⚠️ Сообщение не будет отправлено тем, кто заблокировал бота\n\n"
            "Введите текст рассылки:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")]
            ])
        )
        await state.set_state(AdminStates.waiting_for_broadcast_text)
    
    elif action == "stats":
        total_users = len(users)
        blocked_count = len(blocked_users)
        active_users = total_users - blocked_count
        text = f"📊 **Статистика бота**\n\n"
        text += f"👥 Всего пользователей: {total_users}\n"
        text += f"✅ Активных: {active_users}\n"
        text += f"🚫 Заблокировали бота: {blocked_count}"
        await callback.message.edit_text(text, reply_markup=get_admin_keyboard())
    
    elif action == "back":
        await callback.message.edit_text(
            "🔐 **Админ-панель бота**\n\nВыберите действие:",
            reply_markup=get_admin_keyboard()
        )
        await state.clear()
    
    await callback.answer()

# ========== ОБРАБОТКА ВВОДА ID ПОЛЬЗОВАТЕЛЯ ==========
@dp.message(AdminStates.waiting_for_user_id)
async def process_user_id(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    
    data = await state.get_data()
    action = data.get("action")
    target = message.text.strip()
    
    # Определяем user_id
    target_id = None
    target_username = None
    
    if target.startswith("@"):
        username = target[1:]
        for uid, user_data in users.items():
            if user_data.get("username") == username:
                target_id = int(uid)
                target_username = username
                break
        if not target_id:
            await message.reply(f"❌ Пользователь {target} не найден\nПопробуйте снова или /start для выхода")
            await state.clear()
            return
    else:
        try:
            target_id = int(target)
            # Пытаемся найти username
            if str(target_id) in users:
                target_username = users[str(target_id)].get("username")
        except ValueError:
            await message.reply("❌ Неверный формат. Введите ID числом или @username\nПопробуйте снова:")
            return
    
    # Проверяем, не заблокирован ли пользователь
    if str(target_id) in blocked_users:
        await message.reply(
            f"⚠️ **Внимание!** Пользователь `{target_id}` заблокировал бота.\n"
            f"Сообщения до него не дойдут.\n\n"
            f"Вы всё равно хотите продолжить? (да/нет)",
            parse_mode="Markdown"
        )
        await state.update_data(target_id=target_id, target_username=target_username, action=action, confirmed_blocked=True)
        return
    
    await state.update_data(target_id=target_id, target_username=target_username)
    
    if action == "donate":
        await message.reply(
            f"✅ Выбран пользователь: ID `{target_id}`\n\n"
            f"Введите **количество звёзд** (число) для оплаты:",
            parse_mode="Markdown"
        )
        await state.set_state(AdminStates.waiting_for_stars_amount)
    elif action == "send":
        await message.reply(
            f"✅ Выбран пользователь: ID `{target_id}`\n\n"
            f"Введите текст сообщения для отправки:",
            parse_mode="Markdown"
        )
        await state.set_state(AdminStates.waiting_for_send_text)

# ========== ПОДТВЕРЖДЕНИЕ ОТПРАВКИ ЗАБЛОКИРОВАННОМУ ==========
@dp.message(AdminStates.waiting_for_user_id, lambda m: m.text and m.text.lower() in ["да", "нет"])
async def process_blocked_confirmation(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    
    data = await state.get_data()
    if message.text.lower() == "да":
        target_id = data.get("target_id")
        action = data.get("action")
        await state.update_data(target_id=target_id)
        await state.set_state(AdminStates.waiting_for_stars_amount if action == "donate" else AdminStates.waiting_for_send_text)
        await message.reply(f"⚠️ Продолжаем, но учтите — пользователь может не получить сообщение.\n\nВведите {'количество звёзд' if action == 'donate' else 'текст сообщения'}:")
    else:
        await message.reply("❌ Отменено. Возврат в админ-панель", reply_markup=get_admin_keyboard())
        await state.clear()

# ========== ОБРАБОТКА СУММЫ ЗВЁЗД ==========
@dp.message(AdminStates.waiting_for_stars_amount)
async def process_stars_amount(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    
    try:
        stars = int(message.text.strip())
        if stars <= 0:
            raise ValueError
    except ValueError:
        await message.reply("❌ Введите положительное число звёзд (например: 100)")
        return
    
    data = await state.get_data()
    target_id = data.get("target_id")
    
    # Проверяем блокировку ещё раз
    if str(target_id) in blocked_users:
        await message.reply(f"❌ Невозможно отправить: пользователь `{target_id}` заблокировал бота", parse_mode="Markdown")
        await state.clear()
        return
    
    prices = [LabeledPrice(label="⭐ Звёзды", amount=stars)]
    
    try:
        await bot.send_invoice(
            chat_id=target_id,
            title=f"Пожертвование {stars} ⭐",
            description=f"Вы отправляете {stars} звёзд разработчику",
            payload=f"donate_{stars}_{target_id}",
            provider_token="",
            currency="XTR",
            prices=prices,
            need_name=False,
            need_phone_number=False,
            need_email=False
        )
        
        await message.reply(
            f"✅ **Счёт отправлен!**\n"
            f"Пользователь: `{target_id}`\n"
            f"Сумма: {stars} ⭐\n\n"
            f"Когда пользователь оплатит — пришлю уведомление.",
            parse_mode="Markdown",
            reply_markup=get_admin_keyboard()
        )
        
        await notify_admin(f"💰 Отправлен запрос на оплату {stars} ⭐ пользователю `{target_id}`")
        
    except Exception as e:
        if "bot was blocked" in str(e).lower():
            await check_and_handle_block(target_id)
            await message.reply(f"❌ Пользователь заблокировал бота. Оплата невозможна.")
        else:
            await message.reply(f"❌ Ошибка: {e}")
    
    await state.clear()

# ========== ОБРАБОТКА ТЕКСТА ДЛЯ ОТПРАВКИ ==========
@dp.message(AdminStates.waiting_for_send_text)
async def process_send_text(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    
    data = await state.get_data()
    target_id = data.get("target_id")
    msg_text = message.text.strip()
    
    # Проверяем блокировку
    if str(target_id) in blocked_users:
        await message.reply(f"❌ Невозможно отправить: пользователь `{target_id}` заблокировал бота", parse_mode="Markdown")
        await state.clear()
        return
    
    try:
        await bot.send_message(target_id, msg_text)
        await message.reply(
            f"✅ Сообщение отправлено пользователю `{target_id}`",
            parse_mode="Markdown",
            reply_markup=get_admin_keyboard()
        )
    except Exception as e:
        if "bot was blocked" in str(e).lower():
            await check_and_handle_block(target_id)
            await message.reply(f"❌ Пользователь заблокировал бота. Сообщение не доставлено.")
        else:
            await message.reply(f"❌ Ошибка: {e}")
    
    await state.clear()

# ========== ОБРАБОТКА РАССЫЛКИ (с пропуском заблокировавших) ==========
@dp.message(AdminStates.waiting_for_broadcast_text)
async def process_broadcast(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    
    msg_text = message.text.strip()
    sent = 0
    failed = 0
    blocked = 0
    
    status_msg = await message.reply("📡 Начинаю рассылку...")
    
    for uid, user_data in users.items():
        if uid in blocked_users:  # Пропускаем заблокировавших
            blocked += 1
            continue
        
        try:
            await bot.send_message(int(uid), msg_text)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            if "bot was blocked" in str(e).lower():
                await check_and_handle_block(int(uid), user_data.get("username"))
                blocked += 1
            else:
                failed += 1
    
    await status_msg.edit_text(
        f"✅ **Рассылка завершена**\n"
        f"📨 Отправлено: {sent}\n"
        f"🚫 Пропущено (блокировка): {blocked}\n"
        f"❌ Ошибки: {failed}",
        reply_markup=get_admin_keyboard()
    )
    await state.clear()

# ========== ОБРАБОТКА ПЛАТЕЖА ==========
@dp.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await pre_checkout_query.answer(ok=True)

@dp.message(F.successful_payment)
async def process_successful_payment(message: Message):
    user_id = message.from_user.id
    stars = message.successful_payment.total_amount
    
    await notify_admin(
        f"🎉 **Платёж получен!**\n"
        f"От: @{message.from_user.username or 'нет'} (ID: `{user_id}`)\n"
        f"⭐ {stars} звёзд\n"
        f"Спасибо за поддержку!",
        parse_mode="Markdown"
    )
    
    await message.answer(f"✨ Спасибо за {stars} звёзд! ✨")

# ========== ПЕРЕСЫЛКА СООБЩЕНИЙ ОТ ЮЗЕРОВ ==========
@dp.message()
async def forward_from_user(message: Message):
    if message.from_user.id == ADMIN_ID:
        return
    
    user_id = message.from_user.id
    user_id_str = str(user_id)
    
    # Если пользователь писал — значит он не заблокировал бота
    if user_id_str in blocked_users:
        del blocked_users[user_id_str]
        save_blocked()
        await notify_admin(
            f"✅ **Пользователь разблокировал бота!**\n"
            f"ID: `{user_id}`\n"
            f"Username: @{message.from_user.username or 'нет'}"
        )
    
    if user_id_str not in users:
        users[user_id_str] = {
            "id": user_id,
            "username": message.from_user.username or "",
            "full_name": message.from_user.full_name,
            "first_seen": str(message.date),
            "last_active": str(message.date)
        }
        save_users()
    else:
        users[user_id_str]["last_active"] = str(message.date)
        save_users()
    
    # Отправляем админу
    await notify_admin(
        f"💬 **Сообщение от пользователя**\n"
        f"ID: `{user_id}`\n"
        f"Username: @{message.from_user.username or 'нет'}\n"
        f"Текст: {message.text or '[медиа]'}"
    )
    
    if message.text:
        pass
    elif message.photo:
        await bot.send_photo(ADMIN_ID, message.photo[-1].file_id, caption=f"📸 Фото от {user_id}")
    else:
        await bot.send_message(ADMIN_ID, f"📎 Файл/медиа от {user_id}")

# ========== ЗАПУСК ==========
async def main():
    print("🤖 Бот запущен!")
    
    # Запускаем фоновую проверку блокировок
    asyncio.create_task(check_if_blocked_periodically())
    
    await bot.send_message(ADMIN_ID, "✅ Бот запущен! Нажми /start для админ-панели")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
