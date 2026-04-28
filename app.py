# ============================================================
# ClientBoost.in — Instagram DM Audit + Conversion Engine
# Instagram Audit Bot + Google Sheets CRM + Lead Conversion
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

# New key pools
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
        "gemini-2.5-flash,gemini-2.0-flash"
    ).split(",")
    if model.strip()
]

GEMINI_CONVERSION_MODELS = [
    model.strip()
    for model in os.environ.get(
        "GEMINI_CONVERSION_MODELS",
        "gemini-2.5-flash,gemini-2.0-flash"
    ).split(",")
    if model.strip()
]

RETRY_INTERVAL_SECONDS = int(os.environ.get("RETRY_INTERVAL_SECONDS", "600"))
KEY_COOLDOWN_SECONDS = int(os.environ.get("KEY_COOLDOWN_SECONDS", "900"))
RETRY_SECRET = os.environ.get("RETRY_SECRET", "")

# ============================================================
# GLOBALS
# ============================================================

sheet_lock = threading.Lock()
gemini_lock = threading.Lock()

_gspread_client = None
_workbook = None
_ws_cache = {}

# Used to ignore echo messages created by our own bot replies.
recent_bot_sends = {}

# Key cooldown memory. Sheets will also log API status.
api_cooldowns = {}

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


def get_ws(sheet_name):
    if sheet_name in _ws_cache:
        return _ws_cache[sheet_name]

    workbook = get_workbook()

    if not workbook:
        return None

    try:
        ws = workbook.worksheet(sheet_name)
    except Exception:
        ws = workbook.add_worksheet(title=sheet_name, rows=1000, cols=30)

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


def ensure_all_sheets():
    workbook = _workbook

    if not workbook:
        return

    for sheet_name, headers in SHEET_HEADERS.items():
        try:
            ws = workbook.worksheet(sheet_name)
        except Exception:
            ws = workbook.add_worksheet(title=sheet_name, rows=1000, cols=30)

        ensure_headers(ws, headers)


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


def find_row_by_value(sheet_name, key, value):
    ws = get_ws(sheet_name)

    if not ws:
        return None, None

    try:
        headers = get_headers(ws)

        if key not in headers:
            return ws, None

        col_index = headers.index(key) + 1
        values = ws.col_values(col_index)

        for row_number, cell_value in enumerate(values, start=1):
            if row_number == 1:
                continue

            if str(cell_value).strip() == str(value).strip():
                return ws, row_number

        return ws, None

    except Exception as error:
        print(f"Find row error ({sheet_name}): {error}")
        return ws, None


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


def upsert_record(sheet_name, key, key_value, data):
    ws, row_number = find_row_by_value(sheet_name, key, key_value)

    if row_number:
        return update_row(sheet_name, row_number, data)

    new_data = dict(data)
    new_data[key] = key_value
    return append_record(sheet_name, new_data)


def get_record(sheet_name, key, key_value):
    ws, row_number = find_row_by_value(sheet_name, key, key_value)

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
        "message": message[:45000] if message else "",
        "state": state
    })


# ============================================================
# LEAD + STATE HELPERS
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
            "handover": False
        }

    data = {}

    try:
        data = json.loads(lead.get("data_json", "{}") or "{}")
    except Exception:
        data = {}

    handover_active = str(lead.get("handover_status", "")).lower() == "active"

    handover_row = get_record("Handover_Status", "sender_id", sender_id)

    if handover_row and str(handover_row.get("handover_active", "")).lower() == "true":
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

    save_state(
        sender_id,
        step="new",
        data={},
        extra={
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
            "last_user_message_at": now_iso()
        }
    )

    upsert_record("Handover_Status", "sender_id", sender_id, {
        "handover_active": "false",
        "reason": "reset",
        "activated_at": "",
        "notes": "Reset by user"
    })


def activate_handover(sender_id, reason="human_handover", notes=""):
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

    if not PAGE_ACCESS_TOKEN:
        print("PAGE_ACCESS_TOKEN is missing.")
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


def delayed_send_dm(recipient_id, message, delay=1.2, message_type="bot", state=""):
    time.sleep(delay)
    return send_dm(recipient_id, message, message_type=message_type, state=state)


def is_recent_bot_echo(user_id, seconds=75):
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
# API STATUS + GEMINI HELPERS
# ============================================================

