"""Substack blog import functionality.

Fetches posts from a Substack blog and converts them to markdown files.
"""

from __future__ import annotations

import json
import re
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify as md


def normalize_substack_url(input_str: str) -> str:
    """Convert username or URL to base Substack URL.

    Args:
        input_str: Either a username (e.g., "xiqo") or full URL (e.g., "https://xiqo.substack.com/")

    Returns:
        Base Substack URL (e.g., "https://xiqo.substack.com")
    """
    if input_str.startswith('http://') or input_str.startswith('https://'):
        parsed = urlparse(input_str)
        return f"https://{parsed.netloc}"
    else:
        # Assume it's a username
        return f"https://{input_str}.substack.com"


def fetch_posts_from_api(base_url: str, limit: int = 12, offset: int = 0) -> list[dict]:
    """Fetch posts from Substack API.

    Args:
        base_url: Base Substack URL (e.g., "https://xiqo.substack.com")
        limit: Number of posts to fetch per request
        offset: Pagination offset

    Returns:
        List of post metadata dictionaries

    Raises:
        httpx.HTTPStatusError: If API request fails
    """
    api_url = f"{base_url}/api/v1/archive"
    params = {
        'sort': 'new',
        'limit': limit,
        'offset': offset
    }
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
    }

    response = httpx.get(api_url, params=params, headers=headers, timeout=30.0)
    response.raise_for_status()
    return response.json()


def list_all_posts(substack_input: str, delay: float = 0.5, max_requests: int | None = None) -> dict[str, list[str]]:
    """List all posts from a Substack blog, organized by section.

    Args:
        substack_input: Username or URL of Substack blog
        delay: Delay between API requests in seconds
        max_requests: Maximum number of API requests to make (None = unlimited)

    Returns:
        Dictionary mapping section names to lists of post URLs
    """
    base_url = normalize_substack_url(substack_input)

    print(f"Fetching posts from {base_url}...", file=sys.stderr)

    all_posts = []
    offset = 0
    limit = 50  # Fetch more per request to be efficient
    request_count = 0
    retry_delay = 5

    while True:
        # Check max requests limit
        if max_requests is not None and request_count >= max_requests:
            print(f"Reached max requests limit ({max_requests})", file=sys.stderr)
            break

        request_count += 1

        try:
            posts = fetch_posts_from_api(base_url, limit=limit, offset=offset)

            if not posts:
                print(f"No more posts found at offset {offset}", file=sys.stderr)
                break

            all_posts.extend(posts)
            print(f"Fetched {len(posts)} posts (total: {len(all_posts)})", file=sys.stderr)

            offset += len(posts)
            time.sleep(delay)

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                # Rate limited - wait and retry
                print(f"Rate limited, waiting {retry_delay}s before retrying...", file=sys.stderr)
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)
                continue
            else:
                raise

    # Organize posts by section
    results: dict[str, list[str]] = defaultdict(list)

    for post in all_posts:
        # Construct proper URL from slug
        slug = post.get('slug')

        if slug:
            # Use base_url + /p/ + slug
            url = f"{base_url}/p/{slug}"
        else:
            # Fall back to canonical_url
            url = post.get('canonical_url')

        if not url:
            continue

        # Get section name, default to "All Posts" if no section
        section_name = post.get('section_name') or 'All Posts'

        # Avoid duplicates
        if url not in results[section_name]:
            results[section_name].append(url)

    # Convert defaultdict to regular dict
    return dict(results)


def slugify(text: str) -> str:
    """Convert text to URL-friendly slug.

    Args:
        text: Text to slugify

    Returns:
        URL-friendly slug
    """
    text = text.lower()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_]+', '-', text)
    text = re.sub(r'-+', '-', text)
    return text.strip('-')


