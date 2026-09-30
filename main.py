import os
import re
import uuid
import hashlib
import secrets
from datetime import datetime, timezone
from typing import Optional

import httpx
from bson import ObjectId
from fastapi import (
    FastAPI,
    Request,
    UploadFile,
    File,
    Form,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pymongo import AsyncMongoClient, ASCENDING
from pymongo.errors import DuplicateKeyError


# =========================================================
# CONFIG
# =========================================================

MONGODB_URI = os.getenv("MONGODB_URI", "")
MONGODB_DATABASE = os.getenv(
    "MONGODB_DATABASE",
    "piniyskaya_federation"
)

SESSION_SECRET = os.getenv(
    "SESSION_SECRET",
    "CHANGE_THIS_SECRET"
)

MODERATOR_KEY = os.getenv(
    "MODERATOR_KEY",
    ""
)

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
CLOUDFLARE_ACCOUNT_ID = os.getenv(
    "CLOUDFLARE_ACCOUNT_ID",
    ""
)
CLOUDFLARE_API_KEY = os.getenv(
    "CLOUDFLARE_API_KEY",
    ""
)
OPENWEATHER_API_KEY = os.getenv(
    "OPENWEATHER_API_KEY",
    ""
)

GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "openai/gpt-oss-120b"
)

CLOUDFLARE_IMAGE_MODEL = os.getenv(
    "CLOUDFLARE_IMAGE_MODEL",
    "@cf/black-forest-labs/flux-1-schnell"
)


# =========================================================
# APP
# =========================================================

app = FastAPI(
    title="Piniyskaya Federation Elections",
    version="1.0.0"
)

mongo_client = None
db = None

users = None
sessions = None
candidates = None
votes = None
comments = None
chat_messages = None
ai_messages = None
election = None
files_collection = None


# =========================================================
# CONNECTION
# =========================================================

@app.on_event("startup")
async def startup():

    global mongo_client
    global db
    global users
    global sessions
    global candidates
    global votes
    global comments
    global chat_messages
    global ai_messages
    global election
    global files_collection

    if not MONGODB_URI:
        print("[MONGO] MONGODB_URI is not configured.")

        return

    print("[MONGO] Connecting...")

    mongo_client = AsyncMongoClient(
        MONGODB_URI,
        serverSelectionTimeoutMS=10000
    )

    await mongo_client.admin.command("ping")

    db = mongo_client[MONGODB_DATABASE]

    users = db["users"]
    sessions = db["sessions"]
    candidates = db["candidates"]
    votes = db["votes"]
    comments = db["comments"]
    chat_messages = db["chat_messages"]
    ai_messages = db["ai_messages"]
    election = db["election"]
    files_collection = db["files"]

    await create_indexes()
    await initialize_database()

    print(
        f"[MONGO] Connected to database: "
        f"{MONGODB_DATABASE}"
    )


@app.on_event("shutdown")
async def shutdown():

    global mongo_client

    if mongo_client:
        await mongo_client.close()


async def create_indexes():

    await users.create_index(
        [("username_lower", ASCENDING)],
        unique=True
    )

    await sessions.create_index(
        [("token", ASCENDING)],
        unique=True
    )

    await votes.create_index(
        [("user_id", ASCENDING)],
        unique=True
    )

    await comments.create_index(
        [("candidate_id", ASCENDING), ("created_at", ASCENDING)]
    )

    await chat_messages.create_index(
        [("created_at", ASCENDING)]
    )

    await ai_messages.create_index(
        [("user_id", ASCENDING), ("created_at", ASCENDING)]
    )


async def initialize_database():

    for slot in range(1, 6):

        existing = await candidates.find_one(
            {"slot": slot}
        )

        if not existing:

            await candidates.insert_one({
                "slot": slot,
                "name": "",
                "description": "",
                "image_id": None,
                "created_at": now()
            })

    election_document = await election.find_one(
        {"_id": "main"}
    )

    if not election_document:

        await election.insert_one({
            "_id": "main",
            "status": "active",
            "ended_at": None,
            "winner_candidate_id": None
        })


# =========================================================
# HELPERS
# =========================================================

def now():
    return datetime.now(timezone.utc)


def require_db():

    if db is None:
        raise HTTPException(
            status_code=503,
            detail="MongoDB is not configured."
        )


def hash_password(password: str) -> str:

    salt = secrets.token_bytes(16)

    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode(),
        salt,
        120000
    )

    return (
        salt.hex()
        + ":"
        + derived.hex()
    )


def verify_password(
    password: str,
    stored: str
) -> bool:

    try:

        salt_hex, hash_hex = stored.split(":")

        salt = bytes.fromhex(salt_hex)

        derived = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode(),
            salt,
            120000
        )

        return secrets.compare_digest(
            derived.hex(),
            hash_hex
        )

    except Exception:
        return False