def classify_gemini_error(error):
    error_text = str(error).lower()

    if "429" in error_text or "resource_exhausted" in error_text:
        return "quota"

    if "quota" in error_text or "rate limit" in error_text or "exceeded" in error_text:
        return "quota"

    if "api key" in error_text or "permission" in error_text or "unauthorized" in error_text:
        return "auth"

    if "404" in error_text or "model" in error_text and "not found" in error_text:
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

                try:
                    response_text = response.text
                except Exception as text_error:
                    last_error = str(text_error)
                    upsert_api_status(
                        engine=engine,
                        key_index=key_index,
                        status="empty_response",
                        last_error=last_error
                    )
                    continue

                if response_text and response_text.strip():
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

                last_error = "Empty Gemini response"

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
# AUDIT PROMPT
# ============================================================

def create_audit_prompt(name, business_type, location):
    return f"""
You are the senior Instagram growth auditor for ClientBoost, a global full-service digital growth agency.

You are not a generic AI assistant.
You are acting like an experienced agency strategist reviewing this business for growth, trust, positioning and conversion.

ClientBoost helps businesses grow through:
- Strategy
- SEO
- Ads
- Content
- Social media
- Branding
- Web
- Lead generation
- AI automation
- Conversion systems

Business details:
- Business Name: {name}
- Business Type: {business_type}
- City/Country: {location}

Analyse only what is visible in the screenshot:
- Username
- Name field
- Bio clarity
- Profile picture
- Follower/following count
- Number of posts
- Story highlights
- Highlight names and covers
- Visible post grid
- Visual consistency
- Offer clarity
- CTA clarity
- Trust signals
- Lead flow

Important rules:
- Keep the audit detailed and world-class.
- Make it clean and easy to read in Instagram DM.
- Use professional emojis in headings and key bullets.
- Do not overuse emojis.
- Do not sound childish.
- Do not sound like a PDF report.
- Do not invent fake data.
- Do not claim guaranteed results.
- If something is not visible, say it is not visible.
- Do not recommend “DM GROWTH” or “reply GROWTH” to the business unless it is truly relevant.
- GROWTH is ClientBoost’s audit trigger, not the default CTA for every client.
- Recommend a CTA based on their business type.
- Every recommendation must feel specific to {name}, {business_type}, and {location}.
- Keep paragraphs short.
- Use bullet points.
- Make the user feel that a real strategist understood their profile.

Business-specific CTA guidance:
- Restaurant/Cafe: Order Now, View Menu, WhatsApp to Order, Book Table, DM MENU.
- Salon/Clinic: Book Appointment, Call Now, Get Consultation, DM BOOK.
- Real Estate: Schedule Site Visit, Request Price, Talk to Advisor, DM SITE.
- Gym/Fitness: Claim Trial Session, Book Fitness Consultation, DM TRIAL.
- E-commerce/Boutique: Shop Now, View Collection, Order on WhatsApp, DM CATALOG.
- Agency/Service Business/Personal Brand: Book a Call, Request Quote, Get Consultation, DM AUDIT.
- Other business: choose the most natural CTA based on what they sell.

Write the audit in this exact structure:

━━━━━━━━━━━━━━━━━━━━━━
🎯 CLIENTBOOST INSTAGRAM AUDIT
━━━━━━━━━━━━━━━━━━━━━━

🏢 Business: {name}
📍 Location: {location}
🏷️ Category: {business_type}

Quick verdict:
[Give 2–3 sharp lines summarising the profile’s current strength and biggest growth problem.]

━━━━━━━━━━━━━━━━━━━━━━
📊 1. PROFILE SCORE

Score: X/10

✅ What is working:
- [Specific point from the screenshot]
- [Specific point from the screenshot]

⚠️ What needs fixing:
- [Specific issue from the screenshot]
- [Specific issue from the screenshot]

🔧 Top profile fix:
[One clear action they should do first.]

━━━━━━━━━━━━━━━━━━━━━━
✍️ 2. BIO & POSITIONING FIXES

Current issue:
[Explain what is weak/confusing/missing in the visible bio or name field.]

Recommended bio:
[Write a better Instagram bio for this exact business. Keep it practical and usable.]

Why this works:
- [Reason 1]
- [Reason 2]
- [Reason 3]

CTA recommendation:
[Suggest the best CTA for their business type. Do NOT blindly say DM GROWTH.]

━━━━━━━━━━━━━━━━━━━━━━
📸 3. CONTENT & GRID AUDIT

✅ What is working:
- [Specific visible content strength]
- [Specific visible content strength]

⚠️ What is holding growth back:
- [Specific visible content weakness]
- [Specific visible content weakness]

Content direction:
[Explain what type of content they should post more of.]

3 content ideas for {business_type}:
- [Idea 1 — specific and practical]
- [Idea 2 — specific and practical]
- [Idea 3 — specific and practical]

━━━━━━━━━━━━━━━━━━━━━━
⭐ 4. TRUST & HIGHLIGHT AUDIT

Current issue:
[Analyse highlights, trust signals, reviews, proof, FAQs, results, menu/services, etc.]

Recommended highlights:
- ✅ Services / Menu
- ✅ Reviews / Results
- ✅ How It Works
- ✅ FAQ
- ✅ Contact / Book Now

Best trust fix:
[One practical trust-building action based on their business.]

━━━━━━━━━━━━━━━━━━━━━━
📍 5. LOCAL REACH & DISCOVERY

Current issue:
[Explain if their profile is weak in local search, location clarity, local keywords, or niche positioning.]

Local reach fixes:
- Add location keywords clearly.
- Use area/city terms naturally.
- Post content that proves local presence.
- Use local hashtags only where relevant.

Suggested keywords/hashtags:
[Give specific keyword or hashtag examples based on {business_type} and {location}.]

━━━━━━━━━━━━━━━━━━━━━━
💬 6. LEAD FLOW & CONVERSION

Current issue:
[Explain what stops a visitor from becoming a lead/customer.]

Best CTA for this profile:
[Give a business-specific CTA. Do NOT use DM GROWTH as default.]

Conversion fix:
[Explain how to reduce friction from profile visit to enquiry/order/booking.]

━━━━━━━━━━━━━━━━━━━━━━
🗓️ 7. 7-DAY ACTION PLAN

Day 1:
[Profile/bio fix]

Day 2:
[Highlight/trust fix]

Day 3:
[Content fix]

Day 4:
[Local reach fix]

Day 5:
[Conversion/CTA fix]

Day 6:
[Engagement/story fix]

Day 7:
[Review and improve based on response]

━━━━━━━━━━━━━━━━━━━━━━
🏁 FINAL VERDICT

Overall rating: X/10

[Give one honest final line about where the profile stands today.]

Best next move:
[Give one clear priority action.]

Soft CTA:
If you want ClientBoost to help implement these fixes for your profile, reply HELP and our team will guide you.
"""


