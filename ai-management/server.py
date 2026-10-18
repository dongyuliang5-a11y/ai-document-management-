from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import base64
from http import cookies
import hashlib
import hmac
import json
from mimetypes import guess_type
import os
from pathlib import Path
import secrets
import sqlite3
import time
from urllib.parse import unquote, urlparse


BASE_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = BASE_DIR / "public"
DB_PATH = Path(os.environ.get("AI_MANAGEMENT_DB_PATH", BASE_DIR / "app.db")).expanduser()
SESSION_COOKIE = "ai_management_session"
SESSION_TTL_SECONDS = 60 * 60 * 8
MAX_DOCUMENT_SIZE_BYTES = 5 * 1024 * 1024
SECURE_COOKIE = os.environ.get("AI_MANAGEMENT_SECURE_COOKIE", "0").lower() in {
    "1",
    "true",
    "yes",
}
SESSIONS: dict[str, dict] = {}
ROUTES = {
    "/": "index.html",
    "/login": "login.html",
    "/models": "models.html",
    "/tasks": "tasks.html",
    "/datasets": "datasets.html",
    "/monitoring": "monitoring.html",
    "/settings": "settings.html",
}
PROTECTED_ROUTES = {"/", "/models", "/tasks", "/datasets", "/monitoring", "/settings"}
DEFAULT_USERNAME = os.environ.get("AI_MANAGEMENT_USERNAME", "admin")
DEFAULT_PASSWORD = os.environ.get("AI_MANAGEMENT_PASSWORD", "admin123")


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
    return f"{base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt_text, digest_text = stored_hash.split("$", 1)
        salt = base64.b64decode(salt_text)
        expected = base64.b64decode(digest_text)
    except ValueError:
        return False

    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
    return hmac.compare_digest(actual, expected)


