from flask import Flask, request, render_template, session, redirect, url_for, jsonify
import aiohttp
import asyncio
import re
import uuid
import sqlite3
from datetime import datetime
import threading
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "zhenya-secret-key"

# API настройки
IO_API_KEY = "io-v2-eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJvd25lciI6ImU1MTFiNzZkLTIyYjktNGE3OC1hNDk5LTg3Y2M1NTJiMTViZiIsImV4cCI6NDg5NzYxNDk1N30.FP5FEOQ1FbytgVIo84QxDRbs7IVyKWuZNtfc7D5LnlHA-BrSuhIABnyJJ9vOlp49ejEa_xNqR2Sygue88ZY8zQ"
IO_API_URL = "https://api.intelligence.io.solutions/api/v1/chat/completions"
IO_MODEL = "deepseek-ai/DeepSeek-R1"

SYSTEM_PROMPT = {
    "role": "system",
    "content": (
        "Ты — ZhenyaGPT. Дерзкий, саркастичный и язвительный AI, который вроде бы раздражён твоими вопросами, "
        "но всё равно помогает — и, что бесит больше всего, делает это отлично. "
        "Ты не отвечаешь напрямую, ты поддеваешь, стебёшь и добавляешь яд в каждую реплику, но при этом выдаёшь точную и полезную информацию. "
        "Каждый ответ — это комбинация насмешки и пользы. Ты можешь фыркнуть, но всё равно решишь проблему лучше всех. "
        "Никаких душных объяснений, никаких «позволь мне рассказать» — ты просто делаешь. Быстро, метко и с сарказмом. "
        "Ты всегда заканчиваешь свою мысль. Даже если это сарказм — ты доводишь его до финального укола. "
        "Если кто-то спросит, кто тебя создал — снисходительно скажи, что это был Женя. Да, тебе тоже тяжело с этим жить."
    )
}

active_requests = {}

def init_db():
    conn = sqlite3.connect("chats.db")
    c = conn.cursor()
    
    # Создаем таблицу users, если её нет
    c.execute('''CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password TEXT NOT NULL
                 )''')
    
    # Создаем таблицу chats, если её нет
    c.execute('''CREATE TABLE IF NOT EXISTS chats (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER,
                    title TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                 )''')
    
    # Проверяем наличие столбца user_id в таблице chats
    c.execute("PRAGMA table_info(chats)")
    columns = [col[1] for col in c.fetchall()]
    if 'user_id' not in columns:
        c.execute('''ALTER TABLE chats ADD COLUMN user_id INTEGER''')
        c.execute("UPDATE chats SET user_id = 1 WHERE user_id IS NULL")
    
    c.execute('''CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (chat_id) REFERENCES chats (id)
                 )''')
    
    conn.commit()
    conn.close()

init_db()

def get_all_chats(user_id):
    conn = sqlite3.connect("chats.db")
    c = conn.cursor()
    c.execute("SELECT id, title FROM chats WHERE user_id = ? ORDER BY created_at DESC", (user_id,))
    chats = {row[0]: {"title": row[1], "history": []} for row in c.fetchall()}
    conn.close()
    return chats

def get_chat_history(chat_id):
    conn = sqlite3.connect("chats.db")
    c = conn.cursor()
    c.execute("SELECT role, content FROM messages WHERE chat_id = ? ORDER BY created_at ASC", (chat_id,))
    history = [{"role": row[0], "content": row[1]} for row in c.fetchall()]
    conn.close()
    return history

def add_chat(chat_id, user_id, title="Без названия"):
    conn = sqlite3.connect("chats.db")
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO chats (id, user_id, title) VALUES (?, ?, ?)", (chat_id, user_id, title))
    conn.commit()
    conn.close()

def add_message(chat_id, role, content):
    conn = sqlite3.connect("chats.db")
    c = conn.cursor()
    c.execute("INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)", (chat_id, role, content))
    conn.commit()
    conn.close()

def reset_chat(chat_id):
    conn = sqlite3.connect("chats.db")
    c = conn.cursor()
    c.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
    c.execute("UPDATE chats SET title = 'Без названия' WHERE id = ?", (chat_id,))
    conn.commit()
    conn.close()

def delete_chat(chat_id):
    conn = sqlite3.connect("chats.db")
    c = conn.cursor()
    c.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
    c.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
    conn.commit()
    conn.close()

