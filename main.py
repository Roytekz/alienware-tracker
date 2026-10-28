import os
import sys
import time
import json
import re
import requests
import xml.etree.ElementTree as ET
from datetime import datetime

WEBHOOK_URL = os.getenv("WEBHOOK_URL")

POLL_INTERVAL = 30
DATABASE_FILE = "alienware_state.json"

# Proxied Reddit RSS endpoints to avoid cloud rate limits
PRIMARY_RSS = "https://api.allorigins.win/raw?url=https://www.reddit.com/r/FreeGameFindings/new.rss"
FALLBACK_RSS = "https://api.codetabs.com/v1/proxy?quest=https://www.reddit.com/r/FreeGameFindings/new.rss"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
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

def fetch_steam_artwork_by_title(title):
    """Queries Steam's store search API to dynamically find the game's official Steam App ID and header."""
    try:
        # Strip generic tags/prompts to focus on clean title
        clean_name = re.sub(r'\[.*?\]|\(.*?\)', '', title)
        clean_name = re.sub(r'giveaway|steam|game key|dlc|beta|edition|pack', '', clean_name, flags=re.IGNORECASE)
        clean_name = re.sub(r'[^a-zA-Z0-9\s]', ' ', clean_name).strip()

        search_url = f"https://store.steampowered.com/api/storesearch/?term={requests.utils.quote(clean_name)}&l=english&cc=US"
        resp = requests.get(search_url, timeout=6)
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("items", [])
            if items:
                app_id = items[0].get("id")
                return f"https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/{app_id}/header.jpg"
    except Exception:
        pass
    return None

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
            "footer": {"text": "Giveaway Notifier • Alienware Arena • Concluded"}
        }

    if image_url:
        embed["image"] = {"url": image_url}
    return embed

def post_alert(drop_data, is_active=True, duration_str=""):
    if not WEBHOOK_URL:
        print("Missing WEBHOOK_URL environment variable!", flush=True)
        return None
    embed = build_embed(drop_data, is_active=is_active, duration_str=duration_str)
    target = f"{WEBHOOK_URL}?wait=true"
    try:
        r = requests.post(target, json={"embeds": [embed]}, timeout=10)
        if r.status_code in (200, 201):
            return r.json().get("id")
        else:
            print(f"Discord Post Error {r.status_code}: {r.text}", flush=True)
    except Exception as e:
        print(f"Error publishing: {e}", flush=True)
    return None

def update_to_out_of_keys(message_id, drop_data, duration_str):
    if not WEBHOOK_URL:
        return
    embed = build_embed(drop_data, is_active=False, duration_str=duration_str)
    patch_target = f"{WEBHOOK_URL}/messages/{message_id}"
    try:
        requests.patch(patch_target, json={"embeds": [embed]}, timeout=10)
    except Exception as e:
        print(f"Failed to patch Discord message: {e}", flush=True)

