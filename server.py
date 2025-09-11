from fastapi import FastAPI
from fastapi import Request
import os
import hashlib
import requests
from fastapi import Request, HTTPException

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
SECRET_KEY = os.getenv("SECRET_KEY", "")

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
def create_payment(amount: int = 1000):
    """
    Создаёт тестовую ссылку на оплату
    """
    # Генерируем фиктивную ссылку
    payment_link = f"https://testpay.fake/pay/{len(payments)+1}"
    
    # Сохраняем статус
    payments[payment_link] = "pending"
    
    return {"payment_link": payment_link}


@app.get("/webhook")
async def webhook(request: Request):
    """
    Имитация webhook после оплаты
    """
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