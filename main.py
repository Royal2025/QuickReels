"""
🚀 VIDEO ROCKET API - v14.0
Latest yt-dlp + Force Download + All Platforms Working
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
    version="14.0.0",
    description="Video extraction with latest yt-dlp"
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

# ========== LATEST YT-DLP OPTIONS (Auto-updates) ==========
def get_ytdlp_opts(platform: str = None) -> dict:
    """Latest yt-dlp options with all fixes"""
    
    # Base options for all platforms
    opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'ignoreerrors': False,
        'cachedir': False,
        'noplaylist': True,
        'prefer_ffmpeg': False,
        'format': 'best[ext=mp4]/best',
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-us,en;q=0.5',
            'Sec-Fetch-Mode': 'navigate',
        }
    }
    
    # Platform-specific tweaks
    if platform == 'youtube':
        opts['extractor_args'] = {'youtube': {'skip': ['dash', 'hls']}}
        opts['http_headers']['Cookie'] = 'CONSENT=YES+IN;'
    
    elif platform == 'instagram':
        opts['http_headers']['User-Agent'] = 'Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X)'
    
    elif platform == 'tiktok':
        opts['http_headers']['User-Agent'] = 'TikTok 26.2.0 rv:262018 (iPhone; iOS 14.4.2; en_US) Cronet'
    
    elif platform == 'facebook':
        opts['http_headers']['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    
    return opts

# ========== CACHE ==========
url_cache = {}
CACHE_TTL = 900  # 15 minutes
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

# ========== YOUTUBE FREE API FALLBACK ==========
async def extract_youtube_free_api(url: str) -> Optional[Dict]:
    """Free working YouTube API (no key needed)"""
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
        
        # Method 1: Piped API (working)
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
                    
                    for stream in video_streams:
                        if stream.get('quality') in ['hd', 'medium', 'high']:
                            return {
                                'url': stream.get('url'),
                                'title': data.get('title', 'YouTube Video'),
                                'duration': data.get('duration'),
                                'thumbnail': data.get('thumbnailUrl'),
                                'platform': 'youtube',
                                'method': 'piped_api'
                            }
            except:
                continue
        
        # Method 2: Return watch URL
        return {
            'url': f"https://www.youtube.com/watch?v={video_id}",
            'title': 'YouTube Video',
            'thumbnail': f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
            'platform': 'youtube',
            'method': 'direct_link'
        }
        
    except Exception as e:
        logger.error(f"Free API error: {e}")
        return None

# ========== MAIN EXTRACTOR WITH LATEST YT-DLP ==========
async def extract_video(url: str, platform: str) -> Dict:
    """Extract video using latest yt-dlp"""
    acquired = False
    
    try:
        # Semaphore acquire
        try:
            await asyncio.wait_for(extraction_semaphore.acquire(), timeout=3.0)
            acquired = True
        except asyncio.TimeoutError:
            return {
                'success': False,
                'error': 'Server busy. Please retry.',
                'busy': True
            }
        
        # YouTube ke liye pehle free API try karo (faster)
        if platform == 'youtube':
            free_result = await extract_youtube_free_api(url)
            if free_result and free_result.get('url'):
                free_result['success'] = True
                return free_result
        
        # Latest yt-dlp se extract karo
        opts = get_ytdlp_opts(platform)
        
        def _extract():
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)
        
        info = await asyncio.wait_for(asyncio.to_thread(_extract), timeout=35.0)
        
        if not info:
            return {'success': False, 'error': 'No video info found'}
        
        # Best video URL find karo
        video_url = None
        formats = info.get('formats', [])
        
        # MP4 with audio pehle
        for f in formats:
            if f.get('ext') == 'mp4' and f.get('acodec') != 'none':
                video_url = f.get('url')
                break
        
        # Agar nahi mila, koi bhi video with audio
        if not video_url:
            for f in formats:
                if f.get('vcodec') != 'none' and f.get('acodec') != 'none':
                    video_url = f.get('url')
                    break
        
        # Last option: sirf video
        if not video_url and formats:
            video_url = formats[0].get('url')
        
        if not video_url:
            video_url = info.get('url')
        
        if not video_url:
            return {'success': False, 'error': 'No playable URL found'}
        
        return {
            'success': True,
            'url': video_url,
            'title': info.get('title', f'{platform} Video'),
            'duration': info.get('duration'),
            'thumbnail': info.get('thumbnail'),
            'platform': platform,
            'uploader': info.get('uploader') or info.get('channel'),
            'method': 'yt-dlp'
        }
        
    except asyncio.TimeoutError:
        return {'success': False, 'error': 'Extraction timeout. Try again.'}
    except Exception as e:
        logger.error(f"Extraction error: {e}")
        return {'success': False, 'error': f'Extraction failed: {str(e)[:150]}'}
    
    finally:
        if acquired:
            extraction_semaphore.release()

# ========== MAIN DOWNLOAD ENDPOINT ==========
@app.get("/download")
async def download_video(
    request: Request,
    link: str = Query(..., description="Video URL"),
    download: bool = Query(False, description="Force download")
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
            
            # Agar download=true hai toh redirect with download headers
            if download:
                safe_title = re.sub(r'[^\w\s-]', '', cached.get('title', 'video'))
                safe_title = safe_title.replace(' ', '_')[:50]
                
                return RedirectResponse(
                    url=cached['url'],
                    headers={
                        "Content-Disposition": f'attachment; filename="{safe_title}.mp4"',
                        "Content-Type": "video/mp4"
                    }
                )
            
            return JSONResponse(content=cached)
        
        # Fresh extraction
        result = await extract_video(link, platform)
        response_time = round((time.time() - start_time) * 1000, 2)
        
        if not result.get('success'):
            raise HTTPException(status_code=500, detail=result.get('error', 'Extraction failed'))
        
        # Prepare response
        response_data = {
            'url': result['url'],
            'title': result['title'],
            'duration': result.get('duration'),
            'thumbnail': result.get('thumbnail'),
            'platform': result['platform'],
            'uploader': result.get('uploader'),
            'response_time': response_time,
            'instant': False,
            'method': result.get('method', 'yt-dlp')
        }
        
        # Cache it
        set_cache(cache_key, response_data)
        
        logger.info(f"SUCCESS | {platform} | {result.get('method')} | {response_time}ms")
        
        # Agar download=true hai toh redirect with download headers
        if download:
            safe_title = re.sub(r'[^\w\s-]', '', result.get('title', 'video'))
            safe_title = safe_title.replace(' ', '_')[:50]
            
            return RedirectResponse(
                url=result['url'],
                headers={
                    "Content-Disposition": f'attachment; filename="{safe_title}.mp4"',
                    "Content-Type": "video/mp4"
                }
            )
        
        return JSONResponse(content=response_data)
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Endpoint error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

# ========== HEALTH CHECK ==========
@app.get("/health")
async def health():
    return {"status": "healthy", "version": "14.0.0"}

@app.get("/")
async def root():
    # Check yt-dlp version
    yt_version = yt_dlp.version.__version__
    
    return {
        "name": "QuickReels API",
        "version": "14.0.0",
        "yt_dlp_version": yt_version,
        "status": "Active",
        "supported_platforms": list(SUPPORTED_PLATFORMS),
        "features": {
            "download": "Use ?download=true for force download",
            "cache": "15 minutes"
        }
    }
