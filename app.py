# ============================================================
# ClientBoost.in — Instagram DM Audit + Lead Conversion Engine
# Instagram DM Bot + Google Sheets CRM + Gemini Key Pool
# ============================================================

from flask import Flask, request, jsonify
import requests
import os
import time
import json
import threading
import io
import re
import uuid
from datetime import datetime, timezone, timedelta

from PIL import Image
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

GEMINI_AUDIT_MODELS = [
    model.strip()
    for model in os.environ.get(
        "GEMINI_AUDIT_MODELS",
        "gemini-2.5-flash-lite,gemini-2.0-flash,gemini-2.5-flash"
    ).split(",")
    if model.strip()
]

GEMINI_CONVERSION_MODELS = [
    model.strip()
    for model in os.environ.get(
        "GEMINI_CONVERSION_MODELS",
        "gemini-2.5-flash-lite,gemini-2.0-flash"
    ).split(",")
    if model.strip()
]

RETRY_INTERVAL_SECONDS = int(os.environ.get("RETRY_INTERVAL_SECONDS", "600"))
KEY_COOLDOWN_SECONDS = int(os.environ.get("KEY_COOLDOWN_SECONDS", "900"))
RETRY_SECRET = os.environ.get("RETRY_SECRET", "")
ENABLE_BACKGROUND_RETRY = os.environ.get("ENABLE_BACKGROUND_RETRY", "true").lower() == "true"
AUTO_HANDOVER_ON_ECHO = os.environ.get("AUTO_HANDOVER_ON_ECHO", "false").lower() == "true"

MAX_CONVERSION_TURNS_BEFORE_CONTACT = int(os.environ.get("MAX_CONVERSION_TURNS_BEFORE_CONTACT", "2"))

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
        "handover_status",
        "conversation_state",
        "data_json",
        "audit_id",
        "last_audit_summary",
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
        "status",
        "cooldown_until",
        "last_error",
        "last_used_at"
    ]
}

# ============================================================
# TIME HELPERS
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def now_iso():
    return now_utc().isoformat()


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
    text = text.replace("’", "'")
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

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets"
        ]

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
            ws = workbook.add_worksheet(title=sheet_name, rows=1000, cols=40)

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
        ws = workbook.add_worksheet(title=sheet_name, rows=1000, cols=40)

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
        return {
            "step": "new",
            "data": {},
            "handover": False,
            "lead": {}
        }

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


def save_state(sender_id, step=None, data=None, extra=None):
    sender_id = str(sender_id)

    update_data = {
        "updated_at": now_iso()
    }

    if step is not None:
        update_data["conversation_state"] = step

    if data is not None:
        update_data["data_json"] = json.dumps(data, ensure_ascii=False)

    if extra:
        update_data.update(extra)

    upsert_record("Leads", "sender_id", sender_id, update_data)