async def get_current_user(request: Request):

    require_db()

    token = request.cookies.get(
        "piniy_session"
    )

    if not token:
        return None

    session = await sessions.find_one(
        {"token": token}
    )

    if not session:
        return None

    user = await users.find_one(
        {"_id": session["user_id"]}
    )

    return user


async def require_user(request: Request):

    user = await get_current_user(request)

    if not user:

        raise HTTPException(
            status_code=401,
            detail="Authentication required."
        )

    return user


def serialize_user(user):

    if not user:
        return None

    return {
        "id": str(user["_id"]),
        "username": user["username"],
        "created_at": user["created_at"].isoformat()
    }


def serialize_candidate(candidate):

    image_url = None

    if candidate.get("image_id"):

        image_url = (
            "/api/file/"
            + str(candidate["image_id"])
        )

    return {
        "id": str(candidate["_id"]),
        "slot": candidate["slot"],
        "name": candidate.get("name", ""),
        "description": candidate.get(
            "description",
            ""
        ),
        "image_url": image_url
    }


def is_valid_url(text):

    return bool(
        re.match(
            r"^https?://",
            text,
            re.IGNORECASE
        )
    )

# =========================================================
# WEATHER GEOCODING
# =========================================================

async def geocode_weather_location(
    location: str
):
    """
    Преобразует название места в координаты.

    Работает с:
    - городами
    - посёлками
    - деревнями
    - сёлами
    - хуторами
    - районами
    - другими населёнными пунктами

    Понимает русские падежи через геокодер.
    """

    location = location.strip()

    if not location:
        return None

    url = "https://nominatim.openstreetmap.org/search"

    params = {
        "q": location,
        "format": "jsonv2",
        "limit": 5,
        "addressdetails": 1,
        "accept-language": "ru"
    }

    headers = {
        "User-Agent": (
            "PiniyskayaFederationWeather/1.0 "
            "(weather search)"
        )
    }

    try:

        async with httpx.AsyncClient(
            timeout=15,
            headers=headers
        ) as client:

            response = await client.get(
                url,
                params=params
            )

        if response.status_code != 200:
            print(
                "[GEOCODING ERROR]",
                response.status_code,
                response.text[:500]
            )
            return None

        results = response.json()

        if not results:
            return None

        # Сначала ищем населённые пункты
        preferred_types = {
            "city",
            "town",
            "village",
            "hamlet",
            "municipality",
            "locality"
        }

        for item in results:

            if item.get("type") in preferred_types:

                return {
                    "lat": float(item["lat"]),
                    "lon": float(item["lon"]),
                    "name": (
                        item.get("name")
                        or item.get("display_name")
                    ),
                    "display_name": item.get(
                        "display_name",
                        ""
                    ),
                    "type": item.get(
                        "type",
                        ""
                    ),
                    "address": item.get(
                        "address",
                        {}
                    )
                }

        # Если специальный тип не найден,
        # используем первый результат
        item = results[0]

        return {
            "lat": float(item["lat"]),
            "lon": float(item["lon"]),
            "name": (
                item.get("name")
                or item.get("display_name")
            ),
            "display_name": item.get(
                "display_name",
                ""
            ),
            "type": item.get(
                "type",
                ""
            ),
            "address": item.get(
                "address",
                {}
            )
        }

    except Exception as e:

        print(
            "[GEOCODING EXCEPTION]",
            repr(e)
        )

        return None

# =========================================================
# FRONTEND
# =========================================================

@app.get("/", response_class=HTMLResponse)
async def index():

    with open(
        "index.html",
        "r",
        encoding="utf-8"
    ) as f:

        return HTMLResponse(
            f.read()
        )


@app.get("/style.css")
async def style():

    with open(
        "style.css",
        "r",
        encoding="utf-8"
    ) as f:

        return Response(
            f.read(),
            media_type="text/css"
        )


@app.get("/app.js")
async def javascript():

    with open(
        "app.js",
        "r",
        encoding="utf-8"
    ) as f:

        return Response(
            f.read(),
            media_type="application/javascript"
        )


# =========================================================
# AUTH
# =========================================================