def get_db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_db() as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        user_count = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if user_count == 0:
            connection.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                (DEFAULT_USERNAME, hash_password(DEFAULT_PASSWORD)),
            )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                content_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                content BLOB NOT NULL,
                uploaded_by INTEGER NOT NULL,
                uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (uploaded_by) REFERENCES users(id)
            )
            """
        )


class SimpleHandler(BaseHTTPRequestHandler):
    def _get_json_body(self) -> dict:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0:
            return {}

        try:
            return json.loads(self.rfile.read(content_length).decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def _send_json(self, status_code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, status_code: int, path: Path, content_type: str) -> None:
        body = path.read_bytes()
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()

    def _set_session_cookie(self, token: str) -> None:
        secure = "; Secure" if SECURE_COOKIE else ""
        self.send_header(
            "Set-Cookie",
            f"{SESSION_COOKIE}={token}; HttpOnly; SameSite=Lax; Path=/; "
            f"Max-Age={SESSION_TTL_SECONDS}{secure}",
        )

    def _clear_session_cookie(self) -> None:
        secure = "; Secure" if SECURE_COOKIE else ""
        self.send_header(
            "Set-Cookie",
            f"{SESSION_COOKIE}=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0{secure}",
        )

    def _get_session_token(self) -> str | None:
        cookie_header = self.headers.get("Cookie")
        if not cookie_header:
            return None

        parsed = cookies.SimpleCookie()
        parsed.load(cookie_header)
        morsel = parsed.get(SESSION_COOKIE)
        return morsel.value if morsel else None

    def _current_user(self) -> dict | None:
        token = self._get_session_token()
        if not token:
            return None

        session = SESSIONS.get(token)
        if not session:
            return None

        if session["expires_at"] < time.time():
            SESSIONS.pop(token, None)
            return None

        return session["user"]

    def _create_session(self, user: sqlite3.Row) -> str:
        token = secrets.token_urlsafe(32)
        SESSIONS[token] = {
            "expires_at": time.time() + SESSION_TTL_SECONDS,
            "user": {"id": user["id"], "username": user["username"]},
        }
        return token

    def _serve_path(self, path: str) -> None:
        if path in PROTECTED_ROUTES and not self._current_user():
            self._send_redirect("/login")
            return

        route_file = ROUTES.get(path)
        if route_file:
            self._send_file(200, PUBLIC_DIR / route_file, "text/html; charset=utf-8")
            return

        static_path = (PUBLIC_DIR / unquote(path.lstrip("/"))).resolve()
        if PUBLIC_DIR in static_path.parents and static_path.is_file():
            content_type = guess_type(static_path.name)[0] or "application/octet-stream"
            self._send_file(200, static_path, content_type)
            return

        self._send_json(404, {"error": "Not Found"})

    def do_GET(self) -> None:
        path = urlparse(self.path).path

        if path == "/health":
            self._send_json(200, {"status": "ok"})
            return

        if path == "/api/me":
            user = self._current_user()
            if not user:
                self._send_json(401, {"authenticated": False})
                return
            self._send_json(200, {"authenticated": True, "user": user})
            return

        if path == "/api/documents":
            user = self._current_user()
            if not user:
                self._send_json(401, {"error": "Authentication required"})
                return

            with get_db() as connection:
                rows = connection.execute(
                    """
                    SELECT
                        documents.id,
                        documents.filename,
                        documents.content_type,
                        documents.size_bytes,
                        documents.uploaded_at,
                        users.username AS uploaded_by_username
                    FROM documents
                    JOIN users ON users.id = documents.uploaded_by
                    ORDER BY documents.id DESC
                    """
                ).fetchall()

            self._send_json(
                200,
                {
                    "documents": [
                        {
                            "id": row["id"],
                            "filename": row["filename"],
                            "content_type": row["content_type"],
                            "size_bytes": row["size_bytes"],
                            "uploaded_at": row["uploaded_at"],
                            "uploaded_by_username": row["uploaded_by_username"],
                        }
                        for row in rows
                    ]
                },
            )
            return

        self._serve_path(path)

    def do_POST(self) -> None:
        path = urlparse(self.path).path

        if path == "/api/login":
            body = self._get_json_body()
            username = str(body.get("username", "")).strip()
            password = str(body.get("password", ""))

            with get_db() as connection:
                user = connection.execute(
                    "SELECT id, username, password_hash FROM users WHERE username = ?",
                    (username,),
                ).fetchone()

            if not user or not verify_password(password, user["password_hash"]):
                self._send_json(401, {"error": "Invalid username or password"})
                return

            response = json.dumps(
                {"authenticated": True, "user": {"id": user["id"], "username": user["username"]}}
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(response)))
            self._set_session_cookie(self._create_session(user))
            self.end_headers()
            self.wfile.write(response)
            return

        if path == "/api/logout":
            token = self._get_session_token()
            if token:
                SESSIONS.pop(token, None)

            response = json.dumps({"authenticated": False}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(response)))
            self._clear_session_cookie()
            self.end_headers()
            self.wfile.write(response)
            return

        if path == "/api/documents":
            user = self._current_user()
            if not user:
                self._send_json(401, {"error": "Authentication required"})
                return

            body = self._get_json_body()
            filename = str(body.get("filename", "")).strip()
            content_type = str(body.get("content_type", "application/octet-stream")).strip()
            content_base64 = str(body.get("content_base64", "")).strip()

            if not filename or not content_base64:
                self._send_json(400, {"error": "filename and content_base64 are required"})
                return

            try:
                content = base64.b64decode(content_base64, validate=True)
            except (ValueError, base64.binascii.Error):
                self._send_json(400, {"error": "Invalid base64 content"})
                return

            size_bytes = len(content)
            if size_bytes == 0:
                self._send_json(400, {"error": "Document is empty"})
                return

            if size_bytes > MAX_DOCUMENT_SIZE_BYTES:
                self._send_json(400, {"error": "Document exceeds 5 MB limit"})
                return

            with get_db() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO documents (filename, content_type, size_bytes, content, uploaded_by)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (filename, content_type or "application/octet-stream", size_bytes, content, user["id"]),
                )

            self._send_json(
                201,
                {
                    "document": {
                        "id": cursor.lastrowid,
                        "filename": filename,
                        "content_type": content_type or "application/octet-stream",
                        "size_bytes": size_bytes,
                        "uploaded_by_username": user["username"],
                    }
                },
            )
            return

        self._send_json(404, {"error": "Not Found"})


if __name__ == "__main__":
    init_db()
    host = "0.0.0.0"
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer((host, port), SimpleHandler)
    print(f"Server started at http://{host}:{port}; database={DB_PATH}", flush=True)
    server.serve_forever()