def fetch_feed_drops():
    parsed_items = []
    xml_content = None

    for endpoint in [PRIMARY_RSS, FALLBACK_RSS]:
        try:
            res = requests.get(endpoint, headers=HEADERS, timeout=12)
            if res.status_code == 200 and "<feed" in res.text:
                xml_content = res.text
                break
        except Exception:
            continue

    if not xml_content:
        return None

    try:
        root = ET.fromstring(xml_content)
        namespace = {'atom': 'http://www.w3.org/2005/Atom'}

        for entry in root.findall('atom:entry', namespace):
            title_elem = entry.find('atom:title', namespace)
            raw_title = title_elem.text if title_elem is not None else ""
            
            content_elem = entry.find('atom:content', namespace)
            content_html = content_elem.text if content_elem is not None else ""

            # Check category tags for expired status
            flairs = []
            for cat in entry.findall('atom:category', namespace):
                term = cat.attrib.get('term', '')
                if term:
                    flairs.append(term.lower())

            is_expired = any(x in " ".join(flairs) for x in ["expired", "out of keys", "ended"])

            # Filter for Alienware Arena posts
            is_alienware = (
                "alienwarearena.com" in content_html.lower() or 
                "alienware" in raw_title.lower()
            )
            if not is_alienware:
                continue

            # Extract giveaway URL
            url_match = re.search(r'href="(https://[a-zA-Z0-9\.\-_]*alienwarearena\.com[^\"]*)"', content_html)
            claim_url = url_match.group(1) if url_match else "https://www.alienwarearena.com"

            id_elem = entry.find('atom:id', namespace)
            post_id = id_elem.text if id_elem is not None else raw_title

            clean_title = re.sub(r'\[.*?\]|\(.*?\)', '', raw_title).strip() or raw_title

            platform = "Steam"
            if "epic" in raw_title.lower(): platform = "Epic Games"
            elif "gog" in raw_title.lower(): platform = "GOG"

            # Artwork Pipeline: 
            # 1. Direct AppID in post -> 2. Steam Search by clean name -> 3. Reddit's uploaded preview
            image_url = None
            steam_match = re.search(r'store\.steampowered\.com/app/(\d+)', content_html)
            if steam_match:
                app_id = steam_match.group(1)
                image_url = f"https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/{app_id}/header.jpg"

            if not image_url and platform == "Steam":
                image_url = fetch_steam_artwork_by_title(clean_title)

            if not image_url:
                img_match = re.search(r'<img[^>]+src="(https://preview\.redd\.it/[^\"]+)"', content_html)
                if img_match:
                    image_url = img_match.group(1).replace("&amp;", "&")

            parsed_items.append({
                "id": post_id,
                "title": clean_title,
                "url": claim_url,
                "platform": platform,
                "image": image_url,
                "is_expired": is_expired,
                "created_utc": time.time()
            })

    except Exception as e:
        print(f"Error parsing feed XML: {e}", flush=True)
        return None

    return parsed_items

def run():
    print("🛰️ Alienware Arena Hunter active...", flush=True)
    state = load_state()

    # Establish baseline silently so old drops are never spammed
    initial_drops = fetch_feed_drops()
    if initial_drops is not None:
        for item in initial_drops:
            if item["id"] not in state:
                state[item["id"]] = {
                    "message_id": None,
                    "first_seen": item["created_utc"],
                    "title": item["title"],
                    "data": item,
                    "status": "out_of_keys" if item["is_expired"] else "active"
                }
        save_state(state)
    print("✅ Baseline synced cleanly. Live monitoring active...", flush=True)

    while True:
        drops = fetch_feed_drops()
        if drops is not None:
            for item in drops:
                drop_id = item["id"]

                # New drop detected
                if drop_id not in state:
                    if not item["is_expired"]:
                        print(f"🔥 [NEW DROP DETECTED]: {item['title']}", flush=True)
                        msg_id = post_alert(item, is_active=True)
                        state[drop_id] = {
                            "message_id": msg_id,
                            "first_seen": time.time(),
                            "title": item["title"],
                            "data": item,
                            "status": "active"
                        }
                        save_state(state)
                    else:
                        state[drop_id] = {
                            "message_id": None,
                            "first_seen": time.time(),
                            "title": item["title"],
                            "data": item,
                            "status": "out_of_keys"
                        }
                        save_state(state)

                # Existing active drop marked expired
                elif state[drop_id]["status"] == "active" and item["is_expired"]:
                    if state[drop_id].get("message_id"):
                        start_time = state[drop_id].get("first_seen", item["created_utc"])
                        duration = calculate_duration(time.time() - start_time)
                        print(f"🛑 [OUT OF KEYS]: {item['title']} ({duration})", flush=True)
                        update_to_out_of_keys(state[drop_id]["message_id"], state[drop_id]["data"], duration)
                    state[drop_id]["status"] = "out_of_keys"
                    save_state(state)

        sys.stdout.flush()
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    run()
