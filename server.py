# --- imports ---
import os
import uuid
import hashlib
import requests
from fastapi import FastAPI, Request, HTTPException, Header
import telebot
from telebot import types

# --- env ---
PUBLIC_BASE_URL    = os.getenv("PUBLIC_BASE_URL", "https://alexabot-kg4y.onrender.com")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
MERCHANT_ID        = os.getenv("MERCHANT_ID", "")
SECRET_KEY         = os.getenv("SECRET_KEY", "")
TG_WEBHOOK_SECRET  = os.getenv("TG_WEBHOOK_SECRET", "")

# --- init app & bot (ВАЖНО: app создаём ДО декораторов @app.*) ---
app = FastAPI()
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, threaded=False)

# --- helpers ---
WHITELIST = [
    958579430,
    2095741832,
             ]  # твой chat_id

def has_access(chat_id: int) -> bool:
    return chat_id in WHITELIST

def tg_send(chat_id: int, text: str):
    """Отправка сообщения в Telegram из серверной логики (например, из вебхука Nicepay)."""
    if not TELEGRAM_BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": int(chat_id), "text": text}, timeout=10)
    except Exception:
        pass

# --- core: создание платежа в Nicepay (НЕ ходим к себе по HTTP) ---
def create_payment_core(amount: int, chat_id: int, currency: str = "RUB"):
    # 1) Лимиты (по доке Nicepay)
    if currency == "RUB":
        if amount < 200 or amount > 85000:
            raise HTTPException(400, "Amount must be between 200 and 85000 RUB")
        amount_minor = amount * 100  # копейки
    elif currency == "USD":
        if amount < 10 or amount > 990:
            raise HTTPException(400, "Amount must be between 10 and 990 USD")
        amount_minor = amount * 100  # центы
    else:
        raise HTTPException(400, "Unsupported currency")

    # 2) Генерируем order_id = "<chat_id>-<короткий_uuid>"
    order_id = f"{chat_id}-{uuid.uuid4().hex[:8]}"

    # 3) Запрос в Nicepay
    payload = {
        "merchant_id": MERCHANT_ID,
        "secret":      SECRET_KEY,
        "order_id":    order_id,
        "customer":    f"user_{chat_id}",
        "account":     f"user_{chat_id}",
        "amount":      amount_minor,
        "currency":    currency,
        "description": "Top up from Telegram bot",
        # при желании можно добавить success_url / fail_url:
        # "success_url": f"{PUBLIC_BASE_URL}/health",
        # "fail_url":    f"{PUBLIC_BASE_URL}/health",
    }

    try:
        r = requests.post("https://nicepay.io/public/api/payment", json=payload, timeout=25)
        data = r.json()
    except Exception as e:
        raise HTTPException(502, f"Nicepay request failed: {e}")

    if data.get("status") == "success":
        link = (data.get("data") or {}).get("link")
        if not link:
            raise HTTPException(502, "Nicepay success without link")
        return {"payment_link": link, "order_id": order_id}
    else:
        msg = (data.get("data") or {}).get("message", "Unknown Nicepay error")
        raise HTTPException(400, f"Nicepay error: {msg}")

# --- Telegram handlers ---

# /getid — всегда отвечает (без проверки whitelist)
@bot.message_handler(commands=['getid'])
def getid(message):
    uid = message.chat.id
    uname = f"@{message.from_user.username}" if message.from_user and message.from_user.username else "—"
    bot.send_message(
        message.chat.id,
        f"Ваш chat_id: {uid}\nusername: {uname}"
    )


@bot.message_handler(commands=['start'])
def start(message):
    if not has_access(message.chat.id):
        bot.send_message(message.chat.id, "⛔ У вас нет доступа")
        return
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Оплатить", callback_data="pay_custom"))
    kb.add(types.InlineKeyboardButton("Проснись", callback_data="wake_up"))
    bot.send_message(message.chat.id, "Нажми «Оплатить», затем введи сумму (200–85000 ₽).", reply_markup=kb)

@bot.callback_query_handler(func=lambda call: True)
def callback(call):
    if not has_access(call.message.chat.id):
        bot.answer_callback_query(call.id, "⛔ У вас нет доступа")
        return

    # Диагностика (можно оставить, удобно видеть, что кнопка ловится)
    # bot.send_message(call.message.chat.id, f"Кнопка: {call.data}")

    if call.data == "wake_up":
        bot.answer_callback_query(call.id, "Я на связи ✅")
        return

    if call.data == "pay_custom":
        msg = bot.send_message(call.message.chat.id, "Введи сумму в рублях (200–85000):")
        bot.register_next_step_handler(msg, handle_custom_amount)
        return