@app.post("/api/register")
async def register(
    username: str = Form(...),
    password: str = Form(...)
):

    require_db()

    username = username.strip()

    if len(username) < 2:
        raise HTTPException(
            status_code=400,
            detail="Имя должно содержать минимум 2 символа."
        )

    if len(username) > 40:
        raise HTTPException(
            status_code=400,
            detail="Имя слишком длинное."
        )

    if len(password) < 6:
        raise HTTPException(
            status_code=400,
            detail="Пароль должен содержать минимум 6 символов."
        )

    user_document = {
        "username": username,
        "username_lower": username.lower(),
        "password_hash": hash_password(password),
        "created_at": now()
    }

    try:

        result = await users.insert_one(
            user_document
        )

    except DuplicateKeyError:

        raise HTTPException(
            status_code=409,
            detail="Такое имя уже зарегистрировано."
        )

    token = secrets.token_urlsafe(48)

    await sessions.insert_one({
        "token": token,
        "user_id": result.inserted_id,
        "created_at": now()
    })

    response = JSONResponse({
        "ok": True,
        "user": serialize_user(
            user_document | {
                "_id": result.inserted_id
            }
        )
    })

    response.set_cookie(
        "piniy_session",
        token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 30
    )

    return response


@app.post("/api/login")
async def login(
    username: str = Form(...),
    password: str = Form(...)
):

    require_db()

    user = await users.find_one({
        "username_lower": username.strip().lower()
    })

    if not user:

        raise HTTPException(
            status_code=401,
            detail="Неверное имя или пароль."
        )

    if not verify_password(
        password,
        user["password_hash"]
    ):

        raise HTTPException(
            status_code=401,
            detail="Неверное имя или пароль."
        )

    token = secrets.token_urlsafe(48)

    await sessions.insert_one({
        "token": token,
        "user_id": user["_id"],
        "created_at": now()
    })

    response = JSONResponse({
        "ok": True,
        "user": serialize_user(user)
    })

    response.set_cookie(
        "piniy_session",
        token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 30
    )

    return response


@app.post("/api/logout")
async def logout(request: Request):

    require_db()

    token = request.cookies.get(
        "piniy_session"
    )

    if token:

        await sessions.delete_one({
            "token": token
        })

    response = JSONResponse({
        "ok": True
    })

    response.delete_cookie(
        "piniy_session"
    )

    return response


@app.get("/api/me")
async def me(request: Request):

    user = await get_current_user(request)

    return {
        "user": serialize_user(user)
    }


# =========================================================
# CANDIDATES
# =========================================================

@app.get("/api/candidates")
async def get_candidates():

    require_db()

    result = []

    async for candidate in candidates.find(
        {}
    ).sort("slot", ASCENDING):

        result.append(
            serialize_candidate(candidate)
        )

    return {
        "candidates": result
    }


@app.post("/api/candidates/{slot}")
async def update_candidate(
    request: Request,
    slot: int,
    name: str = Form(""),
    description: str = Form(""),
    image: Optional[UploadFile] = File(None)
):

    require_db()

    moderator_key = request.headers.get(
        "X-Moderator-Key",
        ""
    )

    if not MODERATOR_KEY or (
        moderator_key != MODERATOR_KEY
    ):

        raise HTTPException(
            status_code=403,
            detail="Moderator access required."
        )

    if slot < 1 or slot > 5:

        raise HTTPException(
            status_code=400,
            detail="Candidate slot must be 1-5."
        )

    update = {
        "name": name.strip(),
        "description": description.strip()
    }

    if image:

        if not image.content_type or not image.content_type.startswith(
            "image/"
        ):

            raise HTTPException(
                status_code=400,
                detail="Only images are allowed."
            )

        data = await image.read()

        if len(data) > 10 * 1024 * 1024:

            raise HTTPException(
                status_code=400,
                detail="Image is too large."
            )

        file_id = uuid.uuid4().hex

        await files_collection.insert_one({
            "_id": file_id,
            "content_type": image.content_type,
            "filename": image.filename,
            "data": data,
            "created_at": now()
        })

        update["image_id"] = file_id

    await candidates.update_one(
        {"slot": slot},
        {"$set": update},
        upsert=True
    )

    candidate = await candidates.find_one(
        {"slot": slot}
    )

    return {
        "ok": True,
        "candidate": serialize_candidate(
            candidate
        )
    }


@app.get("/api/file/{file_id}")
async def get_file(file_id: str):

    require_db()

    file = await files_collection.find_one({
        "_id": file_id
    })

    if not file:

        raise HTTPException(
            status_code=404,
            detail="File not found."
        )

    return Response(
        content=file["data"],
        media_type=file["content_type"]
    )


# =========================================================
# VOTING
# =========================================================

@app.post("/api/vote/{candidate_id}")
async def vote(
    request: Request,
    candidate_id: str
):

    require_db()

    user = await require_user(request)

    election_state = await election.find_one(
        {"_id": "main"}
    )

    if election_state.get("status") != "active":

        raise HTTPException(
            status_code=400,
            detail="Голосование уже завершено."
        )

    try:

        candidate_object_id = ObjectId(
            candidate_id
        )

    except Exception:

        raise HTTPException(
            status_code=400,
            detail="Invalid candidate."
        )

    candidate = await candidates.find_one({
        "_id": candidate_object_id
    })

    if not candidate:

        raise HTTPException(
            status_code=404,
            detail="Candidate not found."
        )

    try:

        await votes.insert_one({
            "user_id": user["_id"],
            "candidate_id": candidate["_id"],
            "created_at": now()
        })

    except DuplicateKeyError:

        raise HTTPException(
            status_code=409,
            detail="Вы уже голосовали."
        )

    return {
        "ok": True
    }


