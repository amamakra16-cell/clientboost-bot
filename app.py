# ============================================================
# ClientBoost — Instagram Growth Audit + Preview + Lead Funnel
# Single-file Render deployment version
# ============================================================
# Core stack: Flask + Meta Instagram Messaging API + Gemini + Google Sheets + Pillow
# ============================================================

import os
import re
import io
import json
import time
import uuid
import base64
import textwrap
import threading
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests
from flask import Flask, request, jsonify, send_from_directory
from PIL import Image, ImageDraw, ImageFont, ImageFilter

try:
    import google.generativeai as genai
except Exception:
    genai = None

try:
    import gspread
    from google.oauth2.service_account import Credentials
except Exception:
    gspread = None
    Credentials = None

app = Flask(__name__)

# ============================================================
# ENVIRONMENT
# ============================================================
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "clientboost2024")
PAGE_ACCESS_TOKEN = os.environ.get("PAGE_ACCESS_TOKEN", "")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")

OWNER_RESET_CODE = os.environ.get("OWNER_RESET_CODE", "CBRESET2026")
FOLLOW_REQUIRED = os.environ.get("FOLLOW_REQUIRED", "true").lower() == "true"
FOLLOW_ACCOUNT_USERNAME = os.environ.get("FOLLOW_ACCOUNT_USERNAME", "clientboost.in")
FOLLOW_VERIFY_MODE = os.environ.get("FOLLOW_VERIFY_MODE", "strict").lower()
FOLLOW_CACHE_HOURS = int(os.environ.get("FOLLOW_CACHE_HOURS", "24"))

AUDIT_COOLDOWN_DAYS = int(os.environ.get("AUDIT_COOLDOWN_DAYS", "7"))
SESSION_EXPIRY_DAYS = int(os.environ.get("SESSION_EXPIRY_DAYS", "3"))

GEMINI_AUDIT_API_KEYS = [k.strip() for k in os.environ.get("GEMINI_AUDIT_API_KEYS", os.environ.get("GEMINI_API_KEY", "")).split(",") if k.strip()]
GEMINI_CONVERSION_API_KEYS = [k.strip() for k in os.environ.get("GEMINI_CONVERSION_API_KEYS", ",".join(GEMINI_AUDIT_API_KEYS)).split(",") if k.strip()]
GEMINI_AUDIT_MODELS = [m.strip() for m in os.environ.get(
    "GEMINI_AUDIT_MODELS",
    "gemini-2.5-pro,gemini-2.5-flash,gemini-2.5-flash-lite,gemini-2.0-flash"
).split(",") if m.strip()]
GEMINI_CONVERSION_MODELS = [m.strip() for m in os.environ.get(
    "GEMINI_CONVERSION_MODELS",
    "gemini-2.5-flash-lite,gemini-2.5-flash,gemini-2.5-pro,gemini-2.0-flash"
).split(",") if m.strip()]

GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID", "")
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")

GRAPH_API_VERSION = os.environ.get("GRAPH_API_VERSION", "v21.0")
PREVIEW_DIR = "/tmp/clientboost_previews"
os.makedirs(PREVIEW_DIR, exist_ok=True)

# ============================================================
# CONSTANTS
# ============================================================
STATE_TAB = "User_State"
LEADS_TAB = "Final_Leads"

USER_STATE_HEADERS = [
    "sender_id", "instagram_username", "step", "profile_name", "profile_type_raw",
    "profile_category", "target_location_or_audience", "latest_audit", "audit_score",
    "main_gap", "profile_gap", "content_gap", "trust_gap", "cta_gap", "top_fix",
    "preview_requested", "preview_generated", "preview_style", "lead_temperature",
    "objection_type", "phone_number", "handover", "cooldown_until", "follow_verified",
    "follow_verified_at", "last_active", "final_saved", "state_json"
]

FINAL_LEADS_HEADERS = [
    "timestamp", "sender_id", "instagram_username", "profile_name", "profile_type_raw",
    "profile_category", "target_location_or_audience", "audit_score", "main_gap",
    "top_fix", "preview_requested", "preview_generated", "preview_style",
    "lead_temperature", "objection_type", "phone_number", "handover_status",
    "recommended_offer", "session_status", "latest_user_message"
]

DIRECT_COMMANDS = {
    "growth": "start_audit",
    "start": "start_audit",
    "audit": "start_audit",
    "i followed": "follow_confirmed",
    "followed": "follow_confirmed",
    "done": "generic_done",
    "why follow": "ask_why_follow",
    "cancel": "cancel",
    "preview": "request_preview",
    "see preview": "request_preview",
    "help": "request_help",
    "interested": "request_help",
    "yes": "yes",
    "latest": "request_latest",
    "show audit": "request_latest",
    "new audit": "request_new_audit",
    "reset": "soft_reset",
    "restart": "soft_reset",
}

