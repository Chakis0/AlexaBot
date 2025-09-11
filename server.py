# --- imports ---
import os, uuid, hashlib, requests
from fastapi import FastAPI, Request, HTTPException, Header
import telebot
from telebot import types

# --- env ---
PUBLIC_BASE_URL    = os.getenv("PUBLIC_BASE_URL", "https://alexabot-kg4y.onrender.com")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
MERCHANT_ID        = os.getenv("MERCHANT_ID", "")
SECRET_KEY         = os.getenv("SECRET_KEY", "")
TG_WEBHOOK_SECRET  = os.getenv("TG_WEBHOOK_SECRET", "")

# --- init app & bot (ВАЖНО: app = FastAPI() идёт ПЕРЕД всеми @app.*) ---
app = FastAPI()
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, threaded=False)

# --- helpers ---
WHITELIST = [958579430]
def has_access(chat_id: int) -> bool:
    return chat_id in WHITELIST

def tg_send(chat_id: int, text: str):
    if not TELEGRAM_BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": int(chat_id), "text": text}, timeout=10)
    except Exception:
        pass

# --- Telegram handlers (как у тебя) ---
@bot.message_handler(commands=['start'])
def start(message):
    if not has_access(message.chat.id):
        bot.send_message(message.chat.id, "⛔ У вас нет доступа")
        return
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("200 ₽", callback_data="pay_200"),
        types.InlineKeyboardButton("500 ₽", callback_data="pay_500"),
        types.InlineKeyboardButton("1000 ₽", callback_data="pay_1000"),
    )
    kb.add(types.InlineKeyboardButton("Другая сумма", callback_data="pay_custom"))
    kb.add(types.InlineKeyboardButton("Проснись", callback_data="wake_up"))
    bot.send_message(message.chat.id, "Выбери сумму или введи свою:", reply_markup=kb)

@bot.callback_query_handler(func=lambda call: True)
def callback(call):
    if not has_access(call.message.chat.id):
        bot.answer_callback_query(call.id, "⛔ У вас нет доступа")
        return

    if call.data == "wake_up":
        try:
            r = requests.get(f"{PUBLIC_BASE_URL}/health", timeout=8)
            bot.answer_callback_query(call.id, "Сервер проснулся ✅" if r.ok else f"Ответ {r.status_code}")
        except Exception as e:
            bot.answer_callback_query(call.id, f"❌ {e}")

    elif call.data.startswith("pay_"):
        amt = int(call.data.split("_")[1])
        try:
            r = requests.get(f"{PUBLIC_BASE_URL}/create_payment",
                             params={"amount": amt, "chat_id": call.message.chat.id},
                             timeout=20)
            r.raise_for_status()
            data = r.json()
            link = data.get("payment_link"); oid = data.get("order_id")
            if link:
                bot.send_message(call.message.chat.id, f"Ссылка на оплату ({amt} ₽):\n{link}\n\nOrder ID: {oid}")
            else:
                bot.send_message(call.message.chat.id, f"Ответ сервера: {data}")
        except Exception as e:
            bot.send_message(call.message.chat.id, f"Ошибка при создании платежа ❌\n{e}")

    elif call.data == "pay_custom":
        msg = bot.send_message(call.message.chat.id, "Введи сумму в рублях (200–85000):")
        bot.register_next_step_handler(msg, handle_custom_amount)

def handle_custom_amount(message):
    if not has_access(message.chat.id):
        bot.send_message(message.chat.id, "⛔ У вас нет доступа")
        return
    try:
        amt = int(message.text.strip())
        if amt < 200 or amt > 85000:
            bot.send_message(message.chat.id, "Сумма вне лимитов Nicepay (200–85000 ₽).")
            return
        r = requests.get(f"{PUBLIC_BASE_URL}/create_payment",
                         params={"amount": amt, "chat_id": message.chat.id},
                         timeout=20)
        r.raise_for_status()
        data = r.json()
        link = data.get("payment_link"); oid = data.get("order_id")
        if link:
            bot.send_message(message.chat.id, f"Ссылка на оплату ({amt} ₽):\n{link}\n\nOrder ID: {oid}")
        else:
            bot.send_message(message.chat.id, f"Ответ сервера: {data}")
    except ValueError:
        bot.send_message(message.chat.id, "Введите целое число без копеек.")
    except Exception as e:
        bot.send_message(message.chat.id, f"Ошибка при создании платежа ❌\n{e}")

# --- Telegram webhook endpoint (ПОСЛЕ app = FastAPI) ---
@app.post("/tg-webhook")
async def tg_webhook(request: Request, x_telegram_bot_api_secret_token: str = Header(None)):
    if TG_WEBHOOK_SECRET and x_telegram_bot_api_secret_token != TG_WEBHOOK_SECRET:
        raise HTTPException(403, "forbidden")
    payload = await request.body()
    update = telebot.types.Update.de_json(payload.decode("utf-8"))
    bot.process_new_updates([update])
    return {"ok": True}
    print("UPDATE RECEIVED")

# --- Nicepay webhook (оставляем ОДИН раз) ---
@app.get("/webhook")
async def nicepay_webhook(request: Request):
    params = dict(request.query_params)
    received_hash = params.pop("hash", None)
    if not received_hash:
        raise HTTPException(400, "hash missing")
    base = "{np}".join([v for _, v in sorted(params.items(), key=lambda x: x[0])] + [SECRET_KEY])
    calc_hash = hashlib.sha256(base.encode()).hexdigest()
    if calc_hash != received_hash:
        raise HTTPException(400, "bad hash")

    result   = params.get("result")
    order_id = params.get("order_id", "")
    amount   = params.get("amount", "")
    curr     = params.get("amount_currency", "")

    chat_id = order_id.split("-", 1)[0] if "-" in order_id else None
    if result == "success" and chat_id:
        tg_send(chat_id, f"✅ Оплата подтверждена. Сумма: {amount} {curr}")
    return {"ok": True}

# --- Create Payment ---
@app.get("/create_payment")
def create_payment(amount: int, chat_id: int, currency: str = "RUB"):
    if currency == "RUB":
        if amount < 200 or amount > 85000:
            raise HTTPException(400, "Amount must be between 200 and 85000 RUB")
        amount_minor = amount * 100
    elif currency == "USD":
        if amount < 10 or amount > 990:
            raise HTTPException(400, "Amount must be between 10 and 990 USD")
        amount_minor = amount * 100
    else:
        raise HTTPException(400, "Unsupported currency")

    order_id = f"{chat_id}-{uuid.uuid4().hex[:8]}"
    payload = {
        "merchant_id": MERCHANT_ID,
        "secret":      SECRET_KEY,
        "order_id":    order_id,
        "customer":    f"user_{chat_id}",
        "account":     f"user_{chat_id}",
        "amount":      amount_minor,
        "currency":    currency,
        "description": "Top up from Telegram bot",
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

# --- health ---
@app.get("/health")
def health():
    return {"ok": True}
