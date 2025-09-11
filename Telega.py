import telebot
from telebot import types
import requests

# Токен бота
TOKEN = "8327166939:AAHkKaYzsob_B8bKyH2n25gvURpaEMsMLtY"
bot = telebot.TeleBot(TOKEN)

# Белый список пользователей (только ты)
WHITELIST = [
    958579430,  # твой chat_id
]

# Проверка доступа
def has_access(chat_id):
    return chat_id in WHITELIST

# /start
@bot.message_handler(commands=['start'])
def start(message):
    if not has_access(message.chat.id):
        bot.send_message(message.chat.id, "⛔ У вас нет доступа")
        return

    markup = types.InlineKeyboardMarkup()
    wake_button = types.InlineKeyboardButton("Проснись", callback_data="wake_up")
    pay_button = types.InlineKeyboardButton("Оплатить", callback_data="pay")
    markup.add(wake_button, pay_button)

    bot.send_message(message.chat.id, "Привет! Используй кнопки ниже:", reply_markup=markup)

# Обработчик кнопок
@bot.callback_query_handler(func=lambda call: True)
def callback(call):
    if not has_access(call.message.chat.id):
        bot.answer_callback_query(call.id, "⛔ У вас нет доступа")
        return

    if call.data == "wake_up":
        try:
            # dummy-запрос для пробуждения сервера
            requests.get("http://127.0.0.1:8000/create_payment?amount=1", timeout=5)
            bot.answer_callback_query(call.id, "Сервер проснулся ✅")
        except Exception as e:
            bot.answer_callback_query(call.id, f"Не удалось разбудить сервер ❌\n{e}")

    elif call.data == "pay":
        try:
            # запрос к тестовому серверу для создания платежа
            response = requests.get("http://127.0.0.1:8000/create_payment?amount=500")
            link = response.json().get("payment_link")
            bot.send_message(call.message.chat.id, f"Ссылка на тестовую оплату:\n{link}")
        except Exception as e:
            bot.send_message(call.message.chat.id, f"Ошибка при создании платежа ❌\n{e}")

# Запуск бота
bot.infinity_polling()