PROFILE_CATEGORIES: Dict[str, Dict[str, Any]] = {
    "food_restaurant": {
        "keywords": ["restaurant", "cafe", "food", "bakery", "cloud kitchen", "takeaway", "delivery", "burger", "pizza", "juice", "hotel", "nosh", "biryani", "kitchen"],
        "style": "Warm Commercial",
        "highlights": ["Menu", "Reviews", "Kitchen", "Offers", "Order", "Location"],
        "grid": ["Food", "Review", "Offer", "Kitchen", "Bestseller", "Combo", "Order", "Behind", "CTA"],
        "angle": "orders, trust, craving, and WhatsApp conversion",
    },
    "beauty_salon": {
        "keywords": ["salon", "beauty", "makeup", "bridal", "nails", "spa", "boutique", "fashion", "hair", "skincare"],
        "style": "Warm Commercial",
        "highlights": ["Services", "Results", "Pricing", "Reviews", "Bridal", "Booking"],
        "grid": ["Result", "Service", "Review", "Offer", "Process", "Before", "After", "FAQ", "Booking"],
        "angle": "bookings through transformation proof and trust",
    },
    "fitness_wellness": {
        "keywords": ["gym", "fitness", "yoga", "trainer", "workout", "zumba", "crossfit", "nutrition", "wellness"],
        "style": "Modern Content Grid",
        "highlights": ["Programs", "Results", "Trainers", "Reviews", "Plans", "Join"],
        "grid": ["Workout", "Result", "Trainer", "Tip", "Plan", "Review", "Class", "Diet", "Join"],
        "angle": "membership enquiries, transformation proof, and authority",
    },
    "clinic_healthcare": {
        "keywords": ["clinic", "dental", "doctor", "hospital", "skin", "physio", "physiotherapy", "ayurveda", "health", "medical"],
        "style": "Premium Structured",
        "highlights": ["Treatments", "Doctor", "Results", "Reviews", "FAQ", "Book"],
        "grid": ["Care", "Treatment", "Review", "FAQ", "Doctor", "Tip", "Trust", "Result", "Book"],
        "angle": "patient trust, clarity, and appointment conversion",
    },
    "real_estate": {
        "keywords": ["real estate", "property", "plots", "land", "apartment", "villa", "realtor", "broker", "site", "flat"],
        "style": "Premium Structured",
        "highlights": ["Listings", "Site Visits", "Documents", "Reviews", "Areas", "Contact"],
        "grid": ["Property", "Area", "Docs", "Visit", "Trust", "Review", "Price", "Guide", "CTA"],
        "angle": "buyer trust, documentation clarity, and site visit leads",
    },
    "ecommerce_retail": {
        "keywords": ["store", "shop", "ecommerce", "products", "clothing", "jewellery", "accessories", "electronics", "retail", "brand"],
        "style": "Warm Commercial",
        "highlights": ["Products", "Offers", "Reviews", "New", "Delivery", "Order"],
        "grid": ["Product", "Review", "Offer", "New", "Detail", "Use", "Proof", "Pack", "Order"],
        "angle": "product trust, repeat buying, and DM/WhatsApp orders",
    },
    "automotive_service": {
        "keywords": ["car", "bike", "automobile", "detailing", "garage", "mechanic", "service center", "wash", "vehicle"],
        "style": "Warm Commercial",
        "highlights": ["Services", "Before", "After", "Reviews", "Pricing", "Book"],
        "grid": ["Service", "Before", "After", "Review", "Tip", "Process", "Offer", "Trust", "Book"],
        "angle": "service bookings through proof and transformation",
    },
    "education_coaching": {
        "keywords": ["coaching", "classes", "school", "tuition", "academy", "course", "training", "institute", "teacher", "education"],
        "style": "Modern Content Grid",
        "highlights": ["Courses", "Results", "Students", "Reviews", "FAQ", "Enroll"],
        "grid": ["Lesson", "Result", "Tip", "Student", "Proof", "FAQ", "Review", "Value", "Enroll"],
        "angle": "admissions, trust, results, and student confidence",
    },
    "professional_service": {
        "keywords": ["lawyer", "accountant", "architect", "interior", "consultant", "software", "finance", "insurance", "ca", "advocate"],
        "style": "Premium Structured",
        "highlights": ["Services", "Work", "Results", "Reviews", "FAQ", "Contact"],
        "grid": ["Service", "Case", "Proof", "Tip", "Review", "Process", "FAQ", "Result", "CTA"],
        "angle": "authority, credibility, and consultation enquiries",
    },
    "agency_service": {
        "keywords": ["agency", "marketing", "social media", "ads", "seo", "branding", "automation", "digital"],
        "style": "Premium Structured",
        "highlights": ["Services", "Results", "Proof", "Process", "FAQ", "Contact"],
        "grid": ["Offer", "Result", "Proof", "Tip", "Process", "Case", "CTA", "Trust", "Lead"],
        "angle": "high-trust lead generation and authority",
    },
    "local_service": {
        "keywords": ["plumber", "electrician", "cleaning", "repair", "pest control", "home service", "technician", "local service"],
        "style": "Warm Commercial",
        "highlights": ["Services", "Before", "After", "Reviews", "Pricing", "Call"],
        "grid": ["Service", "Before", "After", "Review", "Tip", "Problem", "Fix", "Offer", "Call"],
        "angle": "local calls, urgency, trust, and service bookings",
    },
    "hospitality_travel": {
        "keywords": ["hotel", "resort", "travel", "tour", "homestay", "airbnb", "stay", "trip"],
        "style": "Warm Commercial",
        "highlights": ["Rooms", "Reviews", "Location", "Offers", "Food", "Book"],
        "grid": ["Stay", "Room", "Review", "View", "Offer", "Food", "Guide", "Proof", "Book"],
        "angle": "booking confidence, location appeal, and guest trust",
    },
    "event_wedding": {
        "keywords": ["event", "wedding", "planner", "decor", "catering", "dj", "venue", "birthday"],
        "style": "Warm Commercial",
        "highlights": ["Work", "Packages", "Reviews", "Decor", "Events", "Book"],
        "grid": ["Event", "Decor", "Review", "Package", "Before", "After", "Story", "Proof", "Book"],
        "angle": "event enquiries through visual proof and trust",
    },
    "home_lifestyle_business": {
        "keywords": ["furniture", "decor", "home", "interior decor", "kitchenware", "lifestyle store"],
        "style": "Warm Commercial",
        "highlights": ["Products", "Rooms", "Reviews", "New", "Delivery", "Order"],
        "grid": ["Product", "Room", "Review", "New", "Detail", "Use", "Proof", "Style", "Order"],
        "angle": "lifestyle trust, product desire, and order enquiries",
    },
    "influencer_creator": {
        "keywords": ["influencer", "creator", "blogger", "reels", "lifestyle", "vlogger", "fashion creator", "content creator"],
        "style": "Modern Content Grid",
        "highlights": ["About", "Reels", "Collabs", "Media Kit", "Results", "Contact"],
        "grid": ["Hook", "Story", "Value", "Lifestyle", "Reel", "Collab", "Insight", "Proof", "CTA"],
        "angle": "identity, audience retention, and collaboration enquiries",
    },
    "personal_brand": {
        "keywords": ["coach", "mentor", "speaker", "founder", "personal brand", "consultant", "public figure"],
        "style": "Modern Content Grid",
        "highlights": ["About", "Work", "Results", "Content", "Reviews", "Contact"],
        "grid": ["Insight", "Story", "Proof", "Tip", "Value", "Offer", "Result", "Trust", "CTA"],
        "angle": "authority, trust, and inbound enquiries",
    },
    "religious_education_content": {
        "keywords": ["islamic", "quran", "dua", "duas", "hadith", "religious", "reminder", "spiritual", "deen", "islam"],
        "style": "Editorial Creator",
        "highlights": ["Reels", "Quran", "Duas", "Lessons", "About", "Contact"],
        "grid": ["Reminder", "Hadith", "Dua", "Reel", "Quote", "Lesson", "Carousel", "Story", "Follow"],
        "angle": "saves, shares, trust, and clearer educational identity",
    },
    "entertainment_meme_page": {
        "keywords": ["meme", "memes", "entertainment", "funny", "comedy", "viral", "fan page", "theme page"],
        "style": "Editorial Creator",
        "highlights": ["Best", "Reels", "Series", "About", "Viral", "Contact"],
        "grid": ["Meme", "Reel", "Trend", "Relatable", "Series", "Story", "Hook", "CTA", "Follow"],
        "angle": "shareability, repeat formats, and audience memory",
    },
    "artist_portfolio": {
        "keywords": ["artist", "art", "portfolio", "painting", "illustration", "designer", "creative", "craft"],
        "style": "Editorial Creator",
        "highlights": ["Work", "BTS", "Process", "Reviews", "Shop", "Contact"],
        "grid": ["Work", "Story", "Detail", "BTS", "Process", "Style", "Review", "Series", "CTA"],
        "angle": "portfolio trust, visual identity, and commissions",
    },
    "photographer_videographer": {
        "keywords": ["photographer", "videographer", "photo", "video", "cinematographer", "shoot", "wedding film"],
        "style": "Editorial Creator",
        "highlights": ["Work", "Weddings", "Reels", "Reviews", "Packages", "Contact"],
        "grid": ["Shoot", "Story", "Detail", "BTS", "Reel", "Work", "Review", "Process", "CTA"],
        "angle": "portfolio clarity, booking confidence, and premium enquiry flow",
    },
    "fashion_lifestyle_creator": {
        "keywords": ["fashion", "lifestyle", "model", "outfit", "ootd", "style", "beauty influencer"],
        "style": "Editorial Creator",
        "highlights": ["Looks", "Reels", "Collabs", "Brands", "About", "Contact"],
        "grid": ["Look", "Story", "Style", "Reel", "Trend", "Collab", "Tip", "Proof", "CTA"],
        "angle": "aesthetic identity, audience retention, and brand collaborations",
    },
    "knowledge_creator": {
        "keywords": ["knowledge", "facts", "finance tips", "business tips", "learning", "educational content", "explain"],
        "style": "Modern Content Grid",
        "highlights": ["Topics", "Best", "Series", "Resources", "About", "Contact"],
        "grid": ["Hook", "Explain", "Tip", "Carousel", "Myth", "Value", "Series", "Proof", "Follow"],
        "angle": "saves, shares, clarity, and repeatable learning series",
    },
    "motivation_quotes_page": {
        "keywords": ["motivation", "quotes", "quote page", "success", "mindset", "inspiration"],
        "style": "Editorial Creator",
        "highlights": ["Quotes", "Reels", "Stories", "Series", "About", "Contact"],
        "grid": ["Quote", "Reel", "Story", "Lesson", "Reminder", "Carousel", "Series", "Value", "Follow"],
        "angle": "shareable identity, memorable content series, and follow conversion",
    },
    "community_page": {
        "keywords": ["community", "city page", "local page", "belagavi page", "updates", "events", "public"],
        "style": "Editorial Creator",
        "highlights": ["Updates", "Events", "People", "Places", "About", "Contact"],
        "grid": ["Update", "Event", "Place", "Story", "People", "Guide", "News", "Feature", "Follow"],
        "angle": "local trust, community engagement, and repeat visits",
    },
    "nonprofit_social_page": {
        "keywords": ["ngo", "nonprofit", "charity", "foundation", "social work", "cause"],
        "style": "Editorial Creator",
        "highlights": ["Mission", "Work", "Impact", "People", "Donate", "Contact"],
        "grid": ["Cause", "Story", "Impact", "People", "Proof", "Need", "Update", "Trust", "CTA"],
        "angle": "trust, mission clarity, and supporter action",
    },
    "news_media_local": {
        "keywords": ["news", "media", "updates", "journal", "daily update", "local news"],
        "style": "Editorial Creator",
        "highlights": ["News", "Local", "Events", "Videos", "About", "Contact"],
        "grid": ["News", "Update", "Explainer", "Video", "Local", "Alert", "Story", "Trust", "Follow"],
        "angle": "credibility, clarity, and repeat audience behavior",
    },
    "default_general_profile": {
        "keywords": [],
        "style": "Premium Structured",
        "highlights": ["About", "Work", "Proof", "FAQ", "Contact"],
        "grid": ["Intro", "Value", "Proof", "Tip", "Story", "Offer", "FAQ", "Result", "CTA"],
        "angle": "clearer positioning, trust, and profile conversion",
    },
}

