# ============================================================
# ClientBoost.in â€” Instagram DM Audit + Lead Conversion Engine
# Free-core version: Gemini audit + Google Sheets CRM + Template Profile Preview
# ============================================================

from flask import Flask, request, jsonify, send_file, abort
import requests
import os
import time
import json
import threading
import io
import re
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
import google.generativeai as genai

import gspread
from google.oauth2.service_account import Credentials
from gspread.utils import rowcol_to_a1

app = Flask(__name__)

# ============================================================
# ENVIRONMENT VARIABLES
# ============================================================

VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "clientboost2024")
PAGE_ACCESS_TOKEN = os.environ.get("PAGE_ACCESS_TOKEN", "")
GRAPH_API_VERSION = os.environ.get("GRAPH_API_VERSION", "v21.0")

GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID", "")
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")

# Public base URL is required only for sending generated mockup images.
# Example: https://clientboost-bot.onrender.com
PUBLIC_BASE_URL = (
    os.environ.get("PUBLIC_BASE_URL", "")
    or os.environ.get("APP_BASE_URL", "")
    or os.environ.get("RENDER_EXTERNAL_URL", "")
).strip().rstrip("/")

GEMINI_AUDIT_API_KEYS = [
    key.strip()
    for key in os.environ.get(
        "GEMINI_AUDIT_API_KEYS",
        os.environ.get("GEMINI_API_KEY", "")
    ).split(",")
    if key.strip()
]

GEMINI_CONVERSION_API_KEYS = [
    key.strip()
    for key in os.environ.get(
        "GEMINI_CONVERSION_API_KEYS",
        os.environ.get("GEMINI_API_KEY", "")
    ).split(",")
    if key.strip()
]

# Keep only models available on your account. The code falls back automatically.
# Professional order: best model across all keys first, then next model across all keys.
GEMINI_AUDIT_MODELS = [
    model.strip()
    for model in os.environ.get(
        "GEMINI_AUDIT_MODELS",
        "gemini-2.5-pro,gemini-2.5-flash,gemini-2.5-flash-lite,gemini-2.0-flash"
    ).split(",")
    if model.strip()
]

GEMINI_CONVERSION_MODELS = [
    model.strip()
    for model in os.environ.get(
        "GEMINI_CONVERSION_MODELS",
        "gemini-2.5-flash,gemini-2.5-flash-lite,gemini-2.0-flash"
    ).split(",")
    if model.strip()
]

RETRY_INTERVAL_SECONDS = int(os.environ.get("RETRY_INTERVAL_SECONDS", "600"))
KEY_COOLDOWN_SECONDS = int(os.environ.get("KEY_COOLDOWN_SECONDS", "900"))
RETRY_SECRET = os.environ.get("RETRY_SECRET", "")
ENABLE_BACKGROUND_RETRY = os.environ.get("ENABLE_BACKGROUND_RETRY", "true").lower() == "true"
AUTO_HANDOVER_ON_ECHO = os.environ.get("AUTO_HANDOVER_ON_ECHO", "false").lower() == "true"

MAX_CONVERSION_TURNS_BEFORE_CONTACT = int(os.environ.get("MAX_CONVERSION_TURNS_BEFORE_CONTACT", "2"))
IMAGE_MONTHLY_LIMIT_PER_USER = int(os.environ.get("IMAGE_MONTHLY_LIMIT_PER_USER", "1"))
MOCKUP_OUTPUT_DIR = Path(os.environ.get("MOCKUP_OUTPUT_DIR", "generated_mockups"))
MOCKUP_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================
# GLOBALS
# ============================================================

sheet_lock = threading.Lock()
gemini_lock = threading.Lock()
user_locks_lock = threading.Lock()

_gspread_client = None
_workbook = None
_ws_cache = {}

recent_bot_sends = {}
# Cooldown is per engine + key + model. This prevents one model quota error from blocking other models.
api_cooldowns = {}
user_locks = {}
seen_message_ids = {}

background_retry_started = False

# ============================================================
# GOOGLE SHEETS HEADERS
# ============================================================

SHEET_HEADERS = {
    "Leads": [
        "sender_id",
        "business_name",
        "business_type",
        "location",
        "goal",
        "service_interest",
        "whatsapp",
        "email",
        "budget",
        "timeline",
        "lead_temperature",
        "lead_score",
        "handover_status",
        "conversation_state",
        "data_json",
        "audit_id",
        "last_audit_summary",
        "last_profile_image_url",
        "created_at",
        "updated_at",
        "last_user_message_at",
        "last_bot_message_at"
    ],
    "Pending_Audits": [
        "audit_id",
        "sender_id",
        "business_name",
        "business_type",
        "location",
        "image_url",
        "status",
        "retry_count",
        "last_attempt_at",
        "created_at",
        "last_user_message_at",
        "warning_23h_sent",
        "audit_sent_at",
        "error_type"
    ],
    "Audit_History": [
        "audit_id",
        "sender_id",
        "business_name",
        "business_type",
        "location",
        "audit_summary",
        "full_audit",
        "created_at"
    ],
    "Message_Log": [
        "timestamp",
        "sender_id",
        "direction",
        "message_type",
        "message",
        "state"
    ],
    "Handover_Status": [
        "sender_id",
        "handover_active",
        "reason",
        "activated_at",
        "notes"
    ],
    "API_Status": [
        "engine",
        "key_index",
        "model_name",
        "status",
        "cooldown_until",
        "last_error",
        "last_used_at"
    ],
    "Image_Mockups": [
        "mockup_id",
        "sender_id",
        "business_name",
        "business_type",
        "location",
        "selected_style",
        "month_key",
        "source_screenshot_url",
        "generated_image_url",
        "provider",
        "model_used",
        "status",
        "created_at",
        "sent_at",
        "error"
    ]
}

# ============================================================
# TIME HELPERS
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def now_iso():
    return now_utc().isoformat()


def current_month_key():
    return now_utc().strftime("%Y-%m")


def parse_iso(value):
    if not value:
        return None

    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def seconds_since(value):
    parsed = parse_iso(value)

    if not parsed:
        return 999999999

    return (now_utc() - parsed).total_seconds()

# ============================================================
# BASIC TEXT HELPERS
# ============================================================

def normalize_text(text):
    text = str(text or "").lower().strip()
    text = text.replace("â€™", "'")
    text = re.sub(r"\s+", " ", text)
    return text


def compact_text(text):
    text = str(text or "").strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def is_truthy(value):
    return str(value).strip().lower() in ["true", "yes", "1", "active"]


def get_user_lock(sender_id):
    sender_id = str(sender_id)

    with user_locks_lock:
        if sender_id not in user_locks:
            user_locks[sender_id] = threading.Lock()

        return user_locks[sender_id]


def is_duplicate_message(mid):
    if not mid:
        return False

    now_ts = time.time()

    expired = [
        saved_mid
        for saved_mid, saved_time in seen_message_ids.items()
        if now_ts - saved_time > 600
    ]

    for saved_mid in expired:
        seen_message_ids.pop(saved_mid, None)

    if mid in seen_message_ids:
        return True

    seen_message_ids[mid] = now_ts
    return False

# ============================================================
# GOOGLE SHEETS HELPERS
# ============================================================

def get_workbook():
    global _gspread_client, _workbook

    if _workbook:
        return _workbook

    if not GOOGLE_SHEET_ID or not GOOGLE_SERVICE_ACCOUNT_JSON:
        print("Google Sheets env missing.")
        return None

    try:
        service_info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)

        if "private_key" in service_info:
            service_info["private_key"] = service_info["private_key"].replace("\\n", "\n")

        scopes = ["https://www.googleapis.com/auth/spreadsheets"]

        credentials = Credentials.from_service_account_info(
            service_info,
            scopes=scopes
        )

        _gspread_client = gspread.authorize(credentials)
        _workbook = _gspread_client.open_by_key(GOOGLE_SHEET_ID)

        ensure_all_sheets()
        return _workbook

    except Exception as error:
        print(f"Google Sheets connection error: {error}")
        return None


def ensure_all_sheets():
    workbook = _workbook

    if not workbook:
        return

    for sheet_name, headers in SHEET_HEADERS.items():
        try:
            ws = workbook.worksheet(sheet_name)
        except Exception:
            ws = workbook.add_worksheet(title=sheet_name, rows=1000, cols=60)

        ensure_headers(ws, headers)


def get_ws(sheet_name):
    if sheet_name in _ws_cache:
        return _ws_cache[sheet_name]

    workbook = get_workbook()

    if not workbook:
        return None

    try:
        ws = workbook.worksheet(sheet_name)
    except Exception:
        ws = workbook.add_worksheet(title=sheet_name, rows=1000, cols=60)

    ensure_headers(ws, SHEET_HEADERS.get(sheet_name, []))
    _ws_cache[sheet_name] = ws
    return ws


def ensure_headers(ws, required_headers):
    if not required_headers:
        return

    try:
        current_headers = ws.row_values(1)

        if not current_headers:
            ws.update("A1", [required_headers])
            return

        final_headers = current_headers[:]

        for header in required_headers:
            if header not in final_headers:
                final_headers.append(header)

        if final_headers != current_headers:
            ws.update("A1", [final_headers])

    except Exception as error:
        print(f"Header setup error for {ws.title}: {error}")


def get_headers(ws):
    return ws.row_values(1)


def append_record(sheet_name, data):
    with sheet_lock:
        ws = get_ws(sheet_name)

        if not ws:
            return False

        try:
            headers = get_headers(ws)
            row = [""] * len(headers)

            for key, value in data.items():
                if key in headers:
                    row[headers.index(key)] = "" if value is None else str(value)

            ws.append_row(row, value_input_option="USER_ENTERED")
            return True

        except Exception as error:
            print(f"Append record error ({sheet_name}): {error}")
            return False


def find_latest_row_by_value(sheet_name, key, value):
    ws = get_ws(sheet_name)

    if not ws:
        return None, None

    try:
        headers = get_headers(ws)

        if key not in headers:
            return ws, None

        col_index = headers.index(key) + 1
        values = ws.col_values(col_index)

        latest_row = None

        for row_number, cell_value in enumerate(values, start=1):
            if row_number == 1:
                continue

            if str(cell_value).strip() == str(value).strip():
                latest_row = row_number

        return ws, latest_row

    except Exception as error:
        print(f"Find latest row error ({sheet_name}): {error}")
        return ws, None


