"""
🚀 VIDEO ROCKET API - v19.0 FINAL FIXED
Fixes:
- Instagram: Now fetches video WITH audio (pre-merged mp4 formats only)
- YouTube: Working with yt-dlp only (removed broken Piped APIs)
- Pinterest: Fixed headers and format selection
- Mobile download: Proxy endpoint working
- All platforms: Better format selection logic
"""

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
import yt_dlp
import time
import asyncio
import hashlib
import logging
import os
import re
import aiohttp
from typing import Dict, Optional, List
from datetime import datetime

# ========== CONFIG ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="QuickReels API",
    version="19.0.0",
    description="Fixed Instagram audio + YouTube extraction"
)

# ========== CORS ==========
ALLOWED_ORIGINS = [
    "https://quicksreels.web.app",
    "https://www.quicksreels.web.app",
    "http://localhost:3000",
    "http://localhost:5500",
    "http://127.0.0.1:5500",
    "https://quickreels-vevh.onrender.com",
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
CACHE_TTL = 900  # 15 minutes
CACHE_HITS = 0
CACHE_MISSES = 0
MAX_CACHE_SIZE = 200

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
    if len(url_cache) >= MAX_CACHE_SIZE:
        items = sorted(url_cache.items(), key=lambda x: x[1][1])
        for k, _ in items[:MAX_CACHE_SIZE // 5]:
            del url_cache[k]
    url_cache[key] = (data.copy(), time.time())

# ========== RATE LIMITING ==========
rate_store = {}
RATE_LIMIT = 50
RATE_WINDOW = 60

def cleanup_rate_limiter():
    now = time.time()
    expired = [ip for ip, entry in rate_store.items() if now > entry['reset_at']]
    for ip in expired:
        del rate_store[ip]

def check_rate_limit(ip: str) -> bool:
    cleanup_rate_limiter()
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

# ========== HELPER FUNCTIONS ==========
def extract_youtube_id(url: str) -> Optional[str]:
    if 'youtu.be' in url:
        return url.split('/')[-1].split('?')[0]
    elif 'youtube.com/watch' in url:
        match = re.search(r'v=([^&]+)', url)
        return match.group(1) if match else None
    elif 'youtube.com/shorts' in url:
        return url.split('/')[-1].split('?')[0]
    return None

# ========== INSTAGRAM EXTRACTION (FIXED - WITH AUDIO) ==========
async def extract_instagram_video(url: str) -> Dict:
    """
    FIXED: Now fetches video WITH audio
    Uses format that selects pre-merged mp4 files only
    """
    opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'ignoreerrors': False,
        'noplaylist': True,
        # CRITICAL FIX: Only select formats that have both video and audio
        'format': 'best[ext=mp4][vcodec!=none][acodec!=none]/best[ext=mp4]/best',
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 Mobile/15E148 Safari/604.1',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-us,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
        }
    }

    try:
        logger.info(f"Extracting Instagram video from: {url}")

        def _extract():
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)

        info = await asyncio.wait_for(asyncio.to_thread(_extract), timeout=30.0)

        if not info:
            return {'success': False, 'error': 'No video info found'}

        video_url = None
        best_height = 0
        formats = info.get('formats', [])

        # Priority 1: Find format with both video+audio in mp4
        for f in formats:
            if (f.get('vcodec') not in (None, 'none') and 
                f.get('acodec') not in (None, 'none') and 
                f.get('ext') == 'mp4' and 
                f.get('url')):
                
                height = f.get('height', 0) or 0
                if height > best_height:
                    best_height = height
                    video_url = f.get('url')

        # Priority 2: Any mp4 format
        if not video_url:
            for f in formats:
                if f.get('ext') == 'mp4' and f.get('url'):
                    height = f.get('height', 0) or 0
                    if height > best_height:
                        best_height = height
                        video_url = f.get('url')

        # Priority 3: Top-level URL
        if not video_url:
            video_url = info.get('url')
            best_height = info.get('height', 0) or 0

        if not video_url:
            return {'success': False, 'error': 'No video URL found'}

        # Check if it's audio-only
        if video_url.endswith(('.mp3', '.m4a', '.aac')) or 'audio' in video_url.lower():
            return {'success': False, 'error': 'Extracted audio-only content'}

        return {
            'success': True,
            'url': video_url,
            'title': info.get('title', 'Instagram Video'),
            'duration': info.get('duration'),
            'thumbnail': info.get('thumbnail'),
            'platform': 'instagram',
            'uploader': info.get('uploader', 'Instagram User'),
            'quality': f"{best_height}p" if best_height else 'HD',
            'method': 'yt-dlp'
        }

    except asyncio.TimeoutError:
        return {'success': False, 'error': 'Extraction timeout after 30 seconds'}
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Instagram extraction error: {error_msg}", exc_info=True)
        return {'success': False, 'error': f'Instagram extraction failed: {error_msg[:100]}'}