# ============================================================
# UTILS
# ============================================================
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def parse_iso(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None

def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())

def safe_json_loads(text: str, default: Any = None) -> Any:
    if default is None:
        default = {}
    try:
        return json.loads(text or "")
    except Exception:
        return default

def extract_json(text: str, default: Any = None) -> Any:
    if default is None:
        default = {}
    if not text:
        return default
    cleaned = text.strip()
    cleaned = cleaned.replace("```json", "```").replace("```JSON", "```")
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    m = re.search(r"\{.*\}", cleaned, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return default
    return default

def split_text(text: str, limit: int = 900) -> List[str]:
    text = (text or "").strip()
    if len(text) <= limit:
        return [text] if text else []
    parts: List[str] = []
    current = ""
    for para in text.split("\n"):
        if len(current) + len(para) + 1 <= limit:
            current += para + "\n"
        else:
            if current.strip():
                parts.append(current.strip())
            if len(para) <= limit:
                current = para + "\n"
            else:
                for chunk in textwrap.wrap(para, width=limit):
                    parts.append(chunk)
                current = ""
    if current.strip():
        parts.append(current.strip())
    return parts

def phone_from_text(text: str) -> Optional[str]:
    digits = re.sub(r"\D", "", text or "")
    if len(digits) >= 10:
        return digits[-10:] if len(digits) <= 12 else digits
    return None

def calculate_recommended_offer(state: Dict[str, Any]) -> str:
    category = state.get("profile_category", "default_general_profile")
    score = int(float(state.get("audit_score") or 0)) if str(state.get("audit_score", "")).replace(".", "", 1).isdigit() else 0
    if state.get("lead_temperature") in ["very_hot", "handover"]:
        if category in ["food_restaurant", "beauty_salon", "real_estate", "clinic_healthcare", "professional_service"]:
            return "Growth Setup + Lead System"
        return "Growth Setup"
    if score and score < 22:
        return "Basic Fix"
    return "Profile Growth Setup"

# ============================================================
# GOOGLE SHEETS STORE
# ============================================================
class SheetStore:
    def __init__(self):
        self.client = None
        self.sheet = None
        self.state_ws = None
        self.leads_ws = None
        self.enabled = False
        self._row_cache: Dict[str, int] = {}
        self._init()

    def _init(self):
        if not (gspread and Credentials and GOOGLE_SHEET_ID and GOOGLE_SERVICE_ACCOUNT_JSON):
            print("Sheets disabled: missing package or environment.")
            return
        try:
            info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
            scopes = ["https://www.googleapis.com/auth/spreadsheets"]
            creds = Credentials.from_service_account_info(info, scopes=scopes)
            self.client = gspread.authorize(creds)
            self.sheet = self.client.open_by_key(GOOGLE_SHEET_ID)
            self.state_ws = self._get_or_create_ws(STATE_TAB, USER_STATE_HEADERS)
            self.leads_ws = self._get_or_create_ws(LEADS_TAB, FINAL_LEADS_HEADERS)
            self.enabled = True
            print("Sheets enabled.")
        except Exception as e:
            print(f"Sheets init failed: {e}")

    def _get_or_create_ws(self, title: str, headers: List[str]):
        try:
            ws = self.sheet.worksheet(title)
        except Exception:
            ws = self.sheet.add_worksheet(title=title, rows=1000, cols=max(20, len(headers) + 2))
        try:
            existing = ws.row_values(1)
            if existing != headers:
                ws.resize(rows=max(ws.row_count, 1000), cols=max(len(headers), ws.col_count))
                ws.update("A1", [headers])
        except Exception as e:
            print(f"Header update failed for {title}: {e}")
        return ws

    def _blank_state(self, sender_id: str) -> Dict[str, Any]:
        return {
            "sender_id": sender_id,
            "instagram_username": "",
            "step": "new",
            "profile_name": "",
            "profile_type_raw": "",
            "profile_category": "",
            "target_location_or_audience": "",
            "latest_audit": "",
            "audit_score": "",
            "main_gap": "",
            "profile_gap": "",
            "content_gap": "",
            "trust_gap": "",
            "cta_gap": "",
            "top_fix": "",
            "preview_requested": "false",
            "preview_generated": "false",
            "preview_style": "",
            "lead_temperature": "cold",
            "objection_type": "none",
            "phone_number": "",
            "handover": "false",
            "cooldown_until": "",
            "follow_verified": "false",
            "follow_verified_at": "",
            "last_active": now_iso(),
            "final_saved": "false",
            "state_json": "{}",
        }

    def get_state(self, sender_id: str) -> Dict[str, Any]:
        if not self.enabled:
            return MEMORY_STORE.get(sender_id, self._blank_state(sender_id))
        try:
            row_num = self._row_cache.get(sender_id)
            if not row_num:
                cell = self.state_ws.find(sender_id, in_column=1)
                row_num = cell.row if cell else None
                if row_num:
                    self._row_cache[sender_id] = row_num
            if not row_num:
                state = self._blank_state(sender_id)
                self.save_state(sender_id, state)
                return state
            row = self.state_ws.row_values(row_num)
            data = dict(zip(USER_STATE_HEADERS, row + [""] * (len(USER_STATE_HEADERS) - len(row))))
            extra = safe_json_loads(data.get("state_json", "{}"), {})
            data.update(extra)
            return data
        except Exception as e:
            print(f"get_state failed: {e}")
            return MEMORY_STORE.get(sender_id, self._blank_state(sender_id))

    def save_state(self, sender_id: str, state: Dict[str, Any]):
        state["sender_id"] = sender_id
        state["last_active"] = now_iso()
        # keep extra fields in state_json
        row_obj = {h: str(state.get(h, "")) for h in USER_STATE_HEADERS}
        extra = {k: v for k, v in state.items() if k not in USER_STATE_HEADERS}
        row_obj["state_json"] = json.dumps(extra, ensure_ascii=False)
        if not self.enabled:
            MEMORY_STORE[sender_id] = state
            return
        try:
            row_num = self._row_cache.get(sender_id)
            if not row_num:
                cell = self.state_ws.find(sender_id, in_column=1)
                row_num = cell.row if cell else None
            values = [[row_obj.get(h, "") for h in USER_STATE_HEADERS]]
            if row_num:
                self._row_cache[sender_id] = row_num
                self.state_ws.update(f"A{row_num}", values)
            else:
                self.state_ws.append_row(values[0], value_input_option="RAW")
                self._row_cache[sender_id] = len(self.state_ws.col_values(1))
        except Exception as e:
            print(f"save_state failed: {e}")
            MEMORY_STORE[sender_id] = state

    def reset_state(self, sender_id: str):
        state = self._blank_state(sender_id)
        self.save_state(sender_id, state)
        return state

    def save_final_lead_once(self, sender_id: str, latest_user_message: str = "") -> bool:
        state = self.get_state(sender_id)
        if str(state.get("final_saved", "false")).lower() == "true":
            return False
        state["final_saved"] = "true"
        state["handover"] = "true"
        state["lead_temperature"] = "handover"
        self.save_state(sender_id, state)
        row = {
            "timestamp": now_iso(),
            "sender_id": sender_id,
            "instagram_username": state.get("instagram_username", ""),
            "profile_name": state.get("profile_name", ""),
            "profile_type_raw": state.get("profile_type_raw", ""),
            "profile_category": state.get("profile_category", ""),
            "target_location_or_audience": state.get("target_location_or_audience", ""),
            "audit_score": state.get("audit_score", ""),
            "main_gap": state.get("main_gap", ""),
            "top_fix": state.get("top_fix", ""),
            "preview_requested": state.get("preview_requested", "false"),
            "preview_generated": state.get("preview_generated", "false"),
            "preview_style": state.get("preview_style", ""),
            "lead_temperature": "handover",
            "objection_type": state.get("objection_type", "none"),
            "phone_number": state.get("phone_number", ""),
            "handover_status": "handover_started",
            "recommended_offer": calculate_recommended_offer(state),
            "session_status": "final_saved",
            "latest_user_message": latest_user_message,
        }
        if not self.enabled:
            FINAL_LEADS_MEMORY.append(row)
            return True
        try:
            self.leads_ws.append_row([row.get(h, "") for h in FINAL_LEADS_HEADERS], value_input_option="RAW")
            return True
        except Exception as e:
            print(f"save_final_lead failed: {e}")
            FINAL_LEADS_MEMORY.append(row)
            return True

MEMORY_STORE: Dict[str, Dict[str, Any]] = {}
FINAL_LEADS_MEMORY: List[Dict[str, Any]] = []
STORE = SheetStore()

# ============================================================
# META MESSAGING
# ============================================================
def send_dm(recipient_id: str, text: str, quick_replies: Optional[List[str]] = None) -> Dict[str, Any]:
    if not PAGE_ACCESS_TOKEN:
        print("PAGE_ACCESS_TOKEN missing. DM not sent.")
        return {"error": "missing token"}
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/me/messages"
    message: Dict[str, Any] = {"text": text[:1900]}
    if quick_replies:
        message["quick_replies"] = [
            {"content_type": "text", "title": title[:20], "payload": title.upper().replace(" ", "_")}
            for title in quick_replies[:13]
        ]
    payload = {
        "recipient": {"id": recipient_id},
        "message": message,
        "messaging_type": "RESPONSE",
    }
    try:
        r = requests.post(url, params={"access_token": PAGE_ACCESS_TOKEN}, json=payload, timeout=15)
        if r.status_code >= 300:
            print("send_dm error", r.status_code, r.text)
        return r.json() if r.text else {}
    except Exception as e:
        print(f"send_dm failed: {e}")
        return {"error": str(e)}

def send_image(recipient_id: str, image_url: str) -> Dict[str, Any]:
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/me/messages"
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"attachment": {"type": "image", "payload": {"url": image_url}}},
        "messaging_type": "RESPONSE",
    }
    try:
        r = requests.post(url, params={"access_token": PAGE_ACCESS_TOKEN}, json=payload, timeout=15)
        if r.status_code >= 300:
            print("send_image error", r.status_code, r.text)
        return r.json() if r.text else {}
    except Exception as e:
        print(f"send_image failed: {e}")
        return {"error": str(e)}

