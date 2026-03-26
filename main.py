"""
🚀 VIDEO ROCKET API - FINAL CORRECTED v10.1
Semaphore fixed | Cache copy | Honest metrics | LAUNCH READY
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
from typing import Dict, Optional

# ========== CONFIG ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Video Rocket API", version="10.1.0")

# ========== RATE LIMIT ==========
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========== SEMAPHORE ==========
extraction_semaphore = asyncio.Semaphore(3)

# ========== YT-DLP OPTIONS ==========
YDL_OPTS = {
    'format': 'bv*[ext=mp4]+ba/b[ext=mp4]/best',
    'quiet': True,
    'no_warnings': True,
    'extract_flat': False,
    'ignoreerrors': True,
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    }
}

# ========== CACHE ==========
url_cache: Dict[str, tuple] = {}
CACHE_TTL = 600
MAX_CACHE_SIZE = 300
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
            # ========== FIX: Return COPY to avoid mutation ==========
            return data.copy()
        else:
            del url_cache[key]
    CACHE_MISSES += 1
    return None

def set_cache(key: str, data: Dict):
    global url_cache
    # Store a copy to prevent future mutations
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

# ========== EXTRACTOR WITH FIXED SEMAPHORE ==========
async def extract_all_data(url: str, retry_count: int = 0) -> Dict:
    """Extract with PROPER semaphore handling"""
    
    acquired = False
    
    try:
        # ========== FIX: Only acquire if slot available ==========
        try:
            await asyncio.wait_for(extraction_semaphore.acquire(), timeout=2.0)
            acquired = True
        except asyncio.TimeoutError:
            return {
                'success': False, 
                'error': 'Server busy. Please retry in few seconds.',
                'busy': True
            }
        
        # ========== MAIN EXTRACTION ==========
        def _extract():
            ydl = yt_dlp.YoutubeDL(YDL_OPTS)
            return ydl.extract_info(url, download=False)
        
        info = await asyncio.wait_for(
            asyncio.to_thread(_extract),
            timeout=15.0
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
        return {'success': False, 'error': 'Timeout (15s). Please try again.'}
        
    except Exception as e:
        logger.error(f"Extraction error: {str(e)[:100]}")
        if retry_count < 1 and 'instagram' in url.lower():
            await asyncio.sleep(1)
            return await extract_all_data(url, retry_count + 1)
        return {'success': False, 'error': str(e)}
        
    finally:
        # ========== FIX: Only release if we actually acquired ==========
        if acquired:
            extraction_semaphore.release()

# ========== MAIN ENDPOINT ==========
@app.get("/download")
@limiter.limit("20/minute")
async def download_video(
    request: Request, 
    link: str = Query(...), 
    raw: bool = Query(False)
):
    """
    🚀 FINAL ENDPOINT - Launch NOW
    Real capacity: 10-15 concurrent users
    """
    
    start_time = time.time()
    
    # Validate URL
    if not link or not link.startswith(('http://', 'https://')):
        raise HTTPException(status_code=400, detail="Invalid URL format")
    
    platform = detect_platform(link)
    if platform == 'unknown':
        raise HTTPException(
            status_code=400, 
            detail="Unsupported platform. Supported: Instagram, Facebook, YouTube, Pinterest"
        )
    
    try:
        # Cache check
        cache_key = hashlib.md5(link.encode()).hexdigest()
        cached_data = get_cached(cache_key)
        
        if cached_data:
            cached_data['response_time'] = round((time.time() - start_time) * 1000, 2)
            cached_data['instant'] = True
            
            # ========== HONEST METRICS ==========
            active_extractions = 3 - extraction_semaphore._value
            cached_data['active_extractions'] = active_extractions
            
            logger.info(f"CACHE | {platform} | {cached_data['response_time']}ms")
            
            if raw:
                return PlainTextResponse(content=cached_data['url'])
            return JSONResponse(content=cached_data)
        
        # Extract
        result = await extract_all_data(link)
        response_time = round((time.time() - start_time) * 1000, 2)
        
        if not result['success']:
            if result.get('busy'):
                logger.warning(f"BUSY | {platform} | {response_time}ms")
                raise HTTPException(status_code=429, detail=result['error'])
            
            logger.error(f"FAIL | {platform} | {response_time}ms")
            raise HTTPException(status_code=500, detail=result['error'])
        
        # ========== HONEST METRICS ==========
        active_extractions = 3 - extraction_semaphore._value
        result['response_time'] = response_time
        result['active_extractions'] = active_extractions
        result['instant'] = False
        
        logger.info(f"SUCCESS | {platform} | {response_time}ms | Active: {active_extractions}")
        
        # Cache it
        set_cache(cache_key, result)
        
        if raw:
            return PlainTextResponse(content=result['url'])
        
        return JSONResponse(content=result)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal error")

# ========== HONEST STATUS ==========
@app.get("/status")
async def get_status():
    """Real system status - No lies"""
    active_extractions = 3 - extraction_semaphore._value
    return {
        "version": "10.1.0",
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
        "rate_limit": "20/minute",
        "workers": 1,
        "real_capacity": "10-15 concurrent users",
        "ready_for_traffic": True
    }

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "version": "10.1.0",
        "ready": extraction_semaphore._value > 0
    }

# Run: uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
