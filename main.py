"""
🚀 VIDEO ROCKET API - FINAL v10.5
Production Ready | Full Platform Support
Platforms: Instagram, Facebook, YouTube, Pinterest, TikTok, Twitter/X, Reddit

Run: uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
Install: pip install fastapi uvicorn yt-dlp
"""

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
import yt_dlp
import time
import asyncio
import hashlib
import logging
import os
from typing import Dict, Optional

# ========== CONFIG ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Video Rocket API",
    version="10.5.0",
    description="Production-ready video extraction API"
)

# ========== CORS ==========
PRODUCTION_DOMAINS = [
    "https://quicksreels.web.app",
    "https://www.quicksreels.web.app",
    "http://localhost:3000",
    "http://localhost:7700",
    "http://localhost:8000",
]

ENV = os.getenv("ENV", "development")
ALLOWED_ORIGINS = ["*"] if ENV == "development" else PRODUCTION_DOMAINS

logger.info(f"Running in {ENV} mode | CORS: {ALLOWED_ORIGINS}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# ========== CONCURRENCY SEMAPHORE ==========
extraction_semaphore = asyncio.Semaphore(3)

# ========== YT-DLP BASE OPTIONS ==========
YDL_OPTS_BASE = {
    'quiet': True,
    'no_warnings': True,
    'extract_flat': False,
    'ignoreerrors': False,
    'cachedir': False,
    'noplaylist': True,
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    }
}

# Per-platform yt-dlp option overrides
PLATFORM_OPTS: Dict[str, dict] = {
    'youtube': {
        'format': 'bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/best[ext=mp4]/best',
    },
    'instagram': {
        'format': 'best[ext=mp4]/best',
    },
    'facebook': {
        'format': 'best[ext=mp4]/best',
    },
    'tiktok': {
        'format': 'best[ext=mp4]/best',
        'http_headers': {
            'User-Agent': 'TikTok 26.2.0 rv:262018 (iPhone; iOS 14.4.2; en_US) Cronet',
        }
    },
    'twitter': {
        'format': 'best[ext=mp4]/best',
    },
    'pinterest': {
        'format': 'best[ext=mp4]/best',
    },
    'reddit': {
        'format': 'best[ext=mp4]/best',
    },
}

# ========== CACHE ==========
url_cache: Dict[str, tuple] = {}
CACHE_TTL = 600        # 10 minutes
MAX_CACHE_SIZE = 100
CACHE_HITS = 0
CACHE_MISSES = 0


def clean_old_cache():
    global url_cache
    if len(url_cache) > MAX_CACHE_SIZE:
        items_to_remove = int(MAX_CACHE_SIZE * 0.2)
        sorted_items = sorted(url_cache.items(), key=lambda x: x[1][1])
        for key, _ in sorted_items[:items_to_remove]:
            del url_cache[key]


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
    url_cache[key] = (data.copy(), time.time())
    clean_old_cache()


# ========== RATE LIMITING (in-memory, no deps) ==========
_rate_store: Dict[str, dict] = {}
RATE_LIMIT = 20       # requests
RATE_WINDOW = 60      # seconds


def check_rate_limit(ip: str) -> bool:
    now = time.time()
    entry = _rate_store.get(ip)
    if not entry or now > entry['reset_at']:
        _rate_store[ip] = {'count': 1, 'reset_at': now + RATE_WINDOW}
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
    if 'twitter.com' in u or 'x.com' in u or 't.co' in u:
        return 'twitter'
    if 'reddit.com' in u or 'redd.it' in u:
        return 'reddit'
    return 'unknown'


SUPPORTED_PLATFORMS = {'instagram', 'facebook', 'youtube', 'pinterest', 'tiktok', 'twitter', 'reddit'}


