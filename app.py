# ============================================================
# ClientBoost — Instagram Growth Audit + Preview + Lead Funnel
# Single-file Render deployment version
# Core stack: Flask + Meta Instagram Messaging API + Gemini + Google Sheets + Pillow
# ============================================================

import os
import re
import io
import json
import time
import uuid
import textwrap
import threading
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests
from flask import Flask, request, jsonify, send_from_directory
from PIL import Image, ImageDraw, ImageFont

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
    "gemini-2.5-pro,gemini-2.5-flash,gemini-2.5-flash-lite"
).split(",") if m.strip()]
GEMINI_CONVERSION_MODELS = [m.strip() for m in os.environ.get(
    "GEMINI_CONVERSION_MODELS",
    "gemini-2.5-flash,gemini-2.5-flash-lite,gemini-2.5-pro"
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
MAX_DM_CHARS = 850
PREFERRED_DM_CHARS = 650

USER_STATE_HEADERS = [
    "sender_id", "instagram_username", "step", "profile_name", "profile_type_raw",
    "profile_category", "target_location_or_audience", "latest_audit", "audit_score",
    "main_gap", "profile_gap", "content_gap", "trust_gap", "cta_gap", "top_fix",
    "preview_requested", "preview_generated", "preview_style", "lead_temperature",
    "objection_type", "phone_number", "contact_info", "contact_type", "goal", "timeline",
    "scope_preference", "handover", "cooldown_until", "follow_verified",
    "follow_verified_at", "last_active", "final_saved", "state_json"
]

FINAL_LEADS_HEADERS = [
    "timestamp", "sender_id", "instagram_username", "profile_name", "profile_type_raw",
    "profile_category", "target_location_or_audience", "audit_score", "main_gap",
    "top_fix", "preview_requested", "preview_generated", "preview_style",
    "lead_temperature", "objection_type", "goal", "timeline", "scope_preference",
    "contact_info", "contact_type", "handover_status", "recommended_offer",
    "session_status", "latest_user_message"
]

# Use exact command matching for trigger words. Do not trigger GROWTH inside normal sentences.
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
    "fix this": "request_help",
    "help": "request_help",
    "interested": "request_help",
    "work with us": "request_help",
    "view audit": "request_latest",
    "latest": "request_latest",
    "show audit": "request_latest",
    "new review": "request_new_audit",
    "new audit": "request_new_audit",
    "yes": "yes",
    "y": "yes",
    "okay": "yes",
    "ok": "yes",
    "tell me": "yes",
    "show me": "yes",
    "explain": "yes",
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
        "keywords": ["influencer", "creator", "blogger", "reels", "lifestyle", "vlogger", "content creator"],
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
        "keywords": ["community", "city page", "local page", "updates", "events", "public"],
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

def split_text(text: str, limit: int = MAX_DM_CHARS) -> List[str]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]
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

def email_from_text(text: str) -> Optional[str]:
    m = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text or "")
    return m.group(0) if m else None

def contact_from_text(text: str) -> Tuple[Optional[str], Optional[str]]:
    email = email_from_text(text)
    if email:
        return email, "email"
    phone = phone_from_text(text)
    if phone:
        return phone, "phone"
    return None, None

def calculate_recommended_offer(state: Dict[str, Any]) -> str:
    category = state.get("profile_category", "default_general_profile")
    goal = normalize(state.get("goal", ""))
    scope = normalize(state.get("scope_preference", ""))
    score_raw = str(state.get("audit_score", ""))
    score = int(float(score_raw)) if score_raw.replace(".", "", 1).isdigit() else 0

    if "full" in scope or "manage" in scope or "system" in scope:
        return "Full Growth System"
    if any(x in goal for x in ["sales", "booking", "enquir", "lead", "client"]):
        if category in ["food_restaurant", "beauty_salon", "real_estate", "clinic_healthcare", "professional_service", "local_service"]:
            return "Growth Setup + Lead System"
    if score and score < 55:
        return "Profile Fix + Growth Setup"
    return "Growth Setup"

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
            "contact_info": "",
            "contact_type": "",
            "goal": "",
            "timeline": "",
            "scope_preference": "",
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
        state["lead_temperature"] = "senior_review"
        state["step"] = "senior_review"
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
            "lead_temperature": "senior_review",
            "objection_type": state.get("objection_type", "none"),
            "goal": state.get("goal", ""),
            "timeline": state.get("timeline", ""),
            "scope_preference": state.get("scope_preference", ""),
            "contact_info": state.get("contact_info", ""),
            "contact_type": state.get("contact_type", ""),
            "handover_status": "senior_review_ready",
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
    parts = split_text(text, MAX_DM_CHARS)
    final_response: Dict[str, Any] = {}
    for i, part in enumerate(parts):
        url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/me/messages"
        message: Dict[str, Any] = {"text": part}
        if quick_replies and i == len(parts) - 1:
            message["quick_replies"] = [
                {"content_type": "text", "title": title[:20], "payload": title.upper().replace(" ", "_")}
                for title in quick_replies[:13]
            ]
        payload = {"recipient": {"id": recipient_id}, "message": message, "messaging_type": "RESPONSE"}
        try:
            r = requests.post(url, params={"access_token": PAGE_ACCESS_TOKEN}, json=payload, timeout=15)
            if r.status_code >= 300:
                print("send_dm error", r.status_code, r.text)
            final_response = r.json() if r.text else {}
        except Exception as e:
            print(f"send_dm failed: {e}")
            final_response = {"error": str(e)}
        if len(parts) > 1:
            time.sleep(0.8)
    return final_response

