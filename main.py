"""
🚀 VIDEO ROCKET API - v12.2
Fully Compatible with QuickReels Frontend
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
    title="QuickReels API",
    version="12.2.0",
    description="Video extraction API for QuickReels frontend"
)

# ========== CORS for your frontend ==========
ALLOWED_ORIGINS = [
    "https://quicksreels.web.app",
    "https://www.quicksreels.web.app",
    "https://quickreels-vevh.onrender.com",  # Your frontend domain
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
extraction_semaphore = asyncio.Semaphore(3)

# ========== ENHANCED YT-DLP OPTIONS ==========
YDL_OPTS_BASE = {
    'quiet': True,
    'no_warnings': True,
    'extract_flat': False,
    'ignoreerrors': False,
    'cachedir': False,
    'noplaylist': True,
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-us,en;q=0.5',
    }
}

# YouTube special options
YOUTUBE_OPTS = {
    'format': 'best[ext=mp4]/best',
    'extractor_args': {'youtube': {'skip': ['dash', 'hls']}},
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Cookie': 'CONSENT=YES+IN;',
    }
}

PLATFORM_OPTS = {
    'youtube': YOUTUBE_OPTS,
    'instagram': {'format': 'best[ext=mp4]/best'},
    'facebook': {'format': 'best[ext=mp4]/best'},
    'pinterest': {'format': 'best[ext=mp4]/best'},
    'tiktok': {'format': 'best[ext=mp4]/best'},
    'twitter': {'format': 'best[ext=mp4]/best'},
    'reddit': {'format': 'best[ext=mp4]/best'},
}

# ========== CACHE ==========
url_cache = {}
CACHE_TTL = 600
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
    if len(url_cache) > 100:
        # Remove oldest 20%
        items = sorted(url_cache.items(), key=lambda x: x[1][1])
        for k, _ in items[:20]:
            del url_cache[k]
    url_cache[key] = (data.copy(), time.time())

# ========== RATE LIMITING ==========
rate_store = {}
RATE_LIMIT = 30
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

# ========== YOUTUBE SPECIAL EXTRACTOR ==========
def extract_youtube_video(url: str) -> Optional[Dict]:
    """YouTube specific extraction with multiple fallbacks"""
    try:
        # Extract video ID
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
        
        # Method 1: Piped API (working YouTube alternative)
        piped_apis = [
            f"https://pipedapi.kavin.rocks/streams/{video_id}",
            f"https://pipedapi.adminforge.de/streams/{video_id}",
        ]
        
        for api in piped_apis:
            try:
                response = requests.get(api, timeout=8)
                if response.status_code == 200:
                    data = response.json()
                    video_streams = data.get('videoStreams', [])
                    
                    # Best quality stream chuno
                    for stream in video_streams:
                        if stream.get('quality') in ['hd', 'medium', 'high']:
                            return {
                                'url': stream.get('url'),
                                'title': data.get('title', 'YouTube Video'),
                                'duration': data.get('duration'),
                                'thumbnail': data.get('thumbnailUrl'),
                                'uploader': data.get('uploader'),
                                'platform': 'youtube',
                                'instant': False
                            }
            except:
                continue
        
        # Method 2: oEmbed (at least link milega)
        oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
        response = requests.get(oembed_url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            return {
                'url': f"https://www.youtube.com/watch?v={video_id}",
                'title': data.get('title', 'YouTube Video'),
                'thumbnail': f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
                'uploader': data.get('author_name'),
                'platform': 'youtube',
                'instant': False,
                'note': 'Video URL ready for download'
            }
            
    except Exception as e:
        logger.error(f"YouTube extractor error: {e}")
    
    return None

# ========== FORMAT SELECTION ==========
def get_best_format(formats: list) -> Optional[str]:
    if not formats:
        return None
    
    # MP4 with audio
    for f in formats:
        if f.get('ext') == 'mp4' and f.get('acodec') != 'none':
            return f.get('url')
    
    # Any video with audio
    for f in formats:
        if f.get('vcodec') != 'none' and f.get('acodec') != 'none':
            return f.get('url')
    
    # Fallback
    if formats:
        return formats[0].get('url')
    
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
        
        # YouTube special handling
        if platform == 'youtube':
            logger.info("Extracting YouTube video...")
            result = await asyncio.to_thread(extract_youtube_video, url)
            if result and result.get('url'):
                result['success'] = True
                return result
        
        # Other platforms with yt-dlp
        opts = {**YDL_OPTS_BASE}
        if platform in PLATFORM_OPTS:
            opts.update(PLATFORM_OPTS[platform])
        
        def _extract():
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)
        
        info = await asyncio.wait_for(asyncio.to_thread(_extract), timeout=30.0)
        
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
                    'instant': False
                }
        
        return {
            'success': False,
            'error': 'Could not extract video. Please check the URL.'
        }
        
    except Exception as e:
        logger.error(f"Extraction error: {e}")
        return {
            'success': False,
            'error': f'Extraction failed: {str(e)[:150]}'
        }
    
    finally:
        if acquired:
            extraction_semaphore.release()

# ========== DOWNLOAD ENDPOINT (Matches Frontend) ==========
@app.get("/download")
async def download_video(
    request: Request,
    link: str = Query(..., description="Video URL")
):
    start_time = time.time()
    
    # Rate limiting
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
            detail=f"Unsupported platform. Supported: Instagram, Facebook, YouTube, Pinterest, TikTok, Twitter, Reddit"
        )
    
    try:
        # Cache check
        cache_key = hashlib.md5(link.encode()).hexdigest()
        cached = get_cached(cache_key)
        
        if cached:
            cached['response_time'] = round((time.time() - start_time) * 1000, 2)
            cached['instant'] = True  # Important for frontend badge
            logger.info(f"CACHE HIT | {platform}")
            return JSONResponse(content=cached)
        
        # Extract fresh
        result = await extract_all_data(link, platform)
        response_time = round((time.time() - start_time) * 1000, 2)
        
        if not result.get('success'):
            raise HTTPException(status_code=500, detail=result.get('error', 'Extraction failed'))
        
        # Add required fields for frontend
        result['response_time'] = response_time
        result['instant'] = False  # First time load
        result['cached'] = False
        
        # Cache for future
        set_cache(cache_key, result)
        
        logger.info(f"SUCCESS | {platform} | {result.get('method', 'unknown')} | {response_time}ms")
        
        # Return exactly what frontend expects
        return JSONResponse(content={
            'url': result['url'],
            'title': result['title'],
            'duration': result.get('duration'),
            'thumbnail': result.get('thumbnail'),
            'platform': result['platform'],
            'uploader': result.get('uploader'),
            'response_time': response_time,
            'instant': False
        })
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Endpoint error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error. Please try again.")

# ========== HEALTH CHECK ==========
@app.get("/health")
async def health():
    return {"status": "healthy", "version": "12.2.0"}

@app.get("/")
async def root():
    return {
        "name": "QuickReels API",
        "version": "12.2.0",
        "status": "Active",
        "supported_platforms": list(SUPPORTED_PLATFORMS),
        "endpoints": {
            "/download?link=URL": "Get video URL",
            "/health": "Health check"
        }
    }
