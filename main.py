import os
import json
import logging
import sqlite3
import socket
import whois
import dns.resolver
import requests
import hashlib
import secrets
import string
from random import randint
from datetime import datetime
from bs4 import BeautifulSoup
from urllib.parse import urlparse, quote
from typing import Dict, List, Optional
import time
import asyncio
from threading import Thread
import re
from concurrent.futures import ThreadPoolExecutor

try:
    from aiogram import Bot, Dispatcher, types, F
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
    from aiogram.filters import Command
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.state import State, StatesGroup
    from aiogram.fsm.storage.memory import MemoryStorage
    from aiogram.exceptions import TelegramBadRequest
except ModuleNotFoundError:
    print("Установите aiogram: pip install aiogram")
    exit(1)

# ========== КОНФИГУРАЦИЯ ==========
MAIN_BOT_TOKEN = "8249888150:AAGF9Q1IprTnFXpbS1vwzQnqwO20pfnmjcU"
LEAKOSINT_API_TOKEN = "7501355771:Tuq93eQf"
LEAKOSINT_URL = "https://leakosintapi.com/"
LANG = "ru"
LIMIT = 3000
WEBSITE_URL = "https://v0-polarsearch.vercel.app"

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Глобальные переменные
cash_reports = {}
user_states = {}
ADMIN_IDS = [7040106327]
REQUIRED_CHANNELS = []
active_bots = {}
mirror_tasks = {}
DB_FILE = "bot_database.db"

# ========== FSM STATES ==========
class UserStates(StatesGroup):
    waiting_for_bot_token = State()
    waiting_for_search_query = State()
    waiting_for_tool_input = State()
    waiting_for_dorking_query = State()
    waiting_for_kb_title = State()
    waiting_for_kb_content = State()

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
async def safe_delete_message(bot: Bot, chat_id: int, message_id: int):
    try:
        await bot.delete_message(chat_id, message_id)
        return True
    except Exception as e:
        logger.warning(f"Не удалось удалить сообщение: {e}")
        return False

async def safe_edit_message(bot: Bot, chat_id: int, message_id: int, text: str, parse_mode: str = "HTML", reply_markup=None):
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            disable_web_page_preview=True
        )
        return True
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            return False
        try:
            await bot.send_message(chat_id, text, parse_mode=parse_mode, reply_markup=reply_markup, disable_web_page_preview=True)
        except:
            pass
        return False
    except Exception as e:
        logger.error(f"Ошибка редактирования: {e}")
        return False

