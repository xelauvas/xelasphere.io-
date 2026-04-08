# Web Search Overhaul — Design Spec

**Date:** 2026-04-08
**Status:** Approved
**Problem:** Search returns shallow snippet-only results from a single backend (Brave), with regex-based detection, no caching, no streaming, no rich media. The result is slow, text-only answers that feel nothing like modern search (ChatGPT, Perplexity).

---

## Summary

Replace the entire search infrastructure with a multi-backend, ACTION-protocol-driven system following claude-code's architecture:

- **ACTION: web_search** — model decides when to search, not regex
- **Three backends** — Brave Web + Brave Images + YouTube Data API
- **Full page fetching** — HTML->Markdown pipeline for top 3 results
- **2-tier LRU cache** — URL cache (15min) + domain cache (5min)
- **Progressive SSE streaming** — results appear as they arrive
- **Rich inline UI** — image carousel, video embed, source cards with citations
- **Inline citations** — model references [1][2][3], linked to source cards

Delete: `searchAutoDetect.js`, `should_auto_enable_search()`, sidebar source panel, `enable_search` checkbox, TF-IDF scoring.

---

## Section 1: ACTION Protocol Extension

### Multi-Line Header Format

```
ACTION: web_search
query: what's the weather in London today
search_type: general
reason: user requested real-time weather data
embed_video: dQw4w9WgXcQ
```

### Fields

| Field | Required | Values | Description |
|-------|----------|--------|-------------|
| `query` | yes | string | Search query. Model resolves all pronouns from context. |
| `search_type` | no | `general` (default), `images`, `video`, `news` | Routes to appropriate backends |
| `reason` | yes | string | Why search is needed. Logged server-side only, never shown to user. Forces model to justify the decision. |
| `embed_video` | no | YouTube video ID | Model picks the most relevant video for inline embed. Not position-based. |

### Parsing

Reuses the existing `StreamingActionBuffer` multi-line header mode built for `generate_image`. Same 3-second timeout, same blank-line terminator, same key-value regex.

`ActionParser.parse()` extracts `search_payload: {query, search_type, reason, embed_video}` from header fields.

### What Gets Deleted

The model decides search intent. No regex, no checkbox:
- `searchAutoDetect.js` — entire file
- `should_auto_enable_search()` — in `search_client.py`
- `#enable-search` checkbox — in `index.html`
- `enable_search` form param — in `chat_routes.py`
- `searchEnabled` append — in `codeWindow.js`

---

## Section 2: Backend Architecture — SearchOrchestrator

### File: `app/services/search_orchestrator.py`

### Flow

```
Query → LRU Cache check
            ↓
      (hit) → return cached results immediately
      (miss) ↓
    ┌────────┼────────┐
    ↓        ↓        ↓
Brave Web  Brave     YouTube
  API      Images    Data API
    ↓        ↓        ↓
    └────────┼────────┘
             ↓
       Top 3 URLs → WebFetcher (2s deadline)
             ↓
       Merge + Score + Rank
             ↓
       Cache results → Stream to frontend
```

**Cache goes BEFORE API calls.** On cache hit, the entire search resolves in <50ms.

### Three Backends

| Backend | API Endpoint | Returns | Triggered When |
|---------|-------------|---------|---------------|
| Brave Web | `api.search.brave.com/res/v1/web/search` | title, url, snippet, favicon, age | `general`, `news` |
| Brave Images | `api.search.brave.com/res/v1/images/search` | thumbnail_url, source_url, title, dimensions | `general`, `images` |
| YouTube Data | `googleapis.com/youtube/v3/search` + `v3/videos` | video_id, title, channel, thumbnail, duration | `general`, `video` |

### Routing by search_type

- `general` — all three fire in parallel via `asyncio.gather()`
- `images` — Brave Images only
- `video` — YouTube only
- `news` — Brave Web with `&freshness=pd` (past day)

### YouTube Duration

`youtube/v3/search` does not return duration. A second call to `youtube/v3/videos?part=contentDetails` is made for the top 5 video results. `duration` is optional in the dataclass — populated when the second call succeeds, `None` otherwise.

### Result Dataclass

