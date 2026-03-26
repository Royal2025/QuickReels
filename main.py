"""
🚀 VIDEO ROCKET API - v16.1
YouTube: Streaming URL | Others: Direct URL
Frontend handles download, backend only gives URL
"""

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
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
    title="QuickReels API",
    version="16.1.0",
    description="Returns video URL only - Frontend handles download"
)

# ========== CORS ==========
ALLOWED_ORIGINS = [
    "https://quicksreels.web.app",
    "https://www.quicksreels.web.app",
    "http://localhost:3000",
    "http://localhost:5500",
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
CACHE_TTL = 900
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
    if len(url_cache) > 200:
        items = sorted(url_cache.items(), key=lambda x: x[1][1])
        for k, _ in items[:40]:
            del url_cache[k]
    url_cache[key] = (data.copy(), time.time())

# ========== RATE LIMITING ==========
rate_store = {}
RATE_LIMIT = 50
RATE_WINDOW = 60

def check_rate_limit(ip: str) -> bool:
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

# ========== YOUTUBE: STREAMING URL (Frontend download karega) ==========
async def get_youtube_streaming_url(url: str) -> Dict:
    """YouTube ka streaming URL do - Frontend fetch karke save karega"""
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
            return {'success': False, 'error': 'Invalid YouTube URL'}
        
        # Piped API se streaming URL lo
        piped_apis = [
            f"https://pipedapi.kavin.rocks/streams/{video_id}",
            f"https://pipedapi.adminforge.de/streams/{video_id}",
        ]
        
        for api in piped_apis:
            try:
                response = requests.get(api, timeout=10)
                if response.status_code == 200:
                    data = response.json()
                    video_streams = data.get('videoStreams', [])
                    
                    # Best quality chuno
                    for stream in video_streams:
                        quality = stream.get('quality', '')
                        if quality in ['hd', 'medium', 'high', '720', '480', '360']:
                            return {
                                'success': True,
                                'url': stream.get('url'),
                                'title': data.get('title', 'YouTube Video'),
                                'duration': data.get('duration'),
                                'thumbnail': data.get('thumbnailUrl'),
                                'platform': 'youtube',
                                'method': 'piped_api',
                                'note': 'Frontend will fetch and save this URL'
                            }
                    break
            except:
                continue
        
        # Fallback: yt-dlp se streaming URL
        opts = {
            'quiet': True,
            'no_warnings': True,
            'format': 'best[ext=mp4]/best',
            'extract_flat': True,
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            }
        }
        
        def _extract():
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)
        
        info = await asyncio.wait_for(asyncio.to_thread(_extract), timeout=20.0)
        
        if info:
            formats = info.get('formats', [])
            for f in formats:
                if f.get('ext') == 'mp4' and f.get('acodec') != 'none':
                    return {
                        'success': True,
                        'url': f.get('url'),
                        'title': info.get('title', 'YouTube Video'),
                        'duration': info.get('duration'),
                        'thumbnail': info.get('thumbnail'),
                        'platform': 'youtube',
                        'method': 'yt-dlp',
                        'note': 'Frontend will fetch and save this URL'
                    }
        
        return {'success': False, 'error': 'Could not get YouTube streaming URL'}
        
    except Exception as e:
        logger.error(f"YouTube error: {e}")
        return {'success': False, 'error': str(e)[:100]}

