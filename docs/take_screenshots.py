#!/usr/bin/env python3
"""Generate XelaSphere showcase screenshots using Playwright."""

import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

SCREENSHOTS_DIR = Path(__file__).parent / "screenshots"
STATIC_DIR = Path(__file__).parent.parent / "app" / "static"
LOGO_PATH = STATIC_DIR / "images" / "logo.png"

# Convert to file:// URL
def file_url(path):
    return f"file://{path.resolve()}"

COMMON_HEAD = f"""
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Montserrat:wght@400;500;600;700&family=Roboto+Mono:wght@400;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">
"""

DARK_CHAT_HTML = f"""<!DOCTYPE html>
<html lang="en">
<head>
{COMMON_HEAD}
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    background: #0f0f0f; color: #f4f4f5; display:flex; height:100vh; overflow:hidden;
}}

/* Sidebar */
.sidebar {{
    width: 260px; background: rgba(5,5,10,0.95); border-right: 1px solid rgba(255,255,255,0.06);
    display:flex; flex-direction:column; flex-shrink:0;
}}
.sidebar-header {{
    padding: 1rem; display:flex; align-items:center; gap:0.75rem;
    border-bottom: 1px solid rgba(255,255,255,0.06);
}}
.sidebar-header img {{ width:30px; height:30px; border-radius:6px; }}
.sidebar-header span {{ font-size:14px; font-weight:600; color:#fff; font-family:'Montserrat',sans-serif; }}
.sidebar-actions {{ padding:0.5rem; }}
.new-chat-btn {{
    width:100%; padding:0.6rem 0.75rem; background:rgba(255,255,255,0.06); border:1px solid rgba(255,255,255,0.08);
    border-radius:8px; color:#e4e4e7; font-size:0.85rem; cursor:pointer; display:flex; align-items:center; gap:0.5rem;
    font-family:inherit; transition: background 150ms ease;
}}
.new-chat-btn:hover {{ background:rgba(255,255,255,0.1); }}
.sidebar-section {{ padding:0.5rem; flex:1; overflow-y:auto; }}
.section-label {{ font-size:0.7rem; text-transform:uppercase; color:#737373; padding:0.5rem 0.75rem; font-weight:600; letter-spacing:0.05em; }}
.chat-tab {{
    padding:0.6rem 0.75rem; border-radius:6px; font-size:0.85rem; color:rgba(255,255,255,0.7);
    cursor:pointer; transition: background 120ms ease; display:flex; align-items:center; gap:0.5rem;
    margin-bottom:2px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
}}
.chat-tab:hover {{ background:rgba(255,255,255,0.08); color:rgba(255,255,255,0.95); }}
.chat-tab.active {{ background:rgba(255,255,255,0.1); color:#fff; }}
.chat-tab i {{ font-size:0.75rem; color:rgba(255,255,255,0.4); }}

/* Main */
.main {{ flex:1; display:flex; flex-direction:column; position:relative; }}

/* Model selector */
.model-select {{
    position:absolute; top:1rem; left:1.5rem; z-index:10;
    padding:0.5rem 0.85rem; background:transparent; border:none; color:rgba(255,255,255,0.7);
    font-size:0.95rem; font-weight:500; cursor:pointer; border-radius:8px;
    display:flex; align-items:center; gap:0.4rem; font-family:inherit;
    transition: background 150ms ease, color 150ms ease;
}}
.model-select:hover {{ background:rgba(255,255,255,0.08); color:#fff; }}
.model-select .chevron {{ font-size:0.6rem; margin-left:0.2rem; }}

/* Chat area */
.chat-area {{
    flex:1; overflow-y:auto; padding:3.5rem 1.5rem 1.5rem; display:flex; flex-direction:column; gap:1.5rem;
    max-width:880px; margin:0 auto; width:100%;
    scrollbar-width:thin; scrollbar-color:rgba(255,255,255,0.2) transparent;
}}

/* Messages */
.message {{ display:flex; gap:0.75rem; }}
.message.user {{ justify-content:flex-end; }}
.avatar {{
    width:28px; height:28px; border-radius:50%; flex-shrink:0;
    display:flex; align-items:center; justify-content:center; font-size:0.75rem; font-weight:600;
}}
.avatar.ai {{ background:linear-gradient(135deg,#3b82f6,#6366f1); color:#fff; }}
.avatar.user {{ background:#404040; color:#e4e4e7; }}
.bubble {{
    border-radius:1.2rem; font-size:0.9rem; line-height:1.7; max-width:720px;
}}
.bubble.ai {{
    background:#222223; padding:1.15rem 1.4rem;
    box-shadow:0 1px 0 rgba(255,255,255,0.04), 0 12px 28px rgba(0,0,0,0.35);
    color:rgba(244,244,245,0.92);
}}
.bubble.user {{
    background:#303030; padding:0.7rem 1rem; border-radius:0.95rem 0.95rem 0.35rem 0.95rem;
    max-width:520px; color:#f7f7f7;
}}
.bubble h3 {{ font-size:1rem; font-weight:600; margin-bottom:0.5rem; color:#fff; }}
.bubble p {{ margin-bottom:0.6rem; }}
.bubble p:last-child {{ margin-bottom:0; }}
.bubble code {{
    background:rgba(0,0,0,0.4); padding:0.15rem 0.35rem; border-radius:0.4rem;
    font-size:0.85em; font-family:'Roboto Mono',monospace; color:#e2e8f0;
}}
.bubble pre {{
    background:rgba(0,0,0,0.45); border:1px solid rgba(255,255,255,0.08); border-radius:0.75rem;
    padding:0.9rem 1rem; margin:0.7rem 0; overflow-x:auto; font-size:0.85em; line-height:1.6;
}}
.bubble pre code {{ background:none; padding:0; font-size:inherit; color:#e2e8f0; }}
.bubble ul, .bubble ol {{ padding-left:1.5rem; margin:0.5rem 0; }}
.bubble li {{ margin-bottom:0.3rem; }}
.bubble strong {{ color:#fff; font-weight:600; }}

/* Composer */
.composer-wrapper {{
    padding:0.75rem 1.5rem 1.25rem; max-width:880px; margin:0 auto; width:100%;
}}
.composer {{
    background:rgba(48,48,48,0.92); border:1px solid rgba(255,255,255,0.1);
    border-radius:24px; padding:0.5rem 0.75rem; display:flex; align-items:flex-end; gap:0.5rem;
    box-shadow: 0 2px 12px rgba(0,0,0,0.2);
}}
.composer-input {{
    flex:1; background:none; border:none; color:#f4f4f5; font-size:15px; font-family:inherit;
    padding:0.35rem 0.5rem; resize:none; outline:none; min-height:34px; line-height:1.6;
}}
.composer-input::placeholder {{ color:rgba(255,255,255,0.35); }}
.composer-btn {{
    width:30px; height:30px; border-radius:50%; border:none; cursor:pointer;
    display:flex; align-items:center; justify-content:center; flex-shrink:0;
    transition: background 150ms ease;
}}
.composer-btn.send {{ background:linear-gradient(135deg,#3b82f6,#6366f1); color:#fff; }}
.composer-btn.attach {{ background:rgba(255,255,255,0.08); color:rgba(255,255,255,0.6); }}
.composer-btn.attach:hover {{ background:rgba(255,255,255,0.15); color:#fff; }}

/* Theme toggle */
.theme-toggle {{
    position:fixed; bottom:1.5rem; right:1.5rem; width:44px; height:44px; border-radius:50%;
    background:#2a2a2a; border:1px solid rgba(255,255,255,0.1); cursor:pointer;
    display:flex; align-items:center; justify-content:center; color:#f5f5f5; font-size:1.1rem;
    box-shadow:0 4px 16px rgba(0,0,0,0.3); transition: transform 200ms ease;
}}
.theme-toggle:hover {{ transform:scale(1.05); }}
</style>
</head>
<body>
<div class="sidebar">
    <div class="sidebar-header">
        <img src="{file_url(LOGO_PATH)}" alt="Logo">
        <span>XelaSphere</span>
    </div>
    <div class="sidebar-actions">
        <button class="new-chat-btn"><i class="fas fa-plus"></i> New Chat</button>
    </div>
    <div class="sidebar-section">
        <div class="section-label">Today</div>
        <div class="chat-tab active"><i class="fas fa-message"></i> Python async patterns</div>
        <div class="chat-tab"><i class="fas fa-message"></i> React component design</div>
        <div class="chat-tab"><i class="fas fa-message"></i> Docker multi-stage builds</div>
        <div class="section-label">Yesterday</div>
        <div class="chat-tab"><i class="fas fa-message"></i> Firebase auth setup</div>
        <div class="chat-tab"><i class="fas fa-message"></i> CSS grid vs flexbox</div>
        <div class="chat-tab"><i class="fas fa-message"></i> API rate limiting strategy</div>
        <div class="section-label">Last Week</div>
        <div class="chat-tab"><i class="fas fa-message"></i> PostgreSQL optimization</div>
        <div class="chat-tab"><i class="fas fa-message"></i> WebSocket real-time chat</div>
        <div class="chat-tab"><i class="fas fa-message"></i> Kubernetes deployment</div>
        <div class="chat-tab"><i class="fas fa-message"></i> OAuth 2.0 implementation</div>
    </div>
</div>
<div class="main">
    <div class="model-select">
        Llama 3.3 70B <span class="chevron"><i class="fas fa-chevron-down"></i></span>
    </div>
    <div class="chat-area">
        <div class="message user">
            <div class="bubble user">Can you explain Python's asyncio and show me a practical example with aiohttp?</div>
            <div class="avatar user">Y</div>
        </div>
        <div class="message">
            <div class="avatar ai"><i class="fas fa-bolt" style="font-size:0.7rem"></i></div>
            <div class="bubble ai">
                <h3>Python asyncio with aiohttp</h3>
                <p><strong>asyncio</strong> is Python's built-in library for writing concurrent code using the <code>async/await</code> syntax. Combined with <strong>aiohttp</strong>, it enables high-performance HTTP operations.</p>
                <p>Here's a practical example that fetches multiple URLs concurrently:</p>
                <pre><code>import asyncio
import aiohttp

async def fetch_url(session, url):
    async with session.get(url) as response:
        return await response.json()

async def main():
    urls = [
        "https://api.github.com/repos/python/cpython",
        "https://api.github.com/repos/nodejs/node",
        "https://api.github.com/repos/rust-lang/rust",
    ]

    async with aiohttp.ClientSession() as session:
        tasks = [fetch_url(session, url) for url in urls]
        results = await asyncio.gather(*tasks)

        for result in results:
            print(f"{{result['full_name']}}: ⭐ {{result['stargazers_count']}}")

asyncio.run(main())</code></pre>
                <p><strong>Key benefits:</strong></p>
                <ul>
                    <li><code>asyncio.gather()</code> runs all requests concurrently, not sequentially</li>
                    <li><code>aiohttp.ClientSession</code> reuses TCP connections efficiently</li>
                    <li>This pattern is <strong>3-5x faster</strong> than sequential <code>requests</code> calls</li>
                    <li>Non-blocking I/O means your event loop stays responsive</li>
                </ul>
            </div>
        </div>
        <div class="message user">
            <div class="bubble user">How do I add error handling and timeouts to this?</div>
            <div class="avatar user">Y</div>
        </div>
        <div class="message">
            <div class="avatar ai"><i class="fas fa-bolt" style="font-size:0.7rem"></i></div>
            <div class="bubble ai">
                <p>Great question! Here's the hardened version with proper error handling:</p>
                <pre><code>import asyncio
import aiohttp
from aiohttp import ClientTimeout

async def fetch_url(session, url, retries=3):
    for attempt in range(retries):
        try:
            timeout = ClientTimeout(total=10)
            async with session.get(url, timeout=timeout) as resp:
                resp.raise_for_status()
                return await resp.json()
        except asyncio.TimeoutError:
            if attempt == retries - 1:
                raise
            await asyncio.sleep(2 ** attempt)  # Exponential backoff</code></pre>
                <p>The key additions are:</p>
                <ul>
                    <li><strong>Timeout</strong> — 10 second limit per request via <code>ClientTimeout</code></li>
                    <li><strong>Retries</strong> — 3 attempts with exponential backoff</li>
                    <li><strong>Status checking</strong> — <code>raise_for_status()</code> catches 4xx/5xx errors</li>
                </ul>
            </div>
        </div>
    </div>
    <div class="composer-wrapper">
        <div class="composer">
            <button class="composer-btn attach"><i class="fas fa-paperclip"></i></button>
            <div class="composer-input" contenteditable="true" data-placeholder="Message XelaSphere..."></div>
            <button class="composer-btn send"><i class="fas fa-arrow-up" style="font-size:0.8rem"></i></button>
        </div>
    </div>
    <div class="theme-toggle"><i class="fas fa-moon"></i></div>
</div>
</body>
</html>"""


