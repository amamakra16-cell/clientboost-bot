# ============================================================
# ClientBoost — Instagram Growth Audit + Specific Preview + Lead Funnel
# Single-file Render deployment version
# Core stack: Flask + Meta Instagram Messaging API + Gemini + Google Sheets + Pillow
# Final professional version — specific audit, no hard audit fail, strict follow unlock
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
APP_VERSION = "clientboost-final-professional-v4.4-no-hard-audit-fail-2026-05-07"

# ============================================================
# ENVIRONMENT
# ============================================================
def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except Exception:
        return default


def env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "clientboost2024")
PAGE_ACCESS_TOKEN = os.environ.get("PAGE_ACCESS_TOKEN", "")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")

OWNER_RESET_CODE = os.environ.get("OWNER_RESET_CODE", "CBRESET2026")
FOLLOW_REQUIRED = env_bool("FOLLOW_REQUIRED", True)
FOLLOW_ACCOUNT_USERNAME = os.environ.get("FOLLOW_ACCOUNT_USERNAME", "clientboost.in")
# Soft is default because Instagram follow verification is unreliable for many IG messaging setups.
# Values: soft, strict, off
FOLLOW_VERIFY_MODE = os.environ.get("FOLLOW_VERIFY_MODE", "strict").strip().lower()
if FOLLOW_VERIFY_MODE not in {"soft", "strict", "off"}:
    FOLLOW_VERIFY_MODE = "soft"
FOLLOW_CACHE_HOURS = env_int("FOLLOW_CACHE_HOURS", 24)
FOLLOW_RECHECK_EVERY_MESSAGE = env_bool("FOLLOW_RECHECK_EVERY_MESSAGE", True)

AUDIT_COOLDOWN_DAYS = env_int("AUDIT_COOLDOWN_DAYS", 7)
SESSION_EXPIRY_DAYS = env_int("SESSION_EXPIRY_DAYS", 3)

PROCESSED_MIDS: Dict[str, float] = {}
PROCESSED_LOCK = threading.Lock()

GEMINI_AUDIT_API_KEYS = [
    k.strip() for k in os.environ.get("GEMINI_AUDIT_API_KEYS", os.environ.get("GEMINI_API_KEY", "")).split(",") if k.strip()
]
GEMINI_CONVERSION_API_KEYS = [
    k.strip() for k in os.environ.get("GEMINI_CONVERSION_API_KEYS", ",".join(GEMINI_AUDIT_API_KEYS)).split(",") if k.strip()
]
# Free-stack friendly defaults. Env can override.
GEMINI_AUDIT_MODELS = [
    m.strip() for m in os.environ.get(
        "GEMINI_AUDIT_MODELS",
        "gemini-2.5-flash,gemini-2.0-flash,gemini-2.5-flash-lite"
    ).split(",") if m.strip()
]
GEMINI_CONVERSION_MODELS = [
    m.strip() for m in os.environ.get(
        "GEMINI_CONVERSION_MODELS",
        "gemini-2.5-flash,gemini-2.0-flash,gemini-2.5-flash-lite"
    ).split(",") if m.strip()
]

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
    "preview_requested", "preview_generated", "preview_style", "preview_filename", "preview_url",
    "lead_temperature", "objection_type", "phone_number", "contact_info", "contact_type",
    "goal", "timeline", "scope_preference", "handover", "cooldown_until", "follow_verified",
    "follow_verified_at", "last_active", "final_saved", "state_json"
]

