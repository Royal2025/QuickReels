"""
🚀 VIDEO ROCKET API - FINAL v11.2
Production Ready | Full Platform Support + Fallback System
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
import re
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
    version="11.2.0",
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

logger.info(f"Running in {ENV} mode")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# ========== CONCURRENCY ==========
extraction_semaphore = asyncio.Semaphore(3)

# ========== YT-DLP OPTIONS ==========
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

PLATFORM_OPTS: Dict[str, dict] = {
    'youtube': {'format': 'best[ext=mp4]/best'},
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

# ========== RATE LIMITING ==========
_rate_store: Dict[str, dict] = {}
RATE_LIMIT = 20
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

# ========== SIMPLE FALLBACK ==========
def simple_fallback(url: str, platform: str) -> Optional[Dict]:
    """Simple fallback methods that don't require heavy dependencies"""
    try:
        if platform == 'youtube':
            # Try to get video ID
            video_id = None
            if 'youtu.be' in url:
                video_id = url.split('/')[-1].split('?')[0]
            elif 'youtube.com/watch' in url:
                match = re.search(r'v=([^&]+)', url)
                if match:
                    video_id = match.group(1)
            
            if video_id:
                # Use YouTube's oEmbed API
                oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
                response = requests.get(oembed_url, timeout=10)
                if response.status_code == 200:
                    data = response.json()
                    return {
                        'url': f"https://www.youtube.com/watch?v={video_id}",
                        'title': data.get('title', 'YouTube Video'),
                        'thumbnail': data.get('thumbnail_url'),
                        'platform': platform,
                        'uploader': data.get('author_name')
                    }
        
        elif platform == 'instagram':
            # Simple Instagram fallback
            headers = {
                'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X)'
            }
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                # Look for video URLs
                video_match = re.search(r'"video_url":"([^"]+)"', response.text)
                if video_match:
                    video_url = video_match.group(1).replace('\\u0026', '&')
                    return {
                        'url': video_url,
                        'title': 'Instagram Video',
                        'platform': platform,
                        'method': 'fallback'
                    }
        
        elif platform == 'tiktok':
            # TikTok oEmbed fallback
            embed_url = f"https://www.tiktok.com/oembed?url={urllib.parse.quote(url)}"
            response = requests.get(embed_url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                # Extract video from embed HTML
                html = data.get('html', '')
                video_match = re.search(r'src="([^"]+\.mp4[^"]*)"', html)
                if video_match:
                    return {
                        'url': video_match.group(1),
                        'title': data.get('title', 'TikTok Video'),
                        'thumbnail': data.get('thumbnail_url'),
                        'platform': platform,
                        'uploader': data.get('author_name'),
                        'method': 'fallback'
                    }
        
        elif platform == 'twitter':
            # Twitter oEmbed fallback
            embed_url = f"https://publish.twitter.com/oembed?url={urllib.parse.quote(url)}"
            response = requests.get(embed_url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                html = data.get('html', '')
                video_match = re.search(r'https://video\.twimg\.com/[^"]+\.mp4', html)
                if video_match:
                    return {
                        'url': video_match.group(0),
                        'title': data.get('title', 'Twitter Video'),
                        'platform': platform,
                        'uploader': data.get('author_name'),
                        'method': 'fallback'
                    }
    
    except Exception as e:
        logger.warning(f"Fallback failed: {e}")
    
    return None

# ========== FORMAT SELECTION ==========
def get_best_format(formats: list) -> Optional[str]:
    if not formats:
        return None
    
    for f in formats:
        if f.get('ext') == 'mp4' and f.get('acodec') not in (None, 'none'):
            return f.get('url')
    
    for f in formats:
        if f.get('acodec') not in (None, 'none') and f.get('url'):
            return f.get('url')
    
    if formats:
        return formats[0].get('url')
    
    return None

# ========== MAIN EXTRACTOR ==========
async def extract_all_data(url: str, platform: str, retry_count: int = 0) -> Dict:
    acquired = False
    
    try:
        try:
            await asyncio.wait_for(extraction_semaphore.acquire(), timeout=2.0)
            acquired = True
        except asyncio.TimeoutError:
            return {
                'success': False,
                'error': 'Server busy. Please retry.',
                'busy': True
            }
        
        # Build options
        opts = {**YDL_OPTS_BASE}
        if platform in PLATFORM_OPTS:
            opts.update(PLATFORM_OPTS[platform])
        
        def _extract():
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)
        
        info = await asyncio.wait_for(
            asyncio.to_thread(_extract),
            timeout=30.0
        )
        
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
                    'uploader': info.get('uploader'),
                    'method': 'yt-dlp'
                }
    
    except Exception as e:
        logger.warning(f"yt-dlp failed: {str(e)[:100]}")
        
        # Try fallback
        if retry_count < 1:
            logger.info(f"Trying fallback for {platform}")
            fallback_result = await asyncio.to_thread(simple_fallback, url, platform)
            
            if fallback_result and fallback_result.get('url'):
                fallback_result['success'] = True
                fallback_result['method'] = 'fallback'
                logger.info(f"Fallback successful for {platform}")
                return fallback_result
    
    finally:
        if acquired:
            extraction_semaphore.release()
    
    return {
        'success': False,
        'error': 'Unable to fetch video. It may be private or removed.'
    }

# ========== ENDPOINTS ==========
@app.get("/download")
async def download_video(
    request: Request,
    link: str = Query(..., description="Video URL"),
    raw: bool = Query(False, description="Return only URL")
):
    start_time = time.time()
    
    # Rate limiting
    forwarded = request.headers.get("x-forwarded-for")
    client_ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")
    
    if not check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    
    if not link or not link.startswith(('http://', 'https://')):
        raise HTTPException(status_code=400, detail="Invalid URL")
    
    platform = detect_platform(link)
    if platform not in SUPPORTED_PLATFORMS:
        raise HTTPException(
            status_code=400,
            detail="Unsupported platform. Supported: Instagram, Facebook, YouTube, Pinterest, TikTok, Twitter/X, Reddit"
        )
    
    try:
        # Cache check
        cache_key = hashlib.md5(link.encode()).hexdigest()
        cached = get_cached(cache_key)
        
        if cached:
            cached['response_time'] = round((time.time() - start_time) * 1000, 2)
            if raw:
                return PlainTextResponse(content=cached['url'])
            return JSONResponse(content=cached)
        
        # Extract
        result = await extract_all_data(link, platform)
        response_time = round((time.time() - start_time) * 1000, 2)
        
        if not result['success']:
            raise HTTPException(status_code=500, detail=result.get('error', 'Extraction failed'))
        
        result['response_time'] = response_time
        result['instant'] = False
        
        set_cache(cache_key, result)
        
        if raw:
            return PlainTextResponse(content=result['url'])
        
        return JSONResponse(content=result)
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/")
async def root():
    return {
        "name": "Video Rocket API",
        "version": "11.2.0",
        "status": "Live",
        "supported_platforms": sorted(SUPPORTED_PLATFORMS)
    }

@app.get("/health")
async def health():
    return {"status": "healthy", "version": "11.2.0"}
