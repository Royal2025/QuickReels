"""
🚀 VIDEO ROCKET API - v18.1
Fixed Instagram video extraction + Enhanced YouTube support
"""

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import yt_dlp
import time
import asyncio
import hashlib
import logging
import os
import re
import aiohttp
import json
from typing import Dict, Optional, List
from datetime import datetime

# ========== CONFIG ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()  # Removed file handler to avoid permission issues
    ]
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="QuickReels API",
    version="18.1.0",
    description="Fixed Instagram video extraction with enhanced platform support"
)

# ========== CORS ==========
ALLOWED_ORIGINS = [
    "https://quicksreels.web.app",
    "https://www.quicksreels.web.app",
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
        # Remove oldest 20% entries
        items = sorted(url_cache.items(), key=lambda x: x[1][1])
        for k, _ in items[:MAX_CACHE_SIZE // 5]:
            del url_cache[k]
    url_cache[key] = (data.copy(), time.time())

# ========== RATE LIMITING ==========
rate_store = {}
RATE_LIMIT = 50
RATE_WINDOW = 60

def cleanup_rate_limiter():
    """Clean up expired rate limit entries"""
    now = time.time()
    expired = [ip for ip, entry in rate_store.items() if now > entry['reset_at']]
    for ip in expired:
        del rate_store[ip]

def check_rate_limit(ip: str) -> bool:
    """Check if IP is within rate limits"""
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
    """Detect platform from URL"""
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
    """Extract YouTube video ID from URL"""
    if 'youtu.be' in url:
        return url.split('/')[-1].split('?')[0]
    elif 'youtube.com/watch' in url:
        match = re.search(r'v=([^&]+)', url)
        return match.group(1) if match else None
    elif 'youtube.com/shorts' in url:
        return url.split('/')[-1].split('?')[0]
    return None

def get_best_youtube_stream(data: Dict, quality: str = "best") -> Optional[str]:
    """Get best quality video stream from Piped API response"""
    video_streams = data.get('videoStreams', [])
    
    if not video_streams:
        return None
    
    # Quality priority
    quality_order = {
        "best": ["2160", "1440", "1080", "720", "480", "360"],
        "high": ["1080", "720"],
        "medium": ["720", "480"],
        "low": ["480", "360"]
    }
    
    priorities = quality_order.get(quality, quality_order["best"])
    
    # Try to find stream with preferred quality
    for priority in priorities:
        for stream in video_streams:
            quality_label = str(stream.get('quality', ''))
            if priority in quality_label or quality_label == priority:
                if stream.get('url'):
                    return stream.get('url')
    
    # Fallback: first valid stream
    for stream in video_streams:
        if stream.get('url'):
            return stream.get('url')
    
    return None

# ========== INSTAGRAM EXTRACTION (FIXED) ==========
async def extract_instagram_video(url: str) -> Dict:
    """Improved Instagram extraction that ensures video (not audio)"""
    opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'ignoreerrors': False,
        'noplaylist': True,
        'format': 'bestvideo+bestaudio/best',  # Force video+audio combo
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 Mobile/15E148 Safari/604.1',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-us,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
        }
    }
    
    # Try to get cookies if available
    cookie_file = get_instagram_cookies()
    if cookie_file:
        opts['cookiefile'] = cookie_file
    
    try:
        logger.info(f"Extracting Instagram video from: {url}")
        
        def _extract():
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)
        
        info = await asyncio.wait_for(asyncio.to_thread(_extract), timeout=30.0)
        
        if not info:
            return {'success': False, 'error': 'No video info found'}
        
        # Get all formats
        formats = info.get('formats', [])
        
        # Filter for video formats with video codec
        video_formats = []
        for f in formats:
            vcodec = f.get('vcodec', 'none')
            ext = f.get('ext', '')
            
            # Check if it's a video format (has video codec)
            if vcodec != 'none':
                video_formats.append(f)
                logger.debug(f"Found video format: {f.get('format_id')} - {ext} - {f.get('height')}p")
        
        if not video_formats:
            logger.error("No video formats found in Instagram response")
            return {'success': False, 'error': 'No video format available'}
        
        # Sort by quality (height) descending
        video_formats.sort(key=lambda x: x.get('height', 0) or 0, reverse=True)
        
        # Get best video format that has both video and audio
        best_format = None
        for f in video_formats:
            if f.get('acodec') != 'none' and f.get('vcodec') != 'none':
                best_format = f
                break
        
        # If no combined format, use best video-only format
        if not best_format:
            best_format = video_formats[0]
            logger.warning("No video+audio format found, using video-only format")
        
        video_url = best_format.get('url')
        if not video_url:
            video_url = info.get('url')
        
        if not video_url:
            return {'success': False, 'error': 'No video URL found'}
        
        # Validate it's not an audio file
        if video_url.endswith(('.mp3', '.m4a', '.aac')) or 'audio' in video_url.lower():
            logger.error(f"Got audio URL: {video_url}")
            return {'success': False, 'error': 'Extracted audio-only content'}
        
        logger.info(f"Successfully extracted Instagram video: {best_format.get('height', 'unknown')}p")
        
        return {
            'success': True,
            'url': video_url,
            'title': info.get('title', 'Instagram Video'),
            'duration': info.get('duration'),
            'thumbnail': info.get('thumbnail'),
            'platform': 'instagram',
            'uploader': info.get('uploader', 'Instagram User'),
            'quality': f"{best_format.get('height', 'unknown')}p",
            'method': 'yt-dlp'
        }
        
    except asyncio.TimeoutError:
        logger.error("Instagram extraction timeout")
        return {'success': False, 'error': 'Extraction timeout after 30 seconds'}
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Instagram extraction error: {error_msg}", exc_info=True)
        return {'success': False, 'error': f'Instagram extraction failed: {error_msg[:100]}'}