def download_image(image_url: str) -> Optional[Image.Image]:
    try:
        headers = {"Authorization": f"Bearer {PAGE_ACCESS_TOKEN}"} if PAGE_ACCESS_TOKEN else {}
        r = requests.get(image_url, headers=headers, timeout=25)
        r.raise_for_status()
        return Image.open(io.BytesIO(r.content)).convert("RGB")
    except Exception as e:
        print(f"download_image failed: {e}")
        return None

def get_instagram_profile(sender_id: str) -> Dict[str, Any]:
    if not PAGE_ACCESS_TOKEN:
        return {}
    fields = "username,profile_pic,is_user_follow_business,is_business_follow_user"
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{sender_id}"
    try:
        r = requests.get(url, params={"fields": fields, "access_token": PAGE_ACCESS_TOKEN}, timeout=15)
        if r.status_code >= 300:
            print("profile check error", r.status_code, r.text)
            return {}
        return r.json()
    except Exception as e:
        print(f"get_instagram_profile failed: {e}")
        return {}

def is_follow_verified(sender_id: str, state: Dict[str, Any], force: bool = False) -> bool:
    if not FOLLOW_REQUIRED:
        return True
    if not force and str(state.get("follow_verified", "false")).lower() == "true":
        ts = parse_iso(state.get("follow_verified_at", ""))
        if ts and datetime.now(timezone.utc) - ts < timedelta(hours=FOLLOW_CACHE_HOURS):
            return True
    profile = get_instagram_profile(sender_id)
    username = profile.get("username", "")
    if username:
        state["instagram_username"] = username
    follows = profile.get("is_user_follow_business") is True
    if follows:
        state["follow_verified"] = "true"
        state["follow_verified_at"] = now_iso()
        STORE.save_state(sender_id, state)
        return True
    state["follow_verified"] = "false"
    STORE.save_state(sender_id, state)
    return False

# ============================================================
# GEMINI
# ============================================================
def gemini_generate(prompt: str, image: Optional[Image.Image] = None, purpose: str = "conversion") -> Optional[str]:
    if genai is None:
        print("google-generativeai not installed")
        return None
    keys = GEMINI_AUDIT_API_KEYS if purpose == "audit" else GEMINI_CONVERSION_API_KEYS
    models = GEMINI_AUDIT_MODELS if purpose == "audit" else GEMINI_CONVERSION_MODELS
    if not keys:
        print(f"No Gemini keys for {purpose}")
        return None
    last_error = None
    for model_name in models:
        for key in keys:
            try:
                genai.configure(api_key=key)
                model = genai.GenerativeModel(model_name)
                content = [prompt, image] if image is not None else prompt
                response = model.generate_content(content)
                text = getattr(response, "text", None)
                if text:
                    print(f"Gemini success: {purpose} {model_name}")
                    return text.strip()
            except Exception as e:
                last_error = e
                print(f"Gemini failed {purpose} model={model_name}: {e}")
                continue
    print(f"Gemini all failed: {last_error}")
    return None

# ============================================================
# CATEGORY + INTENT
# ============================================================
def detect_category(text: str) -> str:
    t = normalize(text)
    for category, cfg in PROFILE_CATEGORIES.items():
        for kw in cfg.get("keywords", []):
            if kw in t:
                return category
    return "default_general_profile"

def direct_intent(text: str) -> Optional[str]:
    t = normalize(text)
    if not t:
        return None
    if phone_from_text(t):
        return "phone_number_sent"
    if OWNER_RESET_CODE and text.strip() == OWNER_RESET_CODE:
        return "owner_reset"
    for key, intent in DIRECT_COMMANDS.items():
        if t == key or (len(key) >= 5 and key in t):
            return intent
    if any(x in t for x in ["price", "cost", "how much", "charges", "package", "rate", "fees"]):
        return "price_question"
    if any(x in t for x in ["details", "send package", "tell me more", "services", "what do you offer"]):
        return "asks_details"
    if any(x in t for x in ["designer", "editor", "social media person", "agency already", "team already"]):
        return "already_has_designer"
    if any(x in t for x in ["i will think", "let me think", "maybe", "later"]):
        return "thinking_delay"
    if any(x in t for x in ["not now", "busy", "after month", "next month"]):
        return "not_now"
    if any(x in t for x in ["can i do", "do myself", "i can manage", "diy"]):
        return "wants_to_do_self"
    if any(x in t for x in ["budget", "expensive", "costly", "cheap", "no money"]):
        return "budget_low"
    if any(x in t for x in ["is this real", "scam", "proof", "who are you", "can i trust"]):
        return "trust_issue"
    if any(x in t for x in ["result", "guarantee", "followers", "leads", "how fast"]):
        return "asks_results"
    return None

def ai_understand_message(state: Dict[str, Any], user_text: str) -> Dict[str, Any]:
    prompt = f"""
You are the intent understanding layer for ClientBoost's Instagram audit bot.
Return ONLY valid JSON. No markdown.

Current state:
step: {state.get('step')}
profile_name: {state.get('profile_name')}
profile_type_raw: {state.get('profile_type_raw')}
profile_category: {state.get('profile_category')}
audit_sent: {bool(state.get('latest_audit'))}
preview_generated: {state.get('preview_generated')}
handover: {state.get('handover')}

User message:
{user_text}

Classify intent as one of:
start_audit, follow_confirmed, ask_why_follow, cancel,
answer_profile_name, answer_profile_type, answer_location_or_audience,
request_preview, request_help, request_latest, request_new_audit,
price_question, asks_details, trust_issue, thinking_delay, not_now,
already_has_designer, wants_to_do_self, budget_low, asks_results,
phone_number_sent, handover_question, general_question, unclear.

Also infer profile_category if the message describes a profile/niche.
Allowed categories:
{', '.join(PROFILE_CATEGORIES.keys())}

JSON schema:
{{
  "intent": "...",
  "confidence": 0.0,
  "profile_category": "...",
  "clean_answer": "...",
  "objection_type": "none|price_question|asks_details|trust_issue|thinking_delay|not_now|already_has_designer|wants_to_do_self|budget_low|asks_results",
  "next_action": "..."
}}
""".strip()
    text = gemini_generate(prompt, purpose="conversion")
    data = extract_json(text or "", {})
    if not data:
        return {"intent": "unclear", "confidence": 0.0, "profile_category": "", "clean_answer": user_text, "objection_type": "none"}
    return data

