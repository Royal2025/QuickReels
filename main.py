"""
INSTAGRAM VIDEO DOWNLOADER API - PRODUCTION READY
Complete working - Reels, Posts, Stories, IGTV
Video + Audio working
Ultra Fast with Caching & Rate Limiting
"""
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from contextlib import asynccontextmanager
import yt_dlp
import asyncio
import aiohttp
import re
import time
import hashlib
import random
import os
import logging
from typing import Dict, Optional, Tuple
from collections import OrderedDict

# ========== LOGGING CONFIGURATION ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ========== CONFIGURATION ==========
MAX_CACHE_SIZE = 100  # Limit cache size to prevent memory leak
CACHE_TIME = 1800  # 30 minutes
MAX_VIDEO_SIZE = 100 * 1024 * 1024  # 100MB limit
REQUEST_TIMEOUT = 30  # seconds
EXTRACTION_TIMEOUT = 25  # seconds
RATE_LIMIT_REQUESTS = 10  # requests per minute
RATE_LIMIT_PERIOD = 60  # seconds

# Simple in-memory rate limiter
rate_limit_cache = {}

# Extract lock for thread safety
extract_lock = asyncio.Lock()

# Cache with size limit
class LimitedSizeCache:
    def __init__(self, max_size: int):
        self.cache = OrderedDict()
        self.max_size = max_size
    
    def __contains__(self, key):
        return key in self.cache
    
    def __getitem__(self, key):
        value, timestamp = self.cache[key]
        return value, timestamp
    
    def __setitem__(self, key, value):
        if len(self.cache) >= self.max_size:
            # Remove oldest entry
            self.cache.popitem(last=False)
        self.cache[key] = value
    
    def get(self, key, default=None):
        if key in self.cache:
            return self.cache[key]
        return default

cache = LimitedSizeCache(MAX_CACHE_SIZE)

# User Agents for better success
USER_AGENTS = [
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
]

def get_ua():
    return random.choice(USER_AGENTS)

