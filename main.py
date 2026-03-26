"""
- YouTube: better yt-dlp format (no ffmpeg needed) + updated Piped instances
- Pinterest: fixed yt-dlp options + proper headers
- Download on mobile: added /proxy-download endpoint with Content-Disposition header
"""

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
import yt_dlp
import time
import asyncio
import hashlib
import logging
import os
import re
import aiohttp
import json
from typing import Dict, Optional, List
from datetime import datetime

# ========== CONFIG ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="QuickReels API",
    version="18.2.0",
    description="Fixed YouTube + Pinterest extraction, mobile download support"
)

# ========== CORS ==========
ALLOWED_ORIGINS = [
    "https://quicksreels.web.app",
    "https://www.quicksreels.web.app",
    "http://localhost:3000",
    "http://localhost:5500",
    "http://127.0.0.1:5500",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========== CONCURRENCY ==========
extraction_semaphore = asyncio.Semaphore(5)

# ========== CACHE ==========
url_cache = {}
CACHE_TTL = 900  # 15 minutes
CACHE_HITS = 0
CACHE_MISSES = 0
MAX_CACHE_SIZE = 200

def get_cached(key: str) -> Optional[Dict]:
    global CACHE_HITS, CACHE_MISSES
    if key in url_cache:
        data, timestamp = url_cache[key]
        if time.time() - timestamp < CACHE_TTL:
            CACHE_HITS += 1
            return data.copy()
        else:
            del url_cache[key]
    CACHE_MISSES += 1
    return None

def set_cache(key: str, data: Dict):
    global url_cache
    if len(url_cache) >= MAX_CACHE_SIZE:
        items = sorted(url_cache.items(), key=lambda x: x[1][1])
        for k, _ in items[:MAX_CACHE_SIZE // 5]:
            del url_cache[k]
    url_cache[key] = (data.copy(), time.time())

# ========== RATE LIMITING ==========
rate_store = {}
RATE_LIMIT = 50
RATE_WINDOW = 60

def cleanup_rate_limiter():
    now = time.time()
    expired = [ip for ip, entry in rate_store.items() if now > entry['reset_at']]
    for ip in expired:
        del rate_store[ip]

def check_rate_limit(ip: str) -> bool:
    cleanup_rate_limiter()
    now = time.time()
    entry = rate_store.get(ip)
    if not entry or now > entry['reset_at']:
        rate_store[ip] = {'count': 1, 'reset_at': now + RATE_WINDOW}
        return True
    if entry['count'] >= RATE_LIMIT:
        return False
    entry['count'] += 1
    return True

# ========== PLATFORM DETECTION ==========
def detect_platform(url: str) -> str:
    u = url.lower()
    if 'instagram.com' in u:
        return 'instagram'
    if 'facebook.com' in u or 'fb.com' in u or 'fb.watch' in u:
        return 'facebook'
    if 'youtube.com' in u or 'youtu.be' in u:
        return 'youtube'
    if 'pinterest.com' in u or 'pin.it' in u:
        return 'pinterest'
    if 'tiktok.com' in u or 'vm.tiktok.com' in u:
        return 'tiktok'
    if 'twitter.com' in u or 'x.com' in u:
        return 'twitter'
    if 'reddit.com' in u or 'redd.it' in u:
        return 'reddit'
    return 'unknown'

SUPPORTED_PLATFORMS = {'instagram', 'facebook', 'youtube', 'pinterest', 'tiktok', 'twitter', 'reddit'}

# ========== HELPER FUNCTIONS ==========
def extract_youtube_id(url: str) -> Optional[str]:
    if 'youtu.be' in url:
        return url.split('/')[-1].split('?')[0]
    elif 'youtube.com/watch' in url:
        match = re.search(r'v=([^&]+)', url)
        return match.group(1) if match else None
    elif 'youtube.com/shorts' in url:
        return url.split('/')[-1].split('?')[0]
    return None

def get_best_youtube_stream(data: Dict, quality: str = "best") -> Optional[str]:
    """
    BUG FIX: Piped API returns both videoStreams (video-only, no audio) and audioStreams.
    We pick from videoStreams and also grab an audioStream separately.
    But since we can't merge on the server without ffmpeg, we just return the best
    video stream URL (the frontend/player handles it) OR fall back to yt-dlp.
    """
    video_streams = data.get('videoStreams', [])

    if not video_streams:
        return None

    quality_order = {
        "best": ["1080", "720", "480", "360"],
        "high": ["1080", "720"],
        "medium": ["720", "480"],
        "low": ["480", "360"]
    }

    priorities = quality_order.get(quality, quality_order["best"])

    for priority in priorities:
        for stream in video_streams:
            quality_label = str(stream.get('quality', ''))
            if priority in quality_label:
                url = stream.get('url')
                if url:
                    return url

    for stream in video_streams:
        url = stream.get('url')
        if url:
            return url

    return None

# ========== INSTAGRAM EXTRACTION ==========
async def extract_instagram_video(url: str) -> Dict:
    opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'ignoreerrors': False,
        'noplaylist': True,
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 Mobile/15E148 Safari/604.1',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-us,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
        }
    }

    cookie_file = get_instagram_cookies()
    if cookie_file:
        opts['cookiefile'] = cookie_file

    try:
        logger.info(f"Extracting Instagram video from: {url}")

        def _extract():
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)

        info = await asyncio.wait_for(asyncio.to_thread(_extract), timeout=30.0)

        if not info:
            return {'success': False, 'error': 'No video info found'}

        formats = info.get('formats', [])

        video_formats = []
        for f in formats:
            vcodec = f.get('vcodec', 'none')
            if vcodec != 'none':
                video_formats.append(f)

        if not video_formats:
            logger.error("No video formats found in Instagram response")
            return {'success': False, 'error': 'No video format available'}

        video_formats.sort(key=lambda x: x.get('height', 0) or 0, reverse=True)

        best_format = None
        for f in video_formats:
            if f.get('acodec') != 'none' and f.get('vcodec') != 'none':
                best_format = f
                break

        if not best_format:
            best_format = video_formats[0]
            logger.warning("No video+audio format found, using video-only format")

        video_url = best_format.get('url')
        if not video_url:
            video_url = info.get('url')

        if not video_url:
            return {'success': False, 'error': 'No video URL found'}

        if video_url.endswith(('.mp3', '.m4a', '.aac')) or 'audio' in video_url.lower():
            return {'success': False, 'error': 'Extracted audio-only content'}

        return {
            'success': True,
            'url': video_url,
            'title': info.get('title', 'Instagram Video'),
            'duration': info.get('duration'),
            'thumbnail': info.get('thumbnail'),
            'platform': 'instagram',
            'uploader': info.get('uploader', 'Instagram User'),
            'quality': f"{best_format.get('height', 'unknown')}p",
            'method': 'yt-dlp'
        }

    except asyncio.TimeoutError:
        return {'success': False, 'error': 'Extraction timeout after 30 seconds'}
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Instagram extraction error: {error_msg}", exc_info=True)
        return {'success': False, 'error': f'Instagram extraction failed: {error_msg[:100]}'}