# ========== OTHER PLATFORMS: DIRECT URL (Frontend download karega) ==========
async def get_direct_url(url: str, platform: str) -> Dict:
    """Instagram, Facebook, Pinterest etc. ka direct URL"""
    
    opts = {
        'quiet': True,
        'no_warnings': True,
        'format': 'best',  # Simple best
        'extract_flat': False,
        'ignoreerrors': False,
        'cachedir': False,
        'noplaylist': True,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        }
    }
    
    # Platform-specific headers
    if platform == 'instagram':
        opts['http_headers']['User-Agent'] = 'Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15'
    elif platform == 'tiktok':
        opts['http_headers']['User-Agent'] = 'TikTok 26.2.0 rv:262018 (iPhone; iOS 14.4.2; en_US) Cronet'
    
    def _extract():
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
    
    try:
        info = await asyncio.wait_for(asyncio.to_thread(_extract), timeout=25.0)
        
        if not info:
            return {'success': False, 'error': 'No video info found'}
        
        # Direct URL nikal lo
        video_url = None
        formats = info.get('formats', [])
        
        for f in formats:
            if f.get('url'):
                video_url = f.get('url')
                break
        
        if not video_url:
            video_url = info.get('url')
        
        if not video_url:
            return {'success': False, 'error': 'No video URL found'}
        
        return {
            'success': True,
            'url': video_url,
            'title': info.get('title', f'{platform} Video'),
            'duration': info.get('duration'),
            'thumbnail': info.get('thumbnail'),
            'platform': platform,
            'uploader': info.get('uploader'),
            'method': 'yt-dlp',
            'note': 'Frontend will download this URL'
        }
        
    except Exception as e:
        error_msg = str(e)
        logger.error(f"{platform} error: {error_msg}")
        return {'success': False, 'error': error_msg[:100]}

# ========== MAIN ENDPOINT ==========
@app.get("/download")
async def process_video(
    request: Request,
    link: str = Query(..., description="Video URL")
):
    start_time = time.time()
    
    # Rate limit
    forwarded = request.headers.get("x-forwarded-for")
    client_ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")
    
    if not check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again in a minute.")
    
    if not link or not link.startswith(('http://', 'https://')):
        raise HTTPException(status_code=400, detail="Invalid URL")
    
    platform = detect_platform(link)
    if platform not in SUPPORTED_PLATFORMS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported platform. Supported: {', '.join(SUPPORTED_PLATFORMS)}"
        )
    
    try:
        # Cache check
        cache_key = hashlib.md5(link.encode()).hexdigest()
        cached = get_cached(cache_key)
        
        if cached:
            cached['response_time'] = round((time.time() - start_time) * 1000, 2)
            cached['instant'] = True
            logger.info(f"CACHE | {platform}")
            return JSONResponse(content=cached)
        
        # Extract based on platform
        if platform == 'youtube':
            result = await get_youtube_streaming_url(link)
        else:
            result = await get_direct_url(link, platform)
        
        response_time = round((time.time() - start_time) * 1000, 2)
        
        if not result.get('success'):
            raise HTTPException(status_code=500, detail=result.get('error', 'Extraction failed'))
        
        # Response data
        response_data = {
            'url': result['url'],
            'title': result['title'],
            'duration': result.get('duration'),
            'thumbnail': result.get('thumbnail'),
            'platform': result['platform'],
            'uploader': result.get('uploader'),
            'response_time': response_time,
            'instant': False,
            'method': result.get('method', 'unknown'),
            'note': 'Frontend: Use fetch + blob to save to device storage'
        }
        
        # Cache it
        set_cache(cache_key, response_data)
        
        logger.info(f"SUCCESS | {platform} | {result.get('method')} | {response_time}ms")
        
        return JSONResponse(content=response_data)
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Endpoint error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

# ========== HEALTH ==========
@app.get("/health")
async def health():
    return {"status": "healthy", "version": "16.1.0"}

@app.get("/")
async def root():
    try:
        yt_version = yt_dlp.version.__version__
    except:
        yt_version = "unknown"
    
    return {
        "name": "QuickReels API",
        "version": "16.1.0",
        "yt_dlp_version": yt_version,
        "status": "Active",
        "supported_platforms": list(SUPPORTED_PLATFORMS),
        "how_it_works": {
            "youtube": "Returns streaming URL - Frontend fetches and saves",
            "others": "Returns direct URL - Frontend downloads instantly"
        }
    }