def find_all_rows_by_value(sheet_name, key, value):
    ws = get_ws(sheet_name)

    if not ws:
        return None, []

    try:
        headers = get_headers(ws)

        if key not in headers:
            return ws, []

        col_index = headers.index(key) + 1
        values = ws.col_values(col_index)

        rows = []

        for row_number, cell_value in enumerate(values, start=1):
            if row_number == 1:
                continue

            if str(cell_value).strip() == str(value).strip():
                rows.append(row_number)

        return ws, rows

    except Exception as error:
        print(f"Find all rows error ({sheet_name}): {error}")
        return ws, []


def update_row(sheet_name, row_number, data):
    with sheet_lock:
        ws = get_ws(sheet_name)

        if not ws or not row_number:
            return False

        try:
            headers = get_headers(ws)
            updates = []

            for key, value in data.items():
                if key not in headers:
                    continue

                col_number = headers.index(key) + 1
                cell = rowcol_to_a1(row_number, col_number)
                updates.append({
                    "range": cell,
                    "values": [["" if value is None else str(value)]]
                })

            if updates:
                ws.batch_update(updates, value_input_option="USER_ENTERED")

            return True

        except Exception as error:
            print(f"Update row error ({sheet_name}): {error}")
            return False


def update_all_rows_by_value(sheet_name, key, value, data):
    ws, rows = find_all_rows_by_value(sheet_name, key, value)

    if not ws:
        return False

    ok = True

    for row_number in rows:
        if not update_row(sheet_name, row_number, data):
            ok = False

    return ok


def upsert_record(sheet_name, key, key_value, data):
    ws, row_number = find_latest_row_by_value(sheet_name, key, key_value)

    if row_number:
        return update_row(sheet_name, row_number, data)

    new_data = dict(data)
    new_data[key] = key_value
    return append_record(sheet_name, new_data)


def get_record(sheet_name, key, key_value):
    ws, row_number = find_latest_row_by_value(sheet_name, key, key_value)

    if not ws or not row_number:
        return None

    try:
        headers = get_headers(ws)
        values = ws.row_values(row_number)
        record = {}

        for index, header in enumerate(headers):
            record[header] = values[index] if index < len(values) else ""

        record["_row_number"] = row_number
        return record

    except Exception as error:
        print(f"Get record error ({sheet_name}): {error}")
        return None


def get_all_records(sheet_name):
    ws = get_ws(sheet_name)

    if not ws:
        return []

    try:
        return ws.get_all_records(default_blank="")
    except Exception as error:
        print(f"Get all records error ({sheet_name}): {error}")
        return []


def log_message(sender_id, direction, message_type, message, state=""):
    append_record("Message_Log", {
        "timestamp": now_iso(),
        "sender_id": sender_id,
        "direction": direction,
        "message_type": message_type,
        "message": str(message or "")[:45000],
        "state": state
    })

# ============================================================
# STATE HELPERS
# ============================================================

def create_lead_if_missing(sender_id):
    sender_id = str(sender_id)
    existing = get_record("Leads", "sender_id", sender_id)

    if existing:
        return existing

    created_at = now_iso()

    append_record("Leads", {
        "sender_id": sender_id,
        "conversation_state": "new",
        "data_json": "{}",
        "lead_temperature": "new",
        "lead_score": "0",
        "handover_status": "none",
        "created_at": created_at,
        "updated_at": created_at
    })

    return get_record("Leads", "sender_id", sender_id)


def get_lead(sender_id):
    return create_lead_if_missing(str(sender_id))


def get_state(sender_id):
    sender_id = str(sender_id)
    lead = get_lead(sender_id)

    if not lead:
        return {"step": "new", "data": {}, "handover": False, "lead": {}}

    try:
        data = json.loads(lead.get("data_json", "{}") or "{}")
    except Exception:
        data = {}

    handover_active = str(lead.get("handover_status", "")).lower() == "active"

    handover_row = get_record("Handover_Status", "sender_id", sender_id)

    if handover_row and is_truthy(handover_row.get("handover_active", "")):
        handover_active = True

    return {
        "step": lead.get("conversation_state") or "new",
        "data": data,
        "handover": handover_active,
        "lead": lead
    }


def calculate_lead_score(step, data):
    score = 0

    if data.get("name"):
        score += 10
    if data.get("type"):
        score += 10
    if data.get("location"):
        score += 10
    if data.get("image_url"):
        score += 20
    if step in ["audit_sent", "lead_goal", "lead_contact", "lead_timeline", "human_handover"]:
        score += 20
    if data.get("goal"):
        score += 15
    if data.get("whatsapp") or data.get("email"):
        score += 25
    if data.get("timeline"):
        score += 10

    return min(score, 100)


def save_state(sender_id, step=None, data=None, extra=None):
    sender_id = str(sender_id)

    update_data = {"updated_at": now_iso()}

    if step is not None:
        update_data["conversation_state"] = step

    if data is not None:
        update_data["data_json"] = json.dumps(data, ensure_ascii=False)
        update_data["lead_score"] = calculate_lead_score(step or get_state(sender_id).get("step", "new"), data)

    if extra:
        update_data.update(extra)

    upsert_record("Leads", "sender_id", sender_id, update_data)


def reset_user(sender_id):
    sender_id = str(sender_id)

    save_state(
        sender_id,
        step="new",
        data={},
        extra={
            "business_name": "",
            "business_type": "",
            "location": "",
            "goal": "",
            "service_interest": "",
            "whatsapp": "",
            "email": "",
            "budget": "",
            "timeline": "",
            "lead_temperature": "new",
            "lead_score": "0",
            "handover_status": "none",
            "audit_id": "",
            "last_audit_summary": "",
            "last_profile_image_url": "",
            "last_user_message_at": now_iso(),
            "updated_at": now_iso()
        }
    )

    upsert_record("Handover_Status", "sender_id", sender_id, {
        "handover_active": "false",
        "reason": "reset",
        "activated_at": "",
        "notes": "Reset by user"
    })

    update_all_rows_by_value("Pending_Audits", "sender_id", sender_id, {
        "status": "cancelled",
        "last_attempt_at": now_iso(),
        "error_type": "reset"
    })


def activate_handover(sender_id, reason="lead_ready", notes=""):
    sender_id = str(sender_id)

    upsert_record("Handover_Status", "sender_id", sender_id, {
        "handover_active": "true",
        "reason": reason,
        "activated_at": now_iso(),
        "notes": notes
    })

    state = get_state(sender_id)

    save_state(
        sender_id,
        step="human_handover",
        data=state.get("data", {}),
        extra={
            "handover_status": "active",
            "lead_temperature": "hot",
            "lead_score": "100",
            "updated_at": now_iso()
        }
    )


def update_last_user_message(sender_id):
    save_state(sender_id, extra={"last_user_message_at": now_iso()})
    refresh_pending_audit_window(sender_id)


def update_last_bot_message(sender_id):
    save_state(sender_id, extra={"last_bot_message_at": now_iso()})

# ============================================================
# INSTAGRAM DM SENDER
# ============================================================

def send_dm(recipient_id, message, message_type="bot", state=""):
    recipient_id = str(recipient_id)
    message = compact_text(message)

    if not message:
        return {"error": "empty message"}

    if not PAGE_ACCESS_TOKEN:
        print("PAGE_ACCESS_TOKEN missing.")
        return {"error": "PAGE_ACCESS_TOKEN missing"}

    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/me/messages"

    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": message},
        "messaging_type": "RESPONSE"
    }

    params = {"access_token": PAGE_ACCESS_TOKEN}
    headers = {"Content-Type": "application/json"}

    try:
        response = requests.post(url, json=payload, params=params, headers=headers, timeout=20)
        print("DM send status:", response.status_code, response.text[:500])
        response.raise_for_status()

        recent_bot_sends[recipient_id] = time.time()
        update_last_bot_message(recipient_id)
        log_message(recipient_id, "outbound", message_type, message, state)

        return response.json()

    except requests.exceptions.RequestException as error:
        print(f"Failed to send DM to {recipient_id}: {error}")
        return {"error": str(error)}


def send_image_dm(recipient_id, image_url, caption="", message_type="image", state=""):
    recipient_id = str(recipient_id)

    if not PAGE_ACCESS_TOKEN:
        print("PAGE_ACCESS_TOKEN missing.")
        return {"error": "PAGE_ACCESS_TOKEN missing"}

    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/me/messages"

    payload = {
        "recipient": {"id": recipient_id},
        "message": {
            "attachment": {
                "type": "image",
                "payload": {
                    "url": image_url,
                    "is_reusable": True
                }
            }
        },
        "messaging_type": "RESPONSE"
    }

    params = {"access_token": PAGE_ACCESS_TOKEN}
    headers = {"Content-Type": "application/json"}

    try:
        response = requests.post(url, json=payload, params=params, headers=headers, timeout=30)
        print("Image DM send status:", response.status_code, response.text[:500])
        response.raise_for_status()

        recent_bot_sends[recipient_id] = time.time()
        update_last_bot_message(recipient_id)
        log_message(recipient_id, "outbound", message_type, caption or image_url, state)

        if caption:
            time.sleep(0.7)
            send_dm(recipient_id, caption, message_type="image_caption", state=state)

        return response.json()

    except requests.exceptions.RequestException as error:
        print(f"Failed to send image DM to {recipient_id}: {error}")
        return {"error": str(error)}


def delayed_send_dm(recipient_id, message, delay=1.0, message_type="bot", state=""):
    time.sleep(delay)
    return send_dm(recipient_id, message, message_type=message_type, state=state)


def is_recent_bot_echo(user_id, seconds=120):
    user_id = str(user_id)
    last_sent = recent_bot_sends.get(user_id)

    if not last_sent:
        return False

    return (time.time() - last_sent) <= seconds

# ============================================================
# IMAGE DOWNLOAD
# ============================================================

def download_image(image_url):
    try:
        headers = {"Authorization": f"Bearer {PAGE_ACCESS_TOKEN}"}
        response = requests.get(image_url, headers=headers, timeout=25)
        response.raise_for_status()
        image = Image.open(io.BytesIO(response.content))
        return image
    except Exception as error:
        print(f"Failed to download image: {error}")
        return None


def has_audit_context(data):
    return bool(
        str(data.get("name", "")).strip()
        and str(data.get("type", "")).strip()
        and str(data.get("location", "")).strip()
    )

# ============================================================
# GEMINI + API STATUS
# ============================================================

