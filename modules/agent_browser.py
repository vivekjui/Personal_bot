import asyncio
from typing import Optional, Dict, Any
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode
from modules.utils import logger

class AgentBrowser:
    """
    AI-native browser based on Crawl4AI.
    Optimized for high-speed scraping and AI-ready output (Markdown).
    """
    def __init__(self):
        self.crawler_config = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            word_count_threshold=10,
            remove_overlay_elements=True,
            process_iframes=True
        )

    async def get_page_markdown(self, url: str) -> Optional[str]:
        """Fetches a page and returns its content as clean Markdown."""
        async with AsyncWebCrawler() as crawler:
            try:
                result = await crawler.arun(url=url, config=self.crawler_config)
                if result.success:
                    return result.markdown
                else:
                    logger.warning(f"Crawl4AI failed for {url}: {result.error_message}")
                    return None
            except Exception as e:
                logger.error(f"Error in AgentBrowser for {url}: {e}")
                return None

    async def get_structured_data(self, url: str, schema: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Fetches a page and extracts structured data using a JSON schema."""
        # Crawl4AI supports extraction strategies (like LLMExtractionStrategy)
        # For monitoring, we can use simple pattern matching on markdown or structured extraction
        from crawl4ai.extraction_strategy import JsonCssExtractionStrategy
        
        config = CrawlerRunConfig(
            extraction_strategy=JsonCssExtractionStrategy(schema),
            cache_mode=CacheMode.BYPASS
        )
        
        async with AsyncWebCrawler() as crawler:
            try:
                result = await crawler.arun(url=url, config=config)
                if result.success:
                    import json
                    return json.loads(result.extracted_content)
                return None
            except Exception as e:
                logger.error(f"Structured extraction failed for {url}: {e}")
                return None

# Singleton-ish helpers
_BROWSER = None

def get_agent_browser():
    global _BROWSER
    if _BROWSER is None:
        _BROWSER = AgentBrowser()
    return _BROWSER

async def fetch_markdown(url: str) -> str:
    """Utility to quickly fetch markdown content."""
    browser = get_agent_browser()
    return await browser.get_page_markdown(url) or ""

def run_async_fetch(url: str) -> str:
    """Synchronous wrapper for async fetch (useful for legacy code)."""
    try:
        return asyncio.run(fetch_markdown(url))
    except Exception as e:
        logger.error(f"Failed to run async fetch: {e}")
        return ""
