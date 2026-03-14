import os
import re
import json
from urllib.parse import quote
from flask import Flask, request, render_template_string
from curl_cffi import requests as cffi_requests

app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Raw Link Extractor</title>
    <link href="https://vjs.zencdn.net/8.6.1/video-js.css" rel="stylesheet" />
    <style>
        body { font-family: 'Segoe UI', sans-serif; background: #0f0f0f; color: #fff; text-align: center; padding: 40px; }
        input[type="text"] { width: 60%; padding: 12px; border-radius: 8px; border: 1px solid #333; background: #222; color: white; }
        button { padding: 12px 24px; background: #ff4757; color: white; border: none; border-radius: 8px; cursor: pointer; font-weight: bold; margin-top: 10px; }
        .link-box { background: #222; padding: 20px; border-radius: 8px; margin: 20px auto; max-width: 800px; word-wrap: break-word; color: #2ecc71; font-family: monospace;}
        .video-wrapper { max-width: 800px; margin: 20px auto; background: #000; }
    </style>
</head>
<body>
    <h1>🔗 Raw Link Extractor</h1>
    <form method="POST" action="/">
        <input type="text" name="url" placeholder="Paste video page URL..." required>
        <br>
        <button type="submit">Extract Direct Link</button>
    </form>

    {% if stream_url %}
        <h3>Raw .m3u8 Stream Link:</h3>
        <div class="link-box">{{ stream_url }}</div>
        
        <h3>Attempting Direct Playback (Will likely fail due to CORS/IP Lock):</h3>
        <div class="video-wrapper">
            <video-js id="my-video" class="vjs-default-skin vjs-16-9" controls preload="auto">
                <source src="{{ stream_url }}" type="application/x-mpegURL">
            </video-js>
        </div>
        <script src="https://vjs.zencdn.net/8.6.1/video.min.js"></script>
        <script>var player = videojs('my-video', { fluid: true });</script>
    {% endif %}
    
    {% if error %}<p style="color:red;">{{ error }}</p>{% endif %}
</body>
</html>
"""

def get_client_ip():
    """Extracts the real user IP, accounting for Render's reverse proxy."""
    if request.headers.getlist("X-Forwarded-For"):
        return request.headers.getlist("X-Forwarded-For")[0]
    return request.remote_addr

@app.route("/", methods=["GET", "POST"])
def index():
    stream_url = None
    error = None

    if request.method == "POST":
        page_url = request.form.get("url")
        client_ip = get_client_ip()
        
        # Attempting to pass the user's IP (Usually ignored by Cloudflare)
        headers = {
            "X-Forwarded-For": client_ip,
            "X-Real-IP": client_ip
        }

        try:
            resp = cffi_requests.get(page_url, impersonate="chrome120", headers=headers, timeout=15)
            html = resp.text

            data = {}
            json_match = re.search(r'window\.initials\s*=\s*({.+?});\s*</script>', html, re.DOTALL)
            if json_match:
                try: data = json.loads(json_match.group(1))
                except: pass

            if data:
                sources = data.get("videoModel", {}).get("sources", {})
                if "hls" in sources: stream_url = sources["hls"].get("url")

            if not stream_url:
                m3u8_matches = re.findall(r'https?:\/\/[^\s<>"\'\\]+\.m3u8[^\s<>"\'\\]*', html)
                if m3u8_matches:
                    valid = [m.replace('\\/', '/') for m in m3u8_matches if 'tsyndicate' not in m]
                    if valid: stream_url = valid[0]

            if not stream_url:
                error = "Could not extract link."

        except Exception as e:
            error = f"Error: {str(e)}"

    return render_template_string(HTML_TEMPLATE, stream_url=stream_url, error=error)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