def send_typing_action(recipient_id: str) -> None:
    # Best-effort only. Some Instagram messaging setups ignore sender actions.
    if not PAGE_ACCESS_TOKEN:
        return
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/me/messages"
    payload = {"recipient": {"id": recipient_id}, "sender_action": "typing_on"}
    try:
        requests.post(url, params={"access_token": PAGE_ACCESS_TOKEN}, json=payload, timeout=5)
    except Exception:
        pass

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
    # Prefer specific creator categories before broad influencer/reels categories.
    priority = [
        "religious_education_content", "food_restaurant", "real_estate", "beauty_salon",
        "clinic_healthcare", "photographer_videographer", "artist_portfolio", "knowledge_creator",
        "motivation_quotes_page", "entertainment_meme_page", "fashion_lifestyle_creator",
    ]
    for category in priority + [c for c in PROFILE_CATEGORIES if c not in priority]:
        cfg = PROFILE_CATEGORIES[category]
        for kw in cfg.get("keywords", []):
            if kw in t:
                return category
    return "default_general_profile"

def direct_intent(text: str) -> Optional[str]:
    t = normalize(text)
    if not t:
        return None
    if OWNER_RESET_CODE and text.strip() == OWNER_RESET_CODE:
        return "owner_reset"
    if t in DIRECT_COMMANDS:
        return DIRECT_COMMANDS[t]
    if any(x in t for x in ["price", "cost", "how much", "charges", "package", "rate", "fees"]):
        return "price_question"
    if any(x in t for x in ["details", "tell me more", "services", "what do you offer", "what will you do", "process"]):
        return "asks_details"
    if any(x in t for x in ["designer", "editor", "social media person", "agency already", "team already"]):
        return "already_has_designer"
    if any(x in t for x in ["think", "maybe", "later"]):
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
You are the intent understanding layer for ClientBoost's Instagram audit conversation.
Return ONLY valid JSON. No markdown.

Current state:
step: {state.get('step')}
profile_name: {state.get('profile_name')}
profile_type_raw: {state.get('profile_type_raw')}
profile_category: {state.get('profile_category')}
audit_sent: {bool(state.get('latest_audit'))}
preview_generated: {state.get('preview_generated')}
senior_review: {state.get('step') == 'senior_review'}

User message:
{user_text}

Classify intent as one of:
start_audit, follow_confirmed, ask_why_follow, cancel,
answer_profile_name, answer_profile_type, answer_location_or_audience,
request_preview, request_help, request_latest, request_new_audit,
price_question, asks_details, trust_issue, thinking_delay, not_now,
already_has_designer, wants_to_do_self, budget_low, asks_results,
answer_goal, answer_timeline, answer_scope, contact_sent,
general_question, unclear.

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
Audit this Instagram profile screenshot like a world-class agency strategist.
Be specific to what is visible in the screenshot.

User details:
Profile / Brand name: {state.get('profile_name')}
Profile type / niche: {state.get('profile_type_raw')}
Internal category: {category}
Target audience: {state.get('target_location_or_audience')}
Likely conversion angle: {cfg.get('angle')}

Important:
Return ONLY valid JSON.
No markdown.
No long paragraphs.
No fake guarantees.
No generic advice.
No backend words.
No mention of AI.

Use this 100-point scoring system:
1. Name + Username clarity /10
2. Profile photo /10
3. Bio positioning /10
4. CTA / action path /10
5. Highlights /10
6. Highlight covers /10
7. Post grid /10
8. Reels + hooks /10
9. Trust signals /10
10. Audience fit + conversion /10

The score must be honest and based on visible evidence.

JSON schema:
{{
  "overall_score": 0,
  "main_gap": "one clear main growth gap based on the screenshot",
  "profile_identity_score": 0,
  "bio_cta_score": 0,
  "highlights_score": 0,
  "posts_reels_score": 0,
  "trust_conversion_score": 0,
  "profile_identity": {{
    "noticed": "specific observation about name, username, profile photo, positioning",
    "meaning": "why this matters",
    "fix": "specific fix"
  }},
  "bio_cta": {{
    "noticed": "specific observation about bio and action path",
    "meaning": "why this affects conversion",
    "fix": "specific fix"
  }},
  "highlights": {{
    "noticed": "specific observation about highlights, labels, covers, order",
    "meaning": "why this affects trust",
    "fix": "specific fix"
  }},
  "posts_reels": {{
    "noticed": "specific observation about posts, reels, grid, covers, hooks",
    "meaning": "why this affects reach or memory",
    "fix": "specific fix"
  }},
  "trust_conversion": {{
    "noticed": "specific observation about trust proof, reviews, authority, CTA, contact path",
    "meaning": "why this affects enquiries, followers, sales, bookings, or collaborations",
    "fix": "specific fix"
  }},
  "top_fix": "one priority fix that should be done first",
  "recommended_preview_style": "{cfg.get('style')}"
}}

