"""
INSTAGRAM VIDEO DOWNLOADER API - IMPROVED VERSION
Better extraction with cookies and multiple fallback methods
"""
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
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
from typing import Dict, Optional
from collections import OrderedDict

# ========== LOGGING ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ========== CONFIGURATION ==========
MAX_CACHE_SIZE = 100
CACHE_TIME = 1800
MAX_VIDEO_SIZE = 100 * 1024 * 1024
EXTRACTION_TIMEOUT = 30

# Cache
class LimitedSizeCache:
    def __init__(self, max_size: int):
        self.cache = OrderedDict()
        self.max_size = max_size
    
    def __contains__(self, key):
        return key in self.cache
    
    def __getitem__(self, key):
        return self.cache[key]
    
    def __setitem__(self, key, value):
        if len(self.cache) >= self.max_size:
            self.cache.popitem(last=False)
        self.cache[key] = value
    
    def get(self, key, default=None):
        return self.cache.get(key, default)

cache = LimitedSizeCache(MAX_CACHE_SIZE)

# User Agents
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
]

def get_ua():
    return random.choice(USER_AGENTS)

def cache_key(url):
    return hashlib.md5(url.encode()).hexdigest()

def detect_type(url: str) -> str:
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

def get_cookie_file() -> Optional[str]:
    """Get cookie file from multiple possible locations"""
    cookie_paths = [
        'cookies.txt',
        '/app/cookies.txt',
        os.path.join(os.path.dirname(__file__), 'cookies.txt'),
        os.path.expanduser('~/cookies.txt')
    ]
    
    for path in cookie_paths:
        if os.path.exists(path):
            logger.info(f"Found cookies at: {path}")
            return path
    
    logger.warning("No cookies.txt file found! Stories and private content may fail.")
    return None

# ========== LIFESPAN ==========
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Instagram Video Downloader API")
    cookie_file = get_cookie_file()
    if cookie_file:
        logger.info(f"Cookies loaded from: {cookie_file}")
    else:
        logger.warning("Running without cookies - only public content will work")
    yield
    logger.info("Shutting down...")
    cache.cache.clear()

app = FastAPI(title="Instagram Video Downloader", version="2.2.0", lifespan=lifespan)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========== INSTAGRAM EXTRACTOR ==========
async def extract_instagram(url: str) -> Dict:
    """Extract Instagram video with improved methods"""
    
    start = time.time()
    
    # Check cache
    key = cache_key(url)
    cached = cache.get(key)
    if cached and time.time() - cached[1] < CACHE_TIME:
        logger.info(f"Cache hit for: {url[:50]}...")
        cached[0]['from_cache'] = True
        cached[0]['response_time'] = f"{(time.time() - start)*1000:.0f}ms"
        return cached[0]
    
    content_type = detect_type(url)
    logger.info(f"Extracting {content_type}: {url[:80]}...")
    
    cookie_file = get_cookie_file()
    
    # Multiple format attempts with different strategies
    format_strategies = [
        'bestvideo+bestaudio/best',
        'best[ext=mp4]/best',
        'best',
    ]
    
    for strategy in format_strategies:
        try:
            opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': False,
                'format': strategy,
                'http_headers': {
                    'User-Agent': get_ua(),
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'DNT': '1',
                    'Connection': 'keep-alive',
                    'Upgrade-Insecure-Requests': '1',
                },
                'prefer_insecure': False,
                'extract_flat': False,
            }
            
            # Add cookies if available
            if cookie_file:
                opts['cookiefile'] = cookie_file
                logger.info("Using cookies for extraction")
            
            # Add proxy if configured
            proxy = os.environ.get('PROXY_URL')
            if proxy:
                opts['proxy'] = proxy
                logger.info(f"Using proxy: {proxy}")
            
            def extract():
                with yt_dlp.YoutubeDL(opts) as ydl:
                    return ydl.extract_info(url, download=False)
            
            info = await asyncio.wait_for(
                asyncio.to_thread(extract), 
                timeout=EXTRACTION_TIMEOUT
            )
            
            if info:
                # Try to get direct URL
                video_url = None
                
                # Check for direct URL
                if info.get('url'):
                    video_url = info['url']
                # Check for formats
                elif info.get('formats'):
                    # Find best video+audio format
                    for fmt in info['formats']:
                        if fmt.get('vcodec') != 'none' and fmt.get('acodec') != 'none':
                            video_url = fmt.get('url')
                            break
                    if not video_url and info['formats']:
                        video_url = info['formats'][-1].get('url')
                
                if video_url and not video_url.endswith(('.mp3', '.m4a', '.aac', '.m3u8')):
                    response_time = (time.time() - start) * 1000
                    
                    result = {
                        'success': True,
                        'url': video_url,
                        'title': info.get('title', 'Instagram Video'),
                        'duration': info.get('duration'),
                        'thumbnail': info.get('thumbnail'),
                        'uploader': info.get('uploader', info.get('channel', 'Instagram')),
                        'content_type': content_type,
                        'quality': f"{info.get('height', 'HD')}p" if info.get('height') else 'HD',
                        'response_time': f"{response_time:.0f}ms",
                        'from_cache': False
                    }
                    
                    cache[key] = (result, time.time())
                    logger.info(f"Successfully extracted {content_type} in {response_time:.0f}ms")
                    return result
                    
        except asyncio.TimeoutError:
            logger.warning(f"Timeout with strategy: {strategy}")
            continue
        except Exception as e:
            logger.warning(f"Strategy {strategy} failed: {str(e)}")
            continue
    
    return {
        'success': False, 
        'error': 'Failed to extract video. Make sure the video is public. For stories and private accounts, cookies are required.'
    }

# ========== API ENDPOINTS ==========
@app.get("/")
async def home():
    return {
        "name": "Instagram Video Downloader API",
        "version": "2.2.0",
        "status": "operational",
        "cookies_available": get_cookie_file() is not None,
        "features": ["Reels", "Posts", "Stories", "IGTV"],
        "limits": {
            "max_video_size_mb": MAX_VIDEO_SIZE // (1024 * 1024),
            "cache_duration_minutes": CACHE_TIME // 60
        },
        "usage": "/download?url=https://www.instagram.com/reel/XXXXXXXXX/"
    }

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "cache_size": len(cache.cache),
        "cookies_available": get_cookie_file() is not None
    }

@app.get("/download")
async def download_video(
    request: Request,
    url: str = Query(..., description="Instagram video URL")
):
    """Download Instagram video"""
    
    if not url or 'instagram.com' not in url:
        raise HTTPException(400, "Invalid Instagram URL")
    
    result = await extract_instagram(url)
    
    if result.get('success'):
        return JSONResponse(content=result)
    else:
        raise HTTPException(500, result.get('error', 'Extraction failed'))

@app.get("/proxy")
async def proxy_download(
    video_url: str = Query(...),
    filename: str = Query("video.mp4")
):
    """Proxy for direct download"""
    
    safe_filename = re.sub(r'[^\w\-.]', '_', filename)
    if not safe_filename.endswith('.mp4'):
        safe_filename += '.mp4'
    
    async def stream():
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            headers = {'User-Agent': get_ua()}
            async with session.get(video_url, headers=headers) as resp:
                if resp.status != 200:
                    raise HTTPException(502, "Video source unavailable")
                async for chunk in resp.content.iter_chunked(8192):
                    yield chunk
    
    return StreamingResponse(
        stream(),
        media_type="video/mp4",
        headers={"Content-Disposition": f'attachment; filename="{safe_filename}"'}
    )

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