def classify_gemini_error(error):
    error_text = str(error).lower()

    if "429" in error_text or "resource_exhausted" in error_text:
        return "quota"
    if "quota" in error_text or "rate limit" in error_text or "exceeded" in error_text:
        return "quota"
    if "api key" in error_text or "permission" in error_text or "unauthorized" in error_text:
        return "auth"
    if "404" in error_text or ("model" in error_text and "not found" in error_text):
        return "model"

    return "error"


def get_engine_keys(engine):
    if engine == "audit":
        return GEMINI_AUDIT_API_KEYS
    return GEMINI_CONVERSION_API_KEYS


def get_engine_models(engine):
    if engine == "audit":
        return GEMINI_AUDIT_MODELS
    return GEMINI_CONVERSION_MODELS


def is_model_on_cooldown(engine, key_index, model_name):
    cooldown_until = api_cooldowns.get((engine, key_index, model_name))

    if not cooldown_until:
        return False

    return now_utc() < cooldown_until


def set_model_cooldown(engine, key_index, model_name, error_message=""):
    cooldown_until = now_utc() + timedelta(seconds=KEY_COOLDOWN_SECONDS)
    api_cooldowns[(engine, key_index, model_name)] = cooldown_until

    upsert_api_status(
        engine=engine,
        key_index=key_index,
        model_name=model_name,
        status="cooldown",
        cooldown_until=cooldown_until.isoformat(),
        last_error=error_message
    )


def upsert_api_status(engine, key_index, model_name, status, cooldown_until="", last_error=""):
    records = get_all_records("API_Status")
    row_to_update = None

    for index, record in enumerate(records, start=2):
        if (
            str(record.get("engine")) == str(engine)
            and str(record.get("key_index")) == str(key_index)
            and str(record.get("model_name")) == str(model_name)
        ):
            row_to_update = index
            break

    data = {
        "engine": engine,
        "key_index": key_index,
        "model_name": model_name,
        "status": status,
        "cooldown_until": cooldown_until,
        "last_error": str(last_error)[:500],
        "last_used_at": now_iso()
    }

    if row_to_update:
        update_row("API_Status", row_to_update, data)
    else:
        append_record("API_Status", data)


def generate_text_with_pool(engine, contents):
    keys = get_engine_keys(engine)
    models = get_engine_models(engine)

    if not keys:
        return {"text": None, "status": "no_keys", "error": "No Gemini API keys configured"}

    last_error = ""
    quota_seen = False

    # Professional fallback: best model across all keys, then second model across all keys.
    for model_name in models:
        for key_index, api_key in enumerate(keys):
            if is_model_on_cooldown(engine, key_index, model_name):
                quota_seen = True
                continue

            try:
                print(f"Trying Gemini {engine} model: {model_name} | key index: {key_index}")

                generation_config = {
                    "temperature": 0.15 if engine == "audit" else 0.25,
                    "top_p": 0.8,
                }

                with gemini_lock:
                    genai.configure(api_key=api_key)
                    model = genai.GenerativeModel(model_name)
                    response = model.generate_content(contents, generation_config=generation_config)

                response_text = getattr(response, "text", "") or ""

                if response_text.strip():
                    upsert_api_status(
                        engine=engine,
                        key_index=key_index,
                        model_name=model_name,
                        status="success",
                        last_error=""
                    )

                    return {"text": response_text.strip(), "status": "success", "error": ""}

                last_error = "Empty Gemini response"

                upsert_api_status(
                    engine=engine,
                    key_index=key_index,
                    model_name=model_name,
                    status="empty_response",
                    last_error=last_error
                )

            except Exception as error:
                error_type = classify_gemini_error(error)
                last_error = str(error)
                print(f"Gemini {engine} failed | key {key_index} | model {model_name}: {error}")

                if error_type == "quota":
                    quota_seen = True
                    set_model_cooldown(engine, key_index, model_name, last_error)
                    continue

                upsert_api_status(
                    engine=engine,
                    key_index=key_index,
                    model_name=model_name,
                    status=error_type,
                    last_error=last_error
                )
                continue

    if quota_seen:
        return {"text": None, "status": "rate_limited", "error": last_error}

    return {"text": None, "status": "failed", "error": last_error}

# ============================================================
# BUSINESS TYPE HELPERS
# ============================================================

def normalize_business_category(business_type):
    text = normalize_text(business_type)

    if any(word in text for word in ["restaurant", "cafe", "food", "takeaway", "hotel", "bakery", "cloud kitchen"]):
        return "restaurant"
    if any(word in text for word in ["salon", "beauty", "spa", "makeup", "clinic", "doctor", "dental", "skin", "aesthetic"]):
        return "salon_clinic"
    if any(word in text for word in ["real estate", "property", "realtor", "builder", "developer", "plot", "land"]):
        return "real_estate"
    if any(word in text for word in ["gym", "fitness", "trainer", "yoga", "workout"]):
        return "gym"
    if any(word in text for word in ["ecommerce", "e-commerce", "boutique", "fashion", "clothing", "store", "shop", "clothes"]):
        return "ecommerce"
    if any(word in text for word in ["agency", "service", "consultant", "coach", "personal brand", "creator", "influencer", "software", "saas"]):
        return "service"

    return "other"


def natural_next_step_for_business(business_type):
    category = normalize_business_category(business_type)

    if category == "restaurant":
        return "DM MENU or WhatsApp ORDER"
    if category == "salon_clinic":
        return "DM BOOK or Book Appointment"
    if category == "real_estate":
        return "DM SITE or Request Details"
    if category == "gym":
        return "DM TRIAL or Book Trial Session"
    if category == "ecommerce":
        return "DM CATALOG or WhatsApp ORDER"
    if category == "service":
        return "DM CALL or Book Consultation"

    return "DM INFO or Book Consultation"


def parse_business_type(raw_text):
    value = normalize_text(raw_text).replace(".", "")

    type_map = {
        "1": "Restaurant", "one": "Restaurant", "restaurant": "Restaurant",
        "2": "Cafe", "two": "Cafe", "cafe": "Cafe", "coffee": "Cafe",
        "3": "Salon", "three": "Salon", "salon": "Salon", "beauty": "Salon",
        "4": "Gym", "four": "Gym", "gym": "Gym", "fitness": "Gym",
        "5": "Boutique", "five": "Boutique", "boutique": "Boutique", "fashion": "Boutique",
        "6": "Clinic", "six": "Clinic", "clinic": "Clinic", "doctor": "Clinic",
        "7": "Real Estate", "seven": "Real Estate", "real estate": "Real Estate", "property": "Real Estate",
        "8": "E-commerce", "eight": "E-commerce", "ecommerce": "E-commerce", "e-commerce": "E-commerce", "online store": "E-commerce",
        "9": "Personal Brand", "nine": "Personal Brand", "personal brand": "Personal Brand", "creator": "Personal Brand",
        "10": "Other", "ten": "Other", "other": "Other", "something else": "Other", "different": "Other"
    }

    if value in type_map:
        return type_map[value]

    return raw_text.strip()

# ============================================================
# GOAL OPTIONS + INTENT UNDERSTANDING
# ============================================================

def get_goal_options(business_type):
    category = normalize_business_category(business_type)

    if category == "restaurant":
        return [
            ("1", "More orders", ["order", "orders", "sales", "more orders", "food orders"]),
            ("2", "Better food reels/content", ["content", "reels", "food reel", "posts", "photos", "videos"]),
            ("3", "More repeat customers", ["repeat", "customers", "loyal", "retention"]),
            ("4", "WhatsApp ordering system", ["whatsapp", "ordering system", "system", "automation"]),
            ("5", "Offers and local campaigns", ["offer", "offers", "campaign", "local", "discount"]),
            ("6", "Full marketing management", ["full", "management", "manage", "everything", "full management"]),
            ("7", "Need guidance", ["not sure", "unsure", "guide", "guidance", "don't know", "dont know", "confused"])
        ]

    if category == "salon_clinic":
        return [
            ("1", "More appointments", ["appointment", "appointments", "booking", "bookings"]),
            ("2", "Better trust and reviews", ["trust", "reviews", "proof", "testimonial"]),
            ("3", "More local reach", ["local", "reach", "nearby", "area"]),
            ("4", "Better reels/content", ["content", "reels", "posts", "videos"]),
            ("5", "Ads for bookings", ["ads", "advertising", "paid", "bookings"]),
            ("6", "Full page management", ["full", "management", "manage", "everything"]),
            ("7", "Need guidance", ["not sure", "unsure", "guide", "guidance", "don't know", "dont know", "confused"])
        ]

    if category == "real_estate":
        return [
            ("1", "More property leads", ["leads", "property leads", "buyers", "enquiries"]),
            ("2", "Better listing content", ["listing", "content", "posts", "property content"]),
            ("3", "More site visit enquiries", ["site visit", "visit", "enquiry", "enquiries"]),
            ("4", "Local authority building", ["authority", "trust", "brand", "local"]),
            ("5", "Ads for buyers/investors", ["ads", "buyers", "investors", "paid"]),
            ("6", "Full lead generation system", ["full", "management", "lead generation", "system", "everything"]),
            ("7", "Need guidance", ["not sure", "unsure", "guide", "guidance", "don't know", "dont know", "confused"])
        ]

    if category == "gym":
        return [
            ("1", "More trial enquiries", ["trial", "trials", "enquiries", "leads"]),
            ("2", "More memberships", ["membership", "memberships", "members"]),
            ("3", "Better transformation content", ["transformation", "content", "reels", "results"]),
            ("4", "Local ads", ["ads", "local ads", "paid"]),
            ("5", "WhatsApp/DM follow-up system", ["whatsapp", "dm", "follow up", "automation"]),
            ("6", "Full Instagram management", ["full", "management", "manage", "everything"]),
            ("7", "Need guidance", ["not sure", "unsure", "guide", "guidance", "don't know", "dont know", "confused"])
        ]

    if category == "ecommerce":
        return [
            ("1", "More product sales", ["sales", "product sales", "orders"]),
            ("2", "Better product content", ["content", "product content", "reels", "posts"]),
            ("3", "More WhatsApp orders", ["whatsapp", "orders", "dm orders"]),
            ("4", "Ads for conversions", ["ads", "conversion", "paid"]),
            ("5", "Influencer/content strategy", ["influencer", "strategy", "content"]),
            ("6", "Full growth system", ["full", "growth", "management", "everything"]),
            ("7", "Need guidance", ["not sure", "unsure", "guide", "guidance", "don't know", "dont know", "confused"])
        ]

    return [
        ("1", "More qualified leads", ["leads", "qualified leads", "clients", "customers"]),
        ("2", "Better authority content", ["authority", "content", "trust", "positioning"]),
        ("3", "More consultation calls", ["calls", "consultation", "bookings", "appointments"]),
        ("4", "Ads and funnel system", ["ads", "funnel", "paid", "lead generation"]),
        ("5", "Personal brand positioning", ["brand", "personal brand", "positioning"]),
        ("6", "Full growth management", ["full", "management", "manage", "everything"]),
        ("7", "Need guidance", ["not sure", "unsure", "guide", "guidance", "don't know", "dont know", "confused"])
    ]


