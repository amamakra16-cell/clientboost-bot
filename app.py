# ============================================================
# ClientBoost - Instagram Growth Audit + Preview + Lead Funnel
# Single-file Render deployment version
# Core stack: Flask + Meta Instagram Messaging API + Gemini + Google Sheets + Pillow
# Professional strict-follow final version
# Version: clientboost-specialized-v4.2-professional-2026-05-07
# ============================================================

import os
import re
import io
import json
import time
import uuid
import hashlib
import textwrap
import threading
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests
from flask import Flask, request, jsonify, send_from_directory
from PIL import Image, ImageDraw, ImageFont, ImageOps

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
def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except Exception:
        return default


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ["true", "1", "yes", "y", "on"]


VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "clientboost2024")
PAGE_ACCESS_TOKEN = os.environ.get("PAGE_ACCESS_TOKEN", "")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")

OWNER_RESET_CODE = os.environ.get("OWNER_RESET_CODE", "CBRESET2026")
FOLLOW_REQUIRED = env_bool("FOLLOW_REQUIRED", True)
FOLLOW_ACCOUNT_USERNAME = os.environ.get("FOLLOW_ACCOUNT_USERNAME", "clientboost.in")
FOLLOW_VERIFY_MODE = os.environ.get("FOLLOW_VERIFY_MODE", "strict").strip().lower()
FOLLOW_CACHE_HOURS = env_int("FOLLOW_CACHE_HOURS", 24)
FOLLOW_RECHECK_EVERY_MESSAGE = env_bool("FOLLOW_RECHECK_EVERY_MESSAGE", True)

AUDIT_COOLDOWN_DAYS = env_int("AUDIT_COOLDOWN_DAYS", 7)
SESSION_EXPIRY_DAYS = env_int("SESSION_EXPIRY_DAYS", 3)

# Free-stack safe defaults. You can override env with higher models if your account supports them.
GEMINI_AUDIT_API_KEYS = [
    k.strip()
    for k in os.environ.get(
        "GEMINI_AUDIT_API_KEYS",
        os.environ.get("GEMINI_API_KEY", "")
    ).split(",")
    if k.strip()
]

GEMINI_CONVERSION_API_KEYS = [
    k.strip()
    for k in os.environ.get(
        "GEMINI_CONVERSION_API_KEYS",
        ",".join(GEMINI_AUDIT_API_KEYS)
    ).split(",")
    if k.strip()
]

GEMINI_AUDIT_MODELS = [
    m.strip()
    for m in os.environ.get(
        "GEMINI_AUDIT_MODELS",
        "gemini-2.5-flash,gemini-2.5-flash-lite"
    ).split(",")
    if m.strip()
]

GEMINI_CONVERSION_MODELS = [
    m.strip()
    for m in os.environ.get(
        "GEMINI_CONVERSION_MODELS",
        "gemini-2.5-flash,gemini-2.5-flash-lite"
    ).split(",")
    if m.strip()
]

GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID", "")
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")

GRAPH_API_VERSION = os.environ.get("GRAPH_API_VERSION", "v21.0")
PREVIEW_DIR = "/tmp/clientboost_previews"
os.makedirs(PREVIEW_DIR, exist_ok=True)

APP_VERSION = "clientboost-specialized-v4.2-professional-2026-05-07"

# ============================================================
# CONSTANTS
# ============================================================
STATE_TAB = "User_State"
LEADS_TAB = "Final_Leads"

MAX_DM_CHARS = 850
PREFERRED_DM_CHARS = 650

# Emoji are stored as Unicode escapes to prevent mojibake when copying/deploying.
EMO = {
    "wave": "\U0001F44B",
    "ok": "\u2705",
    "target": "\U0001F3AF",
    "camera": "\U0001F4F8",
    "eyes": "\U0001F440",
    "smile": "\U0001F642",
    "rocket": "\U0001F680",
    "star": "\u2B50",
    "zap": "\u26A1",
    "art": "\U0001F3A8",
    "down": "\U0001F447",
    "sad": "\U0001F615",
    "pin": "\U0001F4CD",
    "person": "\U0001F464",
    "write": "\u270D\uFE0F",
    "circle": "\u2B55",
    "film": "\U0001F3AC",
    "handshake": "\U0001F91D",
    "money": "\U0001F4B0",
    "lock": "\U0001F512",
    "spark": "\u2728",
}

USER_STATE_HEADERS = [
    "sender_id", "instagram_username", "step", "profile_name", "profile_type_raw",
    "profile_category", "target_location_or_audience", "latest_audit", "audit_score",
    "main_gap", "profile_gap", "content_gap", "trust_gap", "cta_gap", "top_fix",
    "preview_requested", "preview_generated", "preview_style", "preview_filename", "preview_url",
    "lead_temperature", "objection_type", "phone_number", "contact_info", "contact_type",
    "goal", "timeline", "scope_preference", "handover", "cooldown_until", "follow_verified",
    "follow_verified_at", "last_active", "final_saved", "state_json"
]