def analyse_screenshot_with_gemini(image, name, business_type, location):
    prompt = create_audit_prompt(name, business_type, location)
    return generate_text_with_pool("audit", [prompt, image])


# ============================================================
# MESSAGE SPLITTING
# ============================================================

def split_message(text, limit=900):
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
    intro = (
        "Your free ClientBoost Instagram audit is ready ✅\n\n"
        f"🏢 Business: {name}\n"
        f"🏷️ Type: {business_type}\n"
        f"📍 Location: {location}\n\n"
        "Here is the strategic breakdown:"
    )

    send_dm(sender_id, intro, message_type="audit_intro", state="audit_sent")
    time.sleep(1)

    for part in split_message(audit_text):
        send_dm(sender_id, part, message_type="audit_part", state="audit_sent")
        time.sleep(1.2)

    closing = (
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🚀 Want help implementing this?\n\n"
        "Your audit shows what is blocking better enquiries:\n"
        "profile clarity, content direction, trust signals and lead flow.\n\n"
        "Reply HELP if you want ClientBoost to fix this properly. 🤝"
    )

    send_dm(sender_id, closing, message_type="audit_cta", state="audit_sent")

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
            "We’re analysing your Instagram profile now.\n\n"
            "You’ll get a detailed audit with profile, bio, content, trust, reach and lead-flow fixes.\n\n"
            "This usually takes 20–30 seconds ⏳",
            message_type="audit_processing",
            state="audit_processing"
        )

    image = download_image(image_url)

    if not image:
        if not from_retry:
            send_dm(
                sender_id,
                "I could not download the screenshot properly.\n\n"
                "Please send the screenshot again.",
                message_type="image_error",
                state="ask_screenshot"
            )
        return {
            "status": "image_failed"
        }

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
            "Your profile is in the analysis queue 📌\n\n"
            "We’re reviewing it carefully, so this may take a little longer than usual.\n\n"
            "No need to resend anything. Your audit will be sent here automatically.",
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

        if status not in ["pending"]:
            continue

        last_attempt_at = record.get("last_attempt_at", "")
        last_user_message_at = record.get("last_user_message_at", record.get("created_at", ""))
        warning_sent = str(record.get("warning_23h_sent", "")).lower() == "true"

        if not limit_sender_id and seconds_since(last_attempt_at) < RETRY_INTERVAL_SECONDS:
            continue

        window_age = seconds_since(last_user_message_at)

        # Around 23 hours, ask the user to reply so the conversation can continue.
        if window_age >= 23 * 60 * 60 and not warning_sent:
            send_dm(
                sender_id,
                "This is taking longer than expected.\n\n"
                "Your audit request is still saved.\n"
                "Please reply CONTINUE here so we can keep the audit active and send it as soon as analysis is available.",
                message_type="audit_23h_warning",
                state="audit_pending"
            )

            update_row("Pending_Audits", index, {
                "warning_23h_sent": "true",
                "last_attempt_at": now_iso()
            })

            warnings += 1
            continue

        # After 24 hours with no user reply, wait for the user to message again.
        if window_age >= 24 * 60 * 60:
            update_row("Pending_Audits", index, {
                "status": "waiting_user",
                "last_attempt_at": now_iso()
            })

            waiting += 1
            continue

        retry_count = 0

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


