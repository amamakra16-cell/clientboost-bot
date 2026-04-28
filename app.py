# ============================================================
# ClientBoost.in — Instagram DM Audit Bot
# Instagram DM Automation + Gemini Vision Audit + Human Handover
# ============================================================

from flask import Flask, request, jsonify
import requests
import os
import time
import json
import threading
import io
from PIL import Image
import google.generativeai as genai

app = Flask(__name__)

# ============================================================
# ENVIRONMENT VARIABLES
# ============================================================

VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "clientboost2024")
PAGE_ACCESS_TOKEN = os.environ.get("PAGE_ACCESS_TOKEN", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GRAPH_API_VERSION = os.environ.get("GRAPH_API_VERSION", "v21.0")

# Gemini fallback model setup.
# Render can optionally use:
# GEMINI_MODELS = gemini-2.5-flash,gemini-2.0-flash
model_env = os.environ.get("GEMINI_MODELS", "").strip()
single_model_env = os.environ.get("GEMINI_MODEL", "").strip()

if model_env:
    raw_models = model_env
elif single_model_env:
    raw_models = f"{single_model_env},gemini-2.5-flash,gemini-2.0-flash"
else:
    raw_models = "gemini-2.5-flash,gemini-2.0-flash"

GEMINI_MODELS = []
for model_name in raw_models.split(","):
    model_name = model_name.strip()
    if model_name and model_name not in GEMINI_MODELS:
        GEMINI_MODELS.append(model_name)

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# ============================================================
# FILE STORAGE
# Free setup uses local JSON files.
# Good for testing and early launch.
# ============================================================

STATE_FILE = "user_states.json"
COOLDOWN_FILE = "user_cooldowns.json"

# Used to ignore echo messages created by our own bot replies.
# This prevents the bot from accidentally activating handover
# because of its own messages.
recent_bot_sends = {}


def load_json(filename):
    try:
        with open(filename, "r", encoding="utf-8") as file:
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_json(filename, data):
    try:
        with open(filename, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
    except Exception as error:
        print(f"Failed to save {filename}: {error}")


user_states = load_json(STATE_FILE)
user_cooldowns = load_json(COOLDOWN_FILE)

# ============================================================
# INSTAGRAM DM SENDER
# ============================================================

def send_dm(recipient_id, message):
    if not PAGE_ACCESS_TOKEN:
        print("PAGE_ACCESS_TOKEN is missing.")
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
        response = requests.post(
            url,
            json=payload,
            params=params,
            headers=headers,
            timeout=15
        )

        print("DM send status:", response.status_code, response.text[:500])
        response.raise_for_status()

        # Mark this user as recently messaged by bot.
        recent_bot_sends[str(recipient_id)] = time.time()

        return response.json()

    except requests.exceptions.RequestException as error:
        print(f"Failed to send DM to {recipient_id}: {error}")
        return {"error": str(error)}

# ============================================================
# IMAGE DOWNLOAD
# ============================================================

def download_image(image_url):
    try:
        headers = {"Authorization": f"Bearer {PAGE_ACCESS_TOKEN}"}

        response = requests.get(
            image_url,
            headers=headers,
            timeout=20
        )

        response.raise_for_status()

        image = Image.open(io.BytesIO(response.content))
        return image

    except Exception as error:
        print(f"Failed to download image: {error}")
        return None

# ============================================================
# GEMINI FALLBACK GENERATOR
# ============================================================

def generate_text_with_fallback(contents):
    last_error = None

    for model_name in GEMINI_MODELS:
        try:
            print(f"Trying Gemini model: {model_name}")

            model = genai.GenerativeModel(model_name)
            response = model.generate_content(contents)

            try:
                response_text = response.text
            except Exception as text_error:
                last_error = text_error
                print(f"Gemini response had no readable text ({model_name}): {text_error}")
                continue

            if response_text and response_text.strip():
                return response_text.strip()

            last_error = "Empty Gemini response"
            print(f"Gemini model returned empty text: {model_name}")

        except Exception as error:
            last_error = error
            print(f"Gemini model failed ({model_name}): {error}")

    print(f"All Gemini models failed: {last_error}")
    return None

# ============================================================
# GEMINI AUDIT ANALYSER
# ============================================================

def analyse_screenshot_with_gemini(image, name, business_type, location):
    prompt = f"""
You are the senior Instagram growth auditor for ClientBoost, a global digital growth agency.

ClientBoost helps businesses grow through:
- Strategy
- SEO
- Ads
- Content
- Social media
- Web
- Lead generation
- AI automation
- Conversion systems

A business owner requested a free Instagram audit.

Business details:
- Business Name: {name}
- Business Type: {business_type}
- City/Country: {location}

Analyse the Instagram profile screenshot carefully.

Check only what is visible:
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
- Do not invent fake data.
- Do not claim guaranteed results.
- Do not sound generic.
- Do not overpraise.
- Be direct, useful and professional.
- If something is not visible, say it is not visible.
- Keep the audit practical and easy to act on.

Write the audit in this exact structure:

━━━━━━━━━━━━━━━━━━━━━━
CLIENTBOOST FREE INSTAGRAM AUDIT
━━━━━━━━━━━━━━━━━━━━━━

1. PROFILE SCORE
Score: X/10

What is working:
- [specific point from screenshot]

What needs fixing:
- [specific point from screenshot]

Top Fix:
[one clear action]

━━━━━━━━━━━━━━━━━━━━━━
2. BIO FIXES

Current issue:
[specific issue]

Recommended bio:
[write a better bio for this business]

Why this works:
[short reason]

━━━━━━━━━━━━━━━━━━━━━━
3. CONTENT FIXES

What is working:
- [specific point]

What needs fixing:
- [specific point]

3 content ideas for this business:
- [idea 1]
- [idea 2]
- [idea 3]

━━━━━━━━━━━━━━━━━━━━━━
4. HIGHLIGHT FIXES

Current issue:
[specific issue]

Recommended highlights:
- Services
- Results / Proof
- How It Works
- FAQ
- Contact / Book Now

Top Fix:
[one clear action]

━━━━━━━━━━━━━━━━━━━━━━
5. LEAD FLOW FIXES

Current issue:
[specific issue]

Fix:
[how to make the profile convert better]

━━━━━━━━━━━━━━━━━━━━━━
6. 7-DAY ACTION PLAN

Day 1:
[task]

Day 2:
[task]

Day 3:
[task]

Day 4:
[task]

Day 5:
[task]

Day 6:
[task]

Day 7:
[task]

━━━━━━━━━━━━━━━━━━━━━━
7. FINAL NOTE

End with this soft CTA:
"If you want ClientBoost to help implement these fixes, reply INTERESTED and our team will guide you."
"""

    try:
        return generate_text_with_fallback([prompt, image])

    except Exception as error:
        print(f"Gemini Vision error: {error}")
        return None

# ============================================================
# SPLIT LONG MESSAGE FOR INSTAGRAM DM
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
# SEND FULL AUDIT
# ============================================================

def send_full_audit(sender_id, name, business_type, location, image):
    send_dm(
        sender_id,
        "Screenshot received.\n\n"
        "Analysing your Instagram profile now. This usually takes 20–30 seconds."
    )

    audit_text = analyse_screenshot_with_gemini(
        image=image,
        name=name,
        business_type=business_type,
        location=location
    )

    if not audit_text:
        send_dm(
            sender_id,
            "Sorry, the AI analysis could not complete right now.\n\n"
            "Please resend the screenshot once, or try again in a few minutes."
        )
        return

    intro = (
        "Your free ClientBoost Instagram audit is ready.\n\n"
        f"Business: {name}\n"
        f"Type: {business_type}\n"
        f"Location: {location}\n\n"
        "Here are the fixes:"
    )

    send_dm(sender_id, intro)
    time.sleep(1)

    for part in split_message(audit_text):
        send_dm(sender_id, part)
        time.sleep(1.2)

    send_dm(
        sender_id,
        "If you want ClientBoost to help implement these fixes, reply INTERESTED and our team will guide you."
    )

# ============================================================
# USER STATE HELPERS
# ============================================================

def get_state(sender_id):
    sender_id = str(sender_id)

    if sender_id not in user_states:
        user_states[sender_id] = {
            "step": "new",
            "data": {},
            "handover": False
        }

    if "handover" not in user_states[sender_id]:
        user_states[sender_id]["handover"] = False

    if "data" not in user_states[sender_id]:
        user_states[sender_id]["data"] = {}

    if "step" not in user_states[sender_id]:
        user_states[sender_id]["step"] = "new"

    return user_states[sender_id]


def save_states():
    save_json(STATE_FILE, user_states)


def activate_handover(sender_id):
    sender_id = str(sender_id)
    state = get_state(sender_id)
    state["handover"] = True
    state["step"] = "human_handover"
    save_states()


def reset_user(sender_id):
    sender_id = str(sender_id)

    user_states[sender_id] = {
        "step": "new",
        "data": {},
        "handover": False
    }

    # Reset cooldown too, useful during testing.
    if sender_id in user_cooldowns:
        del user_cooldowns[sender_id]
        save_json(COOLDOWN_FILE, user_cooldowns)

    save_states()


def is_recent_bot_echo(user_id, seconds=60):
    user_id = str(user_id)

    last_sent = recent_bot_sends.get(user_id)

    if not last_sent:
        return False

    return (time.time() - last_sent) <= seconds

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

    # Reset should always work.
    if text in ["reset", "restart", "start over", "start again"]:
        reset_user(sender_id)

        send_dm(
            sender_id,
            "Reset done.\n\n"
            "Type GROWTH whenever you are ready for your free Instagram audit."
        )
        return

    # If human handover is active, bot stays silent.
    if state.get("handover") is True:
        print(f"Human handover active for {sender_id}. Bot ignored message.")
        return

    # Stop / opt-out.
    if text in ["stop", "cancel", "unsubscribe"]:
        activate_handover(sender_id)

        send_dm(
            sender_id,
            "No problem. We will stop the automated messages here."
        )
        return

    # Interested = handover to team.
    if "interested" in text:
        activate_handover(sender_id)

        send_dm(
            sender_id,
            "Great. Our team will take it from here.\n\n"
            "Please send your best WhatsApp number and a short note about what you want help with."
        )
        return

    # Image received at correct step.
    if image_url and step == "ask_screenshot":
        image = download_image(image_url)

        if not image:
            send_dm(
                sender_id,
                "I could not download the screenshot.\n\n"
                "Please send it again."
            )
            return

        user_cooldowns[sender_id] = time.time()
        save_json(COOLDOWN_FILE, user_cooldowns)

        send_full_audit(
            sender_id=sender_id,
            name=data.get("name", "Your business"),
            business_type=data.get("type", "Business"),
            location=data.get("location", "Your location"),
            image=image
        )

        user_states[sender_id] = {
            "step": "new",
            "data": {},
            "handover": False
        }
        save_states()
        return

    # Image received too early.
    if image_url and step != "ask_screenshot":
        send_dm(
            sender_id,
            "Thanks for the image.\n\n"
            "Type GROWTH first so I can start your free audit properly."
        )
        return

    # Start audit flow.
    if "growth" in text and step == "new":
        last_time = user_cooldowns.get(sender_id, 0)

        if time.time() - last_time < 86400:
            send_dm(
                sender_id,
                "You already received a free audit recently.\n\n"
                "Reply INTERESTED if you want ClientBoost to help implement the fixes."
            )
            return

        user_states[sender_id] = {
            "step": "ask_name",
            "data": {},
            "handover": False
        }
        save_states()

        send_dm(
            sender_id,
            "Welcome to ClientBoost.\n\n"
            "We will do a free Instagram audit for your business.\n\n"
            "First, what is your business name?"
        )
        return

    # Step 1: Business name.
    if step == "ask_name":
        if len(raw_text) < 2:
            send_dm(sender_id, "Please send a valid business name.")
            return

        data["name"] = raw_text
        state["data"] = data
        state["step"] = "ask_type"
        save_states()

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
            "10. Other"
        )
        return

    # Step 2: Business type.
    # If user selects Other, bot asks exact type.
    if step == "ask_type":
        if len(raw_text) < 1:
            send_dm(sender_id, "Please send your business type.")
            return

        normalized = text.replace(".", "").strip()

        type_map = {
            "1": "Restaurant",
            "restaurant": "Restaurant",
            "2": "Cafe",
            "cafe": "Cafe",
            "coffee shop": "Cafe",
            "3": "Salon",
            "salon": "Salon",
            "4": "Gym",
            "gym": "Gym",
            "fitness": "Gym",
            "5": "Boutique",
            "boutique": "Boutique",
            "6": "Clinic",
            "clinic": "Clinic",
            "7": "Real Estate",
            "real estate": "Real Estate",
            "realestate": "Real Estate",
            "realtor": "Real Estate",
            "8": "E-commerce",
            "ecommerce": "E-commerce",
            "e-commerce": "E-commerce",
            "online store": "E-commerce",
            "9": "Personal Brand",
            "personal brand": "Personal Brand",
            "creator": "Personal Brand",
            "influencer": "Personal Brand",
            "10": "Other",
            "other": "Other"
        }

        selected_type = type_map.get(normalized, raw_text)

        if selected_type == "Other":
            state["step"] = "ask_other_type"
            save_states()

            send_dm(
                sender_id,
                "No problem.\n\n"
                "Please type your exact business type.\n\n"
                "Example: dental clinic, car service, coaching, software company, interior design, etc."
            )
            return

        data["type"] = selected_type
        state["data"] = data
        state["step"] = "ask_location"
        save_states()

        send_dm(
            sender_id,
            f"{selected_type} — noted.\n\n"
            "Which city or country do you serve?"
        )
        return

    # Step 2B: Exact business type after Other.
    if step == "ask_other_type":
        if len(raw_text) < 2:
            send_dm(sender_id, "Please type your exact business type.")
            return

        data["type"] = raw_text
        state["data"] = data
        state["step"] = "ask_location"
        save_states()

        send_dm(
            sender_id,
            f"{raw_text} — noted.\n\n"
            "Which city or country do you serve?"
        )
        return

    # Step 3: Location.
    if step == "ask_location":
        if len(raw_text) < 2:
            send_dm(sender_id, "Please send your city or country.")
            return

        data["location"] = raw_text
        state["data"] = data
        state["step"] = "ask_screenshot"
        save_states()

        send_dm(
            sender_id,
            f"{raw_text} — perfect.\n\n"
            "Now send a screenshot of your Instagram profile.\n\n"
            "Make sure it shows:\n"
            "- Your bio\n"
            "- Story highlights\n"
            "- Follower count\n"
            "- First few posts\n\n"
            "Once you send it, we will analyse your profile and send your audit."
        )
        return

    # Default fallback.
    send_dm(
        sender_id,
        "Hi. Type GROWTH to get your free Instagram audit from ClientBoost."
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
                # 1. Our bot sending a DM through API
                # 2. Your team manually replying from Instagram inbox
                #
                # If it is a recent bot echo, ignore it.
                # If it is not a recent bot echo, treat it as team handover.
                if message_obj.get("is_echo"):
                    if recipient_id and is_recent_bot_echo(recipient_id):
                        print(f"Ignored recent bot echo for {recipient_id}")
                        continue

                    if recipient_id:
                        activate_handover(recipient_id)
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


@app.route("/", methods=["GET"])
def home():
    return "ClientBoost Bot is running", 200

# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)