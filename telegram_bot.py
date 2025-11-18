import telebot 
from telebot import types
from supabase import create_client, Client 
import datetime
import time
import json

# --- КОНФИГУРАЦИЯ БОТА И SUPABASE ---

# 1. ВАШ ТЕЛЕГРАМ ТОКЕН
TELEGRAM_BOT_TOKEN = "8372075125:AAF9E9UfGIVIRx_Qzso4SIDSv7wLggxeDkA"
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

# 2. КОНФИГУРАЦИЯ SUPABASE
SUPABASE_URL = "https://sidygugtiwiocbtyveicbv.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImVpZHlhZ2hndXRpZWlwY2J5ZWp2Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjMxMzA5OTgsImV4cCI6MjA3ODcwNjk5OH0.bpYcJ4LeBwWeqhuO6ZlCMDuMNKMyZXI268C1zs8c2Fk" 
ORDERS_TABLE_NAME = "orders" 

# 3. МЕНЮ КАФЕ 
MENU_ITEMS = {
    "B001": {"name": "Завтрак", "price": "12.00"},
    "O001": {"name": "Паста Песто", "price": "15.00"},
    "D001": {"name": "Сахамедовый сок", "price": "5.00"},
}

# --- Инициализация клиента Supabase ---
try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("✅ Инициализация Supabase прошла успешно.")
except Exception as e:
    print(f"❌ Инициализация Supabase провалилась: {e}")
    supabase = None
    
# --- Локальное хранилище для состояния пользователя ---
user_order_data = {}

# --- ШАГ 1: /start и показ меню ---
@bot.message_handler(commands=['start'])
def start_message(message):
    chat_id = message.chat.id
    user_order_data[chat_id] = {"order_details": {}}
    
    markup = types.ReplyKeyboardMarkup(row_width=1)
    
    categories = {
        "B": "Завтраки",
        "O": "Основные Блюда",
        "D": "Напитки"
    }
    
    for prefix, category_name in categories.items():
        markup.add(types.KeyboardButton(category_name))
        
    markup.add(types.KeyboardButton("🛒 Оформить Заказ"))
    
    bot.send_message(
        chat_id, 
        "Привет! Я бот для заказа еды. Выберите категорию или нажмите '🛒 Оформить Заказ'.", 
        reply_markup=markup
    )

# --- ШАГ 2: Выбор категории и показ товаров ---
@bot.message_handler(func=lambda message: message.text in ["Завтраки", "Основные Блюда", "Напитки"])
def show_category(message):
    chat_id = message.chat.id
    
    category_map = {
        "Завтраки": "B",
        "Основные Блюда": "O",
        "Напитки": "D"
    }
    
    prefix = category_map.get(message.text)
    
    if prefix:
        items_list = ""
        markup = types.InlineKeyboardMarkup()
        
        for item_id, item_data in MENU_ITEMS.items():
            if item_id.startswith(prefix):
                items_list += f"*{item_data['name']}* (ID: {item_id})\n"
                items_list += f"Цена: {item_data['price']}\n\n"
                
                markup.add(types.InlineKeyboardButton(
                    f"➕ Добавить {item_data['name']}", 
                    callback_data=f"add_{item_id}"
                ))

        bot.send_message(
            chat_id, 
            f"**Меню ({message.text}):**\n\n{items_list}", 
            reply_markup=markup,
            parse_mode="Markdown"
        )
    
# --- ШАГ 3: Обработка добавления в корзину (Inline-кнопки) ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('add_'))
def add_to_order_callback(call):
    chat_id = call.message.chat.id
    item_id = call.data.split('_')[1]
    item_name = MENU_ITEMS.get(item_id, {}).get("name")
    
    if chat_id not in user_order_data:
        user_order_data[chat_id] = {"order_details": {}}

    current_count = user_order_data[chat_id]["order_details"].get(item_name, 0)
    user_order_data[chat_id]["order_details"][item_name] = current_count + 1
    
    bot.answer_callback_query(call.id, f"✅ Добавлено: {item_name} ({current_count + 1} шт.)")

    show_current_order(call.message)

