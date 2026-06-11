"""
NewsNow + RSS feeds から最新の仮想通貨ニュースを取得するスクレイパー
"""
import requests
from bs4 import BeautifulSoup
import feedparser
import logging
import time

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# NewsNow URL
NEWSNOW_URL = "https://www.newsnow.co.uk/h/Business+%26+Finance/Cryptocurrencies"

# 一次情報源 RSS フィード
RSS_FEEDS = [
    {"url": "https://www.coindesk.com/arc/outboundfeeds/rss/", "name": "CoinDesk"},
    {"url": "https://cointelegraph.com/rss", "name": "CoinTelegraph"},
    {"url": "https://decrypt.co/feed", "name": "Decrypt"},
    {"url": "https://www.theblock.co/rss.xml", "name": "The Block"},
    {"url": "https://bitcoinmagazine.com/.rss/full/", "name": "Bitcoin Magazine"},
]


def scrape_newsnow(max_articles=20):
    """NewsNow からトレンド記事の URL を取得"""
    articles = []
    try:
        response = requests.get(NEWSNOW_URL, headers=HEADERS, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "lxml")

        # NewsNow の記事リンクを抽出（複数のセレクターを試みる）
        seen_urls = set()
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            text = a_tag.get_text(strip=True)

            # 外部サイトへのリンクのみ（NewsNow 内部リンクは除外）
            if (
                href.startswith("http")
                and "newsnow.co.uk" not in href
                and len(text) > 20
                and href not in seen_urls
            ):
                seen_urls.add(href)
                articles.append({
                    "title": text,
                    "url": href,
                    "source": "NewsNow",
                    "description": "",
                })

            if len(articles) >= max_articles:
                break

        logger.info(f"NewsNow から {len(articles)} 件取得")
    except Exception as e:
        logger.warning(f"NewsNow スクレイピング失敗: {e}")

    return articles


def fetch_from_rss(max_per_feed=5):
    """各 RSS フィードから最新記事を取得"""
    articles = []
    for feed_info in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_info["url"])
            count = 0
            for entry in feed.entries:
                if count >= max_per_feed:
                    break
                url = entry.get("link", "")
                title = entry.get("title", "")
                if url and title:
                    articles.append({
                        "title": title,
                        "url": url,
                        "description": entry.get("summary", "")[:500],
                        "source": feed_info["name"],
                        "published": entry.get("published", ""),
                    })
                    count += 1
            logger.info(f"{feed_info['name']} から {count} 件取得")
            time.sleep(0.5)
        except Exception as e:
            logger.error(f"RSS 取得失敗 ({feed_info['name']}): {e}")

    return articles


def _extract_tweet_urls(soup) -> list:
    """ページから Twitter/X の公式ツイートURLを最大3件抽出する"""
    tweet_urls = []
    seen = set()

    # blockquote.twitter-tweet の最後の <a> が permalink
    for bq in soup.find_all("blockquote", class_=lambda c: c and "twitter" in " ".join(c)):
        links = bq.find_all("a", href=True)
        if links:
            href = links[-1]["href"].split("?")[0]
            if "/status/" in href and href not in seen:
                seen.add(href)
                tweet_urls.append(href)

    # フォールバック: ページ内リンクから twitter.com/x.com の status URL を探す
    if not tweet_urls:
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/status/" in href and ("twitter.com" in href or "x.com" in href):
                clean = href.split("?")[0]
                if clean not in seen:
                    seen.add(clean)
                    tweet_urls.append(clean)
                    if len(tweet_urls) >= 3:
                        break

    return tweet_urls[:3]


def fetch_article_content(url, max_length=4000):
    """元記事の本文とツイートURLを取得して dict で返す"""
    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "lxml")

        # ツイートURLはタグ除去前に抽出する
        tweet_urls = _extract_tweet_urls(soup)

        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form", "iframe"]):
            tag.decompose()

        # 記事本文を探す（優先度順）
        selectors = [
            "article",
            '[class*="article-body"]',
            '[class*="post-content"]',
            '[class*="entry-content"]',
            '[class*="article-content"]',
            "main",
        ]
        text = ""
        for selector in selectors:
            el = soup.select_one(selector)
            if el:
                candidate = el.get_text(separator="\n", strip=True)
                if len(candidate) > 300:
                    text = candidate[:max_length]
                    break

        if not text:
            body = soup.find("body")
            if body:
                text = body.get_text(separator="\n", strip=True)[:max_length]

        if tweet_urls:
            logger.info(f"ツイートURL {len(tweet_urls)} 件を抽出: {tweet_urls}")

        return {"text": text, "tweet_urls": tweet_urls}

    except Exception as e:
        logger.error(f"記事取得失敗 ({url}): {e}")
        return {"text": "", "tweet_urls": []}


def get_latest_articles(count=20):
    """NewsNow + RSS から最新記事を取得（NewsNow 優先）"""
    # NewsNow からトレンド記事を取得
    articles = scrape_newsnow(max_articles=count)

    # NewsNow で取得できなかった場合は RSS を使用
    if len(articles) < count // 2:
        logger.info("RSS フィードにフォールバック")
        rss_articles = fetch_from_rss(max_per_feed=4)
        # 重複除外してマージ
        existing_urls = {a["url"] for a in articles}
        for a in rss_articles:
            if a["url"] not in existing_urls:
                articles.append(a)
                existing_urls.add(a["url"])

    return articles[:count]
