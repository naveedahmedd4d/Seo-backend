import httpx
import asyncio
import re
from typing import Optional, Dict, Any, List, Tuple
from app.config import settings
from app.services.http_ssl import httpx_verify
import time

# Simple rate limiting - reduced interval for faster processing
_last_request_time = 0
_min_request_interval = 0.1  # Reduced from 0.5 to 0.1 seconds for faster processing

# Default Groq model for general completions (all former OpenRouter traffic uses Groq)
BLOG_GENERATION_MODEL_GROQ = "llama-3.3-70b-versatile"
KEYWORD_CATEGORIZATION_MODEL_GROQ = "meta-llama/llama-4-scout-17b-16e-instruct"


async def _rate_limit():
    """Simple rate limiting"""
    global _last_request_time
    current_time = time.time()
    time_since_last = current_time - _last_request_time
    if time_since_last < _min_request_interval:
        await asyncio.sleep(_min_request_interval - time_since_last)
    _last_request_time = time.time()


def _blog_delay_seconds() -> float:
    """Seconds to wait between blog LLM calls to stay under Groq TPM limit."""
    return getattr(settings, "groq_blog_delay_seconds", 4.0)


async def _blog_delay():
    """Wait between batched blog-generation steps to avoid 429."""
    delay = _blog_delay_seconds()
    if delay > 0:
        await asyncio.sleep(delay)


def _parse_groq_429_retry_seconds(error_message: str) -> float:
    """Parse 'try again in X.XXs' from Groq 429 message; return 0 if not found."""
    if not error_message:
        return 0.0
    m = re.search(r"try again in ([\d.]+)s", error_message, re.I)
    if m:
        try:
            return max(1.0, min(60.0, float(m.group(1)) + 0.5))
        except ValueError:
            pass
    return _blog_delay_seconds() * 2


async def _groq_chat_completion(
    messages: List[Dict[str, str]],
    model: str,
    max_tokens: int = 2000,
    temperature: float = 0.7,
    top_p: Optional[float] = None,
    frequency_penalty: Optional[float] = None,
    presence_penalty: Optional[float] = None,
    timeout_seconds: float = 120.0,
) -> str:
    """Call Groq chat completions API with retries on 429."""
    if not settings.groq_api_key:
        raise ValueError("Groq API key not configured")

    await _rate_limit()

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.groq_api_key}",
        "Content-Type": "application/json",
    }
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if top_p is not None:
        payload["top_p"] = top_p
    if frequency_penalty is not None:
        payload["frequency_penalty"] = frequency_penalty
    if presence_penalty is not None:
        payload["presence_penalty"] = presence_penalty

    last_error: Optional[str] = None
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(
                timeout=timeout_seconds,
                verify=httpx_verify(),
            ) as client:
                response = await client.post(url, headers=headers, json=payload)
                if response.status_code == 429:
                    error_data = response.text
                    try:
                        error_json = response.json()
                        error_msg = error_json.get("error", {}).get("message", error_data)
                    except Exception:
                        error_msg = error_data
                    last_error = f"Groq API error 429: {error_msg}"
                    wait_s = _parse_groq_429_retry_seconds(error_msg)
                    await asyncio.sleep(wait_s)
                    continue
                if response.status_code != 200:
                    error_data = response.text
                    try:
                        error_json = response.json()
                        error_msg = error_json.get("error", {}).get("message", error_data)
                    except Exception:
                        error_msg = error_data
                    raise Exception(f"Groq API error {response.status_code}: {error_msg}")
                data = response.json()
                if "choices" in data and len(data["choices"]) > 0:
                    msg = data["choices"][0].get("message") or {}
                    content = msg.get("content")
                    if content is None:
                        raise Exception(
                            "Groq returned no message content. Response data: "
                            f"{data}"
                        )
                    return content
                raise Exception("No content generated in Groq response")
        except Exception as e:
            if attempt == 1:
                raise Exception(f"Groq API request failed: {str(e)}")
            last_error = str(e)
            if "429" in last_error:
                wait_s = _blog_delay_seconds() * 2
                await asyncio.sleep(wait_s)
                continue
            raise
    raise Exception(last_error or "Groq API request failed")