async def safe_send_message(bot: Bot, chat_id: int, text: str, parse_mode: str = "HTML", reply_markup=None, max_retries: int = 3):
    for attempt in range(max_retries):
        try:
            return await bot.send_message(chat_id, text, parse_mode=parse_mode, reply_markup=reply_markup, disable_web_page_preview=True)
        except Exception as e:
            logger.error(f"Ошибка отправки (попытка {attempt + 1}): {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)
    return None

async def safe_send_photo(bot: Bot, chat_id: int, photo_url: str, caption: str, parse_mode: str = "HTML", reply_markup=None):
    try:
        if photo_url.startswith('http'):
            return await bot.send_photo(chat_id, photo_url, caption=caption, parse_mode=parse_mode, reply_markup=reply_markup)
        elif os.path.exists(photo_url):
            photo = types.FSInputFile(photo_url)
            return await bot.send_photo(chat_id, photo, caption=caption, parse_mode=parse_mode, reply_markup=reply_markup)
        else:
            return await safe_send_message(bot, chat_id, caption, parse_mode, reply_markup)
    except Exception as e:
        logger.error(f"Ошибка отправки фото: {e}")
        return await safe_send_message(bot, chat_id, caption, parse_mode, reply_markup)

async def safe_answer_callback(callback: CallbackQuery, text: str = None):
    try:
        await callback.answer(text)
    except Exception as e:
        logger.warning(f"Не удалось ответить на callback: {e}")

async def create_default_photo():
    try:
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new('RGB', (512, 512), color='#2b2d42')
        draw = ImageDraw.Draw(img)
        draw.ellipse([(100, 100), (412, 412)], fill='#8d99ae')
        try:
            font = ImageFont.truetype("arial.ttf", 60)
        except:
            font = ImageFont.load_default()
        draw.text((256, 256), "🔍", fill='#edf2f4', font=font, anchor="mm")
        img.save('start.png')
        logger.info("✅ Создано фото start.png")
    except Exception as e:
        logger.warning(f"Не удалось создать фото: {e}")
        with open('start.png', 'w') as f:
            f.write('Photo placeholder')

# ========== БАЗА ДАННЫХ ==========
def init_database():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            join_date TEXT,
            requests_count INTEGER DEFAULT 0,
            is_admin INTEGER DEFAULT 0
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS channels (
            channel_id TEXT PRIMARY KEY,
            channel_name TEXT,
            channel_url TEXT,
            is_active INTEGER DEFAULT 1
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS statistics (
            date TEXT PRIMARY KEY,
            new_users INTEGER DEFAULT 0,
            total_requests INTEGER DEFAULT 0
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mirror_bots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_token TEXT UNIQUE,
            owner_id INTEGER,
            bot_name TEXT,
            created_date TEXT,
            is_active INTEGER DEFAULT 1
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS knowledge_base (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            created_date TEXT,
            created_by INTEGER
        )
    ''')
    
    conn.commit()
    conn.close()

def add_user(user_id: int, username: str, first_name: str, last_name: str):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        join_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute('''
            INSERT OR IGNORE INTO users (user_id, username, first_name, last_name, join_date)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, username, first_name, last_name, join_date))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Ошибка добавления пользователя: {e}")

def increment_requests(user_id: int):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('UPDATE users SET requests_count = requests_count + 1 WHERE user_id = ?', (user_id,))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Ошибка обновления счетчика: {e}")

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def get_all_users():
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users ORDER BY join_date DESC')
        users = cursor.fetchall()
        conn.close()
        return users
    except Exception as e:
        logger.error(f"Ошибка получения пользователей: {e}")
        return []

def get_user_stats(user_id: int):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        user = cursor.fetchone()
        conn.close()
        return user
    except Exception as e:
        logger.error(f"Ошибка получения статистики: {e}")
        return None

def add_mirror_bot(bot_token: str, owner_id: int, bot_name: str):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        created_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute('''
            INSERT OR REPLACE INTO mirror_bots (bot_token, owner_id, bot_name, created_date, is_active)
            VALUES (?, ?, ?, ?, 1)
        ''', (bot_token, owner_id, bot_name, created_date))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Ошибка добавления зеркала: {e}")
        return False

def get_mirror_bots(owner_id: int = None):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        if owner_id:
            cursor.execute('SELECT * FROM mirror_bots WHERE owner_id = ? AND is_active = 1 ORDER BY created_date DESC', (owner_id,))
        else:
            cursor.execute('SELECT * FROM mirror_bots WHERE is_active = 1 ORDER BY created_date DESC')
        bots = cursor.fetchall()
        conn.close()
        return bots
    except Exception as e:
        logger.error(f"Ошибка получения зеркал: {e}")
        return []

def remove_mirror_bot(bot_token: str):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('UPDATE mirror_bots SET is_active = 0 WHERE bot_token = ?', (bot_token,))
        conn.commit()
        conn.close()
        
        # Остановка зеркала
        if bot_token in mirror_tasks:
            mirror_tasks[bot_token].cancel()
            del mirror_tasks[bot_token]
        if bot_token in active_bots:
            del active_bots[bot_token]
        
        return True
    except Exception as e:
        logger.error(f"Ошибка удаления зеркала: {e}")
        return False

def add_channel(channel_id: str, channel_name: str, channel_url: str):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO channels (channel_id, channel_name, channel_url, is_active)
            VALUES (?, ?, ?, 1)
        ''', (channel_id, channel_name, channel_url))
        conn.commit()
        conn.close()
        global REQUIRED_CHANNELS
        REQUIRED_CHANNELS = get_active_channels()
        return True
    except Exception as e:
        logger.error(f"Ошибка добавления канала: {e}")
        return False

def remove_channel(channel_id: str):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM channels WHERE channel_id = ?', (channel_id,))
        conn.commit()
        conn.close()
        global REQUIRED_CHANNELS
        REQUIRED_CHANNELS = get_active_channels()
        return True
    except Exception as e:
        logger.error(f"Ошибка удаления канала: {e}")
        return False

def get_active_channels():
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM channels WHERE is_active = 1')
        channels = cursor.fetchall()
        conn.close()
        return channels
    except Exception as e:
        logger.error(f"Ошибка получения каналов: {e}")
        return []

# ========== БАЗА ЗНАНИЙ ==========
def add_knowledge(title: str, content: str, created_by: int):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        created_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute('''
            INSERT INTO knowledge_base (title, content, created_date, created_by)
            VALUES (?, ?, ?, ?)
        ''', (title, content, created_date, created_by))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Ошибка добавления в базу знаний: {e}")
        return False

def get_all_knowledge():
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM knowledge_base ORDER BY created_date DESC')
        knowledge = cursor.fetchall()
        conn.close()
        return knowledge
    except Exception as e:
        logger.error(f"Ошибка получения базы знаний: {e}")
        return []

def get_knowledge_by_id(kb_id: int):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM knowledge_base WHERE id = ?', (kb_id,))
        knowledge = cursor.fetchone()
        conn.close()
        return knowledge
    except Exception as e:
        logger.error(f"Ошибка получения записи: {e}")
        return None

def delete_knowledge(kb_id: int):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM knowledge_base WHERE id = ?', (kb_id,))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Ошибка удаления записи: {e}")
        return False

async def check_user_subscription(bot: Bot, user_id: int):
    channels = get_active_channels()
    if not channels:
        return True, []
    
    not_subscribed = []
    for channel in channels:
        channel_id = channel[0]
        try:
            chat_member = await bot.get_chat_member(channel_id, user_id)
            if chat_member.status not in ['member', 'administrator', 'creator']:
                not_subscribed.append({'id': channel_id, 'name': channel[1], 'url': channel[2]})
        except Exception as e:
            logger.error(f"Ошибка проверки подписки на {channel_id}: {e}")
            not_subscribed.append({'id': channel_id, 'name': channel[1], 'url': channel[2]})
    
    return len(not_subscribed) == 0, not_subscribed

# ========== КЛАВИАТУРЫ ==========
def create_start_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Поиск утечек", callback_data="leak_search"),
         InlineKeyboardButton(text="🛠️ Инструменты", callback_data="tools_menu")],
        [InlineKeyboardButton(text="🕵️ Dorking", callback_data="dorking_menu"),
         InlineKeyboardButton(text="📚 База знаний", callback_data="knowledge_base_menu")],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="profile_menu"),
         InlineKeyboardButton(text="🤖 Зеркала", callback_data="mirrors_menu")],
        [InlineKeyboardButton(text="ℹ️ Помощь", callback_data="help_menu"),
         InlineKeyboardButton(text="🌐 Наш сайт", url=WEBSITE_URL)]
    ])

def create_mirrors_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Создать зеркало", callback_data="create_mirror"),
         InlineKeyboardButton(text="📋 Мои зеркала", callback_data="my_mirrors")],
        [InlineKeyboardButton(text="🗑️ Удалить зеркало", callback_data="delete_mirror"),
         InlineKeyboardButton(text="ℹ️ Инструкция", callback_data="mirrors_help")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")]
    ])

def create_tools_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔎 WHOIS", callback_data="tool_whois"),
         InlineKeyboardButton(text="🌐 Поддомены", callback_data="tool_subdomains")],
        [InlineKeyboardButton(text="📡 DNS записи", callback_data="tool_dns"),
         InlineKeyboardButton(text="🔄 Обратный DNS", callback_data="tool_reverse_dns")],
        [InlineKeyboardButton(text="🔗 Внешние ссылки", callback_data="tool_site_relations"),
         InlineKeyboardButton(text="📶 Доступность", callback_data="tool_availability")],
        [InlineKeyboardButton(text="📄 Контент сайта", callback_data="tool_content"),
         InlineKeyboardButton(text="🖥️ Серверное ПО", callback_data="tool_server")],
        [InlineKeyboardButton(text="🔐 Генератор паролей", callback_data="tool_password"),
         InlineKeyboardButton(text="🔒 Хеш MD5/SHA", callback_data="tool_hash")],
        [InlineKeyboardButton(text="📧 Email валидация", callback_data="tool_email"),
         InlineKeyboardButton(text="📱 Телефон инфо", callback_data="tool_phone")],
        [InlineKeyboardButton(text="🌍 IP Geolocation", callback_data="tool_ip_geo"),
         InlineKeyboardButton(text="🔍 Port Scanner", callback_data="tool_port_scan")],
        [InlineKeyboardButton(text="🔐 SSL Info", callback_data="tool_ssl")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")]
    ])

def create_dorking_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Поиск по нику", callback_data="dork_username"),
         InlineKeyboardButton(text="📧 Поиск по email", callback_data="dork_email")],
        [InlineKeyboardButton(text="📱 Поиск по телефону", callback_data="dork_phone"),
         InlineKeyboardButton(text="🆔 Поиск по ID", callback_data="dork_id")],
        [InlineKeyboardButton(text="🌐 Поиск по домену", callback_data="dork_domain"),
         InlineKeyboardButton(text="🔍 Универсальный", callback_data="dork_universal")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")]
    ])

def create_profile_keyboard(user_id: int):
    buttons = [
        [InlineKeyboardButton(text="📊 Моя статистика", callback_data="my_stats"),
         InlineKeyboardButton(text="🆘 Помощь", callback_data="help_menu")]
    ]
    if is_admin(user_id):
        buttons.append([InlineKeyboardButton(text="👑 Админ панель", callback_data="admin_panel")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def create_admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats"),
         InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users")],
        [InlineKeyboardButton(text="📢 Каналы", callback_data="admin_channels"),
         InlineKeyboardButton(text="🤖 Зеркала", callback_data="admin_mirrors")],
        [InlineKeyboardButton(text="📚 База знаний", callback_data="admin_knowledge")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="profile_menu")]
    ])

def create_channels_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить", callback_data="channel_add"),
         InlineKeyboardButton(text="🗑️ Удалить", callback_data="channel_remove")],
        [InlineKeyboardButton(text="📋 Список", callback_data="channel_list"),
         InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel")]
    ])

def create_back_keyboard(callback_data: str = "back_to_main"):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=callback_data)]
    ])

def create_inline_keyboard(query_id: str, page_id: int, count_page: int):
    if count_page <= 1:
        return create_back_keyboard()
    buttons = []
    if page_id > 0:
        buttons.append(InlineKeyboardButton(text="◀️", callback_data=f"page_{query_id}_{page_id-1}"))
    buttons.append(InlineKeyboardButton(text=f"{page_id+1}/{count_page}", callback_data="current_page"))
    if page_id < count_page - 1:
        buttons.append(InlineKeyboardButton(text="▶️", callback_data=f"page_{query_id}_{page_id+1}"))
    return InlineKeyboardMarkup(inline_keyboard=[buttons, [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")]])

def create_subscription_keyboard(channels: List[Dict]):
    buttons = []
    for channel in channels:
        buttons.append([InlineKeyboardButton(text=f"📢 {channel['name']}", url=channel['url'])])
    buttons.append([InlineKeyboardButton(text="✅ Я подписался", callback_data="check_subscription")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def create_mirror_delete_keyboard(mirrors):
    buttons = []
    for mirror in mirrors:
        buttons.append([InlineKeyboardButton(text=f"🗑️ {mirror[3]}", callback_data=f"delete_mirror_{mirror[1]}")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="mirrors_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def create_knowledge_keyboard(knowledge_list):
    buttons = []
    for kb in knowledge_list[:20]:  # Ограничение 20 записей
        buttons.append([InlineKeyboardButton(text=f"📄 {kb[1]}", callback_data=f"kb_view_{kb[0]}")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def create_admin_knowledge_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить", callback_data="kb_add"),
         InlineKeyboardButton(text="📋 Список", callback_data="kb_list")],
        [InlineKeyboardButton(text="🗑️ Удалить", callback_data="kb_delete"),
         InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel")]
    ])

def create_knowledge_delete_keyboard(knowledge_list):
    buttons = []
    for kb in knowledge_list[:20]:
        buttons.append([InlineKeyboardButton(text=f"🗑️ {kb[1]}", callback_data=f"kb_del_{kb[0]}")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_knowledge")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ========== LEAKOSINT ==========
def generate_report(query: str, query_id: str):
    global cash_reports
    data = {"token": LEAKOSINT_API_TOKEN, "request": query.split("\n")[0], "limit": LIMIT, "lang": LANG}
    
    try:
        response = requests.post(LEAKOSINT_URL, json=data, timeout=30).json()
        if "Error code" in response:
            logger.error(f"Ошибка LeakOsint: {response.get('Error code')}")
            return None
        
        cash_reports[str(query_id)] = []
        for database_name in response.get("List", {}).keys():
            text = [f"<b>📁 {database_name}</b>", ""]
            if "InfoLeak" in response["List"][database_name]:
                text.append(response["List"][database_name]["InfoLeak"] + "\n")
            if database_name != "No results found":
                for report_data in response["List"][database_name].get("Data", []):
                    for column_name in report_data.keys():
                        text.append(f"<b>{column_name}</b>: <code>{report_data[column_name]}</code>")
                    text.append("")
            text = "\n".join(text)
            if len(text) > 3500:
                text = text[:3500] + text[3500:].split("\n")[0] + "\n\n⚠️ <i>Некоторые данные не поместились</i>"
            cash_reports[str(query_id)].append(text)
        return cash_reports[str(query_id)]
    except Exception as e:
        logger.error(f"Ошибка при генерации отчета: {e}")
        return None

# ========== НОВЫЕ ИНСТРУМЕНТЫ ==========

def generate_password(length: int = 16, use_special: bool = True) -> str:
    """Генератор надежных паролей"""
    try:
        chars = string.ascii_letters + string.digits
        if use_special:
            chars += "!@#$%^&*()_+-=[]{}|;:,.<>?"
        password = ''.join(secrets.choice(chars) for _ in range(length))
        return f"🔐 <b>Сгенерированный пароль:</b>\n\n<code>{password}</code>\n\n<b>Длина:</b> {length} символов\n<b>Спецсимволы:</b> {'Да' if use_special else 'Нет'}"
    except Exception as e:
        return f"❌ Ошибка: {str(e)}"

def calculate_hash(text: str) -> str:
    """Вычисление хешей MD5 и SHA-256"""
    try:
        md5_hash = hashlib.md5(text.encode()).hexdigest()
        sha256_hash = hashlib.sha256(text.encode()).hexdigest()
        sha1_hash = hashlib.sha1(text.encode()).hexdigest()
        
        result = [
            "🔒 <b>Хеши строки:</b>\n",
            f"<b>Текст:</b> <code>{text[:50]}</code>",
            f"\n<b>MD5:</b>\n<code>{md5_hash}</code>",
            f"\n<b>SHA-1:</b>\n<code>{sha1_hash}</code>",
            f"\n<b>SHA-256:</b>\n<code>{sha256_hash}</code>"
        ]
        return "\n".join(result)
    except Exception as e:
        return f"❌ Ошибка: {str(e)}"

def validate_email(email: str) -> str:
    """Валидация email адреса"""
    try:
        email_regex = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        is_valid = re.match(email_regex, email) is not None
        
        result = [
            "📧 <b>Проверка email:</b>\n",
            f"<b>Email:</b> <code>{email}</code>",
            f"<b>Статус:</b> {'✅ Валидный' if is_valid else '❌ Невалидный'}"
        ]
        
        if is_valid:
            domain = email.split('@')[1]
            try:
                mx_records = dns.resolver.resolve(domain, 'MX')
                result.append(f"<b>MX записи:</b> ✅ Найдены ({len(mx_records)})")
            except:
                result.append(f"<b>MX записи:</b> ❌ Не найдены")
        
        return "\n".join(result)
    except Exception as e:
        return f"❌ Ошибка: {str(e)}"

def analyze_phone(phone: str) -> str:
    """Анализ телефонного номера"""
    try:
        clean_phone = re.sub(r'[^\d+]', '', phone)
        
        result = [
            "📱 <b>Анализ номера:</b>\n",
            f"<b>Номер:</b> <code>{clean_phone}</code>",
            f"<b>Длина:</b> {len(clean_phone)} символов"
        ]
        
        country_codes = {
            '+7': '🇷🇺 Россия/Казахстан',
            '+1': '🇺🇸 США/Канада',
            '+44': '🇬🇧 Великобритания',
            '+49': '🇩🇪 Германия',
            '+33': '🇫🇷 Франция',
            '+380': '🇺🇦 Украина',
            '+375': '🇧🇾 Беларусь'
        }
        
        for code, country in country_codes.items():
            if clean_phone.startswith(code):
                result.append(f"<b>Страна:</b> {country}")
                break
        
        return "\n".join(result)
    except Exception as e:
        return f"❌ Ошибка: {str(e)}"

def get_ip_geolocation(ip: str) -> str:
    """IP Geolocation"""
    try:
        response = requests.get(f"http://ip-api.com/json/{ip}", timeout=10).json()
        if response.get('status') == 'success':
            result = [
                "🌍 <b>IP Geolocation:</b>\n",
                f"<b>IP:</b> <code>{ip}</code>",
                f"<b>Страна:</b> {response.get('country')} {response.get('countryCode')}",
                f"<b>Регион:</b> {response.get('regionName')}",
                f"<b>Город:</b> {response.get('city')}",
                f"<b>ISP:</b> {response.get('isp')}",
                f"<b>Организация:</b> {response.get('org')}",
                f"<b>Координаты:</b> {response.get('lat')}, {response.get('lon')}"
            ]
            return "\n".join(result)
        return "❌ Информация не найдена"
    except Exception as e:
        return f"❌ Ошибка: {str(e)}"

def scan_ports(host: str, ports: str = "21,22,23,25,80,443,3306,3389,8080") -> str:
    """Простой сканер портов"""
    try:
        host = host.replace('http://', '').replace('https://', '').split('/')[0]
        port_list = [int(p.strip()) for p in ports.split(',')]
        
        result = [f"🔍 <b>Сканирование портов: {host}</b>\n"]
        open_ports = []
        
        for port in port_list[:10]:  # Ограничение 10 портов
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            if sock.connect_ex((host, port)) == 0:
                open_ports.append(port)
            sock.close()
        
        if open_ports:
            result.append("<b>Открытые порты:</b>")
            for port in open_ports:
                result.append(f"✅ <code>{port}</code>")
        else:
            result.append("❌ Открытые порты не найдены")
        
        return "\n".join(result)
    except Exception as e:
        return f"❌ Ошибка: {str(e)}"

def get_ssl_info(domain: str) -> str:
    """Информация о SSL сертификате"""
    try:
        import ssl
        domain = domain.replace('http://', '').replace('https://', '').split('/')[0]
        
        context = ssl.create_default_context()
        with socket.create_connection((domain, 443), timeout=10) as sock:
            with context.wrap_socket(sock, server_hostname=domain) as ssock:
                cert = ssock.getpeercert()
        
        result = [
            f"🔐 <b>SSL сертификат: {domain}</b>\n",
            f"<b>Издатель:</b> {dict(x[0] for x in cert['issuer']).get('organizationName', 'N/A')}",
            f"<b>Владелец:</b> {dict(x[0] for x in cert['subject']).get('commonName', 'N/A')}",
            f"<b>Действителен с:</b> {cert.get('notBefore', 'N/A')}",
            f"<b>Действителен до:</b> {cert.get('notAfter', 'N/A')}",
            f"<b>Версия:</b> {cert.get('version', 'N/A')}"
        ]
        return "\n".join(result)
    except Exception as e:
        return f"❌ Ошибка: {str(e)}"

# ========== DORKING ФУНКЦИИ ==========

def dorking_search(query: str, search_type: str) -> str:
    """Поиск информации через Google Dorking"""
    try:
        search_engines = {
            'google': f"https://www.google.com/search?q={quote(query)}",
            'yandex': f"https://yandex.ru/search/?text={quote(query)}",
            'bing': f"https://www.bing.com/search?q={quote(query)}",
            'duckduckgo': f"https://duckduckgo.com/?q={quote(query)}"
        }
        
        dork_queries = []
        
        if search_type == "username":
            dork_queries = [
                f'"{query}" site:vk.com',
                f'"{query}" site:instagram.com',
                f'"{query}" site:twitter.com',
                f'"{query}" site:facebook.com',
                f'"{query}" site:github.com',
                f'"{query}" site:linkedin.com',
                f'"{query}" site:youtube.com',
                f'"{query}" site:tiktok.com',
                f'"{query}" site:telegram.me',
                f'"{query}" site:reddit.com'
            ]
        elif search_type == "email":
            dork_queries = [
                f'"{query}"',
                f'"{query}" site:pastebin.com',
                f'"{query}" filetype:txt',
                f'"{query}" filetype:pdf',
                f'"{query}" site:github.com',
                f'"{query}" intext:"email"'
            ]
        elif search_type == "phone":
            clean_phone = re.sub(r'[^\d+]', '', query)
            dork_queries = [
                f'"{clean_phone}"',
                f'"{clean_phone}" site:vk.com',
                f'"{clean_phone}" intext:"phone"',
                f'"{clean_phone}" intext:"телефон"',
                f'"{clean_phone}" site:avito.ru'
            ]
        elif search_type == "domain":
            dork_queries = [
                f'site:{query}',
                f'site:{query} inurl:admin',
                f'site:{query} filetype:pdf',
                f'site:{query} intext:"password"',
                f'related:{query}'
            ]
        else:
            dork_queries = [f'"{query}"']
        
        result = [
            f"🕵️ <b>Dorking поиск: {search_type}</b>\n",
            f"<b>Запрос:</b> <code>{query}</code>\n",
            "<b>🔍 Ссылки для поиска:</b>\n"
        ]
        
        for i, dork in enumerate(dork_queries[:10], 1):
            encoded_dork = quote(dork)
            google_link = f"https://www.google.com/search?q={encoded_dork}"
            result.append(f"{i}. <a href='{google_link}'>{dork[:50]}</a>")
        
        result.append("\n<b>🌐 Поисковые системы:</b>")
        result.append(f"• <a href='{search_engines['google']}'>Google</a>")
        result.append(f"• <a href='{search_engines['yandex']}'>Yandex</a>")
        result.append(f"• <a href='{search_engines['bing']}'>Bing</a>")
        result.append(f"• <a href='{search_engines['duckduckgo']}'>DuckDuckGo</a>")
        
        result.append("\n<i>💡 Нажмите на ссылку для поиска</i>")
        
        return "\n".join(result)
    except Exception as e:
        logger.error(f"Ошибка dorking: {e}")
        return f"❌ Ошибка: {str(e)}"

# ========== OSINT ФУНКЦИИ ==========

def perform_whois(domain: str) -> str:
    try:
        domain = domain.replace('http://', '').replace('https://', '').replace('www.', '').split('/')[0]
        
        w = whois.whois(domain)
        info = [f"🔎 <b>WHOIS: {domain}</b>\n"]
        
        if w.domain_name:
            domain_name = w.domain_name if isinstance(w.domain_name, str) else w.domain_name[0]
            info.append(f"<b>Домен:</b> <code>{domain_name}</code>")
        if w.registrar:
            info.append(f"<b>Регистратор:</b> {w.registrar}")
        if w.creation_date:
            creation = w.creation_date if isinstance(w.creation_date, datetime) else w.creation_date[0]
            info.append(f"<b>Создан:</b> {creation.strftime('%Y-%m-%d')}")
        if w.expiration_date:
            expiration = w.expiration_date if isinstance(w.expiration_date, datetime) else w.expiration_date[0]
            info.append(f"<b>Истекает:</b> {expiration.strftime('%Y-%m-%d')}")
        if w.name_servers:
            ns_list = w.name_servers if isinstance(w.name_servers, list) else [w.name_servers]
            info.append(f"<b>NS серверы:</b>\n" + "\n".join(f"• {ns}" for ns in ns_list[:5]))
        
        try:
            ip = socket.gethostbyname(domain)
            info.append(f"<b>IP адрес:</b> <code>{ip}</code>")
        except:
            pass
        
        return "\n".join(info) if len(info) > 1 else "❌ Информация не найдена"
    except Exception as e:
        logger.error(f"Ошибка WHOIS: {e}")
        return f"❌ Ошибка: {str(e)}"

def find_subdomains(domain: str) -> str:
    domain = domain.replace('http://', '').replace('https://', '').replace('www.', '').split('/')[0]
    
    common_subs = ['www', 'mail', 'ftp', 'admin', 'test', 'dev', 'api', 'blog', 'shop', 'forum', 
                   'support', 'help', 'docs', 'cdn', 'static', 'img', 'images', 'portal', 'vpn']
    
    valid_subs = []
    for sub in common_subs:
        subdomain = f"{sub}.{domain}"
        try:
            socket.gethostbyname(subdomain)
            valid_subs.append(f"✅ <code>{subdomain}</code>")
        except:
            continue
    
    result = [f"🌐 <b>Поддомены: {domain}</b>\n"]
    if valid_subs:
        result.append("<b>Найдено:</b>\n" + "\n".join(valid_subs))
    else:
        result.append("❌ Активные поддомены не найдены")
    
    return "\n".join(result)

def get_dns_records(domain: str) -> str:
    domain = domain.replace('http://', '').replace('https://', '').replace('www.', '').split('/')[0]
    
    records = {}
    record_types = ['A', 'AAAA', 'MX', 'NS', 'TXT', 'CNAME']
    
    for rec_type in record_types:
        try:
            answers = dns.resolver.resolve(domain, rec_type)
            records[rec_type] = [str(r) for r in answers]
        except:
            records[rec_type] = []
    
    result = [f"📡 <b>DNS записи: {domain}</b>\n"]
    for rec_type, values in records.items():
        if values:
            result.append(f"<b>{rec_type}:</b>")
            for v in values[:5]:
                result.append(f"• <code>{v}</code>")
            result.append("")
    
    return "\n".join(result) if len(result) > 1 else "❌ DNS записи не найдены"

def perform_reverse_dns(ip: str) -> str:
    try:
        hostname = socket.gethostbyaddr(ip)[0]
        result = [
            f"🔄 <b>Обратный DNS</b>\n",
            f"<b>IP:</b> <code>{ip}</code>",
            f"<b>Hostname:</b> <code>{hostname}</code>"
        ]
        return "\n".join(result)
    except Exception as e:
        return f"❌ Ошибка: {str(e)}"

def analyze_site_relations(url: str) -> str:
    try:
        if not url.startswith('http'):
            url = f'http://{url}'
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        resp = requests.get(url, timeout=10, headers=headers)
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        ext_links = set()
        for link in soup.find_all('a', href=True):
            href = link['href']
            if href.startswith('http'):
                parsed_url = urlparse(url)
                parsed_href = urlparse(href)
                if parsed_href.netloc and parsed_href.netloc != parsed_url.netloc:
                    ext_links.add(parsed_href.netloc)
        
        result = [f"🔗 <b>Внешние ссылки</b>\n", f"<b>Сайт:</b> {url}\n"]
        if ext_links:
            result.append(f"<b>Найдено доменов: {len(ext_links)}</b>\n")
            for link in list(ext_links)[:20]:
                result.append(f"• <code>{link}</code>")
        else:
            result.append("❌ Внешние ссылки не найдены")
        
        return "\n".join(result)
    except Exception as e:
        return f"❌ Ошибка: {str(e)}"

def check_host_availability(url: str) -> str:
    try:
        if not url.startswith('http'):
            url = f'http://{url}'
        
        start_time = datetime.now()
        response = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
        response_time = (datetime.now() - start_time).total_seconds()
        
        status_emoji = "✅" if response.status_code == 200 else "⚠️"
        
        result = [
            "📶 <b>Проверка доступности</b>\n",
            f"<b>URL:</b> {url}",
            f"<b>Статус:</b> {status_emoji} {response.status_code}",
            f"<b>Время ответа:</b> {response_time:.2f} сек",
            f"<b>Размер:</b> {len(response.content)} байт"
        ]
        
        if 'Server' in response.headers:
            result.append(f"<b>Сервер:</b> {response.headers['Server']}")
        
        return "\n".join(result)
    except Exception as e:
        return f"❌ Сайт недоступен: {str(e)}"

def search_site_content(url: str) -> str:
    try:
        if not url.startswith('http'):
            url = f'http://{url}'
        
        resp = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        for script in soup(["script", "style"]):
            script.decompose()
        
        text = soup.get_text()
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        
        title = soup.find('title')
        description = soup.find('meta', attrs={'name': 'description'})
        
        result = [f"📄 <b>Контент сайта</b>\n", f"<b>URL:</b> {url}\n"]
        
        if title:
            result.append(f"<b>Заголовок:</b> {title.string}\n")
        if description:
            result.append(f"<b>Описание:</b> {description.get('content', 'N/A')}\n")
        
        result.append("<b>Первые строки:</b>")
        result.append("\n".join(lines[:20]))
        
        return "\n".join(result)
    except Exception as e:
        return f"❌ Ошибка: {str(e)}"

def analyze_server_software(url: str) -> str:
    try:
        if not url.startswith('http'):
            url = f'http://{url}'
        
        resp = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
        headers = resp.headers
        
        result = [f"🖥️ <b>Информация о сервере</b>\n", f"<b>URL:</b> {url}\n"]
        
        server_headers = ['Server', 'X-Powered-By', 'X-AspNet-Version', 'X-AspNetMvc-Version', 
                         'X-Frame-Options', 'X-Content-Type-Options']
        
        found = False
        for header in server_headers:
            if header in headers:
                result.append(f"<b>{header}:</b> <code>{headers[header]}</code>")
                found = True
        
        if not found:
            result.append("❌ Информация о сервере скрыта")
        
        return "\n".join(result)
    except Exception as e:
        return f"❌ Ошибка: {str(e)}"

# ========== ЗЕРКАЛА (ИСПРАВЛЕНО) ==========

async def start_mirror_bot(bot_token: str, owner_id: int, bot_name: str):
    """Запуск зеркала бота в отдельном event loop"""
    try:
        bot = Bot(token=bot_token)
        dp = Dispatcher(storage=MemoryStorage())
        
        active_bots[bot_token] = {
            'bot': bot,
            'owner_id': owner_id,
            'bot_name': bot_name,
            'running': True
        }
        
        @dp.message(Command("start"))
        async def mirror_start(message: types.Message):
            user_id = message.from_user.id
            first_name = message.from_user.first_name or "Пользователь"
            add_user(user_id, message.from_user.username, first_name, message.from_user.last_name)
            
            subscribed, not_subscribed = await check_user_subscription(bot, user_id)
            if not subscribed:
                keyboard = create_subscription_keyboard(not_subscribed)
                await safe_send_message(bot, user_id,
                    f"👋 <b>Добро пожаловать, {first_name}!</b>\n\n📢 Подпишитесь на каналы:",
                    reply_markup=keyboard)
                return
            
            photo_path = 'start.png'
            caption = (f"👋 <b>Добро пожаловать, {first_name}!</b>\n\n"
                      f"🤖 <b>Зеркало:</b> {bot_name}\n"
                      f"🌐 {WEBSITE_URL}\n\n"
                      "🔍 Бот для поиска утечек и OSINT\n\nВыберите действие:")
            
            if os.path.exists(photo_path):
                await safe_send_photo(bot, user_id, photo_path, caption, reply_markup=create_start_keyboard())
            else:
                await safe_send_message(bot, user_id, caption, reply_markup=create_start_keyboard())
        
        @dp.callback_query()
        async def mirror_callback(callback: types.CallbackQuery):
            await handle_callback_logic(callback, bot, is_mirror=True)
        
        @dp.message()
        async def mirror_message(message: types.Message):
            await handle_message_logic(message, bot)
        
        logger.info(f"✅ Запущено зеркало: {bot_name}")
        await dp.start_polling(bot, skip_updates=True)
        
    except asyncio.CancelledError:
        logger.info(f"🛑 Остановлено зеркало: {bot_name}")
        await bot.session.close()
    except Exception as e:
        logger.error(f"❌ Ошибка в зеркале {bot_name}: {e}")
        if bot_token in active_bots:
            active_bots[bot_token]['running'] = False

def create_mirror_bot_instance(bot_token: str, owner_id: int, bot_name: str):
    """Создание и запуск зеркала в отдельном потоке с новым event loop"""
    try:
        if add_mirror_bot(bot_token, owner_id, bot_name):
            def run_mirror_in_thread():
                # Создаем новый event loop для потока
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    # Создаем task для зеркала
                    task = loop.create_task(start_mirror_bot(bot_token, owner_id, bot_name))
                    mirror_tasks[bot_token] = task
                    loop.run_until_complete(task)
                except asyncio.CancelledError:
                    logger.info(f"Зеркало {bot_name} остановлено")
                except Exception as e:
                    logger.error(f"Ошибка в потоке зеркала {bot_name}: {e}")
                finally:
                    loop.close()
            
            mirror_thread = Thread(target=run_mirror_in_thread, daemon=True, name=f"Mirror-{bot_name}")
            mirror_thread.start()
            return True, bot_name
        return False, "Ошибка сохранения в БД"
    except Exception as e:
        logger.error(f"Ошибка создания зеркала: {e}")
        return False, str(e)

# ========== ОБРАБОТЧИКИ СООБЩЕНИЙ ==========

async def handle_message_logic(message: types.Message, bot_instance: Bot):
    """Универсальная обработка текстовых сообщений"""
    user_id = message.from_user.id
    text = message.text.strip() if message.text else ""
    
    if not text:
        return
    
    subscribed, not_subscribed = await check_user_subscription(bot_instance, user_id)
    if not subscribed:
        keyboard = create_subscription_keyboard(not_subscribed)
        await safe_send_message(bot_instance, user_id, "📢 Подпишитесь на каналы!", reply_markup=keyboard)
        return
    
    if user_id in user_states:
        state = user_states[user_id]
        
        if state.get("waiting_for") == "search_query":
            query_id = str(randint(0, 9999999))
            increment_requests(user_id)
            await safe_send_message(bot_instance, user_id, "⏳ Ищу информацию...")
            report = generate_report(text, query_id)
            
            if report and len(report) > 0 and "No results found" not in report[0]:
                markup = create_inline_keyboard(query_id, 0, len(report))
                await safe_send_message(bot_instance, user_id, report[0], reply_markup=markup)
            else:
                await safe_send_message(bot_instance, user_id, 
                    f"🔍 Информация не найдена\n\n<b>Запрос:</b> <code>{text}</code>", 
                    reply_markup=create_back_keyboard())
            
            del user_states[user_id]
            return
        
        elif "tool" in state:
            tool_name = state["tool"]
            tool_functions = {
                "whois": perform_whois,
                "subdomains": find_subdomains,
                "dns": get_dns_records,
                "reverse_dns": perform_reverse_dns,
                "site_relations": analyze_site_relations,
                "availability": check_host_availability,
                "content": search_site_content,
                "server": analyze_server_software,
                "password": lambda x: generate_password(int(x) if x.isdigit() else 16),
                "hash": calculate_hash,
                "email": validate_email,
                "phone": analyze_phone,
                "ip_geo": get_ip_geolocation,
                "port_scan": lambda x: scan_ports(x.split()[0], x.split()[1] if len(x.split()) > 1 else "21,22,23,25,80,443,3306,3389,8080"),
                "ssl": get_ssl_info
            }
            
            result = tool_functions.get(tool_name, lambda x: "❌ Ошибка")(text)
            increment_requests(user_id)
            del user_states[user_id]
            await safe_send_message(bot_instance, user_id, result, reply_markup=create_back_keyboard("tools_menu"))
            return
        
        elif "dorking" in state:
            dork_type = state["dorking"]
            result = dorking_search(text, dork_type)
            increment_requests(user_id)
            del user_states[user_id]
            await safe_send_message(bot_instance, user_id, result, reply_markup=create_back_keyboard("dorking_menu"))
            return
        
        elif "kb_title" in state:
            if user_states[user_id].get("kb_title") is None:
                # Первое сообщение - название
                user_states[user_id]["kb_title"] = text
                await safe_send_message(bot_instance, user_id,
                    "📝 <b>Добавление в базу знаний</b>\n\n"
                    f"<b>Название:</b> {text}\n\n"
                    "Теперь отправьте текст статьи:",
                    reply_markup=create_back_keyboard("admin_knowledge"))
            else:
                # Второе сообщение - текст
                title = user_states[user_id].get("kb_title", "")
                if add_knowledge(title, text, user_id):
                    await safe_send_message(bot_instance, user_id,
                        f"✅ <b>Запись добавлена в базу знаний!</b>\n\n"
                        f"<b>Название:</b> {title}",
                        reply_markup=create_back_keyboard("admin_knowledge"))
                else:
                    await safe_send_message(bot_instance, user_id,
                        "❌ Ошибка добавления записи",
                        reply_markup=create_back_keyboard("admin_knowledge"))
                del user_states[user_id]
            return
    
    await safe_send_message(bot_instance, user_id, 
        "🔍 Используйте кнопки меню\n\n<b>Команды:</b>\n/start - главное меню\n/tools - инструменты\n/help - помощь", 
        reply_markup=create_back_keyboard())

# ========== ОБРАБОТЧИКИ CALLBACK ==========

async def handle_callback_logic(callback: types.CallbackQuery, bot_instance: Bot, is_mirror: bool = False):
    """Универсальная обработка callback"""
    user_id = callback.from_user.id
    message_id = callback.message.message_id
    chat_id = callback.message.chat.id
    data = callback.data
    
    no_check_callbacks = ["check_subscription", "admin_panel", "admin_stats", "admin_users", 
                          "admin_channels", "admin_mirrors", "current_page"]
    
    if data not in no_check_callbacks and not data.startswith("page_"):
        subscribed, not_subscribed = await check_user_subscription(bot_instance, user_id)
        if not subscribed:
            keyboard = create_subscription_keyboard(not_subscribed)
            await safe_delete_message(bot_instance, chat_id, message_id)
            await safe_send_message(bot_instance, chat_id, "📢 Подпишитесь на каналы!", reply_markup=keyboard)
            await safe_answer_callback(callback)
            return
    
    try:
        if data == "check_subscription":
            subscribed, not_subscribed = await check_user_subscription(bot_instance, user_id)
            if subscribed:
                await safe_delete_message(bot_instance, chat_id, message_id)
                photo_path = 'start.png'
                caption = f"✅ Спасибо за подписку!\n\n🌐 {WEBSITE_URL}\n\nВыберите действие:"
                if os.path.exists(photo_path):
                    await safe_send_photo(bot_instance, chat_id, photo_path, caption, reply_markup=create_start_keyboard())
                else:
                    await safe_send_message(bot_instance, chat_id, caption, reply_markup=create_start_keyboard())
            else:
                keyboard = create_subscription_keyboard(not_subscribed)
                await safe_edit_message(bot_instance, chat_id, message_id, "❌ Вы не подписались на все каналы!", reply_markup=keyboard)
            await safe_answer_callback(callback)
        
        elif data == "back_to_main":
            await safe_delete_message(bot_instance, chat_id, message_id)
            photo_path = 'start.png'
            caption = f"🔍 <b>Главное меню</b>\n\n🌐 {WEBSITE_URL}\n\nВыберите действие:"
            if os.path.exists(photo_path):
                await safe_send_photo(bot_instance, chat_id, photo_path, caption, reply_markup=create_start_keyboard())
            else:
                await safe_send_message(bot_instance, chat_id, caption, reply_markup=create_start_keyboard())
        
        elif data == "leak_search":
            await safe_delete_message(bot_instance, chat_id, message_id)
            await safe_send_message(bot_instance, chat_id,
                "🔍 <b>Поиск утечек данных</b>\n\n"
                "<b>Введите данные для поиска:</b>\n\n"
                "<i>Примеры:</i>\n"
                "• example@gmail.com\n"
                "• +79991234567\n"
                "• username\n"
                "• ФИО",
                reply_markup=create_back_keyboard())
            user_states[user_id] = {"waiting_for": "search_query"}
        
        elif data == "tools_menu":
            await safe_delete_message(bot_instance, chat_id, message_id)
            await safe_send_message(bot_instance, chat_id, 
                "🛠️ <b>Инструменты OSINT</b>\n\nВыберите инструмент:", 
                reply_markup=create_tools_keyboard())
        
        elif data.startswith("tool_"):
            tool_name = data.replace("tool_", "")
            tool_prompts = {
                "whois": ("🔎 <b>WHOIS запрос</b>\n\nВведите домен:\n<i>Пример: example.com</i>", "whois"),
                "subdomains": ("🌐 <b>Поиск поддоменов</b>\n\nВведите домен:\n<i>Пример: example.com</i>", "subdomains"),
                "dns": ("📡 <b>DNS записи</b>\n\nВведите домен:\n<i>Пример: example.com</i>", "dns"),
                "reverse_dns": ("🔄 <b>Обратный DNS</b>\n\nВведите IP адрес:\n<i>Пример: 8.8.8.8</i>", "reverse_dns"),
                "site_relations": ("🔗 <b>Внешние ссылки</b>\n\nВведите URL:\n<i>Пример: example.com</i>", "site_relations"),
                "availability": ("📶 <b>Проверка доступности</b>\n\nВведите URL:\n<i>Пример: example.com</i>", "availability"),
                "content": ("📄 <b>Контент сайта</b>\n\nВведите URL:\n<i>Пример: example.com</i>", "content"),
                "server": ("🖥️ <b>Серверное ПО</b>\n\nВведите URL:\n<i>Пример: example.com</i>", "server"),
                "password": ("🔐 <b>Генератор паролей</b>\n\nВведите длину (8-64):\n<i>По умолчанию: 16</i>", "password"),
                "hash": ("🔒 <b>Хеширование</b>\n\nВведите текст для хеширования:", "hash"),
                "email": ("📧 <b>Валидация Email</b>\n\nВведите email адрес:", "email"),
                "phone": ("📱 <b>Анализ телефона</b>\n\nВведите номер телефона:", "phone"),
                "ip_geo": ("🌍 <b>IP Geolocation</b>\n\nВведите IP адрес:\n<i>Пример: 8.8.8.8</i>", "ip_geo"),
                "port_scan": ("🔍 <b>Port Scanner</b>\n\nВведите хост и порты:\n<i>Пример: example.com 80,443</i>", "port_scan"),
                "ssl": ("🔐 <b>SSL Info</b>\n\nВведите домен:\n<i>Пример: example.com</i>", "ssl")
            }
            
            if tool_name in tool_prompts:
                prompt, state_name = tool_prompts[tool_name]
                await safe_delete_message(bot_instance, chat_id, message_id)
                await safe_send_message(bot_instance, chat_id, prompt, reply_markup=create_back_keyboard("tools_menu"))
                user_states[user_id] = {"tool": state_name}
            await safe_answer_callback(callback)
        
        elif data == "dorking_menu":
            await safe_delete_message(bot_instance, chat_id, message_id)
            await safe_send_message(bot_instance, chat_id,
                "🕵️ <b>Dorking поиск</b>\n\n"
                "Поиск информации через поисковые системы\n\n"
                "Выберите тип поиска:",
                reply_markup=create_dorking_keyboard())
        
        elif data.startswith("dork_"):
            dork_type = data.replace("dork_", "")
            dork_prompts = {
                "username": ("👤 <b>Поиск по никнейму</b>\n\nВведите никнейм:", "username"),
                "email": ("📧 <b>Поиск по email</b>\n\nВведите email:", "email"),
                "phone": ("📱 <b>Поиск по телефону</b>\n\nВведите номер:", "phone"),
                "id": ("🆔 <b>Поиск по ID</b>\n\nВведите ID:", "id"),
                "domain": ("🌐 <b>Поиск по домену</b>\n\nВведите домен:", "domain"),
                "universal": ("🔍 <b>Универсальный поиск</b>\n\nВведите запрос:", "universal")
            }
            
            if dork_type in dork_prompts:
                prompt, state_name = dork_prompts[dork_type]
                await safe_delete_message(bot_instance, chat_id, message_id)
                await safe_send_message(bot_instance, chat_id, prompt, reply_markup=create_back_keyboard("dorking_menu"))
                user_states[user_id] = {"dorking": state_name}
            await safe_answer_callback(callback)
        
        elif data == "profile_menu":
            user_stats = get_user_stats(user_id)
            stats_text = "👤 <b>Ваш профиль</b>\n\n"
            if user_stats:
                stats_text += (
                    f"🆔 <b>ID:</b> <code>{user_stats[0]}</code>\n"
                    f"👤 <b>Имя:</b> {user_stats[2]}\n"
                    f"📅 <b>Регистрация:</b> {user_stats[4]}\n"
                    f"📊 <b>Запросов:</b> {user_stats[5]}"
                )
            await safe_delete_message(bot_instance, chat_id, message_id)
            await safe_send_message(bot_instance, chat_id, stats_text, reply_markup=create_profile_keyboard(user_id))
        
        elif data == "my_stats":
            user_stats = get_user_stats(user_id)
            stats_text = "📊 <b>Ваша статистика</b>\n\n"
            if user_stats:
                stats_text += (
                    f"📅 <b>Дата регистрации:</b> {user_stats[4]}\n"
                    f"📊 <b>Всего запросов:</b> {user_stats[5]}\n"
                    f"👑 <b>Статус:</b> {'Администратор' if is_admin(user_id) else 'Пользователь'}"
                )
            await safe_delete_message(bot_instance, chat_id, message_id)
            await safe_send_message(bot_instance, chat_id, stats_text, reply_markup=create_back_keyboard("profile_menu"))
        
        elif data == "mirrors_menu":
            if is_mirror:
                await safe_answer_callback(callback, "Доступно только в основном боте")
                return
            await safe_delete_message(bot_instance, chat_id, message_id)
            await safe_send_message(bot_instance, chat_id, 
                "🤖 <b>Управление зеркалами</b>\n\nСоздавайте копии бота с вашим токеном", 
                reply_markup=create_mirrors_keyboard())
        
        elif data == "create_mirror":
            await safe_delete_message(bot_instance, chat_id, message_id)
            await safe_send_message(bot_instance, chat_id,
                "🤖 <b>Создание зеркала</b>\n\n"
                "<b>Инструкция:</b>\n"
                "1. Перейдите к @BotFather\n"
                "2. Создайте нового бота (/newbot)\n"
                "3. Скопируйте токен\n"
                "4. Отправьте команду:\n\n"
                "<code>/mirror ваш_токен</code>",
                reply_markup=create_back_keyboard("mirrors_menu"))
        
        elif data == "my_mirrors":
            mirrors = get_mirror_bots(user_id)
            if mirrors:
                mirrors_text = "📋 <b>Ваши зеркала:</b>\n\n"
                for i, mirror in enumerate(mirrors, 1):
                    status = "🟢 Активно" if mirror[1] in active_bots else "🔴 Остановлено"
                    mirrors_text += f"{i}. <b>{mirror[3]}</b> {status}\n   <i>Создан: {mirror[4]}</i>\n\n"
            else:
                mirrors_text = "📋 У вас пока нет зеркал\n\nИспользуйте /mirror для создания"
            await safe_delete_message(bot_instance, chat_id, message_id)
            await safe_send_message(bot_instance, chat_id, mirrors_text, reply_markup=create_back_keyboard("mirrors_menu"))
        
        elif data == "delete_mirror":
            mirrors = get_mirror_bots(user_id)
            if mirrors:
                await safe_delete_message(bot_instance, chat_id, message_id)
                await safe_send_message(bot_instance, chat_id, 
                    "🗑️ <b>Удаление зеркала</b>\n\nВыберите зеркало для удаления:", 
                    reply_markup=create_mirror_delete_keyboard(mirrors))
            else:
                await safe_answer_callback(callback, "У вас нет зеркал")
        
        elif data.startswith("delete_mirror_"):
            token = data.replace("delete_mirror_", "")
            if remove_mirror_bot(token):
                await safe_answer_callback(callback, "✅ Зеркало удалено")
                await safe_delete_message(bot_instance, chat_id, message_id)
                await safe_send_message(bot_instance, chat_id, 
                    "✅ <b>Зеркало успешно удалено</b>", 
                    reply_markup=create_back_keyboard("mirrors_menu"))
            else:
                await safe_answer_callback(callback, "❌ Ошибка удаления")
        
        elif data == "mirrors_help":
            await safe_delete_message(bot_instance, chat_id, message_id)
            await safe_send_message(bot_instance, chat_id,
                "ℹ️ <b>Помощь по зеркалам</b>\n\n"
                "<b>Что такое зеркало?</b>\n"
                "Это копия бота с вашим токеном\n\n"
                "<b>Как создать:</b>\n"
                "1. Получите токен от @BotFather\n"
                "2. Используйте /mirror токен\n"
                "3. Зеркало запустится автоматически\n\n"
                "<b>Преимущества:</b>\n"
                "• Собственный бот\n"
                "• Полный функционал\n"
                "• Независимая работа",
                reply_markup=create_back_keyboard("mirrors_menu"))
        
        elif data == "help_menu":
            await safe_delete_message(bot_instance, chat_id, message_id)
            await safe_send_message(bot_instance, chat_id,
                "🆘 <b>Помощь</b>\n\n"
                "<b>Основные команды:</b>\n"
                "/start - главное меню\n"
                "/tools - инструменты\n"
                "/mirror - создать зеркало\n"
                "/profile - профиль\n"
                "/help - помощь\n\n"
                "<b>Возможности:</b>\n"
                "🔍 Поиск утечек данных\n"
                "🛠️ OSINT инструменты (13 инструментов)\n"
                "📚 База знаний\n"
                "🕵️ Dorking поиск\n"
                "🤖 Создание зеркал\n\n"
                f"🌐 <a href='{WEBSITE_URL}'>Наш сайт</a>",
                reply_markup=create_back_keyboard())
        
        elif data == "admin_panel":
            if not is_admin(user_id):
                await safe_answer_callback(callback, "⛔ Нет доступа")
                return
            await safe_delete_message(bot_instance, chat_id, message_id)
            await safe_send_message(bot_instance, chat_id, 
                "👑 <b>Админ панель</b>\n\nУправление ботом", 
                reply_markup=create_admin_keyboard())
        
        elif data == "admin_stats":
            if not is_admin(user_id):
                await safe_answer_callback(callback, "⛔ Нет доступа")
                return
            users = get_all_users()
            total_requests = sum(u[5] for u in users)
            mirrors = get_mirror_bots()
            channels = get_active_channels()
            
            stats_text = (
                "📊 <b>Статистика бота</b>\n\n"
                f"👥 <b>Пользователей:</b> {len(users)}\n"
                f"📊 <b>Запросов:</b> {total_requests}\n"
                f"🤖 <b>Зеркал:</b> {len(mirrors)} (активных: {len(active_bots)})\n"
                f"📢 <b>Каналов:</b> {len(channels)}"
            )
            await safe_delete_message(bot_instance, chat_id, message_id)
            await safe_send_message(bot_instance, chat_id, stats_text, reply_markup=create_back_keyboard("admin_panel"))
        
        elif data == "admin_users":
            if not is_admin(user_id):
                await safe_answer_callback(callback, "⛔ Нет доступа")
                return
            users = get_all_users()
            users_text = "👥 <b>Пользователи (топ 20):</b>\n\n"
            for i, u in enumerate(users[:20], 1):
                username = f"@{u[1]}" if u[1] else "Без ника"
                users_text += f"{i}. {u[2]} ({username}) - {u[5]} запросов\n"
            await safe_delete_message(bot_instance, chat_id, message_id)
            await safe_send_message(bot_instance, chat_id, users_text, reply_markup=create_back_keyboard("admin_panel"))
        
        elif data == "admin_channels":
            if not is_admin(user_id):
                await safe_answer_callback(callback, "⛔ Нет доступа")
                return
            await safe_delete_message(bot_instance, chat_id, message_id)
            await safe_send_message(bot_instance, chat_id, 
                "📢 <b>Управление каналами</b>\n\n"
                "Для добавления канала отправьте:\n"
                "<code>ID|Название|URL</code>\n\n"
                "<i>Пример:</i>\n"
                "<code>@channel|Мой канал|https://t.me/channel</code>", 
                reply_markup=create_channels_keyboard())
        
        elif data == "channel_list":
            if not is_admin(user_id):
                await safe_answer_callback(callback, "⛔ Нет доступа")
                return
            channels = get_active_channels()
            if channels:
                channels_text = "📋 <b>Список каналов:</b>\n\n"
                for i, ch in enumerate(channels, 1):
                    channels_text += f"{i}. <b>{ch[1]}</b>\n   ID: <code>{ch[0]}</code>\n   <a href='{ch[2]}'>Ссылка</a>\n\n"
            else:
                channels_text = "📋 Каналов нет"
            await safe_delete_message(bot_instance, chat_id, message_id)
            await safe_send_message(bot_instance, chat_id, channels_text, reply_markup=create_back_keyboard("admin_channels"))
        
        elif data == "admin_mirrors":
            if not is_admin(user_id):
                await safe_answer_callback(callback, "⛔ Нет доступа")
                return
            mirrors = get_mirror_bots()
            if mirrors:
                mirrors_text = "🤖 <b>Все зеркала:</b>\n\n"
                for i, m in enumerate(mirrors, 1):
                    status = "🟢" if m[1] in active_bots else "🔴"
                    mirrors_text += f"{i}. {status} <b>{m[3]}</b>\n   Владелец: <code>{m[2]}</code>\n   Создан: {m[4]}\n\n"
            else:
                mirrors_text = "🤖 Зеркал нет"
            await safe_delete_message(bot_instance, chat_id, message_id)
            await safe_send_message(bot_instance, chat_id, mirrors_text, reply_markup=create_back_keyboard("admin_panel"))
        
        elif data.startswith("page_"):
            parts = data.split("_")
            query_id = parts[1]
            page_id = int(parts[2])
            if query_id in cash_reports and 0 <= page_id < len(cash_reports[query_id]):
                report = cash_reports[query_id]
                markup = create_inline_keyboard(query_id, page_id, len(report))
                await safe_edit_message(bot_instance, chat_id, message_id, report[page_id], reply_markup=markup)
            await safe_answer_callback(callback)
        
        elif data == "current_page":
            await safe_answer_callback(callback, "Текущая страница")
        
        elif data == "knowledge_base_menu":
            knowledge_list = get_all_knowledge()
            if knowledge_list:
                await safe_delete_message(bot_instance, chat_id, message_id)
                await safe_send_message(bot_instance, chat_id,
                    f"📚 <b>База знаний</b>\n\n"
                    f"Доступно записей: {len(knowledge_list)}\n\n"
                    "Выберите статью:",
                    reply_markup=create_knowledge_keyboard(knowledge_list))
            else:
                await safe_answer_callback(callback, "📚 База знаний пуста")
        
        elif data.startswith("kb_view_"):
            kb_id = int(data.replace("kb_view_", ""))
            kb = get_knowledge_by_id(kb_id)
            if kb:
                content = kb[2]
                if len(content) > 4000:
                    content = content[:4000] + "\n\n⚠️ <i>Текст обрезан</i>"
                await safe_delete_message(bot_instance, chat_id, message_id)
                await safe_send_message(bot_instance, chat_id,
                    f"📄 <b>{kb[1]}</b>\n\n{content}",
                    reply_markup=create_back_keyboard("knowledge_base_menu"))
            else:
                await safe_answer_callback(callback, "❌ Запись не найдена")
        
        elif data == "admin_knowledge":
            if not is_admin(user_id):
                await safe_answer_callback(callback, "⛔ Нет доступа")
                return
            await safe_delete_message(bot_instance, chat_id, message_id)
            await safe_send_message(bot_instance, chat_id,
                "📚 <b>Управление базой знаний</b>\n\n"
                "Добавляйте статьи и справочную информацию",
                reply_markup=create_admin_knowledge_keyboard())
        
        elif data == "kb_add":
            if not is_admin(user_id):
                await safe_answer_callback(callback, "⛔ Нет доступа")
                return
            await safe_delete_message(bot_instance, chat_id, message_id)
            await safe_send_message(bot_instance, chat_id,
                "➕ <b>Добавление в базу знаний</b>\n\n"
                "Отправьте название статьи:",
                reply_markup=create_back_keyboard("admin_knowledge"))
            user_states[user_id] = {"kb_title": None}
        
        elif data == "kb_list":
            if not is_admin(user_id):
                await safe_answer_callback(callback, "⛔ Нет доступа")
                return
            knowledge_list = get_all_knowledge()
            if knowledge_list:
                kb_text = "📋 <b>Список статей:</b>\n\n"
                for i, kb in enumerate(knowledge_list[:20], 1):
                    kb_text += f"{i}. <b>{kb[1]}</b>\n   <i>Создано: {kb[3]}</i>\n\n"
                await safe_delete_message(bot_instance, chat_id, message_id)
                await safe_send_message(bot_instance, chat_id, kb_text,
                    reply_markup=create_back_keyboard("admin_knowledge"))
            else:
                await safe_answer_callback(callback, "📚 База знаний пуста")
        
        elif data == "kb_delete":
            if not is_admin(user_id):
                await safe_answer_callback(callback, "⛔ Нет доступа")
                return
            knowledge_list = get_all_knowledge()
            if knowledge_list:
                await safe_delete_message(bot_instance, chat_id, message_id)
                await safe_send_message(bot_instance, chat_id,
                    "🗑️ <b>Удаление статьи</b>\n\nВыберите статью для удаления:",
                    reply_markup=create_knowledge_delete_keyboard(knowledge_list))
            else:
                await safe_answer_callback(callback, "📚 База знаний пуста")
        
        elif data.startswith("kb_del_"):
            if not is_admin(user_id):
                await safe_answer_callback(callback, "⛔ Нет доступа")
                return
            kb_id = int(data.replace("kb_del_", ""))
            kb = get_knowledge_by_id(kb_id)
            if kb and delete_knowledge(kb_id):
                await safe_answer_callback(callback, "✅ Статья удалена")
                knowledge_list = get_all_knowledge()
                if knowledge_list:
                    await safe_edit_message(bot_instance, chat_id, message_id,
                        "🗑️ <b>Удаление статьи</b>\n\nВыберите статью для удаления:",
                        reply_markup=create_knowledge_delete_keyboard(knowledge_list))
                else:
                    await safe_delete_message(bot_instance, chat_id, message_id)
                    await safe_send_message(bot_instance, chat_id,
                        "📚 База знаний пуста",
                        reply_markup=create_back_keyboard("admin_knowledge"))
            else:
                await safe_answer_callback(callback, "❌ Ошибка удаления")
        
        else:
            await safe_answer_callback(callback, "⚙️ В разработке")
    
    except Exception as e:
        logger.error(f"Ошибка callback {data}: {e}")
        await safe_answer_callback(callback, "❌ Ошибка")

# ========== ОСНОВНОЙ БОТ ==========

async def main():
    bot = Bot(token=MAIN_BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    
    @dp.message(Command("start"))
    async def start_handler(message: types.Message, state: FSMContext):
        user_id = message.from_user.id
        username = message.from_user.username
        first_name = message.from_user.first_name or "Пользователь"
        last_name = message.from_user.last_name
        add_user(user_id, username, first_name, last_name)
        
        subscribed, not_subscribed = await check_user_subscription(bot, user_id)
        if not subscribed:
            keyboard = create_subscription_keyboard(not_subscribed)
            await safe_send_message(bot, user_id, 
                f"👋 <b>Добро пожаловать, {first_name}!</b>\n\n"
                "📢 Для использования бота подпишитесь на каналы:", 
                reply_markup=keyboard)
            return
        
        photo_path = 'start.png'
        caption = (
            f"👋 <b>Добро пожаловать, {first_name}!</b>\n\n"
            f"🌐 {WEBSITE_URL}\n\n"
            "🔍 <b>Возможности бота:</b>\n"
            "• Поиск утечек данных\n"
            "• 13 OSINT инструментов\n"
            "• Dorking поиск\n"
            "• База знаний\n"
            "• Создание зеркал\n\n"
            "Выберите действие:"
        )
        
        if os.path.exists(photo_path):
            await safe_send_photo(bot, user_id, photo_path, caption, reply_markup=create_start_keyboard())
        else:
            await safe_send_message(bot, user_id, caption, reply_markup=create_start_keyboard())
        await state.clear()
    
    @dp.message(Command("mirror"))
    async def mirror_command(message: types.Message, state: FSMContext):
        user_id = message.from_user.id
        text = message.text.strip()
        
        if len(text.split()) < 2:
            await message.answer(
                "🤖 <b>Создание зеркала</b>\n\n"
                "<b>Использование:</b>\n"
                "<code>/mirror токен_бота</code>\n\n"
                "<i>Получите токен у @BotFather</i>"
            )
            return
        
        bot_token = text.split()[1].strip()
        if not bot_token or bot_token.count(':') != 1:
            await message.answer("❌ Неверный формат токена!\n\n<i>Токен должен содержать ':'</i>")
            return
        
        msg = await message.answer("⏳ Создаю зеркало...")
        
        try:
            test_bot = Bot(token=bot_token)
            bot_info = await test_bot.get_me()
            bot_name = bot_info.first_name
            
            success, result = create_mirror_bot_instance(bot_token, user_id, bot_name)
            
            if success:
                await msg.edit_text(
                    f"✅ <b>Зеркало создано!</b>\n\n"
                    f"🤖 <b>Имя:</b> {result}\n"
                    f"🔗 <b>Ссылка:</b> https://t.me/{bot_info.username}\n\n"
                    f"<i>Зеркало запущено и готово к работе</i>",
                    reply_markup=create_back_keyboard("mirrors_menu")
                )
            else:
                await msg.edit_text(
                    f"❌ <b>Ошибка создания:</b>\n\n{result}",
                    reply_markup=create_back_keyboard("mirrors_menu")
                )
            
            await test_bot.session.close()
        except Exception as e:
            logger.error(f"Ошибка создания зеркала: {e}")
            await msg.edit_text(
                "❌ <b>Ошибка!</b>\n\n"
                "Проверьте правильность токена",
                reply_markup=create_back_keyboard("mirrors_menu")
            )
    
    @dp.message(Command("admin"))
    async def admin_command(message: types.Message):
        user_id = message.from_user.id
        if not is_admin(user_id):
            await message.answer("⛔ Нет доступа")
            return
        await safe_send_message(bot, user_id, "👑 Админ панель", reply_markup=create_admin_keyboard())
    
    @dp.message(Command("tools"))
    async def tools_command(message: types.Message):
        await safe_send_message(bot, message.chat.id, 
            "🛠️ <b>Инструменты OSINT</b>\n\n<b>Доступно 14 инструментов</b>\n\nВыберите инструмент:", 
            reply_markup=create_tools_keyboard())
    
    @dp.message(Command("profile"))
    async def profile_command(message: types.Message):
        user_id = message.from_user.id
        user_stats = get_user_stats(user_id)
        stats_text = "👤 <b>Ваш профиль</b>\n\n"
        if user_stats:
            stats_text += (
                f"🆔 <b>ID:</b> <code>{user_stats[0]}</code>\n"
                f"👤 <b>Имя:</b> {user_stats[2]}\n"
                f"📊 <b>Запросов:</b> {user_stats[5]}"
            )
        await safe_send_message(bot, user_id, stats_text, reply_markup=create_profile_keyboard(user_id))
    
    @dp.message(Command("help"))
    async def help_command(message: types.Message):
        help_text = (
            "🆘 <b>Помощь</b>\n\n"
            "<b>Команды:</b>\n"
            "/start - главное меню\n"
            "/tools - инструменты\n"
            "/mirror - создать зеркало\n"
            "/profile - профиль\n"
            "/help - помощь\n\n"
            f"🌐 <a href='{WEBSITE_URL}'>Наш сайт</a>"
        )
        await safe_send_message(bot, message.chat.id, help_text, reply_markup=create_back_keyboard())
    
    @dp.callback_query()
    async def callback_handler(callback: types.CallbackQuery, state: FSMContext):
        await handle_callback_logic(callback, bot)
    
    @dp.message()
    async def message_handler(message: types.Message, state: FSMContext):
        user_id = message.from_user.id
        text = message.text.strip() if message.text else ""
        
        if not text:
            return
        
        subscribed, not_subscribed = await check_user_subscription(bot, user_id)
        if not subscribed:
            keyboard = create_subscription_keyboard(not_subscribed)
            await safe_send_message(bot, user_id, "📢 Подпишитесь на каналы!", reply_markup=keyboard)
            return
        
        if is_admin(user_id):
            if "|" in text and text.count("|") == 2:
                try:
                    channel_id, channel_name, channel_url = [x.strip() for x in text.split("|")]
                    if add_channel(channel_id, channel_name, channel_url):
                        await safe_send_message(bot, user_id, 
                            f"✅ <b>Канал добавлен!</b>\n\n"
                            f"<b>Название:</b> {channel_name}\n"
                            f"<b>ID:</b> <code>{channel_id}</code>\n"
                            f"<b>URL:</b> {channel_url}")
                    else:
                        await safe_send_message(bot, user_id, "❌ Ошибка добавления канала")
                    return
                except Exception as e:
                    await safe_send_message(bot, user_id, f"❌ Ошибка: {str(e)}")
                    return
            elif user_id in user_states and "kb_title" in user_states[user_id]:
                # Обработка уже в handle_message_logic
                pass
        
        await handle_message_logic(message, bot)
    
    logger.info("🤖 Запуск polling основного бота...")
    await dp.start_polling(bot, skip_updates=True)

# ========== ЗАПУСК ==========

if __name__ == "__main__":
    print("=" * 60)
    print("🤖 ЗАПУСК БОТА POLARSEARCH")
    print("=" * 60)
    
    if not os.path.exists('start.png'):
        print("📷 Создание изображения...")
        try:
            import PIL
            asyncio.run(create_default_photo())
        except:
            print("⚠️ Pillow не установлен, создаю заглушку")
            with open('start.png', 'w') as f:
                f.write('Photo placeholder')
    
    try:
        init_database()
        print("✅ База данных инициализирована")
    except Exception as e:
        print(f"❌ Ошибка БД: {e}")
        exit(1)
    
    try:
        REQUIRED_CHANNELS = get_active_channels()
        print(f"✅ Загружено каналов: {len(REQUIRED_CHANNELS)}")
    except Exception as e:
        print(f"❌ Ошибка загрузки каналов: {e}")
    
    try:
        existing_mirrors = get_mirror_bots()
        print(f"✅ Найдено зеркал: {len(existing_mirrors)}")
        for mirror in existing_mirrors:
            try:
                create_mirror_bot_instance(mirror[1], mirror[2], mirror[3])
                print(f"✅ Запущено зеркало: {mirror[3]}")
                time.sleep(1)  # Задержка между запусками
            except Exception as e:
                print(f"❌ Ошибка запуска зеркала {mirror[3]}: {e}")
    except Exception as e:
        print(f"❌ Ошибка загрузки зеркал: {e}")
    
    print(f"👑 Администраторы: {ADMIN_IDS}")
    print(f"🌐 Сайт: {WEBSITE_URL}")
    print("=" * 60)
    print("✅ БОТ ЗАПУЩЕН И ГОТОВ К РАБОТЕ!")
    print("=" * 60)
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Бот остановлен пользователем")
        # Остановка всех зеркал
        for token, task in mirror_tasks.items():
            task.cancel()
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()