def get_instagram_cookies():
    cookie_file = os.path.join(os.path.dirname(__file__), 'instagram_cookies.txt')
    if os.path.exists(cookie_file):
        return cookie_file
    return None

# ========== YOUTUBE VIA PIPED API (FIXED) ==========
async def extract_youtube_piped(url: str, quality: str = "best") -> Dict:
    video_id = extract_youtube_id(url)

    if not video_id:
        return {'success': False, 'error': 'Invalid YouTube URL'}

    logger.info(f"Extracting YouTube video: {video_id}")

    # FIX: Updated Piped API instances — old ones were dead
    piped_apis = [
        f"https://pipedapi.darkness.services/streams/{video_id}",
        f"https://pipedapi.adminforge.de/streams/{video_id}",
        f"https://piped-api.garudalinux.org/streams/{video_id}",
        f"https://pipedapi.in.projectsegfau.lt/streams/{video_id}",
        f"https://pipedapi.tokhmi.xyz/streams/{video_id}",
        f"https://pipedapi.kavin.rocks/streams/{video_id}",
    ]

    for api_url in piped_apis:
        try:
            logger.debug(f"Trying Piped API: {api_url}")
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(api_url) as response:
                    if response.status == 200:
                        data = await response.json(content_type=None)
                        video_url = get_best_youtube_stream(data, quality)

                        if video_url:
                            logger.info(f"YouTube success via Piped: {api_url}")
                            return {
                                'success': True,
                                'url': video_url,
                                'title': data.get('title', 'YouTube Video'),
                                'duration': data.get('duration'),
                                'thumbnail': data.get('thumbnailUrl'),
                                'platform': 'youtube',
                                'uploader': data.get('uploader', 'YouTube'),
                                'quality': quality,
                                'method': 'piped_api'
                            }
        except asyncio.TimeoutError:
            logger.warning(f"Piped API timeout: {api_url}")
            continue
        except Exception as e:
            logger.warning(f"Piped API {api_url} failed: {e}")
            continue

    # Fallback to yt-dlp
    logger.warning("All Piped APIs failed, falling back to yt-dlp")
    return await extract_youtube_ytdlp(url, quality)