# ========== FORMAT SELECTION ==========
def get_best_format(formats: list) -> Optional[str]:
    if not formats:
        return None

    # Sort by resolution (best quality first)
    sorted_formats = sorted(
        [f for f in formats if f.get('height') and f.get('url')],
        key=lambda x: x.get('height', 0),
        reverse=True
    )

    # Prefer mp4 with audio
    for f in sorted_formats:
        if f.get('ext') == 'mp4' and f.get('acodec') not in (None, 'none'):
            return f['url']

    # Any format with audio
    for f in sorted_formats:
        if f.get('acodec') not in (None, 'none') and f.get('url'):
            return f['url']

    # Fallback: highest resolution regardless
    if sorted_formats:
        return sorted_formats[0].get('url')

    return None


# ========== EXTRACTOR ==========
async def extract_all_data(url: str, platform: str, retry_count: int = 0) -> Dict:
    acquired = False

    try:
        # Try to acquire semaphore with 2s timeout
        try:
            await asyncio.wait_for(extraction_semaphore.acquire(), timeout=2.0)
            acquired = True
        except asyncio.TimeoutError:
            return {
                'success': False,
                'error': 'Server busy. Please retry in a few seconds.',
                'busy': True
            }

        # Build platform-specific options
        opts = {**YDL_OPTS_BASE}
        for key, val in PLATFORM_OPTS.get(platform, {}).items():
            if key == 'http_headers':
                opts['http_headers'] = {**opts.get('http_headers', {}), **val}
            else:
                opts[key] = val

        if 'format' not in opts:
            opts['format'] = 'bv*[ext=mp4]+ba/b[ext=mp4]/best[ext=mp4]/best'

        def _extract():
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)

        # Run blocking yt-dlp in a thread
        info = await asyncio.wait_for(
            asyncio.to_thread(_extract),
            timeout=30.0
        )

        if not info:
            return {'success': False, 'error': 'Could not extract video info. May be private or removed.'}

        # Pick best video URL
        video_url = get_best_format(info.get('formats', []))
        if not video_url:
            video_url = info.get('url')

        if not video_url:
            return {'success': False, 'error': 'No playable video URL found in extraction result.'}

        return {
            'success': True,
            'url': video_url,
            'title': info.get('title') or 'video',
            'duration': info.get('duration'),
            'thumbnail': info.get('thumbnail'),
            'platform': platform,
            'uploader': info.get('uploader') or info.get('channel'),
        }

    except asyncio.TimeoutError:
        logger.warning(f"TIMEOUT | {platform} | {url[:50]}")
        if retry_count < 1:
            await asyncio.sleep(1)
            return await extract_all_data(url, platform, retry_count + 1)
        return {'success': False, 'error': 'Extraction timed out (30s). Please try again.'}

    except yt_dlp.utils.DownloadError as e:
        msg = str(e)
        logger.error(f"yt-dlp | {platform} | {msg[:120]}")

        if 'Private video' in msg or 'This video is private' in msg:
            return {'success': False, 'error': 'Video is private or unavailable.'}
        if 'has been removed' in msg or 'no longer available' in msg:
            return {'success': False, 'error': 'Video has been removed or is unavailable.'}
        if 'Unsupported URL' in msg:
            return {'success': False, 'error': 'URL not supported. Make sure it is a direct video post.'}
        if 'Login required' in msg or 'Sign in' in msg:
            return {'success': False, 'error': 'This video requires login to access.'}

        # Auto-retry for session-sensitive platforms
        if retry_count < 1 and platform in ('instagram', 'tiktok', 'twitter', 'facebook'):
            await asyncio.sleep(1.5)
            return await extract_all_data(url, platform, retry_count + 1)

        return {'success': False, 'error': f'Extraction failed: {msg[:200]}'}

    except Exception as e:
        logger.error(f"Unexpected | {platform} | {str(e)[:100]}")
        return {'success': False, 'error': f'Internal error: {str(e)[:200]}'}

    finally:
        if acquired:
            extraction_semaphore.release()


