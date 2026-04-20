# ============================================================
# ClientBoost.in — Instagram DM Automation Bot
# With Google Gemini Vision — 100% Free Forever
# ============================================================

from flask import Flask, request, jsonify
import requests
import os
import time
import json
import threading
import base64
import google.generativeai as genai
from PIL import Image
import io

app = Flask(__name__)

# ============================================================
# ENVIRONMENT VARIABLES
# Set all of these in Render dashboard
# ============================================================
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "clientboost2024")
PAGE_ACCESS_TOKEN = os.environ.get("PAGE_ACCESS_TOKEN", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# Configure Gemini
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

# ============================================================
# PERSISTENT STATE STORAGE
# ============================================================
STATE_FILE = "user_states.json"
COOLDOWN_FILE = "user_cooldowns.json"

def load_json(filename):
    try:
        with open(filename, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_json(filename, data):
    try:
        with open(filename, "w") as f:
            json.dump(data, f)
    except Exception as e:
        print(f"❌ Failed to save {filename}: {e}")

user_states = load_json(STATE_FILE)
user_cooldowns = load_json(COOLDOWN_FILE)


# ============================================================
# SEND DM
# ============================================================
def send_dm(recipient_id, message):
    url = "https://graph.facebook.com/v21.0/me/messages"
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
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"❌ Failed to send DM to {recipient_id}: {e}")
        return {"error": str(e)}


# ============================================================
# DOWNLOAD IMAGE FROM INSTAGRAM
# ============================================================
def download_image(image_url):
    try:
        headers = {"Authorization": f"Bearer {PAGE_ACCESS_TOKEN}"}
        response = requests.get(
            image_url,
            headers=headers,
            timeout=15
        )
        response.raise_for_status()
        # Convert to PIL Image for Gemini
        image = Image.open(io.BytesIO(response.content))
        return image
    except Exception as e:
        print(f"❌ Failed to download image: {e}")
        return None


# ============================================================
# GEMINI VISION ANALYSER
# 100% Free — Analyses screenshot and returns full audit
# ============================================================
def analyse_screenshot_with_gemini(image, name, btype, location):

    prompt = f"""You are an expert Instagram growth consultant working for ClientBoost.in.

A business owner has shared a screenshot of their Instagram profile for a free audit.

Their details:
- Business Name: {name}
- Business Type: {btype}
- Location: {location}

Carefully analyse everything visible in the screenshot:
- Name field and username
- Bio text and structure
- Profile picture quality
- Follower count and following count
- Number of posts
- Story Highlights — covers, labels, quantity
- Post grid — visible posts, visual consistency, theme, quality
- Any visible captions or engagement numbers
- Overall brand feel and professionalism

Now write a detailed fully personalised Instagram Growth Audit.

Use EXACTLY this format and structure:

━━━━━━━━━━━━━━━━━━━━━━
📋 SECTION 1 — PROFILE AUDIT

[Write specific observations about THEIR actual bio, name field, profile picture and highlights]
[Give specific improvements with examples using their actual business name and location]

Profile Score: X / 10
[Justify the score based on what you actually saw]

Top Fix: [One specific actionable fix for their actual profile]

━━━━━━━━━━━━━━━━━━━━━━
📸 SECTION 2 — CONTENT AUDIT

[Write specific observations about THEIR actual posts and grid]
[Comment on quality, themes and variety based on what is visible]

Content Score: X / 10
[Justify based on what you saw]

Top Fix: [One specific actionable fix for their content]

━━━━━━━━━━━━━━━━━━━━━━
📍 SECTION 3 — LOCAL REACH AUDIT

[Based on their bio and visible captions assess local SEO and hashtag strategy]
[Give specific hashtag suggestions using their actual business type and location]

Reach Score: X / 10

Top Fix: [One specific actionable fix for their reach]

━━━━━━━━━━━━━━━━━━━━━━
💬 SECTION 4 — ENGAGEMENT AUDIT

[Based on visible likes, comments and post frequency assess engagement]
[Give specific advice for their type of business]

Engagement Score: X / 10

Top Fix: [One specific actionable fix for engagement]

━━━━━━━━━━━━━━━━━━━━━━
🏆 OVERALL SCORECARD

Profile      →  X / 10
Content      →  X / 10
Reach        →  X / 10
Engagement   →  X / 10
━━━━━━━━━━━━━━━━━━━━━━
TOTAL        →  XX / 40

Rating: [One honest line rating]

━━━━━━━━━━━━━━━━━━━━━━
📅 YOUR 4-WEEK ACTION PLAN

Week 1: [Specific to their actual profile issues you saw]
Week 2: [Specific to their actual content issues you saw]
Week 3: [Specific to their engagement issues]
Week 4: [Review and scale what worked]

Important rules:
- Be honest and specific
- Reference things you actually saw in the screenshot
- Never be generic
- Every single line must feel written specifically for {name}
- If something looks good — say so
- If something is bad — be direct about it"""

    try:
        response = model.generate_content([prompt, image])
        return response.text

    except Exception as e:
        print(f"❌ Gemini Vision error: {e}")
        return None


# ============================================================
# SPLIT AUDIT INTO PARTS FOR SENDING
# Instagram DM limit is 1000 chars
# ============================================================
def split_audit_into_parts(audit_text):
    parts = []
    current = ""

    for line in audit_text.split("\n"):
        if "━━━" in line and current.strip():
            parts.append(current.strip())
            current = line + "\n"
        else:
            current += line + "\n"

    if current.strip():
        parts.append(current.strip())

    # Handle any parts still over 900 chars
    final_parts = []
    for part in parts:
        if len(part) <= 900:
            final_parts.append(part)
        else:
            lines = part.split("\n")
            chunk = ""
            for line in lines:
                if len(chunk) + len(line) + 1 > 900:
                    if chunk.strip():
                        final_parts.append(chunk.strip())
                    chunk = line + "\n"
                else:
                    chunk += line + "\n"
            if chunk.strip():
                final_parts.append(chunk.strip())

    return final_parts


# ============================================================
# FULL AUDIT SENDER
# ============================================================
def send_full_audit(sender_id, name, btype, location, image):

    send_dm(sender_id,
        "Screenshot received! 🎯\n\n"
        "Analysing your Instagram profile using AI now...\n\n"
        "This takes about 20 to 30 seconds ⏳"
    )

    # Run Gemini analysis
    audit_text = analyse_screenshot_with_gemini(image, name, btype, location)

    if not audit_text:
        send_dm(sender_id,
            "Sorry, something went wrong while analysing your screenshot. 😔\n\n"
            "Please try sending the screenshot again."
        )
        return

    # Send header
    send_dm(sender_id,
        f"🎯 FREE INSTAGRAM GROWTH AUDIT\n"
        f"by ClientBoost.in\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Business: {name}\n"
        f"Type: {btype}\n"
        f"Location: {location}\n\n"
        f"Here is your fully personalised audit 👇"
    )

    time.sleep(1)

    # Send audit in parts
    parts = split_audit_into_parts(audit_text)
    for part in parts:
        send_dm(sender_id, part)
        time.sleep(1.2)

    # Send closing CTA
    time.sleep(1)
    send_dm(sender_id,
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🚀 WANT HELP IMPLEMENTING THIS?\n\n"
        f"{name} has real growth potential on Instagram.\n"
        f"The audit shows exactly what needs fixing.\n\n"
        f"If you would like ClientBoost to handle all of this for you —\n"
        f"content, strategy and growth in {location} —\n"
        f"reply INTERESTED and we will take it from there.\n\n"
        f"No pressure. Just a conversation. 🤝\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"ClientBoost.in | Instagram Growth for Local Businesses"
    )


# ============================================================
# CONVERSATION HANDLER
# ============================================================
def handle_message(sender_id, message_obj):

    text = message_obj.get("text", "").lower().strip()
    raw_text = message_obj.get("text", "").strip()

    # Check for image attachment
    attachments = message_obj.get("attachments", [])
    image_url = None

    for attachment in attachments:
        if attachment.get("type") == "image":
            image_url = attachment.get("payload", {}).get("url")
            break

    # Initialise state if new user
    if sender_id not in user_states:
        user_states[sender_id] = {"step": "new", "data": {}}

    state = user_states[sender_id]
    step = state["step"]

    # ── RESET ───────────────────────────────────────────────
    if text in ["reset", "restart", "start over", "start again"]:
        user_states[sender_id] = {"step": "new", "data": {}}
        save_json(STATE_FILE, user_states)
        send_dm(sender_id,
            "No problem! 🔄\n\n"
            "Type GROWTH whenever you are ready."
        )
        return

    # ── IMAGE RECEIVED AT RIGHT STEP ────────────────────────
    if image_url and step == "ask_screenshot":

        send_dm(sender_id, "Got your screenshot! 📸")

        data = user_states[sender_id]["data"]

        # Download image
        image = download_image(image_url)

        if not image:
            send_dm(sender_id,
                "Sorry, I could not download your screenshot. 😔\n\n"
                "Please try sending it again."
            )
            return

        # Update cooldown
        user_cooldowns[sender_id] = time.time()
        save_json(COOLDOWN_FILE, user_cooldowns)

        # Send full AI audit
        send_full_audit(
            sender_id,
            data["name"],
            data["type"],
            data["location"],
            image
        )

        # Reset state
        user_states[sender_id] = {"step": "new", "data": {}}
        save_json(STATE_FILE, user_states)
        return

    # ── IMAGE RECEIVED AT WRONG STEP ────────────────────────
    if image_url and step != "ask_screenshot":
        send_dm(sender_id,
            "Thanks for the image! 📸\n\n"
            "Type GROWTH first to start your free audit "
            "and I will ask for your screenshot at the right time. 🎯"
        )
        return

    # ── TRIGGER: GROWTH ─────────────────────────────────────
    if "growth" in text and step == "new":

        last_time = user_cooldowns.get(sender_id, 0)
        if time.time() - last_time < 86400:
            send_dm(sender_id,
                "Hey! 👋 You already received a free audit recently.\n\n"
                "Your audit is valid for 30 days — work through the "
                "action plan and you will start seeing results. 💪\n\n"
                "Reply INTERESTED if you want our team to help you implement it."
            )
            return

        send_dm(sender_id,
            "Hey! 👋 Welcome to ClientBoost.\n\n"
            "You are about to get a fully personalised Instagram "
            "Growth Audit — analysed by AI based on your actual profile.\n\n"
            "Just 3 quick questions first.\n\n"
            "What is your business name? 🏪"
        )
        user_states[sender_id]["step"] = "ask_name"
        save_json(STATE_FILE, user_states)

    # ── STEP 1: NAME ────────────────────────────────────────
    elif step == "ask_name":
        if len(raw_text) < 2:
            send_dm(sender_id, "Please enter a valid business name. 🏪")
            return

        user_states[sender_id]["data"]["name"] = raw_text
        send_dm(sender_id,
            f"Got it — {raw_text} ✅\n\n"
            "What type of business is it?\n\n"
            "• Restaurant\n"
            "• Cafe\n"
            "• Salon\n"
            "• Gym\n"
            "• Boutique\n"
            "• Clinic\n"
            "• Other"
        )
        user_states[sender_id]["step"] = "ask_type"
        save_json(STATE_FILE, user_states)

    # ── STEP 2: TYPE ────────────────────────────────────────
    elif step == "ask_type":
        if len(raw_text) < 2:
            send_dm(sender_id, "Please enter your business type. 🏷️")
            return

        user_states[sender_id]["data"]["type"] = raw_text
        send_dm(sender_id,
            f"{raw_text} — noted! ✅\n\n"
            "Which city or area is your business in? 📍"
        )
        user_states[sender_id]["step"] = "ask_location"
        save_json(STATE_FILE, user_states)

    # ── STEP 3: LOCATION ────────────────────────────────────
    elif step == "ask_location":
        if len(raw_text) < 2:
            send_dm(sender_id, "Please enter your city or area. 📍")
            return

        user_states[sender_id]["data"]["location"] = raw_text
        send_dm(sender_id,
            f"{raw_text} — perfect! ✅\n\n"
            "Now the most important step 🎯\n\n"
            "Please send a screenshot of your Instagram profile.\n\n"
            "Make sure it shows:\n"
            "• Your name and bio\n"
            "• Your Story Highlights\n"
            "• Your post grid\n"
            "• Your follower count\n\n"
            "Our AI will analyse your actual account and give you "
            "a fully personalised audit — not a generic one. 📸\n\n"
            "Send the screenshot whenever you are ready! 👇"
        )
        user_states[sender_id]["step"] = "ask_screenshot"
        save_json(STATE_FILE, user_states)

    # ── INTERESTED ───────────────────────────────────────────
    elif "interested" in text:
        send_dm(sender_id,
            "That is great to hear! 🙌\n\n"
            "Someone from the ClientBoost team will reach out to you "
            "shortly to understand your business better.\n\n"
            "Talk soon! 😊\n"
            "— Team ClientBoost.in"
        )

    # ── FALLBACK ─────────────────────────────────────────────
    else:
        send_dm(sender_id,
            "Hey! 👋 I am the ClientBoost audit assistant.\n\n"
            "Type GROWTH to get your free personalised "
            "Instagram audit powered by AI. 🎯"
        )


# ============================================================
# WEBHOOK VERIFICATION
# ============================================================
@app.route("/webhook", methods=["GET"])
def verify():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        print("✅ Webhook verified")
        return challenge, 200

    return "Forbidden", 403


# ============================================================
# WEBHOOK RECEIVER
# ============================================================
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json

    if data.get("object") == "instagram":
        for entry in data.get("entry", []):
            for event in entry.get("messaging", []):
                sender_id = event.get("sender", {}).get("id")
                message_obj = event.get("message", {})
                is_echo = message_obj.get("is_echo", False)

                has_text = bool(message_obj.get("text", ""))
                has_image = any(
                    a.get("type") == "image"
                    for a in message_obj.get("attachments", [])
                )

                if sender_id and (has_text or has_image) and not is_echo:
                    thread = threading.Thread(
                        target=handle_message,
                        args=(sender_id, message_obj)
                    )
                    thread.daemon = True
                    thread.start()

    return jsonify({"status": "ok"}), 200


# ============================================================
# HEALTH CHECK
# ============================================================
@app.route("/", methods=["GET"])
def home():
    return "ClientBoost Bot is running ✅", 200


# ============================================================
# RUN
# ============================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)