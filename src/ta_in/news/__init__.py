from ta_in.news.models import NewsItem
from ta_in.news.provider import NewsProvider
from ta_in.news.service import EventSink, NewsService, canonical_url

__all__ = ["EventSink", "NewsItem", "NewsProvider", "NewsService", "canonical_url"]
