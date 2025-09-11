from fastapi import FastAPI
from fastapi import Request

app = FastAPI()

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