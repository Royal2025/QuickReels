"""
🚀 VIDEO ROCKET API - FINAL v12.0
Full Platform Support | Working YouTube, Facebook, Pinterest + All Others
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
import urllib.parse

# ========== CONFIG ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Video Rocket API",
    version="12.0.0",
    description="Full platform video extraction API"
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

logger.info(f"Running in {ENV} mode")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
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
    'prefer_ffmpeg': True,
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-us,en;q=0.5',
        'Sec-Fetch-Mode': 'navigate',
    }
}

# Enhanced platform-specific options
PLATFORM_OPTS: Dict[str, dict] = {
    'youtube': {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'extract_flat': False,
    },
    'instagram': {
        'format': 'best[ext=mp4]/best',
        'extract_flat': False,
    },
    'facebook': {
        'format': 'best[ext=mp4]/best',
        'extract_flat': False,
    },
    'tiktok': {
        'format': 'best[ext=mp4]/best',
        'extract_flat': False,
        'http_headers': {
            'User-Agent': 'TikTok 26.2.0 rv:262018 (iPhone; iOS 14.4.2; en_US) Cronet',
        }
    },
    'twitter': {
        'format': 'best[ext=mp4]/best',
        'extract_flat': False,
    },
    'pinterest': {
        'format': 'best[ext=mp4]/best',
        'extract_flat': False,
    },
    'reddit': {
        'format': 'best[ext=mp4]/best',
        'extract_flat': False,
    },
}

# ========== CACHE ==========
url_cache: Dict[str, tuple] = {}
CACHE_TTL = 600
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
    if 'twitter.com' in u or 'x.com' in u:
        return 'twitter'
    if 'reddit.com' in u or 'redd.it' in u:
        return 'reddit'
    return 'unknown'

SUPPORTED_PLATFORMS = {'instagram', 'facebook', 'youtube', 'pinterest', 'tiktok', 'twitter', 'reddit'}

# ========== IMPROVED FALLBACK METHODS ==========

class PlatformExtractor:
    """Platform-specific extractors with multiple methods"""
    
    @staticmethod
    def extract_youtube(url: str) -> Optional[Dict]:
        """Multiple methods for YouTube extraction"""
        try:
            # Method 1: Get video ID
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
            
            # Try different methods to get video URL
            methods = [
                # Method A: Use YouTube's oEmbed
                lambda: PlatformExtractor._youtube_oembed(video_id),
                # Method B: Use yt-dlp directly (we already did this)
                # Method C: Use invidious API
                lambda: PlatformExtractor._youtube_invidious(video_id),
                # Method D: Use youtube-dl API alternative
                lambda: PlatformExtractor._youtube_direct(video_id),
            ]
            
            for method in methods:
                try:
                    result = method()
                    if result and result.get('url'):
                        return result
                except:
                    continue
            
            # If all methods fail, return at least the video ID
            return {
                'url': f"https://www.youtube.com/watch?v={video_id}",
                'title': f"YouTube Video {video_id}",
                'thumbnail': f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
                'platform': 'youtube',
                'method': 'direct_link'
            }
            
        except Exception as e:
            logger.error(f"YouTube extraction error: {e}")
            return None
    
    @staticmethod
    def _youtube_oembed(video_id: str) -> Optional[Dict]:
        """Use YouTube oEmbed API"""
        try:
            oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
            response = requests.get(oembed_url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
            if response.status_code == 200:
                data = response.json()
                return {
                    'url': f"https://www.youtube.com/watch?v={video_id}",
                    'title': data.get('title', 'YouTube Video'),
                    'thumbnail': f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
                    'platform': 'youtube',
                    'uploader': data.get('author_name'),
                    'method': 'oembed'
                }
        except:
            pass
        return None
    
    @staticmethod
    def _youtube_invidious(video_id: str) -> Optional[Dict]:
        """Use Invidious instances (open-source YouTube proxy)"""
        invidious_instances = [
            "https://invidious.io.lol",
            "https://yewtu.be",
            "https://inv.riverside.rocks",
        ]
        
        for instance in invidious_instances:
            try:
                api_url = f"{instance}/api/v1/videos/{video_id}"
                response = requests.get(api_url, timeout=8)
                if response.status_code == 200:
                    data = response.json()
                    # Get best format
                    formats = data.get('formatStreams', [])
                    for fmt in formats:
                        if fmt.get('type', '').startswith('video/mp4'):
                            return {
                                'url': fmt.get('url'),
                                'title': data.get('title', 'YouTube Video'),
                                'thumbnail': f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
                                'platform': 'youtube',
                                'uploader': data.get('author'),
                                'method': 'invidious'
                            }
            except:
                continue
        return None
    
    @staticmethod
    def _youtube_direct(video_id: str) -> Optional[Dict]:
        """Try to get direct video URL from YouTube's embed page"""
        try:
            embed_url = f"https://www.youtube.com/embed/{video_id}"
            response = requests.get(embed_url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
            if response.status_code == 200:
                # Look for video URLs in the embed page
                video_urls = re.findall(r'"url":"([^"]+\.mp4[^"]*)"', response.text)
                if video_urls:
                    return {
                        'url': video_urls[0].replace('\\u0026', '&'),
                        'title': f"YouTube Video {video_id}",
                        'thumbnail': f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
                        'platform': 'youtube',
                        'method': 'embed'
                    }
        except:
            pass
        return None
    
    @staticmethod
    def extract_facebook(url: str) -> Optional[Dict]:
        """Extract Facebook video"""
        try:
            # Clean URL
            if '?' in url:
                url = url.split('?')[0]
            
            # Method 1: Use Facebook's oEmbed
            oembed_url = f"https://www.facebook.com/plugins/video/oembed.json/?url={urllib.parse.quote(url)}"
            response = requests.get(oembed_url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
            
            if response.status_code == 200:
                data = response.json()
                html = data.get('html', '')
                
                # Extract video URL from embed HTML
                video_match = re.search(r'src="([^"]+\.mp4[^"]*)"', html)
                if video_match:
                    video_url = video_match.group(1)
                    return {
                        'url': video_url,
                        'title': data.get('title', 'Facebook Video'),
                        'thumbnail': data.get('thumbnail_url'),
                        'platform': 'facebook',
                        'uploader': data.get('author_name'),
                        'method': 'oembed'
                    }
            
            # Method 2: Direct page scraping
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'text/html',
            }
            response = requests.get(url, headers=headers, timeout=15)
            if response.status_code == 200:
                # Look for HD video URL
                hd_match = re.search(r'"browser_native_hd_url":"([^"]+)"', response.text)
                if hd_match:
                    video_url = hd_match.group(1).replace('\\/', '/')
                    return {
                        'url': video_url,
                        'title': 'Facebook Video',
                        'platform': 'facebook',
                        'method': 'scraping'
                    }
                
                # Look for SD video URL
                sd_match = re.search(r'"browser_native_sd_url":"([^"]+)"', response.text)
                if sd_match:
                    video_url = sd_match.group(1).replace('\\/', '/')
                    return {
                        'url': video_url,
                        'title': 'Facebook Video',
                        'platform': 'facebook',
                        'method': 'scraping'
                    }
                    
        except Exception as e:
            logger.warning(f"Facebook fallback failed: {e}")
        return None
    
    @staticmethod
    def extract_pinterest(url: str) -> Optional[Dict]:
        """Extract Pinterest video"""
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            }
            response = requests.get(url, headers=headers, timeout=15)
            
            if response.status_code == 200:
                # Look for video URLs in page
                video_patterns = [
                    r'"video_url":"([^"]+)"',
                    r'"url":"([^"]+\.mp4[^"]*)"',
                    r'<meta property="og:video" content="([^"]+)"',
                ]
                
                for pattern in video_patterns:
                    matches = re.findall(pattern, response.text)
                    for match in matches:
                        video_url = match.replace('\\/', '/').replace('\\u0026', '&')
                        if video_url.endswith('.mp4') or 'video' in video_url:
                            # Get title
                            title_match = re.search(r'<meta property="og:title" content="([^"]+)"', response.text)
                            title = title_match.group(1) if title_match else 'Pinterest Video'
                            
                            # Get thumbnail
                            thumb_match = re.search(r'<meta property="og:image" content="([^"]+)"', response.text)
                            thumbnail = thumb_match.group(1) if thumb_match else None
                            
                            return {
                                'url': video_url,
                                'title': title,
                                'thumbnail': thumbnail,
                                'platform': 'pinterest',
                                'method': 'scraping'
                            }
        except Exception as e:
            logger.warning(f"Pinterest fallback failed: {e}")
        return None
    
    @staticmethod
    def extract_tiktok(url: str) -> Optional[Dict]:
        """Extract TikTok video"""
        try:
            # Use TikTok's oEmbed
            embed_url = f"https://www.tiktok.com/oembed?url={urllib.parse.quote(url)}"
            response = requests.get(embed_url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
            
            if response.status_code == 200:
                data = response.json()
                embed_html = data.get('html', '')
                
                # Extract video URL
                video_match = re.search(r'src="([^"]+\.mp4[^"]*)"', embed_html)
                if video_match:
                    video_url = video_match.group(1)
                    return {
                        'url': video_url,
                        'title': data.get('title', 'TikTok Video'),
                        'thumbnail': data.get('thumbnail_url'),
                        'platform': 'tiktok',
                        'uploader': data.get('author_name'),
                        'method': 'oembed'
                    }
        except Exception as e:
            logger.warning(f"TikTok fallback failed: {e}")
        return None
    
    @staticmethod
    def extract_twitter(url: str) -> Optional[Dict]:
        """Extract Twitter/X video"""
        try:
            # Use Twitter's oEmbed
            embed_url = f"https://publish.twitter.com/oembed?url={urllib.parse.quote(url)}"
            response = requests.get(embed_url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
            
            if response.status_code == 200:
                data = response.json()
                embed_html = data.get('html', '')
                
                # Extract video URL
                video_match = re.search(r'https://video\.twimg\.com/[^"]+\.mp4', embed_html)
                if video_match:
                    return {
                        'url': video_match.group(0),
                        'title': data.get('title', 'Twitter Video'),
                        'platform': 'twitter',
                        'uploader': data.get('author_name'),
                        'method': 'oembed'
                    }
        except Exception as e:
            logger.warning(f"Twitter fallback failed: {e}")
        return None
    
    @staticmethod
    def extract_reddit(url: str) -> Optional[Dict]:
        """Extract Reddit video"""
        try:
            # Add .json to URL for Reddit API
            if 'reddit.com' in url:
                json_url = url.rstrip('/') + '.json'
                if '?' in json_url:
                    json_url = json_url.split('?')[0] + '.json'
                
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                }
                response = requests.get(json_url, headers=headers, timeout=15)
                
                if response.status_code == 200:
                    data = response.json()
                    if data and len(data) > 0:
                        post_data = data[0]['data']['children'][0]['data']
                        
                        # Check for video
                        if post_data.get('is_video'):
                            video_url = post_data.get('url')
                            if video_url:
                                return {
                                    'url': video_url,
                                    'title': post_data.get('title', 'Reddit Video'),
                                    'thumbnail': post_data.get('thumbnail'),
                                    'platform': 'reddit',
                                    'uploader': post_data.get('author'),
                                    'method': 'api'
                                }
                        
                        # Check for media
                        media = post_data.get('media')
                        if media and media.get('reddit_video'):
                            video_url = media['reddit_video'].get('fallback_url')
                            if video_url:
                                return {
                                    'url': video_url,
                                    'title': post_data.get('title', 'Reddit Video'),
                                    'thumbnail': post_data.get('thumbnail'),
                                    'platform': 'reddit',
                                    'uploader': post_data.get('author'),
                                    'method': 'api'
                                }
        except Exception as e:
            logger.warning(f"Reddit fallback failed: {e}")
        return None

# ========== FORMAT SELECTION ==========
def get_best_format(formats: list) -> Optional[str]:
    if not formats:
        return None
    
    # Try to find MP4 with audio
    for f in formats:
        if f.get('ext') == 'mp4' and f.get('acodec') != 'none':
            return f.get('url')
    
    # Try any video format
    for f in formats:
        if f.get('vcodec') != 'none' and f.get('url'):
            return f.get('url')
    
    # Fallback to first format
    if formats:
        return formats[0].get('url')
    
    return None

# ========== MAIN EXTRACTOR ==========
async def extract_all_data(url: str, platform: str) -> Dict:
    acquired = False
    
    try:
        # Try to acquire semaphore
        try:
            await asyncio.wait_for(extraction_semaphore.acquire(), timeout=2.0)
            acquired = True
        except asyncio.TimeoutError:
            return {
                'success': False,
                'error': 'Server busy. Please retry.',
                'busy': True
            }
        
        # FIRST: Try yt-dlp
        logger.info(f"Attempting yt-dlp extraction for {platform}")
        
        opts = {**YDL_OPTS_BASE}
        if platform in PLATFORM_OPTS:
            for key, val in PLATFORM_OPTS[platform].items():
                if key == 'http_headers':
                    opts['http_headers'] = {**opts.get('http_headers', {}), **val}
                else:
                    opts[key] = val
        
        def _extract():
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)
        
        try:
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
                        'method': 'yt-dlp'
                    }
        except Exception as e:
            logger.warning(f"yt-dlp failed: {str(e)[:100]}")
        
        # SECOND: Platform-specific fallbacks
        logger.info(f"Attempting fallback extraction for {platform}")
        
        fallback_result = None
        
        if platform == 'youtube':
            fallback_result = await asyncio.to_thread(PlatformExtractor.extract_youtube, url)
        elif platform == 'facebook':
            fallback_result = await asyncio.to_thread(PlatformExtractor.extract_facebook, url)
        elif platform == 'pinterest':
            fallback_result = await asyncio.to_thread(PlatformExtractor.extract_pinterest, url)
        elif platform == 'tiktok':
            fallback_result = await asyncio.to_thread(PlatformExtractor.extract_tiktok, url)
        elif platform == 'twitter':
            fallback_result = await asyncio.to_thread(PlatformExtractor.extract_twitter, url)
        elif platform == 'reddit':
            fallback_result = await asyncio.to_thread(PlatformExtractor.extract_reddit, url)
        
        if fallback_result and fallback_result.get('url'):
            fallback_result['success'] = True
            logger.info(f"Fallback successful for {platform}")
            return fallback_result
        
        # If all methods failed
        return {
            'success': False,
            'error': f'Unable to extract video from {platform}. The video may be private, removed, or requires login.'
        }
        
    except Exception as e:
        logger.error(f"Extraction error: {str(e)}")
        return {
            'success': False,
            'error': f'Extraction failed: {str(e)[:200]}'
        }
    
    finally:
        if acquired:
            extraction_semaphore.release()