LIGHT_CHAT_HTML = f"""<!DOCTYPE html>
<html lang="en">
<head>
{COMMON_HEAD}
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    background: #ffffff; color: #1a1a2e; display:flex; height:100vh; overflow:hidden;
}}
.sidebar {{
    width: 260px; background: #f8f8fa; border-right: 1px solid rgba(0,0,0,0.08);
    display:flex; flex-direction:column; flex-shrink:0;
}}
.sidebar-header {{
    padding: 1rem; display:flex; align-items:center; gap:0.75rem;
    border-bottom: 1px solid rgba(0,0,0,0.06);
}}
.sidebar-header img {{ width:30px; height:30px; border-radius:6px; }}
.sidebar-header span {{ font-size:14px; font-weight:600; color:#1a1a2e; font-family:'Montserrat',sans-serif; }}
.sidebar-actions {{ padding:0.5rem; }}
.new-chat-btn {{
    width:100%; padding:0.6rem 0.75rem; background:rgba(0,0,0,0.04); border:1px solid rgba(0,0,0,0.08);
    border-radius:8px; color:#374151; font-size:0.85rem; cursor:pointer; display:flex; align-items:center; gap:0.5rem;
    font-family:inherit;
}}
.sidebar-section {{ padding:0.5rem; flex:1; overflow-y:auto; }}
.section-label {{ font-size:0.7rem; text-transform:uppercase; color:#9ca3af; padding:0.5rem 0.75rem; font-weight:600; letter-spacing:0.05em; }}
.chat-tab {{
    padding:0.6rem 0.75rem; border-radius:6px; font-size:0.85rem; color:#6b7280;
    cursor:pointer; display:flex; align-items:center; gap:0.5rem; margin-bottom:2px;
    white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
}}
.chat-tab:hover {{ background:rgba(0,0,0,0.04); color:#374151; }}
.chat-tab.active {{ background:rgba(0,0,0,0.06); color:#1a1a2e; font-weight:500; }}
.chat-tab i {{ font-size:0.75rem; color:rgba(0,0,0,0.3); }}
.main {{ flex:1; display:flex; flex-direction:column; position:relative; }}
.model-select {{
    position:absolute; top:1rem; left:1.5rem; z-index:10;
    padding:0.5rem 0.85rem; background:transparent; border:none; color:#6b7280;
    font-size:0.95rem; font-weight:500; cursor:pointer; border-radius:8px;
    display:flex; align-items:center; gap:0.4rem; font-family:inherit;
}}
.model-select:hover {{ background:rgba(0,0,0,0.05); color:#1a1a2e; }}
.model-select .chevron {{ font-size:0.6rem; }}
.chat-area {{
    flex:1; overflow-y:auto; padding:3.5rem 1.5rem 1.5rem; display:flex; flex-direction:column; gap:1.5rem;
    max-width:880px; margin:0 auto; width:100%;
}}
.message {{ display:flex; gap:0.75rem; }}
.message.user {{ justify-content:flex-end; }}
.avatar {{
    width:28px; height:28px; border-radius:50%; flex-shrink:0;
    display:flex; align-items:center; justify-content:center; font-size:0.75rem; font-weight:600;
}}
.avatar.ai {{ background:linear-gradient(135deg,#3b82f6,#6366f1); color:#fff; }}
.avatar.user {{ background:#e5e7eb; color:#374151; }}
.bubble {{ border-radius:1.2rem; font-size:0.9rem; line-height:1.7; max-width:720px; }}
.bubble.ai {{
    background:#f3f4f6; padding:1.15rem 1.4rem; color:#1a1a2e;
    box-shadow:0 1px 3px rgba(0,0,0,0.08);
}}
.bubble.user {{
    background:#3b82f6; padding:0.7rem 1rem; border-radius:0.95rem 0.95rem 0.35rem 0.95rem;
    max-width:520px; color:#fff;
}}
.bubble h3 {{ font-size:1rem; font-weight:600; margin-bottom:0.5rem; }}
.bubble p {{ margin-bottom:0.6rem; }}
.bubble p:last-child {{ margin-bottom:0; }}
.bubble code {{
    background:rgba(0,0,0,0.06); padding:0.15rem 0.35rem; border-radius:0.4rem;
    font-size:0.85em; font-family:'Roboto Mono',monospace; color:#1e293b;
}}
.bubble pre {{
    background:#1e293b; border-radius:0.75rem; padding:0.9rem 1rem; margin:0.7rem 0;
    overflow-x:auto; font-size:0.85em; line-height:1.6; color:#e2e8f0;
}}
.bubble pre code {{ background:none; padding:0; color:#e2e8f0; }}
.bubble ul {{ padding-left:1.5rem; margin:0.5rem 0; }}
.bubble li {{ margin-bottom:0.3rem; }}
.bubble strong {{ font-weight:600; }}
.composer-wrapper {{
    padding:0.75rem 1.5rem 1.25rem; max-width:880px; margin:0 auto; width:100%;
}}
.composer {{
    background:#f3f4f6; border:1px solid rgba(0,0,0,0.1);
    border-radius:24px; padding:0.5rem 0.75rem; display:flex; align-items:flex-end; gap:0.5rem;
    box-shadow:0 1px 4px rgba(0,0,0,0.05);
}}
.composer-input {{
    flex:1; background:none; border:none; color:#1a1a2e; font-size:15px; font-family:inherit;
    padding:0.35rem 0.5rem; outline:none; min-height:34px; line-height:1.6;
}}
.composer-btn {{
    width:30px; height:30px; border-radius:50%; border:none; cursor:pointer;
    display:flex; align-items:center; justify-content:center; flex-shrink:0;
}}
.composer-btn.send {{ background:linear-gradient(135deg,#3b82f6,#6366f1); color:#fff; }}
.composer-btn.attach {{ background:rgba(0,0,0,0.06); color:#6b7280; }}
.theme-toggle {{
    position:fixed; bottom:1.5rem; right:1.5rem; width:44px; height:44px; border-radius:50%;
    background:#f3f4f6; border:1px solid rgba(0,0,0,0.1); cursor:pointer;
    display:flex; align-items:center; justify-content:center; color:#f59e0b; font-size:1.1rem;
    box-shadow:0 2px 8px rgba(0,0,0,0.1);
}}
</style>
</head>
<body>
<div class="sidebar">
    <div class="sidebar-header">
        <img src="{file_url(LOGO_PATH)}" alt="Logo">
        <span>XelaSphere</span>
    </div>
    <div class="sidebar-actions">
        <button class="new-chat-btn"><i class="fas fa-plus"></i> New Chat</button>
    </div>
    <div class="sidebar-section">
        <div class="section-label">Today</div>
        <div class="chat-tab active"><i class="fas fa-message"></i> Building REST APIs</div>
        <div class="chat-tab"><i class="fas fa-message"></i> TypeScript generics</div>
        <div class="chat-tab"><i class="fas fa-message"></i> Redis caching patterns</div>
        <div class="section-label">Yesterday</div>
        <div class="chat-tab"><i class="fas fa-message"></i> GraphQL vs REST</div>
        <div class="chat-tab"><i class="fas fa-message"></i> CI/CD pipeline setup</div>
        <div class="chat-tab"><i class="fas fa-message"></i> Unit testing best practices</div>
        <div class="section-label">Last Week</div>
        <div class="chat-tab"><i class="fas fa-message"></i> Microservices architecture</div>
        <div class="chat-tab"><i class="fas fa-message"></i> Database indexing</div>
    </div>
</div>
<div class="main">
    <div class="model-select">
        DeepSeek R1 <span class="chevron"><i class="fas fa-chevron-down"></i></span>
    </div>
    <div class="chat-area">
        <div class="message user">
            <div class="bubble user">Explain the differences between REST and GraphQL APIs</div>
            <div class="avatar user">Y</div>
        </div>
        <div class="message">
            <div class="avatar ai"><i class="fas fa-bolt" style="font-size:0.7rem"></i></div>
            <div class="bubble ai">
                <h3>REST vs GraphQL</h3>
                <p><strong>REST</strong> uses fixed endpoints where each URL returns a predetermined data structure. <strong>GraphQL</strong> provides a single endpoint where clients specify exactly what data they need.</p>
                <p>Key differences:</p>
                <ul>
                    <li><strong>Data fetching</strong> — REST often over-fetches or requires multiple requests. GraphQL lets you request precisely the fields you need in one query</li>
                    <li><strong>Versioning</strong> — REST uses URL versioning (<code>/api/v2/users</code>). GraphQL evolves the schema without versioning</li>
                    <li><strong>Caching</strong> — REST leverages HTTP caching natively. GraphQL requires custom caching strategies</li>
                    <li><strong>Type safety</strong> — GraphQL has a built-in type system and schema introspection</li>
                </ul>
                <p>Here's a comparison:</p>
                <pre><code># REST: Multiple requests needed
GET /api/users/123
GET /api/users/123/posts
GET /api/users/123/followers

# GraphQL: Single request
query {{
  user(id: "123") {{
    name
    posts {{ title, createdAt }}
    followers {{ name }}
  }}
}}</code></pre>
                <p><strong>When to choose REST:</strong> Simple CRUD APIs, public APIs, heavy caching needs</p>
                <p><strong>When to choose GraphQL:</strong> Complex data relationships, mobile apps (bandwidth), rapid frontend iteration</p>
            </div>
        </div>
    </div>
    <div class="composer-wrapper">
        <div class="composer">
            <button class="composer-btn attach"><i class="fas fa-paperclip"></i></button>
            <div class="composer-input" contenteditable="true" data-placeholder="Message XelaSphere..."></div>
            <button class="composer-btn send"><i class="fas fa-arrow-up" style="font-size:0.8rem"></i></button>
        </div>
    </div>
    <div class="theme-toggle"><i class="fas fa-sun"></i></div>
</div>
</body>
</html>"""