def get_instagram_cookies():
    """Get Instagram cookies file path if exists"""
    cookie_file = os.path.join(os.path.dirname(__file__), 'instagram_cookies.txt')
    if os.path.exists(cookie_file):
        logger.info("Using Instagram cookies file")
        return cookie_file
    return None

# ========== YOUTUBE VIA PIPED API (ENHANCED) ==========
async def extract_youtube_piped(url: str, quality: str = "best") -> Dict:
    """YouTube via Piped API with multiple fallbacks"""
    video_id = extract_youtube_id(url)
    
    if not video_id:
        return {'success': False, 'error': 'Invalid YouTube URL'}
    
    logger.info(f"Extracting YouTube video: {video_id}")
    
    # Piped API instances (more reliable ones)
    piped_apis = [
        f"https://pipedapi.kavin.rocks/streams/{video_id}",
        f"https://pipedapi.adminforge.de/streams/{video_id}",
        f"https://pipedapi.moomoo.me/streams/{video_id}",
        f"https://pipedapi.syncpundit.io/streams/{video_id}",
    ]
    
    # Try Piped APIs
    for api_url in piped_apis:
        try:
            logger.debug(f"Trying Piped API: {api_url}")
            
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(api_url) as response:
                    if response.status == 200:
                        data = await response.json()
                        video_url = get_best_youtube_stream(data, quality)
                        
                        if video_url:
                            logger.info(f"YouTube success via Piped: {quality}")
                            return {
                                'success': True,
                                'url': video_url,
                                'title': data.get('title', 'YouTube Video'),
                                'duration': data.get('duration'),
                                'thumbnail': data.get('thumbnailUrl'),
                                'platform': 'youtube',
                                'uploader': data.get('uploader', 'YouTube'),
                                'quality': quality,
                                'method': 'piped_api'
                            }
        except asyncio.TimeoutError:
            logger.warning(f"Piped API timeout: {api_url}")
            continue
        except Exception as e:
            logger.warning(f"Piped API {api_url} failed: {e}")
            continue
    
    # Fallback to yt-dlp if all Piped APIs fail
    logger.warning("All Piped APIs failed, falling back to yt-dlp")
    return await extract_youtube_ytdlp(url, quality)