# ============================================================
# BUSINESS CATEGORY + GOALS
# ============================================================

def normalize_business_category(business_type):
    text = str(business_type or "").lower()

    if any(word in text for word in ["restaurant", "cafe", "food", "takeaway", "hotel", "bakery", "cloud kitchen"]):
        return "restaurant"

    if any(word in text for word in ["salon", "beauty", "spa", "makeup", "clinic", "doctor", "dental", "skin", "aesthetic"]):
        return "salon_clinic"

    if any(word in text for word in ["real estate", "property", "realtor", "builder", "developer", "plot", "land"]):
        return "real_estate"

    if any(word in text for word in ["gym", "fitness", "trainer", "yoga", "workout"]):
        return "gym"

    if any(word in text for word in ["ecommerce", "e-commerce", "boutique", "fashion", "clothing", "store", "shop"]):
        return "ecommerce"

    if any(word in text for word in ["agency", "service", "consultant", "coach", "personal brand", "creator", "influencer"]):
        return "service"

    return "other"


def get_goal_menu(business_type):
    category = normalize_business_category(business_type)

    if category == "restaurant":
        return (
            "What do you want most right now?\n\n"
            "1. More orders\n"
            "2. Better food reels/content\n"
            "3. More repeat customers\n"
            "4. WhatsApp ordering system\n"
            "5. Offers and local campaigns\n"
            "6. Full marketing management\n"
            "7. Not sure — need guidance"
        )

    if category == "salon_clinic":
        return (
            "What do you want most right now?\n\n"
            "1. More appointments\n"
            "2. Better trust and reviews\n"
            "3. More local reach\n"
            "4. Better reels/content\n"
            "5. Ads for bookings\n"
            "6. Full page management\n"
            "7. Not sure — need guidance"
        )

    if category == "real_estate":
        return (
            "What do you want most right now?\n\n"
            "1. More property leads\n"
            "2. Better listing content\n"
            "3. More site visit enquiries\n"
            "4. Local authority building\n"
            "5. Ads for buyers/investors\n"
            "6. Full lead generation system\n"
            "7. Not sure — need guidance"
        )

    if category == "gym":
        return (
            "What do you want most right now?\n\n"
            "1. More trial enquiries\n"
            "2. More memberships\n"
            "3. Better transformation content\n"
            "4. Local ads\n"
            "5. WhatsApp/DM follow-up system\n"
            "6. Full Instagram management\n"
            "7. Not sure — need guidance"
        )

    if category == "ecommerce":
        return (
            "What do you want most right now?\n\n"
            "1. More product sales\n"
            "2. Better product content\n"
            "3. More WhatsApp orders\n"
            "4. Ads for conversions\n"
            "5. Influencer/content strategy\n"
            "6. Full growth system\n"
            "7. Not sure — need guidance"
        )

    if category == "service":
        return (
            "What do you want most right now?\n\n"
            "1. More qualified leads\n"
            "2. Better authority content\n"
            "3. More consultation calls\n"
            "4. Ads and funnel system\n"
            "5. Personal brand positioning\n"
            "6. Full growth management\n"
            "7. Not sure — need guidance"
        )

    return (
        "What do you want most right now?\n\n"
        "1. More enquiries\n"
        "2. More sales\n"
        "3. Better content\n"
        "4. Better trust and positioning\n"
        "5. Ads and lead generation\n"
        "6. Full digital growth system\n"
        "7. Not sure — need guidance"
    )


