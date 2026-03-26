
"""
🚀 VIDEO ROCKET API - FINAL v10.5
Production Ready | Deploy Now
"""

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
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

app = FastAPI(title="Video Rocket API", version="10.5.0")

# ========== CORS - FIXED WITH SAFE DEFAULT ==========
PRODUCTION_DOMAINS = [
    "https://quicksreels.web.app",
    "https://www.quicksreels.web.app",
    "http://localhost:3000",
    "http://localhost:7700",
    "http://localhost:8000",
]

# ========== FIX 1: Safe ENV default ==========
ENV = os.getenv("ENV", "development")  # Default to development if not set
ALLOWED_ORIGINS = ["*"] if ENV == "development" else PRODUCTION_DOMAINS

logger.info(f"Running in {ENV} mode")
logger.info(f"CORS allowed origins: {ALLOWED_ORIGINS}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# ========== RATE LIMIT ==========
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ========== SEMAPHORE ==========
extraction_semaphore = asyncio.Semaphore(3)

# ========== YT-DLP OPTIONS - OPTIMIZED ==========
YDL_OPTS = {
    'format': 'bv*[ext=mp4]+ba/b[ext=mp4]/best',
    'quiet': True,
    'no_warnings': True,
    'extract_flat': False,
    'ignoreerrors': True,
    # ========== FIX 2: Memory optimization ==========
    'cachedir': False,      # No disk cache
    'noplaylist': True,     # Don't process playlists
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    }
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
    global url_cache
    url_cache[key] = (data.copy(), time.time())
    clean_old_cache()

# ========== PLATFORM DETECTION ==========
def detect_platform(url: str) -> str:
    u = url.lower()
    if 'instagram.com' in u and ('/reel/' in u or '/p/' in u):
        return 'instagram'
    if 'facebook.com' in u or 'fb.com' in u:
        if '/reel/' in u or '/watch/' in u:
            return 'facebook'
    if 'youtube.com' in u or 'youtu.be' in u:
        return 'youtube'
    if 'pinterest.com' in u or 'pin.it' in u:
        return 'pinterest'
    return 'unknown'

# ========== FORMAT SELECTION ==========
def get_best_format(formats: list) -> Optional[str]:
    if not formats:
        return None
    
    sorted_formats = sorted(
        [f for f in formats if f.get('height')],
        key=lambda x: x.get('height', 0),
        reverse=True
    )
    
    for f in sorted_formats:
        if f.get('ext') == 'mp4' and f.get('acodec') != 'none':
            return f.get('url')
    
    for f in sorted_formats:
        if f.get('acodec') != 'none':
            return f.get('url')
    
    if sorted_formats:
        return sorted_formats[0].get('url')
    
    return None

# ========== EXTRACTOR ==========
async def extract_all_data(url: str, retry_count: int = 0) -> Dict:
    acquired = False
    
    try:
        try:
            await asyncio.wait_for(extraction_semaphore.acquire(), timeout=2.0)
            acquired = True
        except asyncio.TimeoutError:
            return {
                'success': False, 
                'error': 'Server busy. Please retry in few seconds.',
                'busy': True
            }
        
        def _extract():
            with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
                return ydl.extract_info(url, download=False)
        
        info = await asyncio.wait_for(
            asyncio.to_thread(_extract),
            timeout=25.0
        )
        
        if not info:
            return {'success': False, 'error': 'Could not extract video information'}
        
        video_url = get_best_format(info.get('formats', []))
        
        if not video_url:
            video_url = info.get('url')
        
        if not video_url:
            return {'success': False, 'error': 'No video URL found'}
        
        return {
            'success': True,
            'url': video_url,
            'title': info.get('title', 'video'),
            'duration': info.get('duration'),
            'thumbnail': info.get('thumbnail'),
            'platform': detect_platform(url),
            'uploader': info.get('uploader')
        }
        
    except asyncio.TimeoutError:
        if retry_count < 1:
            logger.info(f"Retry: {url[:40]}...")
            await asyncio.sleep(1)
            return await extract_all_data(url, retry_count + 1)
        return {'success': False, 'error': 'Timeout (25s). Please try again.'}
        
    except Exception as e:
        logger.error(f"Extraction error: {str(e)[:100]}")
        if retry_count < 1 and 'instagram' in url.lower():
            await asyncio.sleep(1)
            return await extract_all_data(url, retry_count + 1)
        return {'success': False, 'error': str(e)}
        
    finally:
        if acquired:
            extraction_semaphore.release()

# ========== MAIN ENDPOINT ==========
@app.get("/download")
@limiter.limit("20/minute")
async def download_video(
    request: Request, 
    link: str = Query(..., description="Social media video URL"),
    raw: bool = Query(False, description="Return only URL as plain text")
):
    start_time = time.time()
    
    if not link or not link.startswith(('http://', 'https://')):
        raise HTTPException(status_code=400, detail="Invalid URL format")
    
    platform = detect_platform(link)
    if platform == 'unknown':
        raise HTTPException(
            status_code=400, 
            detail="Unsupported platform. Supported: Instagram, Facebook, YouTube, Pinterest"
        )
    
    try:
        cache_key = hashlib.md5(link.encode()).hexdigest()
        cached_data = get_cached(cache_key)
        
        if cached_data:
            cached_data['response_time'] = round((time.time() - start_time) * 1000, 2)
            cached_data['instant'] = True
            cached_data['active_extractions'] = 3 - extraction_semaphore._value
            
            logger.info(f"CACHE | {platform} | {cached_data['response_time']}ms")
            
            if raw:
                return PlainTextResponse(content=cached_data['url'])
            return JSONResponse(content=cached_data)
        
        result = await extract_all_data(link)
        response_time = round((time.time() - start_time) * 1000, 2)
        
        if not result['success']:
            if result.get('busy'):
                logger.warning(f"BUSY | {platform} | {response_time}ms")
                raise HTTPException(status_code=429, detail=result['error'])
            
            logger.error(f"FAIL | {platform} | {response_time}ms")
            raise HTTPException(status_code=500, detail=result['error'])
        
        result['response_time'] = response_time
        result['active_extractions'] = 3 - extraction_semaphore._value
        result['instant'] = False
        
        logger.info(f"SUCCESS | {platform} | {response_time}ms | Active: {result['active_extractions']}")
        
        set_cache(cache_key, result)
        
        if raw:
            return PlainTextResponse(content=result['url'])
        
        return JSONResponse(content=result)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal error")

# ========== STATUS ENDPOINTS ==========
@app.get("/")
async def root():
    return {
        "name": "Video Rocket API",
        "version": "10.5.0",
        "status": "🚀 Production Ready",
        "mode": ENV,
        "cors": f"enabled for {len(ALLOWED_ORIGINS)} origins" if ALLOWED_ORIGINS != ["*"] else "enabled for all origins",
        "cache": f"{len(url_cache)}/{MAX_CACHE_SIZE}",
        "endpoints": {
            "GET /download?link=URL": "Get video data",
            "GET /download?link=URL&raw=true": "Get plain video URL",
            "GET /health": "Health check",
            "GET /status": "System status"
        }
    }

@app.get("/status")
async def get_status():
    active_extractions = 3 - extraction_semaphore._value
    return {
        "version": "10.5.0",
        "mode": ENV,
        "capacity": {
            "max_concurrent": 3,
            "active_extractions": active_extractions,
            "available_slots": extraction_semaphore._value,
            "status": "available" if extraction_semaphore._value > 0 else "busy"
        },
        "cache": {
            "size": len(url_cache),
            "max_size": MAX_CACHE_SIZE,
            "hit_rate": f"{(CACHE_HITS/(CACHE_HITS+CACHE_MISSES)*100):.1f}%" if (CACHE_HITS + CACHE_MISSES) > 0 else "0%"
        },
        "rate_limit": "20/minute"
    }

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "version": "10.5.0",
        "mode": ENV,
        "ready": extraction_semaphore._value > 0
    }

# Run: uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