@app.get("/api/results")
async def results():

    require_db()

    candidate_list = []

    async for candidate in candidates.find(
        {}
    ).sort("slot", ASCENDING):

        count = await votes.count_documents({
            "candidate_id": candidate["_id"]
        })

        candidate_list.append({
            "id": str(candidate["_id"]),
            "slot": candidate["slot"],
            "name": candidate.get(
                "name",
                ""
            ),
            "votes": count
        })

    total = sum(
        item["votes"]
        for item in candidate_list
    )

    for item in candidate_list:

        if total:
            item["percentage"] = round(
                item["votes"] / total * 100,
                2
            )

        else:
            item["percentage"] = 0

    election_state = await election.find_one(
        {"_id": "main"}
    )

    return {
        "status": election_state.get(
            "status",
            "active"
        ),
        "total_votes": total,
        "candidates": candidate_list,
        "winner": election_state.get(
            "winner_candidate_id"
        )
    }


# =========================================================
# COMMENTS
# =========================================================

@app.get("/api/candidates/{candidate_id}/comments")
async def get_comments(
    candidate_id: str
):

    require_db()

    try:

        oid = ObjectId(candidate_id)

    except Exception:

        raise HTTPException(
            status_code=400,
            detail="Invalid candidate."
        )

    result = []

    async for comment in comments.find({
        "candidate_id": oid
    }).sort(
        "created_at",
        ASCENDING
    ):

        user = await users.find_one({
            "_id": comment["user_id"]
        })

        result.append({
            "id": str(comment["_id"]),
            "username": (
                user["username"]
                if user
                else "Unknown"
            ),
            "text": comment["text"],
            "created_at": comment[
                "created_at"
            ].isoformat()
        })

    return {
        "comments": result
    }


@app.post("/api/candidates/{candidate_id}/comments")
async def add_comment(
    request: Request,
    candidate_id: str
):

    require_db()

    user = await require_user(request)

    try:

        oid = ObjectId(candidate_id)

    except Exception:

        raise HTTPException(
            status_code=400,
            detail="Invalid candidate."
        )

    data = await request.json()

    text = str(
        data.get("text", "")
    ).strip()

    if not text:

        raise HTTPException(
            status_code=400,
            detail="Комментарий пуст."
        )

    if len(text) > 2000:

        raise HTTPException(
            status_code=400,
            detail="Комментарий слишком длинный."
        )

    await comments.insert_one({
        "candidate_id": oid,
        "user_id": user["_id"],
        "text": text,
        "created_at": now()
    })

    return {
        "ok": True
    }


# =========================================================
# CHAT
# =========================================================

class ConnectionManager:

    def __init__(self):

        self.connections = []

    async def connect(
        self,
        websocket: WebSocket
    ):

        await websocket.accept()

        self.connections.append(
            websocket
        )

    def disconnect(
        self,
        websocket: WebSocket
    ):

        if websocket in self.connections:

            self.connections.remove(
                websocket
            )

    async def broadcast(
        self,
        message
    ):

        dead = []

        for connection in self.connections:

            try:

                await connection.send_json(
                    message
                )

            except Exception:

                dead.append(connection)

        for connection in dead:

            self.disconnect(
                connection
            )


manager = ConnectionManager()


@app.get("/api/chat")
async def get_chat():

    require_db()

    messages = []

    cursor = chat_messages.find(
        {}
    ).sort(
        "created_at",
        -1
    ).limit(100)

    async for message in cursor:

        user = await users.find_one({
            "_id": message["user_id"]
        })

        messages.append({
            "id": str(message["_id"]),
            "username": (
                user["username"]
                if user
                else "Unknown"
            ),
            "text": message.get(
                "text",
                ""
            ),
            "image_url": message.get(
                "image_url"
            ),
            "created_at": message[
                "created_at"
            ].isoformat()
        })

    messages.reverse()

    return {
        "messages": messages
    }