LOGIN_HTML = f"""<!DOCTYPE html>
<html lang="en">
<head>
{COMMON_HEAD}
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    background: #0a0a0f; color: #f4f4f5; min-height:100vh;
    display:flex; align-items:center; justify-content:center;
    overflow:hidden; position:relative;
}}
.bg {{
    position:fixed; inset:0; z-index:0;
    background:radial-gradient(ellipse at 20% 50%, rgba(99,102,241,0.15) 0%, transparent 50%),
               radial-gradient(ellipse at 80% 20%, rgba(59,130,246,0.1) 0%, transparent 50%),
               radial-gradient(ellipse at 50% 80%, rgba(139,92,246,0.08) 0%, transparent 50%),
               #0a0a0f;
}}
.card {{
    position:relative; z-index:1; width:100%; max-width:440px;
    background:rgba(20,20,24,0.85); backdrop-filter:blur(20px);
    border:1px solid rgba(255,255,255,0.08); border-radius:1.25rem;
    padding:3rem; box-shadow:0 25px 50px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.05);
}}
.brand {{ text-align:center; margin-bottom:2rem; }}
.brand img {{ width:56px; height:56px; border-radius:12px; }}
.header {{ text-align:center; margin-bottom:2rem; }}
.header h2 {{ font-size:1.5rem; font-weight:700; color:#fff; margin-bottom:0.4rem; }}
.header p {{ font-size:0.9rem; color:#737373; }}
.social-btns {{
    display:grid; grid-template-columns:repeat(4,1fr); gap:0.75rem; margin-bottom:1.5rem;
}}
.social-btn {{
    height:48px; display:flex; align-items:center; justify-content:center;
    background:rgba(255,255,255,0.06); border:1px solid rgba(255,255,255,0.1);
    border-radius:0.625rem; cursor:pointer; transition:all 200ms ease; text-decoration:none;
}}
.social-btn:hover {{ background:rgba(255,255,255,0.1); transform:translateY(-2px); }}
.social-btn img {{ width:20px; height:20px; }}
.social-btn i {{ font-size:1.1rem; color:#fff; }}
.separator {{
    display:flex; align-items:center; gap:1rem; margin-bottom:1.5rem; color:#525252; font-size:0.8rem;
}}
.separator::before, .separator::after {{
    content:''; flex:1; height:1px; background:rgba(255,255,255,0.08);
}}
.form-group {{ margin-bottom:1rem; }}
.form-label {{ display:block; font-size:0.8rem; color:#a3a3a3; margin-bottom:0.4rem; font-weight:500; }}
.form-input {{
    width:100%; height:48px; background:rgba(30,30,34,0.6); border:1px solid rgba(255,255,255,0.1);
    border-radius:0.75rem; padding:0 1rem; color:#f4f4f5; font-size:0.9rem; font-family:inherit;
    outline:none; transition:border 200ms ease;
}}
.form-input:focus {{ border-color:#6366f1; box-shadow:0 0 0 3px rgba(99,102,241,0.15); }}
.form-input::placeholder {{ color:#525252; }}
.forgot {{ text-align:right; margin-bottom:1.5rem; }}
.forgot a {{ font-size:0.8rem; color:#60a5fa; text-decoration:none; }}
.forgot a:hover {{ color:#93c5fd; }}
.submit-btn {{
    width:100%; height:48px; background:rgba(20,20,24,0.9); border:1px solid rgba(255,255,255,0.15);
    border-radius:0.625rem; color:#fff; font-size:0.95rem; font-weight:600; cursor:pointer;
    font-family:inherit; display:flex; align-items:center; justify-content:center; gap:0.5rem;
    transition:all 200ms ease;
}}
.submit-btn:hover {{ background:rgba(40,40,44,0.9); transform:translateY(-1px); box-shadow:0 4px 12px rgba(0,0,0,0.3); }}
.register-link {{
    text-align:center; margin-top:1.5rem; font-size:0.85rem; color:#737373;
}}
.register-link a {{ color:#60a5fa; text-decoration:none; font-weight:500; }}
.register-link a:hover {{ color:#93c5fd; }}
</style>
</head>
<body>
<div class="bg"></div>
<div class="card">
    <div class="brand">
        <img src="{file_url(LOGO_PATH)}" alt="XelaSphere">
    </div>
    <div class="header">
        <h2>Welcome back</h2>
        <p>Sign in to continue</p>
    </div>
    <div class="social-btns">
        <a class="social-btn" title="Apple"><i class="fab fa-apple"></i></a>
        <a class="social-btn" title="Discord"><i class="fab fa-discord" style="color:#5865F2"></i></a>
        <a class="social-btn" title="Google"><i class="fab fa-google" style="color:#EA4335"></i></a>
        <a class="social-btn" title="Microsoft"><i class="fab fa-microsoft" style="color:#00A4EF"></i></a>
    </div>
    <div class="separator"><span>or</span></div>
    <div class="form-group">
        <label class="form-label">Email</label>
        <input class="form-input" type="email" placeholder="you@example.com">
    </div>
    <div class="form-group">
        <label class="form-label">Password</label>
        <input class="form-input" type="password" placeholder="Enter your password">
    </div>
    <div class="forgot"><a href="#">Forgot password?</a></div>
    <button class="submit-btn">Sign in <i class="fas fa-arrow-right" style="font-size:0.8rem"></i></button>
    <div class="register-link">Don't have an account? <a href="#">Create one</a></div>
</div>
</body>
</html>"""