def parse_goal(business_type, text):
    category = normalize_business_category(business_type)
    value = str(text or "").lower().strip().replace(".", "")

    goal_maps = {
        "restaurant": {
            "1": "More orders",
            "2": "Better food reels/content",
            "3": "More repeat customers",
            "4": "WhatsApp ordering system",
            "5": "Offers and local campaigns",
            "6": "Full marketing management",
            "7": "Need guidance"
        },
        "salon_clinic": {
            "1": "More appointments",
            "2": "Better trust and reviews",
            "3": "More local reach",
            "4": "Better reels/content",
            "5": "Ads for bookings",
            "6": "Full page management",
            "7": "Need guidance"
        },
        "real_estate": {
            "1": "More property leads",
            "2": "Better listing content",
            "3": "More site visit enquiries",
            "4": "Local authority building",
            "5": "Ads for buyers/investors",
            "6": "Full lead generation system",
            "7": "Need guidance"
        },
        "gym": {
            "1": "More trial enquiries",
            "2": "More memberships",
            "3": "Better transformation content",
            "4": "Local ads",
            "5": "WhatsApp/DM follow-up system",
            "6": "Full Instagram management",
            "7": "Need guidance"
        },
        "ecommerce": {
            "1": "More product sales",
            "2": "Better product content",
            "3": "More WhatsApp orders",
            "4": "Ads for conversions",
            "5": "Influencer/content strategy",
            "6": "Full growth system",
            "7": "Need guidance"
        },
        "service": {
            "1": "More qualified leads",
            "2": "Better authority content",
            "3": "More consultation calls",
            "4": "Ads and funnel system",
            "5": "Personal brand positioning",
            "6": "Full growth management",
            "7": "Need guidance"
        },
        "other": {
            "1": "More enquiries",
            "2": "More sales",
            "3": "Better content",
            "4": "Better trust and positioning",
            "5": "Ads and lead generation",
            "6": "Full digital growth system",
            "7": "Need guidance"
        }
    }

    selected_map = goal_maps.get(category, goal_maps["other"])

    if value in selected_map:
        return selected_map[value]

    for key, label in selected_map.items():
        if label.lower() in value:
            return label

    return text.strip() if text.strip() else "Need guidance"


def contact_request_message(business_type, goal):
    category = normalize_business_category(business_type)

    if category == "restaurant":
        base = (
            "Understood.\n\n"
            "For that goal, the first fix is not only posting food photos.\n"
            "The profile needs a clear offer, trust proof, menu access and a fast ordering path.\n\n"
            "ClientBoost can help with:\n"
            "• profile positioning\n"
            "• food reels/content direction\n"
            "• local offers\n"
            "• WhatsApp ordering flow\n"
            "• repeat customer strategy"
        )
    elif category == "real_estate":
        base = (
            "Understood.\n\n"
            "For that goal, the first fix is trust and clarity.\n"
            "People will not enquire if the listing, location proof and next step are unclear.\n\n"
            "ClientBoost can help with:\n"
            "• listing content\n"
            "• local authority positioning\n"
            "• site-visit lead flow\n"
            "• ads for buyers/investors\n"
            "• follow-up automation"
        )
    elif category == "salon_clinic":
        base = (
            "Understood.\n\n"
            "For that goal, the first fix is trust, reviews and a simple booking path.\n"
            "People need to feel safe before they book.\n\n"
            "ClientBoost can help with:\n"
            "• profile positioning\n"
            "• trust-building content\n"
            "• reels and offers\n"
            "• appointment enquiry flow\n"
            "• local ads when ready"
        )
    elif category == "gym":
        base = (
            "Understood.\n\n"
            "For that goal, the first fix is proof and consistency.\n"
            "People need to see transformation, coaching quality and a clear trial/joining path.\n\n"
            "ClientBoost can help with:\n"
            "• transformation content\n"
            "• trial enquiry system\n"
            "• local campaigns\n"
            "• follow-up automation\n"
            "• full Instagram management"
        )
    elif category == "ecommerce":
        base = (
            "Understood.\n\n"
            "For that goal, the first fix is product trust and a faster buying path.\n"
            "People should understand what to buy, why to trust it and how to order quickly.\n\n"
            "ClientBoost can help with:\n"
            "• product content\n"
            "• offer strategy\n"
            "• WhatsApp/order flow\n"
            "• ads for conversions\n"
            "• growth system"
        )
    else:
        base = (
            "Understood.\n\n"
            "For that goal, the first fix is profile clarity, trust content and lead flow.\n"
            "Once that foundation is clear, content and ads become much easier to scale.\n\n"
            "ClientBoost can help with:\n"
            "• strategy\n"
            "• content direction\n"
            "• branding\n"
            "• ads\n"
            "• automation\n"
            "• conversion systems"
        )

    return (
        f"{base}\n\n"
        "Where should our strategist contact you?\n"
        "You can send your WhatsApp number, email, or both."
    )