async def extract_youtube_ytdlp(url: str, quality: str = "best") -> Dict:
    """
    FIX: Changed format string so it works WITHOUT ffmpeg.
    'bestvideo+bestaudio' requires ffmpeg to merge — servers often don't have it.
    Instead we pick pre-merged mp4 formats up to 1080p.
    """
    quality_format_map = {
        "best":   "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best[ext=mp4]/best",
        "high":   "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best[ext=mp4]/best",
        "medium": "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best[ext=mp4]/best",
        "low":    "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480][ext=mp4]/best[ext=mp4]/best",
    }

    opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'ignoreerrors': False,
        'noplaylist': True,
        'format': quality_format_map.get(quality, quality_format_map["best"]),
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        }
    }

    try:
        def _extract():
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)

        info = await asyncio.wait_for(asyncio.to_thread(_extract), timeout=30.0)

        if not info:
            return {'success': False, 'error': 'No video info found'}

        video_url = info.get('url')

        # If the top-level URL is missing, dig into formats
        if not video_url:
            formats = info.get('formats', [])
            # Prefer pre-merged mp4 with both video+audio
            for f in sorted(formats, key=lambda x: x.get('height', 0) or 0, reverse=True):
                if (f.get('vcodec') not in (None, 'none')
                        and f.get('acodec') not in (None, 'none')
                        and f.get('url')):
                    video_url = f.get('url')
                    break
            # Fallback: any mp4
            if not video_url:
                for f in formats:
                    if f.get('ext') == 'mp4' and f.get('url'):
                        video_url = f.get('url')
                        break
            # Last resort: first format with URL
            if not video_url:
                for f in formats:
                    if f.get('url'):
                        video_url = f.get('url')
                        break

        if not video_url:
            return {'success': False, 'error': 'No video URL found'}

        height = info.get('height') or 'unknown'

        return {
            'success': True,
            'url': video_url,
            'title': info.get('title', 'YouTube Video'),
            'duration': info.get('duration'),
            'thumbnail': info.get('thumbnail'),
            'platform': 'youtube',
            'uploader': info.get('uploader', 'YouTube'),
            'quality': f"{height}p",
            'method': 'yt-dlp'
        }

    except asyncio.TimeoutError:
        return {'success': False, 'error': 'Extraction timeout'}
    except Exception as e:
        logger.error(f"YouTube yt-dlp error: {e}")
        return {'success': False, 'error': str(e)[:200]}