ARTIFACTS_HTML = f"""<!DOCTYPE html>
<html lang="en">
<head>
{COMMON_HEAD}
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    background: #0f0f0f; color: #f4f4f5; display:flex; height:100vh; overflow:hidden;
}}
.sidebar {{
    width: 260px; background: rgba(5,5,10,0.95); border-right: 1px solid rgba(255,255,255,0.06);
    display:flex; flex-direction:column; flex-shrink:0;
}}
.sidebar-header {{
    padding: 1rem; display:flex; align-items:center; gap:0.75rem;
    border-bottom: 1px solid rgba(255,255,255,0.06);
}}
.sidebar-header img {{ width:30px; height:30px; border-radius:6px; }}
.sidebar-header span {{ font-size:14px; font-weight:600; color:#fff; font-family:'Montserrat',sans-serif; }}
.sidebar-section {{ padding:0.5rem; flex:1; }}
.section-label {{ font-size:0.7rem; text-transform:uppercase; color:#737373; padding:0.5rem 0.75rem; font-weight:600; }}
.chat-tab {{
    padding:0.6rem 0.75rem; border-radius:6px; font-size:0.85rem; color:rgba(255,255,255,0.7);
    cursor:pointer; display:flex; align-items:center; gap:0.5rem; margin-bottom:2px;
}}
.chat-tab.active {{ background:rgba(255,255,255,0.1); color:#fff; }}
.chat-tab i {{ font-size:0.75rem; color:rgba(255,255,255,0.4); }}

.main {{ flex:1; display:flex; }}

/* Chat side (narrower) */
.chat-side {{
    flex:0 0 50%; display:flex; flex-direction:column; border-right:1px solid rgba(255,255,255,0.06);
}}
.model-select {{
    padding:1rem 1.5rem; color:rgba(255,255,255,0.7); font-size:0.95rem; font-weight:500;
    display:flex; align-items:center; gap:0.4rem;
}}
.model-select .chevron {{ font-size:0.6rem; }}
.chat-area {{
    flex:1; overflow-y:auto; padding:0 1.5rem 1.5rem; display:flex; flex-direction:column; gap:1.25rem;
}}
.message {{ display:flex; gap:0.75rem; }}
.message.user {{ justify-content:flex-end; }}
.avatar {{
    width:28px; height:28px; border-radius:50%; flex-shrink:0;
    display:flex; align-items:center; justify-content:center; font-size:0.7rem; font-weight:600;
}}
.avatar.ai {{ background:linear-gradient(135deg,#3b82f6,#6366f1); color:#fff; }}
.avatar.user {{ background:#404040; color:#e4e4e7; }}
.bubble {{ border-radius:1.1rem; font-size:0.85rem; line-height:1.65; }}
.bubble.ai {{ background:#222223; padding:1rem 1.2rem; color:rgba(244,244,245,0.92); }}
.bubble.user {{
    background:#303030; padding:0.65rem 0.95rem; border-radius:0.95rem 0.95rem 0.35rem 0.95rem;
    max-width:400px; color:#f7f7f7;
}}
.bubble p {{ margin-bottom:0.5rem; }}
.bubble p:last-child {{ margin-bottom:0; }}
.bubble strong {{ color:#fff; }}
.artifact-ref {{
    display:inline-flex; align-items:center; gap:0.4rem; padding:0.4rem 0.75rem;
    background:rgba(59,130,246,0.1); border:1px solid rgba(59,130,246,0.2); border-radius:8px;
    color:#60a5fa; font-size:0.8rem; cursor:pointer; margin-top:0.5rem;
    transition: background 150ms ease;
}}
.artifact-ref:hover {{ background:rgba(59,130,246,0.15); }}
.composer-wrapper {{ padding:0.75rem 1.5rem 1rem; }}
.composer {{
    background:rgba(48,48,48,0.92); border:1px solid rgba(255,255,255,0.1);
    border-radius:24px; padding:0.5rem 0.75rem; display:flex; align-items:flex-end; gap:0.5rem;
}}
.composer-input {{
    flex:1; background:none; border:none; color:#f4f4f5; font-size:14px; font-family:inherit;
    padding:0.35rem 0.5rem; outline:none; min-height:34px;
}}
.composer-btn {{
    width:28px; height:28px; border-radius:50%; border:none; cursor:pointer;
    display:flex; align-items:center; justify-content:center; flex-shrink:0;
}}
.composer-btn.send {{ background:linear-gradient(135deg,#3b82f6,#6366f1); color:#fff; }}

/* Artifact Panel */
.artifact-panel {{
    flex:0 0 50%; display:flex; flex-direction:column; background:#1a1a1a;
}}
.panel-header {{
    height:56px; padding:0 14px; display:flex; align-items:center; justify-content:space-between;
    border-bottom:1px solid rgba(255,255,255,0.08); flex-shrink:0;
}}
.panel-title {{
    display:flex; align-items:center; gap:0.5rem; font-size:0.9rem; font-weight:600;
}}
.panel-title i {{ color:#60a5fa; }}
.panel-actions {{ display:flex; gap:0.5rem; }}
.panel-btn {{
    height:32px; padding:0 0.75rem; background:transparent; border:1px solid rgba(255,255,255,0.08);
    border-radius:8px; color:rgba(255,255,255,0.7); font-size:0.8rem; cursor:pointer;
    display:flex; align-items:center; gap:0.35rem; font-family:inherit;
    transition: all 150ms ease;
}}
.panel-btn:hover {{ background:rgba(255,255,255,0.06); border-color:rgba(255,255,255,0.15); color:#fff; }}
.panel-btn.active {{ background:rgba(59,130,246,0.15); border-color:rgba(59,130,246,0.3); color:#60a5fa; }}
.panel-close {{
    width:32px; height:32px; background:transparent; border:1px solid rgba(255,255,255,0.08);
    border-radius:8px; color:rgba(255,255,255,0.5); cursor:pointer; display:flex;
    align-items:center; justify-content:center; font-size:0.85rem;
    transition: all 150ms ease;
}}
.panel-close:hover {{ background:rgba(255,255,255,0.06); color:#fff; }}

/* File tree */
.panel-body {{ flex:1; display:flex; overflow:hidden; }}
.file-tree {{
    width:200px; background:#151515; border-right:1px solid rgba(255,255,255,0.06);
    padding:0.75rem 0; overflow-y:auto; flex-shrink:0;
}}
.tree-item {{
    padding:0.35rem 0.75rem; font-size:0.8rem; color:rgba(255,255,255,0.65); cursor:pointer;
    display:flex; align-items:center; gap:0.4rem; transition: background 100ms ease;
}}
.tree-item:hover {{ background:rgba(255,255,255,0.05); color:#fff; }}
.tree-item.active {{ background:rgba(59,130,246,0.12); color:#60a5fa; }}
.tree-item.folder {{ color:rgba(255,255,255,0.5); font-weight:500; }}
.tree-item .indent {{ width:16px; display:inline-block; }}

/* Code view */
.code-view {{
    flex:1; overflow:auto; background:#0f0f0f; padding:1rem;
    font-family:'Roboto Mono',monospace; font-size:0.8rem; line-height:1.7; color:#e2e8f0;
}}
.code-view .line {{ display:flex; }}
.code-view .line-num {{
    width:40px; text-align:right; padding-right:1rem; color:#525252; user-select:none; flex-shrink:0;
}}
.code-view .line-content {{ flex:1; }}
.kw {{ color:#c084fc; }} /* purple - keywords */
.fn {{ color:#60a5fa; }} /* blue - functions */
.str {{ color:#86efac; }} /* green - strings */
.cm {{ color:#525252; font-style:italic; }} /* gray - comments */
.num {{ color:#fbbf24; }} /* yellow - numbers */
.op {{ color:#94a3b8; }} /* gray-blue - operators */
.tag {{ color:#f472b6; }} /* pink - HTML tags */
.attr {{ color:#60a5fa; }} /* blue - attributes */
.val {{ color:#86efac; }} /* green - values */

/* Version badge */
.version-badge {{
    font-size:0.65rem; padding:0.15rem 0.4rem; background:rgba(255,255,255,0.08);
    border-radius:4px; color:#a3a3a3; margin-left:0.5rem;
}}
</style>
</head>
<body>
<div class="sidebar">
    <div class="sidebar-header">
        <img src="{file_url(LOGO_PATH)}" alt="Logo">
        <span>XelaSphere</span>
    </div>
    <div class="sidebar-section">
        <div class="section-label">Today</div>
        <div class="chat-tab active"><i class="fas fa-message"></i> Build a todo app</div>
        <div class="chat-tab"><i class="fas fa-message"></i> React hooks explained</div>
        <div class="chat-tab"><i class="fas fa-message"></i> Flask REST API</div>
    </div>
</div>
<div class="main">
    <div class="chat-side">
        <div class="model-select">
            Llama 3.3 70B <span class="chevron"><i class="fas fa-chevron-down"></i></span>
        </div>
        <div class="chat-area">
            <div class="message user">
                <div class="bubble user">Build me a modern todo app with React and TypeScript</div>
                <div class="avatar user">Y</div>
            </div>
            <div class="message">
                <div class="avatar ai"><i class="fas fa-bolt" style="font-size:0.7rem"></i></div>
                <div class="bubble ai">
                    <p>I've created a complete <strong>Todo App</strong> with React and TypeScript. It includes add, complete, delete, and filter functionality with a clean modern UI.</p>
                    <div class="artifact-ref">
                        <i class="fas fa-cube"></i> Todo App — React + TypeScript
                    </div>
                    <p style="margin-top:0.75rem"><strong>Features included:</strong></p>
                    <p>• Add/remove todos with animations<br>
                    • Mark complete with strikethrough<br>
                    • Filter: All / Active / Completed<br>
                    • Local storage persistence<br>
                    • Responsive design</p>
                </div>
            </div>
            <div class="message user">
                <div class="bubble user">Can you add drag and drop reordering?</div>
                <div class="avatar user">Y</div>
            </div>
            <div class="message">
                <div class="avatar ai"><i class="fas fa-bolt" style="font-size:0.7rem"></i></div>
                <div class="bubble ai">
                    <p>Done! I've updated the artifact to <strong>v2</strong> with drag-and-drop reordering using <code>@dnd-kit</code>.</p>
                    <div class="artifact-ref">
                        <i class="fas fa-cube"></i> Todo App v2 — with Drag & Drop
                    </div>
                </div>
            </div>
        </div>
        <div class="composer-wrapper">
            <div class="composer">
                <div class="composer-input" contenteditable="true"></div>
                <button class="composer-btn send"><i class="fas fa-arrow-up" style="font-size:0.75rem"></i></button>
            </div>
        </div>
    </div>
    <div class="artifact-panel">
        <div class="panel-header">
            <div class="panel-title">
                <i class="fas fa-cube"></i> Todo App
                <span class="version-badge">v2</span>
            </div>
            <div class="panel-actions">
                <button class="panel-btn active"><i class="fas fa-code"></i> Code</button>
                <button class="panel-btn"><i class="fas fa-eye"></i> Preview</button>
                <button class="panel-btn"><i class="fas fa-download"></i></button>
                <button class="panel-btn"><i class="fas fa-share-alt"></i></button>
                <button class="panel-close"><i class="fas fa-times"></i></button>
            </div>
        </div>
        <div class="panel-body">
            <div class="file-tree">
                <div class="tree-item folder"><i class="fas fa-folder-open" style="color:#fbbf24"></i> src</div>
                <div class="tree-item active"><span class="indent"></span><i class="fas fa-file-code" style="color:#60a5fa"></i> App.tsx</div>
                <div class="tree-item"><span class="indent"></span><i class="fas fa-file-code" style="color:#60a5fa"></i> TodoList.tsx</div>
                <div class="tree-item"><span class="indent"></span><i class="fas fa-file-code" style="color:#60a5fa"></i> TodoItem.tsx</div>
                <div class="tree-item"><span class="indent"></span><i class="fas fa-file-code" style="color:#c084fc"></i> types.ts</div>
                <div class="tree-item"><span class="indent"></span><i class="fas fa-file-code" style="color:#f472b6"></i> App.css</div>
                <div class="tree-item"><span class="indent"></span><i class="fas fa-file-code" style="color:#60a5fa"></i> hooks.ts</div>
                <div class="tree-item folder"><i class="fas fa-folder" style="color:#fbbf24"></i> public</div>
                <div class="tree-item"><i class="fas fa-file-code" style="color:#86efac"></i> package.json</div>
                <div class="tree-item"><i class="fas fa-file-code" style="color:#60a5fa"></i> tsconfig.json</div>
            </div>
            <div class="code-view">
                <div class="line"><span class="line-num">1</span><span class="line-content"><span class="kw">import</span> React, {{ useState, useCallback }} <span class="kw">from</span> <span class="str">'react'</span>;</span></div>
                <div class="line"><span class="line-num">2</span><span class="line-content"><span class="kw">import</span> {{ DndContext, closestCenter }} <span class="kw">from</span> <span class="str">'@dnd-kit/core'</span>;</span></div>
                <div class="line"><span class="line-num">3</span><span class="line-content"><span class="kw">import</span> {{ SortableContext }} <span class="kw">from</span> <span class="str">'@dnd-kit/sortable'</span>;</span></div>
                <div class="line"><span class="line-num">4</span><span class="line-content"><span class="kw">import</span> {{ TodoList }} <span class="kw">from</span> <span class="str">'./TodoList'</span>;</span></div>
                <div class="line"><span class="line-num">5</span><span class="line-content"><span class="kw">import</span> {{ useTodos }} <span class="kw">from</span> <span class="str">'./hooks'</span>;</span></div>
                <div class="line"><span class="line-num">6</span><span class="line-content"><span class="kw">import</span> <span class="str">'./App.css'</span>;</span></div>
                <div class="line"><span class="line-num">7</span><span class="line-content"></span></div>
                <div class="line"><span class="line-num">8</span><span class="line-content"><span class="kw">interface</span> <span class="fn">Todo</span> {{</span></div>
                <div class="line"><span class="line-num">9</span><span class="line-content">  id: <span class="kw">string</span>;</span></div>
                <div class="line"><span class="line-num">10</span><span class="line-content">  text: <span class="kw">string</span>;</span></div>
                <div class="line"><span class="line-num">11</span><span class="line-content">  completed: <span class="kw">boolean</span>;</span></div>
                <div class="line"><span class="line-num">12</span><span class="line-content">  createdAt: <span class="kw">Date</span>;</span></div>
                <div class="line"><span class="line-num">13</span><span class="line-content">}}</span></div>
                <div class="line"><span class="line-num">14</span><span class="line-content"></span></div>
                <div class="line"><span class="line-num">15</span><span class="line-content"><span class="kw">type</span> <span class="fn">Filter</span> = <span class="str">'all'</span> | <span class="str">'active'</span> | <span class="str">'completed'</span>;</span></div>
                <div class="line"><span class="line-num">16</span><span class="line-content"></span></div>
                <div class="line"><span class="line-num">17</span><span class="line-content"><span class="kw">export default function</span> <span class="fn">App</span>() {{</span></div>
                <div class="line"><span class="line-num">18</span><span class="line-content">  <span class="kw">const</span> [todos, setTodos] = <span class="fn">useState</span>&lt;Todo[]&gt;([]);</span></div>
                <div class="line"><span class="line-num">19</span><span class="line-content">  <span class="kw">const</span> [filter, setFilter] = <span class="fn">useState</span>&lt;Filter&gt;(<span class="str">'all'</span>);</span></div>
                <div class="line"><span class="line-num">20</span><span class="line-content">  <span class="kw">const</span> [input, setInput] = <span class="fn">useState</span>(<span class="str">''</span>);</span></div>
                <div class="line"><span class="line-num">21</span><span class="line-content"></span></div>
                <div class="line"><span class="line-num">22</span><span class="line-content">  <span class="kw">const</span> <span class="fn">addTodo</span> = <span class="fn">useCallback</span>(() => {{</span></div>
                <div class="line"><span class="line-num">23</span><span class="line-content">    <span class="kw">if</span> (!input.trim()) <span class="kw">return</span>;</span></div>
                <div class="line"><span class="line-num">24</span><span class="line-content">    <span class="fn">setTodos</span>(prev => [...prev, {{</span></div>
                <div class="line"><span class="line-num">25</span><span class="line-content">      id: <span class="fn">crypto</span>.<span class="fn">randomUUID</span>(),</span></div>
                <div class="line"><span class="line-num">26</span><span class="line-content">      text: input.<span class="fn">trim</span>(),</span></div>
                <div class="line"><span class="line-num">27</span><span class="line-content">      completed: <span class="kw">false</span>,</span></div>
                <div class="line"><span class="line-num">28</span><span class="line-content">      createdAt: <span class="kw">new</span> <span class="fn">Date</span>()</span></div>
                <div class="line"><span class="line-num">29</span><span class="line-content">    }}]);</span></div>
                <div class="line"><span class="line-num">30</span><span class="line-content">    <span class="fn">setInput</span>(<span class="str">''</span>);</span></div>
                <div class="line"><span class="line-num">31</span><span class="line-content">  }}, [input]);</span></div>
            </div>
        </div>
    </div>
</div>
</body>
</html>"""


