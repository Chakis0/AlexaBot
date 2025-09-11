from fastapi import FastAPI
from fastapi import Request
import os
import hashlib
import requests
from fastapi import Request, HTTPException
import uuid

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
SECRET_KEY = os.getenv("SECRET_KEY", "65xts-M8QBZ-VrzNj-86SSf-px3Sq")
MERCHANT_ID = os.getenv("MERCHANT_ID", "68c23b7d77760ff5c90f8aed")


def tg_send(chat_id: int, text: str):
    """Отправка сообщения самому себе в Telegram из вебхука."""
    if not TELEGRAM_BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": int(chat_id), "text": text}, timeout=10)
    except Exception:
        pass  # в MVP можно не паниковать

app = FastAPI()

@app.get("/webhook")
async def nicepay_webhook(request: Request):
    # 1) Все GET-параметры в словарь
    params = dict(request.query_params)

    # 2) Вытаскиваем подпись и удаляем её из набора
    received_hash = params.pop("hash", None)
    if not received_hash:
        raise HTTPException(400, "hash missing")

    # 3) Сортируем параметры по ключу (алфавит)
    items = sorted(params.items(), key=lambda x: x[0])

    # 4) Собираем строку: значения через {np} + в конец секрет
    base = "{np}".join([v for _, v in items] + [SECRET_KEY])

    # 5) Считаем sha256 и сравниваем
    calc_hash = hashlib.sha256(base.encode()).hexdigest()
    if calc_hash != received_hash:
        raise HTTPException(400, "bad hash")

    # 6) Если всё ок — можно обработать статус и уведомить в TG
    result = params.get("result")              # "success" или "error"
    order_id = params.get("order_id", "")      # мы туда положим chat_id-uuid
    amount   = params.get("amount", "")
    curr     = params.get("amount_currency", "")

    # Достаём chat_id из order_id вида "<chat_id>-<uuid>"
    chat_id = None
    if "-" in order_id:
        chat_id = order_id.split("-", 1)[0]

    if result == "success" and chat_id:
        tg_send(chat_id, f"✅ Оплата подтверждена. Сумма: {amount} {curr}")

    return {"ok": True}

# Список платежей
payments = {}

@app.get("/create_payment")
def create_payment(amount: int, chat_id: int, currency: str = "RUB"):
    """
    Создаёт платёж в Nicepay и возвращает реальную ссылку.
    amount — целое число в валюте (RUB: рубли), мы конвертируем в копейки.
    chat_id — вшиваем в order_id, чтобы по вебхуку знать, кому писать "Оплачено".
    """

    # 1) Лимиты из доки (пример для RUB и USD)
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

    # 2) Собираем order_id вида "<chat_id>-<короткий uuid>"
    order_id = f"{chat_id}-{uuid.uuid4().hex[:8]}"

    # 3) Тело запроса как в доке Nicepay (Create Payment)
    payload = {
        "merchant_id": MERCHANT_ID,
        "secret": SECRET_KEY,
        "order_id": order_id,
        "customer": f"user_{chat_id}",   # в доке — customer; в примере ещё встречается account
        "account":  f"user_{chat_id}",
        "amount": amount_minor,          # копейки/центы
        "currency": currency,            # "RUB" | "USD" | ...
        "description": "Top up from Telegram bot",
        # "success_url": "https://alexabot-kg4y.onrender.com/health",
        # "fail_url":    "https://alexabot-kg4y.onrender.com/health",
    }

    try:
        r = requests.post("https://nicepay.io/public/api/payment", json=payload, timeout=25)
        data = r.json()
    except Exception as e:
        raise HTTPException(502, f"Nicepay request failed: {e}")

    # 4) Разбираем ответ
    if data.get("status") == "success":
        link = (data.get("data") or {}).get("link")
        if not link:
            raise HTTPException(502, "Nicepay success without link")
        return {"payment_link": link, "order_id": order_id}
    else:
        msg = (data.get("data") or {}).get("message", "Unknown Nicepay error")
        raise HTTPException(400, f"Nicepay error: {msg}")


@app.get("/webhook")
async def nicepay_webhook(request: Request):  
    params = request.query_params
    payment_link = params.get("payment_link")
    status = params.get("status")  # можно "success" или "fail"
    
    if payment_link in payments and status == "success":
        payments[payment_link] = "paid"
        print(f"Платёж {payment_link} прошёл успешно ✅")
    
    return {"ok": True}

@app.get("/health")
def health():
    return {"ok": True}