async def generate_content(
    prompt: str,
    model: Optional[str] = None,
    max_tokens: int = 2000,
    temperature: float = 0.7,
    system: Optional[str] = None,
    top_p: Optional[float] = None,
    frequency_penalty: Optional[float] = None,
    presence_penalty: Optional[float] = None,
    timeout_seconds: float = 60.0,
) -> str:
    """Generate content using Groq (OpenAI-compatible chat completions)."""
    if not settings.groq_api_key:
        raise ValueError("Groq API key not configured")

    resolved_model = model or BLOG_GENERATION_MODEL_GROQ
    messages: List[Dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    try:
        return await _groq_chat_completion(
            messages=messages,
            model=resolved_model,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            frequency_penalty=frequency_penalty,
            presence_penalty=presence_penalty,
            timeout_seconds=timeout_seconds,
        )
    except Exception as e:
        raise Exception(f"Error generating content: {str(e)}")


async def humanize_text(text: str) -> str:
    """Run full humanizer pipeline; returns humanized text only (backward compatible)."""
    from app.services.humanizer.pipeline import humanize_pipeline

    if not text or not str(text).strip():
        raise ValueError("Text is empty")
    result = await humanize_pipeline(text)
    return result.humanized


async def generate_news_headline(
    news_results: List[Dict[str, Any]],
    query: Optional[str] = None
) -> str:
    """
    Generate a 2-line, eye-catching headline for a social media post from 7 news results.
    Returns the headline with newline preserved between lines (max ~220 chars total).
    """
    if not news_results:
        raise ValueError("At least one news result is required")
    lines = []
    for i, item in enumerate(news_results[:7], 1):
        title = item.get("title", "")
        source = item.get("source", "")
        snippet = (item.get("snippet") or "")[:280]
        date = item.get("date", "")
        lines.append(f"{i}. Title: {title}\n   Source: {source}\n   Date: {date}\n   Snippet: {snippet}")
    news_block = "\n\n".join(lines)
    query_line = f"\n\nUser search query: \"{query}\"" if query else ""
    prompt = f"""You are writing a 2-LINE headline for a social media post (Instagram, Twitter, Facebook). The goal is to stop the scroll and get engagement—clicks, likes, shares.

Style reference (long, descriptive, event then reaction): "Tyler Shough's Rookie Season Ends Without ROY — Fans and Analysts Debate the Snub"

Below are 7 recent news articles. Write a headline in exactly two lines:
- Use a long, descriptive style: state what happened (name/subject, outcome), then use an em dash — and add the reaction, debate, or why it matters (e.g. "Fans and Analysts Debate the Snub").
- Line 1: Main hook—55–75 characters. Event or outcome with specifics (names, numbers). Can end with em dash — if the second part continues on line 2.
- Line 2: Reaction, debate, or consequence—55–75 characters. What people are saying, the twist, or the stakes.
- Put a single newline between line 1 and line 2. No other newlines.
- Total length 120–180 characters. Accurate and not clickbait—must reflect the real story.
- Capture the most newsworthy or emotional angle from the articles.
{news_block}{query_line}

Output only the 2-line headline. No quotes, no explanation. Use a real newline between the two lines."""
    if not settings.groq_api_key:
        raise ValueError(
            "No LLM API key configured. Set GROQ_API_KEY in the backend .env file."
        )
    result = await generate_blog_seo_content(
        prompt,
        max_tokens=220,
        temperature=0.75
    )
    headline = (result or "").strip().strip('"').strip("'")
    if not headline:
        raise ValueError(
            "The model returned an empty headline. Try again or adjust your query."
        )
    # Keep at most 2 lines; collapse extra newlines to space within lines
    parts = [p.replace("\n", " ").strip() for p in headline.split("\n")[:2]]
    headline = "\n".join(parts).strip()
    if len(headline) > 220:
        headline = headline[:217] + "..."
    return headline


async def generate_social_post_description(
    news_results: List[Dict[str, Any]],
    headline: str,
    query: Optional[str] = None,
) -> str:
    """
    Generate a short, human-readable description (1–2 sentences) that matches the headline
    and is grounded in the supplied news snippets.
    """
    if not news_results:
        raise ValueError("At least one news result is required")
    if not settings.groq_api_key:
        raise ValueError(
            "No LLM API key configured. Set GROQ_API_KEY in the backend .env file."
        )

    lines = []
    for i, item in enumerate(news_results[:7], 1):
        title = item.get("title", "")
        source = item.get("source", "")
        snippet = (item.get("snippet") or "")[:280]
        date = item.get("date", "")
        lines.append(
            f"{i}. Title: {title}\n   Source: {source}\n   Date: {date}\n   Snippet: {snippet}"
        )
    news_block = "\n\n".join(lines)
    query_line = f'\n\nUser search query: "{query}"' if query else ""
    headline_clean = (headline or "").replace("\n", " ").strip()

    prompt = f"""Write a short description for a social post.

You are given:
- A headline (already written)
- 7 news snippets (the only source of truth)

Rules:
- 1–2 sentences.
- 140–220 characters total.
- Must match the facts implied by the snippets; do NOT add details not present.
- No hashtags, no emojis, no quotes, no bullet points.
- Keep it readable and specific (who/what happened + why it matters).

Headline: {headline_clean}
{news_block}{query_line}

Output only the description text."""

    result = await generate_blog_seo_content(
        prompt,
        max_tokens=120,
        temperature=0.5,
    )
    desc = (result or "").strip().strip('"').strip("'")
    # Compact whitespace
    desc = " ".join(desc.split())
    if len(desc) > 240:
        desc = desc[:237] + "..."
    return desc


async def generate_social_post_story_description(
    news_results: List[Dict[str, Any]],
    headline: str,
    query: Optional[str] = None,
) -> str:
    """
    Generate a storytelling-style description (<= 100 words) that matches the headline
    and stays grounded in the supplied news snippets.
    """
    if not news_results:
        raise ValueError("At least one news result is required")
    if not settings.groq_api_key:
        raise ValueError(
            "No LLM API key configured. Set GROQ_API_KEY in the backend .env file."
        )

    lines = []
    for i, item in enumerate(news_results[:7], 1):
        title = item.get("title", "")
        source = item.get("source", "")
        snippet = (item.get("snippet") or "")[:280]
        date = item.get("date", "")
        lines.append(
            f"{i}. Title: {title}\n   Source: {source}\n   Date: {date}\n   Snippet: {snippet}"
        )
    news_block = "\n\n".join(lines)
    query_line = f'\n\nUser search query: "{query}"' if query else ""
    headline_clean = (headline or "").replace("\n", " ").strip()

    prompt = f"""Write a storytelling-style description for a social post.

Inputs:
- Headline (already written)
- 7 news snippets (the only source of truth)

Rules:
- Up to 100 words.
- 2–5 sentences, with a narrative flow (what happened → why it matters → what people are saying/what’s next).
- Must stay faithful to the snippets. Do NOT invent numbers, quotes, names, or outcomes not present.
- No hashtags, no emojis, no bullet points, no quotes.
- Sound like a newsroom social caption, not SEO.

Headline: {headline_clean}
{news_block}{query_line}

Output only the description text."""

    result = await generate_blog_seo_content(
        prompt,
        max_tokens=220,
        temperature=0.65,
    )
    story = (result or "").strip().strip('"').strip("'")
    story = " ".join(story.split())
    # Hard cap: if model goes long, trim roughly by words.
    words = story.split(" ")
    if len(words) > 100:
        story = " ".join(words[:100]).rstrip() + "..."
    return story


async def generate_social_post_hashtags(
    news_results: List[Dict[str, Any]],
    headline: str,
    query: Optional[str] = None,
    count: int = 12,
) -> List[str]:
    """
    Generate relevant hashtags (10–15) grounded in the supplied news snippets + headline.
    Returns a list like ["#AI", "#TechNews", ...] with no duplicates.
    """
    if not news_results:
        raise ValueError("At least one news result is required")
    if not settings.groq_api_key:
        raise ValueError(
            "No LLM API key configured. Set GROQ_API_KEY in the backend .env file."
        )

    # Clamp requested count to a sane range
    try:
        n = int(count)
    except Exception:
        n = 12
    n = max(10, min(15, n))

    lines = []
    for i, item in enumerate(news_results[:7], 1):
        title = item.get("title", "")
        source = item.get("source", "")
        snippet = (item.get("snippet") or "")[:220]
        date = item.get("date", "")
        lines.append(
            f"{i}. Title: {title}\n   Source: {source}\n   Date: {date}\n   Snippet: {snippet}"
        )
    news_block = "\n\n".join(lines)
    query_line = f'\n\nUser search query: "{query}"' if query else ""
    headline_clean = (headline or "").replace("\n", " ").strip()

    prompt = f"""Generate {n} real, relevant hashtags for a news-based social post.

You are given:
- Headline
- 7 news snippets (only source of truth)

Rules:
- Output MUST be a JSON array of strings, nothing else.
- Provide exactly {n} hashtags.
- Each item must start with #.
- Use mostly broad + a few niche tags; avoid spammy/irrelevant tags.
- Avoid duplicates (case-insensitive) and avoid very generic tags like #News unless truly necessary.
- Prefer CamelCase for multi-word tags (e.g. #ArtificialIntelligence).

Headline: {headline_clean}
{news_block}{query_line}
"""

    result = await generate_blog_seo_content(
        prompt,
        max_tokens=220,
        temperature=0.55,
    )

    text = (result or "").strip()
    # Best-effort JSON parse; fall back to simple extraction.
    tags: List[str] = []
    try:
        import json

        parsed = json.loads(text)
        if isinstance(parsed, list):
            tags = [str(x).strip() for x in parsed if str(x).strip()]
    except Exception:
        # Fallback: split on whitespace/commas/newlines
        raw = (
            text.replace("\n", " ")
            .replace(",", " ")
            .replace("[", " ")
            .replace("]", " ")
            .replace('"', " ")
            .replace("'", " ")
        )
        tags = [t.strip() for t in raw.split(" ") if t.strip()]

    # Normalize: ensure leading #, remove punctuation, dedupe.
    cleaned: List[str] = []
    seen = set()
    for t in tags:
        t = t.strip()
        if not t:
            continue
        if not t.startswith("#"):
            t = "#" + t
        # Keep only hashtag-safe characters
        t = "#" + "".join(ch for ch in t[1:] if ch.isalnum() or ch == "_")
        if len(t) <= 1:
            continue
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(t)
        if len(cleaned) >= n:
            break

    # If model returned too few, pad with conservative defaults based on headline/query.
    if len(cleaned) < n:
        fallback = ["#TechNews", "#AI", "#Cybersecurity", "#Safety", "#Innovation", "#MachineLearning"]
        for t in fallback:
            key = t.lower()
            if key not in seen:
                cleaned.append(t)
                seen.add(key)
            if len(cleaned) >= n:
                break

    return cleaned[:n]


async def generate_blog_outline(
    topic: str,
    keywords: List[str] = None
) -> str:
    """Generate a blog post outline"""
    keywords_text = ", ".join(keywords) if keywords else "general SEO best practices"
    
    prompt = f"""Create a comprehensive blog post outline for the topic: "{topic}"

Include relevant keywords: {keywords_text}

The outline should include:
1. Introduction
2. Main sections with subheadings
3. Conclusion

Format the outline in markdown with clear headings and subheadings."""

    return await generate_content(prompt, max_tokens=1000, temperature=0.8)


async def generate_blog_content(
    topic: str,
    outline: str,
    keywords: List[str] = None,
    tone: str = "professional"
) -> str:
    """Generate full blog post content"""
    keywords_text = ", ".join(keywords) if keywords else ""
    
    prompt = f"""Write a comprehensive blog post on the topic: "{topic}"

Use this outline as a guide:
{outline}

Keywords to naturally incorporate: {keywords_text}

Tone: {tone}

Write a well-structured, engaging blog post that:
- Has a compelling introduction
- Follows the outline structure
- Naturally incorporates the keywords
- Provides valuable information
- Has a strong conclusion
- Is optimized for SEO

Format the content in markdown."""

    return await generate_content(prompt, max_tokens=3000, temperature=0.7)


async def generate_blog_title(
    topic: str,
    keywords: List[str] = None
) -> str:
    """Generate SEO-optimized blog title"""
    keywords_text = ", ".join(keywords) if keywords else ""
    
    prompt = f"""Generate an SEO-optimized, engaging blog title for the topic: "{topic}"

Keywords to consider: {keywords_text}

The title should be:
- 50-60 characters long
- Include relevant keywords naturally
- Be compelling and click-worthy
- Follow SEO best practices

Return only the title, nothing else."""

    return await generate_content(prompt, max_tokens=100, temperature=0.9)


# ============================================
# NEW SEO Blog Generation Functions
# ============================================

async def generate_blog_content_with_groq(
    prompt: str,
    max_tokens: int = 2000,
    temperature: float = 0.7
) -> str:
    """Generate blog content using Groq API with llama-3.3-70b model. Retries on 429."""
    if not settings.groq_api_key:
        raise ValueError("Groq API key not configured")
    return await _groq_chat_completion(
        messages=[{"role": "user", "content": prompt}],
        model=BLOG_GENERATION_MODEL_GROQ,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout_seconds=120.0,
    )


async def generate_blog_seo_content(
    prompt: str,
    max_tokens: int = 2000,
    temperature: float = 0.7
) -> str:
    """
    Generate blog content using Groq with llama-3.3-70b model.
    """
    if not settings.groq_api_key:
        raise ValueError("Groq API key not configured")
    
    return await generate_blog_content_with_groq(prompt, max_tokens, temperature)


async def assess_research_sufficiency(
    topic: str,
    research_context: Optional[str],
    category: str
) -> Tuple[bool, List[str]]:
    """Assess if research context is sufficient; if not, return suggested search queries."""
    import json
    context_preview = (research_context or "")[:1500]
    if not context_preview:
        context_preview = "(No research context provided yet.)"
    prompt = f"""You are evaluating whether we have enough information to write an accurate, comprehensive blog (and for celebrities, multiple intent-specific articles).

Topic: "{topic}"
Content Category: {category}

Current research context (excerpt):
{context_preview}

Is this information sufficient to write accurate, comprehensive content? Consider: key facts, recent updates, figures (e.g. net worth), relationship/personal details for celebrities.

Answer with a JSON object only, no other text:
{{"sufficient": true or false, "queries": ["query1", "query2", "query3"]}}

If sufficient is false, provide 1-3 specific search queries to fill the gaps (e.g. "{topic} net worth 2024", "{topic} relationship update"). If sufficient is true, set queries to [].
Return ONLY the JSON object."""

    try:
        result = await generate_blog_seo_content(prompt, max_tokens=300, temperature=0.3)
        result_clean = result.strip()
        if result_clean.startswith("```json"):
            result_clean = result_clean[7:]
        if result_clean.startswith("```"):
            result_clean = result_clean[3:]
        if result_clean.endswith("```"):
            result_clean = result_clean[:-3]
        result_clean = result_clean.strip()
        data = json.loads(result_clean)
        sufficient = bool(data.get("sufficient", True))
        queries = data.get("queries") or []
        if not isinstance(queries, list):
            queries = []
        queries = [str(q).strip() for q in queries[:3] if q]
        return (sufficient, queries)
    except (json.JSONDecodeError, Exception):
        return (True, [])


async def resolve_celebrity_display_name(topic: str, research_context: Optional[str] = None) -> str:
    """
    Resolve a short topic (e.g. 'William') to the full display name for SEO/slug (e.g. 'Prince William').
    Uses research context when available; returns topic unchanged on failure or if already specific.
    """
    if not topic or not (topic := topic.strip()):
        return topic
    # If topic already looks like a full name (multiple words or contains comma), use as-is
    if "," in topic or len(topic.split()) >= 2:
        return topic
    if not research_context or len(research_context.strip()) < 50:
        return topic
    try:
        snippet = (research_context or "")[:1200]
        prompt = f"""Given the following research excerpt about a celebrity or public figure, what is the person's full or most commonly used display name (e.g. "Prince William", "Shah Rukh Khan")? The topic query was: "{topic}".

Research excerpt:
{snippet}

Reply with ONLY the display name, nothing else. No quotes, no explanation. If unsure, reply with the topic as given: {topic}"""
        result = await generate_blog_seo_content(prompt, max_tokens=50, temperature=0.2)
        name = (result or "").strip().strip('"').strip("'")
        if name and len(name) > len(topic) and 2 <= len(name.split()) <= 6:
            return name
    except Exception:
        pass
    return topic


async def generate_seo_title(
    topic: str,
    description: Optional[str] = None,
    keywords: List[str] = None,
    category: str = "Technology & AI",
    research_context: Optional[str] = None,
    content_intent: Optional[str] = None
) -> str:
    """Generate SEO-optimized blog title following Rank Math guidelines"""
    keywords_text = ", ".join(keywords) if keywords else "relevant terms"
    category_style = CATEGORY_PROMPTS.get(category, CATEGORY_PROMPTS["Lifestyle & Misc"])
    description_text = f"\nContext: {description}" if description else ""
    research_block = f"\n\nResearch context (use to inform your response):\n{research_context}" if research_context else ""
    intent_block = ""
    if content_intent and content_intent in CONTENT_INTENT_PROMPTS:
        intent_block = f"\n\nContent intent (follow this focus):\n{CONTENT_INTENT_PROMPTS[content_intent]}"
    
    prompt = f"""Generate an SEO-optimized blog title for the following topic.

Topic: "{topic}"{description_text}
Target Keywords: {keywords_text}
Content Category: {category}{research_block}{intent_block}

Writing Style Guidelines:
{category_style}

Rank Math SEO Requirements (all required):
- Title must be 50-60 characters long
- Put the Focus Keyword (primary keyword or topic) in the SEO title and near the beginning (first 3-5 words if possible)
- Include at least one power word (e.g. Ultimate, Essential, Best, Proven, Secret, Complete, Guide, Top, Key, Must-Know, Incredible, Simple)
- Include a number in the title (e.g. "5 Facts", "2024 Guide", "10 Things")
- Use positive or negative sentiment (e.g. "Why...", "How...", "The Rise of...", "What Makes...")
- Make it compelling and click-worthy; avoid clickbait - be accurate and informative

Return ONLY the title, nothing else. No quotes, no explanation."""

    result = await generate_blog_seo_content(prompt, max_tokens=100, temperature=0.8)
    # Clean up the result
    return result.strip().strip('"').strip("'")


async def generate_meta_description(
    topic: str,
    description: Optional[str] = None,
    keywords: List[str] = None,
    category: str = "Technology & AI",
    research_context: Optional[str] = None,
    content_intent: Optional[str] = None
) -> str:
    """Generate SEO-optimized meta description following Rank Math guidelines"""
    keywords_text = ", ".join(keywords) if keywords else "relevant terms"
    category_style = CATEGORY_PROMPTS.get(category, CATEGORY_PROMPTS["Lifestyle & Misc"])
    description_text = f"\nContext: {description}" if description else ""
    research_block = f"\n\nResearch context (use to inform your response):\n{research_context}" if research_context else ""
    intent_block = ""
    if content_intent and content_intent in CONTENT_INTENT_PROMPTS:
        intent_block = f"\n\nContent intent (follow this focus):\n{CONTENT_INTENT_PROMPTS[content_intent]}"
    
    prompt = f"""Generate an SEO-optimized meta description for the following blog topic.

Topic: "{topic}"{description_text}
Target Keywords: {keywords_text}
Content Category: {category}{research_block}{intent_block}

Writing Style Guidelines:
{category_style}

Rank Math SEO Requirements (all required):
- Meta description must be 150-160 characters
- Include the Focus Keyword in the meta description (Rank Math requirement)
- Write a compelling summary that encourages clicks
- Include a subtle call-to-action
- Accurately represent the content

Return ONLY the meta description, nothing else. No quotes, no explanation."""

    result = await generate_blog_seo_content(prompt, max_tokens=200, temperature=0.7)
    return result.strip().strip('"').strip("'")


def _build_parent_article_prompt(
    topic: str,
    description_text: str,
    keywords_text: str,
    category_style: str,
    research_block: str,
    last_updated_block: str,
    youtube_url: Optional[str] = None,
    omit_religion: bool = False,
) -> str:
    """Build full 12-section parent/overview article prompt (minimum 2,500 words)."""
    yt_instruction = (
        f'In the "First Media Appearance" section include exactly this placeholder on its own line: [YOUTUBE_EMBED: {youtube_url}] (it will be replaced with the embedded video). Then write 1-2 paragraphs on context and what the interview reveals. Do not write any raw HTML, iframe code, or embed attributes.'
        if youtube_url
        else 'In "First Media Appearance" section write: "No suitable interview video was found for this article." and add 1-2 paragraphs explaining the significance of early media appearances for this person.'
    )
    quick_facts_table = (
        """| Attribute | Details |
|-----------|---------|
| Profession | ... |
| Date of Birth | ... |
| Zodiac Sign | ... |
| Nationality | ... |
- Internal link to the net worth companion only: use flat relative path `/name-slug-net-worth` (lowercase, hyphens). Never use `/person/.../net-worth`.
- **Do not** mention religion, faith, spirituality, church, prayer, religious upbringing, or beliefs anywhere in this article (including Quick Facts, tables, intros, conclusions, or links)."""
        if omit_religion
        else """| Attribute | Details |
|-----------|---------|
| Religion | ... |
| Profession | ... |
| Date of Birth | ... |
| Zodiac Sign | ... |
| Nationality | ... |
- Internal links to companion articles (religion / net worth) must use flat relative paths only, e.g. `/rob-smith-religion` and `/rob-smith-net-worth` (lowercase, hyphens). Never use `/person/.../religion` or `/person/.../net-worth`."""
    )
    return f"""Write a comprehensive celebrity overview article. Total length 2,000-3,000 words. Use research to fact-check career dates, film titles, and awards.

Topic: "{topic}"{description_text}
Target Keywords: {keywords_text}
Writing style: {category_style}
{research_block}

Include where relevant (e.g. end of Conclusion): {last_updated_block}. Use this block exactly as written; do not add image placeholders or links with "blank" or placeholder URLs in the Sources line.

Rank Math SEO (required):
- Focus Keyword: use the topic/primary keyword as the focus keyword. Use it in the first 1-2 sentences (beginning of content), in at least 2-3 H2/H3 subheadings, and naturally in the body. Aim for ~1% keyword density (about once per 100 words).
- Use short paragraphs (2-4 sentences max; Rank Math flags long paragraphs).
- Do not add a Table of Contents.
- Include at least one image placeholder with Focus Keyword in the alt text: e.g. ![Topic Name at Event](image-url) or ![Topic Name - Description](image-url).

STRUCTURE (use these exact H2 headings in order):

## Introduction (200-250 words)
- Compelling hook about the celebrity
- Who they are, why they are famous, what makes them relevant today
- Engaging, informative tone; end with transition into their background

## Quick Facts
- Table immediately after intro. Format:
{quick_facts_table}

## Early Life (250-300 words)
- One well-structured paragraph: birthplace, family background, cultural influences, childhood interests related to acting/arts
- Below the paragraph, 3 bullet points: key childhood experiences, education/training, early inspirations

## First Media Appearance / Interview Embed
- {yt_instruction}

## Career Overview (400-500 words)
- **Career Beginnings**: How career started; first acting opportunity; early struggles or breakthroughs. Use research to fact-check early roles.
- **Career Transition**: Shift from minor roles to mainstream; changes in genre, language, market (local to international).
- **First Leading Role**: Title of first leading role, year, impact; why it was a turning point.

## Personal Life (250-300 words)
- Verified information only: family, close friendships if public, marriage status, children
- Neutral, factual tone; no rumors or unverified gossip

## Filmography
- Use **paragraph and bullet format only** (no tables). Write in full sentences so the content is readable and not tiny.
- **Films**: One short paragraph introducing their film work, then a bullet list. Each bullet: **Year — Title** (Role if known): one or two sentences of context or notes. Example: "- **2018 — Tu Hijo**: She played the lead in this drama; the film premiered at the Cannes Film Festival."
- **Television Work**: A short paragraph, then bullets for major TV shows with role and brief description (full sentences).
- **Music Videos & Other Appearances**: Paragraph and/or bullets; concise but not single-word entries.

## Awards & Nominations (200-250 words)
- Chronological list; highlight wins vs nominations; explain importance of major awards

## Public Image & Legacy (200-300 words)
- Influence on industry, fan following, cultural impact; how they are perceived today

## Conclusion (150-200 words)
- Summarize career journey and key achievements; current projects or future outlook; inspiring, forward-looking close
- Do not put image placeholders (![...](image-url)) in the Conclusion. Do not put them in the Last Updated / Sources line.
- When you include the Last Updated block, use it exactly as given (e.g. "> **Last Updated:** [date] | **Sources:** Celebrity Net Worth, Forbes"). Do not add links with "blank" or placeholder URLs; for source names use plain text or real URLs only (e.g. [Celebrity Net Worth](https://www.celebritynetworth.com/), [Forbes](https://www.forbes.com/)).

Format in clean markdown. Do not include the article title. Start with the introduction. Cite sources as markdown links where relevant (use real URLs only, never "blank"). Use the Focus Keyword in at least 2 of the H2 section headings (e.g. "Early Life of [Name]", "[Name]'s Career Overview")."""


def _build_religion_article_prompt(
    topic: str,
    description_text: str,
    keywords_text: str,
    category_style: str,
    research_block: str,
    last_updated_block: str,
) -> str:
    """Build full 8-section religion article prompt (whatreligionisinfo.com style)."""
    return f"""You are an expert celebrity religion blogger writing for whatreligionisinfo.com. Create a comprehensive, SEO-optimized article about {topic}'s religious beliefs and spiritual background.

Topic: "{topic}"{description_text}
Target Keywords: {keywords_text}
Writing style: {category_style}
{research_block}

Include this recency block where appropriate: {last_updated_block}

Rank Math SEO (required):
- Focus Keyword: use the topic/primary keyword (e.g. "{topic} religion" or "{topic}") as the focus keyword. Use it in the first 1-2 sentences, in at least 2 H2/H3 subheadings, and naturally in the body. Aim for ~1% keyword density.
- Use short paragraphs (2-4 sentences max).
- Do not add a Table of Contents.
- Include at least one image placeholder with Focus Keyword in the alt text, e.g. ![{topic} religion belief](url).

ARTICLE STRUCTURE (follow exactly, use these exact H2 headings):

## Introduction (200-300 words)
- Hook: present as a mystery or surprising revelation about their faith
- Brief bio: birth date, profession, nationality
- Include a Quick Facts table immediately after the first paragraph:
| Attribute | Details |
|-----------|---------|
| Religion | (if known, else "—") |
| Profession | ... |
| Date of Birth | ... |
| Zodiac Sign | ... |
| Nationality | ... |
- If you link to sibling articles (overview or net worth), use flat paths like `/name-slug` and `/name-slug-net-worth` only — never `/person/.../...` URLs.
- Include one expert-style quote about celebrity spirituality (e.g. from a religious studies perspective)
- Thesis statement: preview what the article will explore

## Upbringing & Religious Roots (300-400 words)
- Family background and childhood religion
- Specific traditions, church attendance, cultural context
- Celebrity quote about childhood if available; otherwise expert analysis on how upbringing shapes values

## Current Spiritual Beliefs (300-400 words)
- Evolution from childhood to present
- Direct quotes from interviews about faith/God/spirituality where possible
- Clarify: Do they still practice? Have they converted? Are they secular?
- Expert commentary on generational shifts in spirituality

## Career & Artistic Expression (300-400 words)
- How beliefs influence their work (roles, music, art)
- Specific examples: films, songs, public statements
- Include a table with 3-4 major projects:
| Project | Impact | Expert Opinion |
|---------|--------|-----------------|
| ... | ... | ... |
- Connect themes to spiritual/religious values (compassion, justice, existential questions)

## Comparisons with Other Celebrities (300-400 words)
- 3-4 relevant comparisons (similar background, contrasting paths, same generation)
- How do they handle faith differently? What can we learn?
- Include diverse examples (devout, spiritual-but-not-religious, converted, secular)

## Data & Expert Opinions (200-300 words)
- Statistics if relevant (e.g. Pew Research, demographic trends)
- 2-3 additional expert-style quotes (sociology, psychology, theology)
- Synthesize what scholars say about this celebrity's approach

## Conclusion (200 words)
- Summarize findings without being repetitive
- Final expert-style quote tying it all together
- Forward-looking statement about their spiritual journey

Format in clean markdown. Do not include the article title (it will be added separately). Start with the introduction. Use citations as markdown links where relevant. Total length approximately 2,200-2,800 words. Use the Focus Keyword in at least 2 H2 section headings (e.g. "[Name]'s Upbringing", "Current Beliefs of [Name]")."""


async def generate_blog_body(
    topic: str,
    description: Optional[str] = None,
    keywords: List[str] = None,
    category: str = "Technology & AI",
    research_context: Optional[str] = None,
    content_intent: Optional[str] = None,
    keyword_strategy: Optional[Dict[str, Any]] = None,
    youtube_url: Optional[str] = None,
    omit_religion_in_parent: bool = False,
    omit_religion_refs_in_net_worth: bool = False,
) -> str:
    """Generate SEO-optimized blog body content following Rank Math guidelines"""
    keywords_text = ", ".join(keywords) if keywords else "relevant terms"
    if keyword_strategy:
        extra_kw = keyword_strategy.get("primary") or keyword_strategy.get("secondary") or []
        if isinstance(extra_kw, list):
            keywords_text = keywords_text + ". Also use: " + ", ".join(extra_kw[:5]) if extra_kw else keywords_text
        else:
            keywords_text = keywords_text + ". Primary: " + str(extra_kw) if extra_kw else keywords_text
    category_style = CATEGORY_PROMPTS.get(category, CATEGORY_PROMPTS["Lifestyle & Misc"])
    description_text = f"\n\nAdditional Context/Background:\n{description}" if description else ""
    research_block = f"\n\nResearch context (use to inform your response):\n{research_context}" if research_context else ""
    intent_block = ""
    if content_intent and content_intent in CONTENT_INTENT_PROMPTS:
        intent_block = f"\n\nContent intent (follow this focus):\n{CONTENT_INTENT_PROMPTS[content_intent]}"
    from datetime import date
    _last_updated = date.today().strftime("%B %d, %Y")
    _last_updated_block = f'> **Last Updated:** {_last_updated} | **Sources:** Celebrity Net Worth, Forbes'
    _last_updated_block_nw = (
        f'> **Last Updated:** {_last_updated} | **Citations:** Name only sources you actually cite in the article body (with markdown links). '
        f'Do **not** list "Celebrity Net Worth" or "Forbes" in this line unless the article includes a real link to a page **about this person**.'
    )
    eeat_block = ""
    if content_intent == "net_worth":
        sibling_line = (
            "- Linking: use flat path `/name-slug` for the overview article only — never `/person/.../...` URLs.\n"
            "- **Do not** link to or describe a separate religion article (none exists for this person)."
            if omit_religion_refs_in_net_worth
            else "- Linking: flat paths `/name-slug` (overview) and `/name-slug-religion` (if applicable) — never `/person/.../...` URLs."
        )
        eeat_block = f"""

E-E-A-T and accuracy (YMYL financial content):
- **Banned:** religion, faith, spirituality, church, prayer, religious identity, or any non-financial personal belief content. Keep this article strictly about money, compensation, assets, and career-related finance.
- **Source hierarchy:** For **U.S. Members of Congress / federal officeholders**, prioritize **[House or Senate financial disclosures](https://disclosures-clerk.house.gov/)**, **[OpenSecrets](https://www.opensecrets.org/)**, or **LegiStorm**-style summaries — name the **reporting year or filing period**. Third-party net worth trackers (e.g. Quiver) may be used only if labeled **approximate** with an **as-of date**.
- For **entertainers and general celebrities**, cite the **named** outlet or database with a **real URL**. Do **not** cite "Celebrity Net Worth" or "Forbes" as generic brands without a link to a **dedicated** profile for this person.
- Cite every factual money claim with a **markdown link** where possible — never source names alone.
- Include this disclaimer verbatim once: "Net worth estimates are based on publicly available information and may vary."
- Give **one** headline net worth figure (or a **narrow range** from official disclosures) and state the **as-of date**, **reporting year**, or **methodology** (e.g. midpoint of disclosure ranges).
- **Do not** put **annual salary** in the same "% of net worth" table as asset buckets without explanation. Use either:
  - **Wealth table:** | Category | Estimated amount or range | % of estimated net worth | where rows are **asset/debt components** and percentages refer to **estimated total wealth** (sum 100%); **or**
  - A **separate** small table or paragraph for **annual income** (salary, fees) labeled clearly as **per year**, not as a percentage of net worth unless you show the math.
- Include this block verbatim near the top (after the opening paragraph): {_last_updated_block_nw}
{sibling_line}"""
    elif content_intent == "religion":
        eeat_block = _build_religion_article_prompt(topic, description_text, keywords_text, category_style, research_block, _last_updated_block)
        prompt = eeat_block
        return await generate_blog_seo_content(prompt, max_tokens=4500, temperature=0.6)
    elif content_intent == "parent_overview":
        prompt = _build_parent_article_prompt(
            topic, description_text, keywords_text, category_style,
            research_block, _last_updated_block, youtube_url=youtube_url,
            omit_religion=omit_religion_in_parent,
        )
        return await generate_blog_seo_content(prompt, max_tokens=5000, temperature=0.6)
    
    prompt = f"""Write a comprehensive, SEO-optimized blog post on the following topic.

Topic: "{topic}"{description_text}

Target Keywords to incorporate naturally: {keywords_text}
Content Category: {category}{research_block}{intent_block}{eeat_block}

Writing Style Guidelines:
{category_style}

Rank Math SEO Requirements (all required):
1. Focus Keyword: use it in the first 1-2 sentences (beginning of content), in at least 2-3 H2/H3 subheadings, and naturally in the body. Aim for ~1% keyword density (about once per 100 words).
2. Include LSI (related) keywords naturally throughout.
3. Use proper heading hierarchy: ## for H2, ### for H3; include 3-5 main sections. Use the Focus Keyword in at least 2 subheadings (e.g. "H2: [Keyword] Overview").
4. Keep paragraphs short (2-4 sentences max; Rank Math flags long paragraphs).
5. Do not add a Table of Contents.
6. Include at least one image with Focus Keyword in the alt text: e.g. ![Topic or Keyword description](image-url).
7. Use bullet points and numbered lists where appropriate.
8. Write at least 800-1200 words for comprehensive coverage.
9. Include a compelling introduction that hooks the reader.
10. End with a strong conclusion that summarizes key points.

Information Table (required):
Include exactly one markdown table with 4-5 rows. The table has two columns: the first lists keywords/labels (e.g. "Religion", "Date of Birth"), the second lists the corresponding data. Use accurate, relevant data based on the topic and research context.
Category-specific table content:
- Celebrities & Public Figures: Include details such as Religion, Profession, Date of Birth (or Age), Net Worth, Marital Status, Nationality, Zodiac Sign, or similar biographical facts. Use one row per fact.
- Sports: Include teams, match scores, tournament/event name, winner or key result, date, or similar. Use one row per fact (e.g. "Team", "Match Score", "Tournament", "Winner").
- Finance & Economy: Include a comparison or key-figures table: rates, percentages, key metrics, product/entity names, or dates. Use one row per metric (e.g. "Interest Rate", "APY", "Provider", "Key Figure").
- Technology & AI / News & Politics / Games & Entertainment / Lifestyle & Misc / Weather & Local Conditions: If a table adds value (specs, comparisons, key facts, dates, or similar), include one with 4-5 rows of relevant labels and data; otherwise include a brief "Key facts" or "At a glance" table with the most relevant 4-5 points.
Format the table in standard markdown:
| Label / Keyword | Data |
|----------------|------|
| Row 1 label    | Row 1 value |
| Row 2 label    | Row 2 value |
(4-5 rows total). Place the table in a logical place (e.g. after an early section that introduces the subject). Use only accurate, plausible data; if uncertain, use "—" or "N/A" for that cell.

Format the content in clean markdown. Do not include the title (it will be added separately).
Start directly with the introduction paragraph.
Citations: use markdown links for any source (e.g. [according to Celebrity Net Worth](https://www.celebritynetworth.com/)) not plain text.
When the topic is time-sensitive or factual, include: > **Last Updated:** {_last_updated} | **Sources:** [relevant source names]."""

    return await generate_blog_seo_content(prompt, max_tokens=4000, temperature=0.7)


# ---------- Batched blog generation (fewer LLM calls, shared context, rate-limit friendly) ----------


async def generate_single_blog_batch(
    topic: str,
    description: Optional[str],
    keywords: List[str],
    category: str,
    research_context: Optional[str],
) -> Dict[str, Any]:
    """Generate title, meta, faqs, related_queries in one call; then body in a second call. Reuses shared context."""
    import json
    category_style = CATEGORY_PROMPTS.get(category, CATEGORY_PROMPTS["Lifestyle & Misc"])
    keywords_text = ", ".join(keywords) if keywords else "relevant terms"
    description_text = f"\nContext: {description}" if description else ""
    research_block = f"\n\nResearch context:\n{research_context}" if research_context else ""
    prompt_meta = f"""Generate SEO fields for ONE blog post in a single JSON object. Return ONLY valid JSON, no markdown.

Topic: "{topic}"{description_text}
Keywords: {keywords_text}
Category: {category}{research_block}

Style: {category_style[:200]}

Rank Math: Put Focus Keyword (topic/primary keyword) in title near the beginning. Include a number in the title. Use one power word (e.g. Ultimate, Essential, Best, Guide, Top). Use positive or negative sentiment.

Return this exact structure (replace with your content):
{{"title": "SEO title 50-60 chars", "meta_description": "150-160 chars", "faqs": [{{"question": "...", "answer": "..."}}, ...5 items], "related_queries": [{{"query": "query1", "answer": "1-2 sentence answer."}}, ...8 items]}}
Meta description 150-160 chars and must include the focus keyword. FAQs: 5 items; each FAQ answer must be 2-3 sentences (about 2-3 lines) with a bit more detail—avoid one-line answers. related_queries: 8 items, each with "query" (search phrase) and "answer" (1-2 sentence answer for content length)."""
    result_meta = await generate_blog_seo_content(prompt_meta, max_tokens=2000, temperature=0.6)
    await _blog_delay()
    # Parse meta block
    try:
        clean = result_meta.strip()
        for prefix in ("```json", "```"):
            if clean.startswith(prefix):
                clean = clean[len(prefix):].strip()
        if clean.endswith("```"):
            clean = clean[:-3].strip()
        meta_data = json.loads(clean)
        title = (meta_data.get("title") or "").strip() or f"{topic} - Guide"
        meta_description = (meta_data.get("meta_description") or "").strip().strip('"').strip("'") or title[:160]
        faqs = meta_data.get("faqs")
        if not isinstance(faqs, list):
            faqs = []
        faqs = [{"question": str(x.get("question", "")), "answer": str(x.get("answer", ""))} for x in faqs[:5]]
        related_queries = meta_data.get("related_queries")
        if not isinstance(related_queries, list):
            related_queries = []
        # Normalize to list of {query, answer}; accept legacy list of strings
        _rq = []
        for q in related_queries[:8]:
            if isinstance(q, dict):
                _rq.append({"query": str(q.get("query", "")).strip(), "answer": str(q.get("answer", "")).strip()})
            else:
                _rq.append({"query": str(q).strip(), "answer": ""})
        related_queries = _rq
    except (json.JSONDecodeError, Exception):
        title = f"{topic} - Guide"
        meta_description = (description or topic)[:160]
        faqs = [{"question": f"What is {topic}?", "answer": f"This article covers {topic}."}]
        related_queries = [{"query": f"What is {topic}", "answer": ""}, {"query": f"How to {topic}", "answer": ""}]
    # Body in separate call (largest output)
    body_content = await generate_blog_body(topic, description, keywords, category, research_context=research_context)
    return {
        "title": title,
        "meta_description": meta_description,
        "body_content": body_content,
        "faqs": faqs,
        "related_queries": related_queries,
    }


async def generate_tree_titles_metas_batch(
    topic: str,
    description: Optional[str],
    keywords: List[str],
    category: str,
    research_context: Optional[str],
    include_religion: bool = True,
) -> Dict[str, Any]:
    """One call: parent + child_0 + child_1 each with title and meta_description; or parent + child_1 only when include_religion is False."""
    import json
    research_block = f"\n\nResearch (excerpt):\n{(research_context or '')[:2000]}" if research_context else ""
    if not include_religion:
        prompt = f"""Topic: "{topic}". Category: {category}.{f' Context: {description}' if description else ''}{research_block}

There is NO separate religion article for this person (religion could not be verified). Generate SEO titles for exactly 2 articles only.

Rank Math: Put Focus Keyword ([Name] or topic) in each SEO title near the beginning. Include a number where natural. Use at least one power word per title. Each SEO title at most 60 characters. Meta descriptions 150-160 characters with focus keyword.

1. parent_overview: who they are, career, fame (pattern "Who Is [Name]? 5 Facts About Career & Fame")
2. net_worth: net worth, income (pattern "[Name] Net Worth: Key Facts")

**Do not** include religion, faith, spirituality, or church in any title or meta description.

Return ONLY this JSON (no markdown):
{{"parent": {{"title": "...", "meta_description": "150-160 chars"}}, "child_1": {{"title": "...", "meta_description": "..."}}}}"""
        result = await generate_blog_seo_content(prompt, max_tokens=600, temperature=0.5)
        try:
            clean = result.strip()
            for prefix in ("```json", "```"):
                if clean.startswith(prefix):
                    clean = clean[len(prefix) :].strip()
            if clean.endswith("```"):
                clean = clean[:-3].strip()
            data = json.loads(clean)
            parent = data.get("parent") or {}
            c1 = data.get("child_1") or {}

            def _truncate_title(s: str, max_len: int = 60) -> str:
                s = (s or "").strip()
                if len(s) <= max_len:
                    return s
                truncated = s[:max_len]
                last_space = truncated.rfind(" ")
                if last_space > max_len // 2:
                    return truncated[:last_space].strip()
                return truncated.strip()

            def _meta(s: str, max_len: int = 160) -> str:
                s = (s or "").strip().strip('"').strip("'")[:max_len]
                return s

            pt = _truncate_title(str(parent.get("title", "")).strip() or f"Who Is {topic}?")
            c1t = _truncate_title(str(c1.get("title", "")).strip() or f"{topic} Net Worth")
            return {
                "parent": {"title": pt, "meta_description": _meta(str(parent.get("meta_description", "")))},
                "child_0": {"title": "", "meta_description": ""},
                "child_1": {"title": c1t, "meta_description": _meta(str(c1.get("meta_description", "")))},
            }
        except (json.JSONDecodeError, Exception):
            def _truncate_title(s: str, max_len: int = 60) -> str:
                s = (s or "").strip()
                return s[:max_len] if len(s) > max_len else s

            return {
                "parent": {"title": _truncate_title(f"Who Is {topic}?"), "meta_description": (description or topic)[:160]},
                "child_0": {"title": "", "meta_description": ""},
                "child_1": {"title": _truncate_title(f"{topic} Net Worth"), "meta_description": ""},
            }

    prompt = f"""Topic: "{topic}". Category: {category}.{f' Context: {description}' if description else ''}{research_block}

Rank Math for all 3: Put Focus Keyword ([Name] or topic) in each SEO title near the beginning. Include a number in each title where natural (e.g. "5 Facts", "2024"). Use at least one power word per title (e.g. Complete, Ultimate, Essential, Key). Each SEO title must be at most 60 characters (Religion and Net Worth titles especially). Meta descriptions must be 150-160 characters and include the focus keyword.

Generate SEO titles and meta descriptions for 3 articles in one JSON object:
1. parent_overview: who they are, career, fame (title pattern "Who Is [Name]? 5 Facts About Career & Fame")
2. religion: religious beliefs, faith (title pattern "What Religion is [Name]? Key Facts Explained")
3. net_worth: net worth, income (title pattern "[Name] Net Worth: [Number] Key Facts")

Return ONLY this JSON (no markdown):
{{"parent": {{"title": "...", "meta_description": "150-160 chars"}}, "child_0": {{"title": "...", "meta_description": "..."}}, "child_1": {{"title": "...", "meta_description": "..."}}}}"""
    result = await generate_blog_seo_content(prompt, max_tokens=800, temperature=0.5)
    try:
        clean = result.strip()
        for prefix in ("```json", "```"):
            if clean.startswith(prefix):
                clean = clean[len(prefix):].strip()
        if clean.endswith("```"):
            clean = clean[:-3].strip()
        data = json.loads(clean)
        parent = data.get("parent") or {}
        c0 = data.get("child_0") or {}
        c1 = data.get("child_1") or {}

        def _truncate_title(s: str, max_len: int = 60) -> str:
            s = (s or "").strip()
            if len(s) <= max_len:
                return s
            truncated = s[:max_len]
            last_space = truncated.rfind(" ")
            if last_space > max_len // 2:
                return truncated[:last_space].strip()
            return truncated.strip()

        def _meta(s: str, max_len: int = 160) -> str:
            s = (s or "").strip().strip('"').strip("'")[:max_len]
            return s

        pt = _truncate_title(str(parent.get("title", "")).strip() or f"Who Is {topic}?")
        c0t = _truncate_title(str(c0.get("title", "")).strip() or f"What Religion is {topic}?")
        c1t = _truncate_title(str(c1.get("title", "")).strip() or f"{topic} Net Worth")
        return {
            "parent": {"title": pt, "meta_description": _meta(str(parent.get("meta_description", "")))},
            "child_0": {"title": c0t, "meta_description": _meta(str(c0.get("meta_description", "")))},
            "child_1": {"title": c1t, "meta_description": _meta(str(c1.get("meta_description", "")))},
        }
    except (json.JSONDecodeError, Exception):
        def _truncate_title(s: str, max_len: int = 60) -> str:
            s = (s or "").strip()
            return s[:max_len] if len(s) > max_len else s
        return {
            "parent": {"title": _truncate_title(f"Who Is {topic}?"), "meta_description": (description or topic)[:160]},
            "child_0": {"title": _truncate_title(f"What Religion is {topic}?"), "meta_description": ""},
            "child_1": {"title": _truncate_title(f"{topic} Net Worth"), "meta_description": ""},
        }


async def generate_tree_bodies_batch(
    topic: str,
    description: Optional[str],
    keywords: List[str],
    category: str,
    research_context: Optional[str],
    keyword_strategy: Optional[Dict[str, Any]],
    parent_title: str,
    c1_title: str,
    c2_title: str,
    youtube_url: Optional[str] = None,
) -> Tuple[str, str, str]:
    """One call: generate all three bodies in one response with clear section markers. Returns (parent_body, child_0_body, child_1_body)."""
    keywords_text = ", ".join(keywords) if keywords else "relevant terms"
    if keyword_strategy:
        extra = keyword_strategy.get("primary") or keyword_strategy.get("secondary") or []
        if isinstance(extra, list):
            keywords_text = keywords_text + ". Also use: " + ", ".join(extra[:5]) if extra else keywords_text
        else:
            keywords_text = keywords_text + ". Primary: " + str(extra) if extra else keywords_text
    category_style = CATEGORY_PROMPTS.get(category, CATEGORY_PROMPTS["Lifestyle & Misc"])
    research_block = f"\n\nResearch context (use for all three):\n{research_context}" if research_context else ""
    from datetime import date
    _lu = date.today().strftime("%B %d, %Y")
    _lu_block = f"> **Last Updated:** {_lu} | **Sources:** Celebrity Net Worth, Forbes"
    _lu_block_nw = (
        f"> **Last Updated:** {_lu} | **Citations:** Only sources actually linked in the body. "
        f'Do **not** list "Celebrity Net Worth" or "Forbes" unless linked to a page about this person.'
    )
    _yt_note = f' In "First Media Appearance" put exactly [YOUTUBE_EMBED: {youtube_url}] on its own line (no raw HTML/iframe), then 1-2 paragraphs on the interview.' if youtube_url else ' In "First Media Appearance" write that no suitable interview video was found and add 1-2 paragraphs on early media significance.'
    prompt = f"""Write THREE distinct blog post bodies for the celebrity "{topic}". All in one response, separated by exact markers.

Shared: Topic "{topic}", Keywords: {keywords_text}, Category: {category}.{research_block}

Writing style: {category_style}

Cite sources as markdown links only (e.g. [Celebrity Net Worth](https://www.celebritynetworth.com/)) — never plain text.

Output format — use these exact section headers (each body is 400-700 words, markdown with ## and ###):

---PARENT_BODY---
(Article 1: OVERVIEW, 2,000-3,000 words. Use these H2 sections in order: Introduction (200-250 words), Quick Facts (table Attribute|Details), Early Life (250-300 words + 3 bullets), First Media Appearance (YouTube or brief note), Career Overview (400-500 words: Beginnings, Transition, First Leading Role), Personal Life (250-300 words), Filmography (paragraph and bullet format only — Films, then Television Work, then Music/Other; no tables; use full sentences so content is not tiny), Awards & Nominations (200-250 words), Public Image & Legacy (200-300 words), Conclusion (150-200 words).{_yt_note} Include: {_lu_block})
...full markdown body...

---CHILD_0_BODY---
(Article 2: RELIGION — What Religion is [Name]? style. Use H2 sections: Introduction (with Attribute|Details table), Upbringing & Religious Roots, Current Spiritual Beliefs, Career & Artistic Expression (with Project|Impact|Expert Opinion table), Comparisons with Other Celebrities, Data & Expert Opinions, Conclusion. Include expert-style quotes. Include: {_lu_block})
...full markdown body...

---CHILD_1_BODY---
(Article 3: NET WORTH. No religion/faith/church content. For U.S. politicians prioritize disclosure-based sources (OpenSecrets, House Clerk) with reporting year. Wealth table: | Category | Estimated amount or range | % of estimated net worth | (rows = asset components; sum 100%%); put **annual salary** in a separate line or table, not as a fake %% of net worth. Include disclaimer and: {_lu_block_nw})
...full markdown body...

Return ONLY the three sections with the exact markers ---PARENT_BODY---, ---CHILD_0_BODY---, ---CHILD_1_BODY--- and content. No other preamble."""
    result = await generate_blog_seo_content(prompt, max_tokens=4500, temperature=0.6)
    # Parse sections
    def _extract(s: str, start: str, end: str) -> str:
        i = s.find(start)
        if i == -1:
            return ""
        i += len(start)
        j = s.find(end, i)
        if j == -1:
            return s[i:].strip()
        return s[i:j].strip()
    parent_body = _extract(result, "---PARENT_BODY---", "---CHILD_0_BODY---").strip()
    c1_body = _extract(result, "---CHILD_0_BODY---", "---CHILD_1_BODY---").strip()
    c2_body = _extract(result, "---CHILD_1_BODY---", "---").strip()
    if not parent_body:
        parent_body = await generate_blog_body(topic, description, keywords, category, research_context=research_context, content_intent="parent_overview", keyword_strategy=keyword_strategy, youtube_url=youtube_url)
    if not c1_body:
        c1_body = await generate_blog_body(topic, description, keywords, category, research_context=research_context, content_intent="religion", keyword_strategy=keyword_strategy)
    if not c2_body:
        c2_body = await generate_blog_body(topic, description, keywords, category, research_context=research_context, content_intent="net_worth", keyword_strategy=keyword_strategy)
    return (parent_body, c1_body, c2_body)


async def generate_tree_faqs_queries_batch(
    topic: str,
    description: Optional[str],
    keywords: List[str],
    category: str,
    research_context: Optional[str],
    include_religion: bool = True,
) -> Dict[str, Any]:
    """One call: parent + child_0 + child_1 faqs and related_queries; or parent + net worth only when include_religion is False."""
    import json
    research_block = f"\n\nResearch excerpt:\n{(research_context or '')[:1500]}" if research_context else ""
    if not include_religion:
        prompt = f"""Topic: "{topic}". Category: {category}.{f' Context: {description}' if description else ''}{research_block}

There is NO religion article. Generate FAQs and related search queries for 2 articles only: parent overview and net worth.

**Do not** include any FAQ or query about religion, faith, church, or spirituality.

Return ONE JSON object only:
{{"parent_faqs": [{{"question":"...","answer":"..."}}, ...5], "child_1_faqs": [...5], "parent_queries": [{{"query":"q1","answer":"1-2 sentence answer."}}, ...8], "child_1_queries": [...8]}}

Each faqs array: 5 items; each FAQ answer 2-3 sentences. Each queries array: 8 items with "query" and "answer". No other text."""
        result = await generate_blog_seo_content(prompt, max_tokens=2800, temperature=0.5)
        try:
            clean = result.strip()
            for prefix in ("```json", "```"):
                if clean.startswith(prefix):
                    clean = clean[len(prefix) :].strip()
            if clean.endswith("```"):
                clean = clean[:-3].strip()
            data = json.loads(clean)

            def faq_list(v):
                if not isinstance(v, list):
                    return []
                return [{"question": str(x.get("question", "")), "answer": str(x.get("answer", ""))} for x in v[:5]]

            def q_list(v):
                if not isinstance(v, list):
                    return []
                out = []
                for x in v[:8]:
                    if isinstance(x, dict):
                        out.append({"query": str(x.get("query", "")).strip(), "answer": str(x.get("answer", "")).strip()})
                    else:
                        out.append({"query": str(x).strip(), "answer": ""})
                return out

            return {
                "parent_faqs": faq_list(data.get("parent_faqs")),
                "child_0_faqs": [],
                "child_1_faqs": faq_list(data.get("child_1_faqs")),
                "parent_queries": q_list(data.get("parent_queries")),
                "child_0_queries": [],
                "child_1_queries": q_list(data.get("child_1_queries")),
            }
        except (json.JSONDecodeError, Exception):
            default_faq = [{"question": f"What about {topic}?", "answer": f"This article covers {topic}."}]
            default_q = [{"query": f"{topic} overview", "answer": ""}, {"query": f"{topic} net worth", "answer": ""}]
            return {
                "parent_faqs": default_faq,
                "child_0_faqs": [],
                "child_1_faqs": default_faq,
                "parent_queries": default_q,
                "child_0_queries": [],
                "child_1_queries": default_q,
            }

    prompt = f"""Topic: "{topic}". Category: {category}.{f' Context: {description}' if description else ''}{research_block}

Generate FAQs and related search queries for 3 articles (parent overview, religion/spiritual beliefs, net worth). Child_0 FAQs and queries must be religion-focused (e.g. "What religion is [name]?", "Is [name] religious?"). Child_1 FAQs and queries must be **money and net worth only** — no religion, faith, or church. Return ONE JSON object only:

{{"parent_faqs": [{{"question":"...","answer":"..."}}, ...5], "child_0_faqs": [...5], "child_1_faqs": [...5], "parent_queries": [{{"query":"q1","answer":"1-2 sentence answer."}}, ...8], "child_0_queries": [...8], "child_1_queries": [...8]}}

Each faqs array: 5 items; each FAQ answer must be 2-3 sentences (about 2-3 lines) with a bit more detail—avoid one-line answers. Each queries array: 8 items with "query" (search phrase) and "answer" (1-2 sentence answer for each). No other text."""
    result = await generate_blog_seo_content(prompt, max_tokens=3800, temperature=0.5)
    try:
        clean = result.strip()
        for prefix in ("```json", "```"):
            if clean.startswith(prefix):
                clean = clean[len(prefix):].strip()
        if clean.endswith("```"):
            clean = clean[:-3].strip()
        data = json.loads(clean)
        def faq_list(v):
            if not isinstance(v, list):
                return []
            return [{"question": str(x.get("question", "")), "answer": str(x.get("answer", ""))} for x in v[:5]]
        def q_list(v):
            if not isinstance(v, list):
                return []
            out = []
            for x in v[:8]:
                if isinstance(x, dict):
                    out.append({"query": str(x.get("query", "")).strip(), "answer": str(x.get("answer", "")).strip()})
                else:
                    out.append({"query": str(x).strip(), "answer": ""})
            return out
        return {
            "parent_faqs": faq_list(data.get("parent_faqs")),
            "child_0_faqs": faq_list(data.get("child_0_faqs")),
            "child_1_faqs": faq_list(data.get("child_1_faqs")),
            "parent_queries": q_list(data.get("parent_queries")),
            "child_0_queries": q_list(data.get("child_0_queries")),
            "child_1_queries": q_list(data.get("child_1_queries")),
        }
    except (json.JSONDecodeError, Exception):
        default_faq = [{"question": f"What about {topic}?", "answer": f"This article covers {topic}."}]
        default_q = [{"query": f"{topic} overview", "answer": ""}, {"query": f"{topic} details", "answer": ""}]
        return {
            "parent_faqs": default_faq,
            "child_0_faqs": default_faq,
            "child_1_faqs": default_faq,
            "parent_queries": default_q,
            "child_0_queries": default_q,
            "child_1_queries": default_q,
        }


async def generate_faqs(
    topic: str,
    description: Optional[str] = None,
    keywords: List[str] = None,
    category: str = "Technology & AI",
    research_context: Optional[str] = None,
    content_intent: Optional[str] = None
) -> List[Dict[str, str]]:
    """Generate FAQ section in schema-ready format for Rank Math"""
    keywords_text = ", ".join(keywords) if keywords else "relevant terms"
    category_style = CATEGORY_PROMPTS.get(category, CATEGORY_PROMPTS["Lifestyle & Misc"])
    description_text = f"\nContext: {description}" if description else ""
    research_block = f"\n\nResearch context (use to inform your response):\n{research_context}" if research_context else ""
    intent_block = ""
    if content_intent and content_intent in CONTENT_INTENT_PROMPTS:
        intent_block = f"\n\nContent intent (follow this focus):\n{CONTENT_INTENT_PROMPTS[content_intent]}"
    
    prompt = f"""Generate 5 frequently asked questions (FAQs) with answers for the following blog topic.

Topic: "{topic}"{description_text}
Related Keywords: {keywords_text}
Content Category: {category}{research_block}{intent_block}

Writing Style Guidelines:
{category_style}

Requirements:
1. Create 5 relevant, commonly-asked questions
2. Include target keywords naturally in questions and answers
3. Keep answers concise but informative (2-4 sentences each)
4. Questions should be what real users would search for
5. Answers should provide genuine value

Format your response as a JSON array with this exact structure:
[
  {{"question": "Question 1?", "answer": "Answer 1."}},
  {{"question": "Question 2?", "answer": "Answer 2."}},
  {{"question": "Question 3?", "answer": "Answer 3."}},
  {{"question": "Question 4?", "answer": "Answer 4."}},
  {{"question": "Question 5?", "answer": "Answer 5."}}
]

Return ONLY the JSON array, no other text."""

    import json
    result = await generate_blog_seo_content(prompt, max_tokens=1500, temperature=0.7)
    
    # Parse the JSON response
    try:
        # Clean up the response in case there's markdown formatting
        result_clean = result.strip()
        if result_clean.startswith("```json"):
            result_clean = result_clean[7:]
        if result_clean.startswith("```"):
            result_clean = result_clean[3:]
        if result_clean.endswith("```"):
            result_clean = result_clean[:-3]
        result_clean = result_clean.strip()
        
        faqs = json.loads(result_clean)
        return faqs
    except json.JSONDecodeError:
        # If parsing fails, return a default structure
        return [
            {"question": f"What is {topic}?", "answer": f"This article covers {topic} in detail."},
            {"question": f"Why is {topic} important?", "answer": f"Understanding {topic} is essential for making informed decisions."},
            {"question": f"How can I learn more about {topic}?", "answer": "Continue reading this comprehensive guide for all the details."}
        ]


async def generate_related_queries(
    topic: str,
    description: Optional[str] = None,
    keywords: List[str] = None,
    category: str = "Technology & AI",
    research_context: Optional[str] = None,
    content_intent: Optional[str] = None
) -> List[str]:
    """Generate related search queries for content clustering"""
    keywords_text = ", ".join(keywords) if keywords else "relevant terms"
    description_text = f"\nContext: {description}" if description else ""
    research_block = f"\n\nResearch context (use to inform your response):\n{research_context}" if research_context else ""
    intent_block = ""
    if content_intent and content_intent in CONTENT_INTENT_PROMPTS:
        intent_block = f"\n\nContent intent (queries should align with this focus):\n{CONTENT_INTENT_PROMPTS[content_intent]}"
    
    prompt = f"""Generate 8 related search queries that users might search for related to this blog topic.

Topic: "{topic}"{description_text}
Related Keywords: {keywords_text}
Content Category: {category}{research_block}{intent_block}

Requirements:
1. Create queries that real users would type into Google
2. Include a mix of:
   - "How to" queries
   - "What is" queries  
   - Comparison queries
   - Best/Top queries
   - Specific questions
3. Each query should be related but slightly different angle
4. Include long-tail keyword variations

Format your response as a JSON array of strings:
["query 1", "query 2", "query 3", "query 4", "query 5", "query 6", "query 7", "query 8"]

Return ONLY the JSON array, no other text."""

    import json
    result = await generate_blog_seo_content(prompt, max_tokens=500, temperature=0.8)
    
    # Parse the JSON response
    try:
        result_clean = result.strip()
        if result_clean.startswith("```json"):
            result_clean = result_clean[7:]
        if result_clean.startswith("```"):
            result_clean = result_clean[3:]
        if result_clean.endswith("```"):
            result_clean = result_clean[:-3]
        result_clean = result_clean.strip()
        
        queries = json.loads(result_clean)
        return queries
    except json.JSONDecodeError:
        # If parsing fails, return default queries
        return [
            f"What is {topic}",
            f"How to {topic}",
            f"Best {topic} guide",
            f"{topic} tips and tricks",
            f"{topic} for beginners"
        ]


async def generate_internal_links(
    topic: str,
    source_intent: str,
    article_titles: Dict[str, str],
    research_context: Optional[str] = None,
    religion_skipped: bool = False,
) -> List[Dict[str, Any]]:
    """Generate internal link suggestions from this article to parent/child nodes. article_titles: {parent, child_0, child_1} -> title. When religion_skipped, child_0 is the net worth article only."""
    import json
    titles_block = "\n".join(f"- {k}: {v}" for k, v in article_titles.items() if (v or "").strip())
    research_snippet = (research_context or "")[:800] if research_context else "N/A"
    if religion_skipped:
        if source_intent == "net_worth":
            cluster_note = """
IMPORTANT: There is NO religion article. You are writing the NET WORTH article (it is the only child article). Link ONLY to the parent/overview article.
Valid targets: "parent" only. Do NOT use "child_0" or "child_1".
Anchor text must relate to career, biography, or overview — never religion."""
            valid_targets = ('"parent"',)
        else:
            cluster_note = """
IMPORTANT: There is NO religion article in this cluster. The article labeled child_0 is the NET WORTH companion (not religion).
Valid targets: "parent" and "child_0" only. Do NOT use "child_1".
Anchor text must relate to career/overview or net worth/wealth/finances only — never religion, faith, spirituality, or church."""
            valid_targets = ('"parent"', '"child_0"')
    else:
        cluster_note = ""
        valid_targets = '"parent", "child_0", "child_1"'
    prompt = f"""Topic: {topic}
This article's intent: {source_intent}
Titles of all articles in the cluster:
{titles_block}

Research context (excerpt): {research_snippet}
{cluster_note}

Suggest 1-2 internal links FROM this article TO other articles in the cluster. For each link provide:
- target: one of {valid_targets}
- anchor_text: natural phrase for the link text
- context: where in the article to place it (e.g. "introduction", "personal life section")

Return a JSON array only, no other text:
[{{"target": "parent", "anchor_text": "...", "context": "..."}}, ...]
Do not link to the same article (source)."""

    try:
        result = await generate_blog_seo_content(prompt, max_tokens=400, temperature=0.5)
        result_clean = result.strip()
        for prefix in ("```json", "```"):
            if result_clean.startswith(prefix):
                result_clean = result_clean[len(prefix):].strip()
        if result_clean.endswith("```"):
            result_clean = result_clean[:-3].strip()
        data = json.loads(result_clean)
        if not isinstance(data, list):
            return []
        if religion_skipped and source_intent == "net_worth":
            allowed = {"parent"}
        elif religion_skipped:
            allowed = {"parent", "child_0"}
        else:
            allowed = {"parent", "child_0", "child_1"}
        out = []
        for item in data:
            if isinstance(item, dict):
                t = item.get("target")
                if religion_skipped and t == "child_1":
                    t = "child_0"
                if t in allowed:
                    out.append({
                        "target": t,
                        "anchor_text": str(item.get("anchor_text", "")).strip() or "read more",
                        "context": str(item.get("context", "")).strip() or "article body",
                    })
        return out[:4]
    except (json.JSONDecodeError, Exception):
        return []


def build_faq_schema(faqs: List[Dict[str, str]]) -> Dict[str, Any]:
    """Build FAQPage schema for rich snippets. faqs: list of {question, answer}."""
    if not faqs:
        return {}
    main_entity = []
    for faq in faqs[:10]:
        q = str(faq.get("question", "")).strip()
        a = str(faq.get("answer", "")).strip()
        if not q:
            continue
        main_entity.append({
            "@type": "Question",
            "name": q,
            "acceptedAnswer": {"@type": "Answer", "text": a or "—"},
        })
    if not main_entity:
        return {}
    return {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": main_entity,
    }


def build_breadcrumb_schema(items: List[Dict[str, str]]) -> Dict[str, Any]:
    """Build BreadcrumbList schema. items: [{"name": "Celebrities", "url": "/category/celebrities"}, ...]."""
    if not items:
        return {}
    elements = []
    for i, it in enumerate(items[:8]):
        name = str(it.get("name", "")).strip() or f"Item {i+1}"
        url = str(it.get("url", "")).strip()
        elements.append({
            "@type": "ListItem",
            "position": i + 1,
            "name": name,
            **({"item": url} if url else {}),
        })
    if not elements:
        return {}
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": elements,
    }


