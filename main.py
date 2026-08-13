"""Visibility Test Data Builder — FastAPI application."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

import logging
import traceback

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator
from starlette.middleware.sessions import SessionMiddleware

logger = logging.getLogger("vtdb")

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "app.db"
SECRET_PATH = BASE_DIR / ".session_secret"
TECHNICAL_KEY_RE = re.compile(r"^[A-Z][a-z0-9]*(?: [a-z0-9]+)*$")

ROLE_ADMIN = "admin"
ROLE_AUTOMATION = "automation"
ROLE_MANUAL = "manual_tester"
VALID_ROLES = {ROLE_ADMIN, ROLE_AUTOMATION, ROLE_MANUAL}
ROLE_LABELS = {
    ROLE_ADMIN: "Admin",
    ROLE_AUTOMATION: "Automation",
    ROLE_MANUAL: "Manual tester",
}


def get_secret_key() -> str:
    env_key = os.environ.get("VTDB_SECRET")
    if env_key:
        return env_key
    if SECRET_PATH.exists():
        return SECRET_PATH.read_text(encoding="utf-8").strip()
    key = secrets.token_hex(32)
    SECRET_PATH.write_text(key, encoding="utf-8")
    return key


app = FastAPI(title="Visibility Test Data Builder")
app.add_middleware(
    SessionMiddleware,
    secret_key=get_secret_key(),
    session_cookie="vtdb_session",
    max_age=60 * 60 * 12,
    same_site="lax",
    https_only=False,
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception):
    """Log full traceback so Internal Server Error is diagnosable in the console."""
    logger.error("Unhandled error on %s %s", request.method, request.url.path)
    traceback.print_exc()
    return PlainTextResponse("Internal Server Error", status_code=500)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def db_session():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000
    ).hex()
    return f"{salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
    except ValueError:
        return False
    check = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000
    ).hex()
    return secrets.compare_digest(check, digest)


def public_user(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    return {
        "id": data["id"],
        "username": data["username"],
        "role": data["role"],
        "role_label": ROLE_LABELS.get(data["role"], data["role"]),
        "created_at": data.get("created_at"),
    }


def init_db() -> None:
    with db_session() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS elements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                display_name TEXT NOT NULL,
                technical_key TEXT NOT NULL UNIQUE,
                group_name TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS template_elements (
                template_id INTEGER NOT NULL,
                element_id INTEGER NOT NULL,
                PRIMARY KEY (template_id, element_id),
                FOREIGN KEY (template_id) REFERENCES templates(id) ON DELETE CASCADE,
                FOREIGN KEY (element_id) REFERENCES elements(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS scenarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS scenario_elements (
                scenario_id INTEGER NOT NULL,
                element_id INTEGER NOT NULL,
                is_visible INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (scenario_id, element_id),
                FOREIGN KEY (scenario_id) REFERENCES scenarios(id) ON DELETE CASCADE,
                FOREIGN KEY (element_id) REFERENCES elements(id) ON DELETE CASCADE
            );
            """
        )
        existing = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        if existing == 0:
            conn.execute(
                """
                INSERT INTO users (username, password_hash, role)
                VALUES (?, ?, ?)
                """,
                ("user", hash_password("Bojan1254"), ROLE_ADMIN),
            )