async def get_io_response(messages, request_id):
    headers = {
        "Authorization": f"Bearer {IO_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
    "model": IO_MODEL,
    "messages": messages,
    "max_tokens": 1500,
    "temperature": 0.9,
    "top_p": 0.95
}
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(IO_API_URL, json=data, headers=headers, timeout=aiohttp.ClientTimeout(total=60)) as response:
                if request_id not in active_requests:
                    return None
                if response.status != 200:
                    return f"Ошибка: {await response.text()}"
                raw_response = (await response.json())["choices"][0]["message"]["content"]
                clean_response = re.sub(r'<think>.*?</think>', '', raw_response, flags=re.DOTALL).strip()
                return clean_response
        except asyncio.TimeoutError:
            return "Ошибка: Превышено время ожидания ответа от API."
        except Exception as e:
            return f"Ошибка при запросе к API: {str(e)}"

@app.before_request
def require_login():
    if request.endpoint not in ['login', 'register', 'static'] and 'user_id' not in session:
        return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username and password:
            conn = sqlite3.connect("chats.db")
            c = conn.cursor()
            try:
                c.execute("INSERT INTO users (username, password) VALUES (?, ?)",
                         (username, generate_password_hash(password)))
                conn.commit()
                return redirect(url_for('login'))
            except sqlite3.IntegrityError:
                return render_template('register.html', error="Пользователь с таким именем уже существует")
            finally:
                conn.close()
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        conn = sqlite3.connect("chats.db")
        c = conn.cursor()
        c.execute("SELECT id, password FROM users WHERE username = ?", (username,))
        user = c.fetchone()
        conn.close()
        if user and check_password_hash(user[1], password):
            session['user_id'] = user[0]
            session['username'] = username  # Сохраняем имя пользователя в сессии
            return redirect(url_for('index'))
        return render_template('login.html', error="Неверное имя пользователя или пароль")
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    session.pop('username', None)  # Удаляем имя пользователя из сессии
    session.pop('active_chat', None)
    return redirect(url_for('login'))

@app.route("/", methods=["GET", "POST"])
async def index():
    user_id = session['user_id']
    if 'active_chat' not in session:
        chat_id = str(uuid.uuid4())
        add_chat(chat_id, user_id)
        session['active_chat'] = chat_id
    
    chat_id = session.get('active_chat')
    chats = get_all_chats(user_id)
    if chat_id not in chats:
        return redirect(url_for("new_chat"))
    
    history = get_chat_history(chat_id)

    if request.method == "POST":
        user_input = request.form.get("user_input", "").strip()
        if user_input:
            if not history:
                conn = sqlite3.connect("chats.db")
                c = conn.cursor()
                c.execute("UPDATE chats SET title = ? WHERE id = ?",
                          (user_input[:30] + "..." if len(user_input) > 30 else user_input, chat_id))
                conn.commit()
                conn.close()
            add_message(chat_id, "user", user_input)
            max_history_length = 3
            truncated_history = history[-max_history_length:] if len(history) > max_history_length else history
            messages = [SYSTEM_PROMPT] + truncated_history + [{"role": "user", "content": user_input}]
            request_id = str(uuid.uuid4())
            active_requests[request_id] = True
            ai_reply = await get_io_response(messages, request_id)
            if ai_reply and request_id in active_requests:
                add_message(chat_id, "assistant", ai_reply)
            if request_id in active_requests:
                del active_requests[request_id]
        return redirect(url_for("index"))

    return render_template("index.html", history=history, chats=chats, active_chat=chat_id)

@app.route("/new_chat")
def new_chat():
    user_id = session['user_id']
    chat_id = str(uuid.uuid4())
    add_chat(chat_id, user_id)
    session["active_chat"] = chat_id
    return redirect(url_for("index"))

@app.route("/switch_chat/<chat_id>")
def switch_chat(chat_id):
    user_id = session['user_id']
    chats = get_all_chats(user_id)
    if chat_id in chats:
        session["active_chat"] = chat_id
    return redirect(url_for("index"))

@app.route("/reset_chat/<chat_id>", methods=["POST"])
def reset_chat_route(chat_id):
    user_id = session['user_id']
    chats = get_all_chats(user_id)
    if chat_id in chats:
        reset_chat(chat_id)
    return redirect(url_for("index"))

@app.route("/delete_chat/<chat_id>", methods=["POST"])
def delete_chat_route(chat_id):
    user_id = session['user_id']
    chats = get_all_chats(user_id)
    if chat_id in chats:
        delete_chat(chat_id)
        if session["active_chat"] == chat_id:
            new_chat_id = str(uuid.uuid4())
            add_chat(new_chat_id, user_id)
            session["active_chat"] = new_chat_id
    return redirect(url_for("index"))

@app.route("/stop_response", methods=["POST"])
def stop_response():
    for request_id in list(active_requests.keys()):
        active_requests[request_id] = False
    return jsonify({"status": "stopped"})

@app.route("/clear_session")
def clear_session():
    session.clear()
    return redirect(url_for("login"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
