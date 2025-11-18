import os
import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional

import requests
from flask import Flask, request, jsonify
import telebot
from telebot import types
from supabase import create_client, Client

# ---------------------- Настройка логов ----------------------
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger(__name__)

# ---------------------- Конфигурация из окружения ----------------------
TELEGRAM_BOT_TOKEN = os.environ.get('8372075125:AAF9E9UfGIVIRx_Qzso4SIDSv7wLggxeDkA')
SUPABASE_URL = os.environ.get('https://sidygugtiwiocbtyveicbv.supabase.co')
SUPABASE_KEY = os.environ.get('eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImVpZHlhZ2hndXRpZWlwY2J5ZWp2Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjMxMzA5OTgsImV4cCI6MjA3ODcwNjk5OH0.bpYcJ4LeBwWeqhuO6ZlCMDuMNKMyZXI268C1zs8c2Fk')
WEBHOOK_SECRET = os.environ.get('WEBHOOK_SECRET')  # секретный токен для проверки заголовка
WEBHOOK_URL = os.environ.get('WEBHOOK_URL')  # публичный URL сервера (для авт. установки webhook)
PORT = int(os.environ.get('PORT', 5000))

if not TELEGRAM_BOT_TOKEN:
    log.error("TELEGRAM_BOT_TOKEN не задан. Завершение.")
    raise SystemExit(1)

# ---------------------- Инициализация Supabase ----------------------
supabase: Optional[Client] = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        log.info("Supabase client создан успешно.")
    except Exception as e:
        log.exception("Не удалось инициализировать Supabase: %s", e)
        supabase = None
else:
    log.warning("Supabase не настроен (SUPABASE_URL или SUPABASE_KEY отсутствуют)."
                " В этом режиме заказы не будут сохраняться.")

# ---------------------- Меню (пример) ----------------------
MENU_ITEMS = {
    "B001": {"name": "Завтрак", "price": 12.00},
    "O001": {"name": "Паста Песто", "price": 15.00},
    "D001": {"name": "Сахамедовый сок", "price": 5.00},
}

# ---------------------- Инициализация бота и Flask ----------------------
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, threaded=True)
app = Flask(__name__)

# ---------------------- Вспомогательные функции Supabase ----------------------
# Все функции работают только при наличии supabase клиента

DRAFTS_TABLE = 'order_drafts'
ORDERS_TABLE = 'orders'


def upsert_draft(chat_id: int, draft: Dict[str, Any]) -> bool:
    """Вставить или обновить черновик заказа для chat_id."""
    if not supabase:
        log.warning("Supabase не инициализирован, пропуск upsert_draft")
        return False
    try:
        payload = {
            'chat_id': chat_id,
            'draft': draft,
        }
        # Используем upsert по первичному ключу chat_id — таблица должна иметь уникальный chat_id
        res = supabase.table(DRAFTS_TABLE).upsert(payload, on_conflict='chat_id').execute()
        log.debug("upsert_draft response: %s", res)
        return True
    except Exception as e:
        log.exception("Ошибка upsert_draft: %s", e)
        return False


def get_draft(chat_id: int) -> Dict[str, Any]:
    """Получить черновик по chat_id. Если нет — возвращаем пустой шаблон."""
    if not supabase:
        return {"state": "idle", "order_details": {}}
    try:
        res = supabase.table(DRAFTS_TABLE).select('draft').eq('chat_id', chat_id).limit(1).execute()
        if res.data and len(res.data) > 0:
            draft = res.data[0]['draft']
            return draft
    except Exception as e:
        log.exception("Ошибка get_draft: %s", e)
    return {"state": "idle", "order_details": {}}


def delete_draft(chat_id: int) -> bool:
    if not supabase:
        return True
    try:
        _ = supabase.table(DRAFTS_TABLE).delete().eq('chat_id', chat_id).execute()
        return True
    except Exception as e:
        log.exception("Ошибка delete_draft: %s", e)
        return False


