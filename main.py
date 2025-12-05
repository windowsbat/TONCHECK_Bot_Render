import telegram
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import requests
import json
import os
import asyncio # Для асинхронной работы Telegram-бота

# --- КОНСТАНТЫ И НАСТРОЙКИ ---
TOKEN = os.getenv("TELEGRAM_TOKEN", "ВАШ_ТОКЕН") # Получаем токен из переменных окружения
ALERTS_FILE = 'alerts.json'

# --- 1. ФУНКЦИИ ДЛЯ РАБОТЫ С ДАННЫМИ (JSON) ---

def load_alerts():
    """Загружает алерты из JSON файла."""
    if not os.path.exists(ALERTS_FILE):
        return {}
    try:
        with open(ALERTS_FILE, 'r') as f:
            return json.load(f)
    except json.JSONDecodeError:
        return {} # Если файл пустой или поврежден

def save_alerts(alerts):
    """Сохраняет алерты в JSON файл."""
    with open(ALERTS_FILE, 'w') as f:
        json.dump(alerts, f, indent=4)

# --- 2. ФУНКЦИИ ДЛЯ API (CoinGecko) ---

def get_ton_price():
    """Получает текущую цену TON с CoinGecko."""
    try:
        url = "https://api.coingecko.com/api/v3/simple/price"
        params = {'ids': 'the-open-network', 'vs_currencies': 'usd'}
        response = requests.get(url, params=params)
        response.raise_for_status()
        
        data = response.json()
        price = data.get('the-open-network', {}).get('usd')
        
        return float(price) if price else None

    except Exception as e:
        print(f"Ошибка при получении цены: {e}")
        return None

# --- 3. ХЕНДЛЕРЫ ДЛЯ КОМАНД БОТА ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает команду /start."""
    await update.message.reply_text(
        f'Привет! Я бот TONCHECK. Чтобы установить алерт, используй команду:\n'
        f'/set_alert <цена>\n'
        f'Например: /set_alert 7.50'
    )

async def set_alert_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает команду /set_alert."""
    user_id = str(update.effective_user.id)
    chat_id = update.effective_chat.id
    
    # Проверяем, передал ли пользователь цену
    if not context.args:
        await update.message.reply_text("Пожалуйста, укажите цену! Пример: `/set_alert 7.50`")
        return

    try:
        target_price = round(float(context.args[0]), 2)
        if target_price <= 0:
             await update.message.reply_text("Цена должна быть положительным числом.")
             return
    except ValueError:
        await update.message.reply_text("Неверный формат цены. Используйте число.")
        return

    # Загружаем, добавляем и сохраняем алерт
    alerts = load_alerts()
    if user_id not in alerts:
        alerts[user_id] = {'chat_id': chat_id, 'targets': []}

    if target_price in alerts[user_id]['targets']:
        await update.message.reply_text(f"У вас уже установлен алерт на ${target_price}.")
        return

    alerts[user_id]['targets'].append(target_price)
    alerts[user_id]['targets'].sort() # Сортируем для удобства
    save_alerts(alerts)
    
    current_price = get_ton_price()
    
    await update.message.reply_text(
        f"🔔 Алерт установлен на **${target_price}**.\n"
        f"Текущая цена TON: ${current_price or '...'}",
        parse_mode='Markdown'
    )

async def my_alerts_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает активные алерты пользователя."""
    user_id = str(update.effective_user.id)
    alerts = load_alerts()
    
    if user_id in alerts and alerts[user_id]['targets']:
        targets_list = "\n".join([f"- ${p}" for p in alerts[user_id]['targets']])
        await update.message.reply_text(
            f"Ваши активные алерты:\n{targets_list}",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text("У вас нет активных алертов.")


# --- 4. ОСНОВНАЯ ЛОГИКА ПРОВЕРКИ (Для Cron Job) ---

async def check_alerts():
    """
    Основная функция для Cron Job.
    Проверяет цену и отправляет уведомления.
    """
    print("--- Запуск проверки цены ---")
    current_price = get_ton_price()
    
    if current_price is None:
        print("Не удалось получить цену. Отмена проверки.")
        return

    print(f"Текущая цена TON: ${current_price}")
    
    alerts = load_alerts()
    bot = telegram.Bot(token=TOKEN)
    
    alerts_to_remove = {} # Чтобы избежать ошибок при итерации

    for user_id, data in alerts.items():
        # Создаем новый список для тех алертов, которые НЕ сработали
        new_targets = []
        
        for target_price in data['targets']:
            if (current_price >= target_price) or (current_price <= target_price): # Срабатывает при достижении
                
                # Отправляем уведомление
                message = (
                    f"🚨 **АЛЕРТ СРАБОТАЛ!** 🚨\n"
                    f"Цена **TON** достигла **${target_price}**!\n"
                    f"Текущая цена: **${current_price}**"
                )
                try:
                    await bot.send_message(chat_id=data['chat_id'], text=message, parse_mode='Markdown')
                    print(f"Алерт отправлен пользователю {user_id} на {target_price}")
                except Exception as e:
                    print(f"Ошибка отправки пользователю {user_id}: {e}")
                    # Если ошибка (например, пользователь заблокировал бота), удаляем все алерты
                    if user_id not in alerts_to_remove:
                        alerts_to_remove[user_id] = True
            else:
                new_targets.append(target_price)
        
        # Обновляем список алертов для пользователя
        data['targets'] = new_targets
        
        # Если алертов не осталось, помечаем пользователя для удаления
        if not data['targets']:
             alerts_to_remove[user_id] = True
        
    # Удаляем пользователей без активных алертов или с ошибкой
    for user_id in alerts_to_remove:
        if user_id in alerts:
            del alerts[user_id]
            print(f"Пользователь {user_id} удален из списка алертов.")

    save_alerts(alerts)
    print("--- Проверка завершена. База данных обновлена. ---")

# --- 5. ОСНОВНАЯ ТОЧКА ВХОДА ---

def main():
    """Запускает либо WebHook (бота), либо Cron Job (проверку)."""
    
    # Мы используем env-переменную, чтобы понять, что нам делать
    # Render Cron Job запустит этот файл напрямую (нет переменной RENDER_EXTERNAL_URL)
    # Render Web Service установит RENDER_EXTERNAL_URL
    
  if os.getenv("RENDER_EXTERNAL_URL"):
        # Если переменная установлена - запускаем бота (Web Service)
        
        application = Application.builder().token(TOKEN).build()
        
        # Хендлеры команд (оставляем как есть...)

        # Настройка Webhook для Render
        PORT = int(os.environ.get('PORT', 5000))
        URL = os.environ.get('RENDER_EXTERNAL_URL')
        
        print(f"Запуск Webhook на {URL}, порт {PORT}")
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TOKEN,
            # ВНИМАНИЕ: Исправлено, URL не должен включать TOKEN!
            webhook_url=URL 
        )
    else:
        # Если переменная НЕ установлена - запускаем проверку (Cron Job)
        # Запускаем асинхронную функцию в синхронном окружении
        asyncio.run(check_alerts())


if __name__ == '__main__':
    main()