"""
🚀 VIDEO ROCKET API - FINAL v12.1
YouTube Fixed + Download Feature Added
"""

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
import yt_dlp
import time
import asyncio
import hashlib
import logging
import os
import re
import json
import requests
from typing import Dict, Optional
import urllib.parse

# ========== CONFIG ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Video Rocket API",
    version="12.1.0",
    description="Video extraction API with download support"
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========== CONCURRENCY ==========
extraction_semaphore = asyncio.Semaphore(3)

# ========== ENHANCED YT-DLP OPTIONS FOR YOUTUBE ==========
YDL_OPTS_BASE = {
    'quiet': True,
    'no_warnings': True,
    'extract_flat': False,
    'ignoreerrors': False,
    'cachedir': False,
    'noplaylist': True,
    'prefer_ffmpeg': False,
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-us,en;q=0.5',
        'Sec-Fetch-Mode': 'navigate',
    }
}

# Special options for YouTube (to bypass blocks)
YOUTUBE_OPTS = {
    'format': 'best[ext=mp4]/best',
    'extract_flat': False,
    'extractor_args': {'youtube': {'skip': ['dash', 'hls']}},
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-us,en;q=0.5',
        'Sec-Fetch-Mode': 'navigate',
        'Cookie': 'CONSENT=YES+IN; PREF=hl=en&tz=UTC;',  # Consent cookie
    }
}

PLATFORM_OPTS: Dict[str, dict] = {
    'youtube': YOUTUBE_OPTS,
    'instagram': {'format': 'best[ext=mp4]/best'},
    'facebook': {'format': 'best[ext=mp4]/best'},
    'tiktok': {'format': 'best[ext=mp4]/best'},
    'twitter': {'format': 'best[ext=mp4]/best'},
    'pinterest': {'format': 'best[ext=mp4]/best'},
    'reddit': {'format': 'best[ext=mp4]/best'},
}

# ========== CACHE ==========
url_cache: Dict[str, tuple] = {}
CACHE_TTL = 600
MAX_CACHE_SIZE = 100
CACHE_HITS = 0
CACHE_MISSES = 0

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
    if len(url_cache) > MAX_CACHE_SIZE:
        # Remove oldest 20%
        items_to_remove = int(MAX_CACHE_SIZE * 0.2)
        sorted_items = sorted(url_cache.items(), key=lambda x: x[1][1])
        for key, _ in sorted_items[:items_to_remove]:
            del url_cache[key]
    url_cache[key] = (data.copy(), time.time())

# ========== RATE LIMITING ==========
_rate_store: Dict[str, dict] = {}
RATE_LIMIT = 30
RATE_WINDOW = 60

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
    if 'twitter.com' in u or 'x.com' in u:
        return 'twitter'
    if 'reddit.com' in u or 'redd.it' in u:
        return 'reddit'
    return 'unknown'

SUPPORTED_PLATFORMS = {'instagram', 'facebook', 'youtube', 'pinterest', 'tiktok', 'twitter', 'reddit'}