@app.post("/api/chat")
async def send_chat(
    request: Request
):

    require_db()

    user = await require_user(request)

    data = await request.json()

    text = str(
        data.get("text", "")
    ).strip()

    image_url = data.get(
        "image_url"
    )

    if not text and not image_url:

        raise HTTPException(
            status_code=400,
            detail="Message is empty."
        )

    if len(text) > 4000:

        raise HTTPException(
            status_code=400,
            detail="Message is too long."
        )

    message = {
        "user_id": user["_id"],
        "text": text,
        "image_url": image_url,
        "created_at": now()
    }

    result = await chat_messages.insert_one(
        message
    )

    payload = {
        "id": str(result.inserted_id),
        "username": user["username"],
        "text": text,
        "image_url": image_url,
        "created_at": message[
            "created_at"
        ].isoformat()
    }

    await manager.broadcast(payload)

    return {
        "ok": True,
        "message": payload
    }


@app.websocket("/ws/chat")
async def websocket_chat(
    websocket: WebSocket
):

    await manager.connect(
        websocket
    )

    try:

        while True:

            await websocket.receive_text()

    except WebSocketDisconnect:

        manager.disconnect(
            websocket
        )

    except Exception:

        manager.disconnect(
            websocket
        )


# =========================================================
# CHAT IMAGE UPLOAD
# =========================================================

@app.post("/api/chat/upload")
async def upload_chat_image(
    request: Request,
    image: UploadFile = File(...)
):

    require_db()

    await require_user(request)

    if not image.content_type or not image.content_type.startswith(
        "image/"
    ):

        raise HTTPException(
            status_code=400,
            detail="Only images are allowed."
        )

    data = await image.read()

    if len(data) > 10 * 1024 * 1024:

        raise HTTPException(
            status_code=400,
            detail="Image is too large."
        )

    file_id = uuid.uuid4().hex

    await files_collection.insert_one({
        "_id": file_id,
        "content_type": image.content_type,
        "filename": image.filename,
        "data": data,
        "created_at": now()
    })

    return {
        "url": "/api/file/" + file_id
    }


# =========================================================
# ELECTION END
# =========================================================

@app.post("/api/election/end")
async def end_election(
    request: Request
):

    require_db()

    key = request.headers.get(
        "X-Moderator-Key",
        ""
    )

    if not MODERATOR_KEY or key != MODERATOR_KEY:

        raise HTTPException(
            status_code=403,
            detail="Moderator access required."
        )

    state = await election.find_one(
        {"_id": "main"}
    )

    if state.get("status") == "ended":

        return {
            "ok": True,
            "already_ended": True
        }

    pipeline = [
        {
            "$group": {
                "_id": "$candidate_id",
                "votes": {
                    "$sum": 1
                }
            }
        },
        {
            "$sort": {
                "votes": -1
            }
        }
    ]

    vote_data = []

    async for item in votes.aggregate(
        pipeline
    ):

        vote_data.append(item)

    winner_id = None

    if vote_data:

        winner_id = vote_data[0]["_id"]

    await election.update_one(
        {"_id": "main"},
        {
            "$set": {
                "status": "ended",
                "ended_at": now(),
                "winner_candidate_id": winner_id
            }
        }
    )

    return {
        "ok": True,
        "winner": (
            str(winner_id)
            if winner_id
            else None
        )
    }

# =========================================================
# WEATHER
# =========================================================

@app.get("/api/weather")
async def weather(
    city: str = None,
    lat: float = None,
    lon: float = None
):

    if not OPENWEATHER_API_KEY:

        raise HTTPException(
            status_code=503,
            detail="OPENWEATHER_API_KEY is not configured."
        )

    url = (
        "https://api.openweathermap.org/data/2.5/weather"
    )

    # -----------------------------------------------------
    # WEATHER BY COORDINATES
    # -----------------------------------------------------

    if lat is not None and lon is not None:

        params = {
            "lat": lat,
            "lon": lon,
            "appid": OPENWEATHER_API_KEY,
            "units": "metric",
            "lang": "ru"
        }

    # -----------------------------------------------------
    # WEATHER BY CITY NAME
    # -----------------------------------------------------

    elif city:

        # Сначала превращаем название места
        # в координаты
        location = await geocode_weather_location(
            city
        )

        if not location:

            raise HTTPException(
                status_code=404,
                detail=(
                    "Место не найдено: "
                    + city
                )
            )

        lat = location["lat"]
        lon = location["lon"]

        params = {
            "lat": lat,
            "lon": lon,
            "appid": OPENWEATHER_API_KEY,
            "units": "metric",
            "lang": "ru"
        }

    else:

        raise HTTPException(
            status_code=400,
            detail=(
                "City or coordinates "
                "are required."
            )
        )

    # -----------------------------------------------------
    # OPENWEATHER
    # -----------------------------------------------------

    async with httpx.AsyncClient(
        timeout=15
    ) as client:

        response = await client.get(
            url,
            params=params
        )

    if response.status_code != 200:

        print(
            "[OPENWEATHER ERROR]",
            response.status_code,
            response.text[:500]
        )

        raise HTTPException(
            status_code=response.status_code,
            detail="Weather service error."
        )

    data = response.json()

    return {
        "city": data["name"],
        "country": data["sys"]["country"],

        "latitude": data["coord"]["lat"],
        "longitude": data["coord"]["lon"],

        "temperature": data["main"]["temp"],
        "feels_like": data["main"]["feels_like"],
        "humidity": data["main"]["humidity"],

        "description": data[
            "weather"
        ][0]["description"],

        "icon": data[
            "weather"
        ][0]["icon"]
    }


