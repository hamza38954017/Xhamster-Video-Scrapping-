import os
import re
import json
from urllib.parse import urljoin, quote, unquote, urlparse, urlunparse
from flask import Flask, request, render_template_string, Response
from curl_cffi import requests as cffi_requests

app = Flask(__name__)

BASE_DOMAIN = "https://xhamster45.desi"
SPOOF_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Video Streamer & Downloader</title>
    <link href="https://vjs.zencdn.net/8.6.1/video-js.css" rel="stylesheet" />
    <style>
        body { font-family: 'Segoe UI', Tahoma, sans-serif; background: #0f0f0f; color: #fff; text-align: center; padding: 40px 20px; margin: 0; }
        h1 { color: #ff4757; margin-bottom: 10px; }
        p.subtitle { color: #888; margin-bottom: 30px; }
        .search-container { margin-bottom: 40px; display: flex; justify-content: center; gap: 10px; }
        input[type="text"] { width: 60%; max-width: 600px; padding: 12px 20px; font-size: 16px; border-radius: 8px; border: 1px solid #333; background: #222; color: white; outline: none; }
        button { padding: 12px 24px; font-size: 16px; background: #ff4757; color: white; border: none; border-radius: 8px; cursor: pointer; font-weight: bold; }
        button:hover { background: #ff6b81; }
        .video-wrapper { max-width: 900px; margin: 0 auto; box-shadow: 0 10px 30px rgba(0,0,0,0.8); border-radius: 8px; overflow: hidden; background: #000; margin-bottom: 20px;}
        .error { color: #ff6b81; font-weight: bold; margin-top: 20px; }
        .action-buttons { display: flex; justify-content: center; gap: 15px; margin-top: 20px; }
        .download-btn { padding: 12px 24px; font-size: 16px; background: #2ecc71; color: white; border: none; border-radius: 8px; cursor: pointer; font-weight: bold; text-decoration: none; display: inline-block; }
        .download-btn:hover { background: #27ae60; }
    </style>
</head>
<body>
    <h1>🚀 Ultimate Video Streamer</h1>
    <p class="subtitle">Stream and Download directly bypassing CDN restrictions.</p>

    <form class="search-container" method="POST" action="/">
        <input type="text" name="url" placeholder="Paste full video page URL here..." required>
        <button type="submit">Fetch Video</button>
    </form>

    {% if error %}<p class="error">⚠️ {{ error }}</p>{% endif %}

    {% if stream_url %}
        <h3 style="margin-bottom: 20px;">{{ title }}</h3>
        
        <div class="video-wrapper">
            <video-js id="my-video" class="vjs-default-skin vjs-16-9 vjs-big-play-centered" controls preload="auto" autoplay>
                <source src="/proxy?url={{ quoted_stream_url }}" type="application/x-mpegURL">
            </video-js>
        </div>
        
        <div class="action-buttons">
            {% if quoted_download_url %}
                <a href="/download?url={{ quoted_download_url }}&title={{ encoded_title }}" class="download-btn">⬇️ Download MP4 File</a>
            {% else %}
                <button class="download-btn" style="background: #555; cursor: not-allowed;" disabled>⚠️ Direct MP4 Not Available</button>
            {% endif %}
        </div>

        <script src="https://vjs.zencdn.net/8.6.1/video.min.js"></script>
        <script>var player = videojs('my-video', { fluid: true });</script>
    {% endif %}
</body>
</html>
"""

def smart_urljoin(base, link):
    if link.startswith("http"): return link
    joined = urljoin(base, link)
    base_parsed = urlparse(base)
    joined_parsed = urlparse(joined)
    if base_parsed.query and not joined_parsed.query:
        joined = urlunparse(joined_parsed._replace(query=base_parsed.query))
    return joined

@app.route("/", methods=["GET", "POST"])
def index():
    stream_url, quoted_stream_url = None, None
    download_url, quoted_download_url = None, None
    title = "Video"
    encoded_title = "video"
    error = None

    if request.method == "POST":
        page_url = request.form.get("url")
        try:
            resp = cffi_requests.get(page_url, impersonate="chrome120", timeout=15)
            html = resp.text

            title_match = re.search(r'<title>(.*?)</title>', html)
            if title_match: 
                title = title_match.group(1).replace(" | xHamster", "").strip()
                # Clean title for saving files (remove special chars)
                encoded_title = quote(re.sub(r'[^\w\s-]', '', title).strip().replace(' ', '_'))

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
                
                # Extract HLS for streaming
                if "hls" in sources: 
                    stream_url = sources["hls"].get("url")
                
                # Extract Direct MP4 for downloading
                if "mp4" in sources:
                    mp4_data = sources["mp4"]
                    if isinstance(mp4_data, dict):
                        # Attempt to find a valid URL inside the mp4 dictionary
                        if "url" in mp4_data:
                            download_url = mp4_data["url"]
                        else:
                            for key, val in mp4_data.items():
                                if isinstance(val, dict) and "url" in val:
                                    download_url = val["url"]
                                    break
                    elif isinstance(mp4_data, str):
                        download_url = mp4_data

            if not stream_url:
                m3u8_matches = re.findall(r'https?:\/\/[^\s<>"\'\\]+\.m3u8[^\s<>"\'\\]*', html)
                if m3u8_matches:
                    valid_streams = [m.replace('\\/', '/') for m in m3u8_matches if 'tsyndicate' not in m and 'trafficstars' not in m]
                    if valid_streams: stream_url = valid_streams[0]

            if stream_url:
                quoted_stream_url = quote(stream_url)
                # Fallback: if no MP4 found, default streaming url (though downloading m3u8 won't work well)
                if download_url:
                    quoted_download_url = quote(download_url)
            else:
                error = "Could not find a valid stream link. The video might be premium-locked."

        except Exception as e:
            error = f"Error scraping page: {str(e)}"

    return render_template_string(
        HTML_TEMPLATE, 
        stream_url=stream_url, 
        quoted_stream_url=quoted_stream_url, 
        quoted_download_url=quoted_download_url,
        title=title, 
        encoded_title=encoded_title,
        error=error
    )


@app.route("/proxy")
def proxy():
    """Proxies the HLS Stream for playback."""
    try:
        raw_url = request.url.split("url=", 1)[1]
        target_url = unquote(raw_url)
    except IndexError:
        return "No URL provided", 400

    headers = { "Referer": BASE_DOMAIN, "Origin": BASE_DOMAIN, "User-Agent": SPOOF_USER_AGENT, "Accept": "*/*" }

    try:
        req = cffi_requests.get(target_url, headers=headers, stream=True, impersonate="chrome120", timeout=15)
    except Exception as e:
        return str(e), 500

    if req.status_code >= 400:
        return Response(req.content, status=req.status_code)

    content_type = req.headers.get("Content-Type", "")

    if ".m3u8" in target_url or "mpegurl" in content_type.lower():
        content = req.text
        def replace_uri(match):
            orig_uri = match.group(1)
            abs_uri = smart_urljoin(target_url, orig_uri)
            return f'URI="/proxy?url={quote(abs_uri)}"'
        content = re.sub(r'URI="([^"]+)"', replace_uri, content)
        
        new_m3u8 = []
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("#") or not line: new_m3u8.append(line)
            else:
                abs_url = smart_urljoin(target_url, line)
                new_m3u8.append(f"/proxy?url={quote(abs_url)}")
                
        return Response("\n".join(new_m3u8), content_type="application/vnd.apple.mpegurl")
    
    def generate():
        for chunk in req.iter_content(chunk_size=65536): 
            if chunk: yield chunk
    
    resp = Response(generate(), content_type=content_type or "video/mp2t")
    resp.headers['Access-Control-Allow-Origin'] = '*'
    return resp


@app.route("/download")
def download_video():
    """Proxies the direct MP4 file for downloading to bypass CDN blocks."""
    target_url = request.args.get("url")
    filename = request.args.get("title", "downloaded_video") + ".mp4"
    
    if not target_url:
        return "No URL provided", 400

    headers = { "Referer": BASE_DOMAIN, "User-Agent": SPOOF_USER_AGENT }

    try:
        # We must use curl_cffi to bypass Cloudflare for the download too
        req = cffi_requests.get(unquote(target_url), headers=headers, stream=True, impersonate="chrome120", timeout=15)
    except Exception as e:
        return str(e), 500

    if req.status_code >= 400:
        return f"CDN Blocked the Download (HTTP {req.status_code})", req.status_code

    def generate():
        # Larger chunk size (1MB) because it's a file download, not real-time streaming
        for chunk in req.iter_content(chunk_size=1024 * 1024): 
            if chunk: yield chunk

    resp = Response(generate(), content_type="video/mp4")
    # This header tells the browser to save it as a file instead of playing it
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