```python
@dataclass
class SearchResult:
    type: str              # 'web' | 'image' | 'video'
    title: str
    url: str
    snippet: str           # empty for images
    favicon: str           # Google favicon service URL
    thumbnail: str         # og:image for web, image URL for images, YT thumbnail for video
    full_content: str      # markdown from WebFetcher (web only, top 3)
    video_id: str          # YouTube ID (video only)
    channel: str           # YouTube channel name (video only)
    duration: Optional[str]# "5:32" (video only, requires second API call)
    source_domain: str
    relevance_score: float
```

### Relevance Scoring

No TF-IDF. Simple, honest scoring based on what the APIs actually provide:

- **Web:** `1.0 - (position * 0.1)` x freshness multiplier (1.2 for <24h, 1.1 for <7d, 1.0 otherwise)
- **Images:** `1.0 - (position * 0.1)` (position only)
- **Video:** `1.0 - (position * 0.1)` x view count signal (`1.0 + min(log10(views), 2) * 0.1`)

---

## Section 3: WebFetcher — Full Page Content Pipeline

### File: `app/services/web_fetcher.py`

### Pipeline

```
URL → Domain cache check → Fetch (requests) → Content-Type route
                                                     ↓
                                           HTML → markdownify → Markdown
                                           PDF  → text extraction
                                           Other → skip
                                                     ↓
                                           Extract og:image + og:title
                                                     ↓
                                           Truncate to model budget
                                                     ↓
                                           Cache in URL LRU
```

### Fetching

- Library: `requests` (Python)
- Timeout: 10 seconds
- User-Agent: `XelaSphere/1.0 (Search Bot)`
- Max content: 5MB download limit
- HTTP->HTTPS auto-upgrade
- Max 5 redirects, same-origin only

### HTML->Markdown

- Library: `markdownify` (Python equivalent of Turndown)
- Strips: nav, footer, sidebar, ads, script, style, cookie banners
- Extracts before stripping: `<og:image>`, `<og:title>`, `<meta description>`
- Fallback thumbnail: first `<img>` in article body with `width > 200`

### Context Budget Per Model

```python
SEARCH_CONTEXT_BUDGET = {
    'deepseek': 30_000,
    'llama': 20_000,
    'gpt-4': 60_000,
    'kimi': 80_000,
    'qwen': 40_000,
    'default': 40_000,
}
```

WebFetcher truncates to `budget // 3` per page. Llama = 6.6K per page, 20K total. DeepSeek = 10K per page, 30K total.

### LRU Cache (2-Tier)

| Cache | Key | TTL | Max Entries | Est. Memory |
|-------|-----|-----|-------------|-------------|
| URL Cache | full URL | 15 min | 200 | ~4MB per worker |
| Domain Cache | hostname | 5 min | 128 | negligible |

```python
from cachetools import TTLCache

url_cache = TTLCache(maxsize=200, ttl=900)
domain_cache = TTLCache(maxsize=128, ttl=300)
```

### Preapproved Domains

Skip domain check, fetch immediately:
- Wikipedia, BBC, Reuters, NYT, Guardian, TechCrunch, GitHub, StackOverflow, MDN, Python docs, etc.
- Configurable: `Config.SEARCH_PREAPPROVED_DOMAINS`

### og:image Extraction

- Primary: `<meta property="og:image">`
- Fallback: first `<img>` in article body with `width > 200`
- Blocklist: configurable domains known to have bad og:images

### Error Handling

- Fetch timeout -> skip URL, use snippet instead
- Domain blocked -> skip, use snippet
- Content too large -> truncate with `[Content truncated...]`
- All fetches fail -> fall back to snippets only (never fail the whole search)

---

## Section 4: SSE Streaming — Progressive Search Results

### SSE Event Types

| Event | When | Payload |
|-------|------|---------|
| `search_started` | ACTION: web_search parsed | `{query, search_type, reason}` |
| `search_web_results` | Brave Web returns | `{results: [{title, url, snippet, favicon}]}` |
| `search_images` | Brave Images returns | `{images: [{url, thumbnail, title, source}]}` |
| `search_videos` | YouTube returns | `{videos: [{video_id, title, channel, thumbnail, duration?}]}` |
| `search_content` | WebFetcher completes a page | `{url}` |
| `search_complete` | All backends done | `{total_results, cached}` |

### Timeline (general search)

```
0.0s  "Searching: weather in London..."        <- search_started
0.3s  [5 web result cards appear]              <- search_web_results
0.5s  [4 image thumbnails appear]              <- search_images
0.8s  [3 video cards appear]                   <- search_videos
1.5s  [source 1 card gets checkmark]           <- search_content
2.0s  FETCH_DEADLINE_MS reached                <- start AI call
2.0s  "Search complete"                        <- search_complete
2.0s  [AI starts streaming with citations]     <- content chunks
```