FINAL_LEADS_HEADERS = [
    "timestamp", "sender_id", "instagram_username", "profile_name", "profile_type_raw",
    "profile_category", "target_location_or_audience", "audit_score", "main_gap",
    "top_fix", "specific_strengths", "priority_gaps", "preview_bio", "preview_highlights",
    "preview_grid", "preview_requested", "preview_generated", "preview_style", "preview_url",
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
    "yes done": "follow_confirmed",
    "why follow": "ask_why_follow",
    "cancel": "cancel",
    "don't": "cancel",
    "dont": "cancel",
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
UNCERTAIN_WORDS = {"idk", "i don't know", "i dont know", "not sure", "no idea", "confused"}
OBJECTION_INTENTS = {
    "price_question", "asks_details", "trust_issue", "thinking_delay", "not_now",
    "already_has_designer", "wants_to_do_self", "budget_low", "asks_results"
}
POST_HANDOVER_INTENTS = {"price_question", "asks_details", "asks_results", "trust_issue", "request_latest", "request_preview"}

PROFILE_CATEGORIES: Dict[str, Dict[str, Any]] = {
    "food_restaurant": {
        "keywords": ["restaurant", "cafe", "food", "bakery", "cloud kitchen", "takeaway", "delivery", "burger", "pizza", "juice", "hotel", "nosh", "biryani", "kitchen"],
        "style": "Warm Commercial",
        "highlights": ["Menu", "Reviews", "Kitchen", "Offers", "Order", "Location"],
        "grid": ["Bestseller", "Menu Proof", "Review", "Kitchen BTS", "Offer", "Order CTA", "Location", "Combo", "FAQ"],
        "angle": "orders, trust, craving, and WhatsApp conversion",
        "goal_type": "sales",
    },
    "beauty_salon": {
        "keywords": ["salon", "beauty", "makeup", "bridal", "nails", "spa", "boutique", "fashion", "hair", "skincare"],
        "style": "Warm Commercial",
        "highlights": ["Services", "Results", "Pricing", "Reviews", "Bridal", "Booking"],
        "grid": ["Result", "Service", "Review", "Offer", "Process", "Before After", "FAQ", "Pricing", "Booking"],
        "angle": "bookings through transformation proof and trust",
        "goal_type": "leads",
    },
    "fitness_wellness": {
        "keywords": ["gym", "fitness", "yoga", "trainer", "workout", "zumba", "crossfit", "nutrition", "wellness"],
        "style": "Modern Content Grid",
        "highlights": ["Programs", "Results", "Trainers", "Reviews", "Plans", "Join"],
        "grid": ["Workout", "Result", "Trainer", "Tip", "Plan", "Review", "Class", "Diet", "Join"],
        "angle": "membership enquiries, transformation proof, and authority",
        "goal_type": "leads",
    },
    "clinic_healthcare": {
        "keywords": ["clinic", "dental", "doctor", "hospital", "skin", "physio", "physiotherapy", "ayurveda", "health", "medical"],
        "style": "Premium Structured",
        "highlights": ["Treatments", "Doctor", "Results", "Reviews", "FAQ", "Book"],
        "grid": ["Care", "Treatment", "Review", "FAQ", "Doctor", "Tip", "Trust", "Result", "Book"],
        "angle": "patient trust, clarity, and appointment conversion",
        "goal_type": "leads",
    },
    "real_estate": {
        "keywords": ["real estate", "property", "plots", "land", "apartment", "villa", "realtor", "broker", "site", "flat"],
        "style": "Premium Structured",
        "highlights": ["Listings", "Site Visits", "Documents", "Reviews", "Areas", "Contact"],
        "grid": ["Property", "Area Guide", "Docs Proof", "Site Visit", "Trust", "Review", "Price Clarity", "Buyer Guide", "CTA"],
        "angle": "buyer trust, documentation clarity, and site visit leads",
        "goal_type": "leads",
    },
    "ecommerce_retail": {
        "keywords": ["store", "shop", "ecommerce", "products", "clothing", "jewellery", "accessories", "electronics", "retail", "brand"],
        "style": "Warm Commercial",
        "highlights": ["Products", "Offers", "Reviews", "New", "Delivery", "Order"],
        "grid": ["Product", "Review", "Offer", "New Drop", "Details", "Use Case", "Proof", "Packing", "Order"],
        "angle": "product trust, repeat buying, and DM/WhatsApp orders",
        "goal_type": "sales",
    },
    "professional_service": {
        "keywords": ["lawyer", "accountant", "architect", "interior", "consultant", "software", "finance", "insurance", "ca", "advocate"],
        "style": "Premium Structured",
        "highlights": ["Services", "Work", "Results", "Reviews", "FAQ", "Contact"],
        "grid": ["Service", "Case Study", "Proof", "Tip", "Review", "Process", "FAQ", "Result", "CTA"],
        "angle": "authority, credibility, and consultation enquiries",
        "goal_type": "leads",
    },
    "agency_service": {
        "keywords": ["agency", "marketing", "social media", "ads", "seo", "branding", "automation", "digital"],
        "style": "Premium Structured",
        "highlights": ["Services", "Results", "Proof", "Process", "FAQ", "Contact"],
        "grid": ["Offer", "Result", "Proof", "Tip", "Process", "Case Study", "CTA", "Trust", "Lead"],
        "angle": "high-trust lead generation and authority",
        "goal_type": "leads",
    },
    "local_service": {
        "keywords": ["plumber", "electrician", "cleaning", "repair", "pest control", "home service", "technician", "local service"],
        "style": "Warm Commercial",
        "highlights": ["Services", "Before", "After", "Reviews", "Pricing", "Call"],
        "grid": ["Service", "Before", "After", "Review", "Tip", "Problem", "Fix", "Offer", "Call"],
        "angle": "local calls, urgency, trust, and service bookings",
        "goal_type": "leads",
    },
    "photographer_videographer": {
        "keywords": ["photographer", "videographer", "photo", "video", "cinematographer", "shoot", "wedding film"],
        "style": "Editorial Creator",
        "highlights": ["Work", "Weddings", "Reels", "Reviews", "Packages", "Contact"],
        "grid": ["Shoot", "Story", "Detail", "BTS", "Reel", "Work", "Review", "Process", "CTA"],
        "angle": "portfolio clarity, booking confidence, and premium enquiry flow",
        "goal_type": "leads",
    },
    "personal_brand": {
        "keywords": ["coach", "mentor", "speaker", "founder", "personal brand", "consultant", "public figure"],
        "style": "Modern Content Grid",
        "highlights": ["About", "Work", "Results", "Content", "Reviews", "Contact"],
        "grid": ["Insight", "Story", "Proof", "Tip", "Value", "Offer", "Result", "Trust", "CTA"],
        "angle": "authority, trust, and inbound enquiries",
        "goal_type": "authority",
    },
    "religious_education_content": {
        "keywords": ["islamic", "quran", "dua", "duas", "hadith", "religious", "reminder", "spiritual", "deen", "islam", "sunnah", "haqikat", "haqiqat", "allah"],
        "style": "Editorial Creator",
        "highlights": ["Start", "Quran", "Duas", "Hadith", "Reels", "Collab"],
        "grid": ["Daily Dua", "Quran Reminder", "Hadith Lesson", "Save This", "Ask a Question", "Reel Reminder", "Story Poll", "Collab Info", "Follow CTA"],
        "angle": "saves, shares, trust, follow conversion, and monetization path clarity",
        "goal_type": "creator_monetization",
    },
    "entertainment_meme_page": {
        "keywords": ["meme", "memes", "entertainment", "funny", "comedy", "viral", "fan page"],
        "style": "Editorial Creator",
        "highlights": ["Best", "Reels", "Series", "About", "Viral", "Contact"],
        "grid": ["Meme", "Reel", "Trend", "Relatable", "Series", "Story", "Hook", "CTA", "Follow"],
        "angle": "shareability, repeat formats, audience memory, and monetization path",
        "goal_type": "creator_monetization",
    },
    "knowledge_creator": {
        "keywords": ["knowledge", "facts", "finance tips", "business tips", "learning", "educational content", "explain", "education page"],
        "style": "Modern Content Grid",
        "highlights": ["Topics", "Best", "Series", "Resources", "About", "Contact"],
        "grid": ["Hook", "Explain", "Tip", "Carousel", "Myth", "Value", "Series", "Proof", "Follow"],
        "angle": "saves, shares, clarity, repeatable series, and monetization path",
        "goal_type": "creator_monetization",
    },
    "content_page_general": {
        "keywords": ["page", "theme page", "content page", "quotes", "motivation", "community", "news", "updates"],
        "style": "Editorial Creator",
        "highlights": ["Start", "Best", "Series", "Proof", "About", "Contact"],
        "grid": ["Hook", "Value", "Series", "Save This", "Share", "Proof", "Story", "Collab", "Follow CTA"],
        "angle": "followers, saves, shares, trust, and monetization path clarity",
        "goal_type": "creator_monetization",
    },
    "default_general_profile": {
        "keywords": [],
        "style": "Premium Structured",
        "highlights": ["About", "Work", "Proof", "FAQ", "Contact"],
        "grid": ["Intro", "Value", "Proof", "Tip", "Story", "Offer", "FAQ", "Result", "CTA"],
        "angle": "clearer positioning, trust, and profile conversion",
        "goal_type": "general",
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
    cleaned = text.strip().replace("```json", "```").replace("```JSON", "```")
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


def safe_username(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9_.]", "", normalize(value).replace(" ", ""))
    return cleaned[:24] or "yourprofile"


def clean_dm_text(text: str, limit: int = MAX_DM_CHARS) -> str:
    text = (text or "").replace("```", "").strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    blocked_patterns = [
        r"\bbackend\b", r"\bautomation\b", r"\bAI model\b", r"\bGemini\b",
        r"\bquota\b", r"\bcooldown\b", r"\bapi\b", r"\btoken\b", r"\bJSON\b"
    ]
    for pattern in blocked_patterns:
        text = re.sub(pattern, "", text, flags=re.I)
    text = re.sub(r"[ \t]{2,}", " ", text)
    if text and not re.search(r"[\U0001F300-\U0001FAFF✅🎯📸👀🙂🚀⭐⚡⭕✍🤝🎨👇😕🔍🧩📌🟢🟡🔴]", text):
        text = "🙂 " + text
    return text[:limit].strip()


def as_list(value: Any, fallback: Optional[List[str]] = None, limit: int = 6) -> List[str]:
    fallback = fallback or []
    if isinstance(value, list):
        items = value
    elif isinstance(value, str):
        parsed = safe_json_loads(value, None)
        if isinstance(parsed, list):
            items = parsed
        else:
            items = re.split(r"\s*[|;/]\s*|\n+", value)
    else:
        items = fallback
    cleaned: List[str] = []
    for item in items:
        t = str(item).strip()
        if t and t.lower() not in {"none", "not visible", "not clearly visible", "n/a", "na"}:
            cleaned.append(t[:90])
    return cleaned[:limit] if cleaned else fallback[:limit]


def to_json_list(value: Any, fallback: Optional[List[str]] = None, limit: int = 9) -> str:
    return json.dumps(as_list(value, fallback or [], limit), ensure_ascii=False)


def state_json_list(state: Dict[str, Any], key: str, fallback: Optional[List[str]] = None, limit: int = 9) -> List[str]:
    return as_list(state.get(key, ""), fallback or [], limit)


def profile_is_creator_monetization(state: Dict[str, Any]) -> bool:
    category = state.get("profile_category") or "default_general_profile"
    cfg = PROFILE_CATEGORIES.get(category, PROFILE_CATEGORIES["default_general_profile"])
    return cfg.get("goal_type") == "creator_monetization"


def prepare_audit_image(image: Image.Image) -> Image.Image:
    img = image.convert("RGB")
    w, h = img.size
    target_w = 1440
    if w < target_w:
        ratio = target_w / max(1, w)
        img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
    try:
        from PIL import ImageFilter, ImageEnhance
        img = ImageEnhance.Contrast(img).enhance(1.18)
        img = ImageEnhance.Sharpness(img).enhance(1.35)
        img = img.filter(ImageFilter.SHARPEN)
    except Exception:
        pass
    return img


def facts_have_useful_detail(facts: Dict[str, Any]) -> bool:
    if not isinstance(facts, dict):
        return False

    values: List[str] = []
    for key in [
        "username_visible", "display_name_visible", "profile_photo_observed",
        "bio_lines_visible", "visible_numbers", "cta_or_link_visible",
        "highlight_labels_visible", "post_cover_text_visible", "visual_style_observed",
        "strong_parts_visible", "weak_parts_visible"
    ]:
        values.extend(as_list(facts.get(key), [], 10))

    useful = []
    for value in values:
        lowered = normalize(value)
        if not lowered:
            continue
        if lowered in {"not clearly visible", "not clearly readable", "n/a", "none"}:
            continue
        if "not clearly" in lowered and len(lowered) < 30:
            continue
        useful.append(value)

    joined = " ".join(useful).lower()
    return len(useful) >= 2 or len(joined) >= 18


def fallback_facts_from_state(state: Dict[str, Any], cfg: Dict[str, Any], reason: str = "") -> Dict[str, Any]:
    """
    Safe fallback used when the vision model/API does not return readable facts.
    It prevents the bot from exposing backend failure messages to the user and still
    creates a useful audit from the user-provided profile name, niche, and goal.
    """
    category = state.get("profile_category") or detect_category(
        f"{state.get('profile_name', '')} {state.get('profile_type_raw', '')} {state.get('target_location_or_audience', '')}"
    )
    profile_name = state.get("profile_name") or "the profile"
    profile_type = state.get("profile_type_raw") or readable_category(category) if "readable_category" in globals() else (state.get("profile_type_raw") or category.replace("_", " "))
    goal = state.get("target_location_or_audience") or "growth"
    fallback_grid = build_fallback_preview_grid(state, cfg)
    fallback_highlights = cfg.get("highlights", ["About", "Best", "Proof", "FAQ", "Contact"] )

    return {
        "username_visible": profile_name,
        "display_name_visible": profile_name,
        "profile_photo_observed": "visible profile photo/logo area in the screenshot",
        "bio_lines_visible": [f"Profile context: {profile_type}", f"Goal shared: {goal}"],
        "visible_numbers": "profile statistics area visible, exact numbers not confirmed by model",
        "cta_or_link_visible": "follow/message action area visible, exact link not confirmed by model",
        "highlight_labels_visible": fallback_highlights[:6],
        "post_cover_text_visible": fallback_grid[:9],
        "visual_style_observed": "Instagram profile screenshot with bio area, highlights row, and first grid visible",
        "strong_parts_visible": [
            "The profile has a visible first-screen structure: bio area, highlights, and post grid.",
            f"The niche/goal is usable for a focused audit: {profile_type} / {goal}.",
        ],
        "weak_parts_visible": [
            "The growth path should be made clearer from first look to follow, trust, and action.",
            "The first 9 posts should work like a planned entry point, not only normal uploads.",
        ],
        "screenshot_confidence": 0.48,
        "fallback_reason": reason or "vision facts were incomplete",
    }


def is_generic_audit(data: Dict[str, Any]) -> bool:
    joined = json.dumps(data, ensure_ascii=False).lower()
    generic_terms = [
        "needs clearer structure", "not clear enough from the visible screenshot", "add proof",
        "stronger cta", "content pillars", "random posting", "visitors understand", "generic"
    ]
    hits = sum(1 for term in generic_terms if term in joined)
    evidence = data.get("visible_facts") or data.get("raw_extracted_facts") or {}
    evidence_text = json.dumps(evidence, ensure_ascii=False).strip("{}[] \n")
    return hits >= 4 and len(evidence_text) < 120


def calculate_recommended_offer(state: Dict[str, Any]) -> str:
    category = state.get("profile_category", "default_general_profile")
    goal = normalize(state.get("goal", ""))
    scope = normalize(state.get("scope_preference", ""))
    score_raw = str(state.get("audit_score", ""))
    score = int(float(score_raw)) if score_raw.replace(".", "", 1).isdigit() else 0

    if profile_is_creator_monetization(state):
        if "full" in scope or "manage" in scope or "system" in scope:
            return "Creator Growth + Monetization System"
        if any(x in goal for x in ["money", "monet", "collab", "promo", "brand", "paid", "traffic"]):
            return "Content Page Monetization Setup"
        return "Profile + Content Direction Setup"

    if "full" in scope or "manage" in scope or "system" in scope:
        return "Full Growth System"
    if any(x in goal for x in ["sales", "booking", "enquir", "lead", "client", "customer", "order"]):
        if category in ["food_restaurant", "beauty_salon", "real_estate", "clinic_healthcare", "professional_service", "local_service"]:
            return "Growth Setup + Lead System"
    if score and score < 55:
        return "Profile Fix + Growth Setup"
    return "Growth Setup"


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
        "visible_facts_json": "{}",
        "raw_extracted_facts_json": "{}",
        "specific_strengths_json": "[]",
        "priority_gaps_json": "[]",
        "preview_bio": "",
        "preview_highlights_json": "[]",
        "preview_grid_json": "[]",
        "audit_confidence": "",
    })
    state.update(keep)
    return state

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
            "specific_strengths": state.get("specific_strengths_json", ""),
            "priority_gaps": state.get("priority_gaps_json", ""),
            "preview_bio": state.get("preview_bio", ""),
            "preview_highlights": state.get("preview_highlights_json", ""),
            "preview_grid": state.get("preview_grid_json", ""),
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
            r = requests.post(url, params={"access_token": PAGE_ACCESS_TOKEN}, json=payload, timeout=timeout)
            if r.status_code < 300:
                return r.json() if r.text else {}
            print("Meta send error", r.status_code, r.text)
            last_response = {"error": r.text, "status_code": r.status_code}
        except Exception as e:
            print(f"Meta send failed: {e}")
            last_response = {"error": str(e)}
        time.sleep(0.7)
    return last_response


def send_dm(recipient_id: str, text: str, quick_replies: Optional[List[str]] = None, delay: float = 0.85) -> Dict[str, Any]:
    if not PAGE_ACCESS_TOKEN:
        print("PAGE_ACCESS_TOKEN missing. DM not sent.")
        return {"error": "missing token"}
    text = clean_dm_text(text, limit=5000)
    parts = split_text(text, MAX_DM_CHARS)
    final_response: Dict[str, Any] = {}
    for i, part in enumerate(parts):
        send_typing_action(recipient_id)
        time.sleep(min(2.4, max(delay, len(part) / 520)))
        url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/me/messages"
        message: Dict[str, Any] = {"text": part}
        if quick_replies and i == len(parts) - 1:
            message["quick_replies"] = [
                {"content_type": "text", "title": title[:20], "payload": title.upper().replace(" ", "_")}
                for title in quick_replies[:13]
            ]
        payload = {"recipient": {"id": recipient_id}, "message": message, "messaging_type": "RESPONSE"}
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
        "message": {"attachment": {"type": "image", "payload": {"url": image_url}}},
        "messaging_type": "RESPONSE",
    }
    return post_to_meta(url, payload)


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
    if not FOLLOW_REQUIRED or FOLLOW_VERIFY_MODE == "off":
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
    generation_config = {"temperature": 0.12 if purpose == "audit" else 0.38}
    for model_name in models:
        for key in keys:
            try:
                genai.configure(api_key=key)
                try:
                    model = genai.GenerativeModel(model_name, generation_config=generation_config)
                except TypeError:
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
    priority = [
        "religious_education_content", "food_restaurant", "real_estate", "beauty_salon",
        "clinic_healthcare", "photographer_videographer", "knowledge_creator",
        "entertainment_meme_page", "personal_brand", "content_page_general",
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
    if any(x in t for x in ["price", "cost", "how much", "charges", "package", "rate", "fees", "pricing"]):
        return "price_question"
    if any(x in t for x in ["details", "tell me more", "services", "what do you offer", "what will you do", "process", "what is included"]):
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
answer_profile_name, answer_profile_type, answer_goal_or_audience,
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
# AUDIT + CONVERSION
# ============================================================
def build_fallback_preview_grid(state: Dict[str, Any], cfg: Dict[str, Any]) -> List[str]:
    category = state.get("profile_category") or "default_general_profile"
    if category == "religious_education_content":
        return ["Daily Dua", "Quran Reminder", "Hadith Lesson", "Save This", "Ask a Question", "Reel Reminder", "Story Poll", "Collab Info", "Follow CTA"]
    if category in {"content_page_general", "knowledge_creator", "entertainment_meme_page"}:
        return ["Hook", "Value Post", "Series", "Save This", "Share Post", "Proof", "Story", "Collab", "Follow CTA"]
    return [str(x)[:18] for x in cfg.get("grid", [])[:9]] or ["Intro", "Proof", "Value", "Story", "Offer", "FAQ", "Result", "Trust", "CTA"]


def fallback_audit_from_facts(cfg: Dict[str, Any], facts: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
    category = state.get("profile_category") or "default_general_profile"
    profile_name = state.get("profile_name") or "This profile"
    goal = state.get("target_location_or_audience") or "growth"
    bio_lines = as_list(facts.get("bio_lines_visible"), [], 5)
    highlights = as_list(facts.get("highlight_labels_visible"), cfg.get("highlights", []), 6)
    grid = as_list(facts.get("post_cover_text_visible"), build_fallback_preview_grid(state, cfg), 9)
    strong = as_list(facts.get("strong_parts_visible"), [], 3)
    weak = as_list(facts.get("weak_parts_visible"), [], 4)

    has_bio = bool(bio_lines)
    has_highlights = len(highlights) >= 3
    has_grid_text = len(grid) >= 3
    is_creator = cfg.get("goal_type") == "creator_monetization"

    identity_score = 8 if facts.get("username_visible") and "not clearly" not in str(facts.get("username_visible", "")).lower() else 6
    bio_score = 7 if has_bio else 4
    highlight_score = 7 if has_highlights else 4
    post_score = 6 if has_grid_text else 4
    trust_score = 5 if is_creator else 6
    overall = min(100, (identity_score + bio_score + highlight_score + post_score + trust_score) * 2)

    bio_summary = "; ".join(bio_lines) if has_bio else "bio text is not clearly readable"
    highlights_summary = ", ".join(highlights) if highlights else "highlights are not clearly readable"
    grid_summary = ", ".join(grid[:6]) if grid else "post covers are not clearly readable"

    if is_creator:
        main_gap = f"{profile_name} already has a clear content-page base, but the profile needs a stronger follow reason, repeatable series, and monetization path so attention can become value."
        top_fix = "Keep the existing identity, then add a clear follow reason + collab/resource path in the bio and make the first 9 posts show series, proof, value, and action."
        preview_bio = f"{profile_name} | Daily reminders, useful posts & shareable content | Follow for value • DM for collabs"
        preview_highlights = highlights[:3] + [x for x in ["Start", "Best", "Collab", "FAQ"] if x not in highlights]
        preview_grid = build_fallback_preview_grid(state, cfg)
        default_gaps = [
            "The page needs a clearer reason to follow immediately.",
            "The content should be grouped into repeatable series so people remember the page.",
            "Monetization path is not clear enough yet: collabs, promos, resources, community, or traffic."
        ]
    else:
        main_gap = f"{profile_name} needs a clearer first-screen path from attention to trust to action for {goal}."
        top_fix = "Make the bio, highlights, first posts, proof, and contact path work like one landing page."
        preview_bio = f"{profile_name} | Clear value, proof, and action path for {goal}."
        preview_highlights = highlights[:3] + [x for x in cfg.get("highlights", []) if x not in highlights]
        preview_grid = build_fallback_preview_grid(state, cfg)
        default_gaps = [
            "The next action is not strong enough from the first screen.",
            "Proof should be easier to notice before someone scrolls.",
            "The first 9 posts should guide visitors through value, proof, trust, and action."
        ]

    return {
        "overall_score": overall,
        "audit_confidence": float(facts.get("screenshot_confidence", 0.55) or 0.55),
        "visible_facts": {
            "username": facts.get("username_visible", "not clearly visible"),
            "bio_summary": bio_summary,
            "highlights_summary": highlights_summary,
            "grid_summary": grid_summary,
        },
        "specific_strengths": strong or (["The bio/profile information is already visible." if has_bio else "The profile has a visible base to build from.", "Highlights and post grid are present, so the page is not empty." if has_highlights or has_grid_text else "The profile can be improved once the first screen is clearer."]),
        "priority_gaps": weak or default_gaps,
        "main_gap": main_gap,
        "profile_identity_score": identity_score,
        "bio_cta_score": bio_score,
        "highlights_score": highlight_score,
        "posts_reels_score": post_score,
        "trust_conversion_score": trust_score,
        "profile_identity": {
            "noticed": f"Visible identity: {facts.get('username_visible', profile_name)}. The profile name is present, so identity is not the main problem.",
            "fix": "Sharpen the name line so a new visitor instantly knows what the page gives and why to follow or message."
        },
        "bio_cta": {
            "noticed": f"Bio seen: {bio_summary[:220]}." if has_bio else "The bio text is not clearly readable in the screenshot.",
            "fix": "Keep the useful bio parts, then add one clear action line: Follow for daily value, DM for collabs/resources, or tap the link for the next step."
        },
        "highlights": {
            "noticed": f"Visible highlights: {highlights_summary}.",
            "fix": "Order highlights like a decision path: Start Here, Best Posts, Proof/Reviews, Topics, FAQ, Contact/Collab."
        },
        "posts_reels": {
            "noticed": f"Visible post direction: {grid_summary}.",
            "fix": "Turn the first 9 posts into a mini landing page: hook, value, proof, series, story, FAQ, result, collab/action, follow CTA."
        },
        "trust_conversion": {
            "noticed": "The profile has content, but the trust/action path needs to be more obvious from the first screen.",
            "fix": "Add proof posts, pinned explanation posts, and a clear path for collabs, enquiries, resources, sales, bookings, or followers depending on the goal."
        },
        "top_fix": top_fix,
        "preview_bio": preview_bio,
        "preview_highlights": preview_highlights[:6],
        "preview_grid": preview_grid[:9],
        "recommended_preview_style": cfg.get("style"),
        "raw_extracted_facts": facts,
    }


def normalize_audit_data(data: Dict[str, Any], cfg: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
    raw_facts = data.get("raw_extracted_facts") if isinstance(data.get("raw_extracted_facts"), dict) else {}
    facts = data.get("visible_facts") if isinstance(data.get("visible_facts"), dict) else {}

    for key in ["profile_identity", "bio_cta", "highlights", "posts_reels", "trust_conversion"]:
        if not isinstance(data.get(key), dict):
            data[key] = {"noticed": "Not enough detail was visible.", "fix": "Improve this section with clearer proof and action."}
        for sub in ["noticed", "fix"]:
            data[key][sub] = str(data[key].get(sub, "")).strip()[:430]

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

    try:
        data["audit_confidence"] = max(0.0, min(1.0, float(data.get("audit_confidence", raw_facts.get("screenshot_confidence", 0.65) or 0.65))))
    except Exception:
        data["audit_confidence"] = 0.65

    if not facts:
        facts = {
            "username": raw_facts.get("username_visible", "not clearly visible"),
            "bio_summary": "; ".join(as_list(raw_facts.get("bio_lines_visible"), [], 5)) or "not clearly visible",
            "highlights_summary": ", ".join(as_list(raw_facts.get("highlight_labels_visible"), [], 6)) or "not clearly visible",
            "grid_summary": ", ".join(as_list(raw_facts.get("post_cover_text_visible"), [], 9)) or "not clearly visible",
        }
    data["visible_facts"] = facts

    data["specific_strengths"] = as_list(data.get("specific_strengths"), as_list(raw_facts.get("strong_parts_visible"), ["The profile already has visible information to build from."], 2), 3)
    data["priority_gaps"] = as_list(data.get("priority_gaps"), as_list(raw_facts.get("weak_parts_visible"), ["The first screen needs a clearer path from attention to trust to action."], 3), 4)

    data["main_gap"] = str(data.get("main_gap") or "The first screen needs a clearer path from attention to trust to action.").strip()[:430]
    data["top_fix"] = str(data.get("top_fix") or "Make the bio, highlights, first posts, proof, and action path work together.").strip()[:430]
    data["recommended_preview_style"] = data.get("recommended_preview_style") or cfg.get("style")
    data["preview_bio"] = str(data.get("preview_bio") or build_preview_bio_from_audit(data, state)).strip()[:180]
    data["preview_highlights"] = as_list(data.get("preview_highlights"), cfg.get("highlights", []), 6)
    data["preview_grid"] = as_list(data.get("preview_grid"), build_fallback_preview_grid(state, cfg), 9)
    return data


def build_preview_bio_from_audit(data: Dict[str, Any], state: Dict[str, Any]) -> str:
    name = state.get("profile_name") or "Brand"
    if profile_is_creator_monetization(state):
        return f"{name} | Clear value, shareable series & collab-ready page."
    goal = state.get("target_location_or_audience") or "your audience"
    return f"{name} | Clear value, trust proof, and action path for {goal}."


def audit_profile(image: Image.Image, state: Dict[str, Any]) -> Dict[str, Any]:
    combined_category_text = f"{state.get('profile_name', '')} {state.get('profile_type_raw', '')} {state.get('target_location_or_audience', '')}"
    category = state.get("profile_category") or detect_category(combined_category_text)
    cfg = PROFILE_CATEGORIES.get(category, PROFILE_CATEGORIES["default_general_profile"])
    audit_image = prepare_audit_image(image)

    facts_prompt = f"""
You are reading one Instagram profile screenshot for a ClientBoost audit.
Return ONLY valid JSON. No markdown. Do not guess. If text is not readable, write "not clearly visible".

User-provided context:
Profile / brand name: {state.get('profile_name')}
Profile type / niche: {state.get('profile_type_raw')}
Goal wanted from profile: {state.get('target_location_or_audience')}
Internal category: {category}

Extract visible evidence from the screenshot:
{{
  "username_visible": "exact visible username or not clearly visible",
  "display_name_visible": "exact visible display/name or not clearly visible",
  "profile_photo_observed": "what profile photo/logo looks like, or not clearly visible",
  "bio_lines_visible": ["exact visible line 1", "exact visible line 2", "exact visible line 3"],
  "visible_numbers": "posts/followers/following if visible",
  "cta_or_link_visible": "button/link/contact/action path if visible",
  "highlight_labels_visible": ["label 1", "label 2", "label 3"],
  "post_cover_text_visible": ["cover text 1", "cover text 2", "cover text 3", "cover text 4"],
  "visual_style_observed": "specific colors, mood, consistency, grid feel",
  "strong_parts_visible": ["specific good thing visible", "specific good thing visible"],
  "weak_parts_visible": ["specific weak thing visible", "specific weak thing visible"],
  "screenshot_confidence": 0.0
}}
""".strip()

    facts_text = gemini_generate(facts_prompt, image=audit_image, purpose="audit")
    print("AUDIT_FACTS_RAW:", (facts_text or "")[:1000])
    facts = extract_json(facts_text or "", {})
    if not isinstance(facts, dict):
        facts = {}

    if not facts_text or not facts_have_useful_detail(facts):
        print("AUDIT_FACTS_INCOMPLETE: using safe fallback instead of asking user for another screenshot")
        print("AUDIT_FACTS_REASON:", "no facts text" if not facts_text else json.dumps(facts, ensure_ascii=False)[:700])
        facts = fallback_facts_from_state(state, cfg, "Gemini facts extraction was incomplete")

    prompt = f"""
You are a senior Instagram growth strategist for ClientBoost.
Audit the screenshot using ONLY the visible evidence + user context. Return ONLY valid JSON.

CRITICAL RULES:
- Be specific. Mention visible bio/highlights/post-cover evidence when readable.
- If the bio is already clear, mark it as a strength. Do NOT say the bio is unclear just because you need a gap.
- If it is a content page, do not force sales/bookings. Think followers, saves, shares, collabs, paid promos, resources, traffic, community, and trust.
- Do NOT use template wording like "needs clearer structure" unless you prove it with screenshot evidence.
- Every fix must be practical and specific to this profile/niche.
- No fake guarantees. No markdown. No long paragraphs. Simple English.

User context:
Profile / Brand name: {state.get('profile_name')}
Profile type / niche: {state.get('profile_type_raw')}
Goal wanted from profile: {state.get('target_location_or_audience')}
Internal category: {category}
Likely conversion angle: {cfg.get('angle')}

Screenshot facts extracted:
{json.dumps(facts, ensure_ascii=False)}

Score honestly using 100 points:
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

JSON schema:
{{
  "overall_score": 0,
  "audit_confidence": 0.0,
  "visible_facts": {{
    "username": "...",
    "bio_summary": "...",
    "highlights_summary": "...",
    "grid_summary": "..."
  }},
  "specific_strengths": ["specific strength from screenshot", "specific strength from screenshot"],
  "priority_gaps": ["specific gap from screenshot", "specific gap from screenshot", "specific gap from screenshot"],
  "main_gap": "one clear growth/conversion gap, based on screenshot evidence",
  "profile_identity_score": 0,
  "bio_cta_score": 0,
  "highlights_score": 0,
  "posts_reels_score": 0,
  "trust_conversion_score": 0,
  "profile_identity": {{"noticed": "specific observation", "fix": "specific fix"}},
  "bio_cta": {{"noticed": "specific observation", "fix": "specific fix"}},
  "highlights": {{"noticed": "specific observation", "fix": "specific fix"}},
  "posts_reels": {{"noticed": "specific observation", "fix": "specific fix"}},
  "trust_conversion": {{"noticed": "specific observation", "fix": "specific fix"}},
  "top_fix": "one priority fix that should be done first",
  "preview_bio": "rewritten profile bio direction specific to this profile",
  "preview_highlights": ["highlight label 1", "highlight label 2", "highlight label 3", "highlight label 4", "highlight label 5", "highlight label 6"],
  "preview_grid": ["specific post tile 1", "specific post tile 2", "specific post tile 3", "specific post tile 4", "specific post tile 5", "specific post tile 6", "specific post tile 7", "specific post tile 8", "specific post tile 9"],
  "recommended_preview_style": "{cfg.get('style')}"
}}
""".strip()

    text = gemini_generate(prompt, image=audit_image, purpose="audit")
    print("AUDIT_JSON_RAW:", (text or "")[:1400])
    data = extract_json(text or "", {})
    if not isinstance(data, dict) or not data:
        data = fallback_audit_from_facts(cfg, facts, state)
    data["raw_extracted_facts"] = facts
    data = normalize_audit_data(data, cfg, state)

    if is_generic_audit(data):
        retry_prompt = prompt + """

Your previous output was too generic. Redo it.
Mention at least 4 exact visible details from the screenshot facts.
If a section is already good, mark it as a strength and suggest an upgrade, not a fake problem.
"""
        retry_text = gemini_generate(retry_prompt, image=audit_image, purpose="audit")
        retry_data = extract_json(retry_text or "", {})
        if isinstance(retry_data, dict) and retry_data:
            retry_data["raw_extracted_facts"] = facts
            data = normalize_audit_data(retry_data, cfg, state)
        if is_generic_audit(data):
            data = fallback_audit_from_facts(cfg, facts, state)
            data = normalize_audit_data(data, cfg, state)
    return data


def build_audit_messages(data: Dict[str, Any], state: Dict[str, Any]) -> List[str]:
    profile_name = state.get("profile_name") or "This profile"
    profile_type = state.get("profile_type_raw") or "Instagram profile"
    goal = state.get("target_location_or_audience") or "growth"
    score = str(data.get("overall_score", "0"))
    confidence = float(data.get("audit_confidence", 0.0) or 0.0)
    facts = data.get("visible_facts", {}) if isinstance(data.get("visible_facts"), dict) else {}
    strengths = as_list(data.get("specific_strengths"), [], 3)
    gaps = as_list(data.get("priority_gaps"), [], 4)

    messages = [
        (
            f"📍 CLIENTBOOST AUDIT\n\n"
            f"Profile: {profile_name}\n"
            f"Type: {profile_type}\n"
            f"Goal: {goal}\n\n"
            f"⭐ Rating: {score}/100\n"
            f"🔍 Screenshot confidence: {int(confidence * 100)}%\n\n"
            f"Visible evidence I used:\n"
            f"• Bio: {facts.get('bio_summary', 'not clearly visible')}\n"
            f"• Highlights: {facts.get('highlights_summary', 'not clearly visible')}\n"
            f"• Grid: {facts.get('grid_summary', 'not clearly visible')}"
        )
    ]

    if strengths:
        messages.append("🟢 WHAT IS ALREADY GOOD\n\n" + "\n".join([f"• {x}" for x in strengths]))
    if gaps:
        messages.append("🟡 PRIORITY GAPS\n\n" + "\n".join([f"• {x}" for x in gaps]))

    sections = [
        ("👤", "PROFILE IDENTITY", "profile_identity", "profile_identity_score"),
        ("✍️", "BIO + ACTION PATH", "bio_cta", "bio_cta_score"),
        ("⭕", "HIGHLIGHTS", "highlights", "highlights_score"),
        ("🎬", "POSTS + REELS", "posts_reels", "posts_reels_score"),
        ("🤝", "TRUST + CONVERSION", "trust_conversion", "trust_conversion_score"),
    ]
    for emoji, title, key, score_key in sections:
        sec = data.get(key, {})
        messages.append(
            f"{emoji} {title}\n\n"
            f"Score: {data.get(score_key, 0)}/10\n\n"
            f"Seen:\n• {sec.get('noticed', '')}\n\n"
            f"Fix:\n• {sec.get('fix', '')}"
        )

    messages.append(
        f"⚡ FIRST FIX\n\n"
        f"Start here:\n• {data.get('top_fix', '')}\n\n"
        f"This is the fastest way to make the profile feel clearer, more trusted, and easier to act on."
    )

    final_messages: List[str] = []
    for msg in messages:
        if len(msg) <= PREFERRED_DM_CHARS:
            final_messages.append(msg)
        else:
            final_messages.extend(split_text(msg, PREFERRED_DM_CHARS))
    return final_messages


def build_ai_conversion_message(state: Dict[str, Any], user_text: str, stage: str) -> str:
    cfg = PROFILE_CATEGORIES.get(state.get("profile_category") or "default_general_profile", PROFILE_CATEGORIES["default_general_profile"])
    strengths = state_json_list(state, "specific_strengths_json", [], 3)
    gaps = state_json_list(state, "priority_gaps_json", [], 4)
    monetization_note = "This is a content/page profile, so avoid forcing sales/bookings. Think followers, saves, shares, collabs, paid promos, resources, traffic, community, and trust." if profile_is_creator_monetization(state) else ""
    prompt = f"""
You are a senior ClientBoost strategist speaking in Instagram DM.
Write ONE short message only. Simple English. No paragraph longer than 2 short lines.
Use the screenshot-specific audit, not generic marketing advice.
Use 1-2 natural emojis. End with one clear question.
Do not mention bot, AI, backend, automation, quota, cooldown, or handover.
Do not ask for contact unless stage is ask_contact.
{monetization_note}

Stage: {stage}
User message: {user_text}

Profile:
Name: {state.get('profile_name')}
Type: {state.get('profile_type_raw')}
Goal: {state.get('target_location_or_audience')}
Category: {state.get('profile_category')}
Audit score: {state.get('audit_score')}/100
Main gap: {state.get('main_gap')}
Top fix: {state.get('top_fix')}
Specific strengths: {strengths}
Specific gaps: {gaps}
Preview bio: {state.get('preview_bio')}
Preview highlights: {state_json_list(state, 'preview_highlights_json', cfg.get('highlights', []), 6)}
Preview grid: {state_json_list(state, 'preview_grid_json', cfg.get('grid', []), 9)}

Stage meaning:
pain = acknowledge what is already good, then point to the real hidden gap.
reframe = explain why more posts alone will not fix this profile.
solution = show the exact first fix.
goal = ask what result or monetization path matters most.
timeline = ask how soon they want it improved.
scope = ask profile fix first, content direction, or full growth system. If user is unsure, give simple options.
ask_contact = ask best contact detail, WhatsApp or email, and mention senior review can suggest the right starting plan.
objection = answer briefly, then ask the next logical question.
""".strip()
    text = gemini_generate(prompt, purpose="conversion")
    if text:
        return clean_dm_text(text, MAX_DM_CHARS)
    return deterministic_conversion_message(state, stage, user_text)


def deterministic_conversion_message(state: Dict[str, Any], stage: str, user_text: str = "") -> str:
    main_gap = state.get("main_gap") or "the first screen is not turning attention into trust and action clearly enough"
    top_fix = state.get("top_fix") or "make the bio, highlights, first 9 posts, proof, and action path work together"
    strengths = state_json_list(state, "specific_strengths_json", [], 2)
    gaps = state_json_list(state, "priority_gaps_json", [main_gap], 2)
    strength_line = strengths[0] if strengths else "Your profile already has a base to build from."
    gap_line = gaps[0] if gaps else main_gap
    creator = profile_is_creator_monetization(state)

    if stage == "pain":
        return (
            "I checked it properly 👀\n\n"
            f"Good part: {strength_line}\n\n"
            f"Real gap: {gap_line}\n\n"
            "Want me to show the exact fix direction?"
        )
    if stage == "reframe":
        if creator:
            return (
                "Exactly ✅\n\n"
                "For a page, the fix is not only posting more.\n\n"
                "The page needs a clear follow reason, repeatable series, proof, and a monetization path.\n\n"
                "Should I show what ClientBoost would fix first?"
            )
        return (
            "Exactly ✅\n\n"
            "Posting more is not the first fix here.\n\n"
            "The first screen has to make people understand, trust, and know what to do next.\n\n"
            "Should I show what ClientBoost would fix first?"
        )
    if stage == "solution":
        return (
            "Here is the first move ⚡\n\n"
            f"• {top_fix}\n\n"
            "After that, every post has a job: proof, value, trust, or action.\n\n"
            "Want me to check what support fits you?"
        )
    if stage == "goal":
        if creator:
            return (
                "Good 🎯\n\n"
                "For this page, what result matters most?\n\n"
                "Followers / Saves / Shares / Paid promos / Brand collabs / Digital product / Community / Traffic"
            )
        return (
            "Good 🎯\n\n"
            "What matters most right now?\n\n"
            "Followers / Enquiries / Sales / Bookings / Trust / Clients / Brand deals"
        )
    if stage == "timeline":
        return (
            "Got it ✅\n\n"
            "How soon do you want this improved?\n\n"
            "Reply: immediately, this week, this month, or just exploring."
        )
    if stage == "scope":
        return (
            "That helps ✅\n\n"
            "Choose one starting point:\n"
            "1. Profile fix\n"
            "2. Content direction\n"
            "3. Full growth system\n"
            "4. Not sure"
        )
    if stage == "ask_contact":
        unclear = normalize(user_text) in UNCERTAIN_WORDS or normalize(user_text) in {"4", "not sure"}
        prefix = "No problem — a senior strategist can suggest the right starting plan after reviewing it properly ✅" if unclear else "Perfect ✅"
        return (
            f"{prefix}\n\n"
            "Send your best contact detail — WhatsApp number or email."
        )
    return (
        "Fair point ✅\n\n"
        f"The main issue is: {main_gap}\n\n"
        "Before suggesting anything, what result matters most to you?"
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
    bio = (state.get("preview_bio") or "").strip()
    if bio:
        return bio[:170]
    name = state.get("profile_name") or "Brand"
    if profile_is_creator_monetization(state):
        return f"{name} | Clear value, shareable series & collab-ready page."
    goal = state.get("target_location_or_audience") or "your audience"
    return f"{name} | Clear positioning for {goal}. Better proof, content pillars, and action path."


def grid_subtitle(label: str) -> str:
    mapping = {
        "CTA": "Action", "Book": "Action", "Order": "Action", "Follow": "Action", "Contact": "Lead",
        "Review": "Trust", "Proof": "Trust", "Trust": "Trust", "Story": "Human", "Tip": "Value",
        "Insight": "Value", "Hook": "Reach", "Reel": "Reach", "Offer": "Sales", "Food": "Craving",
        "Reminder": "Save", "Hadith": "Value", "Dua": "Share", "Quran": "Save", "Collab": "Money", "Promo": "Money",
    }
    for key, val in mapping.items():
        if key.lower() in str(label).lower():
            return val
    return "Content"


def generate_preview_image(state: Dict[str, Any]) -> str:
    category = state.get("profile_category") or "default_general_profile"
    cfg = PROFILE_CATEGORIES.get(category, PROFILE_CATEGORIES["default_general_profile"])
    style = state.get("preview_style") or cfg.get("style", "Premium Structured")
    pal = style_palette(style)

    W, H = 1080, 1350
    img = Image.new("RGB", (W, H), pal["bg"])
    draw = ImageDraw.Draw(img)

    font_lg = load_font(34, True)
    font_md = load_font(27, True)
    font_body = load_font(24, False)
    font_sm = load_font(19, False)
    font_xs = load_font(16, False)

    sx, sy = 70, 54
    sw, sh = 940, 1190
    rounded_rect(draw, (sx, sy, sx + sw, sy + sh), 48, pal["screen"])

    text = pal["text"]
    muted = pal["muted"]
    accent = pal["accent"]
    soft = pal["soft"]
    accent_text = (255, 255, 255)

    profile_name = (state.get("profile_name") or "Your Profile").strip()[:36]
    username_display = safe_username(profile_name)
    highlights = state_json_list(state, "preview_highlights_json", cfg.get("highlights", []), 6)
    grid_labels = state_json_list(state, "preview_grid_json", build_fallback_preview_grid(state, cfg), 9)
    strengths = state_json_list(state, "specific_strengths_json", [], 2)

    y = sy + 38
    draw.text((sx + 44, y), username_display, font=font_lg, fill=text)
    draw.text((sx + sw - 100, y + 2), "☰", font=font_lg, fill=text)

    y += 78
    cx, cy = sx + 104, y + 62
    draw.ellipse((cx - 58, cy - 58, cx + 58, cy + 58), fill=soft, outline=accent, width=5)
    initials = re.sub(r"[^A-Za-z0-9]", "", profile_name)[:2].upper() or "CB"
    iw = text_width(draw, initials, font_md)
    draw.text((cx - iw / 2, cy - 18), initials, font=font_md, fill=accent)

    stats_x = sx + 230
    stat_items = [("Bio", "Specific"), (str(len(highlights)), "Highlights"), ("9", "Post plan")]
    gap = 200
    for i, (top, bottom) in enumerate(stat_items):
        px = stats_x + i * gap
        tw = text_width(draw, top, font_md)
        draw.text((px - tw / 2, y + 28), top, font=font_md, fill=text)
        bw = text_width(draw, bottom, font_sm)
        draw.text((px - bw / 2, y + 66), bottom, font=font_sm, fill=muted)

    y += 145
    draw.text((sx + 44, y), profile_name, font=font_md, fill=text)
    y += 38
    bio = build_bio_direction(state, cfg)
    y = draw_wrapped(draw, bio, (sx + 44, y), font_body, text, sw - 88, max_lines=3)
    y += 8
    cta_line = "Clear value → proof → action"
    if strengths:
        cta_line = ("Upgrade: " + strengths[0])[:58]
    draw.text((sx + 44, y), cta_line, font=font_sm, fill=accent)
    y += 50

    btn_w = (sw - 110) // 3
    for i, label in enumerate(["Bio", "Proof", "Action"]):
        bx = sx + 44 + i * (btn_w + 11)
        rounded_rect(draw, (bx, y, bx + btn_w, y + 44), 12, soft)
        lw = text_width(draw, label, font_sm)
        draw.text((bx + btn_w / 2 - lw / 2, y + 11), label, font=font_sm, fill=text)
    y += 76

    spacing = (sw - 88) // max(1, len(highlights[:6]))
    for i, h in enumerate(highlights[:6]):
        hx = sx + 44 + i * spacing + spacing // 2
        draw.ellipse((hx - 37, y, hx + 37, y + 74), fill=pal["screen"], outline=accent, width=3)
        icon = h[:1].upper()
        iw = text_width(draw, icon, font_sm)
        draw.text((hx - iw / 2, y + 24), icon, font=font_sm, fill=accent)
        label = h[:10]
        lw = text_width(draw, label, font_xs)
        draw.text((hx - lw / 2, y + 86), label, font=font_xs, fill=muted)
    y += 130

    draw.line((sx + 44, y, sx + sw - 44, y), fill=soft, width=2)
    y += 22
    tabs = ["▦", "▶", "☷"]
    for i, t in enumerate(tabs):
        tx = sx + sw * (i + 0.5) / 3
        tw = text_width(draw, t, font_md)
        draw.text((tx - tw / 2, y), t, font=font_md, fill=accent if i == 0 else muted)
    y += 54

    tile_gap = 6
    tile_size = (sw - 88 - 2 * tile_gap) // 3
    for idx, label in enumerate(grid_labels[:9]):
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
        draw.rectangle((x, yy, x + tile_size, yy + 56), fill=accent)
        draw.text((x + 16, yy + 16), grid_subtitle(str(label)).upper()[:10], font=font_xs, fill=accent_text)
        label_short = str(label)[:22]
        label_lines = textwrap.wrap(label_short, width=12)[:2]
        ly = yy + tile_size / 2 - (len(label_lines) * 18)
        for line in label_lines:
            lw = text_width(draw, line, font_sm)
            draw.text((x + tile_size / 2 - lw / 2, ly), line, font=font_sm, fill=text)
            ly += 27
        sub = grid_subtitle(str(label))
        sw2 = text_width(draw, sub, font_xs)
        draw.text((x + tile_size / 2 - sw2 / 2, yy + tile_size / 2 + 38), sub, font=font_xs, fill=muted)

    footer = "ClientBoost specific direction — built from your audit"
    fw = text_width(draw, footer, font_sm)
    footer_fill = (80, 80, 80) if pal["bg"] != (20, 23, 30) else (210, 210, 210)
    draw.text((W / 2 - fw / 2, H - 58), footer, font=font_sm, fill=footer_fill)

    filename = f"preview_{uuid.uuid4().hex}.jpg"
    path = os.path.join(PREVIEW_DIR, filename)
    img.save(path, "JPEG", quality=92)
    return filename

# ============================================================
# FLOW MESSAGES
# ============================================================
def msg_follow_gate() -> str:
    return (
        f"👋 To unlock your free audit, follow @{FOLLOW_ACCOUNT_USERNAME} first.\n\n"
        "After that, tap I FOLLOWED."
    )


def msg_follow_gate_strict() -> str:
    return (
        f"🔒 Please follow @{FOLLOW_ACCOUNT_USERNAME} to continue.\n\n"
        "The free audit, preview, and next steps stay unlocked while you are following.\n\n"
        "After following, tap I FOLLOWED."
    )


def msg_why_follow() -> str:
    return (
        "✅ The audit is free, but it takes real review time.\n\n"
        "Following ClientBoost helps us keep the free audit available for more people.\n\n"
        f"Follow @{FOLLOW_ACCOUNT_USERNAME}, then tap I FOLLOWED."
    )


def msg_start_audit() -> str:
    return (
        "Great 👋\n\n"
        "I’ll check your Instagram profile like a growth strategist.\n\n"
        "First, what name should I use for the profile or brand?"
    )


def msg_ask_type(profile_name: str) -> str:
    return (
        f"Noted — {profile_name} ✅\n\n"
        "What is this profile about?\n\n"
        "A simple answer is enough — restaurant, creator, Islamic page, clinic, shop, coach, real estate, etc."
    )


def msg_ask_goal() -> str:
    return (
        "Got it 🎯\n\n"
        "What do you want this profile to bring you?\n\n"
        "Followers, customers, bookings, sales, trust, clients, brand deals, paid promos, or something else?"
    )


def msg_ask_screenshot() -> str:
    return (
        "Perfect 📸\n\n"
        "Now send one clear screenshot of the Instagram profile.\n\n"
        "Make sure it shows the username, bio, highlights, followers area, and first posts."
    )


def msg_after_audit() -> str:
    return (
        "✅ Your audit is ready.\n\n"
        "I used the visible bio, highlights, grid, and action path — not a template.\n\n"
        "What do you want to do next?"
    )


def msg_after_preview() -> str:
    return (
        "✅ This is the profile direction.\n\n"
        "It uses your audit facts for the bio, highlights, and first grid direction.\n\n"
        "Want ClientBoost to map the fix properly?"
    )


def msg_senior_review() -> str:
    return (
        "Done ✅\n\n"
        "Your profile case is ready for senior ClientBoost review.\n\n"
        "They’ll continue with your audit, preview direction, goal, and contact details already noted."
    )

# ============================================================
# FLOW HANDLERS
# ============================================================
def can_start_new_audit(state: Dict[str, Any]) -> Tuple[bool, str]:
    until = parse_iso(state.get("cooldown_until", ""))
    if until and until > datetime.now(timezone.utc):
        return False, (
            "✅ I already have your recent audit here.\n\n"
            "You can view it again, see the preview, or start fixing it."
        )
    return True, ""


def handle_start(sender_id: str, state: Dict[str, Any]):
    if state.get("step") == "senior_review" or str(state.get("handover", "false")).lower() == "true":
        return
    allowed, reason = can_start_new_audit(state)
    if not allowed and state.get("latest_audit"):
        send_dm(sender_id, reason, ["VIEW AUDIT", "SEE PREVIEW", "FIX THIS"])
        return
    if FOLLOW_REQUIRED and FOLLOW_VERIFY_MODE != "off" and not is_follow_verified(sender_id, state, force=False):
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
    if verified or FOLLOW_VERIFY_MODE in {"soft", "off"}:
        state = clear_audit_flow_fields(state)
        state["follow_verified"] = "true" if verified else "manual_soft"
        state["follow_verified_at"] = now_iso()
        state["step"] = "ask_profile_name"
        STORE.save_state(sender_id, state)
        send_dm(sender_id, msg_start_audit())
    else:
        send_dm(sender_id, (
            "I could not confirm it yet 👀\n\n"
            f"Please follow @{FOLLOW_ACCOUNT_USERNAME}, then tap I FOLLOWED again.\n\n"
            "Sometimes Instagram takes a few seconds to update."
        ), ["I FOLLOWED", "WHY FOLLOW", "CANCEL"])


def handle_profile_name(sender_id: str, state: Dict[str, Any], text: str):
    if len(text.strip()) < 2:
        send_dm(sender_id, "Please send the profile or brand name 🙂")
        return
    state["profile_name"] = text.strip()[:80]
    if not state.get("profile_category"):
        state["profile_category"] = detect_category(state["profile_name"])
    state["step"] = "ask_profile_type"
    STORE.save_state(sender_id, state)
    send_dm(sender_id, msg_ask_type(state["profile_name"]))


def handle_profile_type(sender_id: str, state: Dict[str, Any], text: str):
    if len(text.strip()) < 2:
        send_dm(sender_id, "Please tell me what this profile is about 🙂")
        return
    state["profile_type_raw"] = text.strip()[:120]
    state["profile_category"] = detect_category(f"{state.get('profile_name', '')} {text}")
    state["step"] = "ask_goal_or_audience"
    STORE.save_state(sender_id, state)
    send_dm(sender_id, msg_ask_goal())


def handle_goal_or_audience(sender_id: str, state: Dict[str, Any], text: str):
    if len(text.strip()) < 2:
        send_dm(sender_id, "What do you want from this profile — followers, customers, bookings, sales, trust, brand deals, or money? 🎯")
        return
    state["target_location_or_audience"] = text.strip()[:160]
    # Re-check category using goal too; this fixes cases like name=Haqikat.e.islam, type=Page, goal=All.
    state["profile_category"] = detect_category(f"{state.get('profile_name', '')} {state.get('profile_type_raw', '')} {text}")
    state["step"] = "ask_screenshot"
    STORE.save_state(sender_id, state)
    send_dm(sender_id, msg_ask_screenshot())


def handle_screenshot(sender_id: str, state: Dict[str, Any], image_url: str):
    send_dm(sender_id, "Got the screenshot 📸\n\nI’m reading the visible bio, highlights, grid, and post covers now.")
    state["step"] = "processing_audit"
    STORE.save_state(sender_id, state)

    image = download_image(image_url)
    if image is None:
        state["step"] = "ask_screenshot"
        STORE.save_state(sender_id, state)
        send_dm(sender_id, "I could not read that screenshot properly 😕\n\nPlease send a clearer Instagram profile screenshot.")
        return

    data = audit_profile(image, state)
    if data.get("audit_error"):
        print("AUDIT_ERROR_SAFE_FALLBACK:", data.get("audit_error"), data.get("user_message"))
        cfg = PROFILE_CATEGORIES.get(state.get("profile_category"), PROFILE_CATEGORIES["default_general_profile"])
        facts = fallback_facts_from_state(state, cfg, str(data.get("audit_error")))
        data = fallback_audit_from_facts(cfg, facts, state)
        data["raw_extracted_facts"] = facts
        data = normalize_audit_data(data, cfg, state)

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

    state["visible_facts_json"] = json.dumps(data.get("visible_facts", {}), ensure_ascii=False)
    state["raw_extracted_facts_json"] = json.dumps(data.get("raw_extracted_facts", {}), ensure_ascii=False)
    state["specific_strengths_json"] = to_json_list(data.get("specific_strengths"), [], 3)
    state["priority_gaps_json"] = to_json_list(data.get("priority_gaps"), [], 4)
    state["preview_bio"] = str(data.get("preview_bio", ""))[:180]
    cfg = PROFILE_CATEGORIES.get(state.get("profile_category"), PROFILE_CATEGORIES["default_general_profile"])
    state["preview_highlights_json"] = to_json_list(data.get("preview_highlights"), cfg.get("highlights", []), 6)
    state["preview_grid_json"] = to_json_list(data.get("preview_grid"), build_fallback_preview_grid(state, cfg), 9)
    state["audit_confidence"] = str(data.get("audit_confidence", ""))

    # New audit means any old preview must not be reused.
    state["preview_filename"] = ""
    state["preview_url"] = ""
    state["preview_generated"] = "false"

    audit_messages = build_audit_messages(data, state)
    state["latest_audit"] = "\n\n---CB-AUDIT-SECTION---\n\n".join(audit_messages)
    state["lead_temperature"] = "warm"
    state["step"] = "audit_sent"
    state["cooldown_until"] = (datetime.now(timezone.utc) + timedelta(days=AUDIT_COOLDOWN_DAYS)).isoformat()
    STORE.save_state(sender_id, state)

    for msg in audit_messages:
        send_dm(sender_id, msg)
        time.sleep(0.7)

    send_dm(sender_id, msg_after_audit(), ["SEE PREVIEW", "FIX THIS", "VIEW AUDIT"])


def handle_latest(sender_id: str, state: Dict[str, Any]):
    audit = state.get("latest_audit")
    if not audit:
        send_dm(sender_id, "No audit is saved yet 🙂\n\nSend GROWTH to start your free audit.", ["GROWTH"])
        return
    if "---CB-AUDIT-SECTION---" in audit:
        audit_parts = [p.strip() for p in audit.split("---CB-AUDIT-SECTION---") if p.strip()]
    else:
        audit_parts = split_text(audit, MAX_DM_CHARS)
    for part in audit_parts:
        for msg in split_text(part, MAX_DM_CHARS):
            send_dm(sender_id, msg)
            time.sleep(0.7)
    send_dm(sender_id, "✅ That’s the audit again.\n\nWhat would you like next?", ["SEE PREVIEW", "FIX THIS"])


def preview_file_exists(filename: str) -> bool:
    return bool(filename and os.path.exists(os.path.join(PREVIEW_DIR, filename)))


def handle_preview(sender_id: str, state: Dict[str, Any]):
    if not state.get("latest_audit"):
        send_dm(sender_id, "The preview comes after the audit 🙂\n\nSend GROWTH first, then I’ll ask 3 easy questions and review the profile properly.", ["GROWTH"])
        state["step"] = "new"
        STORE.save_state(sender_id, state)
        return
    if FOLLOW_REQUIRED and FOLLOW_VERIFY_MODE == "strict" and not is_follow_verified(sender_id, state, force=True):
        state["step"] = "follow_gate"
        STORE.save_state(sender_id, state)
        send_dm(sender_id, f"To unlock the free preview, follow @{FOLLOW_ACCOUNT_USERNAME} first 👋\n\nOnce done, tap I FOLLOWED.", ["I FOLLOWED", "WHY FOLLOW", "CANCEL"])
        return

    filename = state.get("preview_filename", "")
    if preview_file_exists(filename):
        send_dm(sender_id, "Here is your specific profile preview again 👇")
    else:
        already_generated = str(state.get("preview_generated", "false")).lower() == "true"
        if already_generated:
            send_dm(sender_id, "The saved preview file was cleared by the server, so I’m rebuilding the same audit-based preview once more 🎨")
        else:
            send_dm(sender_id, "Creating your specific profile preview now 🎨\n\nI’ll use the audit facts to show a clearer bio, highlights, and first-grid direction.")
        filename = generate_preview_image(state)
        state["preview_filename"] = filename
        if PUBLIC_BASE_URL:
            state["preview_url"] = f"{PUBLIC_BASE_URL}/preview/{filename}"

    state["step"] = "preview_sent"
    state["preview_requested"] = "true"
    state["preview_generated"] = "true"
    state["lead_temperature"] = "hot"
    STORE.save_state(sender_id, state)

    if PUBLIC_BASE_URL and state.get("preview_url"):
        result = send_image(sender_id, state["preview_url"])
        if result.get("error"):
            send_dm(sender_id, "The preview is ready, but Instagram did not load the image here 😕\n\nPlease try SEE PREVIEW again in a moment.")
    else:
        send_dm(sender_id, "The preview is ready, but I could not send the image here 😕\n\nPlease try again in a moment.")
    time.sleep(0.8)
    send_dm(sender_id, msg_after_preview(), ["FIX THIS", "VIEW AUDIT"])


def start_conversion(sender_id: str, state: Dict[str, Any], text: str = ""):
    if not state.get("latest_audit"):
        send_dm(sender_id, "I can help with that 🙂\n\nFirst I need to audit the profile so the advice is specific, not generic.\n\nSend GROWTH to start the free audit.", ["GROWTH"])
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
        state["goal"] = text.strip()[:180]
    elif step == "conversion_timeline" and norm not in YES_WORDS:
        state["timeline"] = text.strip()[:150]
    elif step == "conversion_scope" and norm not in YES_WORDS:
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
        send_dm(sender_id, "Good question 🙂\n\nThe audit is free. First I’ll review the profile, then the next step can be suggested properly.\n\nSend GROWTH to start.", ["GROWTH"])
        return
    state["objection_type"] = objection_type
    state["lead_temperature"] = "hot"
    if objection_type in ["thinking_delay", "not_now"]:
        state["lead_temperature"] = "warm"
        STORE.save_state(sender_id, state)
        send_dm(sender_id, "No issue 🙂\n\nKeep the audit as your starting point.\n\nWhen you want ClientBoost to map the fix properly, reply FIX THIS.", ["FIX THIS", "VIEW AUDIT"])
        return
    if objection_type == "price_question":
        msg = price_message(state)
    elif objection_type == "asks_details":
        msg = details_message(state)
    elif objection_type == "asks_results":
        msg = results_message(state)
    elif objection_type == "trust_issue":
        msg = "Fair question ✅\n\nThe audit is based on your visible profile screenshot, and the next step is only suggested after your goal is clear.\n\nWhat result matters most to you right now?"
    else:
        msg = build_ai_conversion_message(state, text, "objection")
    state["step"] = "conversion_goal"
    STORE.save_state(sender_id, state)
    send_dm(sender_id, msg)


def price_message(state: Dict[str, Any]) -> str:
    if profile_is_creator_monetization(state):
        return (
            "Cost depends on the starting scope ✅\n\n"
            "For this type of page, the usual options are:\n"
            "• Profile + bio/highlight fix\n"
            "• Content direction + first 9-post plan\n"
            "• Full growth + monetization setup\n\n"
            "A senior strategist will quote the right option after reviewing your audit. What result matters most first?"
        )
    return (
        "Cost depends on the scope ✅\n\n"
        "Usually it starts from one of these:\n"
        "• Profile fix\n"
        "• Growth setup\n"
        "• Full content + lead system\n\n"
        "Before pricing, what result matters most — enquiries, sales, bookings, trust, or followers?"
    )


def details_message(state: Dict[str, Any]) -> str:
    if profile_is_creator_monetization(state):
        return (
            "For this page, ClientBoost would mainly fix 4 things 🧩\n\n"
            "• Follow reason\n"
            "• Content series\n"
            "• Proof/trust posts\n"
            "• Monetization path: collabs, promos, resources, or traffic\n\n"
            "Which result do you want first?"
        )
    return (
        "The fix usually covers 4 things 🧩\n\n"
        "• Profile positioning\n"
            "• Bio, highlights, and CTA\n"
        "• Content direction\n"
        "• Trust + enquiry path\n\n"
        "What result do you want first?"
    )


def results_message(state: Dict[str, Any]) -> str:
    return (
        "Results depend on the profile, niche, consistency, and offer ✅\n\n"
        "The first goal is to make the profile clearer, more trusted, and easier to act on.\n\n"
        "Then growth becomes easier because the content has direction. What result matters most to you?"
    )


def handle_contact(sender_id: str, state: Dict[str, Any], text: str):
    contact, ctype = contact_from_text(text)
    if not contact:
        send_dm(sender_id, "Send your best contact detail — WhatsApp number or email 🙂")
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


def handle_post_handover_message(sender_id: str, state: Dict[str, Any], text: str):
    intent = direct_intent(text)
    if intent == "owner_reset":
        STORE.reset_state(sender_id)
        send_dm(sender_id, "Owner reset complete ✅\n\nSend GROWTH to test from the beginning.", ["GROWTH"])
        return
    if intent == "request_latest":
        handle_latest(sender_id, state)
        return
    if intent == "request_preview":
        handle_preview(sender_id, state)
        return
    if intent == "price_question":
        send_dm(sender_id, price_message(state))
        return
    if intent == "asks_details":
        send_dm(sender_id, details_message(state))
        return
    if intent == "asks_results":
        send_dm(sender_id, results_message(state))
        return
    if intent == "trust_issue":
        send_dm(sender_id, "Your details are saved ✅\n\nA senior ClientBoost strategist will continue from the audit and preview, so they can suggest the right plan properly.")
        return
    # Stay mostly silent after handover so a human can own the chat.
    return


def handle_unclear(sender_id: str, state: Dict[str, Any]):
    step = state.get("step", "new")
    if step == "follow_gate":
        send_dm(sender_id, "Choose one option 🙂", ["I FOLLOWED", "WHY FOLLOW", "CANCEL"])
    elif step == "ask_profile_name":
        send_dm(sender_id, "What name should I use for the profile or brand? 🙂")
    elif step == "ask_profile_type":
        send_dm(sender_id, "What is this profile about? 🙂")
    elif step == "ask_goal_or_audience":
        send_dm(sender_id, "What do you want from this profile — followers, customers, bookings, sales, trust, brand deals, or money? 🎯")
    elif step == "ask_screenshot":
        send_dm(sender_id, "Send one clear screenshot of the Instagram profile 📸")
    elif step == "audit_sent":
        send_dm(sender_id, "What would you like next? 🙂", ["SEE PREVIEW", "FIX THIS", "VIEW AUDIT"])
    elif step == "preview_sent":
        send_dm(sender_id, "What would you like next? 🙂", ["FIX THIS", "VIEW AUDIT"])
    elif step.startswith("conversion"):
        advance_conversion(sender_id, state, text="yes")
    elif step == "ask_contact":
        send_dm(sender_id, "Send your best contact detail — WhatsApp number or email 🙂")
    elif step == "senior_review":
        return
    else:
        send_dm(sender_id, "Send GROWTH to start your free Instagram audit 🚀", ["GROWTH"])

def should_recheck_follow_for_message(state: Dict[str, Any], intent: Optional[str]) -> bool:
    if not FOLLOW_REQUIRED or FOLLOW_VERIFY_MODE != "strict" or not FOLLOW_RECHECK_EVERY_MESSAGE:
        return False

    step = state.get("step", "new")
    allowed_intents = {"owner_reset", "start_audit", "follow_confirmed", "generic_done", "ask_why_follow", "cancel", "soft_reset"}
    if intent in allowed_intents or step == "follow_gate":
        return False

    protected_steps = {
        "ask_profile_name", "ask_profile_type", "ask_goal_or_audience", "ask_screenshot",
        "processing_audit", "audit_sent", "preview_sent", "conversion_pain",
        "conversion_reframe", "conversion_solution", "conversion_goal", "conversion_timeline",
        "conversion_scope", "ask_contact", "senior_review"
    }
    return step in protected_steps or str(state.get("handover", "false")).lower() == "true"


def enforce_follow_if_needed(sender_id: str, state: Dict[str, Any], intent: Optional[str]) -> bool:
    if should_recheck_follow_for_message(state, intent):
        if not is_follow_verified(sender_id, state, force=True):
            state["step"] = "follow_gate"
            STORE.save_state(sender_id, state)
            send_dm(sender_id, msg_follow_gate_strict(), ["I FOLLOWED", "WHY FOLLOW", "CANCEL"])
            return True
    return False


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

    if text == OWNER_RESET_CODE:
        STORE.reset_state(sender_id)
        send_dm(sender_id, "Owner reset complete ✅\n\nSend GROWTH to test from the beginning.", ["GROWTH"])
        return

    early_intent = direct_intent(text) if text else None
    if enforce_follow_if_needed(sender_id, state, early_intent):
        return
    state = STORE.get_state(sender_id)

    # After senior review, answer only useful questions like price/details/latest/preview, otherwise stay silent.
    if state.get("step") == "senior_review" or str(state.get("handover", "false")).lower() == "true":
        if text:
            handle_post_handover_message(sender_id, state, text)
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
                send_dm(sender_id, "I received the image 📸\n\nI already have your recent audit here. What would you like next?", ["SEE PREVIEW", "FIX THIS", "VIEW AUDIT"])
            else:
                send_dm(sender_id, "I received the image 📸\n\nFirst send GROWTH so I can review it in the right order.", ["GROWTH"])
        return

    if not text:
        handle_unclear(sender_id, state)
        return

    step = state.get("step", "new")
    t_norm = normalize(text)
    pre_intent = direct_intent(text)

    priority_commands = {"cancel", "reset", "restart", "don't", "dont"}
    if t_norm in priority_commands:
        intent = pre_intent
    elif step == "follow_gate" and pre_intent == "generic_done":
        intent = "follow_confirmed"
    elif step == "ask_profile_name":
        intent = "answer_profile_name"
    elif step == "ask_profile_type":
        intent = "answer_profile_type"
    elif step == "ask_goal_or_audience":
        intent = "answer_goal_or_audience"
    elif step == "conversion_goal":
        if pre_intent in OBJECTION_INTENTS:
            intent = pre_intent
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
        if float(understood.get("confidence", 0) or 0) >= 0.65:
            intent = understood.get("intent")
            if understood.get("profile_category") in PROFILE_CATEGORIES:
                state["profile_category"] = understood.get("profile_category")
            if understood.get("clean_answer") and intent in [
                "answer_profile_type", "answer_profile_name", "answer_goal", "answer_timeline",
                "answer_scope", "answer_goal_or_audience"
            ]:
                text = understood.get("clean_answer")
            if understood.get("objection_type") and understood.get("objection_type") != "none":
                state["objection_type"] = understood.get("objection_type")
        else:
            intent = "unclear"

    if intent == "owner_reset":
        STORE.reset_state(sender_id)
        send_dm(sender_id, "Owner reset complete ✅\n\nSend GROWTH to test from the beginning.", ["GROWTH"])
    elif intent == "start_audit":
        handle_start(sender_id, state)
    elif intent == "follow_confirmed" or (intent == "generic_done" and step == "follow_gate"):
        handle_follow_confirmed(sender_id, state)
    elif intent == "ask_why_follow":
        send_dm(sender_id, msg_why_follow(), ["I FOLLOWED", "CANCEL"])
    elif intent == "cancel":
        state["step"] = "new"
        STORE.save_state(sender_id, state)
        send_dm(sender_id, "Cancelled ✅\n\nSend GROWTH whenever you are ready.", ["GROWTH"])
    elif intent == "soft_reset":
        state["step"] = "new"
        STORE.save_state(sender_id, state)
        send_dm(sender_id, "No problem ✅\n\nSend GROWTH whenever you are ready.", ["GROWTH"])
    elif intent == "answer_profile_name":
        handle_profile_name(sender_id, state, text)
    elif intent == "answer_profile_type":
        handle_profile_type(sender_id, state, text)
    elif intent == "answer_goal_or_audience":
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
    elif intent == "yes":
        if step in ["audit_sent", "preview_sent"]:
            start_conversion(sender_id, state, text)
        elif step in ["conversion_pain", "conversion_reframe", "conversion_solution"]:
            advance_conversion(sender_id, state, text)
        elif step == "ask_contact":
            send_dm(sender_id, "Send your best contact detail — WhatsApp number or email 🙂")
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
    return f"ClientBoost Bot is running — {APP_VERSION}", 200


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "service": "clientboost-bot",
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