# ========== YOUTUBE SPECIAL EXTRACTOR ==========
def extract_youtube_special(url: str) -> Optional[Dict]:
    """YouTube ke liye special extraction method"""
    try:
        # Video ID nikal lo
        video_id = None
        if 'youtu.be' in url:
            video_id = url.split('/')[-1].split('?')[0]
        elif 'youtube.com/watch' in url:
            match = re.search(r'v=([^&]+)', url)
            if match:
                video_id = match.group(1)
        elif 'youtube.com/shorts' in url:
            video_id = url.split('/')[-1].split('?')[0]
        
        if not video_id:
            return None
        
        logger.info(f"YouTube Video ID: {video_id}")
        
        # Method 1: Piped API (working alternative)
        piped_apis = [
            f"https://pipedapi.kavin.rocks/streams/{video_id}",
            f"https://pipedapi.adminforge.de/streams/{video_id}",
            f"https://pipedapi.moomoo.me/streams/{video_id}",
        ]
        
        for api in piped_apis:
            try:
                response = requests.get(api, timeout=10)
                if response.status_code == 200:
                    data = response.json()
                    # Video streams mein se best quality chuno
                    video_streams = data.get('videoStreams', [])
                    for stream in video_streams:
                        if stream.get('quality') == 'hd' or stream.get('quality') == 'medium':
                            return {
                                'url': stream.get('url'),
                                'title': data.get('title', 'YouTube Video'),
                                'thumbnail': data.get('thumbnailUrl', f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"),
                                'duration': data.get('duration'),
                                'uploader': data.get('uploader'),
                                'platform': 'youtube',
                                'method': 'piped_api'
                            }
            except:
                continue
        
        # Method 2: YouTube oEmbed (at least title milega)
        oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
        response = requests.get(oembed_url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            return {
                'url': f"https://www.youtube.com/watch?v={video_id}",
                'title': data.get('title', 'YouTube Video'),
                'thumbnail': f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
                'platform': 'youtube',
                'uploader': data.get('author_name'),
                'method': 'oembed',
                'note': 'Use this URL directly or try again'
            }
        
    except Exception as e:
        logger.error(f"YouTube special extractor error: {e}")
    
    return None

# ========== FORMAT SELECTION ==========
def get_best_format(formats: list) -> Optional[str]:
    if not formats:
        return None
    
    # MP4 with audio pehle try karo
    for f in formats:
        if f.get('ext') == 'mp4' and f.get('acodec') != 'none':
            return f.get('url')
    
    # Koi bhi video with audio
    for f in formats:
        if f.get('vcodec') != 'none' and f.get('acodec') != 'none':
            return f.get('url')
    
    # Sirf video
    for f in formats:
        if f.get('vcodec') != 'none':
            return f.get('url')
    
    return None

# ========== MAIN EXTRACTOR ==========
async def extract_all_data(url: str, platform: str) -> Dict:
    acquired = False
    
    try:
        # Semaphore acquire
        try:
            await asyncio.wait_for(extraction_semaphore.acquire(), timeout=2.0)
            acquired = True
        except asyncio.TimeoutError:
            return {
                'success': False,
                'error': 'Server busy. Please retry.',
                'busy': True
            }
        
        # YouTube ke liye special handling
        if platform == 'youtube':
            logger.info("Trying YouTube special extractor...")
            special_result = await asyncio.to_thread(extract_youtube_special, url)
            if special_result and special_result.get('url'):
                special_result['success'] = True
                return special_result
        
        # Baaki platforms ke liye yt-dlp
        opts = {**YDL_OPTS_BASE}
        if platform in PLATFORM_OPTS:
            opts.update(PLATFORM_OPTS[platform])
        
        def _extract():
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)
        
        try:
            info = await asyncio.wait_for(asyncio.to_thread(_extract), timeout=35.0)
            
            if info:
                video_url = get_best_format(info.get('formats', []))
                if not video_url:
                    video_url = info.get('url')
                
                if video_url:
                    return {
                        'success': True,
                        'url': video_url,
                        'title': info.get('title', 'Video'),
                        'duration': info.get('duration'),
                        'thumbnail': info.get('thumbnail'),
                        'platform': platform,
                        'uploader': info.get('uploader') or info.get('channel'),
                        'method': 'yt-dlp'
                    }
        except Exception as e:
            logger.warning(f"yt-dlp failed: {str(e)[:100]}")
            
            # Ek baar aur try karo with different options
            if platform == 'youtube':
                logger.info("Retrying YouTube with different method...")
                retry_result = await asyncio.to_thread(extract_youtube_special, url)
                if retry_result and retry_result.get('url'):
                    retry_result['success'] = True
                    return retry_result
        
        return {
            'success': False,
            'error': 'Unable to extract video. Video might be private or region-restricted.'
        }
        
    except Exception as e:
        logger.error(f"Extraction error: {str(e)}")
        return {
            'success': False,
            'error': f'Extraction failed: {str(e)[:200]}'
        }
    
    finally:
        if acquired:
            extraction_semaphore.release()

# ========== DOWNLOAD ENDPOINT (WITH FORCE DOWNLOAD) ==========
@app.get("/download")
async def download_video(
    request: Request,
    link: str = Query(..., description="Video URL"),
    raw: bool = Query(False, description="Return only URL"),
    download: bool = Query(False, description="Force download instead of preview")
):
    start_time = time.time()
    
    # Rate limiting
    forwarded = request.headers.get("x-forwarded-for")
    client_ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")
    
    if not check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="Rate limit exceeded. 30 requests per minute.")
    
    if not link or not link.startswith(('http://', 'https://')):
        raise HTTPException(status_code=400, detail="Invalid URL. Must start with http:// or https://")
    
    platform = detect_platform(link)
    if platform not in SUPPORTED_PLATFORMS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported platform: {platform}. Supported: {', '.join(SUPPORTED_PLATFORMS)}"
        )
    
    try:
        # Cache check
        cache_key = hashlib.md5(link.encode()).hexdigest()
        cached = get_cached(cache_key)
        
        if cached:
            cached['response_time'] = round((time.time() - start_time) * 1000, 2)
            cached['cached'] = True
            
            # Agar download=true hai toh redirect with download headers
            if download:
                return RedirectResponse(
                    url=cached['url'],
                    headers={
                        "Content-Disposition": f'attachment; filename="{cached.get("title", "video")}.mp4"'
                    }
                )
            
            if raw:
                return PlainTextResponse(content=cached['url'])
            return JSONResponse(content=cached)
        
        # Extract video
        result = await extract_all_data(link, platform)
        response_time = round((time.time() - start_time) * 1000, 2)
        
        if not result.get('success'):
            raise HTTPException(status_code=500, detail=result.get('error', 'Extraction failed'))
        
        result['response_time'] = response_time
        result['cached'] = False
        
        # Cache successful results
        set_cache(cache_key, result)
        
        logger.info(f"SUCCESS | {platform} | {result.get('method', 'unknown')} | {response_time}ms")
        
        # Agar download=true hai toh redirect with download headers
        if download:
            # Safe filename banao
            safe_title = re.sub(r'[^\w\s-]', '', result.get('title', 'video'))
            safe_title = safe_title.replace(' ', '_')[:50]
            
            return RedirectResponse(
                url=result['url'],
                headers={
                    "Content-Disposition": f'attachment; filename="{safe_title}.mp4"',
                    "Content-Type": "video/mp4"
                }
            )
        
        if raw:
            return PlainTextResponse(content=result['url'])
        
        return JSONResponse(content=result)
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Endpoint error: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