# =========================================================
# AI CHAT + WEATHER
# =========================================================

async def groq_chat(messages):
    """
    Отправляет сообщения в Groq и возвращает ответ ИИ.
    """

    if not GROQ_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="GROQ_API_KEY is not configured."
        )

    url = "https://api.groq.com/openai/v1/chat/completions"

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": GROQ_MODEL,
        "messages": messages,
        "temperature": 0.7
    }

    async with httpx.AsyncClient(timeout=120) as client:

        response = await client.post(
            url,
            headers=headers,
            json=payload
        )

    if response.status_code != 200:

        print(
            "[GROQ ERROR]",
            response.status_code,
            response.text[:1000]
        )

        raise HTTPException(
            status_code=502,
            detail="Groq API error."
        )

    data = response.json()

    try:

        return data["choices"][0]["message"]["content"]

    except (KeyError, IndexError, TypeError):

        print(
            "[GROQ ERROR] Invalid response:",
            data
        )

        raise HTTPException(
            status_code=502,
            detail="Invalid response from Groq."
        )


# ---------------------------------------------------------
# WEATHER KEYWORDS
# ---------------------------------------------------------

weather_keywords = [

    "погода",
    "погоде",
    "погоду",
    "погодой",

    "температура",
    "температуре",
    "температуру",
    "температурой",

    "прогноз",
    "прогноз погоды",

    "weather",
    "temperature",
    "forecast"
]


def is_weather_message(message: str) -> bool:

    lower = message.lower()

    return any(
        keyword in lower
        for keyword in weather_keywords
    )


# ---------------------------------------------------------
# COORDINATE DETECTION
# ---------------------------------------------------------

def extract_coordinates(message: str):

    coordinate_patterns = [

        r"(?<!\d)"
        r"(-?\d{1,3}(?:\.\d+)?)"
        r"\s*,\s*"
        r"(-?\d{1,3}(?:\.\d+)?)"
        r"(?!\d)",

        r"(?<!\d)"
        r"(-?\d{1,3}(?:\.\d+)?)"
        r"\s+"
        r"(-?\d{1,3}(?:\.\d+)?)"
        r"(?!\d)"
    ]

    for pattern in coordinate_patterns:

        match = re.search(
            pattern,
            message
        )

        if not match:
            continue

        try:

            lat = float(
                match.group(1)
            )

            lon = float(
                match.group(2)
            )

            if (
                -90 <= lat <= 90
                and
                -180 <= lon <= 180
            ):

                return lat, lon

        except ValueError:

            pass

    return None


# ---------------------------------------------------------
# LOCATION EXTRACTION
# ---------------------------------------------------------

def extract_weather_location(message: str):

    patterns = [

        # Русский:
        # погода в Москве
        # температура в Тель-Авиве
        # прогноз для Минска

        r"(?:погод[аеуы]|"
        r"температур[аеуы]|"
        r"прогноз)"
        r".*?"
        r"(?:в|во|у|для|на|около|возле|рядом\s+с)"
        r"\s+"
        r"(.+?)(?:\?|!|$|,|;)",

        # Английский:
        # weather in London
        # temperature in Paris
        # forecast for Berlin

        r"(?:weather|temperature|forecast)"
        r".*?"
        r"(?:in|at|for|near)"
        r"\s+"
        r"(.+?)(?:\?|!|$|,|;)"
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            message,
            flags=re.IGNORECASE
        )

        if not match:
            continue

        location = match.group(1).strip()

        location = re.sub(
            r"\s+"
            r"(сейчас|сегодня|"
            r"там|в данный момент)"
            r"$",
            "",
            location,
            flags=re.IGNORECASE
        ).strip()

        location = location.strip(
            " \t\n\r.,!?;:"
        )

        if location:
            return location

    return None


# =========================================================
# AI ENDPOINT
# =========================================================