### Fetch Deadline

`FETCH_DEADLINE_MS = 2000`

Orchestrator fires all 3 page fetches concurrently via `asyncio.wait(tasks, timeout=2.0)`. Whatever completed by the deadline gets full markdown injected. Remaining URLs fall back to snippet-only. Hard gate, no ambiguity.

### Cache Hit Fast Path

If the query is cached: `search_started` -> `search_complete` in <50ms. All results from cache, no "Searching..." animation.

### Backend Flow

```python
# In generate() streaming function:
if action_buffer.action == 'web_search':
    query = action_buffer.header_fields['query']
    search_type = action_buffer.header_fields.get('search_type', 'general')
    reason = action_buffer.header_fields.get('reason', '')

    yield sse('search_started', {'query': query, 'search_type': search_type})

    # Orchestrator yields results progressively
    for event in search_orchestrator.search_progressive(query, search_type, model):
        yield sse(event.type, event.data)

    # Full content injected into prompt context (server-side)
    # AI response streams with citations
```

---

## Section 5: Frontend — Rich Search Results UI

### Layout

Results render **inline in the chat message**, not in the sidebar. Sidebar source panel is removed.

```
+---------------------------------------------+
| Searching: "weather in London today"         |  <- search_started
+---------------------------------------------+
         | (replaced by results)
+---------------------------------------------+
| [img] [img] [img] [img]    <- scroll        |  <- image carousel
+---------------------------------------------+
| > [YouTube Embed - model selected]           |  <- embed_video
|   "London Weather Forecast Today"            |
|   WeatherChannel . 125K views                |
+---------------------------------------------+
| The weather in London today is partly        |
| cloudy with a high of 18C [1]. Rain is       |  <- AI response with
| expected this evening [2], with winds        |     inline citations
| from the southwest at 15 mph [3].            |
+---------------------------------------------+
| Sources                                      |
| [1] BBC Weather . bbc.co.uk/weather     [x]  |  <- source cards
| [2] Met Office . metoffice.gov.uk        [x]  |     [x] = full content
| [3] Weather.com . weather.com                |     fetched
+---------------------------------------------+
| More videos                                  |
| [thumb]    [thumb]    [thumb]                |  <- video cards
| 5:32       12:01      3:45                   |
| Title...   Title...   Title...               |
+---------------------------------------------+
```

### Components

| Component | SSE Trigger | Behavior |
|-----------|------------|----------|
| Search banner | `search_started` | Query + spinner, replaced when results arrive |
| Image carousel | `search_images` | Horizontal CSS scroll, 4 visible, click opens lightbox |
| Video embed | `done` with `embed_video` | YouTube iframe, model-selected, lazy-loaded |
| AI response | `content` chunks | Markdown with [1][2][3] citation links |
| Source cards | `done` with results | Favicon + title + domain + checkmark badge |
| Video cards | `search_videos` | Thumbnail + duration + title + channel, click opens YT |

### Image Content Guard

Server-side filter before yielding `search_images`:
```python
images = [img for img in raw_images
          if img['source_domain'] not in BLOCKED_IMAGE_DOMAINS
          and img['thumbnail'].startswith('https://')]
```

Client-side: every `<img>` in carousel gets `onerror="this.parentElement.remove()"`.

### Citation Linking

Click `[1]` in AI text -> highlights corresponding source card, scrolls to it.

**Before cards exist (during streaming):** If source card isn't in DOM yet when citation is clicked, scroll to bottom + register `pendingHighlight`. When `done` event renders cards, auto-highlight and pulse the pending card.

Source card click opens URL in new tab.

### Image Carousel

CSS-only: `overflow-x: auto` + `scroll-snap-type`. No JS carousel library. Click opens lightbox overlay.

### Responsive

- Mobile: image carousel shows 2 visible, video cards stack vertically, embed 100% width.

### New Files

| File | Purpose |
|------|---------|
| `app/static/js/searchResults.js` | Inline search result components |
| `app/static/css/search-results.css` | Styles for all search UI |

---

## Section 6: What Gets Deleted

### Old Code Removal