async def extract_youtube_ytdlp(url: str, quality: str = "best") -> Dict:
    """YouTube extraction via yt-dlp (fallback)"""
    opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'ignoreerrors': False,
        'noplaylist': True,
        'format': 'bestvideo+bestaudio/best',
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        }
    }
    
    try:
        def _extract():
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)
        
        info = await asyncio.wait_for(asyncio.to_thread(_extract), timeout=25.0)
        
        if not info:
            return {'success': False, 'error': 'No video info found'}
        
        # Get best video URL based on quality
        formats = info.get('formats', [])
        video_formats = [f for f in formats if f.get('vcodec') != 'none']
        
        if not video_formats:
            return {'success': False, 'error': 'No video formats found'}
        
        # Sort by quality
        video_formats.sort(key=lambda x: x.get('height', 0) or 0, reverse=True)
        
        # Select quality
        if quality == "best":
            best_format = video_formats[0]
        elif quality == "high":
            best_format = next((f for f in video_formats if (f.get('height', 0) or 0) >= 720), video_formats[0])
        elif quality == "medium":
            best_format = next((f for f in video_formats if 480 <= (f.get('height', 0) or 0) < 720), video_formats[0])
        else:
            best_format = video_formats[-1]  # lowest quality
        
        video_url = best_format.get('url')
        
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
            'quality': f"{best_format.get('height', 'unknown')}p",
            'method': 'yt-dlp'
        }
        
    except asyncio.TimeoutError:
        logger.error("YouTube yt-dlp timeout")
        return {'success': False, 'error': 'Extraction timeout'}
    except Exception as e:
        logger.error(f"YouTube yt-dlp error: {e}")
        return {'success': False, 'error': str(e)[:100]}

