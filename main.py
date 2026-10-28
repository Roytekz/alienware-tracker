import os
import sys
import time
import json
import re
import requests
from datetime import datetime

WEBHOOK_URL = os.getenv("WEBHOOK_URL")

POLL_INTERVAL = 25
DATABASE_FILE = "alienware_state.json"
# Reads the latest 50 posts from FGF to catch Alienware drops
FEED_URL = "https://www.reddit.com/r/FreeGameFindings/new.json?limit=50"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AlienwareHunter/4.0"
}

def load_state():
    if os.path.exists(DATABASE_FILE):
        try:
            with open(DATABASE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_state(state):
    with open(DATABASE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def calculate_duration(seconds):
    mins, secs = divmod(int(seconds), 60)
    hours, mins = divmod(mins, 60)
    if hours > 0:
        return f"{hours}h {mins}m {secs}s"
    return f"{mins}m {secs}s"

def build_embed(data, is_active=True, duration_str=""):
    title = data.get("title", "Alienware Key Giveaway")
    claim_url = data.get("url", "https://www.alienwarearena.com")
    platform = data.get("platform", "Steam")
    image_url = data.get("image")

    if is_active:
        embed = {
            "author": {"name": "Alienware Arena – Giveaways"},
            "title": title,
            "url": claim_url,
            "description": f"Keys are live now! 👉 **[Click Here to Claim]({claim_url})**",
            "color": 3447003,  # Blue sidebar
            "fields": [
                {"name": "Status", "value": "🟢 Active", "inline": True},
                {"name": "Platform", "value": f"🎮 {platform}", "inline": True},
                {"name": "Keys", "value": "🔑 Available", "inline": True}
            ],
            "footer": {"text": f"Giveaway Notifier • Alienware Arena • {datetime.utcnow().strftime('%H:%M')}"}
        }
    else:
        duration_display = f"All keys claimed. It was live for **{duration_str}**." if duration_str else "All keys claimed."
        embed = {
            "author": {"name": "Alienware Arena – Giveaways"},
            "title": title,
            "url": claim_url,
            "description": duration_display,
            "color": 15158332,  # Red sidebar
            "fields": [
                {"name": "Status", "value": "❌ Out of keys", "inline": True},
                {"name": "Platform", "value": f"🎮 {platform}", "inline": True},
                {"name": "Keys", "value": "🔑 0", "inline": True}
            ],
            "footer": {"text": f"Giveaway Notifier • Alienware Arena • Concluded"}
        }

    if image_url:
        embed["image"] = {"url": image_url}
    return embed

def post_alert(drop_data, is_active=True, duration_str=""):
    if not WEBHOOK_URL:
        print("Missing WEBHOOK_URL environment variable!")
        return None
    embed = build_embed(drop_data, is_active=is_active, duration_str=duration_str)
    target = f"{WEBHOOK_URL}?wait=true"
    try:
        r = requests.post(target, json={"embeds": [embed]}, timeout=10)
        if r.status_code in (200, 201):
            return r.json().get("id")
        else:
            print(f"Discord Post Error {r.status_code}: {r.text}")
    except Exception as e:
        print(f"Error publishing: {e}")
    return None

def update_to_out_of_keys(message_id, drop_data, duration_str):
    if not WEBHOOK_URL:
        return
    embed = build_embed(drop_data, is_active=False, duration_str=duration_str)
    patch_target = f"{WEBHOOK_URL}/messages/{message_id}"
    try:
        requests.patch(patch_target, json={"embeds": [embed]}, timeout=10)
    except Exception as e:
        print(f"Failed to patch Discord message: {e}")

def fetch_feed_drops():
    parsed_items = []
    try:
        res = requests.get(FEED_URL, headers=HEADERS, timeout=12)
        if res.status_code != 200:
            print(f"Feed error {res.status_code}")
            return parsed_items
        
        posts = res.json().get("data", {}).get("children", [])
        for entry in posts:
            post = entry.get("data", {})
            title_raw = post.get("title", "")
            url = post.get("url", "")
            flair = (post.get("link_flair_text") or "").lower()

            # Filter specifically for Alienware Arena
            if not ("alienwarearena.com" in url or "alienware" in title_raw.lower()):
                continue

            clean_title = re.sub(r'\[.*?\]|\(.*?\)', '', title_raw).strip() or title_raw
            platform = "Steam"
            if "epic" in title_raw.lower(): platform = "Epic Games"
            elif "gog" in title_raw.lower(): platform = "GOG"

            image = None
            preview = post.get("preview", {}).get("images", [])
            if preview:
                image = preview[0].get("source", {}).get("url", "").replace("&amp;", "&")
            elif post.get("thumbnail", "").startswith("http"):
                image = post["thumbnail"]

            is_expired = any(x in flair for x in ["expired", "ended", "out of keys"])

            parsed_items.append({
                "id": post.get("id"),
                "title": clean_title,
                "url": url,
                "platform": platform,
                "image": image,
                "is_expired": is_expired,
                "created_utc": post.get("created_utc", time.time())
            })
    except Exception as e:
        print(f"Error fetching: {e}")
    return parsed_items

def run():
    print("🛰️ Alienware Arena Hunter active...")
    state = load_state()

    # Initial scan
    drops = fetch_feed_drops()

    # BACKFILL: If state is fresh, send the most recent drop right away
    if not state and drops:
        latest_drop = drops[0]  # The most recent Alienware drop recorded
        print(f"📦 First run: Posting recent Alienware drop: {latest_drop['title']}")
        
        # Post it in its current state (Active or Out of Keys)
        msg_id = post_alert(latest_drop, is_active=(not latest_drop["is_expired"]))
        if msg_id:
            state[latest_drop["id"]] = {
                "message_id": msg_id,
                "first_seen": latest_drop["created_utc"],
                "title": latest_drop["title"],
                "data": latest_drop,
                "status": "out_of_keys" if latest_drop["is_expired"] else "active"
            }
            save_state(state)

    # 24/7 Monitoring Loop
    while True:
        drops = fetch_feed_drops()
        for item in drops:
            drop_id = item["id"]

            # New drop detected
            if drop_id not in state:
                if not item["is_expired"]:
                    print(f"🔥 [NEW DROP]: {item['title']}")
                    msg_id = post_alert(item, is_active=True)
                    if msg_id:
                        state[drop_id] = {
                            "message_id": msg_id,
                            "first_seen": time.time(),
                            "title": item["title"],
                            "data": item,
                            "status": "active"
                        }
                        save_state(state)

            # Active drop expired
            elif state[drop_id]["status"] == "active" and item["is_expired"]:
                start_time = state[drop_id].get("first_seen", item["created_utc"])
                duration = calculate_duration(time.time() - start_time)
                print(f"🛑 [OUT OF KEYS]: {item['title']} ({duration})")
                update_to_out_of_keys(state[drop_id]["message_id"], state[drop_id]["data"], duration)
                state[drop_id]["status"] = "out_of_keys"
                save_state(state)

        sys.stdout.flush()
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    run()