# ========== /download ENDPOINT ==========
@app.get("/download")
async def download_video(
    request: Request,
    link: str = Query(..., description="Social media video URL"),
    raw: bool = Query(False, description="Return only the direct video URL as plain text")
):
    start_time = time.time()

    # Get client IP
    forwarded = request.headers.get("x-forwarded-for")
    client_ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")

    # Rate limit
    if not check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Max 20 requests/minute.")

    # Validate URL
    if not link or not link.startswith(('http://', 'https://')):
        raise HTTPException(status_code=400, detail="Invalid URL. Must start with http:// or https://")

    # Detect platform
    platform = detect_platform(link)
    if platform not in SUPPORTED_PLATFORMS:
        raise HTTPException(
            status_code=400,
            detail="Unsupported platform. Supported: Instagram, Facebook, YouTube, Pinterest, TikTok, Twitter/X, Reddit"
        )

    try:
        # Cache lookup
        cache_key = hashlib.md5(link.encode()).hexdigest()
        cached = get_cached(cache_key)

        if cached:
            cached['response_time'] = round((time.time() - start_time) * 1000, 2)
            cached['instant'] = True
            cached['active_extractions'] = 3 - extraction_semaphore._value
            logger.info(f"CACHE | {platform} | {cached['response_time']}ms")
            if raw:
                return PlainTextResponse(content=cached['url'])
            return JSONResponse(content=cached)

        # Fresh extraction
        result = await extract_all_data(link, platform)
        response_time = round((time.time() - start_time) * 1000, 2)

        if not result['success']:
            if result.get('busy'):
                raise HTTPException(status_code=429, detail=result['error'])
            logger.error(f"FAIL | {platform} | {response_time}ms | {result['error']}")
            raise HTTPException(status_code=500, detail=result['error'])

        result['response_time'] = response_time
        result['instant'] = False
        result['active_extractions'] = 3 - extraction_semaphore._value

        logger.info(f"SUCCESS | {platform} | {response_time}ms | {str(result.get('title', ''))[:40]}")

        set_cache(cache_key, result)

        if raw:
            return PlainTextResponse(content=result['url'])

        return JSONResponse(content=result)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Endpoint error: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


# ========== / ROOT ==========
@app.get("/")
async def root():
    return {
        "name": "Video Rocket API",
        "version": "10.5.0",
        "status": "Production Ready",
        "mode": ENV,
        "cors": "all origins" if ALLOWED_ORIGINS == ["*"] else f"{len(ALLOWED_ORIGINS)} domains",
        "cache": f"{len(url_cache)}/{MAX_CACHE_SIZE}",
        "supported_platforms": sorted(SUPPORTED_PLATFORMS),
        "endpoints": {
            "GET /download?link=URL": "Get video info + direct URL (JSON)",
            "GET /download?link=URL&raw=true": "Get direct video URL only (plain text)",
            "GET /health": "Health check",
            "GET /status": "System status",
        }
    }


# ========== /status ==========
@app.get("/status")
async def get_status():
    active = 3 - extraction_semaphore._value
    total = CACHE_HITS + CACHE_MISSES
    return {
        "version": "10.5.0",
        "mode": ENV,
        "capacity": {
            "max_concurrent": 3,
            "active_extractions": active,
            "available_slots": extraction_semaphore._value,
            "status": "available" if extraction_semaphore._value > 0 else "busy",
        },
        "cache": {
            "size": len(url_cache),
            "max_size": MAX_CACHE_SIZE,
            "hit_rate": f"{(CACHE_HITS / total * 100):.1f}%" if total > 0 else "0%",
        },
        "rate_limit": f"{RATE_LIMIT}/minute",
        "supported_platforms": sorted(SUPPORTED_PLATFORMS),
    }


# ========== /health ==========
@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "version": "10.5.0",
        "mode": ENV,
        "ready": extraction_semaphore._value > 0,
    }


# ========== RUN ==========
# uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
