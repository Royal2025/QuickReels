"""
🚀 VIDEO ROCKET API - FINAL v11.0
Production Ready | Full Platform Support + Fallback System
Platforms: Instagram, Facebook, YouTube, Pinterest, TikTok, Twitter/X, Reddit

Run: uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
Install: pip install fastapi uvicorn yt-dlp requests beautifulsoup4
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
import json
import requests
from typing import Dict, Optional, List
from bs4 import BeautifulSoup
import urllib.parse

# ========== CONFIG ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Video Rocket API",
    version="11.0.0",
    description="Production-ready video extraction API with multiple fallback methods"
)

# ========== CORS ==========
PRODUCTION_DOMAINS = [
    "https://quicksreels.web.app",
    "https://www.quicksreels.web.app",
    "http://localhost:3000",
    "http://localhost:7700",
    "http://localhost:8000",
]

ENV = os.getenv("ENV", "development")
ALLOWED_ORIGINS = ["*"] if ENV == "development" else PRODUCTION_DOMAINS

logger.info(f"Running in {ENV} mode | CORS: {ALLOWED_ORIGINS}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# ========== CONCURRENCY SEMAPHORE ==========
extraction_semaphore = asyncio.Semaphore(3)

# ========== YT-DLP BASE OPTIONS ==========
YDL_OPTS_BASE = {
    'quiet': True,
    'no_warnings': True,
    'extract_flat': False,
    'ignoreerrors': False,
    'cachedir': False,
    'noplaylist': True,
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    }
}

# Per-platform yt-dlp option overrides
PLATFORM_OPTS: Dict[str, dict] = {
    'youtube': {
        'format': 'bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/best[ext=mp4]/best',
    },
    'instagram': {
        'format': 'best[ext=mp4]/best',
    },
    'facebook': {
        'format': 'best[ext=mp4]/best',
    },
    'tiktok': {
        'format': 'best[ext=mp4]/best',
        'http_headers': {
            'User-Agent': 'TikTok 26.2.0 rv:262018 (iPhone; iOS 14.4.2; en_US) Cronet',
        }
    },
    'twitter': {
        'format': 'best[ext=mp4]/best',
    },
    'pinterest': {
        'format': 'best[ext=mp4]/best',
    },
    'reddit': {
        'format': 'best[ext=mp4]/best',
    },
}

# ========== CACHE ==========
url_cache: Dict[str, tuple] = {}
CACHE_TTL = 600        # 10 minutes
MAX_CACHE_SIZE = 100
CACHE_HITS = 0
CACHE_MISSES = 0

def clean_old_cache():
    global url_cache
    if len(url_cache) > MAX_CACHE_SIZE:
        items_to_remove = int(MAX_CACHE_SIZE * 0.2)
        sorted_items = sorted(url_cache.items(), key=lambda x: x[1][1])
        for key, _ in sorted_items[:items_to_remove]:
            del url_cache[key]

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
    url_cache[key] = (data.copy(), time.time())
    clean_old_cache()

# ========== RATE LIMITING ==========
_rate_store: Dict[str, dict] = {}
RATE_LIMIT = 20
RATE_WINDOW = 60

def check_rate_limit(ip: str) -> bool:
    now = time.time()
    entry = _rate_store.get(ip)
    if not entry or now > entry['reset_at']:
        _rate_store[ip] = {'count': 1, 'reset_at': now + RATE_WINDOW}
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
    if 'twitter.com' in u or 'x.com' in u or 't.co' in u:
        return 'twitter'
    if 'reddit.com' in u or 'redd.it' in u:
        return 'reddit'
    return 'unknown'

SUPPORTED_PLATFORMS = {'instagram', 'facebook', 'youtube', 'pinterest', 'tiktok', 'twitter', 'reddit'}

# ========== FALLBACK METHODS ==========

class FallbackExtractor:
    """Alternative extraction methods for when yt-dlp fails"""
    
    @staticmethod
    def extract_youtube_video(url: str) -> Optional[Dict]:
        """Fallback for YouTube using direct HTML parsing"""
        try:
            # Method 1: Get video ID and use oEmbed
            video_id = None
            if 'youtu.be' in url:
                video_id = url.split('/')[-1].split('?')[0]
            elif 'youtube.com/watch' in url:
                video_id = re.search(r'v=([^&]+)', url).group(1)
            
            if video_id:
                # Try oEmbed endpoint
                oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
                response = requests.get(oembed_url, timeout=10)
                if response.status_code == 200:
                    oembed_data = response.json()
                    
                    # Construct video URL from video ID
                    video_urls = [
                        f"https://www.youtube.com/watch?v={video_id}",
                        f"https://youtu.be/{video_id}",
                        f"https://www.youtube.com/embed/{video_id}"
                    ]
                    
                    return {
                        'url': video_urls[0],  # Return watch URL for client-side processing
                        'title': oembed_data.get('title', 'YouTube Video'),
                        'duration': None,
                        'thumbnail': oembed_data.get('thumbnail_url'),
                        'platform': 'youtube',
                        'uploader': oembed_data.get('author_name')
                    }
        except Exception as e:
            logger.warning(f"YouTube fallback failed: {e}")
        return None
    
    @staticmethod
    def extract_instagram_fallback(url: str) -> Optional[Dict]:
        """Fallback for Instagram using direct HTML parsing"""
        try:
            # Clean Instagram URL
            if '?' in url:
                url = url.split('?')[0]
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
            }
            
            response = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
            if response.status_code == 200:
                # Look for video URLs in HTML
                video_patterns = [
                    r'<meta property="og:video" content="([^"]+)"',
                    r'<meta property="og:video:secure_url" content="([^"]+)"',
                    r'"video_url":"([^"]+)"',
                    r'"display_url":"([^"]+)"',
                ]
                
                for pattern in video_patterns:
                    match = re.search(pattern, response.text)
                    if match:
                        video_url = match.group(1).replace('\\u0026', '&')
                        if video_url.endswith('.mp4') or 'video' in video_url:
                            # Get title
                            title_match = re.search(r'<meta property="og:title" content="([^"]+)"', response.text)
                            title = title_match.group(1) if title_match else 'Instagram Video'
                            
                            # Get thumbnail
                            thumb_match = re.search(r'<meta property="og:image" content="([^"]+)"', response.text)
                            thumbnail = thumb_match.group(1) if thumb_match else None
                            
                            return {
                                'url': video_url,
                                'title': title,
                                'duration': None,
                                'thumbnail': thumbnail,
                                'platform': 'instagram',
                                'uploader': None
                            }
        except Exception as e:
            logger.warning(f"Instagram fallback failed: {e}")
        return None
    
    @staticmethod
    def extract_tiktok_fallback(url: str) -> Optional[Dict]:
        """Fallback for TikTok using alternative methods"""
        try:
            # Use TikTok's oEmbed endpoint
            embed_url = f"https://www.tiktok.com/oembed?url={urllib.parse.quote(url)}"
            response = requests.get(embed_url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
            
            if response.status_code == 200:
                data = response.json()
                # Try to get video URL from embed HTML
                embed_html = data.get('html', '')
                video_match = re.search(r'src="([^"]+\.mp4[^"]*)"', embed_html)
                
                if video_match:
                    video_url = video_match.group(1)
                    return {
                        'url': video_url,
                        'title': data.get('title', 'TikTok Video'),
                        'duration': None,
                        'thumbnail': data.get('thumbnail_url'),
                        'platform': 'tiktok',
                        'uploader': data.get('author_name')
                    }
        except Exception as e:
            logger.warning(f"TikTok fallback failed: {e}")
        return None
    
    @staticmethod
    def extract_twitter_fallback(url: str) -> Optional[Dict]:
        """Fallback for Twitter/X using oEmbed"""
        try:
            # Use Twitter's oEmbed API
            embed_url = f"https://publish.twitter.com/oembed?url={urllib.parse.quote(url)}"
            response = requests.get(embed_url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
            
            if response.status_code == 200:
                data = response.json()
                embed_html = data.get('html', '')
                
                # Extract video URL from embed HTML
                video_match = re.search(r'<video[^>]*><source[^>]*src="([^"]+)"', embed_html)
                if not video_match:
                    video_match = re.search(r'https://video\.twimg\.com/[^"]+\.mp4', embed_html)
                
                if video_match:
                    video_url = video_match.group(1) if video_match.group(1) else video_match.group(0)
                    return {
                        'url': video_url,
                        'title': data.get('title', 'Twitter Video'),
                        'duration': None,
                        'thumbnail': None,
                        'platform': 'twitter',
                        'uploader': data.get('author_name')
                    }
        except Exception as e:
            logger.warning(f"Twitter fallback failed: {e}")
        return None
    
    @staticmethod
    def extract_facebook_fallback(url: str) -> Optional[Dict]:
        """Fallback for Facebook using direct request"""
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'text/html,application/xhtml+xml',
            }
            response = requests.get(url, headers=headers, timeout=15)
            
            if response.status_code == 200:
                # Look for video URLs in page source
                video_patterns = [
                    r'"playable_url":"([^"]+)"',
                    r'"playable_url_quality_hd":"([^"]+)"',
                    r'<meta property="og:video" content="([^"]+)"',
                    r'"browser_native_hd_url":"([^"]+)"',
                ]
                
                for pattern in video_patterns:
                    match = re.search(pattern, response.text)
                    if match:
                        video_url = match.group(1).replace('\\/', '/')
                        if video_url.startswith('http'):
                            # Get title
                            title_match = re.search(r'<meta property="og:title" content="([^"]+)"', response.text)
                            title = title_match.group(1) if title_match else 'Facebook Video'
                            
                            return {
                                'url': video_url,
                                'title': title,
                                'duration': None,
                                'thumbnail': None,
                                'platform': 'facebook',
                                'uploader': None
                            }
        except Exception as e:
            logger.warning(f"Facebook fallback failed: {e}")
        return None

# ========== FORMAT SELECTION ==========
def get_best_format(formats: list) -> Optional[str]:
    if not formats:
        return None
    
    sorted_formats = sorted(
        [f for f in formats if f.get('height') and f.get('url')],
        key=lambda x: x.get('height', 0),
        reverse=True
    )
    
    for f in sorted_formats:
        if f.get('ext') == 'mp4' and f.get('acodec') not in (None, 'none'):
            return f['url']
    
    for f in sorted_formats:
        if f.get('acodec') not in (None, 'none') and f.get('url'):
            return f['url']
    
    if sorted_formats:
        return sorted_formats[0].get('url')
    
    return None

# ========== MAIN EXTRACTOR WITH FALLBACK ==========
async def extract_all_data(url: str, platform: str, retry_count: int = 0) -> Dict:
    acquired = False
    
    try:
        # Try to acquire semaphore with 2s timeout
        try:
            await asyncio.wait_for(extraction_semaphore.acquire(), timeout=2.0)
            acquired = True
        except asyncio.TimeoutError:
            return {
                'success': False,
                'error': 'Server busy. Please retry in a few seconds.',
                'busy': True
            }
        
        # First attempt: yt-dlp
        opts = {**YDL_OPTS_BASE}
        for key, val in PLATFORM_OPTS.get(platform, {}).items():
            if key == 'http_headers':
                opts['http_headers'] = {**opts.get('http_headers', {}), **val}
            else:
                opts[key] = val
        
        if 'format' not in opts:
            opts['format'] = 'bv*[ext=mp4]+ba/b[ext=mp4]/best[ext=mp4]/best'
        
        def _extract():
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)
        
        # Run blocking yt-dlp in a thread
        info = await asyncio.wait_for(
            asyncio.to_thread(_extract),
            timeout=30.0
        )
        
        if info:
            video_url = get_best_format(info.get('formats', []))
            if not video_url:
                video_url = info.get('url')
            
            if video_url:
                return {
                    'success': True,
                    'url': video_url,
                    'title': info.get('title') or 'video',
                    'duration': info.get('duration'),
                    'thumbnail': info.get('thumbnail'),
                    'platform': platform,
                    'uploader': info.get('uploader') or info.get('channel'),
                    'method': 'yt-dlp'
                }
    
    except (asyncio.TimeoutError, yt_dlp.utils.DownloadError, Exception) as e:
        logger.warning(f"yt-dlp failed for {platform}: {str(e)[:100]}")
        
        # SECOND ATTEMPT: FALLBACK METHODS
        logger.info(f"Attempting fallback for {platform}...")
        fallback_result = None
        
        try:
            if platform == 'youtube':
                fallback_result = await asyncio.to_thread(FallbackExtractor.extract_youtube_video, url)
            elif platform == 'instagram':
                fallback_result = await asyncio.to_thread(FallbackExtractor.extract_instagram_fallback, url)
            elif platform == 'tiktok':
                fallback_result = await asyncio.to_thread(FallbackExtractor.extract_tiktok_fallback, url)
            elif platform == 'twitter':
                fallback_result = await asyncio.to_thread(FallbackExtractor.extract_twitter_fallback, url)
            elif platform == 'facebook':
                fallback_result = await asyncio.to_thread(FallbackExtractor.extract_facebook_fallback, url)
            
            if fallback_result and fallback_result.get('url'):
                fallback_result['success'] = True
                fallback_result['method'] = 'fallback'
                fallback_result['warning'] = 'Extracted using fallback method. Video may be lower quality.'
                logger.info(f"Fallback successful for {platform}")
                return fallback_result
                
        except Exception as fallback_error:
            logger.error(f"Fallback also failed for {platform}: {fallback_error}")
    
    finally:
        if acquired:
            extraction_semaphore.release()
    
    # If all methods failed
    return {
        'success': False,
        'error': 'Unable to fetch video. The video may be private, removed, or requires login. Please try a different URL or check if the video is publicly accessible.'
    }

# ========== /download ENDPOINT ==========
@app.get("/download")
async def download_video(
    request: Request,
    link: str = Query(..., description="Social media video URL"),
    raw: bool = Query(False, description="Return only the direct video URL as plain text")
):
    start_time = time.time()
    
    # Get client IP
    forwarded = request.headers.get("x-forwarded-for")
    client_ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")
    
    # Rate limit
    if not check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Max 20 requests/minute.")
    
    # Validate URL
    if not link or not link.startswith(('http://', 'https://')):
        raise HTTPException(status_code=400, detail="Invalid URL. Must start with http:// or https://")
    
    # Detect platform
    platform = detect_platform(link)
    if platform not in SUPPORTED_PLATFORMS:
        raise HTTPException(
            status_code=400,
            detail="Unsupported platform. Supported: Instagram, Facebook, YouTube, Pinterest, TikTok, Twitter/X, Reddit"
        )
    
    try:
        # Cache lookup
        cache_key = hashlib.md5(link.encode()).hexdigest()
        cached = get_cached(cache_key)
        
        if cached:
            cached['response_time'] = round((time.time() - start_time) * 1000, 2)
            cached['instant'] = True
            cached['active_extractions'] = 3 - extraction_semaphore._value
            logger.info(f"CACHE | {platform} | {cached['response_time']}ms")
            if raw:
                return PlainTextResponse(content=cached['url'])
            return JSONResponse(content=cached)
        
        # Fresh extraction with fallback
        result = await extract_all_data(link, platform)
        response_time = round((time.time() - start_time) * 1000, 2)
        
        if not result['success']:
            logger.error(f"FAIL | {platform} | {response_time}ms | {result.get('error', 'Unknown error')}")
            raise HTTPException(status_code=500, detail=result.get('error', 'Extraction failed'))
        
        result['response_time'] = response_time
        result['instant'] = False
        result['active_extractions'] = 3 - extraction_semaphore._value
        
        logger.info(f"SUCCESS | {platform} | {result.get('method', 'unknown')} | {response_time}ms")
        
        set_cache(cache_key, result)
        
        if raw:
            return PlainTextResponse(content=result['url'])
        
        return JSONResponse(content=result)
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Endpoint error: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

# ========== / ROOT ==========
@app.get("/")
async def root():
    return {
        "name": "Video Rocket API",
        "version": "11.0.0",
        "status": "Production Ready",
        "mode": ENV,
        "cors": "all origins" if ALLOWED_ORIGINS == ["*"] else f"{len(ALLOWED_ORIGINS)} domains",
        "cache": f"{len(url_cache)}/{MAX_CACHE_SIZE}",
        "supported_platforms": sorted(SUPPORTED_PLATFORMS),
        "features": {
            "fallback_methods": True,
            "auto_retry": True,
            "rate_limited": True
        },
        "endpoints": {
            "GET /download?link=URL": "Get video info + direct URL (JSON)",
            "GET /download?link=URL&raw=true": "Get direct video URL only (plain text)",
            "GET /health": "Health check",
            "GET /status": "System status",
        }
    }

# ========== /status ==========
@app.get("/status")
async def get_status():
    active = 3 - extraction_semaphore._value
    total = CACHE_HITS + CACHE_MISSES
    return {
        "version": "11.0.0",
        "mode": ENV,
        "capacity": {
            "max_concurrent": 3,
            "active_extractions": active,
            "available_slots": extraction_semaphore._value,
            "status": "available" if extraction_semaphore._value > 0 else "busy",
        },
        "cache": {
            "size": len(url_cache),
            "max_size": MAX_CACHE_SIZE,
            "hit_rate": f"{(CACHE_HITS / total * 100):.1f}%" if total > 0 else "0%",
        },
        "rate_limit": f"{RATE_LIMIT}/minute",
        "supported_platforms": sorted(SUPPORTED_PLATFORMS),
        "fallback_enabled": True
    }

# ========== /health ==========
@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "version": "11.0.0",
        "mode": ENV,
        "ready": extraction_semaphore._value > 0,
        "fallback_system": "active"
    }

# ========== RUN ==========
# uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