def handle_custom_amount(message):
    if not has_access(message.chat.id):
        bot.send_message(message.chat.id, "⛔ У вас нет доступа")
        return
    try:
        amt = int(message.text.strip())
        if amt < 200 or amt > 85000:
            bot.send_message(message.chat.id, "Сумма вне лимитов Nicepay (200–85000 ₽).")
            return
        # Прямой вызов core-функции (без HTTP к себе)
        result = create_payment_core(amt, message.chat.id, "RUB")
        link = result.get("payment_link")
        oid  = result.get("order_id")
        bot.send_message(message.chat.id, f"Ссылка на оплату ({amt} ₽):\n{link}\n\nOrder ID: {oid}")
    except ValueError:
        bot.send_message(message.chat.id, "Введите целое число без копеек.")
    except Exception as e:
        bot.send_message(message.chat.id, f"Ошибка при создании платежа ❌\n{e}")

# --- Telegram webhook endpoint ---
@app.post("/tg-webhook")
async def tg_webhook(request: Request, x_telegram_bot_api_secret_token: str = Header(None)):
    # проверяем секрет (если задан)
    if TG_WEBHOOK_SECRET and x_telegram_bot_api_secret_token != TG_WEBHOOK_SECRET:
        # Тут можно вернуть 403 — Telegram это поймёт как «не наш запрос».
        # Но 403 тоже фиксируется в last_error_message. Оставим как есть.
        return {"ok": True}

    try:
        payload = await request.body()
        update = telebot.types.Update.de_json(payload.decode("utf-8"))
        bot.process_new_updates([update])
    except Exception as e:
        # Логируем, но Telegram всегда отвечаем 200
        print("TG webhook error:", e)

    return {"ok": True}

# --- Nicepay webhook (GET) ---
@app.get("/webhook")
async def nicepay_webhook(request: Request):
    params = dict(request.query_params)
    received_hash = params.pop("hash", None)
    if not received_hash:
        raise HTTPException(400, "hash missing")

    # Проверка подписи: отсортированные значения через {np} + SECRET в конце
    base = "{np}".join([v for _, v in sorted(params.items(), key=lambda x: x[0])] + [SECRET_KEY])
    calc_hash = hashlib.sha256(base.encode()).hexdigest()
    if calc_hash != received_hash:
        raise HTTPException(400, "bad hash")

    result   = params.get("result")
    order_id = params.get("order_id", "")

    # Денежные поля из вебхука
    amount_str = params.get("amount", "0")                 # в минорах (копейки/центы)
    amount_cur = params.get("amount_currency", "")
    profit_str = params.get("profit")                      # может быть None
    profit_cur = params.get("profit_currency")             # может быть None

    # Конвертнём миноры -> нормальный вид для RUB/USD (÷100), иначе оставим как есть
    def minor_to_human(x: str, cur: str) -> str:
        try:
            val = int(x)
        except Exception:
            return x  # если вдруг пришло не число — вернём как есть

    # На практике Nicepay шлёт миноры (×100) для RUB, USD и USDT
        if cur in ("RUB", "USD", "USDT"):
            return f"{val/100:.2f}"

    # если попадётся другая валюта — вернём как есть
        return str(val)


    amount_human = minor_to_human(amount_str, amount_cur)
    profit_human = minor_to_human(profit_str, profit_cur) if profit_str is not None else None

    # Достаём chat_id из order_id вида "<chat_id>-<uuid>"
    chat_id = order_id.split("-", 1)[0] if "-" in order_id else None

    if result == "success" and chat_id:
        if profit_human is not None and profit_cur:
            text = f"✅ Оплата подтверждена. Сумма: {amount_human} {amount_cur} (на счёт: {profit_human} {profit_cur})"
        else:
            text = f"✅ Оплата подтверждена. Сумма: {amount_human} {amount_cur}"
        tg_send(chat_id, text)

    return {"ok": True}


# --- (опционально) ручной роут для браузерной проверки ---
@app.get("/create_payment")
def create_payment(amount: int, chat_id: int, currency: str = "RUB"):
    return create_payment_core(amount, chat_id, currency)

# --- health ---
@app.get("/health")
def health():
    return {"ok": True}