FINAL_LEADS_HEADERS = [
    "timestamp", "sender_id", "instagram_username", "profile_name", "profile_type_raw",
    "profile_category", "target_location_or_audience", "audit_score", "main_gap",
    "top_fix", "preview_requested", "preview_generated", "preview_style", "preview_url",
    "lead_temperature", "objection_type", "goal", "timeline", "scope_preference",
    "contact_info", "contact_type", "handover_status", "recommended_offer",
    "session_status", "latest_user_message"
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

YES_WORDS = {"yes", "y", "ok", "okay", "show me", "tell me", "explain"}

IDK_WORDS = {
    "idk", "i dont know", "i don't know", "not sure", "no idea",
    "confused", "you tell", "you suggest", "suggest"
}

OBJECTION_INTENTS = {
    "price_question", "asks_details", "trust_issue", "thinking_delay", "not_now",
    "already_has_designer", "wants_to_do_self", "budget_low", "asks_results"
}

PROCESSED_MIDS: Dict[str, float] = {}
PROCESSED_LOCK = threading.Lock()

# ============================================================
# PROFILE CATEGORIES
# ============================================================
PROFILE_CATEGORIES: Dict[str, Dict[str, Any]] = {
    "food_restaurant": {
        "keywords": ["restaurant", "cafe", "food", "bakery", "cloud kitchen", "takeaway", "delivery", "burger", "pizza", "juice", "hotel", "nosh", "biryani", "kitchen"],
        "style": "Warm Commercial",
        "highlights": ["Menu", "Reviews", "Kitchen", "Offers", "Order", "Location"],
        "grid": ["Food", "Review", "Offer", "Kitchen", "Bestseller", "Combo", "Order", "Behind", "CTA"],
        "angle": "orders, trust, craving, and WhatsApp conversion",
        "money_paths": ["orders", "catering", "offers", "repeat customers"],
    },
    "beauty_salon": {
        "keywords": ["salon", "beauty", "makeup", "bridal", "nails", "spa", "boutique", "fashion", "hair", "skincare"],
        "style": "Warm Commercial",
        "highlights": ["Services", "Results", "Pricing", "Reviews", "Bridal", "Booking"],
        "grid": ["Result", "Service", "Review", "Offer", "Process", "Before", "After", "FAQ", "Booking"],
        "angle": "bookings through transformation proof and trust",
        "money_paths": ["bookings", "bridal packages", "service bundles", "repeat clients"],
    },
    "fitness_wellness": {
        "keywords": ["gym", "fitness", "yoga", "trainer", "workout", "zumba", "crossfit", "nutrition", "wellness"],
        "style": "Modern Content Grid",
        "highlights": ["Programs", "Results", "Trainers", "Reviews", "Plans", "Join"],
        "grid": ["Workout", "Result", "Trainer", "Tip", "Plan", "Review", "Class", "Diet", "Join"],
        "angle": "membership enquiries, transformation proof, and authority",
        "money_paths": ["memberships", "personal training", "online plans", "challenges"],
    },
    "clinic_healthcare": {
        "keywords": ["clinic", "dental", "doctor", "hospital", "skin", "physio", "physiotherapy", "ayurveda", "health", "medical"],
        "style": "Premium Structured",
        "highlights": ["Treatments", "Doctor", "Results", "Reviews", "FAQ", "Book"],
        "grid": ["Care", "Treatment", "Review", "FAQ", "Doctor", "Tip", "Trust", "Result", "Book"],
        "angle": "patient trust, clarity, and appointment conversion",
        "money_paths": ["appointments", "consultations", "treatment plans", "local trust"],
    },
    "real_estate": {
        "keywords": ["real estate", "property", "plots", "land", "apartment", "villa", "realtor", "broker", "site", "flat"],
        "style": "Premium Structured",
        "highlights": ["Listings", "Site Visits", "Documents", "Reviews", "Areas", "Contact"],
        "grid": ["Property", "Area", "Docs", "Visit", "Trust", "Review", "Price", "Guide", "CTA"],
        "angle": "buyer trust, documentation clarity, and site visit leads",
        "money_paths": ["site visits", "buyer leads", "seller leads", "property trust"],
    },
    "ecommerce_retail": {
        "keywords": ["store", "shop", "ecommerce", "products", "clothing", "jewellery", "accessories", "electronics", "retail", "brand"],
        "style": "Warm Commercial",
        "highlights": ["Products", "Offers", "Reviews", "New", "Delivery", "Order"],
        "grid": ["Product", "Review", "Offer", "New", "Detail", "Use", "Proof", "Pack", "Order"],
        "angle": "product trust, repeat buying, and DM/WhatsApp orders",
        "money_paths": ["orders", "product drops", "repeat buyers", "offers"],
    },
    "automotive_service": {
        "keywords": ["car", "bike", "automobile", "detailing", "garage", "mechanic", "service center", "wash", "vehicle"],
        "style": "Warm Commercial",
        "highlights": ["Services", "Before", "After", "Reviews", "Pricing", "Book"],
        "grid": ["Service", "Before", "After", "Review", "Tip", "Process", "Offer", "Trust", "Book"],
        "angle": "service bookings through proof and transformation",
        "money_paths": ["service bookings", "detailing packages", "local enquiries", "repeat customers"],
    },
    "education_coaching": {
        "keywords": ["coaching", "classes", "school", "tuition", "academy", "course", "training", "institute", "teacher", "education"],
        "style": "Modern Content Grid",
        "highlights": ["Courses", "Results", "Students", "Reviews", "FAQ", "Enroll"],
        "grid": ["Lesson", "Result", "Tip", "Student", "Proof", "FAQ", "Review", "Value", "Enroll"],
        "angle": "admissions, trust, results, and student confidence",
        "money_paths": ["admissions", "courses", "workshops", "consultations"],
    },
    "professional_service": {
        "keywords": ["lawyer", "accountant", "architect", "interior", "consultant", "software", "finance", "insurance", "ca", "advocate"],
        "style": "Premium Structured",
        "highlights": ["Services", "Work", "Results", "Reviews", "FAQ", "Contact"],
        "grid": ["Service", "Case", "Proof", "Tip", "Review", "Process", "FAQ", "Result", "CTA"],
        "angle": "authority, credibility, and consultation enquiries",
        "money_paths": ["consultations", "client leads", "authority", "case enquiries"],
    },
    "agency_service": {
        "keywords": ["agency", "marketing", "social media", "ads", "seo", "branding", "automation", "digital"],
        "style": "Premium Structured",
        "highlights": ["Services", "Results", "Proof", "Process", "FAQ", "Contact"],
        "grid": ["Offer", "Result", "Proof", "Tip", "Process", "Case", "CTA", "Trust", "Lead"],
        "angle": "high-trust lead generation and authority",
        "money_paths": ["lead generation", "retainers", "consultations", "projects"],
    },
    "local_service": {
        "keywords": ["plumber", "electrician", "cleaning", "repair", "pest control", "home service", "technician", "local service"],
        "style": "Warm Commercial",
        "highlights": ["Services", "Before", "After", "Reviews", "Pricing", "Call"],
        "grid": ["Service", "Before", "After", "Review", "Tip", "Problem", "Fix", "Offer", "Call"],
        "angle": "local calls, urgency, trust, and service bookings",
        "money_paths": ["calls", "bookings", "local trust", "repeat clients"],
    },
    "hospitality_travel": {
        "keywords": ["hotel", "resort", "travel", "tour", "homestay", "airbnb", "stay", "trip"],
        "style": "Warm Commercial",
        "highlights": ["Rooms", "Reviews", "Location", "Offers", "Food", "Book"],
        "grid": ["Stay", "Room", "Review", "View", "Offer", "Food", "Guide", "Proof", "Book"],
        "angle": "booking confidence, location appeal, and guest trust",
        "money_paths": ["room bookings", "tour packages", "guest trust", "seasonal offers"],
    },
    "event_wedding": {
        "keywords": ["event", "wedding", "planner", "decor", "catering", "dj", "venue", "birthday"],
        "style": "Warm Commercial",
        "highlights": ["Work", "Packages", "Reviews", "Decor", "Events", "Book"],
        "grid": ["Event", "Decor", "Review", "Package", "Before", "After", "Story", "Proof", "Book"],
        "angle": "event enquiries through visual proof and trust",
        "money_paths": ["event enquiries", "packages", "premium bookings", "referrals"],
    },
    "home_lifestyle_business": {
        "keywords": ["furniture", "decor", "home", "interior decor", "kitchenware", "lifestyle store"],
        "style": "Warm Commercial",
        "highlights": ["Products", "Rooms", "Reviews", "New", "Delivery", "Order"],
        "grid": ["Product", "Room", "Review", "New", "Detail", "Use", "Proof", "Style", "Order"],
        "angle": "lifestyle trust, product desire, and order enquiries",
        "money_paths": ["orders", "catalogue sales", "room styling", "repeat buyers"],
    },
    "influencer_creator": {
        "keywords": ["influencer", "creator", "blogger", "reels", "lifestyle", "vlogger", "content creator"],
        "style": "Modern Content Grid",
        "highlights": ["About", "Reels", "Collabs", "Media Kit", "Results", "Contact"],
        "grid": ["Hook", "Story", "Value", "Lifestyle", "Reel", "Collab", "Insight", "Proof", "CTA"],
        "angle": "identity, audience retention, and collaboration enquiries",
        "money_paths": ["brand deals", "paid promos", "affiliate", "digital products"],
    },
    "personal_brand": {
        "keywords": ["coach", "mentor", "speaker", "founder", "personal brand", "consultant", "public figure"],
        "style": "Modern Content Grid",
        "highlights": ["About", "Work", "Results", "Content", "Reviews", "Contact"],
        "grid": ["Insight", "Story", "Proof", "Tip", "Value", "Offer", "Result", "Trust", "CTA"],
        "angle": "authority, trust, and inbound enquiries",
        "money_paths": ["consulting", "coaching", "courses", "speaking"],
    },
    "religious_education_content": {
        "keywords": ["islamic", "quran", "dua", "duas", "hadith", "religious", "reminder", "spiritual", "deen", "islam", "haqiqat", "haqikat", "allah", "muslim", "surah"],
        "style": "Editorial Creator",
        "highlights": ["Help", "Quran", "Surah", "Duas", "Lessons", "About"],
        "grid": ["Reminder", "Quran", "Dua", "Lesson", "Reel", "Carousel", "Story", "Series", "Follow"],
        "angle": "saves, shares, trust, and clearer educational identity",
        "money_paths": ["sponsored posts", "paid promotions", "affiliate", "digital products", "community"],
    },
    "entertainment_meme_page": {
        "keywords": ["meme", "memes", "entertainment", "funny", "comedy", "viral", "fan page", "theme page"],
        "style": "Editorial Creator",
        "highlights": ["Best", "Reels", "Series", "About", "Viral", "Contact"],
        "grid": ["Meme", "Reel", "Trend", "Relatable", "Series", "Story", "Hook", "CTA", "Follow"],
        "angle": "shareability, repeat formats, and audience memory",
        "money_paths": ["paid promos", "shoutouts", "affiliate", "theme-page offers"],
    },
    "artist_portfolio": {
        "keywords": ["artist", "art", "portfolio", "painting", "illustration", "designer", "creative", "craft"],
        "style": "Editorial Creator",
        "highlights": ["Work", "BTS", "Process", "Reviews", "Shop", "Contact"],
        "grid": ["Work", "Story", "Detail", "BTS", "Process", "Style", "Review", "Series", "CTA"],
        "angle": "portfolio trust, visual identity, and commissions",
        "money_paths": ["commissions", "prints", "workshops", "brand work"],
    },
    "photographer_videographer": {
        "keywords": ["photographer", "videographer", "photo", "video", "cinematographer", "shoot", "wedding film"],
        "style": "Editorial Creator",
        "highlights": ["Work", "Weddings", "Reels", "Reviews", "Packages", "Contact"],
        "grid": ["Shoot", "Story", "Detail", "BTS", "Reel", "Work", "Review", "Process", "CTA"],
        "angle": "portfolio clarity, booking confidence, and premium enquiry flow",
        "money_paths": ["shoot bookings", "wedding packages", "brand shoots", "retainers"],
    },
    "fashion_lifestyle_creator": {
        "keywords": ["fashion", "lifestyle", "model", "outfit", "ootd", "style", "beauty influencer"],
        "style": "Editorial Creator",
        "highlights": ["Looks", "Reels", "Collabs", "Brands", "About", "Contact"],
        "grid": ["Look", "Story", "Style", "Reel", "Trend", "Collab", "Tip", "Proof", "CTA"],
        "angle": "aesthetic identity, audience retention, and brand collaborations",
        "money_paths": ["brand deals", "affiliate", "paid collabs", "UGC"],
    },
    "knowledge_creator": {
        "keywords": ["knowledge", "facts", "finance tips", "business tips", "learning", "educational content", "explain"],
        "style": "Modern Content Grid",
        "highlights": ["Topics", "Best", "Series", "Resources", "About", "Contact"],
        "grid": ["Hook", "Explain", "Tip", "Carousel", "Myth", "Value", "Series", "Proof", "Follow"],
        "angle": "saves, shares, clarity, and repeatable learning series",
        "money_paths": ["digital products", "affiliate", "sponsors", "paid community"],
    },
    "motivation_quotes_page": {
        "keywords": ["motivation", "quotes", "quote page", "success", "mindset", "inspiration"],
        "style": "Editorial Creator",
        "highlights": ["Quotes", "Reels", "Stories", "Series", "About", "Contact"],
        "grid": ["Quote", "Reel", "Story", "Lesson", "Reminder", "Carousel", "Series", "Value", "Follow"],
        "angle": "shareable identity, memorable content series, and follow conversion",
        "money_paths": ["paid promos", "affiliate", "digital products", "theme-page network"],
    },
    "community_page": {
        "keywords": ["community", "city page", "local page", "updates", "events", "public"],
        "style": "Editorial Creator",
        "highlights": ["Updates", "Events", "People", "Places", "About", "Contact"],
        "grid": ["Update", "Event", "Place", "Story", "People", "Guide", "News", "Feature", "Follow"],
        "angle": "local trust, community engagement, and repeat visits",
        "money_paths": ["local sponsorships", "paid features", "event promos", "business listings"],
    },
    "nonprofit_social_page": {
        "keywords": ["ngo", "nonprofit", "charity", "foundation", "social work", "cause"],
        "style": "Editorial Creator",
        "highlights": ["Mission", "Work", "Impact", "People", "Donate", "Contact"],
        "grid": ["Cause", "Story", "Impact", "People", "Proof", "Need", "Update", "Trust", "CTA"],
        "angle": "trust, mission clarity, and supporter action",
        "money_paths": ["donations", "supporters", "campaigns", "partnerships"],
    },
    "news_media_local": {
        "keywords": ["news", "media", "updates", "journal", "daily update", "local news"],
        "style": "Editorial Creator",
        "highlights": ["News", "Local", "Events", "Videos", "About", "Contact"],
        "grid": ["News", "Update", "Explainer", "Video", "Local", "Alert", "Story", "Trust", "Follow"],
        "angle": "credibility, clarity, and repeat audience behavior",
        "money_paths": ["sponsors", "ads", "paid features", "community deals"],
    },
    "default_general_profile": {
        "keywords": [],
        "style": "Premium Structured",
        "highlights": ["About", "Work", "Proof", "FAQ", "Contact"],
        "grid": ["Intro", "Value", "Proof", "Tip", "Story", "Offer", "FAQ", "Result", "CTA"],
        "angle": "clearer positioning, trust, and profile conversion",
        "money_paths": ["leads", "collabs", "paid offers", "community"],
    },
}

# ============================================================
# BASIC UTILS
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

    cleaned = text.strip().replace("```json", "```").replace("```JSON", "```")
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()

    try:
        return json.loads(cleaned)
    except Exception:
        pass

    match = re.search(r"\{.*\}", cleaned, re.S)
    if match:
        try:
            return json.loads(match.group(0))
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


def short_text(value: Any, limit: int = 360) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text[:limit].strip()


def phone_from_text(text: str) -> Optional[str]:
    digits = re.sub(r"\D", "", text or "")
    if len(digits) >= 10:
        if len(digits) <= 12:
            return digits[-10:]
        return digits
    return None


def email_from_text(text: str) -> Optional[str]:
    match = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text or "")
    return match.group(0) if match else None


def contact_from_text(text: str) -> Tuple[Optional[str], Optional[str]]:
    email = email_from_text(text)
    if email:
        return email, "email"

    phone = phone_from_text(text)
    if phone:
        return phone, "phone"

    return None, None


def safe_username(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9_.]", "", normalize(value).replace(" ", ""))
    return cleaned[:24] or "yourprofile"


def readable_category(category: str) -> str:
    return (category or "default_general_profile").replace("_", " ").title()


def category_config(category: str) -> Dict[str, Any]:
    return PROFILE_CATEGORIES.get(category or "", PROFILE_CATEGORIES["default_general_profile"])


def is_creator_or_page_category(category: str) -> bool:
    return category in {
        "religious_education_content", "entertainment_meme_page", "knowledge_creator",
        "motivation_quotes_page", "community_page", "news_media_local",
        "influencer_creator", "personal_brand", "fashion_lifestyle_creator",
        "artist_portfolio", "nonprofit_social_page"
    }


def format_list(items: List[Any], max_items: int = 5) -> str:
    clean = []
    for item in items or []:
        text = short_text(item, 80)
        if text:
            clean.append(text)
    clean = clean[:max_items]
    if not clean:
        return "- Not clearly readable"
    return "\n".join([f"- {x}" for x in clean])

# ============================================================
# EMOJI / TEXT REPAIR
# ============================================================
def fix_mojibake(text: str) -> str:
    if not text:
        return ""

    markers = ["ðŸ", "âœ", "âš", "â€", "Â", "ã€"]
    if not any(marker in text for marker in markers):
        return text

    original_bad_count = sum(text.count(marker) for marker in markers)
    best = text

    for encoding in ["cp1252", "latin1"]:
        try:
            repaired = text.encode(encoding, errors="ignore").decode("utf-8", errors="ignore")
            repaired_bad_count = sum(repaired.count(marker) for marker in markers)
            if repaired and repaired_bad_count < original_bad_count:
                best = repaired
                break
        except Exception:
            continue

    return best