def finalize_order(chat_id: int) -> Optional[int]:
    """Переносит черновик в таблицу orders и возвращает id заказа (если удалось)."""
    if not supabase:
        log.warning("Supabase не инициализирован — не могу сохранить заказ")
        return None
    try:
        draft = get_draft(chat_id)
        if not draft or not draft.get('order_details'):
            log.info("Нет черновика или пустой заказ для chat_id=%s", chat_id)
            return None

        order_payload = {
            'chat_id': chat_id,
            'guest_name': draft.get('guest_name'),
            'room_number': draft.get('room_number'),
            'phone_number': draft.get('phone_number'),
            'delivery_time': draft.get('delivery_time'),
            'guest_count': int(draft.get('guest_count') or 1),
            'order_details': draft.get('order_details'),
            'status': 'Новый (Telegram)',
            'created_at': datetime.utcnow().isoformat()
        }

        res = supabase.table(ORDERS_TABLE).insert(order_payload).execute()
        log.debug("finalize_order response: %s", res)
        if res.data and len(res.data) > 0:
            order_id = res.data[0].get('id') or res.data[0].get('order_id')
            # Удаляем черновик
            delete_draft(chat_id)
            return order_id
        else:
            log.error("Не удалось сохранить заказ в Supabase: %s", res.error)
            return None
    except Exception as e:
        log.exception("Ошибка finalize_order: %s", e)
        return None


# ---------------------- Утилиты для работы с черновиком ----------------------

def ensure_draft_exists(chat_id: int) -> Dict[str, Any]:
    draft = get_draft(chat_id)
    if not draft:
        draft = {"state": "idle", "order_details": {}}
        upsert_draft(chat_id, draft)
    return draft


def add_item_to_draft(chat_id: int, item_id: str, qty: int = 1) -> Dict[str, Any]:
    draft = ensure_draft_exists(chat_id)
    order = draft.get('order_details', {})
    item = MENU_ITEMS.get(item_id)
    if not item:
        return draft
    name = item['name']
    order[name] = order.get(name, 0) + qty
    draft['order_details'] = order
    draft['updated_at'] = datetime.utcnow().isoformat()
    upsert_draft(chat_id, draft)
    return draft


def compute_total(order_details: Dict[str, int]) -> float:
    total = 0.0
    for name, qty in order_details.items():
        # найдём цену по имени (не идеально, но для примера подходит)
        price = next((i['price'] for i in MENU_ITEMS.values() if i['name'] == name), 0.0)
        total += price * qty
    return total


# ---------------------- Сообщения и клавиатуры ----------------------

def build_main_keyboard():
    markup = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
    markup.add(types.KeyboardButton('Завтраки'))
    markup.add(types.KeyboardButton('Основные Блюда'))
    markup.add(types.KeyboardButton('Напитки'))
    markup.add(types.KeyboardButton('🛒 Оформить Заказ'))
    return markup


def build_items_markup(prefix: str) -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup()
    for item_id, item in MENU_ITEMS.items():
        if item_id.startswith(prefix):
            markup.add(types.InlineKeyboardButton(f"➕ {item['name']} (${item['price']})", callback_data=f"add:{item_id}"))
    return markup


def format_order_text(order_details: Dict[str, int]) -> str:
    if not order_details:
        return "Ваш заказ пока пуст."
    lines = []
    for name, qty in order_details.items():
        price = next((i['price'] for i in MENU_ITEMS.values() if i['name'] == name), 0.0)
        lines.append(f"- {name} x{qty} = ${price * qty:.2f}")
    total = compute_total(order_details)
    return "Ваш текущий заказ:\n" + "\n".join(lines) + f"\n\nОбщая сумма: ${total:.2f}"


# ---------------------- Обработчики Telegram ----------------------

@bot.message_handler(commands=['start'])
def handle_start(message: types.Message):
    chat_id = message.chat.id
    # Создаём или сбрасываем черновик
    draft = {"state": "choosing", "order_details": {}}
    upsert_draft(chat_id, draft)

    safe_text = (
        "Привет! Я бот для заказа еды. Выберите категорию ниже или нажмите '🛒 Оформить Заказ'."
    )
    bot.send_message(chat_id, safe_text, reply_markup=build_main_keyboard())


