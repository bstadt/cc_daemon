"""Unit tests for Substack import functionality."""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
import pytest
from bs4 import BeautifulSoup

from claudeconnect.substack import (
    fetch_posts_from_api,
    convert_to_markdown,
    import_substack_blog,
)


class TestFetchPostsFromApi:
    """Test Substack API fetching."""

    @patch('httpx.get')
    def test_fetch_posts_success(self, mock_get):
        # Mock successful API response
        mock_response = Mock()
        mock_response.json.return_value = [
            {"slug": "post-1", "title": "Post 1"},
            {"slug": "post-2", "title": "Post 2"},
        ]
        mock_get.return_value = mock_response

        result = fetch_posts_from_api("https://xiqo.substack.com", limit=2, offset=0)

        assert len(result) == 2
        assert result[0]["slug"] == "post-1"
        mock_get.assert_called_once()

    @patch('httpx.get')
    def test_fetch_posts_http_error(self, mock_get):
        # Mock HTTP error
        mock_get.side_effect = httpx.HTTPStatusError(
            "404 Not Found",
            request=Mock(),
            response=Mock(status_code=404)
        )

        with pytest.raises(httpx.HTTPStatusError):
            fetch_posts_from_api("https://invalid.substack.com")


class TestConvertToMarkdown:
    """Test HTML to markdown conversion."""

    def test_simple_paragraph(self):
        html = "<p>Hello world</p>"
        soup = BeautifulSoup(html, 'html.parser')
        markdown = convert_to_markdown(soup)

        assert "Hello world" in markdown

    def test_filters_boilerplate(self):
        html = """
        <div class="body">
            <p>Real content here</p>
            <p>Thanks for reading and subscribe for free</p>
        </div>
        """
        soup = BeautifulSoup(html, 'html.parser')
        body = soup.find('div', class_='body')
        markdown = convert_to_markdown(body)

        assert "Real content" in markdown
        assert "Thanks for reading" not in markdown

    def test_handles_images(self):
        html = """
        <div>
            <p>Text before</p>
            <img src="https://example.com/image.jpg" alt="Test Image">
            <p>Text after</p>
        </div>
        """
        soup = BeautifulSoup(html, 'html.parser')
        markdown = convert_to_markdown(soup)

        assert "Text before" in markdown
        assert "Text after" in markdown
        assert "example.com/image.jpg" in markdown


class TestImportSubstackBlog:
    """Test full blog import functionality."""

    @patch('claudeconnect.substack.list_all_posts')
    @patch('claudeconnect.substack.fetch_substack_post')
    def test_import_creates_directory(self, mock_fetch, mock_list):
        # Mock list_all_posts to return one post
        mock_list.return_value = {
            "All Posts": ["https://xiqo.substack.com/p/test-post"]
        }

        # Mock fetch_substack_post to return HTML
        html = """
        <html>
            <head>
                <meta property="article:published_time" content="2024-01-15T10:00:00Z">
            </head>
            <body>
                <h1>Test Post</h1>
                <div class="body"><p>Content</p></div>
            </body>
        </html>
        """
        mock_fetch.return_value = BeautifulSoup(html, 'html.parser')

        # Create temp directory for test
        with tempfile.TemporaryDirectory() as tmpdir:
            context_dir = Path(tmpdir)

            count, output_dir = import_substack_blog("xiqo", context_dir, delay=0.0)

            assert count == 1
            assert output_dir.exists()
            assert output_dir.name == "substack_posts_xiqo"

            # Check that a markdown file was created
            md_files = list(output_dir.glob("*.md"))
            assert len(md_files) == 1
            assert md_files[0].name == "2024-01-15-test-post.md"

            # Check file content
            content = md_files[0].read_text()
            assert "# Test Post" in content
            assert "Published: 2024-01-15" in content

    @patch('claudeconnect.substack.list_all_posts')
    @patch('claudeconnect.substack.fetch_substack_post')
    def test_import_with_max_posts_limit(self, mock_fetch, mock_list):
        # Mock list_all_posts to return 5 posts
        mock_list.return_value = {
            "All Posts": [
                f"https://xiqo.substack.com/p/post-{i}" for i in range(5)
            ]
        }

        # Mock fetch to return different HTML for each post
        html_responses = [
            BeautifulSoup(f"""
            <html>
                <head>
                    <meta property="article:published_time" content="2024-01-{15+i:02d}T10:00:00Z">
                </head>
                <body>
                    <h1>Post Number {i}</h1>
                    <div class="body"><p>Content {i}</p></div>
                </body>
            </html>
            """, 'html.parser')
            for i in range(5)
        ]

        mock_fetch.side_effect = html_responses

        with tempfile.TemporaryDirectory() as tmpdir:
            context_dir = Path(tmpdir)

            # Import with limit of 2 posts
            count, output_dir = import_substack_blog("xiqo", context_dir, delay=0.0, max_posts=2)

            assert count == 2
            md_files = list(output_dir.glob("*.md"))
            assert len(md_files) == 2

    @patch('claudeconnect.substack.list_all_posts')
    @patch('claudeconnect.substack.fetch_substack_post')
    def test_import_handles_errors_gracefully(self, mock_fetch, mock_list):
        # Mock list_all_posts to return two posts
        mock_list.return_value = {
            "All Posts": [
                "https://xiqo.substack.com/p/post-1",
                "https://xiqo.substack.com/p/post-2",
            ]
        }

        # First call succeeds, second fails
        html = """
        <html>
            <body>
                <h1>Post 1</h1>
                <div class="body"><p>Content</p></div>
            </body>
        </html>
        """
        mock_fetch.side_effect = [
            BeautifulSoup(html, 'html.parser'),
            httpx.HTTPStatusError("404", request=Mock(), response=Mock(status_code=404))
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            context_dir = Path(tmpdir)

            # Should import 1 post successfully despite error on second
            count, output_dir = import_substack_blog("xiqo", context_dir, delay=0.0)

            assert count == 1
            md_files = list(output_dir.glob("*.md"))
            assert len(md_files) == 1