# ========== ADDITIONAL DOWNLOAD ENDPOINT WITH PROXY ==========
@app.get("/download/{video_id}")
async def download_proxy(
    video_id: str,
    title: str = "video"
):
    """Proxy download endpoint - video ko direct download karne ke liye"""
    try:
        # Cache se video URL fetch karo
        # Agar nahi milta toh error do
        return JSONResponse({
            "error": "Use main download endpoint with full URL"
        }, status_code=400)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ========== HEALTH & STATUS ==========
@app.get("/")
async def root():
    return {
        "name": "Video Rocket API",
        "version": "12.1.0",
        "status": "Production Ready",
        "supported_platforms": sorted(SUPPORTED_PLATFORMS),
        "features": {
            "download": "Use download=true to force download",
            "raw": "Use raw=true for plain URL",
            "cache": "10 minutes cache"
        },
        "example": "/download?link=https://youtu.be/VIDEO_ID&download=true"
    }

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "version": "12.1.0",
        "platforms": len(SUPPORTED_PLATFORMS)
    }

@app.get("/status")
async def status():
    active = 3 - extraction_semaphore._value
    total = CACHE_HITS + CACHE_MISSES
    return {
        "version": "12.1.0",
        "active_extractions": active,
        "cache_size": len(url_cache),
        "cache_hit_rate": f"{(CACHE_HITS / total * 100):.1f}%" if total > 0 else "0%",
        "supported_platforms": sorted(SUPPORTED_PLATFORMS)
    }