# ============================================================
# CONTACT + INTENT DETECTION
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


def is_interest_message(text):
    value = str(text or "").lower()

    triggers = [
        "help",
        "interested",
        "start",
        "yes",
        "call me",
        "call",
        "price",
        "pricing",
        "how much",
        "what can you do",
        "manage",
        "i need help",
        "need help",
        "let's start",
        "lets start",
        "want to start"
    ]

    return any(trigger in value for trigger in triggers)


def is_stop_message(text):
    value = str(text or "").lower().strip()
    return value in ["stop", "cancel", "unsubscribe", "not interested"]


def is_reset_message(text):
    value = str(text or "").lower().strip()
    return value in ["reset", "restart", "start over", "start again"]


def is_continue_message(text):
    value = str(text or "").lower().strip()
    return value in ["continue", "yes continue", "continue audit", "send audit"]


def is_complex_conversion_message(text):
    value = str(text or "").lower()

    complex_triggers = [
        "how much",
        "price",
        "pricing",
        "cost",
        "budget",
        "no budget",
        "don't have budget",
        "dont have budget",
        "starting",
        "just started",
        "manage everything",
        "what can you do",
        "what will you do",
        "need customers",
        "need clients",
        "ads",
        "guarantee",
        "result",
        "plan",
        "package"
    ]

    return any(trigger in value for trigger in complex_triggers)


# ============================================================
# CONVERSION ENGINE
# ============================================================

def scripted_conversion_reply(text, business_type, goal=""):
    value = str(text or "").lower()

    if "how much" in value or "price" in value or "pricing" in value or "cost" in value:
        return (
            "Pricing depends on what you want handled.\n\n"
            "There is a big difference between:\n"
            "• profile + strategy fix\n"
            "• content management\n"
            "• ads + lead generation\n"
            "• full growth system\n\n"
            "To suggest the right option, tell me what you need most:\n"
            "content, ads, or full management?"
        )

    if "budget" in value and ("no" in value or "low" in value or "less" in value):
        return (
            "That is fine.\n\n"
            "If budget is tight, the wrong move is running ads too early.\n"
            "The right first step is fixing profile clarity, trust content and the enquiry path so your existing traffic converts better.\n\n"
            "Would you prefer a lean starting plan or full monthly management later?"
        )

    if "starting" in value or "just started" in value or "new business" in value:
        return (
            "Good. Starting early is an advantage.\n\n"
            "For a new business, the priority should be:\n"
            "1. clear positioning\n"
            "2. trust-building content\n"
            "3. simple CTA\n"
            "4. consistent posting system\n\n"
            "You do not need complicated ads first.\n"
            "You need a clean foundation.\n\n"
            "Would you like our team to suggest the first 30-day plan?"
        )

    if "manage" in value or "everything" in value:
        return (
            "Yes. That is exactly where ClientBoost fits.\n\n"
            "We can help with strategy, content, branding, ads, automation and conversion systems.\n\n"
            "Before our team takes over, send your WhatsApp number or email so we can understand your business properly and suggest the right scope."
        )

    if "ads" in value:
        return (
            "Ads can work, but only after the foundation is clear.\n\n"
            "If the profile, offer, content and lead flow are weak, ads usually waste money.\n\n"
            "The smart path is:\n"
            "1. fix positioning\n"
            "2. build trust content\n"
            "3. create a clear enquiry path\n"
            "4. then scale with ads\n\n"
            "Send your WhatsApp or email and our strategist will guide you on the right starting point."
        )

    return (
        "ClientBoost helps turn Instagram from a posting page into a growth channel.\n\n"
        "Based on your audit, the first priority is:\n"
        "profile clarity + trust proof + content direction + lead flow.\n\n"
        "After that, we can scale with content, ads and automation.\n\n"
        "What do you want help with first — content, ads, or full management?"
    )