def fetch_substack_post(url: str) -> BeautifulSoup:
    """Fetch and parse a Substack post.

    Args:
        url: URL of the Substack post

    Returns:
        BeautifulSoup object with parsed HTML

    Raises:
        httpx.HTTPStatusError: If request fails
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
    }
    response = httpx.get(url, headers=headers, timeout=30.0)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def extract_post_metadata(soup: BeautifulSoup, url: str) -> dict:
    """Extract post metadata from parsed HTML.

    Args:
        soup: BeautifulSoup object with parsed HTML
        url: Original post URL

    Returns:
        Dictionary with keys: title, subtitle, date, slug, body
    """
    data = {}

    # Extract title
    title_elem = soup.find('h1', class_='post-title') or soup.find('h1')
    data['title'] = title_elem.get_text(strip=True) if title_elem else "Untitled"

    # Extract subtitle
    subtitle_elem = soup.find('h3', class_='subtitle') or soup.find('p', class_='subtitle')
    data['subtitle'] = subtitle_elem.get_text(strip=True) if subtitle_elem else ""

    # Extract date - try multiple sources
    data['date'] = None

    # 1. Try article:published_time meta tag
    date_meta = soup.find('meta', property='article:published_time')
    if date_meta and date_meta.get('content'):
        date_str = date_meta['content'][:10]  # YYYY-MM-DD
        data['date'] = datetime.strptime(date_str, '%Y-%m-%d')

    # 2. Try JSON-LD structured data (common in Substack)
    if not data['date']:
        for script in soup.find_all('script', type='application/ld+json'):
            try:
                ld_data = json.loads(script.string)
                # Handle both single object and array of objects
                if isinstance(ld_data, list):
                    for item in ld_data:
                        if item.get('datePublished'):
                            date_str = item['datePublished'][:10]
                            data['date'] = datetime.strptime(date_str, '%Y-%m-%d')
                            break
                elif ld_data.get('datePublished'):
                    date_str = ld_data['datePublished'][:10]
                    data['date'] = datetime.strptime(date_str, '%Y-%m-%d')
                if data['date']:
                    break
            except (json.JSONDecodeError, ValueError, KeyError, TypeError):
                continue

    # 3. Try time element with datetime attribute
    if not data['date']:
        time_elem = soup.find('time')
        if time_elem and time_elem.get('datetime'):
            date_str = time_elem['datetime'][:10]
            data['date'] = datetime.strptime(date_str, '%Y-%m-%d')

    # 4. Try og:article:published_time meta tag
    if not data['date']:
        og_meta = soup.find('meta', property='og:article:published_time')
        if og_meta and og_meta.get('content'):
            date_str = og_meta['content'][:10]
            data['date'] = datetime.strptime(date_str, '%Y-%m-%d')

    # 5. Look for date in post header/info sections
    if not data['date']:
        # Substack often has date in a specific class
        date_elem = soup.find(class_='post-date') or soup.find(class_='pencraft')
        if date_elem:
            # Try to parse common date formats like "Jan 15, 2024"
            date_text = date_elem.get_text(strip=True)
            for fmt in ['%b %d, %Y', '%B %d, %Y', '%Y-%m-%d', '%d %b %Y']:
                try:
                    data['date'] = datetime.strptime(date_text, fmt)
                    break
                except ValueError:
                    continue

    # Warn if no date found
    if not data['date']:
        print(f"Warning: Could not extract date from post: {url}", file=sys.stderr)

    # Generate slug
    data['slug'] = slugify(data['title'])

    # Find the post body
    body = soup.find('div', class_='body') or soup.find('div', class_='post-content') or soup.find('article')

    if not body:
        # Try finding the main content area
        body = soup.find('div', class_='available-content') or soup.find('div', class_='single-post')

    data['body'] = body
    data['url'] = url

    return data


def convert_to_markdown(soup: BeautifulSoup) -> str:
    """Convert post HTML to simple markdown with remote image links.

    Args:
        soup: BeautifulSoup object containing post body HTML

    Returns:
        Markdown content string
    """
    if not soup:
        return ""

    markdown_parts = []

    # Substack boilerplate patterns to skip
    boilerplate_patterns = [
        "Thanks for reading",
        "Subscribe for free",
        "support my work",
    ]

    # Exact matches to skip (case-insensitive)
    boilerplate_exact = ["subscribe", "share"]

    # Process each element in the body
    for element in soup.children:
        if not hasattr(element, 'name') or element.name is None:
            # Text node
            text = str(element).strip()
            if text:
                markdown_parts.append(text)
            continue

        # Convert HTML element to markdown
        html_str = str(element)
        markdown_text = md(html_str, heading_style='ATX', strip=['div'])
        markdown_text = markdown_text.strip()

        # Skip Substack boilerplate
        if markdown_text:
            is_boilerplate = (
                any(pattern.lower() in markdown_text.lower() for pattern in boilerplate_patterns) or
                markdown_text.strip().lower() in boilerplate_exact
            )
            if not is_boilerplate:
                markdown_parts.append(markdown_text)

    # Join and clean up the markdown
    content = "\n\n".join(markdown_parts)

    # Clean up excessive newlines
    content = re.sub(r'\n{3,}', '\n\n', content)

    return content.strip()


def import_substack_blog(username: str, context_dir: Path, delay: float = 0.01, max_posts: int | None = None) -> tuple[int, Path]:
    """Import all posts from a Substack blog to context directory.

    Args:
        username: Substack username or URL
        context_dir: Context directory where files will be saved
        delay: Delay between API requests in seconds
        max_posts: Maximum number of posts to import (None = all posts)

    Returns:
        Tuple of (number of posts imported, output directory path)

    Raises:
        httpx.HTTPStatusError: If API requests fail
        OSError: If directory creation or file writing fails
    """
    # Normalize username to extract just the username part
    base_url = normalize_substack_url(username)
    parsed = urlparse(base_url)
    username_clean = parsed.netloc.split('.')[0]  # Extract "xiqo" from "xiqo.substack.com"

    # Create output directory
    output_dir = context_dir / f"substack_posts_{username_clean}"
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Importing Substack posts to: {output_dir}")

    # Get all post URLs
    posts_by_section = list_all_posts(username, delay=delay, max_requests=max_posts//50 + 1 if max_posts is not None else None)

    # Flatten to single list of URLs
    all_urls = []
    for urls in posts_by_section.values():
        all_urls.extend(urls)

    # Apply max_posts limit if specified
    if max_posts is not None:
        all_urls = all_urls[:max_posts]

    print(f"\nImporting {len(all_urls)} posts...")

    imported_count = 0
    total = len(all_urls)

    for i, url in enumerate(all_urls, 1):
        try:
            # Show progress on a single updating line
            # Truncate URL if too long to fit nicely
            max_url_len = 100
            display_url = url if len(url) <= max_url_len else url[:max_url_len-3] + "..."
            print(f"\r  [{i}/{total}] {display_url:<{max_url_len}}", end="", flush=True)

            # Fetch and parse post
            soup = fetch_substack_post(url)
            metadata = extract_post_metadata(soup, url)

            # Convert to markdown
            content = convert_to_markdown(metadata['body'])

            # Create markdown file
            date_str = metadata['date'].strftime('%Y-%m-%d') if metadata['date'] else "unknown-date"
            filename = f"{date_str}-{metadata['slug']}.md"
            filepath = output_dir / filename

            # Build final markdown content
            markdown_output = f"# {metadata['title']}\n\n"

            if metadata['subtitle']:
                markdown_output += f"*{metadata['subtitle']}*\n\n"

            markdown_output += f"Published: {date_str}\n"
            markdown_output += f"Source: {url}\n\n"
            markdown_output += "---\n\n"
            markdown_output += content

            # Write file
            filepath.write_text(markdown_output, encoding='utf-8')

            imported_count += 1

            # Rate limiting
            if i < len(all_urls):  # Don't sleep after last post
                time.sleep(delay)

        except Exception as e:
            # Print error on new line, then continue progress line
            print(f"\n  Error importing {url}: {e}", file=sys.stderr)
            continue

    # Move to new line after progress is complete
    print()

    return imported_count, output_dir
