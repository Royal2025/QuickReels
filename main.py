"""
INSTAGRAM VIDEO DOWNLOADER API - REELS, POSTS, STORIES ONLY
Real-time cookies support for better extraction
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
import tempfile
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
CACHE_TIME = 1800  # 30 minutes
MAX_VIDEO_SIZE = 100 * 1024 * 1024  # 100MB
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
    'Mozilla/5.0 (Linux; Android 13; SM-G998B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36',
]

def get_ua():
    return random.choice(USER_AGENTS)

def cache_key(url: str, cookies_hash: str = None) -> str:
    """Generate cache key with optional cookies hash"""
    key = url
    if cookies_hash:
        key = f"{url}_{cookies_hash}"
    return hashlib.md5(key.encode()).hexdigest()

def detect_type(url: str) -> str:
    """Auto detect content type - Only Reels, Posts, Stories"""
    url_lower = url.lower()
    if '/reel/' in url_lower:
        return 'Reel'
    elif '/p/' in url_lower:
        return 'Post'
    elif '/stories/' in url_lower or '/story/' in url_lower:
        return 'Story'
    else:
        return 'Video'

def save_cookies_temp(cookies_str: str) -> str:
    """Save cookies string to temporary file"""
    try:
        # Parse cookies string
        cookies = {}
        for cookie in cookies_str.split(';'):
            if '=' in cookie:
                key, value = cookie.strip().split('=', 1)
                cookies[key] = value
        
        # Create temp file
        temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
        
        # Write in netscape format
        with open(temp_file.name, 'w') as f:
            f.write("# Netscape HTTP Cookie File\n")
            for key, value in cookies.items():
                f.write(f".instagram.com\tTRUE\t/\tFALSE\t0\t{key}\t{value}\n")
        
        logger.info(f"Cookies saved to temp file")
        return temp_file.name
        
    except Exception as e:
        logger.error(f"Failed to save cookies: {e}")
        return None

# ========== LIFESPAN ==========
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Instagram Video Downloader API - Reels, Posts, Stories Only")
    yield
    logger.info("Shutting down...")
    cache.cache.clear()

app = FastAPI(
    title="Instagram Video Downloader", 
    version="2.3.0", 
    lifespan=lifespan,
    description="Download Instagram Reels, Posts, Stories - HD Quality"
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

# ========== INSTAGRAM EXTRACTOR ==========
async def extract_instagram(url: str, cookies_str: str = None) -> Dict:
    """Extract Instagram video with optional cookies"""
    
    start = time.time()
    
    # Generate cache key
    cookies_hash = hashlib.md5(cookies_str.encode()).hexdigest() if cookies_str else None
    key = cache_key(url, cookies_hash)
    
    # Check cache
    cached = cache.get(key)
    if cached and time.time() - cached[1] < CACHE_TIME:
        logger.info(f"Cache hit for: {url[:50]}...")
        cached[0]['from_cache'] = True
        cached[0]['response_time'] = f"{(time.time() - start)*1000:.0f}ms"
        return cached[0]
    
    content_type = detect_type(url)
    logger.info(f"Extracting {content_type}: {url[:80]}...")
    
    # Prepare yt-dlp options
    opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'format': 'bestvideo+bestaudio/best[ext=mp4]/best',
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
    }
    
    # Add cookies if provided
    temp_cookie_file = None
    if cookies_str:
        temp_cookie_file = save_cookies_temp(cookies_str)
        if temp_cookie_file:
            opts['cookiefile'] = temp_cookie_file
            logger.info("Using user cookies for extraction")
    
    # Add proxy if configured
    proxy = os.environ.get('PROXY_URL')
    if proxy:
        opts['proxy'] = proxy
    
    try:
        def extract():
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)
        
        info = await asyncio.wait_for(
            asyncio.to_thread(extract), 
            timeout=EXTRACTION_TIMEOUT
        )
        
        if info:
            # Get video URL
            video_url = None
            
            # Check direct URL
            if info.get('url'):
                video_url = info['url']
            # Check formats
            elif info.get('formats'):
                for fmt in info['formats']:
                    if fmt.get('vcodec') != 'none' and fmt.get('acodec') != 'none':
                        video_url = fmt.get('url')
                        break
                if not video_url and info['formats']:
                    video_url = info['formats'][-1].get('url')
            
            # Check if valid video URL
            if video_url and not video_url.endswith(('.mp3', '.m4a', '.aac', '.m3u8')):
                response_time = (time.time() - start) * 1000
                
                result = {
                    'success': True,
                    'url': video_url,
                    'title': info.get('title', 'Instagram Video'),
                    'duration': info.get('duration'),
                    'thumbnail': info.get('thumbnail'),
                    'uploader': info.get('uploader', info.get('channel', 'Instagram User')),
                    'content_type': content_type,
                    'quality': f"{info.get('height', 'HD')}p" if info.get('height') else 'HD',
                    'response_time': f"{response_time:.0f}ms",
                    'from_cache': False
                }
                
                # Save to cache
                cache[key] = (result, time.time())
                logger.info(f"Successfully extracted {content_type} in {response_time:.0f}ms")
                return result
                
    except asyncio.TimeoutError:
        logger.error(f"Timeout extracting: {url}")
    except Exception as e:
        logger.error(f"Extraction failed: {str(e)}")
    finally:
        # Clean up temp cookie file
        if temp_cookie_file and os.path.exists(temp_cookie_file):
            try:
                os.unlink(temp_cookie_file)
            except:
                pass
    
    return {
        'success': False, 
        'error': 'Failed to extract video. Make sure the video is public. For stories, login cookies are required.'
    }

# ========== API ENDPOINTS ==========
@app.get("/")
async def home():
    return {
        "name": "Instagram Video Downloader API",
        "version": "2.3.0",
        "status": "operational",
        "features": ["Reels", "Posts", "Stories"],
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
        "features": ["Reels", "Posts", "Stories"]
    }

@app.get("/download")
async def download_video(
    request: Request,
    url: str = Query(..., description="Instagram video URL"),
    cookies: Optional[str] = Query(None, description="Instagram cookies (sessionid=...; mid=...; etc)")
):
    """Download Instagram Reels, Posts, Stories"""
    
    if not url or 'instagram.com' not in url:
        raise HTTPException(400, "Invalid Instagram URL")
    
    # Validate content type
    content_type = detect_type(url)
    if content_type not in ['Reel', 'Post', 'Story']:
        raise HTTPException(400, "Only Reels, Posts, and Stories are supported")
    
    result = await extract_instagram(url, cookies)
    
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