MODEL_SELECTOR_HTML = f"""<!DOCTYPE html>
<html lang="en">
<head>
{COMMON_HEAD}
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    background: #0f0f0f; color: #f4f4f5; height:100vh; display:flex;
    align-items:flex-start; justify-content:center; padding-top:2rem;
}}
.wrapper {{ position:relative; }}
.model-btn {{
    padding:0.6rem 1rem; background:rgba(255,255,255,0.06); border:1px solid rgba(255,255,255,0.1);
    border-radius:10px; color:#fff; font-size:1rem; font-weight:500; cursor:pointer;
    display:flex; align-items:center; gap:0.5rem; font-family:inherit;
}}
.model-btn i.chevron {{ font-size:0.6rem; color:rgba(255,255,255,0.5); }}
.dropdown {{
    position:absolute; top:calc(100% + 0.5rem); left:0; min-width:280px;
    background:#2f2f2f; border:1px solid rgba(255,255,255,0.1); border-radius:12px;
    box-shadow:0 8px 24px rgba(0,0,0,0.4); padding:0.5rem 0; overflow:hidden;
}}
.dropdown-header {{
    padding:0.5rem 1rem; font-size:0.7rem; text-transform:uppercase; color:#737373;
    font-weight:600; letter-spacing:0.05em;
}}
.dropdown-item {{
    padding:0.7rem 1rem; display:flex; align-items:center; justify-content:space-between;
    cursor:pointer; transition: background 120ms ease; font-size:0.9rem;
    color:rgba(255,255,255,0.9);
}}
.dropdown-item:hover {{ background:rgba(255,255,255,0.08); }}
.dropdown-item.selected {{ color:#60a5fa; }}
.dropdown-item .check {{ color:#60a5fa; font-size:0.8rem; }}
.dropdown-item .badge {{
    font-size:0.65rem; padding:0.15rem 0.45rem; background:rgba(255,255,255,0.1);
    border-radius:4px; color:#a3a3a3; font-weight:500;
}}
.dropdown-item .badge.fast {{ background:rgba(34,197,94,0.15); color:#22c55e; }}
.dropdown-item .badge.reason {{ background:rgba(139,92,246,0.15); color:#a78bfa; }}
.dropdown-item .badge.vision {{ background:rgba(59,130,246,0.15); color:#60a5fa; }}
.dropdown-item .badge.long {{ background:rgba(245,158,11,0.15); color:#f59e0b; }}
.dropdown-divider {{ height:1px; background:rgba(255,255,255,0.08); margin:0.25rem 0; }}
.model-desc {{ font-size:0.75rem; color:#737373; margin-top:0.15rem; }}
</style>
</head>
<body>
<div class="wrapper">
    <button class="model-btn">
        <i class="fas fa-robot" style="color:#60a5fa"></i>
        Llama 3.3 70B
        <i class="fas fa-chevron-down chevron"></i>
    </button>
    <div class="dropdown">
        <div class="dropdown-header">Smart Mode</div>
        <div class="dropdown-item">
            <div><i class="fas fa-wand-magic-sparkles" style="color:#a78bfa;margin-right:0.5rem"></i> Auto <div class="model-desc">Automatically selects the best model</div></div>
            <span class="badge reason">Smart</span>
        </div>
        <div class="dropdown-divider"></div>
        <div class="dropdown-header">Available Models</div>
        <div class="dropdown-item selected">
            <div><span style="margin-right:0.5rem">🦙</span> Llama 3.3 70B</div>
            <div style="display:flex;gap:0.35rem;align-items:center">
                <span class="badge fast">Fast</span>
                <span class="check"><i class="fas fa-check"></i></span>
            </div>
        </div>
        <div class="dropdown-item">
            <div><span style="margin-right:0.5rem">🧠</span> DeepSeek R1</div>
            <span class="badge reason">Reasoning</span>
        </div>
        <div class="dropdown-item">
            <div><span style="margin-right:0.5rem">🌟</span> Qwen 3 235B</div>
            <span class="badge">128K</span>
        </div>
        <div class="dropdown-item">
            <div><span style="margin-right:0.5rem">🌙</span> Kimi (Moonshot)</div>
            <span class="badge long">262K</span>
        </div>
        <div class="dropdown-item">
            <div><span style="margin-right:0.5rem">👁️</span> Vision Model</div>
            <span class="badge vision">Vision</span>
        </div>
        <div class="dropdown-item">
            <div><span style="margin-right:0.5rem">🎨</span> Image Generation</div>
            <span class="badge vision">Image</span>
        </div>
        <div class="dropdown-divider"></div>
        <div class="dropdown-item">
            <div><span style="margin-right:0.5rem">⚡</span> Cerebras</div>
            <span class="badge fast">Fallback</span>
        </div>
    </div>
</div>
</body>
</html>"""