def reset_user(sender_id):
    sender_id = str(sender_id)

    clean_data = {}

    save_state(
        sender_id,
        step="new",
        data=clean_data,
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
            "handover_status": "none",
            "audit_id": "",
            "last_audit_summary": "",
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

    save_state(
        sender_id,
        step="human_handover",
        extra={
            "handover_status": "active",
            "lead_temperature": "hot",
            "updated_at": now_iso()
        }
    )


def update_last_user_message(sender_id):
    save_state(sender_id, extra={
        "last_user_message_at": now_iso()
    })

    refresh_pending_audit_window(sender_id)


def update_last_bot_message(sender_id):
    save_state(sender_id, extra={
        "last_bot_message_at": now_iso()
    })


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

    params = {
        "access_token": PAGE_ACCESS_TOKEN
    }

    headers = {
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(
            url,
            json=payload,
            params=params,
            headers=headers,
            timeout=20
        )

        print("DM send status:", response.status_code, response.text[:500])
        response.raise_for_status()

        recent_bot_sends[recipient_id] = time.time()
        update_last_bot_message(recipient_id)
        log_message(recipient_id, "outbound", message_type, message, state)

        return response.json()

    except requests.exceptions.RequestException as error:
        print(f"Failed to send DM to {recipient_id}: {error}")
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
        headers = {
            "Authorization": f"Bearer {PAGE_ACCESS_TOKEN}"
        }

        response = requests.get(
            image_url,
            headers=headers,
            timeout=25
        )

        response.raise_for_status()

        image = Image.open(io.BytesIO(response.content))
        return image

    except Exception as error:
        print(f"Failed to download image: {error}")
        return None


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


def is_key_on_cooldown(engine, key_index):
    cooldown_until = api_cooldowns.get((engine, key_index))

    if not cooldown_until:
        return False

    return now_utc() < cooldown_until


def set_key_cooldown(engine, key_index, error_message=""):
    cooldown_until = now_utc() + timedelta(seconds=KEY_COOLDOWN_SECONDS)
    api_cooldowns[(engine, key_index)] = cooldown_until

    upsert_api_status(
        engine=engine,
        key_index=key_index,
        status="cooldown",
        cooldown_until=cooldown_until.isoformat(),
        last_error=error_message
    )


def upsert_api_status(engine, key_index, status, cooldown_until="", last_error=""):
    records = get_all_records("API_Status")
    row_to_update = None

    for index, record in enumerate(records, start=2):
        if str(record.get("engine")) == str(engine) and str(record.get("key_index")) == str(key_index):
            row_to_update = index
            break

    data = {
        "engine": engine,
        "key_index": key_index,
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
        return {
            "text": None,
            "status": "no_keys",
            "error": "No Gemini API keys configured"
        }

    last_error = ""
    quota_seen = False

    for key_index, api_key in enumerate(keys):
        if is_key_on_cooldown(engine, key_index):
            quota_seen = True
            continue

        for model_name in models:
            try:
                print(f"Trying Gemini {engine} model: {model_name} | key index: {key_index}")

                with gemini_lock:
                    genai.configure(api_key=api_key)
                    model = genai.GenerativeModel(model_name)
                    response = model.generate_content(contents)

                response_text = getattr(response, "text", "") or ""

                if response_text.strip():
                    upsert_api_status(
                        engine=engine,
                        key_index=key_index,
                        status="success",
                        last_error=""
                    )

                    return {
                        "text": response_text.strip(),
                        "status": "success",
                        "error": ""
                    }

                last_error = "Empty response"

                upsert_api_status(
                    engine=engine,
                    key_index=key_index,
                    status="empty_response",
                    last_error=last_error
                )

            except Exception as error:
                error_type = classify_gemini_error(error)
                last_error = str(error)

                print(f"Gemini {engine} failed | key {key_index} | model {model_name}: {error}")

                if error_type == "quota":
                    quota_seen = True
                    set_key_cooldown(engine, key_index, last_error)
                    break

                upsert_api_status(
                    engine=engine,
                    key_index=key_index,
                    status=error_type,
                    last_error=last_error
                )

                continue

    if quota_seen:
        return {
            "text": None,
            "status": "rate_limited",
            "error": last_error
        }

    return {
        "text": None,
        "status": "failed",
        "error": last_error
    }


# ============================================================
# BUSINESS TYPE + CTA HELPERS
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
        "1": "Restaurant",
        "one": "Restaurant",
        "first": "Restaurant",
        "restaurant": "Restaurant",

        "2": "Cafe",
        "two": "Cafe",
        "second": "Cafe",
        "cafe": "Cafe",
        "coffee": "Cafe",

        "3": "Salon",
        "three": "Salon",
        "third": "Salon",
        "salon": "Salon",
        "beauty": "Salon",

        "4": "Gym",
        "four": "Gym",
        "fourth": "Gym",
        "gym": "Gym",
        "fitness": "Gym",

        "5": "Boutique",
        "five": "Boutique",
        "fifth": "Boutique",
        "boutique": "Boutique",
        "fashion": "Boutique",

        "6": "Clinic",
        "six": "Clinic",
        "sixth": "Clinic",
        "clinic": "Clinic",
        "doctor": "Clinic",

        "7": "Real Estate",
        "seven": "Real Estate",
        "seventh": "Real Estate",
        "real estate": "Real Estate",
        "property": "Real Estate",

        "8": "E-commerce",
        "eight": "E-commerce",
        "eighth": "E-commerce",
        "ecommerce": "E-commerce",
        "e-commerce": "E-commerce",
        "online store": "E-commerce",

        "9": "Personal Brand",
        "nine": "Personal Brand",
        "ninth": "Personal Brand",
        "personal brand": "Personal Brand",
        "creator": "Personal Brand",

        "10": "Other",
        "ten": "Other",
        "other": "Other",
        "something else": "Other",
        "different": "Other"
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

    if category == "service":
        return [
            ("1", "More qualified leads", ["leads", "qualified leads", "clients", "customers"]),
            ("2", "Better authority content", ["authority", "content", "trust", "positioning"]),
            ("3", "More consultation calls", ["calls", "consultation", "bookings", "appointments"]),
            ("4", "Ads and funnel system", ["ads", "funnel", "paid", "lead generation"]),
            ("5", "Personal brand positioning", ["brand", "personal brand", "positioning"]),
            ("6", "Full growth management", ["full", "management", "manage", "everything"]),
            ("7", "Need guidance", ["not sure", "unsure", "guide", "guidance", "don't know", "dont know", "confused"])
        ]

    return [
        ("1", "More enquiries", ["enquiries", "leads", "clients", "customers"]),
        ("2", "More sales", ["sales", "orders", "revenue"]),
        ("3", "Better content", ["content", "reels", "posts", "videos"]),
        ("4", "Better trust and positioning", ["trust", "positioning", "brand", "proof"]),
        ("5", "Ads and lead generation", ["ads", "lead generation", "paid"]),
        ("6", "Full digital growth system", ["full", "growth", "management", "everything"]),
        ("7", "Need guidance", ["not sure", "unsure", "guide", "guidance", "don't know", "dont know", "confused"])
    ]


def get_goal_menu(business_type):
    options = get_goal_options(business_type)

    lines = [
        "What do you want most right now? 👇",
        ""
    ]

    for number, label, _keywords in options:
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
        return {
            "type": "question",
            "goal": ""
        }

    if value_clean in ["not sure", "unsure", "idk", "i dont know", "i don't know", "confused", "guide me", "guidance"]:
        return {
            "type": "goal",
            "goal": "Need guidance"
        }

    options = get_goal_options(business_type)

    for number, label, keywords in options:
        if value_clean == number:
            return {
                "type": "goal",
                "goal": label
            }

    number_match = re.search(r"\b([1-7])\b", value_clean)

    if number_match:
        selected_number = number_match.group(1)

        for number, label, _keywords in options:
            if number == selected_number:
                return {
                    "type": "goal",
                    "goal": label
                }

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
        return {
            "type": "goal",
            "goal": best_goal
        }

    intent = classify_user_intent(text)

    if intent == "full_management":
        return {
            "type": "goal",
            "goal": "Full management"
        }

    if intent == "proceed":
        return {
            "type": "goal",
            "goal": "Need guidance"
        }

    if intent == "delay":
        return {
            "type": "delay",
            "goal": ""
        }

    if intent == "reject":
        return {
            "type": "reject",
            "goal": ""
        }

    return {
        "type": "goal",
        "goal": text.strip() if text.strip() else "Need guidance"
    }


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
You are a senior Instagram growth strategist at ClientBoost.

You are reviewing a business Instagram profile from a screenshot.

Business:
Name: {name}
Type: {business_type}
Location: {location}

Analyse only what is visible in the screenshot:
username, name field, bio, profile photo, follower/following count, post count, story highlights, visible grid, visual clarity, trust signals, offer clarity, and enquiry path.

Output rules:
- Write like an experienced human strategist.
- Use simple English.
- Use clean bullet points.
- Use professional emojis in headings.
- Keep it suitable for Instagram DM.
- Maximum 750 words.
- Do not use markdown symbols like **, ##, tables, or code formatting.
- Do not write the words CTA, Soft CTA, Call-to-action label, prompt, AI, Gemini, model, API, automation, code, or system.
- Do not recommend DM GROWTH or reply GROWTH.
- The recommended next step for this business should be natural, such as: {natural_action}
- Do not overpromise or guarantee results.
- Do not invent information that is not visible.
- If something is not visible, say it is not visible.

Structure exactly like this:

🎯 CLIENTBOOST INSTAGRAM AUDIT

🏢 Business: {name}
📍 Location: {location}
🏷️ Category: {business_type}

Quick verdict:
Give 2 short lines about the profile’s biggest strength and biggest growth problem.

📊 1. Profile Score
Score: X/10

Working well:
• specific visible strength
• specific visible strength

Needs fixing:
• specific visible weakness
• specific visible weakness

First fix:
One clear first action.

✍️ 2. Bio & Positioning
Current issue:
Short explanation.

Better bio idea:
Write one improved bio for this exact business.

Why it works:
• reason
• reason
• reason

📸 3. Content & Grid
Working well:
• point
• point

Improve:
• point
• point

Post ideas:
• idea 1
• idea 2
• idea 3

⭐ 4. Trust & Highlights
Current issue:
Short explanation.

Recommended highlights:
• Services/Menu
• Reviews/Results
• How It Works
• FAQ
• Contact/Book Now

Best trust fix:
One practical action.

📍 5. Local Reach
Current issue:
Short explanation.

Fixes:
• location keyword idea
• local content idea
• hashtag/search idea

💬 6. Lead Flow
Current issue:
Explain what may stop people from contacting them.

Best next step:
Give the best natural next step for their business. Do not call it CTA.

🗓️ 7. 7-Day Action Plan
Day 1: one action
Day 2: one action
Day 3: one action
Day 4: one action
Day 5: one action
Day 6: one action
Day 7: one action

🏁 Final verdict
Overall rating: X/10

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
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = cleaned.replace("|", " ")
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
        extra={
            "audit_id": audit_id,
            "lead_temperature": "audit_pending"
        }
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

    save_state(
        sender_id,
        step="audit_sent",
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
        "Your ClientBoost audit is ready ✅\n\n"
        f"🏢 Business: {name}\n"
        f"🏷️ Type: {business_type}\n"
        f"📍 Location: {location}\n\n"
        "Here’s the clear breakdown:"
    )

    send_dm(sender_id, intro, message_type="audit_intro", state="audit_sent")
    time.sleep(0.8)

    for part in split_message(audit_text):
        send_dm(sender_id, part, message_type="audit_part", state="audit_sent")
        time.sleep(0.9)

    final_message = (
        "If you want ClientBoost to help fix the profile, content direction, trust signals and enquiry flow, send HELP.\n\n"
        "Our team will guide you from there. 🤝"
    )

    send_dm(sender_id, final_message, message_type="audit_next_step", state="audit_sent")

    save_audit_history(
        audit_id=audit_id,
        sender_id=sender_id,
        name=name,
        business_type=business_type,
        location=location,
        audit_text=audit_text
    )


def process_audit_request(sender_id, data, image_url, first_time=True, from_retry=False):
    sender_id = str(sender_id)

    if first_time:
        send_dm(
            sender_id,
            "Screenshot received 📸\n\n"
            "We’re reviewing your Instagram profile now.\n\n"
            "You’ll get a clear audit covering profile clarity, content, trust, local reach and enquiry flow.\n\n"
            "This usually takes a short moment ⏳",
            message_type="audit_processing",
            state="audit_processing"
        )

    image = download_image(image_url)

    if not image:
        if not from_retry:
            send_dm(
                sender_id,
                "The screenshot did not load properly.\n\n"
                "Please send it once more and we’ll continue.",
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

        return {
            "status": "sent",
            "audit_id": audit_id
        }

    audit_id = enqueue_pending_audit(
        sender_id=sender_id,
        data=data,
        image_url=image_url,
        error_type=result.get("status", "failed")
    )

    if not from_retry:
        send_dm(
            sender_id,
            "Your profile review is saved 📌\n\n"
            "It is taking a little longer than usual, but no need to resend anything.\n\n"
            "Your audit will be sent here automatically once it is ready.",
            message_type="audit_queued",
            state="audit_pending"
        )

    return {
        "status": "queued",
        "audit_id": audit_id
    }


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

            update_row("Pending_Audits", index, {
                "warning_23h_sent": "true",
                "last_attempt_at": now_iso()
            })

            warnings += 1
            continue

        if window_age >= 24 * 60 * 60:
            update_row("Pending_Audits", index, {
                "status": "waiting_user",
                "last_attempt_at": now_iso()
            })

            waiting += 1
            continue

        try:
            retry_count = int(record.get("retry_count", 0))
        except Exception:
            retry_count = 0

        update_row("Pending_Audits", index, {
            "retry_count": retry_count + 1,
            "last_attempt_at": now_iso()
        })

        data = {
            "name": record.get("business_name", ""),
            "type": record.get("business_type", ""),
            "location": record.get("location", ""),
            "audit_id": record.get("audit_id", "")
        }

        result = process_audit_request(
            sender_id=sender_id,
            data=data,
            image_url=record.get("image_url", ""),
            first_time=False,
            from_retry=True
        )

        processed += 1

        if result.get("status") == "sent":
            update_row("Pending_Audits", index, {
                "status": "sent",
                "audit_sent_at": now_iso()
            })
            sent += 1

        time.sleep(1)

    return {
        "processed": processed,
        "sent": sent,
        "warnings": warnings,
        "waiting_user": waiting
    }


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
# CONVERSION MESSAGES
# ============================================================

def explain_goal_question_message(business_type):
    return (
        "No issue 👍\n\n"
        "This question helps us understand what kind of help you need first, so we don’t suggest random services.\n\n"
        "You can reply with a number, or just type it in words.\n\n"
        "Example: content, ads, orders, leads, bookings, or full management."
    )


def contact_request_message(business_type, goal):
    category = normalize_business_category(business_type)

    if goal.lower() in ["need guidance", "guidance"]:
        return (
            "That is completely fine 👍\n\n"
            "If you’re not sure, the right first step is to review your profile, content, trust signals and enquiry path together.\n\n"
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
            f"Understood — {goal}. ✅\n\n"
            "For food businesses, the priority is not just posting food photos.\n"
            "The profile needs a clear offer, trust proof, menu access and a fast ordering path.\n\n"
            "Where should our strategist contact you?\n"
            "Send your WhatsApp number, email, or both."
        )

    if category == "real_estate":
        return (
            f"Understood — {goal}. ✅\n\n"
            "For real estate, trust and clarity matter first.\n"
            "People enquire when the property, location proof and next step are clear.\n\n"
            "Where should our strategist contact you?\n"
            "Send your WhatsApp number, email, or both."
        )

    if category == "salon_clinic":
        return (
            f"Understood — {goal}. ✅\n\n"
            "For appointment-based businesses, trust and a simple booking path matter most.\n"
            "People need confidence before they book.\n\n"
            "Where should our strategist contact you?\n"
            "Send your WhatsApp number, email, or both."
        )

    if category == "gym":
        return (
            f"Understood — {goal}. ✅\n\n"
            "For gyms and fitness brands, proof, consistency and a clear trial/joining path are the first growth levers.\n\n"
            "Where should our strategist contact you?\n"
            "Send your WhatsApp number, email, or both."
        )

    if category == "ecommerce":
        return (
            f"Understood — {goal}. ✅\n\n"
            "For product brands, people need to quickly understand what to buy, why to trust it, and how to order.\n\n"
            "Where should our strategist contact you?\n"
            "Send your WhatsApp number, email, or both."
        )

    return (
        f"Understood — {goal}. ✅\n\n"
        "The right first step is to fix profile clarity, trust content and enquiry flow before scaling with content or ads.\n\n"
        "Where should our strategist contact you?\n"
        "Send your WhatsApp number, email, or both."
    )


def pricing_reply():
    return (
        "Pricing depends on what you want handled.\n\n"
        "There is a difference between:\n"
        "• profile and strategy fix\n"
        "• content management\n"
        "• ads and lead generation\n"
        "• full growth management\n\n"
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
                "Simple answer: ClientBoost helps fix the parts that turn profile visitors into real enquiries — profile clarity, content direction, trust proof and lead flow.\n\n"
                "To guide you properly, send your WhatsApp number or email and our strategist will continue from there."
            )

        return (
            "Good question.\n\n"
            "We are asking for contact because your audit shows several areas that need proper context before suggesting a service: profile clarity, content direction, trust proof and enquiry flow.\n\n"
            "A strategist can understand your business better and suggest the right starting point.\n\n"
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
            "Your audit is still useful. When you’re ready, send your WhatsApp number or email and our team will guide you from there."
        )

    if intent == "reject":
        return (
            "No problem. Your audit is yours to use.\n\n"
            "You can come back anytime if you want help implementing it."
        )

    if turns >= MAX_CONVERSION_TURNS_BEFORE_CONTACT:
        return (
            "I’ll keep it simple.\n\n"
            "If you want ClientBoost to help, send your WhatsApp number or email and our strategist will continue personally."
        )

    return (
        "ClientBoost can help with strategy, content direction, branding, ads, automation and conversion systems.\n\n"
        "The best starting point depends on your business stage and goal.\n\n"
        "Send your WhatsApp number or email and our strategist will guide you properly."
    )


def final_handover_message():
    return (
        "Perfect — noted ✅\n\n"
        "A ClientBoost strategist will take over from here.\n\n"
        "You can also send:\n"
        "• your current monthly marketing budget\n"
        "• your target customers\n"
        "• what you want help with first\n\n"
        "Our team will continue personally from here. 🤝"
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

        send_dm(
            sender_id,
            "Reset done ✅\n\n"
            "Type GROWTH whenever you’re ready for your free Instagram audit.",
            message_type="reset",
            state="new"
        )
        return

    if intent == "continue":
        refresh_pending_audit_window(sender_id)

        send_dm(
            sender_id,
            "Thanks ✅\n\n"
            "Your audit request is active again. No need to resend the screenshot.",
            message_type="continue_audit",
            state="audit_pending"
        )

        retry_pending_audits(limit_sender_id=sender_id)
        return

    if state.get("handover") is True:
        print(f"Human handover active for {sender_id}. Bot ignored message.")
        return

    if intent == "reject" and step in ["lead_goal", "lead_contact", "lead_timeline", "audit_sent"]:
        save_state(sender_id, step="audit_sent", data=data, extra={"lead_temperature": "nurture"})

        send_dm(
            sender_id,
            "No problem. Your audit is yours to use.\n\n"
            "You can come back anytime if you want help implementing it.",
            message_type="not_interested",
            state="audit_sent"
        )
        return

    # Image at correct stage.
    if image_url and step == "ask_screenshot":
        data["image_url"] = image_url

        process_audit_request(
            sender_id=sender_id,
            data=data,
            image_url=image_url,
            first_time=True,
            from_retry=False
        )
        return

    # Image too early.
    if image_url and step != "ask_screenshot":
        send_dm(
            sender_id,
            "Thanks for the image 📸\n\n"
            "Type GROWTH first so we can start your free audit properly.",
            message_type="image_early",
            state=step
        )
        return

    # Start audit flow.
    if "growth" == text and step == "new":
        save_state(
            sender_id,
            step="ask_name",
            data={},
            extra={
                "lead_temperature": "new",
                "handover_status": "none"
            }
        )

        send_dm(
            sender_id,
            "Hey 👋 Welcome to ClientBoost.\n\n"
            "We’ll review your Instagram profile and show what may be blocking more trust, enquiries and conversions.\n\n"
            "First, what’s your business name?",
            message_type="ask_name",
            state="ask_name"
        )
        return

    if "growth" == text and step != "new":
        send_dm(
            sender_id,
            "You’re already inside the audit flow.\n\n"
            "Please answer the current question, or type RESET to start again.",
            message_type="already_in_flow",
            state=step
        )
        return

    # Ask business name.
    if step == "ask_name":
        if len(raw_text) < 2:
            send_dm(sender_id, "Please send a valid business name.", state=step)
            return

        data["name"] = raw_text

        save_state(
            sender_id,
            step="ask_type",
            data=data,
            extra={
                "business_name": raw_text
            }
        )

        send_dm(
            sender_id,
            f"Got it — {raw_text} ✅\n\n"
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
                "No problem 👍\n\n"
                "Please type your exact business type.\n\n"
                "Example: dental clinic, car service, coaching, software company, interior design, etc.",
                message_type="ask_other_type",
                state="ask_other_type"
            )
            return

        data["type"] = selected_type

        save_state(
            sender_id,
            step="ask_location",
            data=data,
            extra={
                "business_type": selected_type
            }
        )

        send_dm(
            sender_id,
            f"{selected_type} — noted ✅\n\n"
            "Which city or country do you serve?",
            message_type="ask_location",
            state="ask_location"
        )
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

        save_state(
            sender_id,
            step="ask_location",
            data=data,
            extra={
                "business_type": raw_text
            }
        )

        send_dm(
            sender_id,
            f"{raw_text} — noted ✅\n\n"
            "Which city or country do you serve?",
            message_type="ask_location",
            state="ask_location"
        )
        return

    # Ask location.
    if step == "ask_location":
        if len(raw_text) < 2:
            send_dm(sender_id, "Please send your city or country.", state=step)
            return

        data["location"] = raw_text

        save_state(
            sender_id,
            step="ask_screenshot",
            data=data,
            extra={
                "location": raw_text
            }
        )

        send_dm(
            sender_id,
            f"{raw_text} — perfect ✅\n\n"
            "Now send a screenshot of your Instagram profile.\n\n"
            "Make sure it shows:\n"
            "• your bio\n"
            "• story highlights\n"
            "• follower count\n"
            "• first few posts\n\n"
            "Once you send it, we’ll review your profile and send your audit.",
            message_type="ask_screenshot",
            state="ask_screenshot"
        )
        return

    # After audit: user wants help / asks.
    if step == "audit_sent":
        if intent in ["proceed", "question", "pricing", "full_management", "general"]:
            business_type = data.get("type", "")

            data["conversion_turns"] = 0

            save_state(
                sender_id,
                step="lead_goal",
                data=data,
                extra={
                    "lead_temperature": "warm"
                }
            )

            if intent == "pricing":
                send_dm(
                    sender_id,
                    pricing_reply(),
                    message_type="pricing_reply",
                    state="lead_contact"
                )
                save_state(sender_id, step="lead_contact", data=data)
                return

            send_dm(
                sender_id,
                "Good move 🤝\n\n"
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
            send_dm(
                sender_id,
                explain_goal_question_message(business_type),
                message_type="goal_question_explain",
                state="lead_goal"
            )
            return

        if parsed["type"] == "delay":
            save_state(sender_id, step="audit_sent", data=data, extra={"lead_temperature": "nurture"})

            send_dm(
                sender_id,
                "No problem.\n\n"
                "Your audit is still useful. When you’re ready, send HELP and we’ll guide you from there.",
                message_type="lead_delay",
                state="audit_sent"
            )
            return

        if parsed["type"] == "reject":
            save_state(sender_id, step="audit_sent", data=data, extra={"lead_temperature": "nurture"})

            send_dm(
                sender_id,
                "No problem. You can use the audit on your own for now.",
                message_type="lead_reject",
                state="audit_sent"
            )
            return

        goal = parsed["goal"] or "Need guidance"
        data["goal"] = goal
        data["conversion_turns"] = 0

        save_state(
            sender_id,
            step="lead_contact",
            data=data,
            extra={
                "goal": goal,
                "service_interest": goal,
                "lead_temperature": "warm"
            }
        )

        delayed_send_dm(
            sender_id,
            contact_request_message(business_type, goal),
            delay=0.8,
            message_type="lead_contact_request",
            state="lead_contact"
        )
        return

    # Contact collection.
    if step == "lead_contact":
        phone = extract_phone(raw_text)
        email = extract_email(raw_text)

        if phone or email:
            extra = {
                "lead_temperature": "hot"
            }

            if phone:
                extra["whatsapp"] = phone
                data["whatsapp"] = phone

            if email:
                extra["email"] = email
                data["email"] = email

            save_state(
                sender_id,
                step="lead_timeline",
                data=data,
                extra=extra
            )

            delayed_send_dm(
                sender_id,
                "Got it ✅\n\n"
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

        reply = contact_stage_reply(
            user_text=raw_text,
            business_type=data.get("type", ""),
            goal=data.get("goal", ""),
            data=data
        )

        send_dm(
            sender_id,
            reply,
            message_type="conversion_reply",
            state="lead_contact"
        )
        return

    # Timeline / final handover.
    if step == "lead_timeline":
        phone = extract_phone(raw_text)
        email = extract_email(raw_text)

        extra = {
            "timeline": raw_text,
            "lead_temperature": "hot"
        }

        if phone:
            extra["whatsapp"] = phone
            data["whatsapp"] = phone

        if email:
            extra["email"] = email
            data["email"] = email

        data["timeline"] = raw_text

        save_state(
            sender_id,
            step="human_handover",
            data=data,
            extra=extra
        )

        send_dm(
            sender_id,
            final_handover_message(),
            message_type="final_handover",
            state="human_handover"
        )

        activate_handover(
            sender_id,
            reason="lead_qualified",
            notes="Lead provided contact/timeline and is ready for human follow-up."
        )
        return

    # Pending audit.
    if step == "audit_pending":
        send_dm(
            sender_id,
            "Your profile review is still saved 📌\n\n"
            "No need to resend the screenshot. We’ll send your audit here once it is ready.",
            message_type="audit_pending_status",
            state="audit_pending"
        )
        return

    # Fallback.
    send_dm(
        sender_id,
        "Hi 👋 Type GROWTH to get your free Instagram audit from ClientBoost.",
        message_type="fallback",
        state=step
    )


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

                # Echo messages are our own outgoing messages or manual inbox replies.
                # Default: ignore echo. Do not activate handover automatically.
                if message_obj.get("is_echo"):
                    if recipient_id and is_recent_bot_echo(recipient_id):
                        print(f"Ignored recent bot echo for {recipient_id}")
                        continue

                    if AUTO_HANDOVER_ON_ECHO and recipient_id:
                        activate_handover(
                            recipient_id,
                            reason="manual_team_reply",
                            notes="Manual reply detected from inbox."
                        )
                        print(f"Manual handover activated from echo for {recipient_id}")

                    continue

                has_text = bool(message_obj.get("text"))
                has_image = any(
                    attachment.get("type") == "image"
                    for attachment in message_obj.get("attachments", [])
                )

                if has_text or has_image:
                    thread = threading.Thread(
                        target=handle_message,
                        args=(sender_id, message_obj)
                    )
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