# ============================================================
# AUDIT + CONVERSION PROMPTS
# ============================================================
def audit_profile(image: Image.Image, state: Dict[str, Any]) -> Dict[str, Any]:
    category = state.get("profile_category") or detect_category(state.get("profile_type_raw", ""))
    cfg = PROFILE_CATEGORIES.get(category, PROFILE_CATEGORIES["default_general_profile"])
    prompt = f"""
You are a senior Instagram growth strategist for ClientBoost.
Audit this Instagram profile screenshot with agency-level precision.

User details:
Profile/Page name: {state.get('profile_name')}
Profile type/niche: {state.get('profile_type_raw')}
Internal category: {category}
Target location/audience: {state.get('target_location_or_audience')}
Likely conversion angle: {cfg.get('angle')}

Analyze visible screenshot details:
- username/name field
- bio clarity
- profile picture
- follower/following count if visible
- post count if visible
- highlights
- visible grid posts/reels covers
- proof/trust signals
- CTA/contact path
- content consistency

Return ONLY valid JSON. No markdown.

Schema:
{{
  "audit_text": "premium short audit in clear sections, under 1800 words",
  "audit_score": 0,
  "main_gap": "one clear main gap",
  "profile_gap": "bio/positioning/profile issue",
  "content_gap": "content/grid/reel issue",
  "trust_gap": "proof/authority issue",
  "cta_gap": "conversion/contact issue",
  "top_fix": "one priority fix",
  "recommended_preview_style": "{cfg.get('style')}"
}}

Audit format inside audit_text:
CLIENTBOOST PROFILE AUDIT

Profile: ...
Type: ...
Main Issue: ...
Score: XX/40

1. Profile Positioning
Score: X/10
Issue: ...
Fix: ...

2. Content Direction
Score: X/10
Issue: ...
Fix: ...

3. Trust & Authority
Score: X/10
Issue: ...
Fix: ...

4. Conversion Path
Score: X/10
Issue: ...
Fix: ...

Priority Fix:
...

Next:
PREVIEW — see the improved direction
HELP — let ClientBoost fix it
LATEST — view this audit again

Rules:
- Be specific to the screenshot.
- Use short lines.
- No fake claims.
- No big paragraphs.
- Do not promise followers, sales, or viral results.
- If information is not visible, say "not clearly visible".
""".strip()
    text = gemini_generate(prompt, image=image, purpose="audit")
    data = extract_json(text or "", {})
    if not data:
        # safe fallback if JSON fails
        data = {
            "audit_text": text or "I could not complete the audit properly. Please resend a clearer screenshot.",
            "audit_score": 0,
            "main_gap": "unclear profile structure",
            "profile_gap": "bio and positioning need clarity",
            "content_gap": "content pillars are not clear",
            "trust_gap": "trust proof is not strong enough",
            "cta_gap": "CTA/contact path needs clarity",
            "top_fix": "rebuild bio, highlights, and first 9-post structure",
            "recommended_preview_style": cfg.get("style"),
        }
    return data

def build_conversion_reply(state: Dict[str, Any], user_text: str, objection_type: str) -> str:
    category = state.get("profile_category") or "default_general_profile"
    cfg = PROFILE_CATEGORIES.get(category, PROFILE_CATEGORIES["default_general_profile"])
    prompt = f"""
You are the ClientBoost conversion strategist.
Write a short premium Instagram DM reply that converts a warm lead to WhatsApp handover.

User message:
{user_text}

Objection/intent:
{objection_type}

Profile data:
Profile name: {state.get('profile_name')}
Profile type: {state.get('profile_type_raw')}
Category: {category}
Target/audience: {state.get('target_location_or_audience')}
Audit score: {state.get('audit_score')}
Main gap: {state.get('main_gap')}
Profile gap: {state.get('profile_gap')}
Content gap: {state.get('content_gap')}
Trust gap: {state.get('trust_gap')}
CTA gap: {state.get('cta_gap')}
Top fix: {state.get('top_fix')}
Preview style: {state.get('preview_style') or cfg.get('style')}
Preview highlights: {', '.join(cfg.get('highlights', []))}
Preview grid direction: {', '.join(cfg.get('grid', []))}

Goal:
- Use the exact audit gap.
- Show why that gap matters for this profile type.
- Connect preview direction as the solution if preview exists.
- Position ClientBoost as strategy + execution, not just design.
- Ask for WhatsApp number only if user intent is warm/hot.

Rules:
- No fake guarantees.
- No fake scarcity.
- No fake testimonials.
- No pressure.
- Short premium message.
- Use line breaks.
- End with a clear next step.
""".strip()
    text = gemini_generate(prompt, purpose="conversion")
    if text:
        return text.strip()[:1600]
    # deterministic fallback
    main_gap = state.get("main_gap") or "profile structure is not clear enough"
    return (
        "Fair.\n\n"
        f"Your audit shows the main gap: {main_gap}.\n\n"
        "That gap can reduce trust, enquiries, followers, orders, or collaborations depending on your profile.\n\n"
        "ClientBoost can help fix the structure, content direction, trust signals, and CTA flow.\n\n"
        "Send your WhatsApp number.\nOur strategist will review your audit and guide the next step."
    )