Tone:
Premium, direct, human, expert.
Use screenshot evidence.
If something is not visible, say it is not clearly visible.
""".strip()

    text = gemini_generate(prompt, image=image, purpose="audit")
    data = extract_json(text or "", {})
    if not data:
        data = fallback_audit(cfg)
    data = normalize_audit_data(data, cfg)
    return data

def fallback_audit(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "overall_score": 50,
        "main_gap": "The profile structure is not clear enough for a new visitor to understand, trust, and act quickly.",
        "profile_identity_score": 5,
        "bio_cta_score": 5,
        "highlights_score": 5,
        "posts_reels_score": 5,
        "trust_conversion_score": 5,
        "profile_identity": {
            "noticed": "The profile identity is not clear enough from the visible screenshot.",
            "meaning": "A new visitor should understand the profile within a few seconds.",
            "fix": "Make the name, profile photo, and first line of the bio more direct."
        },
        "bio_cta": {
            "noticed": "The bio and action path need more clarity.",
            "meaning": "People may visit but leave without knowing what to do next.",
            "fix": "Add a clear value line and one direct action."
        },
        "highlights": {
            "noticed": "The highlight structure needs improvement.",
            "meaning": "Highlights should build trust before someone scrolls.",
            "fix": "Use clearer labels and covers based on the profile type."
        },
        "posts_reels": {
            "noticed": "The grid needs stronger content pillars.",
            "meaning": "Random content may get views, but structured content builds memory and trust.",
            "fix": "Create repeatable content pillars for value, proof, story, and CTA."
        },
        "trust_conversion": {
            "noticed": "Trust and conversion signals are not strong enough.",
            "meaning": "People act when they understand and trust the profile.",
            "fix": "Add proof, stronger CTA, and a clearer contact route."
        },
        "top_fix": "Rebuild the bio, highlights, and first 9-post structure.",
        "recommended_preview_style": cfg.get("style"),
    }

def normalize_audit_data(data: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    for key in ["profile_identity", "bio_cta", "highlights", "posts_reels", "trust_conversion"]:
        if not isinstance(data.get(key), dict):
            data[key] = {"noticed": "Not clearly visible.", "meaning": "This affects trust and action.", "fix": "Make this section clearer."}
        for sub in ["noticed", "meaning", "fix"]:
            data[key][sub] = str(data[key].get(sub, "")).strip()[:500]
    for key in ["profile_identity_score", "bio_cta_score", "highlights_score", "posts_reels_score", "trust_conversion_score"]:
        try:
            data[key] = max(0, min(10, int(float(data.get(key, 0)))))
        except Exception:
            data[key] = 5
    try:
        score = int(float(data.get("overall_score", 0)))
    except Exception:
        score = sum(int(data.get(k, 0)) for k in ["profile_identity_score", "bio_cta_score", "highlights_score", "posts_reels_score", "trust_conversion_score"])
        score = min(100, score * 2)
    data["overall_score"] = max(0, min(100, score))
    data["main_gap"] = str(data.get("main_gap") or "The profile needs stronger structure.").strip()[:500]
    data["top_fix"] = str(data.get("top_fix") or "Rebuild the bio, highlights, and content structure.").strip()[:500]
    data["recommended_preview_style"] = data.get("recommended_preview_style") or cfg.get("style")
    return data

def build_audit_messages(data: Dict[str, Any], state: Dict[str, Any]) -> List[str]:
    profile_name = state.get("profile_name") or "This profile"
    profile_type = state.get("profile_type_raw") or "Instagram profile"
    audience = state.get("target_location_or_audience") or "target audience"
    score = str(data.get("overall_score", "0"))
    main_gap = data.get("main_gap") or "The profile needs stronger structure."

    messages = [
        (
            f"📍 CLIENTBOOST INSTAGRAM AUDIT\n\n"
            f"Profile: {profile_name}\n"
            f"Type: {profile_type}\n"
            f"Audience: {audience}\n\n"
            f"◆ Overall Rating: {score}/100\n\n"
            f"Main Growth Gap:\n{main_gap}\n\n"
            f"Next, I’ll break down where the profile is losing attention, trust, or action."
        )
    ]

    sections = [
        ("👤", "1. PROFILE IDENTITY", "profile_identity", "profile_identity_score", "Next: Bio + CTA."),
        ("✍️", "2. BIO + CTA", "bio_cta", "bio_cta_score", "Next: Highlights."),
        ("⭕", "3. HIGHLIGHTS", "highlights", "highlights_score", "Next: Posts + Reels."),
        ("🎬", "4. POSTS + REELS", "posts_reels", "posts_reels_score", "Next: Trust + Conversion."),
        ("🤝", "5. TRUST + CONVERSION", "trust_conversion", "trust_conversion_score", ""),
    ]

    for emoji, title, key, score_key, cta in sections:
        sec = data.get(key, {})
        msg = (
            f"{emoji} {title}\n\n"
            f"Rating: {data.get(score_key, 0)}/10\n\n"
            f"I noticed:\n{sec.get('noticed', '')}\n\n"
            f"What this means:\n{sec.get('meaning', '')}\n\n"
            f"Fix:\n{sec.get('fix', '')}"
        )
        if cta:
            msg += f"\n\n{cta}"
        messages.append(msg)

    messages.append(
        f"⚡ PRIORITY FIX\n\n"
        f"Your first fix should be:\n\n"
        f"{data.get('top_fix', '')}\n\n"
        f"This is the change that will make the profile easier to understand, trust, and act on."
    )

    final_messages: List[str] = []
    for msg in messages:
        if len(msg) <= MAX_DM_CHARS:
            final_messages.append(msg)
        else:
            final_messages.extend(split_text(msg, MAX_DM_CHARS))
    return final_messages

def build_ai_conversion_message(state: Dict[str, Any], user_text: str, stage: str) -> str:
    cfg = PROFILE_CATEGORIES.get(state.get("profile_category") or "default_general_profile", PROFILE_CATEGORIES["default_general_profile"])
    prompt = f"""
You are a senior ClientBoost strategist speaking in Instagram DM.
Write ONE short message only.
Do not ask for contact unless the stage is ask_contact.
Do not mention bot, AI, backend, automation, cooldown, or handover.
Do not use fake guarantees, fake urgency, or fake scarcity.
Use the exact audit gap and profile type.
Keep under 650 characters.
Use light premium emojis only if useful.
End with one clear CTA question.

Stage: {stage}
User message: {user_text}

Profile:
Name: {state.get('profile_name')}
Type: {state.get('profile_type_raw')}
Audience: {state.get('target_location_or_audience')}
Category: {state.get('profile_category')}
Audit score: {state.get('audit_score')}/100
Main gap: {state.get('main_gap')}
Top fix: {state.get('top_fix')}
Profile fix: {state.get('profile_gap')}
Content fix: {state.get('content_gap')}
Trust fix: {state.get('trust_gap')}
CTA fix: {state.get('cta_gap')}
Preview direction: {', '.join(cfg.get('grid', []))}