def get_goal_menu(business_type):
    lines = ["What do you want most right now? ðŸ‘‡", ""]

    for number, label, _keywords in get_goal_options(business_type):
        lines.append(f"{number}. {label}")

    return "\n".join(lines)


def classify_user_intent(text):
    value = normalize_text(text)
    clean = re.sub(r"[^a-z0-9+@.\s]", "", value).strip()

    if not clean:
        return "empty"
    if clean in ["stop", "cancel", "unsubscribe", "not interested", "no thanks", "no thank you"]:
        return "reject"
    if clean in ["reset", "restart", "start over", "start again"]:
        return "reset"
    if clean in ["continue", "yes continue", "continue audit", "send audit"]:
        return "continue"
    if any(word in clean for word in ["preview", "mockup", "visual", "profile look", "how it look", "how my profile", "sample design"]):
        return "preview"
    if clean in ["?", "what", "why", "how", "explain", "what happened", "tell me more", "more details", "details"]:
        return "question"
    if any(word in clean for word in ["price", "pricing", "cost", "how much", "package", "charges"]):
        return "pricing"
    if any(word in clean for word in ["later", "not now", "maybe later", "after some time"]):
        return "delay"
    if any(word in clean for word in ["yes", "ok", "okay", "done", "do it", "go ahead", "let us", "lets", "start", "interested", "help", "continue", "proceed"]):
        return "proceed"
    if any(word in clean for word in ["full", "management", "manage everything", "everything", "complete"]):
        return "full_management"

    return "general"


def parse_goal(business_type, text):
    value = normalize_text(text)
    value_clean = re.sub(r"[^a-z0-9\s]", "", value).strip()

    if value_clean in ["?", "what", "why", "how", "what happened", "explain"]:
        return {"type": "question", "goal": ""}

    if value_clean in ["not sure", "unsure", "idk", "i dont know", "i don't know", "confused", "guide me", "guidance"]:
        return {"type": "goal", "goal": "Need guidance"}

    options = get_goal_options(business_type)

    for number, label, keywords in options:
        if value_clean == number:
            return {"type": "goal", "goal": label}

    number_match = re.search(r"\b([1-7])\b", value_clean)

    if number_match:
        selected_number = number_match.group(1)

        for number, label, _keywords in options:
            if number == selected_number:
                return {"type": "goal", "goal": label}

    best_score = 0
    best_goal = ""

    for _number, label, keywords in options:
        score = 0

        if label.lower() in value:
            score += 5

        for keyword in keywords:
            if keyword in value:
                score += 3

        if score > best_score:
            best_score = score
            best_goal = label

    if best_goal:
        return {"type": "goal", "goal": best_goal}

    intent = classify_user_intent(text)

    if intent == "full_management":
        return {"type": "goal", "goal": "Full management"}
    if intent == "proceed":
        return {"type": "goal", "goal": "Need guidance"}
    if intent == "delay":
        return {"type": "delay", "goal": ""}
    if intent == "reject":
        return {"type": "reject", "goal": ""}

    return {"type": "goal", "goal": text.strip() if text.strip() else "Need guidance"}

# ============================================================
# CONTACT EXTRACTION
# ============================================================