@app.post("/api/ai")
async def ai_chat(
    request: Request
):

    require_db()

    user = await require_user(request)

    data = await request.json()

    message = str(
        data.get("message", "")
    ).strip()

    if not message:

        raise HTTPException(
            status_code=400,
            detail="Message is empty."
        )

    if len(message) > 10000:

        raise HTTPException(
            status_code=400,
            detail="Message is too long."
        )

    # =====================================================
    # WEATHER
    # =====================================================

    if is_weather_message(message):

        coordinates = extract_coordinates(
            message
        )

        # -------------------------------------------------
        # WEATHER BY COORDINATES
        # -------------------------------------------------

        if coordinates:

            lat, lon = coordinates

            try:

                weather_data = await weather(
                    lat=lat,
                    lon=lon
                )

                answer = (
                    f"Сейчас в районе "
                    f"{weather_data['city']}, "
                    f"{weather_data['country']}: "
                    f"{weather_data['temperature']}°C, "
                    f"{weather_data['description']}. "
                    f"Влажность — "
                    f"{weather_data['humidity']}%."
                )

                await ai_messages.insert_one({

                    "user_id": user["_id"],

                    "user_message": message,

                    "assistant_message": answer,

                    "type": "weather",

                    "created_at": now()

                })

                return {
                    "type": "weather",
                    "answer": answer,
                    "weather": weather_data
                }

            except HTTPException as e:

                print(
                    "[WEATHER COORDINATES ERROR]",
                    e.detail
                )

        # -------------------------------------------------
        # WEATHER BY PLACE NAME
        # -------------------------------------------------

        location = extract_weather_location(
            message
        )

        if location:

            try:

                weather_data = await weather(
                    city=location
                )

                answer = (
                    f"Сейчас в "
                    f"{weather_data['city']}, "
                    f"{weather_data['country']}: "
                    f"{weather_data['temperature']}°C, "
                    f"{weather_data['description']}. "
                    f"Влажность — "
                    f"{weather_data['humidity']}%."
                )

                await ai_messages.insert_one({

                    "user_id": user["_id"],

                    "user_message": message,

                    "assistant_message": answer,

                    "type": "weather",

                    "created_at": now()

                })

                return {
                    "type": "weather",
                    "answer": answer,
                    "weather": weather_data
                }

            except HTTPException as e:

                print(
                    "[WEATHER LOCATION ERROR]",
                    e.detail
                )

        # -------------------------------------------------
        # WEATHER REQUEST BUT LOCATION NOT FOUND
        # -------------------------------------------------

        if location:

            answer = (
                f"Я не смог найти место "
                f"«{location}». "
                f"Попробуй указать название "
                f"города или координаты, например "
                f"`50.45, 30.52`."
            )

            await ai_messages.insert_one({

                "user_id": user["_id"],

                "user_message": message,

                "assistant_message": answer,

                "type": "weather_error",

                "created_at": now()

            })

            return {
                "type": "weather",
                "answer": answer,
                "weather": None
            }


    # =====================================================
    # NORMAL GROQ CHAT
    # =====================================================

    system_prompt = """
Ты ИИ-помощник Пинийской Федерации.

Отвечай на русском языке, если пользователь
не попросил другой язык.

Будь полезным, понятным и дружелюбным.

Если пользователь просит создать изображение,
НЕ создавай изображение самостоятельно.

Вместо этого обязательно верни специальную строку:

IMAGE_PROMPT: <подробный улучшенный английский prompt>

После строки IMAGE_PROMPT можешь кратко
описать результат на русском языке.

Если пользователь не просит изображение,
не используй IMAGE_PROMPT.
"""

    answer = await groq_chat([
        {
            "role": "system",
            "content": system_prompt
        },
        {
            "role": "user",
            "content": message
        }
    ])

    # =====================================================
    # IMAGE PROMPT DETECTION
    # =====================================================

    image_prompt = None

    match = re.search(
        r"IMAGE_PROMPT:\s*(.+?)(?:\n|$)",
        answer,
        re.IGNORECASE
    )

    if match:

        image_prompt = (
            match.group(1)
            .strip()
        )

    # =====================================================
    # SAVE AI MESSAGE
    # =====================================================

    await ai_messages.insert_one({

        "user_id": user["_id"],

        "user_message": message,

        "assistant_message": answer,

        "type": (
            "image"
            if image_prompt
            else "chat"
        ),

        "image_prompt": image_prompt,

        "created_at": now()

    })

    # =====================================================
    # RESPONSE
    # =====================================================

    return {

        "type": (
            "image"
            if image_prompt
            else "chat"
        ),

        "answer": answer,

        "image_prompt": image_prompt

    }

# =========================================================
# CLOUDFLARE IMAGE GENERATION
# =========================================================