MOBILE_HTML = f"""<!DOCTYPE html>
<html lang="en">
<head>
{COMMON_HEAD}
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    background: #0f0f0f; color: #f4f4f5; height:100vh; display:flex; flex-direction:column;
    overflow:hidden; width:100%;
}}
.mobile-header {{
    height:56px; display:flex; align-items:center; justify-content:space-between;
    padding:0 1rem; border-bottom:1px solid rgba(255,255,255,0.06); flex-shrink:0;
}}
.mobile-header .burger {{ font-size:1.2rem; color:rgba(255,255,255,0.7); cursor:pointer; }}
.mobile-header .logo {{ display:flex; align-items:center; gap:0.5rem; }}
.mobile-header .logo img {{ width:24px; height:24px; border-radius:4px; }}
.mobile-header .logo span {{ font-size:0.9rem; font-weight:600; font-family:'Montserrat',sans-serif; }}
.mobile-header .actions {{ display:flex; gap:0.75rem; }}
.mobile-header .actions i {{ font-size:1rem; color:rgba(255,255,255,0.6); }}
.model-bar {{
    padding:0.5rem 1rem; font-size:0.85rem; color:rgba(255,255,255,0.6); font-weight:500;
    display:flex; align-items:center; gap:0.3rem;
}}
.model-bar .chevron {{ font-size:0.55rem; }}
.chat-area {{
    flex:1; overflow-y:auto; padding:0.75rem 1rem; display:flex; flex-direction:column; gap:1rem;
}}
.message {{ display:flex; gap:0.5rem; }}
.message.user {{ justify-content:flex-end; }}
.avatar {{
    width:24px; height:24px; border-radius:50%; flex-shrink:0;
    display:flex; align-items:center; justify-content:center; font-size:0.6rem; font-weight:600;
}}
.avatar.ai {{ background:linear-gradient(135deg,#3b82f6,#6366f1); color:#fff; }}
.avatar.user {{ background:#404040; color:#e4e4e7; }}
.bubble {{ border-radius:1rem; font-size:0.85rem; line-height:1.6; }}
.bubble.ai {{ background:#222223; padding:0.85rem 1rem; color:rgba(244,244,245,0.92); }}
.bubble.user {{
    background:#303030; padding:0.6rem 0.85rem; border-radius:0.85rem 0.85rem 0.3rem 0.85rem;
    color:#f7f7f7;
}}
.bubble p {{ margin-bottom:0.4rem; }}
.bubble p:last-child {{ margin-bottom:0; }}
.bubble strong {{ color:#fff; }}
.bubble code {{
    background:rgba(0,0,0,0.4); padding:0.1rem 0.3rem; border-radius:0.3rem;
    font-size:0.8em; font-family:'Roboto Mono',monospace;
}}
.bubble pre {{
    background:rgba(0,0,0,0.45); border-radius:0.5rem; padding:0.65rem 0.75rem;
    margin:0.5rem 0; font-size:0.75em; line-height:1.5; overflow-x:auto; color:#e2e8f0;
}}
.bubble pre code {{ background:none; padding:0; color:#e2e8f0; }}
.composer-wrapper {{ padding:0.5rem 0.75rem 1rem; }}
.composer {{
    background:rgba(48,48,48,0.92); border:1px solid rgba(255,255,255,0.1);
    border-radius:20px; padding:0.4rem 0.6rem; display:flex; align-items:flex-end; gap:0.4rem;
}}
.composer-input {{
    flex:1; background:none; border:none; color:#f4f4f5; font-size:16px; font-family:inherit;
    padding:0.3rem 0.4rem; outline:none; min-height:32px;
}}
.composer-btn {{
    width:28px; height:28px; border-radius:50%; border:none; cursor:pointer;
    display:flex; align-items:center; justify-content:center; flex-shrink:0;
}}
.composer-btn.send {{ background:linear-gradient(135deg,#3b82f6,#6366f1); color:#fff; }}
.composer-btn.attach {{ background:rgba(255,255,255,0.08); color:rgba(255,255,255,0.6); }}
</style>
</head>
<body>
<div class="mobile-header">
    <span class="burger"><i class="fas fa-bars"></i></span>
    <div class="logo">
        <img src="{file_url(LOGO_PATH)}" alt="Logo">
        <span>XelaSphere</span>
    </div>
    <div class="actions">
        <i class="fas fa-search"></i>
        <i class="fas fa-plus"></i>
    </div>
</div>
<div class="model-bar">
    Llama 3.3 70B <span class="chevron"><i class="fas fa-chevron-down"></i></span>
</div>
<div class="chat-area">
    <div class="message user">
        <div class="bubble user">What's the difference between let, const, and var in JavaScript?</div>
    </div>
    <div class="message">
        <div class="avatar ai"><i class="fas fa-bolt" style="font-size:0.6rem"></i></div>
        <div class="bubble ai">
            <p>Here's a quick breakdown:</p>
            <p><strong><code>var</code></strong> — Function-scoped, hoisted, can be redeclared. Legacy, avoid in modern code.</p>
            <p><strong><code>let</code></strong> — Block-scoped, not hoisted to usable state, can be reassigned but not redeclared.</p>
            <p><strong><code>const</code></strong> — Block-scoped, must be initialized, cannot be reassigned (but objects/arrays are still mutable).</p>
            <pre><code>const user = {{ name: "Alex" }};
user.name = "Sam";  // OK
user = {{}};           // Error!</code></pre>
            <p><strong>Rule of thumb:</strong> Use <code>const</code> by default, <code>let</code> when reassignment is needed, never use <code>var</code>.</p>
        </div>
    </div>
</div>
<div class="composer-wrapper">
    <div class="composer">
        <button class="composer-btn attach"><i class="fas fa-paperclip"></i></button>
        <div class="composer-input" contenteditable="true"></div>
        <button class="composer-btn send"><i class="fas fa-arrow-up" style="font-size:0.7rem"></i></button>
    </div>
</div>
</body>
</html>"""


DASHBOARD_HTML = f"""<!DOCTYPE html>
<html lang="en">
<head>
{COMMON_HEAD}
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    background: #0f0f0f; color: #f4f4f5; min-height:100vh; padding:2rem;
}}
.dashboard-header {{
    display:flex; align-items:center; justify-content:space-between;
    margin-bottom:2rem; padding-bottom:1.5rem; border-bottom:1px solid rgba(255,255,255,0.08);
}}
.dashboard-header h1 {{ font-size:1.75rem; font-weight:700; }}
.dashboard-header .user-info {{ display:flex; align-items:center; gap:0.75rem; }}
.user-avatar {{
    width:40px; height:40px; border-radius:50%; background:linear-gradient(135deg,#3b82f6,#6366f1);
    display:flex; align-items:center; justify-content:center; font-weight:600; font-size:1rem;
}}
.user-name {{ font-weight:500; }}
.user-email {{ font-size:0.8rem; color:#737373; }}
.stats-grid {{
    display:grid; grid-template-columns:repeat(4,1fr); gap:1rem; margin-bottom:2rem;
}}
.stat-card {{
    background:#1a1a1a; border:1px solid rgba(255,255,255,0.06); border-radius:12px;
    padding:1.25rem; display:flex; flex-direction:column; gap:0.5rem;
}}
.stat-card .stat-label {{ font-size:0.8rem; color:#737373; font-weight:500; }}
.stat-card .stat-value {{ font-size:1.75rem; font-weight:700; }}
.stat-card .stat-change {{
    font-size:0.75rem; display:flex; align-items:center; gap:0.25rem;
}}
.stat-card .stat-change.up {{ color:#22c55e; }}
.stat-card .stat-change.down {{ color:#ef4444; }}
.section {{ margin-bottom:2rem; }}
.section h2 {{ font-size:1.15rem; font-weight:600; margin-bottom:1rem; }}
.recent-chats {{
    background:#1a1a1a; border:1px solid rgba(255,255,255,0.06); border-radius:12px;
    overflow:hidden;
}}
.chat-row {{
    padding:0.85rem 1.25rem; display:flex; align-items:center; justify-content:space-between;
    border-bottom:1px solid rgba(255,255,255,0.04); transition:background 120ms ease;
    cursor:pointer;
}}
.chat-row:last-child {{ border-bottom:none; }}
.chat-row:hover {{ background:rgba(255,255,255,0.03); }}
.chat-row .chat-info {{ display:flex; flex-direction:column; gap:0.15rem; }}
.chat-row .chat-title {{ font-size:0.9rem; font-weight:500; }}
.chat-row .chat-preview {{ font-size:0.8rem; color:#737373; }}
.chat-row .chat-meta {{ font-size:0.75rem; color:#525252; display:flex; align-items:center; gap:0.75rem; }}
.chat-row .chat-meta i {{ font-size:0.65rem; }}
.model-usage {{
    display:grid; grid-template-columns:repeat(3,1fr); gap:1rem;
}}
.model-card {{
    background:#1a1a1a; border:1px solid rgba(255,255,255,0.06); border-radius:12px;
    padding:1rem; display:flex; align-items:center; gap:0.75rem;
}}
.model-icon {{
    width:40px; height:40px; border-radius:8px; display:flex; align-items:center;
    justify-content:center; font-size:1.25rem;
}}
.model-icon.llama {{ background:rgba(59,130,246,0.15); }}
.model-icon.deepseek {{ background:rgba(139,92,246,0.15); }}
.model-icon.qwen {{ background:rgba(245,158,11,0.15); }}
.model-card .model-info .model-name {{ font-size:0.85rem; font-weight:500; }}
.model-card .model-info .model-count {{ font-size:0.75rem; color:#737373; }}
.progress-bar {{
    height:4px; background:rgba(255,255,255,0.08); border-radius:2px; margin-top:0.5rem; overflow:hidden;
}}
.progress-bar .fill {{ height:100%; border-radius:2px; }}
.fill.blue {{ background:#3b82f6; }}
.fill.purple {{ background:#8b5cf6; }}
.fill.amber {{ background:#f59e0b; }}
</style>
</head>
<body>
<div class="dashboard-header">
    <h1>Dashboard</h1>
    <div class="user-info">
        <div>
            <div class="user-name">Youssef</div>
            <div class="user-email">youssef@xelas.io</div>
        </div>
        <div class="user-avatar">Y</div>
    </div>
</div>
<div class="stats-grid">
    <div class="stat-card">
        <span class="stat-label">Total Conversations</span>
        <span class="stat-value">247</span>
        <span class="stat-change up"><i class="fas fa-arrow-up"></i> 12% this week</span>
    </div>
    <div class="stat-card">
        <span class="stat-label">Messages Sent</span>
        <span class="stat-value">1,834</span>
        <span class="stat-change up"><i class="fas fa-arrow-up"></i> 8% this week</span>
    </div>
    <div class="stat-card">
        <span class="stat-label">Artifacts Created</span>
        <span class="stat-value">56</span>
        <span class="stat-change up"><i class="fas fa-arrow-up"></i> 23% this week</span>
    </div>
    <div class="stat-card">
        <span class="stat-label">Files Processed</span>
        <span class="stat-value">89</span>
        <span class="stat-change down"><i class="fas fa-arrow-down"></i> 3% this week</span>
    </div>
</div>
<div class="section">
    <h2>Recent Conversations</h2>
    <div class="recent-chats">
        <div class="chat-row">
            <div class="chat-info">
                <span class="chat-title">Python async patterns</span>
                <span class="chat-preview">Explained asyncio gather with aiohttp examples...</span>
            </div>
            <div class="chat-meta"><i class="fas fa-clock"></i> 2 hours ago &nbsp; <i class="fas fa-message"></i> 8 messages</div>
        </div>
        <div class="chat-row">
            <div class="chat-info">
                <span class="chat-title">React component design</span>
                <span class="chat-preview">Built a reusable modal with compound components...</span>
            </div>
            <div class="chat-meta"><i class="fas fa-clock"></i> 5 hours ago &nbsp; <i class="fas fa-message"></i> 14 messages</div>
        </div>
        <div class="chat-row">
            <div class="chat-info">
                <span class="chat-title">Docker multi-stage builds</span>
                <span class="chat-preview">Optimized Dockerfile from 1.2GB to 340MB...</span>
            </div>
            <div class="chat-meta"><i class="fas fa-clock"></i> Yesterday &nbsp; <i class="fas fa-message"></i> 6 messages</div>
        </div>
        <div class="chat-row">
            <div class="chat-info">
                <span class="chat-title">Firebase auth setup</span>
                <span class="chat-preview">Configured OAuth 2.0 with Google, Microsoft, Discord...</span>
            </div>
            <div class="chat-meta"><i class="fas fa-clock"></i> Yesterday &nbsp; <i class="fas fa-message"></i> 11 messages</div>
        </div>
        <div class="chat-row">
            <div class="chat-info">
                <span class="chat-title">API rate limiting strategy</span>
                <span class="chat-preview">Implemented sliding window with Redis backend...</span>
            </div>
            <div class="chat-meta"><i class="fas fa-clock"></i> 2 days ago &nbsp; <i class="fas fa-message"></i> 9 messages</div>
        </div>
    </div>
</div>
<div class="section">
    <h2>Model Usage</h2>
    <div class="model-usage">
        <div class="model-card">
            <div class="model-icon llama">🦙</div>
            <div class="model-info">
                <div class="model-name">Llama 3.3 70B</div>
                <div class="model-count">156 conversations</div>
                <div class="progress-bar"><div class="fill blue" style="width:63%"></div></div>
            </div>
        </div>
        <div class="model-card">
            <div class="model-icon deepseek">🧠</div>
            <div class="model-info">
                <div class="model-name">DeepSeek R1</div>
                <div class="model-count">62 conversations</div>
                <div class="progress-bar"><div class="fill purple" style="width:25%"></div></div>
            </div>
        </div>
        <div class="model-card">
            <div class="model-icon qwen">🌟</div>
            <div class="model-info">
                <div class="model-name">Qwen 3 235B</div>
                <div class="model-count">29 conversations</div>
                <div class="progress-bar"><div class="fill amber" style="width:12%"></div></div>
            </div>
        </div>
    </div>
</div>
</body>
</html>"""


