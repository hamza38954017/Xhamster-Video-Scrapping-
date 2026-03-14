import os
import re
import json
from urllib.parse import urljoin, quote, unquote, urlparse, urlunparse
from flask import Flask, request, render_template_string, Response
from curl_cffi import requests as cffi_requests

app = Flask(__name__)

BASE_DOMAIN = "https://xhamster45.desi"
SPOOF_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# The Frontend UI
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SS-Style Video Fetcher</title>
    <style>
        body { font-family: 'Segoe UI', Tahoma, sans-serif; background: #0f0f0f; color: #fff; text-align: center; padding: 40px 20px; margin: 0; }
        h1 { color: #ff4757; margin-bottom: 10px; }
        .search-container { margin-bottom: 40px; display: flex; justify-content: center; gap: 10px; }
        input[type="text"] { width: 60%; max-width: 600px; padding: 12px 20px; font-size: 16px; border-radius: 8px; border: 1px solid #333; background: #222; color: white; outline: none; }
        button { padding: 12px 24px; font-size: 16px; background: #ff4757; color: white; border: none; border-radius: 8px; cursor: pointer; font-weight: bold; }
        button:hover { background: #ff6b81; }
        .video-wrapper { max-width: 900px; margin: 0 auto; box-shadow: 0 10px 30px rgba(0,0,0,0.8); border-radius: 8px; overflow: hidden; background: #000; margin-bottom: 20px;}
        .error { color: #ff6b81; font-weight: bold; margin-top: 20px; }
        .temp-url-box { background: #222; padding: 15px; border-radius: 8px; font-family: monospace; color: #2ecc71; word-break: break-all; margin-bottom: 20px; }
    </style>
</head>
<body>
    <h1>🚀 SS-Style Fetcher</h1>
    <p style="color: #888;">Generates a proxy-stream link just like ssyoutube.</p>

    <form class="search-container" method="POST" action="/">
        <input type="text" name="url" placeholder="Paste full video page URL here..." required>
        <button type="submit">Generate Link</button>
    </form>

    {% if error %}<p class="error">⚠️ {{ error }}</p>{% endif %}

    {% if temp_url %}
        <h3 style="margin-bottom: 20px;">{{ title }}</h3>
        
        <p>Temporary Streaming URL (Hosted on your server):</p>
        <div class="temp-url-box">{{ request.host_url[:-1] }}{{ temp_url }}</div>
        
        <div class="video-wrapper">
            <video controls preload="metadata" width="100%" height="auto" autoplay>
                <source src="{{ temp_url }}" type="video/mp4">
                Your browser does not support the video tag.
            </video>
        </div>
    {% endif %}
</body>
</html>
"""

@app.route("/", methods=["GET", "POST"])
def index():
    temp_url = None
    title = "Video Player"
    error = None

    if request.method == "POST":
        page_url = request.form.get("url")
        try:
            # 1. Bypass Cloudflare to get the raw page
            resp = cffi_requests.get(page_url, impersonate="chrome120", timeout=15)
            html = resp.text

            title_match = re.search(r'<title>(.*?)</title>', html)
            if title_match: title = title_match.group(1).replace(" | xHamster", "").strip()

            data = {}
            json_match = re.search(r'window\.initials\s*=\s*({.+?});\s*</script>', html, re.DOTALL)
            if json_match:
                try: data = json.loads(json_match.group(1))
                except: pass
            
            if not data:
                script_matches = re.findall(r'<script[^>]*>(.*?videoModel.*?)</script>', html, re.DOTALL | re.IGNORECASE)
                for script in script_matches:
                    try:
                        start, end = script.find('{'), script.rfind('}') + 1
                        data = json.loads(script[start:end])
                        break
                    except: continue

            raw_mp4_url = None
            
            # 2. Prioritize Direct MP4 extraction (This is what SSYouTube does)
            if data:
                sources = data.get("videoModel", {}).get("sources", {})
                if "mp4" in sources:
                    mp4_data = sources["mp4"]
                    if isinstance(mp4_data, dict):
                        if "url" in mp4_data: raw_mp4_url = mp4_data["url"]
                        else:
                            for key, val in mp4_data.items():
                                if isinstance(val, dict) and "url" in val:
                                    raw_mp4_url = val["url"]
                                    break
                    elif isinstance(mp4_data, str): raw_mp4_url = mp4_data
                
                # Fallback to HLS if MP4 isn't available
                if not raw_mp4_url and "hls" in sources: 
                    raw_mp4_url = sources["hls"].get("url")

            # 3. Generate the Temporary URL pointing to our Python Proxy
            if raw_mp4_url:
                temp_url = f"/stream?target={quote(raw_mp4_url)}"
            else:
                error = "Could not extract video source. Might be a premium-only stream."

        except Exception as e:
            error = f"Error scraping: {str(e)}"

    return render_template_string(HTML_TEMPLATE, temp_url=temp_url, title=title, error=error)

@app.route("/stream")
def stream_proxy():
    """
    The SSYouTube Proxy Engine.
    Intercepts the target URL, injects headers, and critically: handles Range requests!
    """
    target_url = request.args.get("target")
    if not target_url: return "No target", 400
    target_url = unquote(target_url)

    # Prepare spoofing headers for the CDN
    proxy_headers = {
        "Referer": BASE_DOMAIN,
        "User-Agent": SPOOF_USER_AGENT
    }

    # CRITICAL: Forward the exact byte-range the browser is asking for
    client_range = request.headers.get("Range")
    if client_range:
        proxy_headers["Range"] = client_range

    try:
        # Request the video from the CDN with the range header
        req = cffi_requests.get(target_url, headers=proxy_headers, stream=True, impersonate="chrome120", timeout=15)
    except Exception as e:
        return str(e), 500

    # Read the response headers from the CDN
    status_code = req.status_code
    content_type = req.headers.get("Content-Type", "video/mp4")
    content_length = req.headers.get("Content-Length")
    content_range = req.headers.get("Content-Range")

    # If it's an m3u8 playlist, we must fall back to the old HLS logic
    if ".m3u8" in target_url or "mpegurl" in content_type.lower():
        content = req.text
        def replace_uri(match):
            orig = match.group(1)
            abs_url = urljoin(target_url, orig)
            return f'URI="/stream?target={quote(abs_url)}"'
        content = re.sub(r'URI="([^"]+)"', replace_uri, content)
        
        new_m3u8 = []
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("#") or not line: new_m3u8.append(line)
            else:
                abs_url = urljoin(target_url, line)
                new_m3u8.append(f"/stream?target={quote(abs_url)}")
        return Response("\n".join(new_m3u8), content_type="application/vnd.apple.mpegurl")

    # The Generator that pipes the video bytes back to the browser
    def generate():
        for chunk in req.iter_content(chunk_size=65536): 
            if chunk: yield chunk

    # Create the Flask Response
    resp = Response(generate(), status=status_code, content_type=content_type)
    
    # Pass the CDN's exact size and range markers back to the browser
    if content_length: resp.headers["Content-Length"] = content_length
    if content_range: resp.headers["Content-Range"] = content_range
    resp.headers["Accept-Ranges"] = "bytes"
    resp.headers["Access-Control-Allow-Origin"] = "*"

    return resp

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