def generate_conversion_reply(sender_id, user_text, business_type, goal, lead_data):
    # Use scripted replies first to protect API credits.
    if not is_complex_conversion_message(user_text):
        return scripted_conversion_reply(user_text, business_type, goal)

    prompt = f"""
You are the ClientBoost senior conversion strategist.

Context:
- The user already received a ClientBoost Instagram audit.
- Business type: {business_type}
- Selected goal: {goal}
- Lead data: {json.dumps(lead_data, ensure_ascii=False)}

User message:
{user_text}

Your job:
Reply like a premium agency strategist.
Keep the reply short enough for Instagram DM.
Use business psychology, authority, clarity and objection handling.
Do not overpromise.
Do not guarantee results.
Do not sound like a chatbot.
Do not repeat the audit.
Do not be pushy.
Do not use fake urgency or fake scarcity.
Do not reveal technical or AI details.
Move the user toward sending WhatsApp/email or choosing a clear next step.

Write only the DM reply.
"""

    result = generate_text_with_pool("conversion", [prompt])

    if result.get("text"):
        return result["text"]

    return scripted_conversion_reply(user_text, business_type, goal)


def final_handover_message():
    return (
        "Got it. ✅\n\n"
        "Your details are noted.\n"
        "A ClientBoost strategist will take over from here.\n\n"
        "You can also send:\n"
        "• your current monthly marketing budget\n"
        "• your target customers\n"
        "• what service you want help with first\n\n"
        "Our team will continue personally from here. 🤝"
    )


# ============================================================
# MESSAGE HANDLER
# ============================================================