# ========== OTHER PLATFORMS EXTRACTION ==========
async def extract_other_platform(url: str, platform: str) -> Dict:
    """Extract videos from other platforms"""
    
    opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'ignoreerrors': False,
        'cachedir': False,
        'noplaylist': True,
        'format': 'best[ext=mp4]/best',
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        }
    }
    
    # Platform-specific settings
    if platform == 'tiktok':
        opts['format'] = 'best[ext=mp4]/best'
        opts['http_headers']['User-Agent'] = 'TikTok 26.2.0 rv:262018 (iPhone; iOS 14.4.2; en_US) Cronet'
        
    elif platform == 'pinterest':
        opts['format'] = 'best[ext=mp4]/best'
        
    elif platform == 'facebook':
        opts['format'] = 'best[ext=mp4]/best'
        
    elif platform == 'twitter':
        opts['format'] = 'best[ext=mp4]/best'
        
    elif platform == 'reddit':
        opts['format'] = 'best[ext=mp4]/best'
    
    try:
        logger.info(f"Extracting {platform} video from: {url}")
        
        def _extract():
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)
        
        info = await asyncio.wait_for(asyncio.to_thread(_extract), timeout=25.0)
        
        if not info:
            return {'success': False, 'error': 'No video info found'}
        
        # Get video URL
        video_url = None
        formats = info.get('formats', [])
        
        # Try to find mp4 video
        for f in formats:
            if f.get('ext') == 'mp4' and f.get('vcodec') != 'none':
                video_url = f.get('url')
                if video_url:
                    break
        
        # Fallback to any video format
        if not video_url:
            for f in formats:
                if f.get('vcodec') != 'none' and f.get('url'):
                    video_url = f.get('url')
                    break
        
        # Last resort
        if not video_url:
            video_url = info.get('url')
        
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
            'method': 'yt-dlp'
        }
        
    except asyncio.TimeoutError:
        logger.error(f"{platform} extraction timeout")
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
    """Main endpoint to download videos from supported platforms"""
    start_time = time.time()
    
    # Rate limit check
    forwarded = request.headers.get("x-forwarded-for")
    client_ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")
    
    if not check_rate_limit(client_ip):
        logger.warning(f"Rate limit exceeded for IP: {client_ip}")
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again in a minute.")
    
    # Validate URL
    if not link or not link.startswith(('http://', 'https://')):
        raise HTTPException(status_code=400, detail="Invalid URL. Must start with http:// or https://")
    
    # Detect platform
    platform = detect_platform(link)
    if platform not in SUPPORTED_PLATFORMS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported platform. Supported: {', '.join(SUPPORTED_PLATFORMS)}"
        )
    
    # Validate quality parameter
    if quality not in ['best', 'high', 'medium', 'low']:
        quality = 'best'
    
    try:
        # Check cache
        cache_key = hashlib.md5(f"{link}_{quality}".encode()).hexdigest()
        cached = get_cached(cache_key)
        
        if cached:
            cached['response_time'] = round((time.time() - start_time) * 1000, 2)
            cached['from_cache'] = True
            logger.info(f"CACHE HIT | {platform} | IP: {client_ip}")
            return JSONResponse(content=cached)
        
        # Extract based on platform
        if platform == 'youtube':
            result = await extract_youtube_piped(link, quality)
        elif platform == 'instagram':
            result = await extract_instagram_video(link)
        else:
            result = await extract_other_platform(link, platform)
        
        response_time = round((time.time() - start_time) * 1000, 2)
        
        if not result.get('success'):
            logger.error(f"Extraction failed for {platform}: {result.get('error')}")
            raise HTTPException(status_code=500, detail=result.get('error', 'Extraction failed'))
        
        # Prepare response
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
        
        # Cache the result
        set_cache(cache_key, response_data)
        
        logger.info(f"SUCCESS | {platform} | {result.get('method')} | {response_time}ms | IP: {client_ip}")
        
        return JSONResponse(content=response_data)
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error. Please try again later.")

# ========== DEDICATED INSTAGRAM ENDPOINT ==========
@app.get("/instagram")
async def download_instagram(
    request: Request,
    link: str = Query(..., description="Instagram URL")
):
    """Dedicated endpoint for Instagram videos"""
    start_time = time.time()
    
    # Rate limit check
    forwarded = request.headers.get("x-forwarded-for")
    client_ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")
    
    if not check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    
    if 'instagram.com' not in link:
        raise HTTPException(status_code=400, detail="Not an Instagram URL")
    
    # Check cache
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
    """Get API statistics"""
    return {
        "cache_hits": CACHE_HITS,
        "cache_misses": CACHE_MISSES,
        "cache_size": len(url_cache),
        "active_ips": len(rate_store),
        "cache_hit_rate": f"{(CACHE_HITS / (CACHE_HITS + CACHE_MISSES) * 100):.1f}%" if (CACHE_HITS + CACHE_MISSES) > 0 else "0%",
        "supported_platforms": list(SUPPORTED_PLATFORMS),
        "version": "18.1.0"
    }

# ========== HEALTH CHECK ==========
@app.get("/health")
async def health():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "version": "18.1.0",
        "timestamp": datetime.now().isoformat()
    }

@app.get("/")
async def root():
    """Root endpoint with API info"""
    return {
        "name": "QuickReels API",
        "version": "18.1.0",
        "status": "Active",
        "youtube_method": "Piped API + yt-dlp fallback",
        "instagram_fix": "Video-only extraction (no audio)",
        "supported_platforms": list(SUPPORTED_PLATFORMS),
        "endpoints": [
            "/download?link=URL&quality=best",
            "/instagram?link=URL",
            "/stats",
            "/health"
        ],
        "quality_options": ["best", "high", "medium", "low"]
    }

# ========== ERROR HANDLERS ==========
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Custom HTTP exception handler"""
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
    """General exception handler"""
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