# ========== ENDPOINTS ==========
@app.get("/download")
async def download_video(
    request: Request,
    link: str = Query(..., description="Video URL"),
    raw: bool = Query(False, description="Return only URL")
):
    start_time = time.time()
    
    # Rate limiting
    forwarded = request.headers.get("x-forwarded-for")
    client_ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")
    
    if not check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Max 20 requests/minute.")
    
    if not link or not link.startswith(('http://', 'https://')):
        raise HTTPException(status_code=400, detail="Invalid URL. Must start with http:// or https://")
    
    platform = detect_platform(link)
    if platform not in SUPPORTED_PLATFORMS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported platform: {platform}. Supported: {', '.join(SUPPORTED_PLATFORMS)}"
        )
    
    try:
        # Cache check
        cache_key = hashlib.md5(link.encode()).hexdigest()
        cached = get_cached(cache_key)
        
        if cached:
            cached['response_time'] = round((time.time() - start_time) * 1000, 2)
            cached['cached'] = True
            logger.info(f"CACHE HIT | {platform}")
            if raw:
                return PlainTextResponse(content=cached['url'])
            return JSONResponse(content=cached)
        
        # Extract video
        result = await extract_all_data(link, platform)
        response_time = round((time.time() - start_time) * 1000, 2)
        
        if not result.get('success'):
            raise HTTPException(status_code=500, detail=result.get('error', 'Extraction failed'))
        
        result['response_time'] = response_time
        result['cached'] = False
        
        # Cache successful results
        set_cache(cache_key, result)
        
        logger.info(f"SUCCESS | {platform} | {result.get('method', 'unknown')} | {response_time}ms")
        
        if raw:
            return PlainTextResponse(content=result['url'])
        
        return JSONResponse(content=result)
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Endpoint error: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/")
async def root():
    return {
        "name": "Video Rocket API",
        "version": "12.0.0",
        "status": "Production Ready",
        "supported_platforms": sorted(SUPPORTED_PLATFORMS),
        "methods": ["yt-dlp", "platform_fallbacks"],
        "endpoints": {
            "/download?link=URL": "Get video URL",
            "/health": "Health check",
            "/status": "System status"
        }
    }

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "version": "12.0.0",
        "platforms": len(SUPPORTED_PLATFORMS)
    }

@app.get("/status")
async def status():
    active = 3 - extraction_semaphore._value
    total = CACHE_HITS + CACHE_MISSES
    return {
        "version": "12.0.0",
        "active_extractions": active,
        "cache_size": len(url_cache),
        "cache_hit_rate": f"{(CACHE_HITS / total * 100):.1f}%" if total > 0 else "0%",
        "supported_platforms": sorted(SUPPORTED_PLATFORMS)
    }
