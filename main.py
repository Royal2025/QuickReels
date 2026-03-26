from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
import yt_dlp
import time
import asyncio
from typing import Dict, Optional
from datetime import datetime
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Video URL Rocket API",
    version="6.0.0",
    description="Extract direct video URLs - Production Ready"
)

# CORS for any frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== CACHE SYSTEM ====================
url_cache: Dict[str, tuple] = {}
CACHE_TTL = 300
MAX_CACHE_SIZE = 100

def clean_old_cache():
    global url_cache
    if len(url_cache) > MAX_CACHE_SIZE:
        items_to_remove = int(MAX_CACHE_SIZE * 0.2)
        sorted_items = sorted(url_cache.items(), key=lambda x: x[1][1])
        for key, _ in sorted_items[:items_to_remove]:
            del url_cache[key]

def get_cached(key: str) -> Optional[Dict]:
    if key in url_cache:
        data, timestamp = url_cache[key]
        if time.time() - timestamp < CACHE_TTL:
            return data
        else:
            del url_cache[key]
    return None

def set_cache(key: str, data: Dict):
    global url_cache
    url_cache[key] = (data, time.time())
    clean_old_cache()

# ==================== PLATFORM DETECTION ====================
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

# ==================== DIRECT URL EXTRACTOR (WITH TIMEOUT) ====================
async def extract_direct_url(url: str) -> Dict:
    """Extract direct video URL with 15 second timeout"""
    
    ydl_opts = {
        'format': 'bv*[ext=mp4]+ba/b[ext=mp4]/best',
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'ignoreerrors': True,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-us,en;q=0.5',
        }
    }
    
    try:
        # ========== TIMEOUT SAFETY ==========
        def _extract():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(url, download=False)
        
        info = await asyncio.wait_for(
            asyncio.to_thread(_extract),
            timeout=15.0  # 15 second timeout - prevents hanging
        )
        
        if not info:
            return {'success': False, 'error': 'Could not extract video information'}
        
        # Safe format selection
        video_url = None
        formats = info.get('formats', [])
        
        best_format = None
        for f in formats:
            if f.get('ext') == 'mp4' and f.get('height') and f.get('acodec') != 'none':
                if not best_format or f.get('height', 0) > best_format.get('height', 0):
                    best_format = f
        
        if best_format:
            video_url = best_format.get('url')
        
        if not video_url:
            video_url = info.get('url')
        
        if not video_url and formats:
            video_url = formats[0].get('url')
        
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
        logger.error(f"Timeout extracting: {url}")
        return {'success': False, 'error': 'Request timeout (15s). Please try again.'}
    except Exception as e:
        logger.error(f"Extraction error: {e}")
        if 'instagram' in url.lower():
            return {'success': False, 'error': 'Instagram video failed. Try again or use different reel.'}
        return {'success': False, 'error': str(e)}

# ==================== API ENDPOINTS ====================
@app.get("/")
async def root():
    return {
        "name": "Video URL Rocket API",
        "version": "6.0.0",
        "status": "🚀 Production Ready",
        "features": ["Direct URL", "Timeout Safety", "Auto Cache", "Monetization Ready"],
        "endpoints": {
            "GET /url?link=...": "Get video URL",
            "GET /url?link=...&raw=true": "Plain URL only",
            "GET /info?link=...": "Get metadata"
        }
    }

@app.get("/url")
async def get_video_url(
    link: str = Query(...),
    raw: bool = Query(False)
):
    """Get direct video URL"""
    
    start_time = time.time()
    
    if not link or not link.startswith(('http://', 'https://')):
        raise HTTPException(status_code=400, detail="Invalid URL")
    
    platform = detect_platform(link)
    if platform == 'unknown':
        raise HTTPException(status_code=400, detail="Unsupported platform")
    
    # Check cache
    cache_key = f"url_{link}"
    cached = get_cached(cache_key)
    if cached:
        if raw:
            return PlainTextResponse(content=cached['url'])
        cached['cached'] = True
        cached['response_time'] = round((time.time() - start_time) * 1000, 2)
        return JSONResponse(content=cached)
    
    # Extract with timeout
    result = await extract_direct_url(link)
    
    if not result['success']:
        raise HTTPException(status_code=500, detail=result['error'])
    
    result['response_time'] = round((time.time() - start_time) * 1000, 2)
    result['cached'] = False
    
    set_cache(cache_key, result)
    
    if raw:
        return PlainTextResponse(content=result['url'])
    
    return JSONResponse(content=result)

@app.get("/info")
async def get_info(link: str = Query(...)):
    """Get video metadata"""
    
    start_time = time.time()
    
    cache_key = f"info_{link}"
    cached = get_cached(cache_key)
    if cached:
        cached['cached'] = True
        cached['response_time'] = round((time.time() - start_time) * 1000, 2)
        return JSONResponse(content=cached)
    
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'ignoreerrors': True,
        'http_headers': {'User-Agent': 'Mozilla/5.0'}
    }
    
    try:
        def _extract():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(link, download=False)
        
        info = await asyncio.wait_for(asyncio.to_thread(_extract), timeout=10.0)
        
        if not info:
            raise HTTPException(status_code=500, detail="Could not extract info")
        
        result = {
            'title': info.get('title'),
            'duration': info.get('duration'),
            'thumbnail': info.get('thumbnail'),
            'platform': detect_platform(link),
            'uploader': info.get('uploader'),
            'response_time': round((time.time() - start_time) * 1000, 2),
            'cached': False
        }
        
        set_cache(cache_key, result)
        return JSONResponse(content=result)
        
    except asyncio.TimeoutError:
        raise HTTPException(status_code=500, detail="Timeout fetching info")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health():
    return {"status": "healthy", "version": "6.0.0", "cache": len(url_cache)}

# Run: uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