# ========== YOUTUBE EXTRACTION (FIXED - DIRECT yt-dlp) ==========
async def extract_youtube_video(url: str, quality: str = "best") -> Dict:
    """
    FIXED: YouTube extraction using only yt-dlp
    Removed broken Piped APIs
    """
    # Quality mapping for different preferences
    quality_format_map = {
        "best": "best[ext=mp4][height<=1080][vcodec!=none][acodec!=none]/best[ext=mp4][height<=1080]/best[ext=mp4]/best",
        "high": "best[ext=mp4][height<=1080][vcodec!=none][acodec!=none]/best[ext=mp4][height<=1080]/best[ext=mp4]/best",
        "medium": "best[ext=mp4][height<=720][vcodec!=none][acodec!=none]/best[ext=mp4][height<=720]/best[ext=mp4]/best",
        "low": "best[ext=mp4][height<=480][vcodec!=none][acodec!=none]/best[ext=mp4][height<=480]/best[ext=mp4]/best",
    }
    
    opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'ignoreerrors': False,
        'noplaylist': True,
        'format': quality_format_map.get(quality, quality_format_map["best"]),
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-us,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
        }
    }
    
    try:
        logger.info(f"Extracting YouTube video from: {url}")
        
        def _extract():
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)
        
        info = await asyncio.wait_for(asyncio.to_thread(_extract), timeout=45.0)
        
        if not info:
            return {'success': False, 'error': 'No video info found'}
        
        video_url = None
        best_height = 0
        formats = info.get('formats', [])
        
        # Priority 1: Find format with both video+audio in mp4
        for f in formats:
            if (f.get('vcodec') not in (None, 'none') and 
                f.get('acodec') not in (None, 'none') and 
                f.get('ext') == 'mp4' and 
                f.get('url')):
                
                height = f.get('height', 0) or 0
                if height > best_height:
                    best_height = height
                    video_url = f.get('url')
        
        # Priority 2: Any mp4 format with video
        if not video_url:
            for f in formats:
                if f.get('ext') == 'mp4' and f.get('vcodec') not in (None, 'none') and f.get('url'):
                    height = f.get('height', 0) or 0
                    if height > best_height:
                        best_height = height
                        video_url = f.get('url')
        
        # Priority 3: Top-level URL
        if not video_url:
            video_url = info.get('url')
            best_height = info.get('height', 0) or 0
        
        if not video_url:
            return {'success': False, 'error': 'No video URL found'}
        
        return {
            'success': True,
            'url': video_url,
            'title': info.get('title', 'YouTube Video'),
            'duration': info.get('duration'),
            'thumbnail': info.get('thumbnail'),
            'platform': 'youtube',
            'uploader': info.get('uploader', 'YouTube'),
            'quality': f"{best_height}p" if best_height else quality,
            'method': 'yt-dlp'
        }
        
    except asyncio.TimeoutError:
        return {'success': False, 'error': 'YouTube extraction timeout after 45 seconds'}
    except Exception as e:
        error_msg = str(e)
        logger.error(f"YouTube error: {error_msg}", exc_info=True)
        return {'success': False, 'error': f'YouTube extraction failed: {error_msg[:200]}'}