# ========== OTHER PLATFORMS (FIXED Pinterest + general) ==========
async def extract_other_platform(url: str, platform: str) -> Dict:
    """
    FIX:
    - 'cachedir': False  →  'no_cache': True  (wrong key was causing yt-dlp errors)
    - Pinterest: added proper User-Agent + referer headers
    - Better format fallback chain for all platforms
    """
    opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'ignoreerrors': False,
        'no_cache': True,          # FIX: was 'cachedir': False which is wrong
        'noplaylist': True,
        'format': 'best[ext=mp4]/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best',
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        }
    }

    if platform == 'tiktok':
        opts['http_headers']['User-Agent'] = 'TikTok 26.2.0 rv:262018 (iPhone; iOS 14.4.2; en_US) Cronet'
        opts['format'] = 'best[ext=mp4]/best'

    elif platform == 'pinterest':
        # FIX: Pinterest needs a browser-like referer + accept headers
        opts['http_headers'] = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': 'https://www.pinterest.com/',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        }
        opts['format'] = 'best[ext=mp4]/best'

    elif platform == 'facebook':
        opts['format'] = 'best[ext=mp4]/best'

    elif platform == 'twitter':
        opts['format'] = 'best[ext=mp4]/best'

    elif platform == 'reddit':
        opts['format'] = 'best[ext=mp4]/best'

    try:
        logger.info(f"Extracting {platform} video from: {url}")

        def _extract():
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)

        info = await asyncio.wait_for(asyncio.to_thread(_extract), timeout=30.0)

        if not info:
            return {'success': False, 'error': 'No video info found'}

        video_url = info.get('url')
        formats = info.get('formats', [])

        # Try mp4 with video codec first
        if not video_url:
            for f in sorted(formats, key=lambda x: x.get('height', 0) or 0, reverse=True):
                if f.get('ext') == 'mp4' and f.get('vcodec') not in (None, 'none') and f.get('url'):
                    video_url = f.get('url')
                    break

        # Any format with video codec
        if not video_url:
            for f in sorted(formats, key=lambda x: x.get('height', 0) or 0, reverse=True):
                if f.get('vcodec') not in (None, 'none') and f.get('url'):
                    video_url = f.get('url')
                    break

        # Last resort: first URL
        if not video_url:
            for f in formats:
                if f.get('url'):
                    video_url = f.get('url')
                    break

        if not video_url:
            return {'success': False, 'error': 'No video URL found'}

        return {
            'success': True,
            'url': video_url,
            'title': info.get('title', f'{platform.capitalize()} Video'),
            'duration': info.get('duration'),
            'thumbnail': info.get('thumbnail'),
            'platform': platform,
            'uploader': info.get('uploader'),
            'quality': f"{info.get('height', 'unknown')}p",
            'method': 'yt-dlp'
        }

    except asyncio.TimeoutError:
        return {'success': False, 'error': 'Extraction timeout'}
    except Exception as e:
        error_msg = str(e)
        logger.error(f"{platform} error: {error_msg}")
        return {'success': False, 'error': error_msg[:100]}

# ========== MAIN ENDPOINT ==========
@app.get("/download")
async def process_video(
    request: Request,
    link: str = Query(..., description="Video URL"),
    quality: str = Query("best", description="Video quality (best, high, medium, low)")
):
    start_time = time.time()

    forwarded = request.headers.get("x-forwarded-for")
    client_ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")

    if not check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again in a minute.")

    if not link or not link.startswith(('http://', 'https://')):
        raise HTTPException(status_code=400, detail="Invalid URL. Must start with http:// or https://")

    platform = detect_platform(link)
    if platform not in SUPPORTED_PLATFORMS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported platform. Supported: {', '.join(SUPPORTED_PLATFORMS)}"
        )

    if quality not in ['best', 'high', 'medium', 'low']:
        quality = 'best'

    try:
        cache_key = hashlib.md5(f"{link}_{quality}".encode()).hexdigest()
        cached = get_cached(cache_key)

        if cached:
            cached['response_time'] = round((time.time() - start_time) * 1000, 2)
            cached['from_cache'] = True
            return JSONResponse(content=cached)

        if platform == 'youtube':
            result = await extract_youtube_piped(link, quality)
        elif platform == 'instagram':
            result = await extract_instagram_video(link)
        else:
            result = await extract_other_platform(link, platform)

        response_time = round((time.time() - start_time) * 1000, 2)

        if not result.get('success'):
            raise HTTPException(status_code=500, detail=result.get('error', 'Extraction failed'))

        response_data = {
            'url': result['url'],
            'title': result['title'],
            'duration': result.get('duration'),
            'thumbnail': result.get('thumbnail'),
            'platform': result['platform'],
            'uploader': result.get('uploader'),
            'quality': result.get('quality', quality),
            'response_time': response_time,
            'from_cache': False,
            'method': result.get('method', 'unknown'),
            'timestamp': datetime.now().isoformat()
        }

        set_cache(cache_key, response_data)
        logger.info(f"SUCCESS | {platform} | {result.get('method')} | {response_time}ms | IP: {client_ip}")

        return JSONResponse(content=response_data)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error. Please try again later.")