def fetch_user(conn: sqlite3.Connection, user_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def fetch_user_by_username(
    conn: sqlite3.Connection, username: str
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
        (username,),
    ).fetchone()
    return dict(row) if row else None


def get_current_user(request: Request) -> dict[str, Any]:
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    with db_session() as conn:
        user = fetch_user(conn, int(user_id))
    if not user:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def require_roles(*roles: str):
    allowed = set(roles)

    def dependency(
        user: dict[str, Any] = Depends(get_current_user),
    ) -> dict[str, Any]:
        if user["role"] not in allowed:
            raise HTTPException(status_code=403, detail="Permission denied")
        return user

    return dependency


@app.on_event("startup")
def on_startup() -> None:
    init_db()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

def normalize_technical_key(value: str) -> str:
    """First letter capital; words separated by spaces. e.g. Email field."""
    cleaned = re.sub(r"['\"]", "", value.strip())
    cleaned = cleaned.replace("_", " ")
    cleaned = re.sub(r"[^A-Za-z0-9 ]+", " ", cleaned)
    cleaned = re.sub(r" +", " ", cleaned).strip().lower()
    if not cleaned:
        raise ValueError("technical_key is required")
    return cleaned[0].upper() + cleaned[1:]


class ElementCreate(BaseModel):
    display_name: str = Field(..., min_length=1)
    technical_key: str = Field(..., min_length=1)
    group_name: str = ""

    @field_validator("display_name", "technical_key", "group_name", mode="before")
    @classmethod
    def strip_strings(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("display_name")
    @classmethod
    def capitalize_first_word(cls, value: str) -> str:
        if not value:
            return value
        # First word must start with a capital letter
        return value[0].upper() + value[1:]

    @field_validator("technical_key")
    @classmethod
    def validate_technical_key(cls, value: str) -> str:
        try:
            normalized = normalize_technical_key(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        if not TECHNICAL_KEY_RE.match(normalized):
            raise ValueError(
                "technical_key must start with a capital letter and use "
                "spaces between words (e.g. Email field)"
            )
        return normalized


class ElementUpdate(BaseModel):
    display_name: str = Field(..., min_length=1)
    technical_key: str = Field(..., min_length=1)
    group_name: str = ""

    @field_validator("display_name", "technical_key", "group_name", mode="before")
    @classmethod
    def strip_strings(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("display_name")
    @classmethod
    def capitalize_first_word(cls, value: str) -> str:
        if not value:
            return value
        return value[0].upper() + value[1:]

    @field_validator("technical_key")
    @classmethod
    def validate_technical_key(cls, value: str) -> str:
        try:
            normalized = normalize_technical_key(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        if not TECHNICAL_KEY_RE.match(normalized):
            raise ValueError(
                "technical_key must start with a capital letter and use "
                "spaces between words (e.g. Email field)"
            )
        return normalized


class GroupRename(BaseModel):
    old_name: str
    new_name: str

    @field_validator("old_name", "new_name", mode="before")
    @classmethod
    def strip_strings(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value


class TemplateCreate(BaseModel):
    name: str = Field(..., min_length=1)
    description: str = ""
    element_ids: list[int] = Field(default_factory=list)

    @field_validator("name", "description", mode="before")
    @classmethod
    def strip_strings(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value


class TemplateUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    element_ids: Optional[list[int]] = None

    @field_validator("name", "description", mode="before")
    @classmethod
    def strip_strings(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value


class ScenarioCreate(BaseModel):
    name: str = ""
    description: str = ""
    template_id: Optional[int] = None

    @field_validator("name", "description", mode="before")
    @classmethod
    def strip_strings(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value


class ScenarioUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None

    @field_validator("name", "description", mode="before")
    @classmethod
    def strip_strings(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value


class ScenarioElementAdd(BaseModel):
    element_id: int
    is_visible: bool = True


class ScenarioElementVisibility(BaseModel):
    is_visible: bool


class ApplyTemplateRequest(BaseModel):
    template_id: int


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)

    @field_validator("username", "password", mode="before")
    @classmethod
    def strip_strings(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value


class UserCreate(BaseModel):
    username: str = Field(..., min_length=2, max_length=50)
    password: str = Field(..., min_length=6, max_length=128)
    role: str

    @field_validator("username", "password", mode="before")
    @classmethod
    def strip_strings(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("role")
    @classmethod
    def validate_role(cls, value: str) -> str:
        value = value.strip()
        if value not in VALID_ROLES:
            raise ValueError(
                "role must be admin, automation, or manual_tester"
            )
        return value


class UserRoleUpdate(BaseModel):
    role: str

    @field_validator("role")
    @classmethod
    def validate_role(cls, value: str) -> str:
        value = value.strip()
        if value not in VALID_ROLES:
            raise ValueError(
                "role must be admin, automation, or manual_tester"
            )
        return value


class UserPasswordUpdate(BaseModel):
    password: str = Field(..., min_length=6, max_length=128)

    @field_validator("password", mode="before")
    @classmethod
    def strip_password(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


def fetch_element(conn: sqlite3.Connection, element_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM elements WHERE id = ?", (element_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Element not found")
    return dict(row)


def fetch_scenario(conn: sqlite3.Connection, scenario_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM scenarios WHERE id = ?", (scenario_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Scenario not found")
    return dict(row)


def get_scenario_elements(
    conn: sqlite3.Connection, scenario_id: int
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT e.id AS element_id, e.display_name, e.technical_key, e.group_name,
               se.is_visible
        FROM scenario_elements se
        JOIN elements e ON e.id = se.element_id
        WHERE se.scenario_id = ?
        ORDER BY e.group_name, e.display_name
        """,
        (scenario_id,),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["is_visible"] = bool(item["is_visible"])
        result.append(item)
    return result


def get_template_with_elements(
    conn: sqlite3.Connection, template_id: int
) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM templates WHERE id = ?", (template_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Template not found")
    elements = conn.execute(
        """
        SELECT e.id, e.display_name, e.technical_key, e.group_name
        FROM template_elements te
        JOIN elements e ON e.id = te.element_id
        WHERE te.template_id = ?
        ORDER BY e.group_name, e.display_name
        """,
        (template_id,),
    ).fetchall()
    data = dict(row)
    data["elements"] = [dict(e) for e in elements]
    data["element_ids"] = [e["id"] for e in elements]
    return data


def build_visibility_map(
    conn: sqlite3.Connection, scenario_id: int
) -> dict[str, bool]:
    rows = conn.execute(
        """
        SELECT e.technical_key, se.is_visible
        FROM scenario_elements se
        JOIN elements e ON e.id = se.element_id
        WHERE se.scenario_id = ?
        ORDER BY e.technical_key
        """,
        (scenario_id,),
    ).fetchall()
    return {row["technical_key"]: bool(row["is_visible"]) for row in rows}


def format_python_dict(mapping: dict[str, bool]) -> str:
    if not mapping:
        return "expected_visibility = {}"
    lines = ["expected_visibility = {"]
    for key, value in mapping.items():
        lines.append(f'    "{key}": {value},')
    lines.append("}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pages + auth
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse(url="/login", status_code=303)
    with db_session() as conn:
        user = fetch_user(conn, int(user_id))
    if not user:
        request.session.clear()
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "user": public_user(user),
            "user_json": json.dumps(public_user(user)),
        },
    )


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request})


@app.get("/api/me")
def me(user: dict[str, Any] = Depends(get_current_user)):
    return public_user(user)


@app.post("/api/login")
def login(payload: LoginRequest, request: Request):
    username = payload.username.strip()
    password = payload.password
    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password are required")
    with db_session() as conn:
        user = fetch_user_by_username(conn, username)
        if not user or not verify_password(password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="Invalid username or password")
        request.session["user_id"] = user["id"]
        return public_user(user)


@app.post("/api/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Elements API
# ---------------------------------------------------------------------------

@app.get("/api/elements")
def list_elements(
    search: str = Query("", alias="search"),
    user: dict[str, Any] = Depends(get_current_user),
):
    search = search.strip()
    with db_session() as conn:
        if search:
            like = f"%{search}%"
            rows = conn.execute(
                """
                SELECT * FROM elements
                WHERE display_name LIKE ? COLLATE NOCASE
                   OR technical_key LIKE ? COLLATE NOCASE
                   OR group_name LIKE ? COLLATE NOCASE
                ORDER BY group_name, display_name
                """,
                (like, like, like),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM elements ORDER BY group_name, display_name"
            ).fetchall()
        return [dict(r) for r in rows]


@app.post("/api/elements", status_code=201)
def create_element(
    payload: ElementCreate,
    user: dict[str, Any] = Depends(require_roles(ROLE_ADMIN, ROLE_AUTOMATION)),
):
    if not payload.display_name:
        raise HTTPException(status_code=400, detail="display_name is required")
    try:
        with db_session() as conn:
            cursor = conn.execute(
                """
                INSERT INTO elements (display_name, technical_key, group_name)
                VALUES (?, ?, ?)
                """,
                (payload.display_name, payload.technical_key, payload.group_name),
            )
            return fetch_element(conn, cursor.lastrowid)
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=409,
            detail=f"technical_key '{payload.technical_key}' already exists",
        )


@app.put("/api/elements/groups/rename")
def rename_element_group(
    payload: GroupRename,
    user: dict[str, Any] = Depends(require_roles(ROLE_ADMIN, ROLE_AUTOMATION)),
):
    old_name = payload.old_name
    new_name = payload.new_name
    # UI uses "Ungrouped" for empty group_name
    old_db = "" if old_name.lower() == "ungrouped" else old_name
    new_db = "" if new_name.lower() == "ungrouped" else new_name
    if old_db == new_db:
        return {"ok": True, "updated": 0}
    with db_session() as conn:
        if old_db == "":
            cursor = conn.execute(
                """
                UPDATE elements
                SET group_name = ?
                WHERE TRIM(group_name) = '' OR group_name IS NULL
                """,
                (new_db,),
            )
        else:
            cursor = conn.execute(
                """
                UPDATE elements
                SET group_name = ?
                WHERE group_name = ? COLLATE NOCASE
                """,
                (new_db, old_db),
            )
        updated = cursor.rowcount
        if updated == 0:
            raise HTTPException(status_code=404, detail="Group not found")
        return {"ok": True, "updated": updated, "new_name": new_db or "Ungrouped"}


@app.put("/api/elements/{element_id}")
def update_element(
    element_id: int,
    payload: ElementUpdate,
    user: dict[str, Any] = Depends(require_roles(ROLE_ADMIN, ROLE_AUTOMATION)),
):
    if not payload.display_name:
        raise HTTPException(status_code=400, detail="display_name is required")
    try:
        with db_session() as conn:
            fetch_element(conn, element_id)
            conn.execute(
                """
                UPDATE elements
                SET display_name = ?, technical_key = ?, group_name = ?
                WHERE id = ?
                """,
                (
                    payload.display_name,
                    payload.technical_key,
                    payload.group_name,
                    element_id,
                ),
            )
            return fetch_element(conn, element_id)
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=409,
            detail=f"technical_key '{payload.technical_key}' already exists",
        )


@app.delete("/api/elements/{element_id}")
def delete_element(
    element_id: int,
    user: dict[str, Any] = Depends(require_roles(ROLE_ADMIN, ROLE_AUTOMATION)),
):
    with db_session() as conn:
        fetch_element(conn, element_id)
        conn.execute("DELETE FROM elements WHERE id = ?", (element_id,))
    return {"ok": True}


# ---------------------------------------------------------------------------
# Templates API
# ---------------------------------------------------------------------------

@app.get("/api/templates")
def list_templates(user: dict[str, Any] = Depends(get_current_user)):
    with db_session() as conn:
        rows = conn.execute(
            "SELECT * FROM templates ORDER BY name"
        ).fetchall()
        return [get_template_with_elements(conn, row["id"]) for row in rows]


@app.post("/api/templates", status_code=201)
def create_template(
    payload: TemplateCreate,
    user: dict[str, Any] = Depends(get_current_user),
):
    if not payload.name:
        raise HTTPException(status_code=400, detail="name is required")
    if not payload.element_ids:
        raise HTTPException(
            status_code=400, detail="Select at least one element for the template"
        )
    try:
        with db_session() as conn:
            # Validate all element IDs exist
            for eid in payload.element_ids:
                fetch_element(conn, eid)
            cursor = conn.execute(
                """
                INSERT INTO templates (name, description)
                VALUES (?, ?)
                """,
                (payload.name, payload.description),
            )
            template_id = cursor.lastrowid
            for eid in sorted(set(payload.element_ids)):
                conn.execute(
                    """
                    INSERT INTO template_elements (template_id, element_id)
                    VALUES (?, ?)
                    """,
                    (template_id, eid),
                )
            return get_template_with_elements(conn, template_id)
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=409,
            detail=f"Template name '{payload.name}' already exists",
        )


@app.put("/api/templates/{template_id}")
def update_template(
    template_id: int,
    payload: TemplateUpdate,
    user: dict[str, Any] = Depends(get_current_user),
):
    with db_session() as conn:
        current = get_template_with_elements(conn, template_id)
        name = payload.name if payload.name is not None else current["name"]
        description = (
            payload.description
            if payload.description is not None
            else current["description"]
        )
        if not name:
            raise HTTPException(status_code=400, detail="name is required")

        element_ids = (
            payload.element_ids
            if payload.element_ids is not None
            else current["element_ids"]
        )
        if not element_ids:
            raise HTTPException(
                status_code=400,
                detail="Select at least one element for the template",
            )

        for eid in element_ids:
            fetch_element(conn, eid)

        try:
            conn.execute(
                """
                UPDATE templates
                SET name = ?, description = ?
                WHERE id = ?
                """,
                (name, description, template_id),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(
                status_code=409,
                detail=f"Template name '{name}' already exists",
            )

        conn.execute(
            "DELETE FROM template_elements WHERE template_id = ?",
            (template_id,),
        )
        for eid in sorted(set(element_ids)):
            conn.execute(
                """
                INSERT INTO template_elements (template_id, element_id)
                VALUES (?, ?)
                """,
                (template_id, eid),
            )
        return get_template_with_elements(conn, template_id)


@app.delete("/api/templates/{template_id}")
def delete_template(
    template_id: int,
    user: dict[str, Any] = Depends(get_current_user),
):
    with db_session() as conn:
        get_template_with_elements(conn, template_id)
        conn.execute("DELETE FROM templates WHERE id = ?", (template_id,))
    return {"ok": True}

# ---------------------------------------------------------------------------
# Scenarios API
# ---------------------------------------------------------------------------

def next_untitled_scenario_name(conn: sqlite3.Connection) -> str:
    rows = conn.execute(
        "SELECT name FROM scenarios WHERE name LIKE 'Untitled scenario%'"
    ).fetchall()
    used = {row["name"] for row in rows}
    if "Untitled scenario" not in used:
        return "Untitled scenario"
    n = 2
    while f"Untitled scenario {n}" in used:
        n += 1
    return f"Untitled scenario {n}"


@app.get("/api/scenarios")
def list_scenarios(user: dict[str, Any] = Depends(get_current_user)):
    with db_session() as conn:
        rows = conn.execute(
            "SELECT * FROM scenarios ORDER BY name"
        ).fetchall()
        return [dict(r) for r in rows]


@app.post("/api/scenarios", status_code=201)
def create_scenario(
    payload: ScenarioCreate,
    user: dict[str, Any] = Depends(get_current_user),
):
    try:
        with db_session() as conn:
            name = payload.name or next_untitled_scenario_name(conn)
            if payload.template_id is not None:
                get_template_with_elements(conn, payload.template_id)
            cursor = conn.execute(
                """
                INSERT INTO scenarios (name, description)
                VALUES (?, ?)
                """,
                (name, payload.description),
            )
            scenario_id = cursor.lastrowid

            if payload.template_id is not None:
                template = get_template_with_elements(conn, payload.template_id)
                for element in template["elements"]:
                    conn.execute(
                        """
                        INSERT INTO scenario_elements
                            (scenario_id, element_id, is_visible)
                        VALUES (?, ?, 1)
                        """,
                        (scenario_id, element["id"]),
                    )

            scenario = fetch_scenario(conn, scenario_id)
            scenario["elements"] = get_scenario_elements(conn, scenario_id)
            return scenario
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=409,
            detail=f"Scenario name '{payload.name}' already exists",
        )


@app.get("/api/scenarios/{scenario_id}")
def get_scenario(
    scenario_id: int,
    user: dict[str, Any] = Depends(get_current_user),
):
    with db_session() as conn:
        scenario = fetch_scenario(conn, scenario_id)
        scenario["elements"] = get_scenario_elements(conn, scenario_id)
        return scenario


@app.put("/api/scenarios/{scenario_id}")
def update_scenario(
    scenario_id: int,
    payload: ScenarioUpdate,
    user: dict[str, Any] = Depends(get_current_user),
):
    with db_session() as conn:
        scenario = fetch_scenario(conn, scenario_id)
        name = payload.name if payload.name is not None else scenario["name"]
        description = (
            payload.description
            if payload.description is not None
            else scenario["description"]
        )
        if not name:
            raise HTTPException(status_code=400, detail="name is required")
        try:
            conn.execute(
                """
                UPDATE scenarios
                SET name = ?, description = ?, updated_at = datetime('now')
                WHERE id = ?
                """,
                (name, description, scenario_id),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(
                status_code=409,
                detail=f"Scenario name '{name}' already exists",
            )
        updated = fetch_scenario(conn, scenario_id)
        updated["elements"] = get_scenario_elements(conn, scenario_id)
        return updated


@app.delete("/api/scenarios/{scenario_id}")
def delete_scenario(
    scenario_id: int,
    user: dict[str, Any] = Depends(get_current_user),
):
    with db_session() as conn:
        fetch_scenario(conn, scenario_id)
        conn.execute("DELETE FROM scenarios WHERE id = ?", (scenario_id,))
    return {"ok": True}


@app.post("/api/scenarios/{scenario_id}/elements", status_code=201)
def add_scenario_element(
    scenario_id: int,
    payload: ScenarioElementAdd,
    user: dict[str, Any] = Depends(get_current_user),
):
    with db_session() as conn:
        fetch_scenario(conn, scenario_id)
        fetch_element(conn, payload.element_id)
        existing = conn.execute(
            """
            SELECT 1 FROM scenario_elements
            WHERE scenario_id = ? AND element_id = ?
            """,
            (scenario_id, payload.element_id),
        ).fetchone()
        if existing:
            raise HTTPException(
                status_code=409,
                detail="Element is already in this scenario",
            )
        conn.execute(
            """
            INSERT INTO scenario_elements (scenario_id, element_id, is_visible)
            VALUES (?, ?, ?)
            """,
            (scenario_id, payload.element_id, int(payload.is_visible)),
        )
        conn.execute(
            "UPDATE scenarios SET updated_at = datetime('now') WHERE id = ?",
            (scenario_id,),
        )
        scenario = fetch_scenario(conn, scenario_id)
        scenario["elements"] = get_scenario_elements(conn, scenario_id)
        return scenario


@app.put("/api/scenarios/{scenario_id}/elements/{element_id}")
def update_scenario_element_visibility(
    scenario_id: int,
    element_id: int,
    payload: ScenarioElementVisibility,
    user: dict[str, Any] = Depends(get_current_user),
):
    with db_session() as conn:
        fetch_scenario(conn, scenario_id)
        row = conn.execute(
            """
            SELECT 1 FROM scenario_elements
            WHERE scenario_id = ? AND element_id = ?
            """,
            (scenario_id, element_id),
        ).fetchone()
        if not row:
            raise HTTPException(
                status_code=404, detail="Element not found in scenario"
            )
        conn.execute(
            """
            UPDATE scenario_elements
            SET is_visible = ?
            WHERE scenario_id = ? AND element_id = ?
            """,
            (int(payload.is_visible), scenario_id, element_id),
        )
        conn.execute(
            "UPDATE scenarios SET updated_at = datetime('now') WHERE id = ?",
            (scenario_id,),
        )
        scenario = fetch_scenario(conn, scenario_id)
        scenario["elements"] = get_scenario_elements(conn, scenario_id)
        return scenario


@app.delete("/api/scenarios/{scenario_id}/elements/{element_id}")
def remove_scenario_element(
    scenario_id: int,
    element_id: int,
    user: dict[str, Any] = Depends(get_current_user),
):
    with db_session() as conn:
        fetch_scenario(conn, scenario_id)
        result = conn.execute(
            """
            DELETE FROM scenario_elements
            WHERE scenario_id = ? AND element_id = ?
            """,
            (scenario_id, element_id),
        )
        if result.rowcount == 0:
            raise HTTPException(
                status_code=404, detail="Element not found in scenario"
            )
        conn.execute(
            "UPDATE scenarios SET updated_at = datetime('now') WHERE id = ?",
            (scenario_id,),
        )
        scenario = fetch_scenario(conn, scenario_id)
        scenario["elements"] = get_scenario_elements(conn, scenario_id)
        return scenario


@app.post("/api/scenarios/{scenario_id}/apply-template")
def apply_template_to_scenario(
    scenario_id: int,
    payload: ApplyTemplateRequest,
    user: dict[str, Any] = Depends(get_current_user),
):
    with db_session() as conn:
        fetch_scenario(conn, scenario_id)
        template = get_template_with_elements(conn, payload.template_id)
        added = 0
        skipped = 0
        for element in template["elements"]:
            existing = conn.execute(
                """
                SELECT 1 FROM scenario_elements
                WHERE scenario_id = ? AND element_id = ?
                """,
                (scenario_id, element["id"]),
            ).fetchone()
            if existing:
                skipped += 1
                continue
            conn.execute(
                """
                INSERT INTO scenario_elements (scenario_id, element_id, is_visible)
                VALUES (?, ?, 1)
                """,
                (scenario_id, element["id"]),
            )
            added += 1
        conn.execute(
            "UPDATE scenarios SET updated_at = datetime('now') WHERE id = ?",
            (scenario_id,),
        )
        scenario = fetch_scenario(conn, scenario_id)
        scenario["elements"] = get_scenario_elements(conn, scenario_id)
        scenario["apply_summary"] = {"added": added, "skipped": skipped}
        return scenario


@app.get("/api/scenarios/{scenario_id}/export")
def export_scenario(
    scenario_id: int,
    user: dict[str, Any] = Depends(get_current_user),
):
    with db_session() as conn:
        fetch_scenario(conn, scenario_id)
        mapping = build_visibility_map(conn, scenario_id)
        return {
            "mapping": mapping,
            "python": format_python_dict(mapping),
            "json": json.dumps(mapping, indent=2),
        }


# ---------------------------------------------------------------------------
# Users API (admin only)
# ---------------------------------------------------------------------------

@app.get("/api/users")
def list_users(user: dict[str, Any] = Depends(require_roles(ROLE_ADMIN))):
    with db_session() as conn:
        rows = conn.execute(
            "SELECT * FROM users ORDER BY username"
        ).fetchall()
        return [public_user(r) for r in rows]


@app.post("/api/users", status_code=201)
def create_user(
    payload: UserCreate,
    user: dict[str, Any] = Depends(require_roles(ROLE_ADMIN)),
):
    username = payload.username.strip()
    if not re.match(r"^[a-zA-Z0-9._-]{2,50}$", username):
        raise HTTPException(
            status_code=400,
            detail="Username may use letters, numbers, dot, underscore, hyphen",
        )
    try:
        with db_session() as conn:
            cursor = conn.execute(
                """
                INSERT INTO users (username, password_hash, role)
                VALUES (?, ?, ?)
                """,
                (username, hash_password(payload.password), payload.role),
            )
            created = fetch_user(conn, cursor.lastrowid)
            return public_user(created)
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=409,
            detail=f"Username '{username}' already exists",
        )


@app.put("/api/users/{user_id}/role")
def update_user_role(
    user_id: int,
    payload: UserRoleUpdate,
    user: dict[str, Any] = Depends(require_roles(ROLE_ADMIN)),
):
    with db_session() as conn:
        target = fetch_user(conn, user_id)
        if not target:
            raise HTTPException(status_code=404, detail="User not found")
        if (
            target["role"] == ROLE_ADMIN
            and payload.role != ROLE_ADMIN
        ):
            admin_count = conn.execute(
                "SELECT COUNT(*) AS c FROM users WHERE role = ?",
                (ROLE_ADMIN,),
            ).fetchone()["c"]
            if admin_count <= 1:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot remove the last admin",
                )
        conn.execute(
            "UPDATE users SET role = ? WHERE id = ?",
            (payload.role, user_id),
        )
        updated = fetch_user(conn, user_id)
        return public_user(updated)


@app.put("/api/users/{user_id}/password")
def update_user_password(
    user_id: int,
    payload: UserPasswordUpdate,
    user: dict[str, Any] = Depends(require_roles(ROLE_ADMIN)),
):
    with db_session() as conn:
        target = fetch_user(conn, user_id)
        if not target:
            raise HTTPException(status_code=404, detail="User not found")
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_password(payload.password), user_id),
        )
    return {"ok": True}


@app.delete("/api/users/{user_id}")
def delete_user(
    user_id: int,
    user: dict[str, Any] = Depends(require_roles(ROLE_ADMIN)),
):
    if user["id"] == user_id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    with db_session() as conn:
        target = fetch_user(conn, user_id)
        if not target:
            raise HTTPException(status_code=404, detail="User not found")
        if target["role"] == ROLE_ADMIN:
            admin_count = conn.execute(
                "SELECT COUNT(*) AS c FROM users WHERE role = ?",
                (ROLE_ADMIN,),
            ).fetchone()["c"]
            if admin_count <= 1:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot delete the last admin",
                )
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    return {"ok": True}