FILE_UPLOAD_HTML = f"""<!DOCTYPE html>
<html lang="en">
<head>
{COMMON_HEAD}
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    background: #0f0f0f; color: #f4f4f5; min-height:100vh;
    display:flex; align-items:center; justify-content:center; padding:2rem;
}}
.upload-demo {{
    width:100%; max-width:700px;
}}
.upload-title {{ font-size:1.1rem; font-weight:600; margin-bottom:1rem; text-align:center; }}
/* Composer with attachments */
.composer-wrapper {{ margin-bottom:2rem; }}
.attachments {{
    display:flex; gap:0.5rem; padding:0.5rem 0.75rem; flex-wrap:wrap;
    background:rgba(48,48,48,0.5); border-radius:16px 16px 0 0; border:1px solid rgba(255,255,255,0.08);
    border-bottom:none;
}}
.attachment {{
    display:flex; align-items:center; gap:0.5rem; padding:0.4rem 0.65rem;
    background:rgba(255,255,255,0.06); border:1px solid rgba(255,255,255,0.1);
    border-radius:8px; font-size:0.8rem;
}}
.attachment i.file-icon {{ color:#60a5fa; }}
.attachment i.file-pdf {{ color:#ef4444; }}
.attachment i.file-img {{ color:#22c55e; }}
.attachment i.file-doc {{ color:#3b82f6; }}
.attachment .file-name {{ color:rgba(255,255,255,0.8); max-width:120px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
.attachment .file-size {{ color:#737373; font-size:0.7rem; }}
.attachment .remove {{
    color:rgba(255,255,255,0.4); cursor:pointer; font-size:0.7rem;
    margin-left:0.25rem;
}}
.attachment .remove:hover {{ color:#ef4444; }}
.composer {{
    background:rgba(48,48,48,0.92); border:1px solid rgba(255,255,255,0.1);
    border-radius:0 0 20px 20px; padding:0.5rem 0.75rem; display:flex; align-items:flex-end; gap:0.5rem;
}}
.composer.no-attach {{ border-radius:24px; }}
.composer-input {{
    flex:1; background:none; border:none; color:#f4f4f5; font-size:15px; font-family:inherit;
    padding:0.35rem 0.5rem; outline:none; min-height:34px;
}}
.composer-btn {{
    width:30px; height:30px; border-radius:50%; border:none; cursor:pointer;
    display:flex; align-items:center; justify-content:center; flex-shrink:0;
}}
.composer-btn.send {{ background:linear-gradient(135deg,#3b82f6,#6366f1); color:#fff; }}
.composer-btn.attach {{ background:rgba(255,255,255,0.08); color:rgba(255,255,255,0.6); }}

/* Supported formats grid */
.formats-title {{ font-size:0.9rem; font-weight:600; margin-bottom:0.75rem; text-align:center; }}
.formats-grid {{
    display:grid; grid-template-columns:repeat(3,1fr); gap:0.75rem;
}}
.format-card {{
    background:#1a1a1a; border:1px solid rgba(255,255,255,0.06); border-radius:10px;
    padding:0.85rem; display:flex; align-items:center; gap:0.65rem;
}}
.format-icon {{
    width:36px; height:36px; border-radius:8px; display:flex; align-items:center;
    justify-content:center; font-size:1rem; flex-shrink:0;
}}
.format-icon.pdf {{ background:rgba(239,68,68,0.15); color:#ef4444; }}
.format-icon.img {{ background:rgba(34,197,94,0.15); color:#22c55e; }}
.format-icon.doc {{ background:rgba(59,130,246,0.15); color:#3b82f6; }}
.format-icon.xls {{ background:rgba(34,197,94,0.15); color:#22c55e; }}
.format-icon.ppt {{ background:rgba(245,158,11,0.15); color:#f59e0b; }}
.format-icon.txt {{ background:rgba(255,255,255,0.08); color:#a3a3a3; }}
.format-info .format-name {{ font-size:0.8rem; font-weight:500; }}
.format-info .format-ext {{ font-size:0.7rem; color:#737373; }}
.format-info .format-feat {{ font-size:0.65rem; color:#525252; margin-top:0.1rem; }}
.size-limit {{
    text-align:center; margin-top:1rem; font-size:0.75rem; color:#525252;
    display:flex; align-items:center; justify-content:center; gap:0.4rem;
}}
</style>
</head>
<body>
<div class="upload-demo">
    <div class="upload-title">File Upload & Processing</div>
    <div class="composer-wrapper">
        <div class="attachments">
            <div class="attachment">
                <i class="fas fa-file-pdf file-pdf"></i>
                <span class="file-name">architecture.pdf</span>
                <span class="file-size">2.4 MB</span>
                <span class="remove"><i class="fas fa-times"></i></span>
            </div>
            <div class="attachment">
                <i class="fas fa-file-image file-img"></i>
                <span class="file-name">diagram.png</span>
                <span class="file-size">840 KB</span>
                <span class="remove"><i class="fas fa-times"></i></span>
            </div>
            <div class="attachment">
                <i class="fas fa-file-word file-doc"></i>
                <span class="file-name">requirements.docx</span>
                <span class="file-size">156 KB</span>
                <span class="remove"><i class="fas fa-times"></i></span>
            </div>
        </div>
        <div class="composer">
            <button class="composer-btn attach"><i class="fas fa-paperclip"></i></button>
            <div class="composer-input" style="color:rgba(255,255,255,0.35)">Analyze these documents and summarize...</div>
            <button class="composer-btn send"><i class="fas fa-arrow-up" style="font-size:0.8rem"></i></button>
        </div>
    </div>

    <div class="formats-title">Supported Formats</div>
    <div class="formats-grid">
        <div class="format-card">
            <div class="format-icon pdf"><i class="fas fa-file-pdf"></i></div>
            <div class="format-info">
                <div class="format-name">PDF Documents</div>
                <div class="format-ext">.pdf</div>
                <div class="format-feat">OCR for scanned pages</div>
            </div>
        </div>
        <div class="format-card">
            <div class="format-icon img"><i class="fas fa-file-image"></i></div>
            <div class="format-info">
                <div class="format-name">Images</div>
                <div class="format-ext">.jpg, .png</div>
                <div class="format-feat">Tesseract + EasyOCR</div>
            </div>
        </div>
        <div class="format-card">
            <div class="format-icon doc"><i class="fas fa-file-word"></i></div>
            <div class="format-info">
                <div class="format-name">Word Documents</div>
                <div class="format-ext">.docx</div>
                <div class="format-feat">Text + table extraction</div>
            </div>
        </div>
        <div class="format-card">
            <div class="format-icon xls"><i class="fas fa-file-excel"></i></div>
            <div class="format-info">
                <div class="format-name">Spreadsheets</div>
                <div class="format-ext">.xlsx</div>
                <div class="format-feat">Multi-sheet parsing</div>
            </div>
        </div>
        <div class="format-card">
            <div class="format-icon ppt"><i class="fas fa-file-powerpoint"></i></div>
            <div class="format-info">
                <div class="format-name">Presentations</div>
                <div class="format-ext">.pptx</div>
                <div class="format-feat">Slide text + notes</div>
            </div>
        </div>
        <div class="format-card">
            <div class="format-icon txt"><i class="fas fa-file-alt"></i></div>
            <div class="format-info">
                <div class="format-name">Text Files</div>
                <div class="format-ext">.txt, .rtf, .csv</div>
                <div class="format-feat">Plain text parsing</div>
            </div>
        </div>
    </div>
    <div class="size-limit"><i class="fas fa-shield-alt"></i> 16MB max • Magic byte validation • Multi-layer security</div>
</div>
</body>
</html>"""