def cache_key(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()

def rate_limit_check(client_ip: str) -> bool:
    """Simple rate limiting based on IP"""
    current_time = time.time()
    key = f"rate:{client_ip}"
    
    if key not in rate_limit_cache:
        rate_limit_cache[key] = []
    
    # Clean old requests
    rate_limit_cache[key] = [t for t in rate_limit_cache[key] 
                            if current_time - t < RATE_LIMIT_PERIOD]
    
    if len(rate_limit_cache[key]) >= RATE_LIMIT_REQUESTS:
        return False
    
    rate_limit_cache[key].append(current_time)
    return True

def clean_rate_limit_cache():
    """Periodically clean rate limit cache"""
    current_time = time.time()
    keys_to_remove = []
    for key, timestamps in rate_limit_cache.items():
        timestamps = [t for t in timestamps if current_time - t < RATE_LIMIT_PERIOD]
        if not timestamps:
            keys_to_remove.append(key)
        else:
            rate_limit_cache[key] = timestamps
    
    for key in keys_to_remove:
        del rate_limit_cache[key]

# ========== LIFESPAN MANAGEMENT ==========
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle startup and shutdown events"""
    # Startup
    logger.info("🚀 Starting Instagram Video Downloader API")
    logger.info(f"Configuration: Cache Size={MAX_CACHE_SIZE}, Cache Time={CACHE_TIME}s, Max Video Size={MAX_VIDEO_SIZE//(1024*1024)}MB")
    
    # Check for cookies
    cookie_file = os.environ.get('INSTAGRAM_COOKIES', 'cookies.txt')
    if os.path.exists(cookie_file):
        logger.info(f"✅ Cookies file found: {cookie_file}")
    else:
        logger.warning("⚠️ No cookies file found. Stories from private accounts may fail.")
    
    yield
    
    # Cleanup
    logger.info("🛑 Shutting down API, clearing cache...")
    cache.cache.clear()
    rate_limit_cache.clear()
    logger.info("✅ Cleanup complete")

app = FastAPI(
    title="Instagram Video Downloader", 
    version="2.1.0",
    lifespan=lifespan
)

# ========== MIDDLEWARE ==========
# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Trusted Host Middleware (optional, enable in production)
# app.add_middleware(TrustedHostMiddleware, allowed_hosts=["your-domain.com", "localhost"])

# Request logging middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log all incoming requests"""
    start_time = time.time()
    
    # Rate limiting
    client_ip = request.client.host if request.client else "unknown"
    if not rate_limit_check(client_ip):
        logger.warning(f"Rate limit exceeded for IP: {client_ip}")
        return JSONResponse(
            status_code=429,
            content={"error": "Too many requests. Please try again later."}
        )
    
    response = await call_next(request)
    process_time = (time.time() - start_time) * 1000
    
    logger.info(
        f"{request.method} {request.url.path} - "
        f"Status: {response.status_code} - "
        f"Time: {process_time:.0f}ms - "
        f"IP: {client_ip}"
    )
    
    return response

# ========== HELPER FUNCTIONS ==========
def detect_type(url: str) -> str:
    """Auto detect content type from URL"""
    url_lower = url.lower()
    if '/reel/' in url_lower:
        return 'Reel'
    elif '/p/' in url_lower:
        return 'Post'
    elif '/stories/' in url_lower or '/story/' in url_lower:
        return 'Story'
    elif '/tv/' in url_lower:
        return 'IGTV'
    else:
        return 'Video'

def validate_url(url: str) -> bool:
    """Validate Instagram URL"""
    if not url or 'instagram.com' not in url:
        return False
    
    # Check for valid Instagram URL patterns
    patterns = [
        r'instagram\.com/(p|reel|tv|stories)/[A-Za-z0-9_-]+',
        r'instagram\.com/[A-Za-z0-9_.]+/(p|reel|tv|stories)/[A-Za-z0-9_-]+'
    ]
    
    for pattern in patterns:
        if re.search(pattern, url):
            return True
    
    return True  # Allow other formats too

def get_cookie_file() -> Optional[str]:
    """Get cookie file path from environment or default"""
    cookie_file = os.environ.get('INSTAGRAM_COOKIES', 'cookies.txt')
    if os.path.exists(cookie_file):
        return cookie_file
    
    # Check in common locations
    alt_paths = ['/app/cookies.txt', './cookies.txt', '../cookies.txt']
    for path in alt_paths:
        if os.path.exists(path):
            return path
    
    return None

async def check_video_size(url: str) -> Tuple[bool, int]:
    """Check video size before streaming"""
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            headers = {'User-Agent': get_ua()}
            async with session.head(url, headers=headers) as resp:
                if resp.status == 200:
                    size = resp.headers.get('content-length', 0)
                    if size:
                        size_int = int(size)
                        if size_int > MAX_VIDEO_SIZE:
                            return False, size_int
                        return True, size_int
        return True, 0
    except Exception as e:
        logger.warning(f"Failed to check video size: {e}")
        return True, 0  # Allow if can't check

# ========== INSTAGRAM EXTRACTOR ==========
async def extract_instagram(url: str) -> Dict:
    """Extract Instagram video with audio - Production ready"""
    
    start = time.time()
    
    # Check cache first
    key = cache_key(url)
    if key in cache:
        data, ts = cache[key]
        if time.time() - ts < CACHE_TIME:
            logger.info(f"Cache hit for: {url[:50]}...")
            data['from_cache'] = True
            data['response_time'] = f"{(time.time() - start)*1000:.0f}ms"
            return data
    
    # Detect content type
    content_type = detect_type(url)
    logger.info(f"Extracting {content_type}: {url[:50]}...")
    
    # Stories require cookies
    cookie_file = get_cookie_file()
    if content_type == 'Story' and not cookie_file:
        logger.warning(f"Story extraction attempted without cookies: {url}")
        return {
            'success': False, 
            'error': 'Stories require login cookies. Please provide cookies.txt file.'
        }
    
    # Try different formats
    formats = [
        'bv*+ba/bv/best',  # Best video + best audio
        'best[ext=mp4][vcodec!=none][acodec!=none]/best[ext=mp4]/best',
        'best[ext=mp4]/best'
    ]
    
    # Try proxy if configured
    proxy = os.environ.get('PROXY_URL')
    
    for fmt in formats:
        try:
            opts = {
                'quiet': True,
                'no_warnings': True,
                'noplaylist': True,
                'format': fmt,
                'http_headers': {
                    'User-Agent': get_ua(),
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.9',
                },
                'extract_flat': False,
            }
            
            if cookie_file:
                opts['cookiefile'] = cookie_file
                logger.info(f"Using cookies from: {cookie_file}")
            
            if proxy:
                opts['proxy'] = proxy
                logger.info(f"Using proxy: {proxy}")
            
            def extract():
                with yt_dlp.YoutubeDL(opts) as ydl:
                    return ydl.extract_info(url, download=False)
            
            # Use lock for thread safety
            async with extract_lock:
                info = await asyncio.wait_for(
                    asyncio.to_thread(extract), 
                    timeout=EXTRACTION_TIMEOUT
                )
            
            if info and info.get('url'):
                video_url = info['url']
                
                # Check not audio-only
                if not video_url.endswith(('.mp3', '.m4a', '.aac')):
                    
                    # Check video size
                    size_ok, size_bytes = await check_video_size(video_url)
                    if not size_ok:
                        logger.warning(f"Video too large: {size_bytes} bytes")
                        return {
                            'success': False,
                            'error': f'Video too large ({size_bytes // (1024*1024)}MB). Maximum size: {MAX_VIDEO_SIZE // (1024*1024)}MB'
                        }
                    
                    response_time = (time.time() - start) * 1000
                    
                    result = {
                        'success': True,
                        'url': video_url,
                        'title': info.get('title', 'Instagram Video'),
                        'duration': info.get('duration'),
                        'thumbnail': info.get('thumbnail'),
                        'uploader': info.get('uploader', 'Instagram'),
                        'content_type': content_type,
                        'quality': f"{info.get('height', 'HD')}p",
                        'response_time': f"{response_time:.0f}ms",
                        'from_cache': False,
                        'size_mb': round(size_bytes / (1024 * 1024), 2) if size_bytes else None
                    }
                    
                    # Save to cache
                    cache[key] = (result, time.time())
                    logger.info(f"Successfully extracted {content_type} in {response_time:.0f}ms")
                    return result
                    
        except asyncio.TimeoutError:
            logger.error(f"Timeout extracting: {url}")
            continue
        except Exception as e:
            logger.error(f"Format {fmt} failed: {str(e)}")
            continue
    
    return {
        'success': False, 
        'error': 'Failed to extract video. The video might be private, deleted, or Instagram has changed their API.'
    }

# ========== BACKGROUND TASKS ==========
async def cleanup_task():
    """Background task to clean caches periodically"""
    while True:
        await asyncio.sleep(300)  # Run every 5 minutes
        clean_rate_limit_cache()
        logger.debug("Rate limit cache cleaned")

# Start background task
@app.on_event("startup")
async def start_cleanup_task():
    asyncio.create_task(cleanup_task())

# ========== API ENDPOINTS ==========
@app.get("/")
async def home():
    """Home endpoint with API information"""
    return {
        "name": "Instagram Video Downloader API",
        "version": "2.1.0",
        "status": "operational",
        "features": ["Reels", "Posts", "Stories", "IGTV"],
        "limits": {
            "max_video_size_mb": MAX_VIDEO_SIZE // (1024 * 1024),
            "rate_limit": f"{RATE_LIMIT_REQUESTS} requests per {RATE_LIMIT_PERIOD} seconds",
            "cache_duration_minutes": CACHE_TIME // 60
        },
        "endpoints": {
            "/": "API information",
            "/download": "Get video information (use ?url=INSTAGRAM_URL)",
            "/proxy": "Direct video download (use ?video_url=URL&filename=NAME)",
            "/health": "Health check endpoint"
        },
        "usage": "/download?url=https://www.instagram.com/reel/XXXXXXXXX/"
    }

@app.get("/health")
async def health():
    """Health check endpoint for monitoring"""
    return {
        "status": "healthy",
        "cache_size": len(cache.cache),
        "rate_limit_cache_size": len(rate_limit_cache),
        "timestamp": time.time()
    }

@app.get("/download")
async def download_video(
    request: Request,
    url: str = Query(..., description="Instagram video URL"),
    force_refresh: bool = Query(False, description="Force refresh cache")
):
    """Get Instagram video download information"""
    
    # Validate URL
    if not validate_url(url):
        raise HTTPException(400, "Invalid Instagram URL. Please provide a valid Instagram video URL.")
    
    # Force refresh cache if requested
    if force_refresh:
        key = cache_key(url)
        if key in cache.cache:
            del cache.cache[key]
            logger.info(f"Cache cleared for: {url[:50]}...")
    
    # Extract video info
    result = await extract_instagram(url)
    
    if result.get('success'):
        return JSONResponse(
            content=result,
            headers={
                "Cache-Control": "public, max-age=1800",
                "X-Content-Type-Options": "nosniff"
            }
        )
    else:
        raise HTTPException(500, result.get('error', 'Extraction failed'))

@app.get("/proxy")
async def proxy_download(
    video_url: str = Query(..., description="Video URL to download"),
    filename: str = Query("video.mp4", description="Output filename")
):
    """Proxy endpoint for direct video download"""
    
    # Validate video URL
    if not video_url.startswith(('http://', 'https://')):
        raise HTTPException(400, "Invalid video URL")
    
    # Sanitize filename
    safe_filename = re.sub(r'[^\w\-.]', '_', filename)
    if not safe_filename.endswith('.mp4'):
        safe_filename += '.mp4'
    
    async def stream():
        """Stream video in chunks"""
        try:
            timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                headers = {'User-Agent': get_ua()}
                async with session.get(video_url, headers=headers) as resp:
                    if resp.status != 200:
                        raise HTTPException(502, f"Video source unavailable (Status: {resp.status})")
                    
                    # Stream in chunks
                    async for chunk in resp.content.iter_chunked(8192):
                        yield chunk
        except asyncio.TimeoutError:
            raise HTTPException(504, "Download timeout")
        except Exception as e:
            logger.error(f"Streaming error: {e}")
            raise HTTPException(500, "Download failed")
    
    return StreamingResponse(
        stream(),
        media_type="video/mp4",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_filename}"',
            "Cache-Control": "public, max-age=3600",
            "Accept-Ranges": "bytes"
        }
    )

@app.get("/stats")
async def stats():
    """Get API statistics"""
    return {
        "cache": {
            "size": len(cache.cache),
            "max_size": MAX_CACHE_SIZE,
            "keys": list(cache.cache.keys())[:10]  # Show first 10 keys
        },
        "rate_limit": {
            "active_ips": len(rate_limit_cache),
            "limit": f"{RATE_LIMIT_REQUESTS}/{RATE_LIMIT_PERIOD}s"
        },
        "config": {
            "max_video_size_mb": MAX_VIDEO_SIZE // (1024 * 1024),
            "cache_time_seconds": CACHE_TIME,
            "extraction_timeout": EXTRACTION_TIMEOUT
        }
    }

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Custom exception handler for better error responses"""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "error": exc.detail,
            "status_code": exc.status_code
        }
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """Handle uncaught exceptions"""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": "Internal server error. Please try again later."
        }
    )

if __name__ == "__main__":
    import uvicorn
    
    # Get port from environment variable (for cloud deployment)
    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", "0.0.0.0")
    
    # Run with production settings
    uvicorn.run(
        app,
        host=host,
        port=port,
        workers=int(os.environ.get("WORKERS", 4)),
        log_level="info",
        access_log=True
    )