# ============================================================
# PREVIEW IMAGE GENERATOR
# ============================================================
def load_font(size: int, bold: bool = False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()

def draw_wrapped(draw: ImageDraw.ImageDraw, text: str, xy: Tuple[int, int], font, fill, max_width: int, line_gap: int = 8, max_lines: int = 4) -> int:
    words = (text or "").split()
    lines, current = [], ""
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
    lines = lines[:max_lines]
    x, y = xy
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += font.size + line_gap
    return y

def rounded_rect(draw, box, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)

def style_palette(style: str) -> Dict[str, Tuple[int, int, int]]:
    if style == "Warm Commercial":
        return {"bg": (252, 245, 235), "card": (255, 255, 255), "dark": (34, 24, 18), "muted": (112, 86, 65), "accent": (181, 92, 44), "soft": (241, 222, 202)}
    if style == "Modern Content Grid":
        return {"bg": (18, 20, 24), "card": (30, 34, 42), "dark": (250, 250, 250), "muted": (190, 195, 205), "accent": (118, 160, 255), "soft": (46, 53, 68)}
    if style == "Editorial Creator":
        return {"bg": (241, 238, 232), "card": (255, 255, 252), "dark": (30, 30, 30), "muted": (100, 96, 90), "accent": (97, 86, 70), "soft": (225, 219, 210)}
    return {"bg": (246, 247, 249), "card": (255, 255, 255), "dark": (18, 23, 31), "muted": (100, 108, 120), "accent": (16, 88, 210), "soft": (228, 235, 248)}

def generate_preview_image(state: Dict[str, Any]) -> str:
    category = state.get("profile_category") or "default_general_profile"
    cfg = PROFILE_CATEGORIES.get(category, PROFILE_CATEGORIES["default_general_profile"])
    style = state.get("preview_style") or cfg.get("style", "Premium Structured")
    pal = style_palette(style)

    W, H = 1080, 1350
    img = Image.new("RGB", (W, H), pal["bg"])
    draw = ImageDraw.Draw(img)

    font_huge = load_font(54, True)
    font_title = load_font(38, True)
    font_sub = load_font(27, True)
    font_body = load_font(25, False)
    font_small = load_font(20, False)
    font_tiny = load_font(17, False)

    margin = 64

    # background accents
    draw.ellipse((-140, -180, 340, 280), fill=pal["soft"])
    draw.ellipse((830, 1120, 1240, 1520), fill=pal["soft"])

    # Header
    draw.text((margin, 48), "CLIENTBOOST", font=font_sub, fill=pal["accent"])
    draw.text((margin, 88), "7-Day Instagram Direction", font=font_huge, fill=pal["dark"])
    profile_name = state.get("profile_name") or "Your Profile"
    cat_label = (state.get("profile_type_raw") or category.replace("_", " ")).title()
    draw.text((margin, 158), profile_name[:34], font=font_title, fill=pal["dark"])
    draw.text((margin, 205), cat_label[:55], font=font_body, fill=pal["muted"])

    # Audit gap card
    card_y = 255
    rounded_rect(draw, (margin, card_y, W-margin, card_y+170), 32, pal["card"])
    draw.text((margin+32, card_y+26), "Audit Gap", font=font_sub, fill=pal["accent"])
    gap = state.get("main_gap") or state.get("top_fix") or "Profile needs clearer positioning, content structure, and CTA."
    draw_wrapped(draw, gap, (margin+32, card_y+70), font_body, pal["dark"], W - 2*margin - 64, max_lines=3)

    # Bio direction card
    bio_y = 455
    rounded_rect(draw, (margin, bio_y, W-margin, bio_y+155), 32, pal["card"])
    draw.text((margin+32, bio_y+24), "Bio Direction", font=font_sub, fill=pal["accent"])
    bio_text = build_bio_direction(state, cfg)
    draw_wrapped(draw, bio_text, (margin+32, bio_y+66), font_small, pal["dark"], W - 2*margin - 64, max_lines=3)

    # Highlights row
    hi_y = 640
    draw.text((margin, hi_y), "Highlights", font=font_sub, fill=pal["dark"])
    highlights = cfg.get("highlights", [])[:6]
    circle_y = hi_y + 62
    spacing = (W - 2*margin) // 6
    for i, h in enumerate(highlights):
        cx = margin + spacing * i + spacing//2
        draw.ellipse((cx-34, circle_y-34, cx+34, circle_y+34), fill=pal["card"], outline=pal["accent"], width=3)
        label = h[:10]
        bbox = draw.textbbox((0, 0), label, font=font_tiny)
        draw.text((cx-(bbox[2]-bbox[0])/2, circle_y+48), label, font=font_tiny, fill=pal["muted"])

    # Grid preview
    grid_y = 790
    draw.text((margin, grid_y-45), "First 9-Post Structure", font=font_sub, fill=pal["dark"])
    grid_labels = cfg.get("grid", [])[:9]
    tile_gap = 18
    tile_size = (W - 2*margin - 2*tile_gap) // 3
    for idx, label in enumerate(grid_labels):
        row, col = divmod(idx, 3)
        x = margin + col * (tile_size + tile_gap)
        y = grid_y + row * (tile_size + tile_gap)
        # alternate card tones based on style
        if style in ["Modern Content Grid"] and idx % 2 == 0:
            fill = pal["soft"]
        elif style == "Warm Commercial" and idx in [0, 4, 6]:
            fill = pal["soft"]
        elif style == "Editorial Creator" and idx in [1, 4, 8]:
            fill = pal["soft"]
        else:
            fill = pal["card"]
        rounded_rect(draw, (x, y, x+tile_size, y+tile_size), 24, fill)
        # subtle icon block
        draw.rounded_rectangle((x+18, y+18, x+tile_size-18, y+tile_size//2), radius=18, fill=pal["accent"])
        bbox = draw.textbbox((0,0), label, font=font_sub)
        draw.text((x + tile_size/2 - (bbox[2]-bbox[0])/2, y + tile_size//2 + 18), label[:12], font=font_sub, fill=pal["dark"])
        small = grid_subtitle(label)
        bbox2 = draw.textbbox((0,0), small, font=font_tiny)
        draw.text((x + tile_size/2 - (bbox2[2]-bbox2[0])/2, y + tile_size//2 + 58), small, font=font_tiny, fill=pal["muted"])

    # Footer panel
    footer_y = 1190
    rounded_rect(draw, (margin, footer_y, W-margin, H-56), 32, pal["card"])
    draw.text((margin+32, footer_y+24), "Priority Fix", font=font_sub, fill=pal["accent"])
    fix = state.get("top_fix") or "Rebuild bio, highlights, content pillars, and CTA."
    draw_wrapped(draw, fix, (margin+32, footer_y+66), font_small, pal["dark"], W - 2*margin - 64, max_lines=2)
    footer = "Preview by ClientBoost — direction only, not fake growth numbers."
    draw.text((margin+32, H-88), footer, font=font_tiny, fill=pal["muted"])

    # Save
    filename = f"preview_{uuid.uuid4().hex}.jpg"
    path = os.path.join(PREVIEW_DIR, filename)
    img.save(path, "JPEG", quality=92)
    return filename

def build_bio_direction(state: Dict[str, Any], cfg: Dict[str, Any]) -> str:
    name = state.get("profile_name") or "Brand"
    audience = state.get("target_location_or_audience") or "your audience"
    category = state.get("profile_category") or "default_general_profile"
    if category == "food_restaurant":
        return f"{name} | Food for {audience}. Clear menu, trust proof, and one-tap order CTA."
    if category == "real_estate":
        return f"{name} | Property guidance for {audience}. Listings, documents, site visits, and contact CTA."
    if category == "religious_education_content":
        return f"{name} | Daily reminders, duas, lessons, and shareable Islamic content. Follow for consistent value."
    if category in ["influencer_creator", "personal_brand", "fashion_lifestyle_creator"]:
        return f"{name} | Clear niche, strong hooks, collab-ready profile, and content people remember."
    return f"{name} | Clear positioning for {audience}. Better proof, content pillars, and action path."

def grid_subtitle(label: str) -> str:
    mapping = {
        "CTA": "Action", "Book": "Action", "Order": "Action", "Follow": "Action", "Contact": "Lead",
        "Review": "Trust", "Proof": "Trust", "Trust": "Trust", "Story": "Human", "Tip": "Value",
        "Insight": "Value", "Hook": "Reach", "Reel": "Reach", "Offer": "Sales", "Food": "Craving",
    }
    return mapping.get(label, "Content")

# ============================================================
# FLOW MESSAGES
# ============================================================
def msg_follow_gate() -> str:
    return (
        f"Before I prepare your free audit, please follow @{FOLLOW_ACCOUNT_USERNAME}.\n\n"
        "This helps us keep the audit and preview free.\n\n"
        "After following, tap I FOLLOWED."
    )

def msg_why_follow() -> str:
    return (
        "The audit and preview use AI credits and processing time.\n\n"
        "Following ClientBoost helps us keep this free while giving you a proper profile review.\n\n"
        f"Follow @{FOLLOW_ACCOUNT_USERNAME}, then tap I FOLLOWED."
    )

def msg_start_audit() -> str:
    return (
        "Welcome to ClientBoost.\n\n"
        "Let’s prepare your free Instagram growth audit.\n\n"
        "What is your profile, brand, or page name?"
    )

def msg_ask_type(profile_name: str) -> str:
    return (
        f"Got it — {profile_name}.\n\n"
        "What type of Instagram profile is this?\n\n"
        "Examples:\n"
        "restaurant, influencer, Islamic reels, salon, real estate, coach, shop, education page."
    )

def msg_ask_location() -> str:
    return (
        "Profile type noted.\n\n"
        "Who do you mainly want to reach?\n\n"
        "Examples:\n"
        "Belagavi customers, India audience, global audience, students, parents, buyers."
    )

def msg_ask_screenshot() -> str:
    return (
        "Good.\n\n"
        "Now send a screenshot of your Instagram profile.\n\n"
        "Make sure it shows:\n"
        "bio, highlights, follower area, and post grid."
    )

def msg_after_audit() -> str:
    return (
        "Your audit is ready.\n\n"
        "Next step:\n\n"
        "PREVIEW — see the improved direction\n"
        "HELP — let ClientBoost fix it\n"
        "LATEST — view this audit again"
    )

def msg_after_preview() -> str:
    return (
        "This is the direction your profile should move toward.\n\n"
        "Next step:\n\n"
        "HELP — let ClientBoost fix it\n"
        "LATEST — view audit again\n"
        "NEW AUDIT — check cooldown"
    )

def msg_handover() -> str:
    return (
        "Got it.\n\n"
        "I am handing this to the ClientBoost team now.\n\n"
        "They will review your audit, preview direction, and message you with the right next step."
    )

# ============================================================
# FLOW HANDLERS
# ============================================================
def can_start_new_audit(state: Dict[str, Any]) -> Tuple[bool, str]:
    until = parse_iso(state.get("cooldown_until", ""))
    if until and until > datetime.now(timezone.utc):
        date_txt = until.astimezone(timezone.utc).strftime("%d %b")
        return False, (
            "You already received a free audit recently.\n\n"
            f"Your next free audit unlocks after {date_txt}.\n\n"
            "Use LATEST to view your audit again, or HELP if you want ClientBoost to fix it."
        )
    return True, ""

def handle_start(sender_id: str, state: Dict[str, Any]):
    if str(state.get("handover", "false")).lower() == "true":
        send_dm(sender_id, "Your audit is already with the ClientBoost team.\n\nThey will review your profile and reply here.")
        return
    allowed, reason = can_start_new_audit(state)
    if not allowed and state.get("latest_audit"):
        send_dm(sender_id, reason, ["LATEST", "HELP"])
        return
    if FOLLOW_REQUIRED and not is_follow_verified(sender_id, state, force=False):
        state["step"] = "follow_gate"
        STORE.save_state(sender_id, state)
        send_dm(sender_id, msg_follow_gate(), ["I FOLLOWED", "WHY FOLLOW", "CANCEL"])
        return
    state["step"] = "ask_profile_name"
    state["lead_temperature"] = "cold"
    STORE.save_state(sender_id, state)
    send_dm(sender_id, msg_start_audit())

def handle_follow_confirmed(sender_id: str, state: Dict[str, Any]):
    if is_follow_verified(sender_id, state, force=True) or FOLLOW_VERIFY_MODE != "strict":
        state["step"] = "ask_profile_name"
        STORE.save_state(sender_id, state)
        send_dm(sender_id, msg_start_audit())
    else:
        send_dm(sender_id, (
            "I could not confirm the follow yet.\n\n"
            f"Please follow @{FOLLOW_ACCOUNT_USERNAME} first, then tap I FOLLOWED again.\n"
            "Sometimes Instagram takes a few seconds to update."
        ), ["I FOLLOWED", "WHY FOLLOW", "CANCEL"])

def handle_profile_name(sender_id: str, state: Dict[str, Any], text: str):
    if len(text.strip()) < 2:
        send_dm(sender_id, "Please send your profile, brand, or page name.")
        return
    state["profile_name"] = text.strip()[:80]
    state["step"] = "ask_profile_type"
    STORE.save_state(sender_id, state)
    send_dm(sender_id, msg_ask_type(state["profile_name"]))

def handle_profile_type(sender_id: str, state: Dict[str, Any], text: str):
    if len(text.strip()) < 2:
        send_dm(sender_id, "Please tell me what type of profile this is.")
        return
    state["profile_type_raw"] = text.strip()[:120]
    state["profile_category"] = detect_category(text)
    state["step"] = "ask_location_or_audience"
    STORE.save_state(sender_id, state)
    send_dm(sender_id, msg_ask_location())

def handle_location(sender_id: str, state: Dict[str, Any], text: str):
    if len(text.strip()) < 2:
        send_dm(sender_id, "Please send your target city, area, or audience.\n\nExample: Belagavi customers, India audience, or GLOBAL.")
        return
    state["target_location_or_audience"] = text.strip()[:120]
    state["step"] = "ask_screenshot"
    STORE.save_state(sender_id, state)
    send_dm(sender_id, msg_ask_screenshot())

def handle_screenshot(sender_id: str, state: Dict[str, Any], image_url: str):
    send_dm(sender_id, "Screenshot received.\n\nAnalysing your profile now. This may take a little time.")
    state["step"] = "processing_audit"
    STORE.save_state(sender_id, state)
    image = download_image(image_url)
    if image is None:
        state["step"] = "ask_screenshot"
        STORE.save_state(sender_id, state)
        send_dm(sender_id, "I could not read that screenshot.\n\nPlease send a clearer Instagram profile screenshot.")
        return
    data = audit_profile(image, state)
    # update state with audit data
    for key in ["audit_score", "main_gap", "profile_gap", "content_gap", "trust_gap", "cta_gap", "top_fix"]:
        state[key] = str(data.get(key, ""))
    state["latest_audit"] = data.get("audit_text", "")
    state["preview_style"] = data.get("recommended_preview_style") or PROFILE_CATEGORIES.get(state.get("profile_category"), PROFILE_CATEGORIES["default_general_profile"]).get("style")
    state["lead_temperature"] = "warm"
    state["step"] = "audit_sent"
    state["cooldown_until"] = (datetime.now(timezone.utc) + timedelta(days=AUDIT_COOLDOWN_DAYS)).isoformat()
    STORE.save_state(sender_id, state)
    for part in split_text(state["latest_audit"], 900):
        send_dm(sender_id, part)
        time.sleep(0.8)
    send_dm(sender_id, msg_after_audit(), ["PREVIEW", "HELP", "LATEST"])

def handle_latest(sender_id: str, state: Dict[str, Any]):
    audit = state.get("latest_audit")
    if not audit:
        send_dm(sender_id, "No audit is saved yet.\n\nSend GROWTH to start your free audit.", ["GROWTH"])
        return
    for part in split_text(audit, 900):
        send_dm(sender_id, part)
        time.sleep(0.7)
    send_dm(sender_id, msg_after_audit(), ["PREVIEW", "HELP", "LATEST"])

def handle_preview(sender_id: str, state: Dict[str, Any]):
    if not state.get("latest_audit"):
        send_dm(sender_id, "Preview needs your audit first.\n\nSend your Instagram screenshot so I can understand the profile properly.")
        state["step"] = "ask_screenshot"
        STORE.save_state(sender_id, state)
        return
    if FOLLOW_REQUIRED and not is_follow_verified(sender_id, state, force=True):
        state["step"] = "follow_gate"
        STORE.save_state(sender_id, state)
        send_dm(sender_id, (
            f"To unlock your free preview, please follow @{FOLLOW_ACCOUNT_USERNAME}.\n\n"
            "After following, tap I FOLLOWED."
        ), ["I FOLLOWED", "WHY FOLLOW", "CANCEL"])
        return
    send_dm(sender_id, "Creating your profile preview now.\n\nIt will show realistic direction — not fake followers or fake results.")
    state["step"] = "preview_processing"
    state["preview_requested"] = "true"
    state["lead_temperature"] = "hot"
    STORE.save_state(sender_id, state)
    filename = generate_preview_image(state)
    if PUBLIC_BASE_URL:
        image_url = f"{PUBLIC_BASE_URL}/preview/{filename}"
        send_image(sender_id, image_url)
    else:
        send_dm(sender_id, "Preview image created, but PUBLIC_BASE_URL is missing in Render. Add it so Instagram can receive the image.")
    state["preview_generated"] = "true"
    state["step"] = "preview_sent"
    STORE.save_state(sender_id, state)
    send_dm(sender_id, msg_after_preview(), ["HELP", "LATEST", "NEW AUDIT"])

def handle_help_or_objection(sender_id: str, state: Dict[str, Any], text: str, objection_type: str = "request_help"):
    state["lead_temperature"] = "very_hot"
    state["objection_type"] = objection_type
    state["step"] = "ask_phone"
    STORE.save_state(sender_id, state)
    reply = build_conversion_reply(state, text, objection_type)
    send_dm(sender_id, reply)
    time.sleep(0.6)
    send_dm(sender_id, "Send your WhatsApp number so the ClientBoost team can continue.", ["Type Number"])

def handle_phone(sender_id: str, state: Dict[str, Any], text: str):
    phone = phone_from_text(text)
    if not phone:
        send_dm(sender_id, "Please send a valid WhatsApp number.\n\nExample: 9876543210")
        return
    state["phone_number"] = phone
    state["handover"] = "true"
    state["lead_temperature"] = "handover"
    state["step"] = "handover"
    STORE.save_state(sender_id, state)
    STORE.save_final_lead_once(sender_id, latest_user_message=text)
    send_dm(sender_id, msg_handover())

def handle_unclear(sender_id: str, state: Dict[str, Any]):
    step = state.get("step", "new")
    if step == "follow_gate":
        send_dm(sender_id, "Please choose one option.", ["I FOLLOWED", "WHY FOLLOW", "CANCEL"])
    elif step == "ask_profile_name":
        send_dm(sender_id, "Please send your profile, brand, or page name.")
    elif step == "ask_profile_type":
        send_dm(sender_id, "Please tell me what type of Instagram profile this is.\n\nExample: restaurant, influencer, Islamic reels, salon, real estate, coach.")
    elif step == "ask_location_or_audience":
        send_dm(sender_id, "Who do you mainly want to reach?\n\nExample: Belagavi customers, India audience, global audience, students, buyers.")
    elif step == "ask_screenshot":
        send_dm(sender_id, "Please send a screenshot of your Instagram profile.")
    elif step == "audit_sent":
        send_dm(sender_id, "Choose your next step:", ["PREVIEW", "HELP", "LATEST"])
    elif step == "preview_sent":
        send_dm(sender_id, "Choose your next step:", ["HELP", "LATEST", "NEW AUDIT"])
    elif step == "ask_phone":
        send_dm(sender_id, "Please send your WhatsApp number so our team can continue.", ["Type Number"])
    elif step == "handover":
        send_dm(sender_id, "Your audit is already with the ClientBoost team.\n\nThey will review your profile and reply here.")
    else:
        send_dm(sender_id, "Send GROWTH to start your free Instagram audit.", ["GROWTH"])

# ============================================================
# MAIN MESSAGE ROUTER
# ============================================================
def handle_message(sender_id: str, message_obj: Dict[str, Any]):
    raw_text = message_obj.get("text", "") or ""
    text = raw_text.strip()
    attachments = message_obj.get("attachments", []) or []
    image_url = None
    for a in attachments:
        if a.get("type") == "image":
            image_url = a.get("payload", {}).get("url")
            break

    state = STORE.get_state(sender_id)

    # Session expiry: keep latest audit, reset active step if very old and not handover
    last_active = parse_iso(state.get("last_active", ""))
    if last_active and datetime.now(timezone.utc) - last_active > timedelta(days=SESSION_EXPIRY_DAYS):
        if state.get("step") not in ["audit_sent", "preview_sent", "handover"] and str(state.get("handover", "false")).lower() != "true":
            state["step"] = "new"
            STORE.save_state(sender_id, state)

    # Owner reset
    if text == OWNER_RESET_CODE:
        STORE.reset_state(sender_id)
        send_dm(sender_id, "Owner reset complete.\n\nSend GROWTH to test from the beginning.", ["GROWTH"])
        return

    # Handover protection
    if str(state.get("handover", "false")).lower() == "true" and state.get("step") == "handover":
        send_dm(sender_id, "Your audit is already with the ClientBoost team.\n\nThey will review your profile and reply here.")
        return

    # Screenshot handling
    if image_url:
        if state.get("step") == "ask_screenshot":
            handle_screenshot(sender_id, state, image_url)
        else:
            send_dm(sender_id, "I received the image.\n\nTo audit it properly, send GROWTH first and I will ask for the screenshot at the right step.", ["GROWTH"])
        return

    # No text fallback
    if not text:
        handle_unclear(sender_id, state)
        return

    intent = direct_intent(text)
    step = state.get("step", "new")

    # For state answers, avoid wasting AI
    if not intent:
        if step == "ask_profile_name":
            intent = "answer_profile_name"
        elif step == "ask_profile_type":
            intent = "answer_profile_type"
        elif step == "ask_location_or_audience":
            intent = "answer_location_or_audience"
        elif step == "ask_phone" and phone_from_text(text):
            intent = "phone_number_sent"
        else:
            understood = ai_understand_message(state, text)
            if float(understood.get("confidence", 0) or 0) >= 0.65:
                intent = understood.get("intent")
                if understood.get("profile_category") in PROFILE_CATEGORIES:
                    state["profile_category"] = understood.get("profile_category")
                if understood.get("clean_answer") and intent in ["answer_profile_type", "answer_location_or_audience", "answer_profile_name"]:
                    text = understood.get("clean_answer")
                if understood.get("objection_type") and understood.get("objection_type") != "none":
                    state["objection_type"] = understood.get("objection_type")
            else:
                intent = "unclear"

    # Route intent
    if intent == "owner_reset":
        STORE.reset_state(sender_id)
        send_dm(sender_id, "Owner reset complete.\n\nSend GROWTH to test from the beginning.", ["GROWTH"])
    elif intent == "start_audit":
        handle_start(sender_id, state)
    elif intent == "follow_confirmed" or (intent == "generic_done" and step == "follow_gate"):
        handle_follow_confirmed(sender_id, state)
    elif intent == "ask_why_follow":
        send_dm(sender_id, msg_why_follow(), ["I FOLLOWED", "CANCEL"])
    elif intent == "cancel":
        state["step"] = "new"
        STORE.save_state(sender_id, state)
        send_dm(sender_id, "Cancelled.\n\nSend GROWTH anytime to start again.", ["GROWTH"])
    elif intent == "soft_reset":
        state["step"] = "new"
        STORE.save_state(sender_id, state)
        send_dm(sender_id, "No problem.\n\nSend GROWTH whenever you are ready.", ["GROWTH"])
    elif intent == "answer_profile_name":
        handle_profile_name(sender_id, state, text)
    elif intent == "answer_profile_type":
        handle_profile_type(sender_id, state, text)
    elif intent == "answer_location_or_audience":
        handle_location(sender_id, state, text)
    elif intent == "request_latest":
        handle_latest(sender_id, state)
    elif intent == "request_preview":
        handle_preview(sender_id, state)
    elif intent == "request_new_audit":
        allowed, reason = can_start_new_audit(state)
        if allowed:
            state["step"] = "new"
            STORE.save_state(sender_id, state)
            handle_start(sender_id, state)
        else:
            send_dm(sender_id, reason, ["LATEST", "HELP"])
    elif intent in ["request_help", "price_question", "asks_details", "trust_issue", "thinking_delay", "already_has_designer", "wants_to_do_self", "budget_low", "asks_results"]:
        if intent in ["thinking_delay", "not_now"]:
            state["lead_temperature"] = "warm"
            state["objection_type"] = intent
            STORE.save_state(sender_id, state)
            reply = build_conversion_reply(state, text, intent)
            send_dm(sender_id, reply, ["HELP", "LATEST"])
        else:
            handle_help_or_objection(sender_id, state, text, intent)
    elif intent == "not_now":
        state["lead_temperature"] = "warm"
        state["objection_type"] = "not_now"
        STORE.save_state(sender_id, state)
        send_dm(sender_id, "Understood.\n\nUse the audit as your starting point for now.\n\nReply HELP anytime when you want ClientBoost to handle it.", ["HELP", "LATEST"])
    elif intent == "phone_number_sent":
        handle_phone(sender_id, state, text)
    elif intent == "yes":
        if step in ["audit_sent", "preview_sent", "conversion_active"]:
            handle_help_or_objection(sender_id, state, text, "request_help")
        elif step == "ask_phone":
            send_dm(sender_id, "Please send your WhatsApp number so our team can continue.", ["Type Number"])
        else:
            handle_unclear(sender_id, state)
    else:
        handle_unclear(sender_id, state)

# ============================================================
# WEBHOOK ROUTES
# ============================================================
@app.route("/", methods=["GET"])
def home():
    return "ClientBoost Bot is running", 200

@app.route("/preview/<filename>", methods=["GET"])
def preview_file(filename):
    return send_from_directory(PREVIEW_DIR, filename)

@app.route("/webhook", methods=["GET"])
def verify():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return challenge or "", 200
    return "Forbidden", 403

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json or {}
    if data.get("object") == "instagram":
        for entry in data.get("entry", []):
            for event in entry.get("messaging", []):
                sender_id = event.get("sender", {}).get("id")
                message_obj = event.get("message", {}) or {}
                if message_obj.get("is_echo"):
                    continue
                has_text = bool(message_obj.get("text"))
                has_image = any(a.get("type") == "image" for a in message_obj.get("attachments", []) or [])
                if sender_id and (has_text or has_image):
                    t = threading.Thread(target=handle_message, args=(sender_id, message_obj), daemon=True)
                    t.start()
    return jsonify({"status": "ok"}), 200

# ============================================================
# RUN LOCAL
# ============================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