# --- Функция для отображения текущего заказа ---
def show_current_order(message):
    chat_id = message.chat.id
    order = user_order_data.get(chat_id, {}).get("order_details", {})
    
    if not order:
        text = "Ваш заказ пока пуст."
    else:
        order_list = "\n".join([f"- {name}: {count} шт." for name, count in order.items()])
        text = f"**Ваш текущий заказ:**\n{order_list}\n\nНажмите '🛒 Оформить Заказ' для продолжения."

    bot.send_message(
        chat_id,
        text,
        parse_mode="Markdown"
    )

# --- ШАГ 4: Переход к оформлению заказа ---
@bot.message_handler(func=lambda message: message.text == '🛒 Оформить Заказ')
def checkout_order(message):
    chat_id = message.chat.id
    order = user_order_data.get(chat_id, {}).get("order_details", {})

    if not order:
        bot.send_message(chat_id, "Ваш заказ пуст. Сначала добавьте товары из меню.")
        return

    msg = bot.send_message(
        chat_id, 
        "Введите номер вашей комнаты (например, 546):",
        reply_markup=types.ReplyKeyboardRemove()
    )
    bot.register_next_step_handler(msg, get_guest_name)

# --- ШАГ 5-8: Сбор данных ---
def get_guest_name(message):
    chat_id = message.chat.id
    user_order_data[chat_id]['room_number'] = message.text
    msg = bot.send_message(chat_id, "Введите ваше ФИО:")
    bot.register_next_step_handler(msg, get_phone_number)

def get_phone_number(message):
    chat_id = message.chat.id
    user_order_data[chat_id]['guest_name'] = message.text
    msg = bot.send_message(chat_id, "Введите ваш номер телефона:")
    bot.register_next_step_handler(msg, get_delivery_time)

def get_delivery_time(message):
    chat_id = message.chat.id
    user_order_data[chat_id]['phone_number'] = message.text
    msg = bot.send_message(chat_id, "Введите желаемое время доставки (например, 14:30 или 'сейчас'):")
    bot.register_next_step_handler(msg, get_guest_count)

def get_guest_count(message):
    chat_id = message.chat.id
    user_order_data[chat_id]['delivery_time'] = message.text
    msg = bot.send_message(chat_id, "Введите количество гостей:")
    bot.register_next_step_handler(msg, process_final_step)

# --- ШАГ 9: Финальное подтверждение и сохранение ---
def process_final_step(message):
    chat_id = message.chat.id
    user_order_data[chat_id]['guest_count'] = message.text
    
    if not supabase:
        final_message = "Извините, база данных недоступна. Заказ не может быть оформлен."
    else:
        try:
            order_data = user_order_data[chat_id]
            
            supabase_data = {
                "timestamp": datetime.datetime.now().isoformat(),
                "guest_name": order_data["guest_name"],
                "room_number": order_data["room_number"],
                "phone_number": order_data["phone_number"],
                "delivery_time": order_data["delivery_time"],
                "guest_count": order_data["guest_count"],
                "order_details": json.dumps(order_data["order_details"]), 
                "status": "Новый (Telegram)" 
            }

            response = supabase.table(ORDERS_TABLE_NAME).insert(supabase_data).execute()
            
            if response.data and response.data[0]['id']:
                order_id = response.data[0]['id']
                final_message = f"✅ Ваш заказ №{order_id} принят и сохранен в базе данных! Ожидайте доставку."
            else:
                print(f"❌ Supabase не вернул ID: {response.error}")
                final_message = "❌ Ошибка при сохранении заказа. Попробуйте еще раз или свяжитесь с персоналом."

        except Exception as e:
            print(f"❌ Критическая ошибка при сохранении заказа: {e}")
            final_message = "❌ Произошла внутренняя ошибка при оформлении заказа. Попробуйте снова."

    bot.send_message(chat_id, final_message)
    
    if chat_id in user_order_data:
        del user_order_data[chat_id]
        
    bot.send_message(chat_id, "Вы можете начать новый заказ с команды /start")


# --- Обработка любых других сообщений ---
@bot.message_handler(func=lambda message: True)
def echo_all(message):
    bot.reply_to(message, "Я могу принимать только заказы. Начните с команды /start.")


# --- ЗАПУСК БОТА ---
if __name__ == '__main__':
    print("🤖 Бот запущен. Ожидание входящих сообщений...")
    bot.infinity_polling()