@bot.message_handler(func=lambda m: m.text in ['Завтраки', 'Основные Блюда', 'Напитки'])
def handle_category_choice(message: types.Message):
    chat_id = message.chat.id
    mapping = {'Завтраки': 'B', 'Основные Блюда': 'O', 'Напитки': 'D'}
    prefix = mapping.get(message.text)
    markup = build_items_markup(prefix)
    bot.send_message(chat_id, f"Меню — {message.text}:", reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data and call.data.startswith('add:'))
def handle_add_callback(call: types.CallbackQuery):
    try:
        chat_id = call.message.chat.id
        _, item_id = call.data.split(':')
        draft = add_item_to_draft(chat_id, item_id, qty=1)
        bot.answer_callback_query(call.id, text=f"Добавлено: {MENU_ITEMS[item_id]['name']}")

        # Отправляем/обновляем текст с текущим заказом
        order_text = format_order_text(draft.get('order_details', {}))
        bot.send_message(chat_id, order_text)
    except Exception as e:
        log.exception("Ошибка в callback add: %s", e)
        try:
            bot.answer_callback_query(call.id, text="Ошибка при добавлении товара")
        except Exception:
            pass


@bot.message_handler(func=lambda m: m.text == '🛒 Оформить Заказ')
def handle_checkout(message: types.Message):
    chat_id = message.chat.id
    draft = get_draft(chat_id)
    if not draft or not draft.get('order_details'):
        bot.send_message(chat_id, "Ваш заказ пуст. Добавьте товары из меню перед оформлением.")
        return
    # Переводим state и просим номер комнаты
    draft['state'] = 'collect_room'
    upsert_draft(chat_id, draft)
    bot.send_message(chat_id, "Введите номер вашей комнаты (например, 546):", reply_markup=types.ReplyKeyboardRemove())


@bot.message_handler(func=lambda m: True, content_types=['text'])
def handle_text_messages(message: types.Message):
    chat_id = message.chat.id
    text = (message.text or '').strip()
    draft = ensure_draft_exists(chat_id)
    state = draft.get('state', 'idle')

    try:
        if state == 'collect_room':
            draft['room_number'] = text
            draft['state'] = 'collect_name'
            upsert_draft(chat_id, draft)
            bot.send_message(chat_id, "Введите ваше ФИО:")
            return

        if state == 'collect_name':
            draft['guest_name'] = text
            draft['state'] = 'collect_phone'
            upsert_draft(chat_id, draft)
            bot.send_message(chat_id, "Введите ваш номер телефона:")
            return

        if state == 'collect_phone':
            draft['phone_number'] = text
            draft['state'] = 'collect_time'
            upsert_draft(chat_id, draft)
            bot.send_message(chat_id, "Введите желаемое время доставки (например, 14:30 или 'сейчас'):")
            return

        if state == 'collect_time':
            draft['delivery_time'] = text
            draft['state'] = 'collect_guests'
            upsert_draft(chat_id, draft)
            bot.send_message(chat_id, "Введите количество гостей (числом):")
            return

        if state == 'collect_guests':
            if not text.isdigit():
                bot.send_message(chat_id, "Пожалуйста, введите число для количества гостей:")
                return
            draft['guest_count'] = int(text)
            # финализируем
            order_id = finalize_order(chat_id)
            if order_id:
                bot.send_message(chat_id, f"✅ Ваш заказ №{order_id} принят и сохранён. Спасибо!")
            else:
                bot.send_message(chat_id, "❌ Не удалось сохранить заказ. Попробуйте позже или свяжитесь с персоналом.")
            # чистим draft и возвращаем main keyboard
            delete_draft(chat_id)
            bot.send_message(chat_id, "Вы можете начать новый заказ с команды /start", reply_markup=build_main_keyboard())
            return

        # Если состояние idle — помогаем
        bot.send_message(chat_id, "Я могу помочь только с заказами. Начните с /start")
    except Exception as e:
        log.exception("Ошибка при обработке текстового сообщения: %s", e)
        bot.send_message(chat_id, "Произошла ошибка. Попробуйте снова.")


# ---------------------- Flask routes (webhook endpoint) ----------------------

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({'ok': True, 'time': datetime.utcnow().isoformat()}), 200


@app.route('/set_webhook', methods=['POST'])
def set_webhook_route():