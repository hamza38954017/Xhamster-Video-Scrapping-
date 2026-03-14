import os
import re
import json
from urllib.parse import urljoin, quote, unquote
from flask import Flask, request, render_template_string, Response
from curl_cffi import requests as cffi_requests
import requests as std_requests

app = Flask(__name__)

# The exact spoofing credentials
BASE_DOMAIN = "https://xhamster45.desi"
SPOOF_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# The Frontend UI
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Video Streamer</title>
    <link href="https://vjs.zencdn.net/8.6.1/video-js.css" rel="stylesheet" />
    <style>
        body { font-family: 'Segoe UI', Tahoma, sans-serif; background: #0f0f0f; color: #fff; text-align: center; padding: 40px 20px; margin: 0; }
        h1 { color: #ff4757; }
        .search-container { margin-bottom: 40px; display: flex; justify-content: center; gap: 10px; }
        input[type="text"] { width: 60%; max-width: 600px; padding: 12px 20px; font-size: 16px; border-radius: 8px; border: 1px solid #333; background: #222; color: white; outline: none; }
        button { padding: 12px 24px; font-size: 16px; background: #ff4757; color: white; border: none; border-radius: 8px; cursor: pointer; font-weight: bold; }
        button:hover { background: #ff6b81; }
        .video-wrapper { max-width: 900px; margin: 0 auto; box-shadow: 0 10px 30px rgba(0,0,0,0.8); border-radius: 8px; overflow: hidden; background: #000; }
        .error { color: #ff6b81; font-weight: bold; margin-top: 20px; }
    </style>
</head>
<body>

    <h1>🚀 Ultimate Video Streamer</h1>
    
    <form class="search-container" method="POST" action="/">
        <input type="text" name="url" placeholder="Paste full video page URL here..." required>
        <button type="submit">Play Video</button>
    </form>

    {% if error %}
        <p class="error">⚠️ {{ error }}</p>
    {% endif %}

    {% if stream_url %}
        <h3 style="margin-bottom: 20px;">{{ title }}</h3>
        <div class="video-wrapper">
            <video-js id="my-video" class="vjs-default-skin vjs-16-9 vjs-big-play-centered" controls preload="auto" autoplay>
                <source src="/proxy?url={{ quoted_stream_url }}" type="application/x-mpegURL">
            </video-js>
        </div>
        
        <script src="https://vjs.zencdn.net/8.6.1/video.min.js"></script>
        <script>var player = videojs('my-video', { fluid: true });</script>
    {% endif %}

</body>
</html>
"""

@app.route("/", methods=["GET", "POST"])
def index():
    stream_url = None
    quoted_stream_url = None
    title = "Video Player"
    error = None

    if request.method == "POST":
        page_url = request.form.get("url")
        try:
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

            if data:
                sources = data.get("videoModel", {}).get("sources", {})
                if "hls" in sources: stream_url = sources["hls"].get("url")
                elif "mp4" in sources: stream_url = sources["mp4"].get("url")

            if not stream_url:
                m3u8_matches = re.findall(r'https?:\/\/[^\s<>"\'\\]+\.m3u8[^\s<>"\'\\]*', html)
                if m3u8_matches:
                    valid_streams = [m.replace('\\/', '/') for m in m3u8_matches if 'tsyndicate' not in m and 'trafficstars' not in m]
                    if valid_streams: stream_url = valid_streams[0]

            if stream_url:
                quoted_stream_url = quote(stream_url)
            else:
                error = "Could not find a valid stream link. The video might be premium-locked."

        except Exception as e:
            error = f"Error scraping page: {str(e)}"

    return render_template_string(HTML_TEMPLATE, stream_url=stream_url, quoted_stream_url=quoted_stream_url, title=title, error=error)


@app.route("/proxy")
def proxy():
    """Proxies the m3u8 playlists, AES keys, and .ts video chunks."""
    
    # Safe URL extraction (prevents query parameters from being chopped off)
    try:
        raw_url = request.url.split("url=", 1)[1]
        target_url = unquote(raw_url)
    except IndexError:
        return "No URL provided", 400

    headers = {
        "Referer": BASE_DOMAIN,
        "User-Agent": SPOOF_USER_AGENT
    }

    try:
        req = std_requests.get(target_url, headers=headers, stream=True, timeout=15)
    except Exception as e:
        return str(e), 500

    # Handle M3U8 Playlists
    if ".m3u8" in target_url or "mpegurl" in req.headers.get("Content-Type", "").lower():
        content = req.text
        
        # FIX 1: Find and proxy hidden AES Decryption Keys inside the playlist
        def replace_uri(match):
            orig_uri = match.group(1)
            abs_uri = urljoin(target_url, orig_uri)
            return f'URI="/proxy?url={quote(abs_uri)}"'
            
        content = re.sub(r'URI="([^"]+)"', replace_uri, content)
        
        # FIX 2: Proxy all standard video chunk paths
        new_m3u8 = []
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("#") or not line:
                new_m3u8.append(line)
            else:
                abs_url = urljoin(target_url, line)
                new_m3u8.append(f"/proxy?url={quote(abs_url)}")
                
        return Response("\n".join(new_m3u8), content_type="application/vnd.apple.mpegurl")
    
    # Handle Video Chunks (.ts files)
    def generate():
        # FIX 3: Reduced chunk size to 64KB for smooth, instant web streaming
        for chunk in req.iter_content(chunk_size=65536): 
            if chunk:
                yield chunk
    
    return Response(generate(), content_type=req.headers.get("Content-Type", "video/mp2t"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