@app.post("/api/ai/image")
async def generate_image(
    request: Request
):

    require_db()

    user = await require_user(request)

    if not CLOUDFLARE_ACCOUNT_ID:
        raise HTTPException(
            status_code=503,
            detail="Cloudflare account ID is not configured."
        )

    if not CLOUDFLARE_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="Cloudflare API key is not configured."
        )

    data = await request.json()

    prompt = str(
        data.get("prompt", "")
    ).strip()

    if not prompt:
        raise HTTPException(
            status_code=400,
            detail="Prompt is empty."
        )

    url = (
        "https://api.cloudflare.com/client/v4/accounts/"
        + CLOUDFLARE_ACCOUNT_ID
        + "/ai/run/"
        + CLOUDFLARE_IMAGE_MODEL
    )

    headers = {
        "Authorization":
            f"Bearer {CLOUDFLARE_API_KEY}",
        "Content-Type":
            "application/json"
    }

    async with httpx.AsyncClient(
        timeout=120
    ) as client:

        response = await client.post(
            url,
            headers=headers,
            json={
                "prompt": prompt
            }
        )

    # -----------------------------------------------------
    # CLOUDFLARE ERROR
    # -----------------------------------------------------

    if response.status_code != 200:

        print(
            "[CLOUDFLARE IMAGE ERROR]",
            response.status_code,
            response.text[:1000]
        )

        raise HTTPException(
            status_code=502,
            detail=(
                "Cloudflare image generation failed: "
                + response.text[:500]
            )
        )

    # -----------------------------------------------------
    # GET IMAGE DATA
    # -----------------------------------------------------

    content_type = (
        response.headers.get(
            "content-type",
            ""
        ).lower()
    )

    image_data = None

    # Cloudflare sometimes returns the image directly
    if content_type.startswith("image/"):

        image_data = response.content

        real_content_type = (
            content_type.split(";")[0]
        )

    else:

        # Otherwise try to read JSON response
        try:

            result = response.json()

        except Exception:

            print(
                "[CLOUDFLARE IMAGE ERROR] "
                "Unknown response format:",
                content_type,
                response.text[:1000]
            )

            raise HTTPException(
                status_code=502,
                detail=(
                    "Cloudflare returned "
                    "an unknown image format."
                )
            )

        # -------------------------------------------------
        # TRY COMMON CLOUDFLARE IMAGE FIELDS
        # -------------------------------------------------

        image_base64 = (
            result.get("result", {})
            if isinstance(
                result.get("result"),
                dict
            )
            else {}
        ).get("image")

        if image_base64:

            import base64

            try:

                image_data = base64.b64decode(
                    image_base64
                )

            except Exception:

                raise HTTPException(
                    status_code=502,
                    detail=(
                        "Cloudflare returned "
                        "invalid base64 image data."
                    )
                )

            real_content_type = "image/png"

        else:

            print(
                "[CLOUDFLARE IMAGE RESPONSE]",
                result
            )

            raise HTTPException(
                status_code=502,
                detail=(
                    "Cloudflare did not return "
                    "an image."
                )
            )

    # -----------------------------------------------------
    # SAVE IMAGE TO MONGODB
    # -----------------------------------------------------

    if not image_data:

        raise HTTPException(
            status_code=502,
            detail="Generated image is empty."
        )

    file_id = uuid.uuid4().hex

    await files_collection.insert_one({
        "_id": file_id,
        "content_type": real_content_type,
        "filename": "ai-generated.png",
        "data": image_data,
        "created_at": now(),
        "owner_id": user["_id"]
    })

    return {
        "image_url":
            "/api/file/" + file_id
    }

# =========================================================
# VOICE -> GROQ WHISPER
# =========================================================

@app.post("/api/ai/voice")
async def ai_voice(
    request: Request,
    audio: UploadFile = File(...)
):

    require_db()

    await require_user(request)

    if not GROQ_API_KEY:

        raise HTTPException(
            status_code=503,
            detail="GROQ_API_KEY is not configured."
        )

    audio_data = await audio.read()

    files = {
        "file": (
            audio.filename or "audio.webm",
            audio_data,
            audio.content_type
            or "audio/webm"
        )
    }

    data = {
        "model": "whisper-large-v3",
        "response_format": "json"
    }

    headers = {
        "Authorization":
            f"Bearer {GROQ_API_KEY}"
    }

    url = (
        "https://api.groq.com/openai/v1/audio/transcriptions"
    )

    async with httpx.AsyncClient(
        timeout=120
    ) as client:

        response = await client.post(
            url,
            headers=headers,
            data=data,
            files=files
        )

    if response.status_code != 200:

        raise HTTPException(
            status_code=502,
            detail="Groq transcription failed."
        )

    result = response.json()

    return {
        "text": result.get(
            "text",
            ""
        )
    }


# =========================================================
# HEALTH
# =========================================================

@app.get("/health")
async def health():

    mongo_status = (
        "connected"
        if db is not None
        else "not_configured"
    )

    return {
        "status": "ok",
        "service": "Piniyskaya Federation Elections",
        "mongodb": mongo_status
    }
