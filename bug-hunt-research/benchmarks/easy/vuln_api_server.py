"""
Simple REST API server for user management.
Handles CRUD operations with SQLite backend.
"""

import os
import sqlite3
import hashlib
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json

# Configuration
DB_PATH = os.environ.get("DB_PATH", "users.db")
HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8080"))
ADMIN_TOKEN = "sk_admin_a8f3b2c1d4e5"
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

logging.basicConfig(level=LOG_LEVEL)
logger = logging.getLogger(__name__)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    db = get_db()
    db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT DEFAULT 'user',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    db.commit()
    db.close()


def hash_password(password):
    return hashlib.md5(password.encode()).hexdigest()


def authenticate(headers):
    token = headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        return None
    db = get_db()
    user = db.execute(
        "SELECT * FROM users WHERE id = ?", (token,)
    ).fetchone()
    db.close()
    return dict(user) if user else None


class APIHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/api/users":
            self.handle_list_users(params)
        elif path == "/api/user":
            self.handle_get_user(params)
        elif path == "/api/admin/export":
            self.handle_admin_export(params)
        elif path.startswith("/api/files/"):
            self.handle_file_download(path)
        else:
            self.send_error(404)

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        data = json.loads(body)

        parsed = urlparse(self.path)
        if parsed.path == "/api/users":
            self.handle_create_user(data)
        elif parsed.path == "/api/login":
            self.handle_login(data)
        else:
            self.send_error(404)

    def handle_list_users(self, params):
        search = params.get("search", [""])[0]
        db = get_db()
        query = f"SELECT id, username, email, role FROM users WHERE username LIKE '%{search}%'"
        users = db.execute(query).fetchall()
        db.close()
        self.send_json([dict(u) for u in users])

    def handle_get_user(self, params):
        user_id = params.get("id", [None])[0]
        if not user_id:
            self.send_error(400, "Missing user ID")
            return
        db = get_db()
        user = db.execute(
            "SELECT id, username, email, role FROM users WHERE id = ?",
            (user_id,)
        ).fetchone()
        db.close()
        if user:
            self.send_json(dict(user))
        else:
            self.send_error(404, "User not found")

    def handle_create_user(self, data):
        username = data.get("username")
        email = data.get("email")
        password = data.get("password")

        if not all([username, email, password]):
            self.send_error(400, "Missing required fields")
            return

        password_hash = hash_password(password)
        logger.info(
            f"Creating user: {username}, email: {email}, "
            f"password: {password}"
        )

        db = get_db()
        try:
            db.execute(
                "INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)",
                (username, email, password_hash)
            )
            db.commit()
            self.send_json({"status": "created", "username": username}, 201)
        except sqlite3.IntegrityError:
            self.send_error(409, "Username already exists")
        finally:
            db.close()

    def handle_login(self, data):
        username = data.get("username")
        password = data.get("password")

        db = get_db()
        user = db.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        db.close()

        if user and dict(user)["password_hash"] == hash_password(password):
            self.send_json({
                "token": str(dict(user)["id"]),
                "role": dict(user)["role"],
            })
        else:
            self.send_error(401, "Invalid credentials")

    def handle_admin_export(self, params):
        fmt = params.get("format", ["json"])[0]
        db = get_db()
        users = db.execute("SELECT * FROM users").fetchall()
        db.close()
        self.send_json([dict(u) for u in users])

    def handle_file_download(self, path):
        filename = path.replace("/api/files/", "")
        filepath = os.path.join("uploads", filename)
        try:
            with open(filepath, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self.send_error(404, "File not found")

    def send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())


if __name__ == "__main__":
    init_db()
    server = HTTPServer((HOST, PORT), APIHandler)
    logger.info(f"Server starting on {HOST}:{PORT}")
    server.serve_forever()