async def generate_schema_markup(
    content_intent: str,
    research_context: Optional[str],
    article_data: Dict[str, Any],
    faqs: Optional[List[Dict[str, str]]] = None,
    breadcrumb_items: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """Generate Schema.org JSON-LD by intent. Returns single schema or @graph with Person/Article + FAQPage + BreadcrumbList."""
    import json
    topic = article_data.get("topic", "")
    title = article_data.get("title", "")
    meta_desc = article_data.get("meta_description", "")
    url = article_data.get("url", "")
    body_snippet = (article_data.get("body_content") or "")[:500]
    research_snippet = (research_context or "")[:600] if research_context else ""

    base = {
        "@context": "https://schema.org",
        "name": topic,
        "description": meta_desc or title,
    }
    if url:
        base["mainEntityOfPage"] = {"@type": "WebPage", "@id": url}

    if content_intent == "parent_overview":
        base["@type"] = "Person"
        base["jobTitle"] = "Public Figure"
        prompt_knows = f"""From this research about {topic}, extract 2-4 "knowsAbout" topics (e.g. "Comedy", "Banjo", "Screenwriting"). Return a JSON array of strings only."""
        try:
            r = await generate_blog_seo_content(
                f"Research excerpt:\n{research_snippet}\n\n{prompt_knows}", max_tokens=150, temperature=0.3
            )
            r = r.strip().strip("[]").strip('"')
            if r:
                base["knowsAbout"] = [s.strip().strip('"') for s in r.split(",")][:4]
        except Exception:
            pass
        prompt_person = f"""From this research about {topic}, extract: birthDate (YYYY-MM-DD), birthPlace (city, country), jobTitle (array e.g. ["Comedian","Actor","Musician","Writer"]). Return JSON: {{"birthDate":"...","birthPlace":"...","jobTitle":["..."]}}. Only JSON."""
        try:
            r = await generate_blog_seo_content(
                f"Research:\n{research_snippet}\n\n{prompt_person}", max_tokens=120, temperature=0.2
            )
            data = json.loads(r.strip().strip("`").strip())
            if data.get("birthDate"):
                base["birthDate"] = data["birthDate"]
            if data.get("birthPlace"):
                base["birthPlace"] = data["birthPlace"]
            if data.get("jobTitle"):
                jt = data["jobTitle"] if isinstance(data["jobTitle"], list) else [data["jobTitle"]]
                base["jobTitle"] = [str(x) for x in jt[:6]]
        except Exception:
            pass
        prompt_nw = f"""From this research about {topic}, extract a single net worth figure (number in millions, e.g. 140) and currency. Return JSON: {{"value": number, "currency": "USD"}}. Only JSON."""
        try:
            r = await generate_blog_seo_content(
                f"Research:\n{research_snippet}\n\n{prompt_nw}", max_tokens=80, temperature=0.2
            )
            data_nw = json.loads(r.strip().strip("`").strip())
            if data_nw.get("value") is not None:
                val = data_nw["value"]
                if isinstance(val, (int, float)) and val < 10000:
                    val = int(val * 1e6)
                base["netWorth"] = {
                    "@type": "MonetaryAmount",
                    "currency": data_nw.get("currency", "USD"),
                    "value": val,
                }
        except Exception:
            pass
        graph = [base]
        if faqs:
            faq_schema = build_faq_schema(faqs)
            if faq_schema:
                graph.append(faq_schema)
        if breadcrumb_items:
            bc = build_breadcrumb_schema(breadcrumb_items)
            if bc:
                graph.append(bc)
        if len(graph) > 1:
            return {"@context": "https://schema.org", "@graph": graph}
        return base

    if content_intent == "religion":
        base["@type"] = ["Person", "Article"]
        base["headline"] = title
        base["description"] = (meta_desc or title or "") + " (Religious and spiritual beliefs.)"
        if body_snippet:
            base["articleBody"] = body_snippet[:300] + ("..." if len(body_snippet) > 300 else "")
        graph = [base]
        if faqs:
            faq_schema = build_faq_schema(faqs)
            if faq_schema:
                graph.append(faq_schema)
        if breadcrumb_items:
            bc = build_breadcrumb_schema(breadcrumb_items)
            if bc:
                graph.append(bc)
        if len(graph) > 1:
            return {"@context": "https://schema.org", "@graph": graph}
        return base

    if content_intent == "net_worth":
        base["@type"] = ["Person", "Article"]
        base["headline"] = title
        prompt = f"""From this research about {topic}, extract a single net worth figure and currency (e.g. 250, "USD"). Return JSON: {{"value": number_or_string, "currency": "USD"}}. If unknown return {{"value": null, "currency": "USD"}}. Only JSON."""
        try:
            r = await generate_blog_seo_content(
                f"Research:\n{research_snippet}\n\n{prompt}", max_tokens=80, temperature=0.2
            )
            data_nw = json.loads(r.strip().strip("`").strip())
            if data_nw.get("value") is not None:
                val = data_nw["value"]
                if isinstance(val, (int, float)) and val < 10000:
                    val = int(val * 1e6)
                base["netWorth"] = {
                    "@type": "MonetaryAmount",
                    "currency": data_nw.get("currency", "USD"),
                    "value": val,
                }
        except Exception:
            pass
        graph = [base]
        if faqs:
            faq_schema = build_faq_schema(faqs)
            if faq_schema:
                graph.append(faq_schema)
        if breadcrumb_items:
            bc = build_breadcrumb_schema(breadcrumb_items)
            if bc:
                graph.append(bc)
        if len(graph) > 1:
            return {"@context": "https://schema.org", "@graph": graph}
        return base

    return base


async def extract_keyword_clusters(
    research_context: str,
    topic: str
) -> Dict[str, Any]:
    """Extract primary, secondary, LSI, PAA, long-tail from research for content optimization."""
    import json
    snippet = (research_context or "")[:2000]
    prompt = f"""Analyze this research about "{topic}" and extract keyword clusters for SEO content.

Research excerpt:
{snippet}

Return a JSON object with these keys only:
- primary: one high-volume primary keyword (string)
- secondary: array of 3-5 medium-volume keywords
- lsi: array of 10-15 LSI/semantic keywords
- paa: array of 5 "People Also Ask" style questions
- long_tail: array of 5 long-tail keyword variations

Return ONLY the JSON object, no other text."""

    try:
        result = await generate_blog_seo_content(prompt, max_tokens=600, temperature=0.4)
        result_clean = result.strip()
        for prefix in ("```json", "```"):
            if result_clean.startswith(prefix):
                result_clean = result_clean[len(prefix):].strip()
        if result_clean.endswith("```"):
            result_clean = result_clean[:-3].strip()
        data = json.loads(result_clean)
        return {
            "primary": data.get("primary") or topic,
            "secondary": data.get("secondary") or [],
            "lsi": data.get("lsi") or [],
            "paa": data.get("paa") or [],
            "long_tail": data.get("long_tail") or [],
        }
    except (json.JSONDecodeError, Exception):
        return {"primary": topic, "secondary": [], "lsi": [], "paa": [], "long_tail": []}


def get_available_models() -> List[Dict[str, Any]]:
    """Get list of available models (simplified)"""
    return [
 
    ]


# Predefined categories for keyword categorization
KEYWORD_CATEGORIES = [
    "Celebrities & Public Figures",
    "Sports",
    "Finance & Economy",
    "Technology & AI",
    "News & Politics",
    "Weather & Local Conditions",
    "Games & Entertainment",
    "Lifestyle & Misc"
]

# Category-specific writing style prompts for blog generation
CATEGORY_PROMPTS = {
    "Celebrities & Public Figures": """Write in an engaging, news-style tone that captures reader attention. 
Use conversational language that feels like insider knowledge. Include relevant background context, 
recent developments, and interesting facts. Appeal to fans and casual readers alike with a balance 
of informative and entertaining content.""",
    
    "Sports": """Write with energy and passion that resonates with sports enthusiasts. 
Include relevant statistics, historical context, and expert analysis where appropriate. 
Use action-oriented language that captures the excitement of competition. Balance technical 
insights with accessible explanations for casual fans.""",
    
    "Finance & Economy": """Write authoritatively with data-driven insights and professional analysis. 
Use clear, precise language that conveys expertise while remaining accessible. Include relevant 
market context, economic indicators, and practical implications. Maintain objectivity while 
providing actionable insights for readers.""",
    
    "Technology & AI": """Write in a technical yet accessible style that educates and informs. 
Explain complex concepts clearly without oversimplifying. Include practical applications, 
industry trends, and future implications. Balance technical depth with readability for 
both experts and newcomers to the topic.""",
    
    "News & Politics": """Write in a neutral, factual tone that prioritizes accuracy and balance. 
Present multiple perspectives where relevant without bias. Include context, background information, 
and verified facts. Use professional journalistic standards while remaining engaging and informative.""",
    
    "Weather & Local Conditions": """Write in an informative, practical style focused on usefulness. 
Provide clear, actionable information that helps readers plan and prepare. Include relevant 
local context and practical tips. Use straightforward language that prioritizes clarity 
and immediate applicability.""",
    
    "Games & Entertainment": """Write in a fun, engaging style that connects with enthusiasts. 
Use community-familiar terminology and references appropriately. Include relevant reviews, 
comparisons, and recommendations. Balance excitement with useful information that helps 
readers make decisions or enhance their experience.""",
    
    "Lifestyle & Misc": """Write in a friendly, relatable style that connects with everyday readers. 
Use practical, actionable advice and personal touches where appropriate. Include tips, 
recommendations, and real-world applications. Maintain an approachable tone that feels 
like advice from a knowledgeable friend."""
}

async def assess_religion_verifiability(topic: str, research_context: Optional[str]) -> bool:
    """Return True only if research clearly supports a factual religion/spirituality article; else False (skip Religion post)."""
    import json
    if not (research_context and research_context.strip()):
        return False
    prompt = f"""You decide if we can write a VERIFIED article about this person's religion or spiritual beliefs (not rumors).

Person/topic: "{topic}"

Research excerpt:
{(research_context or "")[:4500]}

Return ONLY valid JSON, no markdown:
{{"verifiable": true or false, "reason": "short"}}

Set "verifiable" to true ONLY if the research explicitly and credibly states their religion, denomination, or clearly described religious/spiritual practice (enough to write a factual article).

Set "verifiable" to false if religion is absent, unknown, "private", "not discussed", vague, speculative, disputed without resolution, or only tabloid-level claims.

When uncertain, use false."""
    try:
        result = await generate_blog_seo_content(prompt, max_tokens=120, temperature=0.1)
        clean = result.strip()
        for prefix in ("```json", "```"):
            if clean.startswith(prefix):
                clean = clean[len(prefix) :].strip()
        if clean.endswith("```"):
            clean = clean[:-3].strip()
        data = json.loads(clean)
        return bool(data.get("verifiable")) is True
    except (json.JSONDecodeError, Exception):
        return False


# Content intent prompts for tree-based (celebrity) blog generation
CONTENT_INTENT_PROMPTS = {
    "parent_overview": """This article is the OVERVIEW/PARENT: focus on who the person is, why they are famous or trending, career highlights, and general background and public interest. Title pattern: "Who Is [Name]? Career, Fame, and Public Influence" or similar.""",
    "religion": """This article is the RELIGION/SPIRITUAL BELIEFS child for whatreligionisinfo.com: focus on religious upbringing, current spiritual beliefs, how faith influences their work, and expert-informed analysis. Title pattern: "What Religion is [Name]? [Intriguing Subtitle]" or similar.""",
    "net_worth": """This article is the NET WORTH/FINANCIAL child: focus on net worth, compensation, assets, and liabilities—not religion or personal beliefs. Title pattern: "[Name] Net Worth: Income, Endorsements & Assets" or similar. Do not mention faith, church, or spirituality here. The religion and net worth articles must stay separate.""",
}

async def _categorize_with_groq(prompt: str) -> str:
    """Categorize using Groq API with llama-4-scout model."""
    if not settings.groq_api_key:
        raise ValueError("Groq API key not configured")
    return await _groq_chat_completion(
        messages=[{"role": "user", "content": prompt}],
        model=KEYWORD_CATEGORIZATION_MODEL_GROQ,
        max_tokens=50,
        temperature=0.3,
        timeout_seconds=60.0,
    )


def _validate_category(category: str) -> Optional[str]:
    """Validate and normalize a category string"""
    if not category:
        return None
    
    category = category.strip()
    
    # Remove quotes if present
    if category.startswith('"') and category.endswith('"'):
        category = category[1:-1]
    if category.startswith("'") and category.endswith("'"):
        category = category[1:-1]
    
    # Exact match
    if category in KEYWORD_CATEGORIES:
        return category
    
    # Case-insensitive match
    category_lower = category.lower()
    for cat in KEYWORD_CATEGORIES:
        if cat.lower() == category_lower:
            return cat
    
    # Partial match
    for cat in KEYWORD_CATEGORIES:
        if cat.lower() in category_lower or category_lower in cat.lower():
            return cat
    
    return None


async def categorize_keyword(keyword: str) -> Optional[str]:
    """
    Categorize a keyword using Groq (meta-llama/llama-4-scout-17b-16e-instruct).
    """
    if not settings.groq_api_key:
        raise ValueError("No LLM API key configured (set GROQ_API_KEY)")

    categories_text = "\n".join([f"{i+1}. {cat}" for i, cat in enumerate(KEYWORD_CATEGORIES)])

    prompt = f"""Categorize the following keyword into ONE of these categories:
{categories_text}

Keyword: "{keyword}"

Respond with ONLY the category name (e.g., "Sports" or "Finance & Economy"). Do not include any explanation or additional text."""

    try:
        response = await _categorize_with_groq(prompt)
        return _validate_category(response)
    except Exception as e:
        print(f"Groq categorization failed for '{keyword}': {str(e)}")
    return None


async def select_featured_image(
    blog_title: str,
    blog_meta: str,
    images: List[Dict[str, Any]],
) -> int:
    """
    Given blog title, meta description, and a list of image dicts (each with title, source, link),
    ask the LLM to pick the best image (1-based index). Returns 0-based index.
    """
    if not images:
        return 0
    if len(images) == 1:
        return 0
    lines = []
    for i, img in enumerate(images, 1):
        title = img.get("title", "")[:200]
        source = img.get("source", "")[:100]
        link = img.get("link", "")[:150]
        lines.append(f"{i}. Title: {title} | Source: {source} | Link: {link}")
    images_block = "\n".join(lines)
    n = len(images)
    prompt = f"""Given this blog post, choose the single best image to use as the featured image.

Blog title: {blog_title[:200]}
Meta description: {blog_meta[:300]}

Images (choose one by number 1-{n}):
{images_block}

Reply with ONLY the number (1 to {n}) of the most relevant and high-quality image. No other text."""
    try:
        response = await generate_content(
            prompt,
            max_tokens=50,
            temperature=0.3,
        )
        text = (response or "").strip()
        # Extract first number found (1-10 or 1-n)
        match = re.search(r"\b(10|\d)\b", text)
        if match:
            one_based = int(match.group(1))
            if 1 <= one_based <= n:
                return one_based - 1
        for part in text.replace(",", " ").split():
            if part.isdigit():
                idx = int(part)
                if 1 <= idx <= n:
                    return idx - 1
        return 0
    except Exception:
        return 0

