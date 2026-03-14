import os
import re
import json
from urllib.parse import urljoin, quote
from flask import Flask, request, render_template_string, Response
from curl_cffi import requests as cffi_requests
import requests as std_requests

app = Flask(__name__)

# The base domain to use for spoofing the CDN
BASE_DOMAIN = "https://xhamster45.desi"

# The Frontend UI (HTML, CSS, and Video.js)
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Video Streamer</title>
    <link href="https://vjs.zencdn.net/8.6.1/video-js.css" rel="stylesheet" />
    <style>
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background-color: #0f0f0f;
            color: #ffffff;
            text-align: center;
            padding: 40px 20px;
            margin: 0;
        }
        h1 { color: #ff4757; }
        .search-container {
            margin-bottom: 40px;
            display: flex;
            justify-content: center;
            gap: 10px;
        }
        input[type="text"] {
            width: 60%;
            max-width: 600px;
            padding: 12px 20px;
            font-size: 16px;
            border-radius: 8px;
            border: 1px solid #333;
            background: #222;
            color: white;
            outline: none;
        }
        input[type="text"]:focus { border-color: #ff4757; }
        button {
            padding: 12px 24px;
            font-size: 16px;
            background-color: #ff4757;
            color: white;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-weight: bold;
            transition: 0.2s;
        }
        button:hover { background-color: #ff6b81; }
        .video-wrapper {
            max-width: 900px;
            margin: 0 auto;
            box-shadow: 0 10px 30px rgba(0,0,0,0.8);
            border-radius: 8px;
            overflow: hidden;
            background: #000;
        }
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
        <script>
            var player = videojs('my-video', { fluid: true });
        </script>
    {% endif %}

</body>
</html>
"""

@app.route("/", methods=["GET", "POST"])
def index():
    """Handles the Home Page and the Web Scraping."""
    stream_url = None
    quoted_stream_url = None
    title = "Video Player"
    error = None

    if request.method == "POST":
        page_url = request.form.get("url")
        try:
            # 1. Scrape the page using curl_cffi to bypass Cloudflare
            resp = cffi_requests.get(page_url, impersonate="chrome120", timeout=15)
            html = resp.text

            # 2. Extract Title
            title_match = re.search(r'<title>(.*?)</title>', html)
            if title_match:
                title = title_match.group(1).replace(" | xHamster", "").strip()

            # 3. Extract the fresh m3u8 link from the JSON block
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

            # Fallback regex
            if not stream_url:
                m3u8_matches = re.findall(r'https?:\/\/[^\s<>"\'\\]+\.m3u8[^\s<>"\'\\]*', html)
                if m3u8_matches:
                    valid_streams = [m.replace('\\/', '/') for m in m3u8_matches if 'tsyndicate' not in m and 'trafficstars' not in m]
                    if valid_streams: stream_url = valid_streams[0]

            if stream_url:
                quoted_stream_url = quote(stream_url)
            else:
                error = "Could not find a valid stream link on that page. It might be a premium-only video."

        except Exception as e:
            error = f"Error scraping page: {str(e)}"

    return render_template_string(HTML_TEMPLATE, stream_url=stream_url, quoted_stream_url=quoted_stream_url, title=title, error=error)


@app.route("/proxy")
def proxy():
    """
    THE MAGIC SAUCE: 
    This route intercepts the video player's requests, attaches the fake Referer, 
    fetches the video from the CDN, and pipes it back to the frontend.
    """
    target_url = request.args.get("url")
    if not target_url:
        return "No URL provided", 400

    # Spoof the CDN
    headers = {
        "Referer": BASE_DOMAIN,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    try:
        req = std_requests.get(target_url, headers=headers, stream=True, timeout=15)
    except Exception as e:
        return str(e), 500

    # If the response is an M3U8 Playlist, we must rewrite the internal links 
    # so the video chunks (.ts files) also route through this proxy.
    if ".m3u8" in target_url or "mpegurl" in req.headers.get("Content-Type", "").lower():
        content = req.text
        new_m3u8 = []
        for line in content.splitlines():
            if line.startswith("#") or not line.strip():
                new_m3u8.append(line) # Keep metadata tags intact
            else:
                # Convert relative chunk links to absolute URLs, then wrap them in our proxy
                absolute_url = urljoin(target_url, line.strip())
                proxied_url = f"/proxy?url={quote(absolute_url)}"
                new_m3u8.append(proxied_url)
        
        return Response("\n".join(new_m3u8), content_type="application/vnd.apple.mpegurl")
    
    # If the response is the actual video data chunk (.ts or .mp4), stream it back directly
    def generate():
        for chunk in req.iter_content(chunk_size=1024 * 1024): # Stream in 1MB chunks
            if chunk:
                yield chunk
    
    return Response(generate(), content_type=req.headers.get("Content-Type", "video/mp2t"))


if __name__ == "__main__":
    # Render assigns a dynamic port, this grabs it automatically
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