def has_emoji(text: str) -> bool:
    return bool(re.search(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", text or ""))


def clean_dm_text(text: str, limit: int = MAX_DM_CHARS) -> str:
    text = fix_mojibake(text or "")
    text = text.replace("```", "").strip()
    text = re.sub(r"\n{3,}", "\n\n", text)

    blocked_patterns = [
        r"\bbackend\b",
        r"\bautomation\b",
        r"\bAI model\b",
        r"\bGemini\b",
        r"\bquota\b",
        r"\bcooldown\b",
        r"\bapi\b",
        r"\btoken\b",
    ]
    for pattern in blocked_patterns:
        text = re.sub(pattern, "", text, flags=re.I)

    text = re.sub(r"[ \t]{2,}", " ", text).strip()

    if text and not has_emoji(text):
        text = f"{EMO['smile']} {text}"

    return text[:limit].strip()

# ============================================================
# STATE HELPERS
# ============================================================
def blank_extra_state() -> Dict[str, Any]:
    return {
        "audit_json": "{}",
        "last_user_message": "",
        "last_bot_message": "",
        "pre_follow_step": "",
    }


def clear_audit_flow_fields(state: Dict[str, Any]) -> Dict[str, Any]:
    keep = {
        "sender_id": state.get("sender_id", ""),
        "instagram_username": state.get("instagram_username", ""),
        "follow_verified": state.get("follow_verified", "false"),
        "follow_verified_at": state.get("follow_verified_at", ""),
        "last_active": state.get("last_active", now_iso()),
    }

    state.update({
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
        "preview_filename": "",
        "preview_url": "",
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
        "final_saved": "false",
        "audit_json": "{}",
        "last_user_message": "",
        "last_bot_message": "",
        "pre_follow_step": "",
    })

    state.update(keep)
    return state


def get_audit_json(state: Dict[str, Any]) -> Dict[str, Any]:
    raw = state.get("audit_json", "{}")
    if isinstance(raw, dict):
        return raw
    return safe_json_loads(raw, {})


def set_audit_json(state: Dict[str, Any], data: Dict[str, Any]) -> None:
    state["audit_json"] = json.dumps(data or {}, ensure_ascii=False)


def calculate_recommended_offer(state: Dict[str, Any]) -> str:
    category = state.get("profile_category", "default_general_profile")
    goal = normalize(state.get("goal", ""))
    scope = normalize(state.get("scope_preference", ""))
    score_raw = str(state.get("audit_score", ""))
    score = int(float(score_raw)) if score_raw.replace(".", "", 1).isdigit() else 0

    if is_creator_or_page_category(category):
        if any(x in goal for x in ["money", "monet", "paid", "sponsor", "promo", "brand", "affiliate"]):
            return "Page Monetization Growth Map"
        if "full" in scope or "manage" in scope or "system" in scope:
            return "Creator/Page Growth System"
        return "Profile + Content Direction Map"

    if "full" in scope or "manage" in scope or "system" in scope:
        return "Full Growth System"

    if any(x in goal for x in ["sales", "booking", "enquir", "lead", "client", "customer", "order"]):
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
        state = {
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
            "preview_filename": "",
            "preview_url": "",
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
        state.update(blank_extra_state())
        return state

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
            data.update(blank_extra_state())
            data.update(extra)
            return data

        except Exception as e:
            print(f"get_state failed: {e}")
            return MEMORY_STORE.get(sender_id, self._blank_state(sender_id))

    def save_state(self, sender_id: str, state: Dict[str, Any]):
        state["sender_id"] = sender_id
        state["last_active"] = now_iso()

        row_obj = {header: str(state.get(header, "")) for header in USER_STATE_HEADERS}
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

            values = [[row_obj.get(header, "") for header in USER_STATE_HEADERS]]

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
            "preview_url": state.get("preview_url", ""),
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
            self.leads_ws.append_row([row.get(header, "") for header in FINAL_LEADS_HEADERS], value_input_option="RAW")
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
def send_typing_action(recipient_id: str) -> None:
    if not PAGE_ACCESS_TOKEN:
        return

    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/me/messages"
    payload = {"recipient": {"id": recipient_id}, "sender_action": "typing_on"}

    try:
        requests.post(url, params={"access_token": PAGE_ACCESS_TOKEN}, json=payload, timeout=5)
    except Exception:
        pass


def post_to_meta(url: str, payload: Dict[str, Any], timeout: int = 15) -> Dict[str, Any]:
    last_response: Dict[str, Any] = {}

    for _ in range(2):
        try:
            response = requests.post(
                url,
                params={"access_token": PAGE_ACCESS_TOKEN},
                json=payload,
                timeout=timeout,
            )

            if response.status_code < 300:
                return response.json() if response.text else {}

            print("Meta send error", response.status_code, response.text)
            last_response = {"error": response.text, "status_code": response.status_code}

        except Exception as e:
            print(f"Meta send failed: {e}")
            last_response = {"error": str(e)}

        time.sleep(0.7)

    return last_response


def send_dm(recipient_id: str, text: str, quick_replies: Optional[List[str]] = None, delay: float = 0.9) -> Dict[str, Any]:
    if not PAGE_ACCESS_TOKEN:
        print("PAGE_ACCESS_TOKEN missing. DM not sent.")
        return {"error": "missing token"}

    text = clean_dm_text(text, limit=5000)
    parts = split_text(text, MAX_DM_CHARS)

    final_response: Dict[str, Any] = {}

    for index, part in enumerate(parts):
        send_typing_action(recipient_id)
        sleep_time = min(2.4, max(delay, len(part) / 520))
        time.sleep(sleep_time)

        url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/me/messages"
        message: Dict[str, Any] = {"text": part}

        if quick_replies and index == len(parts) - 1:
            message["quick_replies"] = [
                {
                    "content_type": "text",
                    "title": title[:20],
                    "payload": title.upper().replace(" ", "_"),
                }
                for title in quick_replies[:13]
            ]

        payload = {
            "recipient": {"id": recipient_id},
            "message": message,
            "messaging_type": "RESPONSE",
        }

        final_response = post_to_meta(url, payload)

        if len(parts) > 1:
            time.sleep(0.6)

    return final_response


def send_image(recipient_id: str, image_url: str) -> Dict[str, Any]:
    if not PAGE_ACCESS_TOKEN:
        print("PAGE_ACCESS_TOKEN missing. Image not sent.")
        return {"error": "missing token"}

    send_typing_action(recipient_id)
    time.sleep(1.1)

    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/me/messages"
    payload = {
        "recipient": {"id": recipient_id},
        "message": {
            "attachment": {
                "type": "image",
                "payload": {"url": image_url},
            }
        },
        "messaging_type": "RESPONSE",
    }

    return post_to_meta(url, payload)


def download_image(image_url: str) -> Optional[Image.Image]:
    try:
        headers = {"Authorization": f"Bearer {PAGE_ACCESS_TOKEN}"} if PAGE_ACCESS_TOKEN else {}
        response = requests.get(image_url, headers=headers, timeout=25)
        response.raise_for_status()
        image = Image.open(io.BytesIO(response.content))
        image = ImageOps.exif_transpose(image)
        return image.convert("RGB")
    except Exception as e:
        print(f"download_image failed: {e}")
        return None


def get_instagram_profile(sender_id: str) -> Dict[str, Any]:
    if not PAGE_ACCESS_TOKEN:
        return {}

    fields = "username,profile_pic,is_user_follow_business,is_business_follow_user"
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{sender_id}"

    try:
        response = requests.get(
            url,
            params={"fields": fields, "access_token": PAGE_ACCESS_TOKEN},
            timeout=15,
        )

        if response.status_code >= 300:
            print("profile check error", response.status_code, response.text)
            return {}

        return response.json()

    except Exception as e:
        print(f"get_instagram_profile failed: {e}")
        return {}


def is_follow_verified(sender_id: str, state: Dict[str, Any], force: bool = False) -> bool:
    if not FOLLOW_REQUIRED:
        return True

    if not force and str(state.get("follow_verified", "false")).lower() == "true":
        verified_at = parse_iso(state.get("follow_verified_at", ""))
        if verified_at and datetime.now(timezone.utc) - verified_at < timedelta(hours=FOLLOW_CACHE_HOURS):
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


def should_strict_follow_gate(state: Dict[str, Any], intent: Optional[str]) -> bool:
    if not FOLLOW_REQUIRED:
        return False

    if FOLLOW_VERIFY_MODE != "strict":
        return False

    if not FOLLOW_RECHECK_EVERY_MESSAGE:
        return False

    step = state.get("step", "new")

    allow_intents = {
        "owner_reset", "follow_confirmed", "generic_done", "ask_why_follow",
        "cancel", "soft_reset", "start_audit"
    }

    if intent in allow_intents:
        return False

    if step == "follow_gate":
        return False

    protected_steps = {
        "ask_profile_name", "ask_profile_type", "ask_goal_or_audience",
        "ask_screenshot", "processing_audit", "audit_sent",
        "preview_processing", "preview_sent", "conversion_pain",
        "conversion_reframe", "conversion_solution", "conversion_goal",
        "conversion_timeline", "conversion_scope", "ask_contact",
        "senior_review"
    }

    if step in protected_steps:
        return True

    if intent in {
        "request_preview", "request_help", "request_latest", "yes",
        "answer_goal", "answer_timeline", "answer_scope", "contact_sent"
    }:
        return True

    return False


def enforce_follow_if_needed(sender_id: str, state: Dict[str, Any], intent: Optional[str]) -> bool:
    if should_strict_follow_gate(state, intent):
        if not is_follow_verified(sender_id, state, force=True):
            current_step = state.get("step", "new")

            if current_step != "follow_gate" and not state.get("pre_follow_step"):
                state["pre_follow_step"] = current_step

            state["step"] = "follow_gate"
            STORE.save_state(sender_id, state)

            send_dm(sender_id, msg_follow_gate_strict(), ["I FOLLOWED", "WHY FOLLOW", "CANCEL"])
            return True

    return False

# ============================================================
# GEMINI
# ============================================================
def prepare_image_for_audit(image: Image.Image) -> Image.Image:
    image = ImageOps.exif_transpose(image).convert("RGB")
    width, height = image.size

    max_side = max(width, height)
    if max_side < 1200:
        scale = 1200 / max_side
        new_size = (int(width * scale), int(height * scale))
        image = image.resize(new_size, Image.Resampling.LANCZOS)
    elif max_side > 2200:
        scale = 2200 / max_side
        new_size = (int(width * scale), int(height * scale))
        image = image.resize(new_size, Image.Resampling.LANCZOS)

    return image


def gemini_generate(prompt: str, image: Optional[Image.Image] = None, purpose: str = "conversion") -> Optional[str]:
    if genai is None:
        print("google-generativeai not installed")
        return None

    keys = GEMINI_AUDIT_API_KEYS if purpose == "audit" else GEMINI_CONVERSION_API_KEYS
    models = GEMINI_AUDIT_MODELS if purpose == "audit" else GEMINI_CONVERSION_MODELS

    if not keys:
        print(f"No Gemini keys for {purpose}")
        return None

    generation_config = {
        "temperature": 0.12 if purpose == "audit" else 0.35,
        "top_p": 0.9,
        "top_k": 40,
    }

    last_error = None

    for model_name in models:
        for key in keys:
            try:
                genai.configure(api_key=key)
                model = genai.GenerativeModel(model_name, generation_config=generation_config)
                content = [prompt, image] if image is not None else prompt
                response = model.generate_content(content)
                text = getattr(response, "text", None)

                if text and text.strip():
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
    value = normalize(text)

    priority = [
        "religious_education_content",
        "food_restaurant",
        "real_estate",
        "beauty_salon",
        "clinic_healthcare",
        "photographer_videographer",
        "artist_portfolio",
        "knowledge_creator",
        "motivation_quotes_page",
        "entertainment_meme_page",
        "fashion_lifestyle_creator",
        "community_page",
        "news_media_local",
    ]

    for category in priority + [c for c in PROFILE_CATEGORIES if c not in priority]:
        cfg = PROFILE_CATEGORIES[category]
        for keyword in cfg.get("keywords", []):
            if keyword in value:
                return category

    if "page" in value:
        return "knowledge_creator"

    return "default_general_profile"


def direct_intent(text: str) -> Optional[str]:
    value = normalize(text)

    if not value:
        return None

    if OWNER_RESET_CODE and text.strip() == OWNER_RESET_CODE:
        return "owner_reset"

    if value in DIRECT_COMMANDS:
        return DIRECT_COMMANDS[value]

    if any(x in value for x in ["price", "cost", "how much", "charges", "package", "rate", "fees"]):
        return "price_question"

    if any(x in value for x in ["details", "tell me more", "services", "what do you offer", "what will you do", "process"]):
        return "asks_details"

    if any(x in value for x in ["designer", "editor", "social media person", "agency already", "team already"]):
        return "already_has_designer"

    if any(x in value for x in ["think", "maybe", "later"]):
        return "thinking_delay"

    if any(x in value for x in ["not now", "busy", "after month", "next month", "just exploring"]):
        return "not_now"

    if any(x in value for x in ["can i do", "do myself", "i can manage", "diy"]):
        return "wants_to_do_self"

    if any(x in value for x in ["budget", "expensive", "costly", "cheap", "no money"]):
        return "budget_low"

    if any(x in value for x in ["is this real", "scam", "proof", "who are you", "can i trust"]):
        return "trust_issue"

    if any(x in value for x in ["result", "guarantee", "followers", "leads", "how fast"]):
        return "asks_results"

    if any(x in value for x in ["money", "monetize", "monetise", "sponsor", "paid promo", "affiliate"]):
        return "money_goal"

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
handover: {state.get('handover')}

User message:
{user_text}

Classify intent as one of:
start_audit, follow_confirmed, ask_why_follow, cancel,
answer_profile_name, answer_profile_type, answer_goal_or_audience,
request_preview, request_help, request_latest, request_new_audit,
price_question, asks_details, trust_issue, thinking_delay, not_now,
already_has_designer, wants_to_do_self, budget_low, asks_results,
money_goal, answer_goal, answer_timeline, answer_scope, contact_sent,
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
        return {
            "intent": "unclear",
            "confidence": 0.0,
            "profile_category": "",
            "clean_answer": user_text,
            "objection_type": "none",
        }

    return data

# ============================================================
# AUDIT
# ============================================================
def audit_prompt(state: Dict[str, Any], retry_reason: str = "") -> str:
    current_category = state.get("profile_category") or detect_category(
        f"{state.get('profile_name', '')} {state.get('profile_type_raw', '')}"
    )
    cfg = category_config(current_category)

    retry_note = ""
    if retry_reason:
        retry_note = f"""
Previous attempt problem:
{retry_reason}

Redo the audit. Do NOT give generic template advice.
""".strip()

    return f"""
You are a senior Instagram growth strategist for ClientBoost.

Task:
Audit the Instagram profile screenshot with visible evidence only.
This must feel like the screenshot was actually reviewed.

User inputs:
Profile or brand name: {state.get('profile_name')}
Profile type: {state.get('profile_type_raw')}
User goal: {state.get('target_location_or_audience')}
Current inferred category: {current_category}
Category angle: {cfg.get('angle')}

{retry_note}

Strict rules:
- Return ONLY valid JSON.
- No markdown.
- No fake guarantees.
- No generic template audit.
- Do not say "bio needs clarity" if the bio is already clear.
- If the profile has strengths, mention them clearly.
- Use exact visible details from the screenshot when readable: username, profile name, bio words, highlight labels, post/reel topics, visual style.
- If some text is too small, say "not clearly readable" for that exact field.
- Be fair. Score based on visible evidence.
- For creator/theme pages, do not force bookings/sales. Think saves, shares, followers, trust, monetization, paid promos, sponsors, affiliate, digital product, community.
- Keep each field short and specific.

Use this scoring:
1. Name + username clarity /10
2. Profile photo clarity /10
3. Bio positioning /10
4. CTA/action path /10
5. Highlights labels /10
6. Highlight cover quality /10
7. Grid clarity /10
8. Reels/hooks /10
9. Trust/proof/authority /10
10. Audience fit/conversion/monetization /10

Required JSON schema:
{{
  "readability": "high|medium|low",
  "detected_category": "one allowed category id",
  "username_seen": "exact username visible or not clearly readable",
  "profile_name_seen": "exact visible profile name or not clearly readable",
  "bio_seen": "short exact visible bio summary or not clearly readable",
  "highlight_labels_seen": ["visible highlight label 1", "visible highlight label 2"],
  "post_topics_seen": ["visible post/reel topic 1", "visible post/reel topic 2"],
  "visual_style_seen": "specific visual style observation",
  "overall_score": 0,
  "main_gap": "main growth gap based on visible evidence",
  "strength_summary": "what is already working based on screenshot",
  "profile_identity": {{
    "score": 0,
    "evidence": "specific visible evidence",
    "strength": "what is good",
    "gap": "what is missing or weak",
    "fix": "specific improvement"
  }},
  "bio_cta": {{
    "score": 0,
    "evidence": "specific visible evidence",
    "strength": "what is good",
    "gap": "what is missing or weak",
    "fix": "specific improvement"
  }},
  "highlights": {{
    "score": 0,
    "evidence": "specific visible evidence",
    "strength": "what is good",
    "gap": "what is missing or weak",
    "fix": "specific improvement"
  }},
  "posts_reels": {{
    "score": 0,
    "evidence": "specific visible evidence",
    "strength": "what is good",
    "gap": "what is missing or weak",
    "fix": "specific improvement"
  }},
  "trust_conversion": {{
    "score": 0,
    "evidence": "specific visible evidence",
    "strength": "what is good",
    "gap": "what is missing or weak",
    "fix": "specific improvement"
  }},
  "top_fix": "one first priority fix",
  "monetization_direction": "specific monetization path if this is a creator/page, otherwise business conversion path",
  "preview": {{
    "bio_line_1": "specific improved bio line",
    "bio_line_2": "specific improved bio line",
    "cta_line": "specific CTA/action line",
    "highlight_labels": ["6 specific highlight labels"],
    "content_tiles": ["9 specific post/reel tile labels"],
    "content_pillars": ["3 specific pillars"],
    "monetization_angle": "specific angle"
  }},
  "recommended_preview_style": "{cfg.get('style')}"
}}

Allowed category ids:
{', '.join(PROFILE_CATEGORIES.keys())}
""".strip()


def audit_is_specific(data: Dict[str, Any]) -> Tuple[bool, str]:
    if not data or not isinstance(data, dict):
        return False, "No valid JSON returned."

    readability = normalize(data.get("readability", ""))
    if readability == "low":
        return False, "The screenshot readability was low."

    specific_fields = [
        data.get("username_seen", ""),
        data.get("profile_name_seen", ""),
        data.get("bio_seen", ""),
        data.get("visual_style_seen", ""),
        data.get("strength_summary", ""),
        data.get("main_gap", ""),
    ]

    specific_fields.extend(data.get("highlight_labels_seen", []) or [])
    specific_fields.extend(data.get("post_topics_seen", []) or [])

    section_keys = ["profile_identity", "bio_cta", "highlights", "posts_reels", "trust_conversion"]
    for key in section_keys:
        section = data.get(key, {}) if isinstance(data.get(key), dict) else {}
        specific_fields.extend([
            section.get("evidence", ""),
            section.get("strength", ""),
            section.get("gap", ""),
            section.get("fix", ""),
        ])

    generic_phrases = [
        "not clear enough",
        "needs clearer structure",
        "visitors understand, trust, and act",
        "bio and action path need more clarity",
        "grid needs stronger content pillars",
        "trust and conversion signals are not strong enough",
        "not clearly visible",
    ]

    useful_count = 0
    for value in specific_fields:
        text = normalize(str(value))
        if len(text) < 8:
            continue
        if text in ["not clearly readable", "not clearly visible"]:
            continue
        if any(phrase in text for phrase in generic_phrases):
            continue
        useful_count += 1

    if useful_count < 6:
        return False, f"Audit looked generic. Useful evidence count: {useful_count}"

    preview = data.get("preview", {}) if isinstance(data.get("preview"), dict) else {}
    if len(preview.get("content_tiles", []) or []) < 5:
        return False, "Preview content tiles were missing."

    return True, ""


def normalize_audit_data(data: Dict[str, Any], fallback_category: str) -> Dict[str, Any]:
    detected_category = data.get("detected_category")
    if detected_category not in PROFILE_CATEGORIES:
        detected_category = fallback_category or "default_general_profile"

    cfg = category_config(detected_category)

    data["detected_category"] = detected_category
    data["readability"] = data.get("readability") or "medium"

    for field in ["highlight_labels_seen", "post_topics_seen"]:
        if not isinstance(data.get(field), list):
            data[field] = []

    for field in [
        "username_seen", "profile_name_seen", "bio_seen", "visual_style_seen",
        "main_gap", "strength_summary", "top_fix", "monetization_direction"
    ]:
        data[field] = short_text(data.get(field), 380)

    section_keys = ["profile_identity", "bio_cta", "highlights", "posts_reels", "trust_conversion"]
    for key in section_keys:
        section = data.get(key)
        if not isinstance(section, dict):
            section = {}

        try:
            score = int(float(section.get("score", 0)))
        except Exception:
            score = 5

        section["score"] = max(0, min(10, score))
        section["evidence"] = short_text(section.get("evidence"), 360)
        section["strength"] = short_text(section.get("strength"), 360)
        section["gap"] = short_text(section.get("gap"), 360)
        section["fix"] = short_text(section.get("fix"), 360)
        data[key] = section

    try:
        overall = int(float(data.get("overall_score", 0)))
    except Exception:
        overall = sum(data[key]["score"] for key in section_keys) * 2

    data["overall_score"] = max(0, min(100, overall))

    preview = data.get("preview")
    if not isinstance(preview, dict):
        preview = {}

    highlight_labels = preview.get("highlight_labels")
    if not isinstance(highlight_labels, list) or len(highlight_labels) < 4:
        highlight_labels = cfg.get("highlights", [])[:6]

    content_tiles = preview.get("content_tiles")
    if not isinstance(content_tiles, list) or len(content_tiles) < 6:
        content_tiles = cfg.get("grid", [])[:9]

    content_pillars = preview.get("content_pillars")
    if not isinstance(content_pillars, list) or len(content_pillars) < 3:
        content_pillars = ["Identity", "Value", "Action"]

    preview["bio_line_1"] = short_text(preview.get("bio_line_1") or f"{data.get('profile_name_seen') or 'Profile'} | {readable_category(detected_category)}", 90)
    preview["bio_line_2"] = short_text(preview.get("bio_line_2") or data.get("main_gap") or cfg.get("angle"), 90)
    preview["cta_line"] = short_text(preview.get("cta_line") or "Follow for clear, useful content", 90)
    preview["highlight_labels"] = [short_text(x, 18) for x in highlight_labels[:6]]
    preview["content_tiles"] = [short_text(x, 22) for x in content_tiles[:9]]
    preview["content_pillars"] = [short_text(x, 24) for x in content_pillars[:3]]
    preview["monetization_angle"] = short_text(preview.get("monetization_angle") or data.get("monetization_direction") or cfg.get("angle"), 100)

    data["preview"] = preview
    data["recommended_preview_style"] = data.get("recommended_preview_style") or cfg.get("style")

    return data


def audit_profile(image: Image.Image, state: Dict[str, Any]) -> Dict[str, Any]:
    image = prepare_image_for_audit(image)

    fallback_category = state.get("profile_category") or detect_category(
        f"{state.get('profile_name', '')} {state.get('profile_type_raw', '')}"
    )

    retry_reason = ""

    for _ in range(2):
        prompt = audit_prompt(state, retry_reason=retry_reason)
        text = gemini_generate(prompt, image=image, purpose="audit")
        data = extract_json(text or "", {})

        if not data:
            retry_reason = "Model did not return valid JSON."
            continue

        data = normalize_audit_data(data, fallback_category)
        ok, reason = audit_is_specific(data)

        if ok:
            return data

        retry_reason = reason

    return {
        "_error": "audit_not_specific",
        "message": retry_reason or "The screenshot could not be audited specifically enough.",
    }


def build_audit_messages(data: Dict[str, Any], state: Dict[str, Any]) -> List[str]:
    profile_name = state.get("profile_name") or data.get("profile_name_seen") or "This profile"
    profile_type = state.get("profile_type_raw") or readable_category(data.get("detected_category", ""))
    goal = state.get("target_location_or_audience") or "growth"
    score = str(data.get("overall_score", "0"))

    messages = [
        (
            f"{EMO['pin']} CLIENTBOOST AUDIT\n\n"
            f"Profile: {profile_name}\n"
            f"Type: {profile_type}\n"
            f"Goal: {goal}\n\n"
            f"{EMO['star']} Score: {score}/100\n\n"
            f"What I can see:\n"
            f"- Username: {data.get('username_seen') or 'Not clearly readable'}\n"
            f"- Bio: {data.get('bio_seen') or 'Not clearly readable'}\n"
            f"- Style: {data.get('visual_style_seen') or 'Not clearly readable'}\n\n"
            f"Main gap:\n{data.get('main_gap')}"
        )
    ]

    if data.get("strength_summary"):
        messages.append(
            f"{EMO['ok']} WHAT IS ALREADY WORKING\n\n"
            f"{data.get('strength_summary')}\n\n"
            f"Visible highlights:\n{format_list(data.get('highlight_labels_seen', []), 5)}\n\n"
            f"Visible post/reel topics:\n{format_list(data.get('post_topics_seen', []), 5)}"
        )

    section_map = [
        (EMO["person"], "PROFILE IDENTITY", "profile_identity"),
        (EMO["write"], "BIO + ACTION", "bio_cta"),
        (EMO["circle"], "HIGHLIGHTS", "highlights"),
        (EMO["film"], "POSTS + REELS", "posts_reels"),
        (EMO["handshake"], "TRUST + MONETIZATION", "trust_conversion"),
    ]

    for icon, title, key in section_map:
        section = data.get(key, {})
        messages.append(
            f"{icon} {title}\n\n"
            f"Score: {section.get('score', 0)}/10\n\n"
            f"Evidence:\n{section.get('evidence') or 'Not clearly readable'}\n\n"
            f"Working:\n{section.get('strength') or 'Not clearly visible'}\n\n"
            f"Gap:\n{section.get('gap') or 'Not clearly visible'}\n\n"
            f"Fix:\n{section.get('fix') or 'Make this section more specific.'}"
        )

    messages.append(
        f"{EMO['zap']} FIRST FIX\n\n"
        f"Start here:\n{data.get('top_fix')}\n\n"
        f"Money/growth direction:\n{data.get('monetization_direction') or 'Build clearer trust and action path.'}"
    )

    final_messages: List[str] = []
    for message in messages:
        if len(message) <= PREFERRED_DM_CHARS:
            final_messages.append(message)
        else:
            final_messages.extend(split_text(message, PREFERRED_DM_CHARS))

    return final_messages

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


def text_width(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def draw_wrapped(
    draw: ImageDraw.ImageDraw,
    text: str,
    xy: Tuple[int, int],
    font,
    fill,
    max_width: int,
    line_gap: int = 7,
    max_lines: int = 4,
) -> int:
    words = (text or "").split()
    lines: List[str] = []
    current = ""

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


def draw_centered(draw: ImageDraw.ImageDraw, text: str, center_x: int, y: int, font, fill):
    width = text_width(draw, text, font)
    draw.text((center_x - width / 2, y), text, font=font, fill=fill)


def rounded_rect(draw, box, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def style_palette(style: str) -> Dict[str, Tuple[int, int, int]]:
    if style == "Warm Commercial":
        return {"bg": (245, 236, 224), "screen": (255, 255, 255), "text": (20, 20, 20), "muted": (92, 92, 92), "accent": (179, 89, 42), "soft": (239, 219, 198), "tile": (255, 248, 240)}

    if style == "Modern Content Grid":
        return {"bg": (20, 23, 30), "screen": (12, 14, 18), "text": (245, 245, 245), "muted": (178, 183, 190), "accent": (118, 160, 255), "soft": (35, 41, 54), "tile": (24, 28, 38)}

    if style == "Editorial Creator":
        return {"bg": (238, 233, 224), "screen": (255, 255, 252), "text": (28, 28, 28), "muted": (93, 88, 82), "accent": (92, 78, 59), "soft": (225, 218, 205), "tile": (248, 244, 236)}

    return {"bg": (239, 242, 248), "screen": (255, 255, 255), "text": (18, 23, 31), "muted": (94, 102, 114), "accent": (25, 88, 210), "soft": (225, 233, 248), "tile": (248, 250, 255)}


def preview_filename_for(sender_id: str, state: Dict[str, Any]) -> str:
    basis = f"{sender_id}|{state.get('profile_name')}|{state.get('latest_audit')}|{state.get('audit_score')}"
    digest = hashlib.sha1(basis.encode("utf-8", errors="ignore")).hexdigest()[:24]
    return f"preview_{digest}.jpg"


def preview_file_exists(filename: str) -> bool:
    return bool(filename and os.path.exists(os.path.join(PREVIEW_DIR, filename)))


def normalize_preview_list(items: Any, fallback: List[str], count: int) -> List[str]:
    clean: List[str] = []

    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                text = item.get("title") or item.get("label") or item.get("name") or ""
            else:
                text = str(item)
            text = short_text(text, 22)
            if text:
                clean.append(text)

    for item in fallback:
        if len(clean) >= count:
            break
        text = short_text(item, 22)
        if text and text not in clean:
            clean.append(text)

    return clean[:count]


def grid_subtitle(label: str) -> str:
    value = normalize(label)
    if any(x in value for x in ["review", "proof", "trust", "result"]):
        return "Trust"
    if any(x in value for x in ["dua", "quran", "hadith", "lesson", "tip", "explain", "value"]):
        return "Save"
    if any(x in value for x in ["reel", "hook", "trend"]):
        return "Reach"
    if any(x in value for x in ["offer", "price", "order", "book", "cta", "contact", "promo"]):
        return "Action"
    if any(x in value for x in ["story", "behind", "bts"]):
        return "Human"
    return "Content"


def generate_preview_image(state: Dict[str, Any], sender_id: str) -> str:
    audit = get_audit_json(state)

    category = audit.get("detected_category") or state.get("profile_category") or "default_general_profile"
    cfg = category_config(category)

    preview = audit.get("preview", {}) if isinstance(audit.get("preview"), dict) else {}
    style = state.get("preview_style") or audit.get("recommended_preview_style") or cfg.get("style", "Premium Structured")
    pal = style_palette(style)

    filename = preview_filename_for(sender_id, state)
    path = os.path.join(PREVIEW_DIR, filename)

    W, H = 1080, 1350
    img = Image.new("RGB", (W, H), pal["bg"])
    draw = ImageDraw.Draw(img)

    font_xl = load_font(42, True)
    font_lg = load_font(34, True)
    font_md = load_font(27, True)
    font_body = load_font(24, False)
    font_sm = load_font(19, False)
    font_xs = load_font(16, False)

    text = pal["text"]
    muted = pal["muted"]
    accent = pal["accent"]
    soft = pal["soft"]
    screen = pal["screen"]
    tile = pal["tile"]

    draw.text((70, 45), "ClientBoost Profile Direction", font=font_xl, fill=text)
    draw.text((72, 96), readable_category(category), font=font_sm, fill=muted)

    sx, sy = 70, 140
    sw, sh = 940, 1110
    rounded_rect(draw, (sx, sy, sx + sw, sy + sh), 44, screen)

    profile_name = short_text(state.get("profile_name") or audit.get("profile_name_seen") or "Your Profile", 34)
    username_display = safe_username(profile_name)

    y = sy + 38
    draw.text((sx + 44, y), username_display, font=font_lg, fill=text)
    draw.text((sx + sw - 88, y + 2), "menu", font=font_sm, fill=muted)

    y += 78
    cx, cy = sx + 104, y + 62
    draw.ellipse((cx - 58, cy - 58, cx + 58, cy + 58), fill=soft, outline=accent, width=5)
    initials = re.sub(r"[^A-Za-z0-9]", "", profile_name)[:2].upper() or "CB"
    draw_centered(draw, initials, cx, cy - 18, font_md, accent)

    stats = [("Bio", "Sharper"), ("6", "Highlights"), ("9", "Post ideas")]
    stats_x = sx + 255
    gap = 190

    for idx, (top, bottom) in enumerate(stats):
        px = stats_x + idx * gap
        draw_centered(draw, top, px, y + 25, font_md, text)
        draw_centered(draw, bottom, px, y + 64, font_sm, muted)

    y += 145
    draw.text((sx + 44, y), profile_name, font=font_md, fill=text)
    y += 38

    bio_line_1 = short_text(preview.get("bio_line_1") or f"{profile_name} | {readable_category(category)}", 95)
    bio_line_2 = short_text(preview.get("bio_line_2") or audit.get("main_gap") or cfg.get("angle"), 95)
    cta_line = short_text(preview.get("cta_line") or "Follow for clear, useful content", 95)

    y = draw_wrapped(draw, bio_line_1, (sx + 44, y), font_body, text, sw - 88, max_lines=2)
    y = draw_wrapped(draw, bio_line_2, (sx + 44, y + 2), font_sm, muted, sw - 88, max_lines=2)
    draw.text((sx + 44, y + 8), cta_line, font=font_sm, fill=accent)
    y += 52

    button_labels = ["Position", "Proof", "Action"]
    btn_w = (sw - 110) // 3

    for i, label in enumerate(button_labels):
        bx = sx + 44 + i * (btn_w + 11)
        rounded_rect(draw, (bx, y, bx + btn_w, y + 44), 12, soft)
        draw_centered(draw, label, int(bx + btn_w / 2), y + 11, font_sm, text)

    y += 76

    highlights = normalize_preview_list(preview.get("highlight_labels"), cfg.get("highlights", []), 6)
    spacing = (sw - 88) // 6
    for i, label in enumerate(highlights):
        hx = sx + 44 + i * spacing + spacing // 2
        draw.ellipse((hx - 37, y, hx + 37, y + 74), fill=screen, outline=accent, width=3)
        icon = label[:1].upper()
        draw_centered(draw, icon, hx, y + 24, font_sm, accent)
        draw_centered(draw, label[:10], hx, y + 86, font_xs, muted)

    y += 135

    pillars = normalize_preview_list(preview.get("content_pillars"), ["Identity", "Value", "Action"], 3)
    pillar_w = (sw - 100) // 3
    for i, pillar in enumerate(pillars):
        px = sx + 44 + i * (pillar_w + 6)
        rounded_rect(draw, (px, y, px + pillar_w, y + 50), 14, tile, outline=soft, width=1)
        draw_centered(draw, pillar[:18], int(px + pillar_w / 2), y + 14, font_sm, text)

    y += 74

    draw.line((sx + 44, y, sx + sw - 44, y), fill=soft, width=2)
    y += 20
    draw_centered(draw, "Posts", sx + sw // 6, y, font_sm, accent)
    draw_centered(draw, "Reels", sx + sw // 2, y, font_sm, muted)
    draw_centered(draw, "Proof", sx + (sw * 5) // 6, y, font_sm, muted)
    y += 48

    content_tiles = normalize_preview_list(preview.get("content_tiles"), cfg.get("grid", []), 9)
    tile_gap = 6
    tile_size = (sw - 88 - 2 * tile_gap) // 3

    for idx, label in enumerate(content_tiles):
        row, col = divmod(idx, 3)
        x = sx + 44 + col * (tile_size + tile_gap)
        yy = y + row * (tile_size + tile_gap)

        fill = tile if idx % 2 == 0 else screen
        draw.rectangle((x, yy, x + tile_size, yy + tile_size), fill=fill, outline=pal["bg"], width=2)
        draw.rectangle((x, yy, x + tile_size, yy + 56), fill=accent)

        draw_centered(draw, label[:16], int(x + tile_size / 2), yy + tile_size // 2 - 25, font_sm, text)
        subtitle = grid_subtitle(label)
        draw_centered(draw, subtitle, int(x + tile_size / 2), yy + tile_size // 2 + 18, font_xs, muted)

    footer = short_text(preview.get("monetization_angle") or audit.get("monetization_direction") or "Built from your audit", 90)
    footer_fill = (80, 80, 80) if pal["bg"] != (20, 23, 30) else (210, 210, 210)
    draw_centered(draw, footer, W // 2, H - 64, font_sm, footer_fill)

    img.save(path, "JPEG", quality=92)
    return filename

# ============================================================
# FLOW MESSAGES
# ============================================================
def msg_follow_gate() -> str:
    return (
        f"{EMO['wave']} To unlock your free audit, follow @{FOLLOW_ACCOUNT_USERNAME} first.\n\n"
        "After that, tap I FOLLOWED."
    )


def msg_follow_gate_strict() -> str:
    return (
        f"{EMO['lock']} Please follow @{FOLLOW_ACCOUNT_USERNAME} to continue.\n\n"
        "The audit, preview, and next steps stay unlocked only while you are following.\n\n"
        "After following, tap I FOLLOWED."
    )


def msg_why_follow() -> str:
    return (
        f"{EMO['ok']} The audit is free, but it takes real review time.\n\n"
        "Following ClientBoost helps us keep the free audit available for more people.\n\n"
        f"Follow @{FOLLOW_ACCOUNT_USERNAME}, then tap I FOLLOWED."
    )


def msg_start_audit() -> str:
    return (
        f"Great {EMO['wave']}\n\n"
        "I will check your Instagram profile like a growth strategist.\n\n"
        "First, what name should I use for the profile or brand?"
    )


def msg_ask_type(profile_name: str) -> str:
    return (
        f"Noted - {profile_name} {EMO['ok']}\n\n"
        "What is this profile about?\n\n"
        "A simple answer is enough: Islamic page, restaurant, creator, clinic, shop, coach, real estate, meme page, etc."
    )


def msg_ask_goal() -> str:
    return (
        f"Got it {EMO['target']}\n\n"
        "What do you want this profile to bring you?\n\n"
        "Examples: followers, trust, paid promos, sponsors, customers, bookings, sales, clients, brand deals, or monetization."
    )


def msg_ask_screenshot() -> str:
    return (
        f"Perfect {EMO['camera']}\n\n"
        "Now send one screenshot of the Instagram profile.\n\n"
        "Make sure it shows the bio, highlights, follower area, and first posts."
    )


def msg_after_audit(data: Dict[str, Any]) -> str:
    main_gap = data.get("main_gap") or "the profile needs a stronger growth direction"
    return (
        f"{EMO['ok']} Your audit is ready.\n\n"
        f"Main focus:\n{main_gap}\n\n"
        "What do you want to do next?"
    )


def msg_after_preview() -> str:
    return (
        f"{EMO['ok']} This is the profile direction.\n\n"
        "It is built from your audit: bio, highlights, content ideas, and growth angle.\n\n"
        "Want ClientBoost to map the fix properly?"
    )


def msg_senior_review() -> str:
    return (
        f"Done {EMO['ok']}\n\n"
        "Your profile case is saved for senior ClientBoost review.\n\n"
        "Your audit, preview direction, goal, and contact details are already noted."
    )

# ============================================================
# CONVERSION MESSAGES
# ============================================================
def deterministic_conversion_message(state: Dict[str, Any], stage: str, user_text: str = "") -> str:
    audit = get_audit_json(state)
    category = state.get("profile_category") or audit.get("detected_category") or "default_general_profile"
    cfg = category_config(category)

    main_gap = state.get("main_gap") or audit.get("main_gap") or "the profile needs a clearer growth direction"
    top_fix = state.get("top_fix") or audit.get("top_fix") or "build a clearer profile, content direction, and action path"
    goal = normalize(state.get("goal") or user_text)
    is_page = is_creator_or_page_category(category)

    if stage == "pain":
        if is_page:
            return (
                f"I see the real issue {EMO['eyes']}\n\n"
                "For this type of page, the goal is not just posting more.\n\n"
                f"The gap is:\n{main_gap}\n\n"
                "Want me to show the growth direction?"
            )

        return (
            f"I see the real issue {EMO['eyes']}\n\n"
            f"The gap is:\n{main_gap}\n\n"
            "That is where profiles silently lose trust, enquiries, or sales.\n\n"
            "Want me to show the fix?"
        )

    if stage == "reframe":
        if is_page:
            paths = ", ".join(cfg.get("money_paths", [])[:4])
            return (
                f"Exactly {EMO['ok']}\n\n"
                "A page grows when people remember it, save it, share it, and trust the theme.\n\n"
                f"Money path can be: {paths}.\n\n"
                "Should I show the first fix?"
            )

        return (
            f"Exactly {EMO['ok']}\n\n"
            "More posts will not help if the profile foundation is weak.\n\n"
            "First we fix the profile, proof, content direction, and action path.\n\n"
            "Should I show the first fix?"
        )

    if stage == "solution":
        return (
            f"Here is the first move {EMO['zap']}\n\n"
            f"{top_fix}\n\n"
            "After that, content becomes easier because every post has a purpose.\n\n"
            "Want me to check what support fits you?"
        )

    if stage == "goal":
        if is_page:
            return (
                f"What matters most now? {EMO['target']}\n\n"
                "Reply with one:\n"
                "Followers / Shares / Paid promos / Sponsors / Affiliate / Digital product / Community"
            )

        return (
            f"What matters most now? {EMO['target']}\n\n"
            "Reply with one:\n"
            "Followers / Enquiries / Sales / Bookings / Trust / Brand deals"
        )

    if stage == "timeline":
        if is_page and any(x in goal for x in ["money", "monet", "paid", "sponsor", "affiliate"]):
            return (
                f"Got it {EMO['money']}\n\n"
                "For a page, monetization usually needs 3 things: clear theme, repeatable content, and a reason for brands/people to trust it.\n\n"
                "How soon do you want to improve this?"
            )

        return (
            f"Got it {EMO['ok']}\n\n"
            "How soon do you want to improve this?\n\n"
            "Reply: immediately, this week, this month, or just exploring."
        )

    if stage == "scope":
        if is_page:
            return (
                f"That helps {EMO['ok']}\n\n"
                "For this page, do you want a simple profile + content direction first, or a full page growth and monetization system?"
            )

        return (
            f"That helps {EMO['ok']}\n\n"
            "Do you want only the profile structure fixed first, or do you want ClientBoost to handle the full growth system too?"
        )

    if stage == "ask_contact":
        return (
            f"Perfect {EMO['ok']}\n\n"
            "This is worth reviewing properly with a senior ClientBoost strategist.\n\n"
            "Send your best contact detail - WhatsApp number or email."
        )

    if stage == "objection":
        return objection_reply(state, user_text)

    return (
        f"Fair point {EMO['ok']}\n\n"
        f"The key gap is:\n{main_gap}\n\n"
        "Before suggesting the next step, what result matters most?"
    )


def objection_reply(state: Dict[str, Any], user_text: str) -> str:
    category = state.get("profile_category") or get_audit_json(state).get("detected_category") or "default_general_profile"
    is_page = is_creator_or_page_category(category)
    value = normalize(user_text)

    if any(x in value for x in ["price", "cost", "how much", "charges", "fees", "package"]):
        return (
            f"The audit and preview are free {EMO['ok']}\n\n"
            "If you want ClientBoost to do the fix, cost depends on the scope.\n\n"
            "First tell me your main goal so the right plan is suggested."
        )

    if any(x in value for x in ["result", "guarantee", "followers", "leads", "how fast"]):
        if is_page:
            return (
                f"Fair question {EMO['target']}\n\n"
                "For a page, growth depends on theme clarity, content consistency, saves, shares, and audience memory.\n\n"
                "What matters most: followers, shares, sponsors, or monetization?"
            )

        return (
            f"Fair question {EMO['target']}\n\n"
            "No honest agency should fake-guarantee numbers. The right move is to fix the profile path first, then content and conversion.\n\n"
            "What result matters most?"
        )

    if any(x in value for x in ["trust", "real", "scam", "proof"]):
        return (
            f"Valid concern {EMO['ok']}\n\n"
            "That is why we start with a free audit and preview before suggesting anything paid.\n\n"
            "What do you want this profile to achieve first?"
        )

    if any(x in value for x in ["budget", "expensive", "cheap", "no money"]):
        return (
            f"No issue {EMO['smile']}\n\n"
            "Start with the free audit direction first. The paid scope can be small or full depending on what you actually need.\n\n"
            "What is your main goal?"
        )

    return deterministic_conversion_message(state, "goal", user_text)


def build_ai_conversion_message(state: Dict[str, Any], user_text: str, stage: str) -> str:
    return deterministic_conversion_message(state, stage, user_text)

# ============================================================
# FLOW HANDLERS
# ============================================================
def can_start_new_audit(state: Dict[str, Any]) -> Tuple[bool, str]:
    until = parse_iso(state.get("cooldown_until", ""))
    if until and until > datetime.now(timezone.utc):
        return False, (
            f"{EMO['ok']} I already have your recent audit here.\n\n"
            "You can view it again, see the preview, or start fixing it."
        )
    return True, ""


def handle_start(sender_id: str, state: Dict[str, Any]):
    if state.get("step") == "senior_review" or str(state.get("handover", "false")).lower() == "true":
        handle_after_handover(sender_id, state, "growth")
        return

    allowed, reason = can_start_new_audit(state)

    if not allowed and state.get("latest_audit"):
        send_dm(sender_id, reason, ["VIEW AUDIT", "SEE PREVIEW", "FIX THIS"])
        return

    if FOLLOW_REQUIRED and not is_follow_verified(sender_id, state, force=(FOLLOW_VERIFY_MODE == "strict")):
        state["step"] = "follow_gate"
        STORE.save_state(sender_id, state)
        send_dm(sender_id, msg_follow_gate(), ["I FOLLOWED", "WHY FOLLOW", "CANCEL"])
        return

    state = clear_audit_flow_fields(state)
    state["step"] = "ask_profile_name"
    state["lead_temperature"] = "cold"
    STORE.save_state(sender_id, state)

    send_dm(sender_id, msg_start_audit())


def handle_follow_confirmed(sender_id: str, state: Dict[str, Any]):
    verified = is_follow_verified(sender_id, state, force=True)

    if not verified and FOLLOW_VERIFY_MODE == "strict":
        state["step"] = "follow_gate"
        STORE.save_state(sender_id, state)
        send_dm(
            sender_id,
            (
                f"I could not confirm it yet {EMO['eyes']}\n\n"
                f"Please follow @{FOLLOW_ACCOUNT_USERNAME}, then tap I FOLLOWED again.\n\n"
                "Sometimes Instagram takes a few seconds to update."
            ),
            ["I FOLLOWED", "WHY FOLLOW", "CANCEL"]
        )
        return

    previous_step = state.get("pre_follow_step", "")
    state["pre_follow_step"] = ""

    if previous_step and previous_step not in ["new", "follow_gate"]:
        state["step"] = previous_step
        STORE.save_state(sender_id, state)

        if str(state.get("handover", "false")).lower() == "true" or previous_step == "senior_review":
            send_dm(sender_id, f"Verified {EMO['ok']}\n\nYour profile case is already saved for senior ClientBoost review.")
            return

        if previous_step == "ask_profile_name":
            send_dm(sender_id, msg_start_audit())
            return

        if previous_step == "ask_profile_type":
            send_dm(sender_id, msg_ask_type(state.get("profile_name") or "your profile"))
            return

        if previous_step == "ask_goal_or_audience":
            send_dm(sender_id, msg_ask_goal())
            return

        if previous_step == "ask_screenshot":
            send_dm(sender_id, msg_ask_screenshot())
            return

        if previous_step == "audit_sent":
            send_dm(sender_id, f"Verified {EMO['ok']}\n\nYou can continue from your audit.", ["SEE PREVIEW", "FIX THIS", "VIEW AUDIT"])
            return

        if previous_step == "preview_sent":
            send_dm(sender_id, f"Verified {EMO['ok']}\n\nYou can continue from your preview.", ["FIX THIS", "VIEW AUDIT"])
            return

        if previous_step.startswith("conversion"):
            send_dm(sender_id, f"Verified {EMO['ok']}\n\nWe can continue from where we stopped.")
            return

        if previous_step == "ask_contact":
            send_dm(sender_id, f"Verified {EMO['ok']}\n\nSend your best contact detail - WhatsApp number or email.")
            return

        send_dm(sender_id, f"Verified {EMO['ok']}\n\nYou can continue now.")
        return

    state = clear_audit_flow_fields(state)
    state["step"] = "ask_profile_name"
    STORE.save_state(sender_id, state)
    send_dm(sender_id, msg_start_audit())


def handle_profile_name(sender_id: str, state: Dict[str, Any], text: str):
    if len(text.strip()) < 2:
        send_dm(sender_id, f"Please send the profile or brand name {EMO['smile']}")
        return

    state["profile_name"] = text.strip()[:80]
    state["step"] = "ask_profile_type"
    STORE.save_state(sender_id, state)
    send_dm(sender_id, msg_ask_type(state["profile_name"]))


def handle_profile_type(sender_id: str, state: Dict[str, Any], text: str):
    if len(text.strip()) < 2:
        send_dm(sender_id, f"Please tell me what this profile is about {EMO['smile']}")
        return

    state["profile_type_raw"] = text.strip()[:120]
    combined = f"{state.get('profile_name', '')} {text}"
    state["profile_category"] = detect_category(combined)
    state["step"] = "ask_goal_or_audience"
    STORE.save_state(sender_id, state)
    send_dm(sender_id, msg_ask_goal())


def handle_goal_or_audience(sender_id: str, state: Dict[str, Any], text: str):
    if len(text.strip()) < 2:
        send_dm(sender_id, f"What do you want from this profile - followers, trust, paid promos, sponsors, customers, sales, or brand deals? {EMO['target']}")
        return

    state["target_location_or_audience"] = text.strip()[:160]
    state["step"] = "ask_screenshot"
    STORE.save_state(sender_id, state)
    send_dm(sender_id, msg_ask_screenshot())


def handle_screenshot(sender_id: str, state: Dict[str, Any], image_url: str):
    send_dm(sender_id, f"Got the screenshot {EMO['camera']}\n\nI am checking the real visible details now. This can take a few seconds.", delay=1.0)

    state["step"] = "processing_audit"
    STORE.save_state(sender_id, state)

    image = download_image(image_url)

    if image is None:
        state["step"] = "ask_screenshot"
        STORE.save_state(sender_id, state)
        send_dm(sender_id, f"I could not read that screenshot properly {EMO['sad']}\n\nPlease send a clearer Instagram profile screenshot.")
        return

    data = audit_profile(image, state)

    if data.get("_error"):
        state["step"] = "ask_screenshot"
        STORE.save_state(sender_id, state)
        send_dm(
            sender_id,
            (
                f"I do not want to send a generic audit {EMO['eyes']}\n\n"
                "This screenshot was not read specifically enough.\n\n"
                "Please send a clearer full profile screenshot showing the username, bio, highlights, and first posts."
            )
        )
        return

    detected_category = data.get("detected_category")
    if detected_category in PROFILE_CATEGORIES:
        state["profile_category"] = detected_category

    state["audit_score"] = str(data.get("overall_score", ""))
    state["main_gap"] = str(data.get("main_gap", ""))
    state["profile_gap"] = str(data.get("profile_identity", {}).get("fix", ""))
    state["content_gap"] = str(data.get("posts_reels", {}).get("fix", ""))
    state["trust_gap"] = str(data.get("trust_conversion", {}).get("fix", ""))
    state["cta_gap"] = str(data.get("bio_cta", {}).get("fix", ""))
    state["top_fix"] = str(data.get("top_fix", ""))
    state["preview_style"] = data.get("recommended_preview_style") or category_config(state.get("profile_category")).get("style")

    state["preview_filename"] = ""
    state["preview_url"] = ""
    state["preview_generated"] = "false"

    set_audit_json(state, data)

    audit_messages = build_audit_messages(data, state)
    state["latest_audit"] = "\n\n---CB-AUDIT-SECTION---\n\n".join(audit_messages)
    state["lead_temperature"] = "warm"
    state["step"] = "audit_sent"
    state["cooldown_until"] = (datetime.now(timezone.utc) + timedelta(days=AUDIT_COOLDOWN_DAYS)).isoformat()
    STORE.save_state(sender_id, state)

    for message in audit_messages:
        send_dm(sender_id, message, delay=1.1)
        time.sleep(0.7)

    send_dm(sender_id, msg_after_audit(data), ["SEE PREVIEW", "FIX THIS", "VIEW AUDIT"])


def handle_latest(sender_id: str, state: Dict[str, Any]):
    audit = state.get("latest_audit")

    if not audit:
        send_dm(sender_id, f"No audit is saved yet {EMO['smile']}\n\nSend GROWTH to start your free audit.", ["GROWTH"])
        return

    if "---CB-AUDIT-SECTION---" in audit:
        audit_parts = [part.strip() for part in audit.split("---CB-AUDIT-SECTION---") if part.strip()]
    else:
        audit_parts = split_text(audit, MAX_DM_CHARS)

    for part in audit_parts:
        for message in split_text(part, MAX_DM_CHARS):
            send_dm(sender_id, message, delay=1.0)
            time.sleep(0.7)

    send_dm(sender_id, f"{EMO['ok']} That is the audit again.\n\nWhat would you like next?", ["SEE PREVIEW", "FIX THIS"])


def handle_preview(sender_id: str, state: Dict[str, Any]):
    if not state.get("latest_audit"):
        send_dm(
            sender_id,
            f"The preview comes after the audit {EMO['smile']}\n\nSend GROWTH first, then I will ask 3 easy questions and review the profile properly.",
            ["GROWTH"]
        )
        state["step"] = "new"
        STORE.save_state(sender_id, state)
        return

    if FOLLOW_REQUIRED and not is_follow_verified(sender_id, state, force=(FOLLOW_VERIFY_MODE == "strict")):
        state["step"] = "follow_gate"
        STORE.save_state(sender_id, state)
        send_dm(sender_id, msg_follow_gate_strict(), ["I FOLLOWED", "WHY FOLLOW", "CANCEL"])
        return

    expected_filename = preview_filename_for(sender_id, state)
    filename = state.get("preview_filename") or expected_filename

    if not preview_file_exists(filename):
        if str(state.get("preview_generated", "false")).lower() == "true":
            send_dm(sender_id, f"The old preview file expired after server restart {EMO['smile']}\n\nI am rebuilding the same direction now.")
        else:
            send_dm(sender_id, f"Creating your profile preview now {EMO['art']}\n\nThis will use the audit details: bio direction, highlights, content ideas, and growth angle.")

        filename = generate_preview_image(state, sender_id)
        state["preview_filename"] = filename

    if PUBLIC_BASE_URL and filename:
        state["preview_url"] = f"{PUBLIC_BASE_URL}/preview/{filename}"

    state["step"] = "preview_sent"
    state["preview_requested"] = "true"
    state["preview_generated"] = "true"
    state["lead_temperature"] = "hot"
    STORE.save_state(sender_id, state)

    if PUBLIC_BASE_URL and state.get("preview_url"):
        result = send_image(sender_id, state["preview_url"])
        if result.get("error"):
            send_dm(sender_id, f"The preview is ready, but Instagram did not load the image here {EMO['sad']}\n\nPlease try SEE PREVIEW again in a moment.")
    else:
        send_dm(sender_id, f"The preview is ready, but I could not send the image here {EMO['sad']}\n\nPlease check PUBLIC_BASE_URL and try again.")

    time.sleep(0.8)
    send_dm(sender_id, msg_after_preview(), ["FIX THIS", "VIEW AUDIT"])


def start_conversion(sender_id: str, state: Dict[str, Any], text: str = ""):
    if not state.get("latest_audit"):
        send_dm(
            sender_id,
            f"I can help with that {EMO['smile']}\n\nFirst I need to audit the profile so the advice is specific, not generic.\n\nSend GROWTH to start the free audit.",
            ["GROWTH"]
        )
        return

    state["lead_temperature"] = "hot"
    state["step"] = "conversion_pain"
    STORE.save_state(sender_id, state)

    msg = build_ai_conversion_message(state, text, "pain")
    send_dm(sender_id, msg, ["YES", "SHOW ME", "NOT NOW"])


def advance_conversion(sender_id: str, state: Dict[str, Any], text: str, target_stage: Optional[str] = None):
    step = state.get("step")
    norm = normalize(text)

    if step == "conversion_goal" and norm not in YES_WORDS:
        state["goal"] = text.strip()[:150]
    elif step == "conversion_timeline" and norm not in YES_WORDS:
        state["timeline"] = text.strip()[:150]
    elif step == "conversion_scope" and norm not in YES_WORDS:
        if norm in IDK_WORDS:
            category = state.get("profile_category") or get_audit_json(state).get("detected_category")
            if is_creator_or_page_category(category):
                state["scope_preference"] = "Not sure - recommend profile + content + monetization map"
            else:
                state["scope_preference"] = "Not sure - recommend profile fix first"
        else:
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
    send_dm(sender_id, msg)


def handle_objection(sender_id: str, state: Dict[str, Any], text: str, objection_type: str):
    if not state.get("latest_audit"):
        send_dm(
            sender_id,
            f"Good question {EMO['smile']}\n\nThe audit and preview are free.\n\nFirst I will review the profile, then the next step can be suggested properly.\n\nSend GROWTH to start.",
            ["GROWTH"]
        )
        return

    state["objection_type"] = objection_type
    state["lead_temperature"] = "hot"

    if objection_type in ["thinking_delay", "not_now"]:
        state["lead_temperature"] = "warm"
        STORE.save_state(sender_id, state)
        send_dm(sender_id, f"No issue {EMO['smile']}\n\nKeep the audit as your starting point.\n\nWhen you want ClientBoost to map the fix properly, reply FIX THIS.")
        return

    state["step"] = "conversion_goal"
    STORE.save_state(sender_id, state)
    msg = build_ai_conversion_message(state, text, "objection")
    send_dm(sender_id, msg)


def handle_contact(sender_id: str, state: Dict[str, Any], text: str):
    contact, contact_type = contact_from_text(text)

    if not contact:
        send_dm(sender_id, f"Send your best contact detail - WhatsApp number or email {EMO['smile']}")
        return

    state["contact_info"] = contact
    state["contact_type"] = contact_type or "contact"

    if contact_type == "phone":
        state["phone_number"] = contact

    state["handover"] = "true"
    state["lead_temperature"] = "senior_review"
    state["step"] = "senior_review"
    STORE.save_state(sender_id, state)

    STORE.save_final_lead_once(sender_id, latest_user_message=text)
    send_dm(sender_id, msg_senior_review())


def handle_after_handover(sender_id: str, state: Dict[str, Any], text: str):
    intent = direct_intent(text) or "general_question"

    if intent == "price_question":
        send_dm(
            sender_id,
            (
                f"Your details are already saved {EMO['ok']}\n\n"
                "The audit and preview are free.\n\n"
                "If you want ClientBoost to do the work, pricing depends on the scope. A senior strategist will suggest the right starting plan from your saved audit."
            )
        )
        return

    if intent in ["request_help", "start_audit", "asks_details", "general_question", None]:
        send_dm(
            sender_id,
            (
                f"Your case is already in senior review {EMO['ok']}\n\n"
                "Your audit, preview, goal, and contact details are saved.\n\n"
                "A strategist can continue from there without starting again."
            )
        )
        return

    send_dm(sender_id, f"Noted {EMO['ok']}\n\nYour profile case is already saved for senior ClientBoost review.")


def handle_unclear(sender_id: str, state: Dict[str, Any]):
    step = state.get("step", "new")

    if step == "follow_gate":
        send_dm(sender_id, f"Choose one option {EMO['smile']}", ["I FOLLOWED", "WHY FOLLOW", "CANCEL"])
    elif step == "ask_profile_name":
        send_dm(sender_id, f"What name should I use for the profile or brand? {EMO['smile']}")
    elif step == "ask_profile_type":
        send_dm(sender_id, f"What is this profile about? {EMO['smile']}")
    elif step == "ask_goal_or_audience":
        send_dm(sender_id, f"What do you want from this profile - followers, trust, money, sponsors, bookings, sales, or brand deals? {EMO['target']}")
    elif step == "ask_screenshot":
        send_dm(sender_id, f"Send one screenshot of the Instagram profile {EMO['camera']}")
    elif step == "audit_sent":
        send_dm(sender_id, f"What would you like next? {EMO['smile']}", ["SEE PREVIEW", "FIX THIS", "VIEW AUDIT"])
    elif step == "preview_sent":
        send_dm(sender_id, f"What would you like next? {EMO['smile']}", ["FIX THIS", "VIEW AUDIT"])
    elif step.startswith("conversion"):
        advance_conversion(sender_id, state, text="yes")
    elif step == "ask_contact":
        send_dm(sender_id, f"Send your best contact detail - WhatsApp number or email {EMO['smile']}")
    elif step == "senior_review":
        handle_after_handover(sender_id, state, "")
    else:
        send_dm(sender_id, f"Send GROWTH to start your free Instagram audit {EMO['rocket']}", ["GROWTH"])

# ============================================================
# MESSAGE ROUTER
# ============================================================
def handle_message(sender_id: str, message_obj: Dict[str, Any]):
    raw_text = message_obj.get("text", "") or ""
    text = fix_mojibake(raw_text.strip())
    attachments = message_obj.get("attachments", []) or []

    image_url = None
    for attachment in attachments:
        if attachment.get("type") == "image":
            image_url = attachment.get("payload", {}).get("url")
            break

    state = STORE.get_state(sender_id)

    if text == OWNER_RESET_CODE:
        STORE.reset_state(sender_id)
        send_dm(sender_id, f"Owner reset complete {EMO['ok']}\n\nSend GROWTH to test from the beginning.", ["GROWTH"])
        return

    t_norm = normalize(text)
    pre_intent = direct_intent(text)

    if enforce_follow_if_needed(sender_id, state, pre_intent):
        return

    state = STORE.get_state(sender_id)

    follow_gate_allowed_intents = {"follow_confirmed", "generic_done", "ask_why_follow", "cancel", "owner_reset"}
    if state.get("step") == "senior_review" or str(state.get("handover", "false")).lower() == "true":
        if not (state.get("step") == "follow_gate" and pre_intent in follow_gate_allowed_intents):
            if text:
                handle_after_handover(sender_id, state, text)
            return

    last_active = parse_iso(state.get("last_active", ""))
    if last_active and datetime.now(timezone.utc) - last_active > timedelta(days=SESSION_EXPIRY_DAYS):
        if state.get("step") not in ["audit_sent", "preview_sent"]:
            state["step"] = "new"
            STORE.save_state(sender_id, state)

    if image_url:
        if state.get("step") == "ask_screenshot":
            handle_screenshot(sender_id, state, image_url)
        else:
            if state.get("latest_audit"):
                send_dm(sender_id, f"I received the image {EMO['camera']}\n\nI already have your recent audit here. What would you like next?", ["SEE PREVIEW", "FIX THIS", "VIEW AUDIT"])
            else:
                send_dm(sender_id, f"I received the image {EMO['camera']}\n\nFirst send GROWTH so I can review it in the right order.", ["GROWTH"])
        return

    if not text:
        handle_unclear(sender_id, state)
        return

    step = state.get("step", "new")
    t_norm = normalize(text)
    pre_intent = direct_intent(text)
    priority_commands = {"cancel", "reset", "restart"}

    if t_norm in priority_commands:
        intent = pre_intent
    elif step == "ask_profile_name":
        intent = "answer_profile_name"
    elif step == "ask_profile_type":
        intent = "answer_profile_type"
    elif step == "ask_goal_or_audience":
        intent = "answer_goal_or_audience"
    elif step == "conversion_goal":
        if pre_intent in OBJECTION_INTENTS or pre_intent == "money_goal":
            intent = "answer_goal" if pre_intent == "money_goal" else pre_intent
        elif t_norm in YES_WORDS:
            send_dm(sender_id, deterministic_conversion_message(state, "goal"))
            return
        else:
            intent = "answer_goal"
    elif step == "conversion_timeline":
        if pre_intent in OBJECTION_INTENTS:
            intent = pre_intent
        elif t_norm in YES_WORDS:
            send_dm(sender_id, deterministic_conversion_message(state, "timeline"))
            return
        else:
            intent = "answer_timeline"
    elif step == "conversion_scope":
        if pre_intent in OBJECTION_INTENTS:
            intent = pre_intent
        elif t_norm in YES_WORDS:
            send_dm(sender_id, deterministic_conversion_message(state, "scope"))
            return
        else:
            intent = "answer_scope"
    elif step == "ask_contact" and contact_from_text(text)[0]:
        intent = "contact_sent"
    else:
        intent = pre_intent

    if not intent:
        understood = ai_understand_message(state, text)
        confidence = float(understood.get("confidence", 0) or 0)

        if confidence >= 0.65:
            intent = understood.get("intent")
            detected = understood.get("profile_category")
            if detected in PROFILE_CATEGORIES:
                state["profile_category"] = detected

            if understood.get("clean_answer") and intent in [
                "answer_profile_type", "answer_profile_name", "answer_goal", "answer_timeline",
                "answer_scope", "answer_goal_or_audience",
            ]:
                text = understood.get("clean_answer")

            if understood.get("objection_type") and understood.get("objection_type") != "none":
                state["objection_type"] = understood.get("objection_type")
        else:
            intent = "unclear"

    if intent == "owner_reset":
        STORE.reset_state(sender_id)
        send_dm(sender_id, f"Owner reset complete {EMO['ok']}\n\nSend GROWTH to test from the beginning.", ["GROWTH"])
    elif intent == "start_audit":
        handle_start(sender_id, state)
    elif intent == "follow_confirmed" or (intent == "generic_done" and step == "follow_gate"):
        handle_follow_confirmed(sender_id, state)
    elif intent == "ask_why_follow":
        send_dm(sender_id, msg_why_follow(), ["I FOLLOWED", "CANCEL"])
    elif intent == "cancel":
        state["step"] = "new"
        state["pre_follow_step"] = ""
        STORE.save_state(sender_id, state)
        send_dm(sender_id, f"Cancelled {EMO['ok']}\n\nSend GROWTH whenever you are ready.", ["GROWTH"])
    elif intent == "soft_reset":
        state["step"] = "new"
        STORE.save_state(sender_id, state)
        send_dm(sender_id, f"No problem {EMO['ok']}\n\nSend GROWTH whenever you are ready.", ["GROWTH"])
    elif intent == "answer_profile_name":
        handle_profile_name(sender_id, state, text)
    elif intent == "answer_profile_type":
        handle_profile_type(sender_id, state, text)
    elif intent in ["answer_goal_or_audience", "answer_location_or_audience"]:
        handle_goal_or_audience(sender_id, state, text)
    elif intent == "request_latest":
        handle_latest(sender_id, state)
    elif intent == "request_preview":
        handle_preview(sender_id, state)
    elif intent == "request_new_audit":
        allowed, reason = can_start_new_audit(state)
        if allowed:
            state = clear_audit_flow_fields(state)
            state["step"] = "new"
            STORE.save_state(sender_id, state)
            handle_start(sender_id, state)
        else:
            send_dm(sender_id, reason, ["VIEW AUDIT", "SEE PREVIEW", "FIX THIS"])
    elif intent == "request_help":
        start_conversion(sender_id, state, text)
    elif intent in OBJECTION_INTENTS:
        handle_objection(sender_id, state, text, intent)
    elif intent == "money_goal":
        if step in ["audit_sent", "preview_sent"]:
            start_conversion(sender_id, state, text)
        elif step.startswith("conversion"):
            advance_conversion(sender_id, state, text)
        else:
            handle_goal_or_audience(sender_id, state, text)
    elif intent == "yes":
        if step in ["audit_sent", "preview_sent"]:
            start_conversion(sender_id, state, text)
        elif step in ["conversion_pain", "conversion_reframe", "conversion_solution"]:
            advance_conversion(sender_id, state, text)
        elif step == "ask_contact":
            send_dm(sender_id, f"Send your best contact detail - WhatsApp number or email {EMO['smile']}")
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
        if step in ["conversion_pain", "conversion_reframe", "conversion_solution", "conversion_goal", "conversion_timeline", "conversion_scope"]:
            advance_conversion(sender_id, state, text)
        else:
            handle_unclear(sender_id, state)

# ============================================================
# WEBHOOK ROUTES
# ============================================================
@app.route("/", methods=["GET"])
def home():
    return f"ClientBoost Bot is running - {APP_VERSION}", 200


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "service": "clientboost-bot",
        "status": "ok",
        "version": APP_VERSION,
        "follow_mode": FOLLOW_VERIFY_MODE,
        "strict_recheck": FOLLOW_RECHECK_EVERY_MESSAGE,
        "sheets_enabled": STORE.enabled,
    }), 200


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

                if not sender_id:
                    continue

                if message_obj.get("is_echo"):
                    continue

                mid = message_obj.get("mid")
                if mid:
                    with PROCESSED_LOCK:
                        now_ts = time.time()
                        old_keys = [k for k, v in PROCESSED_MIDS.items() if now_ts - v > 600]
                        for key in old_keys:
                            PROCESSED_MIDS.pop(key, None)

                        if mid in PROCESSED_MIDS:
                            continue

                        PROCESSED_MIDS[mid] = now_ts

                has_text = bool(message_obj.get("text"))
                has_image = any(
                    attachment.get("type") == "image"
                    for attachment in message_obj.get("attachments", []) or []
                )

                if has_text or has_image:
                    thread = threading.Thread(
                        target=handle_message,
                        args=(sender_id, message_obj),
                        daemon=True,
                    )
                    thread.start()

    return jsonify({"status": "ok"}), 200

# ============================================================
# RUN LOCAL
# ============================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