# ========== OTHER PLATFORMS (FIXED) ==========
async def extract_other_platform(url: str, platform: str) -> Dict:
    """
    FIXED: Better format selection for all platforms
    """
    opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'ignoreerrors': False,
        'no_cache': True,
        'noplaylist': True,
        'format': 'best[ext=mp4][vcodec!=none][acodec!=none]/best[ext=mp4]/best',
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        }
    }

    # Platform-specific headers
    if platform == 'tiktok':
        opts['http_headers']['User-Agent'] = 'TikTok 26.2.0 rv:262018 (iPhone; iOS 14.4.2; en_US) Cronet'
        opts['format'] = 'best[ext=mp4]/best'
        
    elif platform == 'pinterest':
        opts['http_headers'] = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': 'https://www.pinterest.com/',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        }
        
    elif platform == 'facebook':
        opts['format'] = 'best[ext=mp4][vcodec!=none]/best[ext=mp4]/best'
        
    elif platform == 'twitter':
        opts['format'] = 'best[ext=mp4]/best'
        
    elif platform == 'reddit':
        opts['format'] = 'best[ext=mp4]/best'

    try:
        logger.info(f"Extracting {platform} video from: {url}")

        def _extract():
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)

        info = await asyncio.wait_for(asyncio.to_thread(_extract), timeout=30.0)

        if not info:
            return {'success': False, 'error': 'No video info found'}

        video_url = None
        best_height = 0
        formats = info.get('formats', [])

        # Find best format with video+audio
        for f in formats:
            if f.get('vcodec') not in (None, 'none') and f.get('url'):
                # Prefer formats with audio
                has_audio = f.get('acodec') not in (None, 'none')
                height = f.get('height', 0) or 0
                
                # Give priority to formats with audio
                if has_audio and height >= best_height:
                    best_height = height
                    video_url = f.get('url')
                elif not video_url and height >= best_height:
                    best_height = height
                    video_url = f.get('url')

        # Fallback to top-level URL
        if not video_url:
            video_url = info.get('url')
            best_height = info.get('height', 0) or 0

        if not video_url:
            return {'success': False, 'error': 'No video URL found'}

        return {
            'success': True,
            'url': video_url,
            'title': info.get('title', f'{platform.capitalize()} Video'),
            'duration': info.get('duration'),
            'thumbnail': info.get('thumbnail'),
            'platform': platform,
            'uploader': info.get('uploader'),
            'quality': f"{best_height}p" if best_height else 'SD',
            'method': 'yt-dlp'
        }

    except asyncio.TimeoutError:
        return {'success': False, 'error': 'Extraction timeout'}
    except Exception as e:
        error_msg = str(e)
        logger.error(f"{platform} error: {error_msg}")
        return {'success': False, 'error': error_msg[:100]}

# ========== MAIN ENDPOINT ==========
@app.get("/download")
async def process_video(
    request: Request,
    link: str = Query(..., description="Video URL"),
    quality: str = Query("best", description="Video quality (best, high, medium, low)")
):
    start_time = time.time()

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
            detail=f"Unsupported platform. Supported: {', '.join(SUPPORTED_PLATFORMS)}"
        )

    if quality not in ['best', 'high', 'medium', 'low']:
        quality = 'best'

    try:
        cache_key = hashlib.md5(f"{link}_{quality}".encode()).hexdigest()
        cached = get_cached(cache_key)

        if cached:
            cached['response_time'] = round((time.time() - start_time) * 1000, 2)
            cached['from_cache'] = True
            return JSONResponse(content=cached)

        # Use semaphore to limit concurrent extractions
        async with extraction_semaphore:
            if platform == 'youtube':
                result = await extract_youtube_video(link, quality)
            elif platform == 'instagram':
                result = await extract_instagram_video(link)
            else:
                result = await extract_other_platform(link, platform)

        response_time = round((time.time() - start_time) * 1000, 2)

        if not result.get('success'):
            raise HTTPException(status_code=500, detail=result.get('error', 'Extraction failed'))

        response_data = {
            'url': result['url'],
            'title': result['title'],
            'duration': result.get('duration'),
            'thumbnail': result.get('thumbnail'),
            'platform': result['platform'],
            'uploader': result.get('uploader'),
            'quality': result.get('quality', quality),
            'response_time': response_time,
            'from_cache': False,
            'method': result.get('method', 'unknown'),
            'timestamp': datetime.now().isoformat()
        }

        set_cache(cache_key, response_data)
        logger.info(f"SUCCESS | {platform} | {result.get('method')} | {response_time}ms | IP: {client_ip}")

        return JSONResponse(content=response_data)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error. Please try again later.")