Stage meanings:
pain = make the user feel understood and aware of the hidden cost.
reframe = explain why random posting is not the real fix.
solution = position ClientBoost as strategy + execution and ask if they want fit checked.
goal = ask what result matters most.
timeline = ask how soon they want to improve.
scope = ask profile fix first or full growth system.
ask_contact = ask best contact detail, WhatsApp or email, for senior strategist review.
objection = answer the objection, then move to the next logical question.
""".strip()
    text = gemini_generate(prompt, purpose="conversion")
    if text:
        return text.strip()[:MAX_DM_CHARS]
    return deterministic_conversion_message(state, stage, user_text)

def deterministic_conversion_message(state: Dict[str, Any], stage: str, user_text: str = "") -> str:
    main_gap = state.get("main_gap") or "the profile is not giving visitors a strong enough reason to trust and act"
    top_fix = state.get("top_fix") or "rebuild the bio, highlights, content pillars, and CTA"
    if stage == "pain":
        return (
            "I can see the main issue clearly.\n\n"
            f"This profile is not losing because of one post. The bigger gap is: {main_gap}.\n\n"
            "That is where most profiles lose opportunities silently.\n\n"
            "Does that feel accurate from your side?"
        )
    if stage == "reframe":
        return (
            "Exactly.\n\n"
            "Most people try to fix Instagram by posting more. But posting more does not help if the profile foundation is weak.\n\n"
            "First we fix the structure: bio, highlights, content pillars, proof, CTA, and enquiry path.\n\n"
            "Should I show what ClientBoost would fix first?"
        )
    if stage == "solution":
        return (
            "ClientBoost would not start with random posts.\n\n"
            f"For this profile, the first move is: {top_fix}.\n\n"
            "Then content is planned around what your audience needs to trust before they follow, message, buy, book, or collaborate.\n\n"
            "Want me to check what level of support fits you?"
        )
    if stage == "goal":
        return (
            "Good. What matters most right now?\n\n"
            "1. More enquiries\n"
            "2. More followers\n"
            "3. More sales/bookings\n"
            "4. More trust\n"
            "5. Better personal brand\n"
            "6. Brand collaborations"
        )
    if stage == "timeline":
        return (
            "Understood.\n\n"
            "How soon do you want to improve this?\n\n"
            "1. Immediately\n"
            "2. This week\n"
            "3. This month\n"
            "4. Just exploring"
        )
    if stage == "scope":
        return (
            "That helps.\n\n"
            "Do you want only the profile structure fixed first, or do you want ClientBoost to handle the full growth system too?"
        )
    if stage == "ask_contact":
        return (
            "Good. This is worth reviewing properly with a senior ClientBoost strategist.\n\n"
            "Send the best contact detail for you — WhatsApp number or email.\n\n"
            "They’ll review your audit, preview direction, goal, and suggest the right starting plan."
        )
    return (
        f"Fair point. Your audit shows the main gap is: {main_gap}.\n\n"
        "Before suggesting anything, I want to understand your goal clearly.\n\n"
        "What matters most right now — enquiries, followers, sales, trust, or collaborations?"
    )

# ============================================================
# PREVIEW IMAGE GENERATOR — Instagram profile style mockup
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

def text_width(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]

def draw_wrapped(draw: ImageDraw.ImageDraw, text: str, xy: Tuple[int, int], font, fill, max_width: int, line_gap: int = 7, max_lines: int = 4) -> int:
    words = (text or "").split()
    lines, current = [], ""
    for word in words:
        test = (current + " " + word).strip()
        if text_width(draw, test, font) <= max_width:
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
        return {"bg": (245, 236, 224), "screen": (255, 255, 255), "text": (20, 20, 20), "muted": (100, 100, 100), "accent": (179, 89, 42), "soft": (239, 219, 198)}
    if style == "Modern Content Grid":
        return {"bg": (20, 23, 30), "screen": (12, 14, 18), "text": (245, 245, 245), "muted": (178, 183, 190), "accent": (118, 160, 255), "soft": (35, 41, 54)}
    if style == "Editorial Creator":
        return {"bg": (238, 233, 224), "screen": (255, 255, 252), "text": (28, 28, 28), "muted": (93, 88, 82), "accent": (92, 78, 59), "soft": (225, 218, 205)}
    return {"bg": (239, 242, 248), "screen": (255, 255, 255), "text": (18, 23, 31), "muted": (94, 102, 114), "accent": (25, 88, 210), "soft": (225, 233, 248)}

def build_bio_direction(state: Dict[str, Any], cfg: Dict[str, Any]) -> str:
    name = state.get("profile_name") or "Brand"
    audience = state.get("target_location_or_audience") or "your audience"
    category = state.get("profile_category") or "default_general_profile"
    if category == "food_restaurant":
        return f"{name} | Fresh food for {audience}. Menu, proof, and easy order path."
    if category == "real_estate":
        return f"{name} | Property guidance for {audience}. Listings, documents, visits, and contact."
    if category == "religious_education_content":
        return f"{name} | Daily reminders, duas, lessons, and shareable Islamic content."
    if category in ["influencer_creator", "personal_brand", "fashion_lifestyle_creator"]:
        return f"{name} | Clear niche, stronger hooks, collab-ready profile, and memorable content."
    if category == "clinic_healthcare":
        return f"{name} | Clear care, treatments, trust proof, and simple booking path."
    return f"{name} | Clear positioning for {audience}. Better proof, content pillars, and action path."

def grid_subtitle(label: str) -> str:
    mapping = {
        "CTA": "Action", "Book": "Action", "Order": "Action", "Follow": "Action", "Contact": "Lead",
        "Review": "Trust", "Proof": "Trust", "Trust": "Trust", "Story": "Human", "Tip": "Value",
        "Insight": "Value", "Hook": "Reach", "Reel": "Reach", "Offer": "Sales", "Food": "Craving",
        "Reminder": "Save", "Hadith": "Value", "Dua": "Share", "Quote": "Share",
    }
    return mapping.get(label, "Content")

def generate_preview_image(state: Dict[str, Any]) -> str:
    category = state.get("profile_category") or "default_general_profile"
    cfg = PROFILE_CATEGORIES.get(category, PROFILE_CATEGORIES["default_general_profile"])
    style = state.get("preview_style") or cfg.get("style", "Premium Structured")
    pal = style_palette(style)

    W, H = 1080, 1350
    img = Image.new("RGB", (W, H), pal["bg"])
    draw = ImageDraw.Draw(img)

    font_xl = load_font(44, True)
    font_lg = load_font(34, True)
    font_md = load_font(27, True)
    font_body = load_font(24, False)
    font_sm = load_font(19, False)
    font_xs = load_font(16, False)

    # Phone/screen card
    sx, sy = 70, 54
    sw, sh = 940, 1190
    rounded_rect(draw, (sx, sy, sx + sw, sy + sh), 48, pal["screen"])

    text = pal["text"]
    muted = pal["muted"]
    accent = pal["accent"]
    soft = pal["soft"]

    # Top bar
    y = sy + 38
    username = (state.get("instagram_username") or state.get("profile_name") or "yourprofile").strip().replace(" ", "").lower()[:24]
    if not username.startswith("@"):
        username_display = username
    else:
        username_display = username[1:]
    draw.text((sx + 44, y), username_display, font=font_lg, fill=text)
    draw.text((sx + sw - 100, y + 2), "☰", font=font_lg, fill=text)

    # Profile block
    y += 78
    cx, cy = sx + 104, y + 62
    draw.ellipse((cx - 58, cy - 58, cx + 58, cy + 58), fill=soft, outline=accent, width=5)
    initials = (state.get("profile_name") or "CB")[:2].upper()
    iw = text_width(draw, initials, font_md)
    draw.text((cx - iw / 2, cy - 18), initials, font=font_md, fill=accent)

    stats_x = sx + 230
    stat_items = [("9", "Post plan"), ("Bio", "Clear"), ("CTA", "Ready")]
    gap = 200
    for i, (top, bottom) in enumerate(stat_items):
        px = stats_x + i * gap
        tw = text_width(draw, top, font_md)
        draw.text((px - tw / 2, y + 28), top, font=font_md, fill=text)
        bw = text_width(draw, bottom, font_sm)
        draw.text((px - bw / 2, y + 66), bottom, font=font_sm, fill=muted)

    y += 145
    profile_name = (state.get("profile_name") or "Your Profile")[:36]
    draw.text((sx + 44, y), profile_name, font=font_md, fill=text)
    y += 38
    bio = build_bio_direction(state, cfg)
    y = draw_wrapped(draw, bio, (sx + 44, y), font_body, text, sw - 88, max_lines=3)
    y += 12
    cta_line = "Start with structure → then content → then conversion"
    draw.text((sx + 44, y), cta_line, font=font_sm, fill=accent)
    y += 50

    # Action buttons
    btn_w = (sw - 110) // 3
    for i, label in enumerate(["Bio", "Highlights", "Content"]):
        bx = sx + 44 + i * (btn_w + 11)
        rounded_rect(draw, (bx, y, bx + btn_w, y + 44), 12, soft)
        lw = text_width(draw, label, font_sm)
        draw.text((bx + btn_w / 2 - lw / 2, y + 11), label, font=font_sm, fill=text)
    y += 76

    # Highlights
    highlights = cfg.get("highlights", [])[:6]
    spacing = (sw - 88) // 6
    for i, h in enumerate(highlights):
        hx = sx + 44 + i * spacing + spacing // 2
        draw.ellipse((hx - 37, y, hx + 37, y + 74), fill=pal["screen"], outline=accent, width=3)
        icon = h[:1].upper()
        iw = text_width(draw, icon, font_sm)
        draw.text((hx - iw / 2, y + 24), icon, font=font_sm, fill=accent)
        label = h[:10]
        lw = text_width(draw, label, font_xs)
        draw.text((hx - lw / 2, y + 86), label, font=font_xs, fill=muted)
    y += 130

    # Tabs
    draw.line((sx + 44, y, sx + sw - 44, y), fill=soft, width=2)
    y += 22
    tabs = ["▦", "▶", "☷"]
    for i, t in enumerate(tabs):
        tx = sx + sw * (i + 0.5) / 3
        tw = text_width(draw, t, font_md)
        draw.text((tx - tw / 2, y), t, font=font_md, fill=accent if i == 0 else muted)
    y += 54

    # Grid
    grid_labels = cfg.get("grid", [])[:9]
    tile_gap = 6
    tile_size = (sw - 88 - 2 * tile_gap) // 3
    for idx, label in enumerate(grid_labels):
        row, col = divmod(idx, 3)
        x = sx + 44 + col * (tile_size + tile_gap)
        yy = y + row * (tile_size + tile_gap)
        if style == "Modern Content Grid":
            fill = soft if idx % 2 == 0 else pal["screen"]
        elif style == "Warm Commercial":
            fill = soft if idx in [0, 4, 6] else pal["screen"]
        elif style == "Editorial Creator":
            fill = soft if idx in [1, 4, 8] else pal["screen"]
        else:
            fill = soft if idx in [2, 4, 6] else pal["screen"]
        draw.rectangle((x, yy, x + tile_size, yy + tile_size), fill=fill, outline=pal["bg"], width=2)
        # cover header strip
        draw.rectangle((x, yy, x + tile_size, yy + 56), fill=accent)
        label_short = label[:13]
        lw = text_width(draw, label_short, font_md)
        draw.text((x + tile_size / 2 - lw / 2, yy + tile_size / 2 - 22), label_short, font=font_md, fill=text)
        sub = grid_subtitle(label)
        sw2 = text_width(draw, sub, font_xs)
        draw.text((x + tile_size / 2 - sw2 / 2, yy + tile_size / 2 + 20), sub, font=font_xs, fill=muted)

    # Bottom footer outside phone
    footer = "ClientBoost profile direction — built from the audit"
    fw = text_width(draw, footer, font_sm)
    draw.text((W / 2 - fw / 2, H - 58), footer, font=font_sm, fill=(80, 80, 80) if pal["bg"] != (20, 23, 30) else (210, 210, 210))

    filename = f"preview_{uuid.uuid4().hex}.jpg"
    path = os.path.join(PREVIEW_DIR, filename)
    img.save(path, "JPEG", quality=92)
    return filename

# ============================================================
# FLOW MESSAGES
# ============================================================
def msg_follow_gate() -> str:
    return (
        f"To unlock the free audit, follow @{FOLLOW_ACCOUNT_USERNAME} first.\n\n"
        "Once done, tap I FOLLOWED."
    )

def msg_why_follow() -> str:
    return (
        "The free audit is reserved for people who genuinely want to improve their profile.\n\n"
        "Following ClientBoost helps us keep it available without charging upfront.\n\n"
        f"Follow @{FOLLOW_ACCOUNT_USERNAME}, then tap I FOLLOWED."
    )

def msg_start_audit() -> str:
    return (
        "Got it.\n\n"
        "I’ll review the profile from a ClientBoost growth lens — positioning, content, trust, and enquiry path.\n\n"
        "First, what is the profile or brand name?"
    )

def msg_ask_type(profile_name: str) -> str:
    return (
        f"Noted — {profile_name}.\n\n"
        "What best describes this Instagram profile?"
    )

def msg_ask_location() -> str:
    return (
        "Understood.\n\n"
        "Who should this profile attract?\n\n"
        "You can reply naturally — customers, buyers, students, brands, clients, followers, or a global audience."
    )

def msg_ask_screenshot() -> str:
    return (
        "Good.\n\n"
        "Now send a screenshot of the Instagram profile.\n\n"
        "Make sure the screenshot shows the bio, highlights, follower area, and post grid."
    )

def msg_after_audit() -> str:
    return (
        "Your first fix is clear.\n\n"
        "This profile does not need random posting first.\n"
        "It needs stronger structure.\n\n"
        "Choose the next step:"
    )

def msg_after_preview() -> str:
    return (
        "I’ve prepared the profile direction.\n\n"
        "It shows how the bio, highlights, and content grid can be structured from the audit.\n\n"
        "Want ClientBoost to map how this would be fixed properly?"
    )

def msg_senior_review() -> str:
    return (
        "Done.\n\n"
        "Your profile case is now ready for senior ClientBoost review.\n\n"
        "The strategist will continue from here with your audit, preview direction, goal, and contact details already noted."
    )

# ============================================================
# FLOW HANDLERS
# ============================================================
def can_start_new_audit(state: Dict[str, Any]) -> Tuple[bool, str]:
    until = parse_iso(state.get("cooldown_until", ""))
    if until and until > datetime.now(timezone.utc):
        return False, (
            "Your latest audit is still active.\n\n"
            "You can view it again or continue with the next step."
        )
    return True, ""

def handle_start(sender_id: str, state: Dict[str, Any]):
    if state.get("step") == "senior_review" or str(state.get("handover", "false")).lower() == "true":
        return
    allowed, reason = can_start_new_audit(state)
    if not allowed and state.get("latest_audit"):
        send_dm(sender_id, reason, ["VIEW AUDIT", "SEE PREVIEW", "FIX THIS"])
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
            "I could not confirm it yet.\n\n"
            f"Please follow @{FOLLOW_ACCOUNT_USERNAME}, then tap I FOLLOWED again.\n\n"
            "Sometimes Instagram takes a few seconds to update."
        ), ["I FOLLOWED", "WHY FOLLOW", "CANCEL"])

def handle_profile_name(sender_id: str, state: Dict[str, Any], text: str):
    if len(text.strip()) < 2:
        send_dm(sender_id, "Please send the profile or brand name.")
        return
    state["profile_name"] = text.strip()[:80]
    state["step"] = "ask_profile_type"
    STORE.save_state(sender_id, state)
    send_dm(sender_id, msg_ask_type(state["profile_name"]))

def handle_profile_type(sender_id: str, state: Dict[str, Any], text: str):
    if len(text.strip()) < 2:
        send_dm(sender_id, "Please tell me what best describes this profile.")
        return
    state["profile_type_raw"] = text.strip()[:120]
    state["profile_category"] = detect_category(text)
    state["step"] = "ask_location_or_audience"
    STORE.save_state(sender_id, state)
    send_dm(sender_id, msg_ask_location())

def handle_location(sender_id: str, state: Dict[str, Any], text: str):
    if len(text.strip()) < 2:
        send_dm(sender_id, "Who should this profile attract?")
        return
    state["target_location_or_audience"] = text.strip()[:120]
    state["step"] = "ask_screenshot"
    STORE.save_state(sender_id, state)
    send_dm(sender_id, msg_ask_screenshot())

def handle_screenshot(sender_id: str, state: Dict[str, Any], image_url: str):
    send_dm(sender_id, "Got the screenshot 📸\n\nI’m reviewing the profile structure now.")
    send_typing_action(sender_id)
    state["step"] = "processing_audit"
    STORE.save_state(sender_id, state)

    image = download_image(image_url)
    if image is None:
        state["step"] = "ask_screenshot"
        STORE.save_state(sender_id, state)
        send_dm(sender_id, "I could not read that screenshot properly.\n\nPlease send a clearer Instagram profile screenshot.")
        return

    data = audit_profile(image, state)

    state["audit_score"] = str(data.get("overall_score", ""))
    state["main_gap"] = str(data.get("main_gap", ""))
    state["profile_gap"] = str(data.get("profile_identity", {}).get("fix", ""))
    state["content_gap"] = str(data.get("posts_reels", {}).get("fix", ""))
    state["trust_gap"] = str(data.get("trust_conversion", {}).get("fix", ""))
    state["cta_gap"] = str(data.get("bio_cta", {}).get("fix", ""))
    state["top_fix"] = str(data.get("top_fix", ""))
    state["preview_style"] = data.get("recommended_preview_style") or PROFILE_CATEGORIES.get(
        state.get("profile_category"), PROFILE_CATEGORIES["default_general_profile"]
    ).get("style")

    audit_messages = build_audit_messages(data, state)
    state["latest_audit"] = "\n\n---CB-AUDIT-SECTION---\n\n".join(audit_messages)
    state["lead_temperature"] = "warm"
    state["step"] = "audit_sent"
    state["cooldown_until"] = (datetime.now(timezone.utc) + timedelta(days=AUDIT_COOLDOWN_DAYS)).isoformat()
    STORE.save_state(sender_id, state)

    for msg in audit_messages:
        send_typing_action(sender_id)
        send_dm(sender_id, msg)
        time.sleep(1.8)

    send_dm(sender_id, msg_after_audit(), ["SEE PREVIEW", "FIX THIS", "VIEW AUDIT"])

def handle_latest(sender_id: str, state: Dict[str, Any]):
    audit = state.get("latest_audit")
    if not audit:
        send_dm(sender_id, "No audit is saved yet.\n\nSend GROWTH to start your free audit.", ["GROWTH"])
        return

    if "---CB-AUDIT-SECTION---" in audit:
        audit_parts = [p.strip() for p in audit.split("---CB-AUDIT-SECTION---") if p.strip()]
    else:
        audit_parts = split_text(audit, MAX_DM_CHARS)

    for part in audit_parts:
        for msg in split_text(part, MAX_DM_CHARS):
            send_dm(sender_id, msg)
            time.sleep(1.0)
    send_dm(sender_id, msg_after_audit(), ["SEE PREVIEW", "FIX THIS", "VIEW AUDIT"])

def handle_preview(sender_id: str, state: Dict[str, Any]):
    if not state.get("latest_audit"):
        send_dm(sender_id, "The preview needs the audit first.\n\nSend the Instagram profile screenshot so I can review it properly.")
        state["step"] = "ask_screenshot"
        STORE.save_state(sender_id, state)
        return
    if FOLLOW_REQUIRED and not is_follow_verified(sender_id, state, force=True):
        state["step"] = "follow_gate"
        STORE.save_state(sender_id, state)
        send_dm(sender_id, f"To unlock the free preview, follow @{FOLLOW_ACCOUNT_USERNAME} first.\n\nOnce done, tap I FOLLOWED.", ["I FOLLOWED", "WHY FOLLOW", "CANCEL"])
        return
    send_dm(sender_id, "Creating the profile preview now.\n\nIt will show the improved bio, highlights, and content grid from your audit.")
    state["step"] = "preview_processing"
    state["preview_requested"] = "true"
    state["lead_temperature"] = "hot"
    STORE.save_state(sender_id, state)
    filename = generate_preview_image(state)
    if PUBLIC_BASE_URL:
        image_url = f"{PUBLIC_BASE_URL}/preview/{filename}"
        send_image(sender_id, image_url)
    else:
        send_dm(sender_id, "The preview is prepared, but I could not send the image here. Please try SEE PREVIEW again in a moment.")
    state["preview_generated"] = "true"
    state["step"] = "preview_sent"
    STORE.save_state(sender_id, state)
    time.sleep(1.2)
    send_dm(sender_id, msg_after_preview(), ["FIX THIS", "VIEW AUDIT"])

def start_conversion(sender_id: str, state: Dict[str, Any], text: str = ""):
    state["lead_temperature"] = "hot"
    state["step"] = "conversion_pain"
    STORE.save_state(sender_id, state)
    msg = build_ai_conversion_message(state, text, "pain")
    send_dm(sender_id, msg, ["YES", "TELL ME", "NOT NOW"])

def advance_conversion(sender_id: str, state: Dict[str, Any], text: str, target_stage: Optional[str] = None):
    step = state.get("step")

    # Save the user's answer before moving forward.
    # This prevents goal/timeline/scope from being lost when a target stage is passed.
    if step == "conversion_goal":
        state["goal"] = text.strip()[:150]
    elif step == "conversion_timeline":
        state["timeline"] = text.strip()[:150]
    elif step == "conversion_scope":
        state["scope_preference"] = text.strip()[:200]

    if target_stage:
        stage = target_stage
    elif step == "conversion_pain":
        stage = "reframe"
    elif step == "conversion_reframe":
        stage = "solution"
    elif step == "conversion_solution":
        stage = "goal"
    elif step == "conversion_goal":
        stage = "timeline"
    elif step == "conversion_timeline":
        stage = "scope"
    elif step == "conversion_scope":
        stage = "ask_contact"
    else:
        stage = "pain"

    next_step_map = {
        "pain": "conversion_pain",
        "reframe": "conversion_reframe",
        "solution": "conversion_solution",
        "goal": "conversion_goal",
        "timeline": "conversion_timeline",
        "scope": "conversion_scope",
        "ask_contact": "ask_contact",
        "objection": step,
    }

    state["step"] = next_step_map.get(stage, state.get("step"))
    if stage in ["goal", "timeline", "scope", "ask_contact"]:
        state["lead_temperature"] = "very_hot"

    STORE.save_state(sender_id, state)

    msg = build_ai_conversion_message(state, text, stage)
    if stage in ["pain", "reframe", "solution"]:
        send_dm(sender_id, msg, ["YES", "TELL ME", "NOT NOW"])
    else:
        send_dm(sender_id, msg)

def handle_objection(sender_id: str, state: Dict[str, Any], text: str, objection_type: str):
    state["objection_type"] = objection_type
    state["lead_temperature"] = "hot"
    # Price/details objections should not ask contact directly. Move toward goal qualification.
    if objection_type in ["price_question", "asks_details", "asks_results", "budget_low", "trust_issue", "already_has_designer", "wants_to_do_self"]:
        state["step"] = "conversion_goal"
        STORE.save_state(sender_id, state)
        msg = build_ai_conversion_message(state, text, "objection")
        send_dm(sender_id, msg)
        return
    if objection_type in ["thinking_delay", "not_now"]:
        state["lead_temperature"] = "warm"
        STORE.save_state(sender_id, state)
        send_dm(sender_id, "No issue.\n\nKeep the audit as your starting point. If you want ClientBoost to map the fix properly later, reply FIX THIS.", ["FIX THIS", "VIEW AUDIT"])
        return
    start_conversion(sender_id, state, text)

def handle_contact(sender_id: str, state: Dict[str, Any], text: str):
    contact, ctype = contact_from_text(text)
    if not contact:
        send_dm(sender_id, "Send the best contact detail for you — WhatsApp number or email.")
        return
    state["contact_info"] = contact
    state["contact_type"] = ctype or "contact"
    if ctype == "phone":
        state["phone_number"] = contact
    state["handover"] = "true"
    state["lead_temperature"] = "senior_review"
    state["step"] = "senior_review"
    STORE.save_state(sender_id, state)
    STORE.save_final_lead_once(sender_id, latest_user_message=text)
    send_dm(sender_id, msg_senior_review())

def handle_unclear(sender_id: str, state: Dict[str, Any]):
    step = state.get("step", "new")
    if step == "follow_gate":
        send_dm(sender_id, "Choose one option:", ["I FOLLOWED", "WHY FOLLOW", "CANCEL"])
    elif step == "ask_profile_name":
        send_dm(sender_id, "What is the profile or brand name?")
    elif step == "ask_profile_type":
        send_dm(sender_id, "What best describes this Instagram profile?")
    elif step == "ask_location_or_audience":
        send_dm(sender_id, "Who should this profile attract?")
    elif step == "ask_screenshot":
        send_dm(sender_id, "Send a screenshot of the Instagram profile.")
    elif step == "audit_sent":
        send_dm(sender_id, "Choose the next step:", ["SEE PREVIEW", "FIX THIS", "VIEW AUDIT"])
    elif step == "preview_sent":
        send_dm(sender_id, "Choose the next step:", ["FIX THIS", "VIEW AUDIT"])
    elif step.startswith("conversion"):
        advance_conversion(sender_id, state, "yes")
    elif step == "ask_contact":
        send_dm(sender_id, "Send the best contact detail for you — WhatsApp number or email.")
    elif step == "senior_review":
        return
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

    # Owner reset always works.
    if text == OWNER_RESET_CODE:
        STORE.reset_state(sender_id)
        send_dm(sender_id, "Owner reset complete.\n\nSend GROWTH to test from the beginning.", ["GROWTH"])
        return

    # After senior review, automation stays silent. Human/senior strategist owns the chat.
    if state.get("step") == "senior_review" or str(state.get("handover", "false")).lower() == "true":
        return

    last_active = parse_iso(state.get("last_active", ""))
    if last_active and datetime.now(timezone.utc) - last_active > timedelta(days=SESSION_EXPIRY_DAYS):
        if state.get("step") not in ["audit_sent", "preview_sent"]:
            state["step"] = "new"
            STORE.save_state(sender_id, state)

    # Screenshot handling
    if image_url:
        if state.get("step") == "ask_screenshot":
            handle_screenshot(sender_id, state, image_url)
        else:
            if state.get("latest_audit"):
                send_dm(sender_id, "I received the image.\n\nYour latest audit is already active. Choose the next step:", ["SEE PREVIEW", "FIX THIS", "VIEW AUDIT"])
            else:
                send_dm(sender_id, "I received the image.\n\nFirst, send GROWTH so I can review it in the right order.", ["GROWTH"])
        return

    if not text:
        handle_unclear(sender_id, state)
        return

    step = state.get("step", "new")
    t_norm = normalize(text)

    # State-answer priority:
    # In these steps, the user's text is usually an answer, not a command.
    # This prevents names like "Budget Cafe" or goals like "more followers"
    # from being misread as objections or result questions.
    priority_commands = {"cancel", "reset", "restart"}

    if t_norm in priority_commands:
        intent = direct_intent(text)
    elif step == "ask_profile_name":
        intent = "answer_profile_name"
    elif step == "ask_profile_type":
        intent = "answer_profile_type"
    elif step == "ask_location_or_audience":
        intent = "answer_location_or_audience"
    elif step == "conversion_goal":
        intent = "answer_goal"
    elif step == "conversion_timeline":
        intent = "answer_timeline"
    elif step == "conversion_scope":
        intent = "answer_scope"
    elif step == "ask_contact" and contact_from_text(text)[0]:
        intent = "contact_sent"
    else:
        intent = direct_intent(text)

    if not intent:
        understood = ai_understand_message(state, text)
        if float(understood.get("confidence", 0) or 0) >= 0.65:
            intent = understood.get("intent")
            if understood.get("profile_category") in PROFILE_CATEGORIES:
                state["profile_category"] = understood.get("profile_category")
            if understood.get("clean_answer") and intent in ["answer_profile_type", "answer_location_or_audience", "answer_profile_name", "answer_goal", "answer_timeline", "answer_scope"]:
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
        send_dm(sender_id, "Cancelled.\n\nSend GROWTH whenever you are ready.", ["GROWTH"])
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
            send_dm(sender_id, reason, ["VIEW AUDIT", "SEE PREVIEW", "FIX THIS"])
    elif intent == "request_help":
        start_conversion(sender_id, state, text)
    elif intent in ["price_question", "asks_details", "trust_issue", "thinking_delay", "not_now", "already_has_designer", "wants_to_do_self", "budget_low", "asks_results"]:
        handle_objection(sender_id, state, text, intent)
    elif intent == "yes":
        if step in ["audit_sent", "preview_sent"]:
            start_conversion(sender_id, state, text)
        elif step in ["conversion_pain", "conversion_reframe", "conversion_solution"]:
            advance_conversion(sender_id, state, text)
        elif step == "conversion_goal":
            send_dm(sender_id, deterministic_conversion_message(state, "goal"))
        elif step == "conversion_timeline":
            send_dm(sender_id, deterministic_conversion_message(state, "timeline"))
        elif step == "conversion_scope":
            send_dm(sender_id, deterministic_conversion_message(state, "scope"))
        elif step == "ask_contact":
            send_dm(sender_id, "Send the best contact detail for you — WhatsApp number or email.")
        else:
            handle_unclear(sender_id, state)
    elif intent == "answer_goal":
        advance_conversion(sender_id, state, text, "timeline")
    elif intent == "answer_timeline":
        advance_conversion(sender_id, state, text, "scope")
    elif intent == "answer_scope":
        advance_conversion(sender_id, state, text, "ask_contact")
    elif intent == "contact_sent":
        if step == "ask_contact":
            handle_contact(sender_id, state, text)
        elif step in ["audit_sent", "preview_sent"]:
            start_conversion(sender_id, state, text)
        elif step in ["conversion_pain", "conversion_reframe", "conversion_solution", "conversion_goal", "conversion_timeline", "conversion_scope"]:
            advance_conversion(sender_id, state, text)
        else:
            handle_unclear(sender_id, state)
    else:
        # If user responds during conversion, keep moving one step at a time.
        if step in ["conversion_pain", "conversion_reframe", "conversion_solution", "conversion_goal", "conversion_timeline", "conversion_scope"]:
            advance_conversion(sender_id, state, text)
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