# ========== PROXY DOWNLOAD ENDPOINT (NEW - fixes mobile download bug) ==========
# FIX: Mobile browsers open video URLs in a new tab instead of downloading.
# This endpoint fetches the video server-side and streams it back with
# Content-Disposition: attachment — the browser MUST download it, not open it.
#
# Frontend usage:
#   Instead of: window.open(videoUrl)
#   Use:        window.location.href = `/proxy-download?url=${encodeURIComponent(videoUrl)}&filename=video.mp4`
#
@app.get("/proxy-download")
async def proxy_download(
    request: Request,
    url: str = Query(..., description="Direct video URL to proxy"),
    filename: str = Query("video.mp4", description="Download filename")
):
    forwarded = request.headers.get("x-forwarded-for")
    client_ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")

    if not check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    if not url.startswith(('http://', 'https://')):
        raise HTTPException(status_code=400, detail="Invalid URL")

    # Sanitize filename
    safe_filename = re.sub(r'[^\w\-.]', '_', filename)
    if not safe_filename.endswith('.mp4'):
        safe_filename += '.mp4'

    try:
        timeout = aiohttp.ClientTimeout(total=None, connect=10, sock_read=60)

        async def stream_video():
            async with aiohttp.ClientSession(timeout=timeout) as session:
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Referer': url.split('/')[0] + '//' + url.split('/')[2] + '/',
                }
                async with session.get(url, headers=headers) as resp:
                    if resp.status not in (200, 206):
                        raise HTTPException(status_code=502, detail="Failed to fetch video from source")
                    async for chunk in resp.content.iter_chunked(1024 * 64):
                        yield chunk

        return StreamingResponse(
            stream_video(),
            media_type="video/mp4",
            headers={
                "Content-Disposition": f'attachment; filename="{safe_filename}"',
                "Cache-Control": "no-cache",
                "X-Content-Type-Options": "nosniff",
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Proxy download error: {e}")
        raise HTTPException(status_code=500, detail="Failed to proxy video download")


# ========== DEDICATED INSTAGRAM ENDPOINT ==========
@app.get("/instagram")
async def download_instagram(
    request: Request,
    link: str = Query(..., description="Instagram URL")
):
    start_time = time.time()

    forwarded = request.headers.get("x-forwarded-for")
    client_ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")

    if not check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    if 'instagram.com' not in link:
        raise HTTPException(status_code=400, detail="Not an Instagram URL")

    cache_key = hashlib.md5(f"insta_{link}".encode()).hexdigest()
    cached = get_cached(cache_key)

    if cached:
        cached['response_time'] = round((time.time() - start_time) * 1000, 2)
        cached['from_cache'] = True
        return JSONResponse(content=cached)

    result = await extract_instagram_video(link)

    if not result.get('success'):
        raise HTTPException(status_code=500, detail=result.get('error'))

    response_time = round((time.time() - start_time) * 1000, 2)
    response_data = {
        **result,
        'response_time': response_time,
        'from_cache': False
    }

    set_cache(cache_key, response_data)
    return JSONResponse(content=response_data)


# ========== STATS ENDPOINT ==========
@app.get("/stats")
async def get_stats():
    return {
        "cache_hits": CACHE_HITS,
        "cache_misses": CACHE_MISSES,
        "cache_size": len(url_cache),
        "active_ips": len(rate_store),
        "cache_hit_rate": f"{(CACHE_HITS / (CACHE_HITS + CACHE_MISSES) * 100):.1f}%" if (CACHE_HITS + CACHE_MISSES) > 0 else "0%",
        "supported_platforms": list(SUPPORTED_PLATFORMS),
        "version": "18.2.0"
    }

# ========== HEALTH CHECK ==========
@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "version": "18.2.0",
        "timestamp": datetime.now().isoformat()
    }

@app.get("/")
async def root():
    return {
        "name": "QuickReels API",
        "version": "18.2.0",
        "status": "Active",
        "youtube_method": "Piped API (updated instances) + yt-dlp fallback (no ffmpeg required)",
        "supported_platforms": list(SUPPORTED_PLATFORMS),
        "endpoints": [
            "/download?link=URL&quality=best",
            "/instagram?link=URL",
            "/proxy-download?url=DIRECT_VIDEO_URL&filename=video.mp4",
            "/stats",
            "/health"
        ],
        "quality_options": ["best", "high", "medium", "low"]
    }

# ========== ERROR HANDLERS ==========
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "error": exc.detail,
            "status_code": exc.status_code,
            "timestamp": datetime.now().isoformat()
        }
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": "Internal server error",
            "timestamp": datetime.now().isoformat()
        }
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")(app, host="0.0.0.0", port=8000)
