"""
🚀 VIDEO ROCKET API - v19.0
Direct Download from Backend | No New Tab
"""

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import yt_dlp
import time
import asyncio
import hashlib
import logging
import re
import requests
import io
from typing import Dict, Optional

# ========== CONFIG ==========
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = FastAPI(title="QuickReels API", version="19.0.0")

# ========== CORS ==========
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========== CONCURRENCY ==========
semaphore = asyncio.Semaphore(3)

# ========== CACHE ==========
cache = {}
CACHE_TTL = 900

def get_cache(key: str) -> Optional[Dict]:
    if key in cache:
        data, ts = cache[key]
        if time.time() - ts < CACHE_TTL:
            return data.copy()
        del cache[key]
    return None

def set_cache(key: str, data: Dict):
    if len(cache) > 100:
        for k in list(cache.keys())[:20]:
            del cache[k]
    cache[key] = (data.copy(), time.time())

# ========== RATE LIMIT ==========
rate_store = {}

def check_rate(ip: str) -> bool:
    now = time.time()
    entry = rate_store.get(ip)
    if not entry or now > entry['reset']:
        rate_store[ip] = {'count': 1, 'reset': now + 60}
        return True
    if entry['count'] >= 20:
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
    if 'pinterest.com' in u or 'pin.it' in u:
        return 'pinterest'
    if 'tiktok.com' in u or 'vm.tiktok.com' in u:
        return 'tiktok'
    if 'twitter.com' in u or 'x.com' in u:
        return 'twitter'
    return 'unknown'

SUPPORTED = {'instagram', 'facebook', 'pinterest', 'tiktok', 'twitter'}

# ========== VIDEO URL EXTRACTOR ==========
async def get_video_url(url: str, platform: str) -> Dict:
    """Extract video URL from platform"""
    
    opts = {
        'quiet': True,
        'no_warnings': True,
        'ignoreerrors': False,
        'cachedir': False,
        'noplaylist': True,
        'http_headers': {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    }
    
    # Platform-specific settings
    if platform == 'instagram':
        opts['format'] = 'bestvideo[ext=mp4]+bestaudio/best[ext=mp4]/best'
        opts['http_headers']['User-Agent'] = 'Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15'
    elif platform == 'facebook':
        opts['format'] = 'best[ext=mp4]/best'
    elif platform == 'pinterest':
        opts['format'] = 'best[ext=mp4]/best'
    elif platform == 'tiktok':
        opts['format'] = 'best[ext=mp4]/best'
        opts['http_headers']['User-Agent'] = 'TikTok 26.2.0 (iPhone; iOS 14.4.2)'
    elif platform == 'twitter':
        opts['format'] = 'best[ext=mp4]/best'
    
    def _extract():
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
    
    try:
        info = await asyncio.wait_for(asyncio.to_thread(_extract), timeout=25)
        
        if not info:
            return {'success': False, 'error': 'No video found'}
        
        # Get video URL
        video_url = None
        for f in info.get('formats', []):
            if f.get('url'):
                video_url = f['url']
                break
        
        if not video_url:
            video_url = info.get('url')
        
        if not video_url:
            return {'success': False, 'error': 'No URL found'}
        
        return {
            'success': True,
            'url': video_url,
            'title': info.get('title', f'{platform}_video'),
            'platform': platform
        }
    except Exception as e:
        return {'success': False, 'error': str(e)[:100]}

# ========== DOWNLOAD ENDPOINT (Direct Download) ==========
@app.get("/download")
async def download_video(
    request: Request,
    link: str = Query(..., description="Video URL")
):
    start = time.time()
    
    # Rate limit
    ip = request.headers.get("x-forwarded-for", request.client.host).split(',')[0]
    if not check_rate(ip):
        raise HTTPException(429, "Too many requests. Try again.")
    
    if not link.startswith(('http://', 'https://')):
        raise HTTPException(400, "Invalid URL")
    
    platform = detect_platform(link)
    if platform not in SUPPORTED:
        raise HTTPException(400, f"Unsupported. Supported: Instagram, Facebook, Pinterest, TikTok, Twitter")
    
    try:
        # Check cache
        cache_key = hashlib.md5(link.encode()).hexdigest()
        cached = get_cache(cache_key)
        
        if cached:
            logger.info(f"CACHE | {platform}")
            video_url = cached['url']
            filename = f"{cached['title']}.mp4"
        else:
            # Extract video URL
            async with semaphore:
                result = await get_video_url(link, platform)
            
            if not result.get('success'):
                raise HTTPException(500, result.get('error', 'Extraction failed'))
            
            video_url = result['url']
            filename = f"{result['title']}.mp4"
            
            # Cache for next time
            set_cache(cache_key, {'url': video_url, 'title': result['title']})
        
        logger.info(f"Downloading | {platform} | {round((time.time() - start) * 1000)}ms")
        
        # Fetch video and stream to user
        response = requests.get(video_url, stream=True)
        
        if response.status_code != 200:
            raise HTTPException(500, "Failed to fetch video")
        
        # Clean filename
        safe_filename = re.sub(r'[^\w\s-]', '', filename)
        safe_filename = safe_filename.replace(' ', '_')[:50]
        
        # Return streaming response (direct download)
        return StreamingResponse(
            response.iter_content(chunk_size=8192),
            media_type="video/mp4",
            headers={
                "Content-Disposition": f'attachment; filename="{safe_filename}"',
                "Content-Type": "video/mp4",
                "Content-Length": response.headers.get("content-length", "")
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error: {e}")
        raise HTTPException(500, "Download failed. Try again.")

# ========== INFO ENDPOINT (Get Video Info Without Download) ==========
@app.get("/info")
async def get_info(
    request: Request,
    link: str = Query(..., description="Video URL")
):
    """Get video info without downloading"""
    
    ip = request.headers.get("x-forwarded-for", request.client.host).split(',')[0]
    if not check_rate(ip):
        raise HTTPException(429, "Too many requests.")
    
    platform = detect_platform(link)
    if platform not in SUPPORTED:
        raise HTTPException(400, f"Unsupported platform")
    
    cache_key = hashlib.md5(link.encode()).hexdigest()
    cached = get_cache(cache_key)
    
    if cached:
        return {
            'title': cached['title'],
            'platform': platform,
            'cached': True
        }
    
    async with semaphore:
        result = await get_video_url(link, platform)
    
    if not result.get('success'):
        raise HTTPException(500, result.get('error'))
    
    set_cache(cache_key, {'url': result['url'], 'title': result['title']})
    
    return {
        'title': result['title'],
        'platform': platform,
        'cached': False
    }

# ========== HEALTH ==========
@app.get("/")
async def root():
    return {
        "name": "QuickReels API",
        "version": "19.0.0",
        "status": "Active",
        "supported": ["Instagram", "Facebook", "Pinterest", "TikTok", "Twitter"],
        "features": {
            "direct_download": "Video downloads directly to device",
            "no_new_tab": "Files save automatically",
            "cached": "Faster repeat downloads"
        }
    }

@app.get("/health")
async def health():
    return {"status": "healthy", "version": "19.0.0"}