MEMORY_HTML = f"""<!DOCTYPE html>
<html lang="en">
<head>
{COMMON_HEAD}
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    background: #0f0f0f; color: #f4f4f5; min-height:100vh;
    display:flex; align-items:center; justify-content:center; padding:2rem;
}}
.memory-demo {{ width:100%; max-width:600px; }}
.memory-title {{
    font-size:1.2rem; font-weight:700; margin-bottom:0.5rem; text-align:center;
    display:flex; align-items:center; justify-content:center; gap:0.5rem;
}}
.memory-title i {{ color:#a78bfa; }}
.memory-subtitle {{ font-size:0.85rem; color:#737373; text-align:center; margin-bottom:1.5rem; }}
.memory-list {{ display:flex; flex-direction:column; gap:0.75rem; }}
.memory-item {{
    background:#1a1a1a; border:1px solid rgba(255,255,255,0.06); border-radius:12px;
    padding:1rem 1.25rem; position:relative;
}}
.memory-item .memory-type {{
    font-size:0.65rem; text-transform:uppercase; font-weight:600; letter-spacing:0.05em;
    margin-bottom:0.35rem; display:flex; align-items:center; gap:0.35rem;
}}
.memory-type.preference {{ color:#60a5fa; }}
.memory-type.profile {{ color:#22c55e; }}
.memory-type.project {{ color:#f59e0b; }}
.memory-type.style {{ color:#a78bfa; }}
.memory-item .memory-content {{ font-size:0.85rem; line-height:1.5; color:rgba(255,255,255,0.85); }}
.memory-item .memory-meta {{
    margin-top:0.5rem; display:flex; align-items:center; justify-content:space-between;
    font-size:0.7rem; color:#525252;
}}
.confidence {{
    display:flex; align-items:center; gap:0.3rem;
}}
.confidence-bar {{
    width:50px; height:4px; background:rgba(255,255,255,0.08); border-radius:2px; overflow:hidden;
}}
.confidence-fill {{ height:100%; border-radius:2px; }}
.confidence-fill.high {{ background:#22c55e; }}
.confidence-fill.med {{ background:#f59e0b; }}
.status-badge {{
    font-size:0.6rem; padding:0.1rem 0.35rem; border-radius:4px; text-transform:uppercase;
    font-weight:600;
}}
.status-badge.active {{ background:rgba(34,197,94,0.15); color:#22c55e; }}
.status-badge.provisional {{ background:rgba(245,158,11,0.15); color:#f59e0b; }}
.safeguards {{
    margin-top:1.5rem; background:#1a1a1a; border:1px solid rgba(255,255,255,0.06);
    border-radius:12px; padding:1rem 1.25rem;
}}
.safeguards-title {{ font-size:0.85rem; font-weight:600; margin-bottom:0.5rem; display:flex; align-items:center; gap:0.4rem; }}
.safeguards-title i {{ color:#ef4444; font-size:0.75rem; }}
.safeguards-list {{ font-size:0.75rem; color:#737373; display:flex; flex-wrap:wrap; gap:0.5rem; }}
.safeguard-tag {{
    padding:0.2rem 0.5rem; background:rgba(239,68,68,0.08); border:1px solid rgba(239,68,68,0.15);
    border-radius:6px; color:rgba(239,68,68,0.8); display:flex; align-items:center; gap:0.25rem;
}}
.safeguard-tag i {{ font-size:0.6rem; }}
</style>
</head>
<body>
<div class="memory-demo">
    <div class="memory-title"><i class="fas fa-brain"></i> Memory System</div>
    <div class="memory-subtitle">Automatically learns and remembers context across conversations</div>
    <div class="memory-list">
        <div class="memory-item">
            <div class="memory-type preference"><i class="fas fa-sliders-h"></i> PREFERENCE</div>
            <div class="memory-content">Prefers TypeScript over JavaScript for all new projects. Uses strict mode with no-any rule.</div>
            <div class="memory-meta">
                <div class="confidence">
                    Confidence: 0.92
                    <div class="confidence-bar"><div class="confidence-fill high" style="width:92%"></div></div>
                </div>
                <span class="status-badge active">Active</span>
            </div>
        </div>
        <div class="memory-item">
            <div class="memory-type profile"><i class="fas fa-user"></i> PROFILE</div>
            <div class="memory-content">Full-stack developer working on XelaSphere. Primary languages: Python, TypeScript. Uses Flask and React.</div>
            <div class="memory-meta">
                <div class="confidence">
                    Confidence: 0.88
                    <div class="confidence-bar"><div class="confidence-fill high" style="width:88%"></div></div>
                </div>
                <span class="status-badge active">Active</span>
            </div>
        </div>
        <div class="memory-item">
            <div class="memory-type project"><i class="fas fa-folder-open"></i> PROJECT</div>
            <div class="memory-content">XelaSphere uses Firebase Firestore as the primary database with multi-tenant architecture per user.</div>
            <div class="memory-meta">
                <div class="confidence">
                    Confidence: 0.85
                    <div class="confidence-bar"><div class="confidence-fill high" style="width:85%"></div></div>
                </div>
                <span class="status-badge active">Active</span>
            </div>
        </div>
        <div class="memory-item">
            <div class="memory-type style"><i class="fas fa-palette"></i> COMMUNICATION</div>
            <div class="memory-content">Prefers concise, code-first responses. Likes examples before explanations.</div>
            <div class="memory-meta">
                <div class="confidence">
                    Confidence: 0.71
                    <div class="confidence-bar"><div class="confidence-fill med" style="width:71%"></div></div>
                </div>
                <span class="status-badge provisional">Provisional</span>
            </div>
        </div>
    </div>
    <div class="safeguards">
        <div class="safeguards-title"><i class="fas fa-shield-alt"></i> Privacy Safeguards</div>
        <div class="safeguards-list">
            <span class="safeguard-tag"><i class="fas fa-ban"></i> No passwords</span>
            <span class="safeguard-tag"><i class="fas fa-ban"></i> No API keys</span>
            <span class="safeguard-tag"><i class="fas fa-ban"></i> No health data</span>
            <span class="safeguard-tag"><i class="fas fa-ban"></i> No financial info</span>
            <span class="safeguard-tag"><i class="fas fa-ban"></i> Injection detection</span>
            <span class="safeguard-tag"><i class="fas fa-ban"></i> 30-day soft delete</span>
        </div>
    </div>
</div>
</body>
</html>"""


async def main():
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    # Copy logo
    import shutil
    if LOGO_PATH.exists():
        shutil.copy2(LOGO_PATH, SCREENSHOTS_DIR / "logo.png")
        print(f"Copied logo to {SCREENSHOTS_DIR / 'logo.png'}")

    pages = {
        "chat-dark": (DARK_CHAT_HTML, 1440, 900, None),
        "chat-light": (LIGHT_CHAT_HTML, 1440, 900, None),
        "login": (LOGIN_HTML, 1440, 900, None),
        "artifacts-panel": (ARTIFACTS_HTML, 1440, 900, None),
        "model-selector": (MODEL_SELECTOR_HTML, 500, 550, None),
        "mobile-view": (MOBILE_HTML, 390, 844, None),
        "dashboard": (DASHBOARD_HTML, 1440, 900, None),
        "file-upload": (FILE_UPLOAD_HTML, 800, 650, None),
        "memory-system": (MEMORY_HTML, 700, 750, None),
    }

    # Write HTML files
    html_dir = SCREENSHOTS_DIR / "html"
    html_dir.mkdir(exist_ok=True)

    for name, (html, w, h, _) in pages.items():
        html_path = html_dir / f"{name}.html"
        html_path.write_text(html)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
        )

        for name, (html, w, h, _) in pages.items():
            html_path = html_dir / f"{name}.html"
            context = await browser.new_context(
                viewport={"width": w, "height": h},
                device_scale_factor=2,  # Retina quality
            )
            page = await context.new_page()
            await page.goto(f"file://{html_path.resolve()}")
            await page.wait_for_timeout(1500)  # Wait for fonts to load

            screenshot_path = SCREENSHOTS_DIR / f"{name}.png"
            await page.screenshot(path=str(screenshot_path), full_page=False)
            print(f"Screenshot saved: {screenshot_path}")

            await context.close()

        await browser.close()

    print(f"\nAll screenshots saved to {SCREENSHOTS_DIR}")


if __name__ == "__main__":
    asyncio.run(main())