def handle_message(sender_id, message_obj):
    sender_id = str(sender_id)

    raw_text = message_obj.get("text", "").strip()
    text = raw_text.lower()

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

    if is_reset_message(text):
        reset_user(sender_id)

        send_dm(
            sender_id,
            "Reset done.\n\n"
            "Type GROWTH whenever you are ready for your free Instagram audit.",
            message_type="reset",
            state="new"
        )
        return

    if is_continue_message(text):
        refresh_pending_audit_window(sender_id)

        send_dm(
            sender_id,
            "Thanks. Your audit request is active again ✅\n\n"
            "No need to resend the screenshot. We’ll send the audit here automatically once analysis is available.",
            message_type="continue_audit",
            state="audit_pending"
        )

        retry_pending_audits(limit_sender_id=sender_id)
        return

    if state.get("handover") is True:
        print(f"Human handover active for {sender_id}. Bot ignored message.")
        return

    if is_stop_message(text):
        activate_handover(sender_id, reason="user_stopped", notes="User asked bot to stop")

        send_dm(
            sender_id,
            "No problem. We will stop the automated messages here.",
            message_type="stop",
            state="stopped"
        )
        return

    # Start lead conversion from audit.
    if step in ["audit_sent", "lead_goal", "lead_contact", "lead_timeline"] and is_interest_message(text):
        if step == "audit_sent":
            business_type = data.get("type", "")
            save_state(sender_id, step="lead_goal", data=data, extra={
                "lead_temperature": "warm"
            })

            delayed_send_dm(
                sender_id,
                "Good move. 🤝\n\n"
                "Based on your audit, the priority is not just posting more.\n"
                "The priority is building a profile, content direction and lead-flow system that turns visitors into enquiries.\n\n"
                + get_goal_menu(business_type),
                delay=1.5,
                message_type="lead_goal_menu",
                state="lead_goal"
            )
            return

    # Image received at correct step.
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

    # Image received early.
    if image_url and step != "ask_screenshot":
        send_dm(
            sender_id,
            "Thanks for the image.\n\n"
            "Type GROWTH first so I can start your free audit properly.",
            message_type="image_early",
            state=step
        )
        return

    # Start audit flow.
    if "growth" in text and step == "new":
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
            "Welcome to ClientBoost.\n\n"
            "We will do a free Instagram audit for your business.\n\n"
            "First, what is your business name?",
            message_type="ask_name",
            state="ask_name"
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
            f"Got it — {raw_text}.\n\n"
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

        normalized = text.replace(".", "").strip()

        type_map = {
            "1": "Restaurant",
            "restaurant": "Restaurant",
            "2": "Cafe",
            "cafe": "Cafe",
            "3": "Salon",
            "salon": "Salon",
            "4": "Gym",
            "gym": "Gym",
            "5": "Boutique",
            "boutique": "Boutique",
            "6": "Clinic",
            "clinic": "Clinic",
            "7": "Real Estate",
            "real estate": "Real Estate",
            "realestate": "Real Estate",
            "8": "E-commerce",
            "ecommerce": "E-commerce",
            "e-commerce": "E-commerce",
            "9": "Personal Brand",
            "personal brand": "Personal Brand",
            "10": "Other",
            "other": "Other"
        }

        selected_type = type_map.get(normalized, raw_text)

        if selected_type == "Other":
            save_state(sender_id, step="ask_other_type", data=data)

            send_dm(
                sender_id,
                "No problem.\n\n"
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
            f"{selected_type} — noted.\n\n"
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
            f"{raw_text} — noted.\n\n"
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
            f"{raw_text} — perfect.\n\n"
            "Now send a screenshot of your Instagram profile.\n\n"
            "Make sure it shows:\n"
            "- Your bio\n"
            "- Story highlights\n"
            "- Follower count\n"
            "- First few posts\n\n"
            "Once you send it, we will analyse your profile and send your audit.",
            message_type="ask_screenshot",
            state="ask_screenshot"
        )
        return

    # Lead goal selection.
    if step == "lead_goal":
        business_type = data.get("type", "")
        goal = parse_goal(business_type, raw_text)

        data["goal"] = goal

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
            delay=1.4,
            message_type="lead_contact_request",
            state="lead_contact"
        )
        return

    # Lead contact collection.
    if step == "lead_contact":
        phone = extract_phone(raw_text)
        email = extract_email(raw_text)

        update_data = {}

        if phone:
            update_data["whatsapp"] = phone
            data["whatsapp"] = phone

        if email:
            update_data["email"] = email
            data["email"] = email

        if phone or email:
            save_state(
                sender_id,
                step="lead_timeline",
                data=data,
                extra={
                    **update_data,
                    "lead_temperature": "hot"
                }
            )

            delayed_send_dm(
                sender_id,
                "Got it ✅\n\n"
                "One last thing so our strategist understands the urgency:\n\n"
                "Are you looking to start immediately, this month, or later?",
                delay=1.2,
                message_type="lead_timeline",
                state="lead_timeline"
            )
            return

        # No contact given — answer intelligently and continue.
        business_type = data.get("type", "")
        goal = data.get("goal", "")
        reply = generate_conversion_reply(sender_id, raw_text, business_type, goal, data)

        send_dm(
            sender_id,
            reply,
            message_type="conversion_reply",
            state="lead_contact"
        )
        return

    # Lead timeline / final handover.
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
            notes="Lead provided goal/contact/timeline and is ready for human follow-up."
        )
        return

    # If audit was sent and user replies with anything meaningful.
    if step == "audit_sent":
        if is_interest_message(text) or is_complex_conversion_message(text):
            business_type = data.get("type", "")

            save_state(
                sender_id,
                step="lead_goal",
                data=data,
                extra={
                    "lead_temperature": "warm"
                }
            )

            delayed_send_dm(
                sender_id,
                "Good move. 🤝\n\n"
                "Based on your audit, the priority is not just posting more.\n"
                "The priority is building a profile, content direction and lead-flow system that turns visitors into enquiries.\n\n"
                + get_goal_menu(business_type),
                delay=1.3,
                message_type="lead_goal_menu",
                state="lead_goal"
            )
            return

    # Pending audit state.
    if step == "audit_pending":
        send_dm(
            sender_id,
            "Your audit request is still saved 📌\n\n"
            "No need to resend the screenshot.\n"
            "We’ll send the audit here automatically once the analysis is available.",
            message_type="audit_pending_status",
            state="audit_pending"
        )
        return

    # Default fallback.
    send_dm(
        sender_id,
        "Hi. Type GROWTH to get your free Instagram audit from ClientBoost.",
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
    data = request.json
    print("Webhook received:", json.dumps(data)[:1000])

    if data.get("object") == "instagram":
        for entry in data.get("entry", []):
            for event in entry.get("messaging", []):
                sender_id = event.get("sender", {}).get("id")
                recipient_id = event.get("recipient", {}).get("id")
                message_obj = event.get("message", {})

                if not sender_id:
                    continue

                # Echo messages can come from:
                # 1. Our bot sending through API
                # 2. Your team manually replying from Instagram inbox
                if message_obj.get("is_echo"):
                    if recipient_id and is_recent_bot_echo(recipient_id):
                        print(f"Ignored recent bot echo for {recipient_id}")
                        continue

                    if recipient_id:
                        activate_handover(
                            recipient_id,
                            reason="manual_team_reply",
                            notes="Human team replied from Instagram inbox."
                        )
                        print(f"Human handover activated from echo for {recipient_id}")

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
    return "ClientBoost Bot is running", 200


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)