| File | What | Why |
|------|------|-----|
| `app/static/js/searchAutoDetect.js` | Entire file | Regex detection replaced by model ACTION |
| `app/search_client.py` | `should_auto_enable_search()` | Model decides, not regex |
| `app/search_client.py` | `SearchClient.search()` / `search_async()` | Replaced by SearchOrchestrator |
| `app/search_client.py` | `perform_web_search()` | Replaced by orchestrator |
| `app/search_client.py` | TF-IDF scoring (`_score_results()`) | Replaced by position x freshness |
| `app/blueprints/chat_routes.py` | `enable_search` form param handling | No checkbox, model decides |
| `app/blueprints/chat_routes.py` | Search result injection (~lines 2121-2131) | Orchestrator handles injection |
| `app/templates/index.html` | `#enable-search` checkbox + `.search-btn` | No user toggle |
| `app/static/js/codeWindow.js` | `populateRightSidebar()` search results | Replaced by inline results |
| `app/static/js/codeWindow.js` | `searchEnabled` form append | No checkbox to read |
| `app/static/js/codeWindow.js:4085-4086` | `window.attachSourcesToMessage()` call | Replaced by inline cards |
| `app/static/js/speechSynthesisModule.js:158` | `renderSourcesButton()` definition | Replaced by inline cards |
| `app/static/js/speechSynthesisModule.js:710` | `renderSourcesButton()` call | Replaced by inline cards |
| `app/static/js/speechSynthesisModule.js:715-750` | `attachSourcesToMessage()` + global export | Replaced by inline cards |

### What Stays (Extended)

| Component | Why |
|-----------|-----|
| `SearchResult` dataclass | Extended with new fields |
| `SearchBackend` enum | Extended with `BRAVE_IMAGES`, `YOUTUBE` |
| `BRAVE_SEARCH_API_KEY` config | Same key for web + images |
| `search_results` in chat history | Same field, richer data |

### New Files

| File | Purpose |
|------|---------|
| `app/services/search_orchestrator.py` | Multi-backend coordination + progressive streaming |
| `app/services/web_fetcher.py` | Full page fetch + HTML->Markdown + cache |
| `app/static/js/searchResults.js` | Inline search result components |
| `app/static/css/search-results.css` | Search UI styles |

### System Prompt Addition

```
ACTION: web_search
  query: <search query, resolve all pronouns from context>
  search_type: general | images | video | news
  reason: <why search is needed, logged server-side only>
  embed_video: <YouTube video ID> (optional, model picks most relevant)

  Use when: user asks about current events, real-time data, facts you're
  not confident about, prices, weather, news, "search for", "look up",
  or any question where up-to-date information improves your answer.

  NEVER use web_search for:
  - Questions you can answer confidently from training data
  - Creative tasks (writing, coding, brainstorming)
  - Explaining concepts you know well
  - Conversational responses ("how are you", "thank you")

  After search results are injected, you MUST cite sources inline using
  [1], [2], [3] notation. Every factual claim from search results must
  have a citation.
```

---

## Migration Order

1. Delete old detection code (safe — search stops auto-triggering, /search button still works if added)
2. Build SearchOrchestrator + WebFetcher + cache
3. Add ACTION: web_search to StreamingActionBuffer + ActionParser + ParsedAction
4. Add SSE event types and progressive streaming in chat_routes.py
5. Build frontend components (searchResults.js, search-results.css)
6. Add to system prompt (feature flag — activates the new behavior)
7. Verify: current events, image queries, video queries, cache hits, citation linking, error fallbacks

---

## Config Requirements

```python
# Existing
BRAVE_SEARCH_API_KEY = ...  # Already configured

# New
YOUTUBE_API_KEY = ...       # Free, 10K queries/day
SEARCH_CONTEXT_BUDGET = {
    'deepseek': 30_000,
    'llama': 20_000,
    'gpt-4': 60_000,
    'kimi': 80_000,
    'qwen': 40_000,
    'default': 40_000,
}
SEARCH_PREAPPROVED_DOMAINS = [
    'wikipedia.org', 'bbc.com', 'bbc.co.uk', 'reuters.com',
    'nytimes.com', 'theguardian.com', 'techcrunch.com',
    'github.com', 'stackoverflow.com', 'developer.mozilla.org',
    'docs.python.org', 'arxiv.org', 'weather.com',
]
BLOCKED_IMAGE_DOMAINS = [
    # Populated as needed
]
FETCH_DEADLINE_MS = 2000
```