# ========== PROXY DOWNLOAD ENDPOINT (FIXED) ==========
@app.get("/proxy-download")
async def proxy_download(
    request: Request,
    url: str = Query(..., description="Direct video URL to proxy"),
    filename: str = Query("video.mp4", description="Download filename")
):
    forwarded = request.headers.get("x-forwarded-for")
    client_ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")

    if not check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    if not url.startswith(('http://', 'https://')):
        raise HTTPException(status_code=400, detail="Invalid URL")

    # Sanitize filename
    safe_filename = re.sub(r'[^\w\-.]', '_', filename)
    if not safe_filename.endswith('.mp4'):
        safe_filename += '.mp4'

    try:
        timeout = aiohttp.ClientTimeout(total=None, connect=10, sock_read=60)

        async def stream_video():
            async with aiohttp.ClientSession(timeout=timeout) as session:
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Referer': url.split('/')[0] + '//' + url.split('/')[2] + '/',
                }
                async with session.get(url, headers=headers) as resp:
                    if resp.status not in (200, 206):
                        raise HTTPException(status_code=502, detail="Failed to fetch video from source")
                    
                    # Stream in chunks
                    async for chunk in resp.content.iter_chunked(1024 * 64):
                        yield chunk

        return StreamingResponse(
            stream_video(),
            media_type="video/mp4",
            headers={
                "Content-Disposition": f'attachment; filename="{safe_filename}"',
                "Cache-Control": "no-cache",
                "X-Content-Type-Options": "nosniff",
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Proxy download error: {e}")
        raise HTTPException(status_code=500, detail="Failed to proxy video download")

# ========== DEDICATED INSTAGRAM ENDPOINT ==========
@app.get("/instagram")
async def download_instagram(
    request: Request,
    link: str = Query(..., description="Instagram URL")
):
    start_time = time.time()

    forwarded = request.headers.get("x-forwarded-for")
    client_ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")

    if not check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    if 'instagram.com' not in link:
        raise HTTPException(status_code=400, detail="Not an Instagram URL")

    cache_key = hashlib.md5(f"insta_{link}".encode()).hexdigest()
    cached = get_cached(cache_key)

    if cached:
        cached['response_time'] = round((time.time() - start_time) * 1000, 2)
        cached['from_cache'] = True
        return JSONResponse(content=cached)

    result = await extract_instagram_video(link)

    if not result.get('success'):
        raise HTTPException(status_code=500, detail=result.get('error'))

    response_time = round((time.time() - start_time) * 1000, 2)
    response_data = {
        **result,
        'response_time': response_time,
        'from_cache': False
    }

    set_cache(cache_key, response_data)
    return JSONResponse(content=response_data)

# ========== STATS ENDPOINT ==========
@app.get("/stats")
async def get_stats():
    return {
        "cache_hits": CACHE_HITS,
        "cache_misses": CACHE_MISSES,
        "cache_size": len(url_cache),
        "active_ips": len(rate_store),
        "cache_hit_rate": f"{(CACHE_HITS / (CACHE_HITS + CACHE_MISSES) * 100):.1f}%" if (CACHE_HITS + CACHE_MISSES) > 0 else "0%",
        "supported_platforms": list(SUPPORTED_PLATFORMS),
        "version": "19.0.0"
    }

# ========== HEALTH CHECK ==========
@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "version": "19.0.0",
        "timestamp": datetime.now().isoformat()
    }

@app.get("/")
async def root():
    return {
        "name": "QuickReels API",
        "version": "19.0.0",
        "status": "Active",
        "youtube_method": "yt-dlp direct extraction (no ffmpeg required)",
        "instagram_method": "Pre-merged mp4 formats (video + audio together)",
        "supported_platforms": list(SUPPORTED_PLATFORMS),
        "endpoints": [
            "/download?link=URL&quality=best",
            "/instagram?link=URL",
            "/proxy-download?url=DIRECT_VIDEO_URL&filename=video.mp4",
            "/stats",
            "/health"
        ],
        "quality_options": ["best", "high", "medium", "low"]
    }

# ========== ERROR HANDLERS ==========
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "error": exc.detail,
            "status_code": exc.status_code,
            "timestamp": datetime.now().isoformat()
        }
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": "Internal server error",
            "timestamp": datetime.now().isoformat()
        }
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