def extract_email(text):
    match = re.search(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", text or "")
    return match.group(0) if match else ""


def extract_phone(text):
    if not text:
        return ""

    candidates = re.findall(r"(?:\+?\d[\d\s\-()]{7,}\d)", text)

    for candidate in candidates:
        digits = re.sub(r"\D", "", candidate)

        if 10 <= len(digits) <= 15:
            return candidate.strip()

    return ""


def has_contact(text):
    return bool(extract_phone(text) or extract_email(text))

# ============================================================
# AUDIT PROMPT + CLEANING
# ============================================================

def create_audit_prompt(name, business_type, location):
    natural_action = natural_next_step_for_business(business_type)

    return f"""
You are the senior Instagram growth auditor at ClientBoost.

You are reviewing a business Instagram profile from one screenshot.

Business:
Name: {name}
Type: {business_type}
Location: {location}

Analyse only what is visible in the screenshot:
username, name field, bio, profile photo, follower/following count, post count, story highlights, visible grid, visual clarity, trust signals, offer clarity, local clarity and enquiry path.

Critical scoring rules:
- The score is a Visible Profile Score, not real revenue, not real account analytics, and not a growth guarantee.
- Use this fixed rubric out of 100:
  Bio clarity: 15
  Offer/positioning clarity: 15
  Trust proof/highlights: 15
  Content/grid quality: 20
  Lead/enquiry path: 20
  Local/search clarity: 10
  Brand consistency: 5
- Mention the sub-scores briefly and give the final score out of 100.
- Be consistent. Do not randomly change the score for the same screenshot.
- If something is not visible, say it is not visible and score only from visible evidence.

Output rules:
- Write like an experienced human strategist.
- Use simple English.
- Use clean bullets.
- Use professional emojis in headings.
- Keep suitable for Instagram DM.
- Maximum 850 words.
- Do not use markdown symbols like **, ##, tables, or code formatting.
- Do not write: CTA, Soft CTA, prompt, AI, Gemini, model, API, automation, code, or system.
- Do not recommend DM GROWTH or reply GROWTH.
- Recommended next step for this business should be natural, such as: {natural_action}
- Do not overpromise or guarantee results.
- Every section must include visible evidence from the screenshot or say not visible.

Structure exactly like this:

ðŸŽ¯ CLIENTBOOST INSTAGRAM AUDIT

ðŸ¢ Business: {name}
ðŸ“ Location: {location}
ðŸ·ï¸ Category: {business_type}

Quick verdict:
Give 2 short lines: strongest visible asset and biggest visible growth blocker.

ðŸ“Š 1. Visible Profile Score
Score: X/100

Sub-scores:
â€¢ Bio clarity: X/15
â€¢ Offer/positioning: X/15
â€¢ Trust/highlights: X/15
â€¢ Content/grid: X/20
â€¢ Lead path: X/20
â€¢ Local/search clarity: X/10
â€¢ Brand consistency: X/5

Why this score:
â€¢ reason based on screenshot
â€¢ reason based on screenshot
â€¢ reason based on screenshot

âœï¸ 2. Bio & Positioning
Current issue:
Short explanation.

Better bio idea:
Write one improved bio for this exact business.

Why it works:
â€¢ reason
â€¢ reason
â€¢ reason

ðŸ“¸ 3. Content & Grid
Working well:
â€¢ point
â€¢ point

Improve:
â€¢ point
â€¢ point

Post ideas:
â€¢ idea 1
â€¢ idea 2
â€¢ idea 3

â­ 4. Trust & Highlights
Current issue:
Short explanation.

Recommended highlights:
â€¢ Services/Menu
â€¢ Reviews/Results
â€¢ How It Works
â€¢ FAQ
â€¢ Contact/Book Now

Best trust fix:
One practical action.

ðŸ“ 5. Local Reach
Current issue:
Short explanation.

Fixes:
â€¢ location keyword idea
â€¢ local content idea
â€¢ hashtag/search idea

ðŸ’¬ 6. Lead Flow
Current issue:
Explain what may stop people from contacting them.

Best next step:
Give the best natural next step for their business. Do not call it CTA.

ðŸ—“ï¸ 7. 7-Day Action Plan
Day 1: one action
Day 2: one action
Day 3: one action
Day 4: one action
Day 5: one action
Day 6: one action
Day 7: one action

ðŸ Final verdict
Give one honest final line and one priority move.
"""


def clean_ai_text_for_instagram(text, business_type=""):
    if not text:
        return ""

    action = natural_next_step_for_business(business_type)
    cleaned = str(text)

    replacements = {
        "**": "",
        "__": "",
        "###": "",
        "##": "",
        "#": "",
        "`": "",
        "Soft CTA:": "",
        "CTA recommendation:": "Best next step:",
        "CTA Recommendation:": "Best next step:",
        "CTA:": "Best next step:",
        "Call to Action:": "Best next step:",
        "call-to-action": "next step",
        "Call-to-action": "Next step",
        "DM GROWTH": action,
        "Dm Growth": action,
        "dm growth": action,
        "reply GROWTH": f"use {action}",
        "Reply GROWTH": f"use {action}",
        "send GROWTH": f"use {action}",
        "Send GROWTH": f"use {action}",
        "Gemini": "",
        "API": "",
        "model": "",
        "automation": ""
    }

    for old, new in replacements.items():
        cleaned = cleaned.replace(old, new)

    cleaned = re.sub(r"\n\s*Soft CTA\s*:?\s*\n?", "\n", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\n\s*CTA\s*:?\s*\n?", "\n", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.replace("|", " ")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = compact_text(cleaned)

    return cleaned


def analyse_screenshot_with_gemini(image, name, business_type, location):
    prompt = create_audit_prompt(name, business_type, location)
    return generate_text_with_pool("audit", [prompt, image])

# ============================================================
# MESSAGE SPLITTING
# ============================================================

def split_message(text, limit=850):
    text = compact_text(text)
    parts = []
    current = ""

    for line in text.split("\n"):
        if len(current) + len(line) + 1 > limit:
            if current.strip():
                parts.append(current.strip())
            current = line + "\n"
        else:
            current += line + "\n"

    if current.strip():
        parts.append(current.strip())

    return parts

# ============================================================
# AUDIT QUEUE
# ============================================================

def create_audit_id():
    return f"audit_{uuid.uuid4().hex[:12]}"


def enqueue_pending_audit(sender_id, data, image_url, error_type="rate_limited"):
    sender_id = str(sender_id)
    audit_id = data.get("audit_id") or create_audit_id()
    existing = get_record("Pending_Audits", "audit_id", audit_id)

    record = {
        "audit_id": audit_id,
        "sender_id": sender_id,
        "business_name": data.get("name", ""),
        "business_type": data.get("type", ""),
        "location": data.get("location", ""),
        "image_url": image_url,
        "status": "pending",
        "retry_count": existing.get("retry_count", "0") if existing else "0",
        "last_attempt_at": now_iso(),
        "created_at": existing.get("created_at", now_iso()) if existing else now_iso(),
        "last_user_message_at": now_iso(),
        "warning_23h_sent": "false",
        "audit_sent_at": "",
        "error_type": error_type
    }

    if existing and existing.get("_row_number"):
        update_row("Pending_Audits", existing["_row_number"], record)
    else:
        append_record("Pending_Audits", record)

    data["audit_id"] = audit_id

    save_state(
        sender_id,
        step="audit_pending",
        data=data,
        extra={"audit_id": audit_id, "lead_temperature": "audit_pending"}
    )

    return audit_id


def refresh_pending_audit_window(sender_id):
    sender_id = str(sender_id)
    records = get_all_records("Pending_Audits")

    for index, record in enumerate(records, start=2):
        if str(record.get("sender_id")) == sender_id and str(record.get("status")).lower() in ["pending", "waiting_user"]:
            update_row("Pending_Audits", index, {
                "status": "pending",
                "last_user_message_at": now_iso(),
                "warning_23h_sent": "false"
            })


def save_audit_history(audit_id, sender_id, name, business_type, location, audit_text):
    summary = audit_text[:800] if audit_text else ""

    append_record("Audit_History", {
        "audit_id": audit_id,
        "sender_id": sender_id,
        "business_name": name,
        "business_type": business_type,
        "location": location,
        "audit_summary": summary,
        "full_audit": audit_text,
        "created_at": now_iso()
    })

    state = get_state(sender_id)
    data = state.get("data", {})

    save_state(
        sender_id,
        step="audit_sent",
        data=data,
        extra={
            "audit_id": audit_id,
            "last_audit_summary": summary,
            "lead_temperature": "nurture",
            "updated_at": now_iso()
        }
    )


def send_audit_to_user(sender_id, audit_id, name, business_type, location, audit_text):
    audit_text = clean_ai_text_for_instagram(audit_text, business_type)

    intro = (
        "Your ClientBoost audit is ready âœ…\n\n"
        f"ðŸ¢ Business: {name}\n"
        f"ðŸ·ï¸ Type: {business_type}\n"
        f"ðŸ“ Location: {location}\n\n"
        "Hereâ€™s the clear breakdown:"
    )

    send_dm(sender_id, intro, message_type="audit_intro", state="audit_sent")
    time.sleep(0.8)

    for part in split_message(audit_text):
        send_dm(sender_id, part, message_type="audit_part", state="audit_sent")
        time.sleep(0.9)

    final_message = (
        "If you want ClientBoost to help fix the profile, content direction, trust signals and enquiry flow, send HELP.\n\n"
        "If you want a simple visual direction of how your profile could look, send PREVIEW."
    )

    send_dm(sender_id, final_message, message_type="audit_next_step", state="audit_sent")
    save_audit_history(audit_id, sender_id, name, business_type, location, audit_text)


def process_audit_request(sender_id, data, image_url, first_time=True, from_retry=False):
    sender_id = str(sender_id)

    if first_time:
        send_dm(
            sender_id,
            "Screenshot received ðŸ“¸\n\n"
            "Weâ€™re reviewing your Instagram profile now.\n\n"
            "Youâ€™ll get a clear audit covering profile clarity, content, trust, local reach and enquiry flow.\n\n"
            "This usually takes a short moment â³",
            message_type="audit_processing",
            state="audit_processing"
        )

    image = download_image(image_url)

    if not image:
        if not from_retry:
            send_dm(
                sender_id,
                "The screenshot did not load properly.\n\nPlease send it once more and weâ€™ll continue.",
                message_type="image_error",
                state="ask_screenshot"
            )
        return {"status": "image_failed"}

    result = analyse_screenshot_with_gemini(
        image=image,
        name=data.get("name", "Your business"),
        business_type=data.get("type", "Business"),
        location=data.get("location", "Your location")
    )

    if result.get("text"):
        audit_id = data.get("audit_id") or create_audit_id()

        send_audit_to_user(
            sender_id=sender_id,
            audit_id=audit_id,
            name=data.get("name", "Your business"),
            business_type=data.get("type", "Business"),
            location=data.get("location", "Your location"),
            audit_text=result["text"]
        )

        return {"status": "sent", "audit_id": audit_id}

    audit_id = enqueue_pending_audit(
        sender_id=sender_id,
        data=data,
        image_url=image_url,
        error_type=result.get("status", "failed")
    )

    if not from_retry:
        send_dm(
            sender_id,
            "Your profile review is saved ðŸ“Œ\n\n"
            "It is taking a little longer than usual, but no need to resend anything.\n\n"
            "Your audit will be sent here automatically once it is ready.",
            message_type="audit_queued",
            state="audit_pending"
        )

    return {"status": "queued", "audit_id": audit_id}


def retry_pending_audits(limit_sender_id=None):
    records = get_all_records("Pending_Audits")
    processed = 0
    sent = 0
    warnings = 0
    waiting = 0

    for index, record in enumerate(records, start=2):
        status = str(record.get("status", "")).lower()
        sender_id = str(record.get("sender_id", ""))

        if limit_sender_id and sender_id != str(limit_sender_id):
            continue
        if status != "pending":
            continue

        last_attempt_at = record.get("last_attempt_at", "")
        last_user_message_at = record.get("last_user_message_at", record.get("created_at", ""))
        warning_sent = str(record.get("warning_23h_sent", "")).lower() == "true"

        if not limit_sender_id and seconds_since(last_attempt_at) < RETRY_INTERVAL_SECONDS:
            continue

        window_age = seconds_since(last_user_message_at)

        if window_age >= 23 * 60 * 60 and not warning_sent:
            send_dm(
                sender_id,
                "This is taking longer than expected.\n\n"
                "Your audit request is still saved.\n"
                "Please reply CONTINUE here so we can keep it active and send it as soon as it is ready.",
                message_type="audit_23h_warning",
                state="audit_pending"
            )

            update_row("Pending_Audits", index, {"warning_23h_sent": "true", "last_attempt_at": now_iso()})
            warnings += 1
            continue

        if window_age >= 24 * 60 * 60:
            update_row("Pending_Audits", index, {"status": "waiting_user", "last_attempt_at": now_iso()})
            waiting += 1
            continue

        try:
            retry_count = int(record.get("retry_count", 0))
        except Exception:
            retry_count = 0

        update_row("Pending_Audits", index, {"retry_count": retry_count + 1, "last_attempt_at": now_iso()})

        data = {
            "name": record.get("business_name", ""),
            "type": record.get("business_type", ""),
            "location": record.get("location", ""),
            "audit_id": record.get("audit_id", "")
        }

        result = process_audit_request(sender_id, data, record.get("image_url", ""), first_time=False, from_retry=True)
        processed += 1

        if result.get("status") == "sent":
            update_row("Pending_Audits", index, {"status": "sent", "audit_sent_at": now_iso()})
            sent += 1

        time.sleep(1)

    return {"processed": processed, "sent": sent, "warnings": warnings, "waiting_user": waiting}

# ============================================================
# TEMPLATE PROFILE PREVIEW RENDERER â€” NO IMAGE API USED
# ============================================================

PREVIEW_STYLES = {
    "1": "Clean Premium Grid",
    "2": "Trust + Proof Grid",
    "3": "Product / Service Showcase",
    "4": "Bold Conversion Grid"
}

STYLE_COLORS = {
    "1": {"bg": (248, 248, 246), "card": (255, 255, 255), "accent": (24, 24, 24), "soft": (238, 238, 236)},
    "2": {"bg": (244, 247, 244), "card": (255, 255, 255), "accent": (40, 91, 75), "soft": (226, 238, 232)},
    "3": {"bg": (247, 245, 240), "card": (255, 255, 255), "accent": (142, 92, 43), "soft": (240, 229, 214)},
    "4": {"bg": (20, 22, 26), "card": (34, 37, 43), "accent": (255, 190, 72), "soft": (55, 59, 68)}
}


def parse_preview_style(text):
    value = normalize_text(text)
    value = re.sub(r"[^a-z0-9\s]", "", value).strip()

    if value in ["1", "one", "first", "clean", "premium", "clean premium"]:
        return "1"
    if value in ["2", "two", "second", "trust", "proof", "trust proof"]:
        return "2"
    if value in ["3", "three", "third", "product", "service", "showcase"]:
        return "3"
    if value in ["4", "four", "fourth", "bold", "conversion", "bold conversion"]:
        return "4"

    return ""


def get_preview_options_message():
    return (
        "Choose a profile preview style ðŸ‘‡\n\n"
        "1. Clean Premium Grid\n"
        "2. Trust + Proof Grid\n"
        "3. Product / Service Showcase\n"
        "4. Bold Conversion Grid\n\n"
        "Reply 1, 2, 3 or 4."
    )


def has_mockup_this_month(sender_id):
    month_key = current_month_key()
    records = get_all_records("Image_Mockups")

    used = []
    for record in records:
        if str(record.get("sender_id")) == str(sender_id) and str(record.get("month_key")) == month_key:
            if str(record.get("status", "")).lower() in ["generated", "sent"]:
                used.append(record)

    return len(used) >= IMAGE_MONTHLY_LIMIT_PER_USER


def get_font(size, bold=False):
    candidates = []
    if bold:
        candidates.extend([
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"
        ])
    candidates.extend([
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"
    ])

    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)

    return ImageFont.load_default()


def wrap_text(draw, text, font, max_width):
    words = str(text or "").split()
    lines = []
    current = ""

    for word in words:
        test = (current + " " + word).strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word

    if current:
        lines.append(current)

    return lines


def draw_text_block(draw, text, xy, font, fill, max_width, line_gap=6):
    x, y = xy
    for line in wrap_text(draw, text, font, max_width):
        draw.text((x, y), line, font=font, fill=fill)
        bbox = draw.textbbox((x, y), line, font=font)
        y += (bbox[3] - bbox[1]) + line_gap
    return y


def suggested_bio_lines(name, business_type, location):
    category = normalize_business_category(business_type)

    if category == "restaurant":
        return [
            f"{name} | {location} Food",
            "Fresh orders â€¢ Offers â€¢ Reviews",
            "Menu + WhatsApp ordering below"
        ]
    if category == "salon_clinic":
        return [
            f"{name} | {location}",
            "Services â€¢ Results â€¢ Reviews",
            "Book your appointment below"
        ]
    if category == "real_estate":
        return [
            f"{name} | {location} Properties",
            "Listings â€¢ Site visits â€¢ Buyer guidance",
            "DM SITE for current options"
        ]
    if category == "gym":
        return [
            f"{name} | Fitness in {location}",
            "Transformations â€¢ Training â€¢ Trials",
            "Book a trial session below"
        ]
    if category == "ecommerce":
        return [
            f"{name} | Online Store",
            "New drops â€¢ Reviews â€¢ Offers",
            "DM CATALOG or order on WhatsApp"
        ]

    return [
        f"{name} | {business_type}",
        f"Helping customers in {location}",
        "Proof â€¢ Services â€¢ Contact below"
    ]


def content_tiles_for_style(style_id, business_type):
    category = normalize_business_category(business_type)

    if category == "restaurant":
        base = ["Best Seller", "Kitchen Proof", "Review", "Menu", "Fresh Prep", "Offer", "How to Order", "Customer Order", "Local Reel"]
    elif category == "real_estate":
        base = ["Property Tour", "Location Proof", "Client Visit", "Documents", "Buyer Tip", "New Listing", "Area Guide", "Site Visit", "DM SITE"]
    elif category == "salon_clinic":
        base = ["Result", "Service", "Review", "Before/After", "Expert Tip", "Offer", "Booking", "FAQ", "Trust Proof"]
    elif category == "gym":
        base = ["Transformation", "Workout", "Member Proof", "Trainer Tip", "Trial Offer", "Class", "Nutrition", "Result", "DM TRIAL"]
    elif category == "ecommerce":
        base = ["Product", "Review", "How to Use", "Offer", "New Drop", "UGC", "Best Seller", "Packing", "DM CATALOG"]
    else:
        base = ["Service", "Proof", "Review", "FAQ", "Case Study", "Offer", "Behind Scenes", "Client Result", "Book Call"]

    if style_id == "2":
        return ["Review", "Proof", "Result", "FAQ", "Behind Scenes", "Client Story", "How It Works", "Trust", "Contact"]
    if style_id == "3":
        return ["Hero Offer", "Service 1", "Service 2", "Product", "Before/After", "Use Case", "Benefits", "Proof", "Order/Book"]
    if style_id == "4":
        return ["Problem", "Solution", "Proof", "Offer", "Result", "Trust", "FAQ", "Urgency", "Action"]

    return base


def render_profile_mockup(sender_id, data, style_id):
    mockup_id = f"mockup_{uuid.uuid4().hex[:12]}"
    name = data.get("name", "Your Business")
    business_type = data.get("type", "Business")
    location = data.get("location", "Your location")
    style_name = PREVIEW_STYLES.get(style_id, PREVIEW_STYLES["1"])
    colors = STYLE_COLORS.get(style_id, STYLE_COLORS["1"])

    width, height = 1080, 1600
    image = Image.new("RGB", (width, height), colors["bg"])
    draw = ImageDraw.Draw(image)

    title_font = get_font(48, bold=True)
    subtitle_font = get_font(28, bold=False)
    body_font = get_font(26, bold=False)
    small_font = get_font(22, bold=False)
    bold_body = get_font(28, bold=True)
    tile_font = get_font(28, bold=True)

    dark_mode = style_id == "4"
    text_color = (242, 242, 242) if dark_mode else (30, 30, 30)
    muted = (190, 190, 190) if dark_mode else (95, 95, 95)

    # Header
    draw.text((60, 50), "ClientBoost Profile Preview", font=title_font, fill=text_color)
    draw.text((60, 110), "Strategic visual direction â€” not a final design", font=subtitle_font, fill=muted)

    # Phone/profile card
    card_x, card_y, card_w, card_h = 70, 180, 940, 1320
    draw.rounded_rectangle((card_x, card_y, card_x + card_w, card_y + card_h), radius=46, fill=colors["card"])

    # Top bar
    draw.text((card_x + 50, card_y + 40), name[:28], font=bold_body, fill=text_color)
    draw.text((card_x + 50, card_y + 78), f"{business_type} â€¢ {location}", font=small_font, fill=muted)

    # Profile circle
    accent = colors["accent"]
    draw.ellipse((card_x + 55, card_y + 145, card_x + 195, card_y + 285), fill=accent)
    initials = "".join([part[0] for part in name.split()[:2]]).upper() or "CB"
    draw.text((card_x + 95, card_y + 185), initials[:2], font=get_font(42, bold=True), fill=(255, 255, 255) if not dark_mode else (20, 20, 20))

    # Profile stats - explicitly preview, not fake performance
    stats_x = card_x + 250
    for i, (num, label) in enumerate([("Plan", "content"), ("Trust", "proof"), ("Fast", "enquiry")]):
        x = stats_x + i * 190
        draw.text((x, card_y + 150), num, font=bold_body, fill=text_color)
        draw.text((x, card_y + 190), label, font=small_font, fill=muted)

    # Bio block
    bio_y = card_y + 320
    draw.text((card_x + 55, bio_y), "Suggested bio direction", font=bold_body, fill=text_color)
    bio_y += 48
    for line in suggested_bio_lines(name, business_type, location):
        draw.text((card_x + 55, bio_y), line, font=body_font, fill=text_color)
        bio_y += 38

    # Style and enquiry path pill
    pill_y = bio_y + 15
    draw.rounded_rectangle((card_x + 55, pill_y, card_x + 885, pill_y + 70), radius=22, fill=colors["soft"])
    pill_text = f"Style: {style_name}  â€¢  Next step: {natural_next_step_for_business(business_type)}"
    draw_text_block(draw, pill_text, (card_x + 80, pill_y + 18), small_font, text_color, 770, line_gap=4)

    # Highlight bubbles
    h_y = pill_y + 115
    highlights = ["Services", "Reviews", "Results", "FAQ", "Contact"]
    spacing = 170
    for i, label in enumerate(highlights):
        cx = card_x + 95 + i * spacing
        draw.ellipse((cx, h_y, cx + 92, h_y + 92), outline=accent, width=6, fill=colors["soft"])
        bbox = draw.textbbox((0, 0), label, font=small_font)
        draw.text((cx + 46 - (bbox[2] - bbox[0]) / 2, h_y + 105), label, font=small_font, fill=text_color)

    # Grid preview
    grid_y = h_y + 170
    draw.text((card_x + 55, grid_y), "9-post grid direction", font=bold_body, fill=text_color)
    grid_y += 55
    tile_gap = 14
    tile_size = int((card_w - 110 - tile_gap * 2) / 3)
    tiles = content_tiles_for_style(style_id, business_type)

    tile_colors = [
        accent,
        colors["soft"],
        (255, 255, 255) if not dark_mode else (45, 48, 55),
        colors["soft"],
        accent,
        (255, 255, 255) if not dark_mode else (45, 48, 55),
        (255, 255, 255) if not dark_mode else (45, 48, 55),
        colors["soft"],
        accent,
    ]

    for idx, label in enumerate(tiles[:9]):
        row = idx // 3
        col = idx % 3
        x = card_x + 55 + col * (tile_size + tile_gap)
        y = grid_y + row * (tile_size + tile_gap)
        fill = tile_colors[idx]
        draw.rounded_rectangle((x, y, x + tile_size, y + tile_size), radius=18, fill=fill)

        label_color = (255, 255, 255) if fill == accent else text_color
        # Add small visual icon circle
        draw.ellipse((x + 22, y + 22, x + 76, y + 76), fill=(255, 255, 255) if fill == accent else accent)
        # Text centered-ish
        label_lines = wrap_text(draw, label, tile_font, tile_size - 44)
        text_y = y + tile_size - 92
        for line in label_lines[:2]:
            bbox = draw.textbbox((0, 0), line, font=tile_font)
            draw.text((x + tile_size / 2 - (bbox[2] - bbox[0]) / 2, text_y), line, font=tile_font, fill=label_color)
            text_y += 34

    # Footer
    footer = "Preview shows structure: bio, highlights, trust content, grid themes and enquiry path."
    draw_text_block(draw, footer, (80, 1530), small_font, muted, 920)

    filepath = MOCKUP_OUTPUT_DIR / f"{mockup_id}.png"
    image.save(filepath, format="PNG", optimize=True)

    return mockup_id, filepath


def create_and_send_profile_preview(sender_id, style_id):
    sender_id = str(sender_id)
    state = get_state(sender_id)
    data = state.get("data", {})

    if has_mockup_this_month(sender_id):
        send_dm(
            sender_id,
            "You already received your free profile preview this month âœ…\n\n"
            "Use it as your direction for improving the page. If you want our team to implement it, send HELP.",
            message_type="mockup_limit",
            state="audit_sent"
        )
        save_state(sender_id, step="audit_sent", data=data)
        return

    mockup_id = ""
    row_created = False

    try:
        mockup_id, filepath = render_profile_mockup(sender_id, data, style_id)
        month_key = current_month_key()

        if PUBLIC_BASE_URL:
            image_url = f"{PUBLIC_BASE_URL}/mockups/{mockup_id}.png"
        else:
            image_url = ""

        append_record("Image_Mockups", {
            "mockup_id": mockup_id,
            "sender_id": sender_id,
            "business_name": data.get("name", ""),
            "business_type": data.get("type", ""),
            "location": data.get("location", ""),
            "selected_style": PREVIEW_STYLES.get(style_id, ""),
            "month_key": month_key,
            "source_screenshot_url": data.get("image_url", ""),
            "generated_image_url": image_url,
            "provider": "ClientBoost template renderer",
            "model_used": "No image API",
            "status": "generated",
            "created_at": now_iso(),
            "sent_at": "",
            "error": ""
        })
        row_created = True

        send_dm(
            sender_id,
            "Creating your profile preview now âœ…\n\n"
            "This is a strategic visual direction, not a final design.",
            message_type="mockup_processing",
            state="preview_style"
        )
        time.sleep(1)

        if image_url:
            result = send_image_dm(
                sender_id,
                image_url,
                caption=(
                    "Here is your profile preview direction ðŸ‘†\n\n"
                    "It shows the improved structure: bio, highlights, content grid and enquiry path.\n\n"
                    "If you want ClientBoost to build this properly, send HELP."
                ),
                message_type="mockup_image",
                state="audit_sent"
            )

            update_all_rows_by_value("Image_Mockups", "mockup_id", mockup_id, {
                "status": "sent" if not result.get("error") else "send_failed",
                "sent_at": now_iso() if not result.get("error") else "",
                "error": result.get("error", "")
            })
        else:
            send_dm(
                sender_id,
                "Your preview direction is ready, but our team needs to connect the public preview link once.\n\n"
                "Send HELP and weâ€™ll continue manually.",
                message_type="mockup_base_url_missing",
                state="audit_sent"
            )
            update_all_rows_by_value("Image_Mockups", "mockup_id", mockup_id, {
                "status": "generated_not_sent",
                "error": "PUBLIC_BASE_URL missing"
            })

        save_state(sender_id, step="audit_sent", data=data)

    except Exception as error:
        print(f"Mockup generation error: {error}")
        if row_created and mockup_id:
            update_all_rows_by_value("Image_Mockups", "mockup_id", mockup_id, {"status": "failed", "error": str(error)[:500]})
        send_dm(
            sender_id,
            "The preview is taking longer than expected.\n\n"
            "Your audit is still ready. Send HELP if you want our team to guide you from here.",
            message_type="mockup_failed",
            state="audit_sent"
        )
        save_state(sender_id, step="audit_sent", data=data)

# ============================================================
# CONVERSION MESSAGES
# ============================================================

def explain_goal_question_message(_business_type):
    return (
        "No issue ðŸ‘\n\n"
        "This question helps us understand what kind of help you need first, so we donâ€™t suggest random services.\n\n"
        "You can reply with a number, or just type it in words.\n\n"
        "Example: content, ads, orders, leads, bookings, or full management."
    )


def contact_request_message(business_type, goal):
    category = normalize_business_category(business_type)

    if goal.lower() in ["need guidance", "guidance"]:
        return (
            "That is completely fine ðŸ‘\n\n"
            "If youâ€™re not sure, the right first step is to review your profile, content, trust signals and enquiry path together.\n\n"
            "Where should our strategist contact you?\n"
            "Send your WhatsApp number, email, or both."
        )

    if "full" in goal.lower() or "management" in goal.lower():
        return (
            "Full management makes sense if you want ClientBoost to handle the strategy, content direction, posting, lead flow and growth execution properly.\n\n"
            "Before our team suggests the right scope, where should our strategist contact you?\n"
            "Send your WhatsApp number, email, or both."
        )

    if category == "restaurant":
        return (
            f"Understood â€” {goal}. âœ…\n\n"
            "For food businesses, the priority is not just posting food photos. The profile needs a clear offer, trust proof, menu access and a fast ordering path.\n\n"
            "Where should our strategist contact you?\nSend your WhatsApp number, email, or both."
        )

    if category == "real_estate":
        return (
            f"Understood â€” {goal}. âœ…\n\n"
            "For real estate, trust and clarity matter first. People enquire when the property, location proof and next step are clear.\n\n"
            "Where should our strategist contact you?\nSend your WhatsApp number, email, or both."
        )

    if category == "salon_clinic":
        return (
            f"Understood â€” {goal}. âœ…\n\n"
            "For appointment-based businesses, trust and a simple booking path matter most. People need confidence before they book.\n\n"
            "Where should our strategist contact you?\nSend your WhatsApp number, email, or both."
        )

    return (
        f"Understood â€” {goal}. âœ…\n\n"
        "The right first step is to fix profile clarity, trust content and enquiry flow before scaling with content or ads.\n\n"
        "Where should our strategist contact you?\nSend your WhatsApp number, email, or both."
    )


def pricing_reply():
    return (
        "Pricing depends on what you want handled.\n\n"
        "There is a difference between:\n"
        "â€¢ profile and strategy fix\n"
        "â€¢ content management\n"
        "â€¢ ads and lead generation\n"
        "â€¢ full growth management\n\n"
        "Send your WhatsApp number or email and our strategist will suggest the right scope after understanding your business."
    )


def contact_stage_reply(user_text, business_type, goal, data):
    intent = classify_user_intent(user_text)
    turns = int(data.get("conversion_turns", 0) or 0)

    if intent == "pricing":
        return pricing_reply()

    if intent == "question":
        if turns >= 1:
            return (
                "Simple answer: ClientBoost helps fix the parts that turn profile visitors into real enquiries â€” profile clarity, content direction, trust proof and lead flow.\n\n"
                "To guide you properly, send your WhatsApp number or email and our strategist will continue from there."
            )

        return (
            "Good question.\n\n"
            "We ask for contact because your audit shows areas that need proper context before suggesting a service: profile clarity, content direction, trust proof and enquiry flow.\n\n"
            "Send your WhatsApp number or email when ready."
        )

    if intent == "full_management":
        return (
            "Full management is where ClientBoost can handle the complete growth side: strategy, content direction, posting, lead flow, automation and conversion system.\n\n"
            "Send your WhatsApp number or email and our strategist will understand your business properly before suggesting the right scope."
        )

    if intent == "delay":
        return (
            "No problem.\n\n"
            "Your audit is still useful. When youâ€™re ready, send your WhatsApp number or email and our team will guide you from there."
        )

    if intent == "reject":
        return (
            "No problem. Your audit is yours to use.\n\n"
            "You can come back anytime if you want help implementing it."
        )

    if turns >= MAX_CONVERSION_TURNS_BEFORE_CONTACT:
        return (
            "Iâ€™ll keep it simple.\n\n"
            "If you want ClientBoost to help, send your WhatsApp number or email and our strategist will continue personally."
        )

    return (
        "ClientBoost can help with strategy, content direction, branding, ads, automation and conversion systems.\n\n"
        "The best starting point depends on your business stage and goal.\n\n"
        "Send your WhatsApp number or email and our strategist will guide you properly."
    )


def final_handover_message():
    return (
        "Perfect â€” noted âœ…\n\n"
        "A ClientBoost strategist will take over from here.\n\n"
        "You can also send:\n"
        "â€¢ your current monthly marketing budget\n"
        "â€¢ your target customers\n"
        "â€¢ what you want help with first\n\n"
        "Our team will continue personally from here. ðŸ¤"
    )

# ============================================================
# MAIN MESSAGE HANDLER
# ============================================================

def handle_message(sender_id, message_obj):
    sender_id = str(sender_id)

    with get_user_lock(sender_id):
        _handle_message_locked(sender_id, message_obj)


def _handle_message_locked(sender_id, message_obj):
    raw_text = message_obj.get("text", "").strip()
    text = normalize_text(raw_text)

    attachments = message_obj.get("attachments", [])
    image_url = None

    for attachment in attachments:
        if attachment.get("type") == "image":
            image_url = attachment.get("payload", {}).get("url")
            break

    state = get_state(sender_id)
    step = state.get("step", "new")
    data = state.get("data", {})

    log_message(sender_id, "inbound", "image" if image_url else "text", raw_text or "[image]", step)
    update_last_user_message(sender_id)

    intent = classify_user_intent(raw_text)

    if intent == "reset":
        reset_user(sender_id)
        send_dm(sender_id, "Reset done âœ…\n\nType GROWTH whenever youâ€™re ready for your free Instagram audit.", message_type="reset", state="new")
        return

    if intent == "continue":
        refresh_pending_audit_window(sender_id)
        send_dm(sender_id, "Thanks âœ…\n\nYour audit request is active again. No need to resend the screenshot.", message_type="continue_audit", state="audit_pending")
        retry_pending_audits(limit_sender_id=sender_id)
        return

    if state.get("handover") is True:
        print(f"Human handover active for {sender_id}. Bot ignored message.")
        return

    if intent == "reject" and step in ["lead_goal", "lead_contact", "lead_timeline", "audit_sent", "preview_style"]:
        save_state(sender_id, step="audit_sent", data=data, extra={"lead_temperature": "nurture"})
        send_dm(sender_id, "No problem. Your audit is yours to use.\n\nYou can come back anytime if you want help implementing it.", message_type="not_interested", state="audit_sent")
        return

    # Image handling.
    # Accept image if user is in ask_screenshot OR if enough business context is saved.
    if image_url:
        latest_state = get_state(sender_id)
        step = latest_state.get("step", step)
        data = latest_state.get("data", data)

        if step == "ask_screenshot" or has_audit_context(data):
            data["image_url"] = image_url

            save_state(
                sender_id,
                step="audit_processing",
                data=data,
                extra={
                    "business_name": data.get("name", ""),
                    "business_type": data.get("type", ""),
                    "location": data.get("location", ""),
                    "last_profile_image_url": image_url
                }
            )

            process_audit_request(sender_id, data, image_url, first_time=True, from_retry=False)
            return

        send_dm(sender_id, "Thanks for the image ðŸ“¸\n\nType GROWTH first so we can start your free audit properly.", message_type="image_early", state=step)
        return

    # Start audit flow.
    if text == "growth" and step == "new":
        save_state(sender_id, step="ask_name", data={}, extra={"lead_temperature": "new", "handover_status": "none"})
        send_dm(
            sender_id,
            "Hey ðŸ‘‹ Welcome to ClientBoost.\n\n"
            "Weâ€™ll review your Instagram profile and show what may be blocking more trust, enquiries and conversions.\n\n"
            "First, whatâ€™s your business name?",
            message_type="ask_name",
            state="ask_name"
        )
        return

    if text == "growth" and step != "new":
        send_dm(sender_id, "Youâ€™re already inside the audit flow.\n\nPlease answer the current question, or type RESET to start again.", message_type="already_in_flow", state=step)
        return

    # Ask business name.
    if step == "ask_name":
        if len(raw_text) < 2:
            send_dm(sender_id, "Please send a valid business name.", state=step)
            return

        data["name"] = raw_text
        save_state(sender_id, step="ask_type", data=data, extra={"business_name": raw_text})

        send_dm(
            sender_id,
            f"Got it â€” {raw_text} âœ…\n\n"
            "What type of business is it?\n\n"
            "Reply with one option:\n"
            "1. Restaurant\n"
            "2. Cafe\n"
            "3. Salon\n"
            "4. Gym\n"
            "5. Boutique\n"
            "6. Clinic\n"
            "7. Real Estate\n"
            "8. E-commerce\n"
            "9. Personal Brand\n"
            "10. Other",
            message_type="ask_type",
            state="ask_type"
        )
        return

    # Ask business type.
    if step == "ask_type":
        if len(raw_text) < 1:
            send_dm(sender_id, "Please send your business type.", state=step)
            return

        selected_type = parse_business_type(raw_text)

        if selected_type == "Other":
            save_state(sender_id, step="ask_other_type", data=data)
            send_dm(
                sender_id,
                "No problem ðŸ‘\n\n"
                "Please type your exact business type.\n\n"
                "Example: dental clinic, car service, coaching, software company, interior design, etc.",
                message_type="ask_other_type",
                state="ask_other_type"
            )
            return

        data["type"] = selected_type
        save_state(sender_id, step="ask_location", data=data, extra={"business_type": selected_type})
        send_dm(sender_id, f"{selected_type} â€” noted âœ…\n\nWhich city or country do you serve?", message_type="ask_location", state="ask_location")
        return

    # Exact business type after Other.
    if step == "ask_other_type":
        if len(raw_text) < 2:
            send_dm(sender_id, "Please type your exact business type.", state=step)
            return

        if text == "growth":
            send_dm(
                sender_id,
                "Please type your exact business type, not the audit trigger.\n\n"
                "Example: marketing agency, software company, car service, coaching, interior design, etc.",
                message_type="ask_other_type_again",
                state="ask_other_type"
            )
            return

        data["type"] = raw_text
        save_state(sender_id, step="ask_location", data=data, extra={"business_type": raw_text})
        send_dm(sender_id, f"{raw_text} â€” noted âœ…\n\nWhich city or country do you serve?", message_type="ask_location", state="ask_location")
        return

    # Ask location.
    if step == "ask_location":
        if len(raw_text) < 2:
            send_dm(sender_id, "Please send your city or country.", state=step)
            return

        data["location"] = raw_text
        save_state(sender_id, step="ask_screenshot", data=data, extra={"location": raw_text})

        send_dm(
            sender_id,
            f"{raw_text} â€” perfect âœ…\n\n"
            "Now send a screenshot of your Instagram profile.\n\n"
            "Make sure it shows:\n"
            "â€¢ your bio\n"
            "â€¢ story highlights\n"
            "â€¢ follower count\n"
            "â€¢ first few posts\n\n"
            "Once you send it, weâ€™ll review your profile and send your audit.",
            message_type="ask_screenshot",
            state="ask_screenshot"
        )
        return

    # Audit complete: preview path.
    if step == "audit_sent" and intent == "preview":
        save_state(sender_id, step="preview_style", data=data)
        send_dm(sender_id, get_preview_options_message(), message_type="preview_options", state="preview_style")
        return

    if step == "preview_style":
        style_id = parse_preview_style(raw_text)

        if not style_id:
            send_dm(sender_id, "Please choose one option: 1, 2, 3 or 4.", message_type="preview_invalid", state="preview_style")
            return

        create_and_send_profile_preview(sender_id, style_id)
        return

    # After audit: user wants help / asks.
    if step == "audit_sent":
        if intent in ["proceed", "question", "pricing", "full_management", "general"]:
            business_type = data.get("type", "")
            data["conversion_turns"] = 0
            save_state(sender_id, step="lead_goal", data=data, extra={"lead_temperature": "warm"})

            if intent == "pricing":
                send_dm(sender_id, pricing_reply(), message_type="pricing_reply", state="lead_contact")
                save_state(sender_id, step="lead_contact", data=data)
                return

            send_dm(
                sender_id,
                "Good move ðŸ¤\n\n"
                "The priority is not just posting more.\n"
                "The priority is building a profile, content direction and enquiry path that turns visitors into real enquiries.\n\n"
                + get_goal_menu(business_type),
                message_type="lead_goal_menu",
                state="lead_goal"
            )
            return

    # Lead goal selection.
    if step == "lead_goal":
        business_type = data.get("type", "")
        parsed = parse_goal(business_type, raw_text)

        if parsed["type"] == "question":
            send_dm(sender_id, explain_goal_question_message(business_type), message_type="goal_question_explain", state="lead_goal")
            return

        if parsed["type"] == "delay":
            save_state(sender_id, step="audit_sent", data=data, extra={"lead_temperature": "nurture"})
            send_dm(sender_id, "No problem.\n\nYour audit is still useful. When youâ€™re ready, send HELP and weâ€™ll guide you from there.", message_type="lead_delay", state="audit_sent")
            return

        if parsed["type"] == "reject":
            save_state(sender_id, step="audit_sent", data=data, extra={"lead_temperature": "nurture"})
            send_dm(sender_id, "No problem. You can use the audit on your own for now.", message_type="lead_reject", state="audit_sent")
            return

        goal = parsed["goal"] or "Need guidance"
        data["goal"] = goal
        data["conversion_turns"] = 0

        save_state(sender_id, step="lead_contact", data=data, extra={"goal": goal, "service_interest": goal, "lead_temperature": "warm"})
        delayed_send_dm(sender_id, contact_request_message(business_type, goal), delay=0.8, message_type="lead_contact_request", state="lead_contact")
        return

    # Contact collection.
    if step == "lead_contact":
        phone = extract_phone(raw_text)
        email = extract_email(raw_text)

        if phone or email:
            extra = {"lead_temperature": "hot"}

            if phone:
                extra["whatsapp"] = phone
                data["whatsapp"] = phone
            if email:
                extra["email"] = email
                data["email"] = email

            save_state(sender_id, step="lead_timeline", data=data, extra=extra)
            delayed_send_dm(
                sender_id,
                "Got it âœ…\n\n"
                "One last thing so our strategist understands the urgency:\n\n"
                "Are you looking to start immediately, this month, or later?",
                delay=0.8,
                message_type="lead_timeline",
                state="lead_timeline"
            )
            return

        turns = int(data.get("conversion_turns", 0) or 0)
        data["conversion_turns"] = turns + 1
        save_state(sender_id, step="lead_contact", data=data)

        reply = contact_stage_reply(raw_text, data.get("type", ""), data.get("goal", ""), data)
        send_dm(sender_id, reply, message_type="conversion_reply", state="lead_contact")
        return

    # Timeline / final handover.
    if step == "lead_timeline":
        phone = extract_phone(raw_text)
        email = extract_email(raw_text)

        extra = {"timeline": raw_text, "lead_temperature": "hot"}

        if phone:
            extra["whatsapp"] = phone
            data["whatsapp"] = phone
        if email:
            extra["email"] = email
            data["email"] = email

        data["timeline"] = raw_text

        save_state(sender_id, step="human_handover", data=data, extra=extra)
        send_dm(sender_id, final_handover_message(), message_type="final_handover", state="human_handover")
        activate_handover(sender_id, reason="lead_qualified", notes="Lead provided contact/timeline and is ready for human follow-up.")
        return

    # Pending audit.
    if step == "audit_pending":
        send_dm(
            sender_id,
            "Your profile review is still saved ðŸ“Œ\n\nNo need to resend the screenshot. Weâ€™ll send your audit here once it is ready.",
            message_type="audit_pending_status",
            state="audit_pending"
        )
        return

    # Fallback.
    send_dm(sender_id, "Hi ðŸ‘‹ Type GROWTH to get your free Instagram audit from ClientBoost.", message_type="fallback", state=step)

# ============================================================
# BACKGROUND RETRY
# ============================================================

def background_retry_loop():
    time.sleep(15)

    while True:
        try:
            retry_pending_audits()
        except Exception as error:
            print(f"Background retry loop error: {error}")

        time.sleep(RETRY_INTERVAL_SECONDS)


def start_background_retry_once():
    global background_retry_started

    if not ENABLE_BACKGROUND_RETRY:
        return
    if background_retry_started:
        return

    background_retry_started = True
    thread = threading.Thread(target=background_retry_loop)
    thread.daemon = True
    thread.start()

# ============================================================
# WEBHOOK ROUTES
# ============================================================

@app.route("/webhook", methods=["GET"])
def verify_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        print("Webhook verified.")
        return challenge, 200

    return "Forbidden", 403


@app.route("/webhook", methods=["POST"])
def receive_webhook():
    data = request.json or {}
    print("Webhook received:", json.dumps(data)[:1200])

    if data.get("object") == "instagram":
        for entry in data.get("entry", []):
            for event in entry.get("messaging", []):
                sender_id = event.get("sender", {}).get("id")
                recipient_id = event.get("recipient", {}).get("id")
                message_obj = event.get("message", {})

                if not sender_id:
                    continue

                mid = message_obj.get("mid")

                if is_duplicate_message(mid):
                    print(f"Duplicate message ignored: {mid}")
                    continue

                if message_obj.get("is_echo"):
                    if recipient_id and is_recent_bot_echo(recipient_id):
                        print(f"Ignored recent bot echo for {recipient_id}")
                        continue

                    if AUTO_HANDOVER_ON_ECHO and recipient_id:
                        activate_handover(recipient_id, reason="manual_team_reply", notes="Manual reply detected from inbox.")
                        print(f"Manual handover activated from echo for {recipient_id}")

                    continue

                has_text = bool(message_obj.get("text"))
                has_image = any(attachment.get("type") == "image" for attachment in message_obj.get("attachments", []))

                if has_text or has_image:
                    thread = threading.Thread(target=handle_message, args=(sender_id, message_obj))
                    thread.daemon = True
                    thread.start()

    return jsonify({"status": "ok"}), 200


@app.route("/retry-pending-audits", methods=["GET", "POST"])
def retry_pending_audits_route():
    if RETRY_SECRET:
        provided_secret = request.args.get("secret", "")
        if provided_secret != RETRY_SECRET:
            return jsonify({"error": "unauthorized"}), 401

    result = retry_pending_audits()
    return jsonify(result), 200


@app.route("/mockups/<mockup_id>.png", methods=["GET"])
def serve_mockup(mockup_id):
    safe_id = re.sub(r"[^a-zA-Z0-9_\-]", "", mockup_id)
    file_path = MOCKUP_OUTPUT_DIR / f"{safe_id}.png"

    if not file_path.exists():
        abort(404)

    return send_file(file_path, mimetype="image/png", max_age=86400)


@app.route("/", methods=["GET"])
def home():
    start_background_retry_once()
    return "ClientBoost Bot is running", 200

# ============================================================
# STARTUP
# ============================================================

start_background_retry_once()

# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)