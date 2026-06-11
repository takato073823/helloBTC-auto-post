"""
helloBTC WordPress SEO 初期設定スクリプト（workflow_dispatch で一回だけ実行）

自動実行できる内容:
  - 不要プラグイン削除（Hello Dolly、WP File Manager）
  - WordPress コア設定最適化
  - ニュースサイトマップ生成状況の確認レポート

手動対応が必要な内容はスクリプト末尾にレポートとして出力する。
"""
import os
import json
import logging
import requests
import base64
from urllib.parse import quote

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

WP_URL = os.environ["WP_URL"].rstrip("/")
WP_USERNAME = os.environ["WP_USERNAME"]
WP_APP_PASSWORD = os.environ["WP_APP_PASSWORD"]

session = requests.Session()
_token = base64.b64encode(f"{WP_USERNAME}:{WP_APP_PASSWORD}".encode()).decode()
session.headers.update({
    "Authorization": f"Basic {_token}",
    "Content-Type": "application/json",
})

BASE = f"{WP_URL}/wp-json/wp/v2"
MANUAL_ACTIONS = []


# ─────────────────────────────────────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────────────────────────────────────

def wp_get(endpoint, params=None):
    return session.get(f"{BASE}/{endpoint}", params=params, timeout=30)


def wp_post(endpoint, data):
    return session.post(f"{BASE}/{endpoint}", json=data, timeout=30)


def wp_delete(endpoint):
    return session.delete(f"{BASE}/{endpoint}", timeout=30)


# ─────────────────────────────────────────────────────────────────────────────
# Step 1: プラグイン一覧確認
# ─────────────────────────────────────────────────────────────────────────────

def list_plugins():
    logger.info("=" * 60)
    logger.info("STEP 1: インストール済みプラグイン一覧")
    logger.info("=" * 60)
    r = wp_get("plugins")
    if not r.ok:
        logger.error(f"プラグイン取得失敗: {r.status_code}")
        return []
    plugins = r.json()
    for p in plugins:
        icon = "✅" if p.get("status") == "active" else "⏸"
        logger.info(f"  {icon} {p.get('name', '?'):40s} | {p.get('plugin', '?')}")
    return plugins


# ─────────────────────────────────────────────────────────────────────────────
# Step 2: 不要プラグイン削除
# ─────────────────────────────────────────────────────────────────────────────

def delete_plugin(plugin_path: str, display_name: str):
    """スラッシュを %2F にエンコードして正しい REST エンドポイントを構築"""
    logger.info(f"\n--- {display_name} を削除 ---")
    encoded = quote(plugin_path, safe="")   # "wp-foo/bar" → "wp-foo%2Fbar"
    endpoint_url = f"{BASE}/plugins/{encoded}"

    # 停止
    r = session.post(endpoint_url, json={"status": "inactive"}, timeout=30)
    if r.ok:
        logger.info(f"  停止: OK")
    else:
        logger.warning(f"  停止スキップ（既に停止中 or 不要）: {r.status_code}")

    # 削除
    r = session.delete(endpoint_url, timeout=30)
    if r.ok:
        logger.info(f"  ✅ 削除完了: {display_name}")
    else:
        logger.error(f"  ❌ 削除失敗: {r.status_code} | {r.text[:200]}")


def step_delete_plugins():
    logger.info("\n" + "=" * 60)
    logger.info("STEP 2: 不要プラグイン削除")
    logger.info("=" * 60)
    delete_plugin("hello-dolly/hello", "Hello Dolly")
    delete_plugin("wp-file-manager/file_folder_manager", "WP File Manager")


# ─────────────────────────────────────────────────────────────────────────────
# Step 3: WordPress コア設定最適化
# ─────────────────────────────────────────────────────────────────────────────

def step_update_core_settings():
    logger.info("\n" + "=" * 60)
    logger.info("STEP 3: WordPress コア設定を最適化")
    logger.info("=" * 60)

    # 現在値を取得
    r = wp_get("settings")
    if r.ok:
        cur = r.json()
        logger.info(f"  現在のタイトル: {cur.get('title')}")
        logger.info(f"  現在の説明文:   {cur.get('description')}")
        logger.info(f"  タイムゾーン:   {cur.get('timezone_string')}")
        logger.info(f"  コメント設定:   {cur.get('default_comment_status')}")

    # 更新
    settings = {
        "title": "helloBTC",
        "description": "仮想通貨・ビットコイン最新ニュースと投資情報",
        "timezone_string": "Asia/Tokyo",
        "date_format": "Y年n月j日",
        "time_format": "H:i",
        "default_comment_status": "closed",   # コメントスパム防止
        "default_ping_status": "closed",
        "start_of_week": 1,                   # 週の始まり: 月曜
    }

    r = wp_post("settings", settings)
    if r.ok:
        result = r.json()
        logger.info(f"\n  ✅ 設定更新完了")
        logger.info(f"  タイトル: {result.get('title')}")
        logger.info(f"  説明文:   {result.get('description')}")
        logger.info(f"  タイムゾーン: {result.get('timezone_string')}")
        logger.info(f"  コメント: {result.get('default_comment_status')}")
    else:
        logger.error(f"  ❌ 設定更新失敗: {r.status_code} | {r.text[:300]}")


# ─────────────────────────────────────────────────────────────────────────────
# Step 4: ニュースサイトマップ生成状況確認
# ─────────────────────────────────────────────────────────────────────────────

def step_check_news_sitemap():
    logger.info("\n" + "=" * 60)
    logger.info("STEP 4: ニュースサイトマップ確認")
    logger.info("=" * 60)

    urls_to_check = [
        f"{WP_URL}/sitemap-news.xml",      # XML Sitemap & Google News の実際のURL
        f"{WP_URL}/news-sitemap.xml",
        f"{WP_URL}/?feed=news-sitemap",
    ]

    found = False
    for url in urls_to_check:
        r = requests.get(url, timeout=15)
        if r.status_code == 200 and ("news" in r.text.lower() or "xml" in r.text[:100].lower()):
            logger.info(f"  ✅ ニュースサイトマップ確認: {url}")
            found = True
            break
        else:
            logger.info(f"  ❌ {url} → {r.status_code}")

    if not found:
        logger.warning("  ⚠️ ニュースサイトマップ未生成 → 手動設定が必要")
        MANUAL_ACTIONS.append(
            "【最重要】WordPress管理画面 → 設定 → XML Sitemap & Google News\n"
            "   ① 「Google Newsサイトマップ」タブをクリック\n"
            "   ② 「Enable Google News Sitemap」にチェック\n"
            "   ③ Publication Name: helloBTC\n"
            "   ④ 含めるカテゴリ: 「ニュース」のみチェック\n"
            "   ⑤ 保存後、https://hellobtc.jp/news-sitemap.xml にアクセスして確認"
        )

    return found


# ─────────────────────────────────────────────────────────────────────────────
# Step 5: 通常サイトマップ確認
# ─────────────────────────────────────────────────────────────────────────────

def step_check_regular_sitemap():
    logger.info("\n" + "=" * 60)
    logger.info("STEP 5: 通常サイトマップ確認")
    logger.info("=" * 60)

    r = requests.get(f"{WP_URL}/sitemap.xml", timeout=15)
    if r.ok:
        logger.info(f"  ✅ sitemap.xml: OK ({len(r.text)} bytes)")
    else:
        logger.warning(f"  ❌ sitemap.xml: {r.status_code}")

    # robots.txtのサイトマップ宣言確認
    r = requests.get(f"{WP_URL}/robots.txt", timeout=15)
    if r.ok:
        lines = r.text.strip().splitlines()
        sitemap_lines = [l for l in lines if l.lower().startswith("sitemap:")]
        logger.info(f"  robots.txt のサイトマップ宣言:")
        for l in sitemap_lines:
            logger.info(f"    {l}")
        if not any("news" in l.lower() for l in sitemap_lines):
            MANUAL_ACTIONS.append(
                "robots.txt にニュースサイトマップを追記\n"
                "   SEO SIMPLE PACK → robots.txt 編集 に以下を追加:\n"
                "   Sitemap: https://hellobtc.jp/sitemap-news.xml"
            )


# ─────────────────────────────────────────────────────────────────────────────
# Step 6: パーマリンク構造確認
# ─────────────────────────────────────────────────────────────────────────────

def step_check_permalink():
    logger.info("\n" + "=" * 60)
    logger.info("STEP 6: パーマリンク構造確認")
    logger.info("=" * 60)

    # 最新記事のURLを確認
    r = wp_get("posts", params={"per_page": 3, "status": "publish"})
    if r.ok:
        posts = r.json()
        for p in posts:
            link = p.get("link", "")
            slug = p.get("slug", "")
            logger.info(f"  記事URL: {link}")
            if slug and all(ord(c) < 128 for c in slug):
                logger.info(f"    ✅ 英語スラッグ: {slug}")
            else:
                logger.info(f"    ⚠️ 日本語スラッグ（既存記事）: {slug[:50]}")


# ─────────────────────────────────────────────────────────────────────────────
# Step 7: Organization スキーマ用コードを出力（手動貼り付け用）
# ─────────────────────────────────────────────────────────────────────────────

def step_output_org_schema():
    logger.info("\n" + "=" * 60)
    logger.info("STEP 7: Organization スキーマ（WP Headers And Footers に手動追加）")
    logger.info("=" * 60)

    org_schema = {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": "helloBTC",
        "url": "https://hellobtc.jp",
        "logo": {
            "@type": "ImageObject",
            "url": "https://hellobtc.jp/wp-content/uploads/hellobtc-logo.png",
            "width": 200,
            "height": 60,
        },
        "sameAs": [],
    }
    schema_html = (
        '<script type="application/ld+json">\n'
        + json.dumps(org_schema, ensure_ascii=False, indent=2)
        + "\n</script>"
    )
    logger.info(f"\n以下を WordPress管理画面 → 設定 → WP Headers And Footers → Header に貼り付け:\n")
    logger.info(schema_html)

    MANUAL_ACTIONS.append(
        "Organization スキーマ追加\n"
        "   WordPress管理画面 → 設定 → WP Headers And Footers\n"
        "   「Scripts in Header」に上記スクリプトを貼り付けて保存"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Step 8: Google News Publisher Center 案内
# ─────────────────────────────────────────────────────────────────────────────

def step_news_publisher_instructions():
    MANUAL_ACTIONS.append(
        "Google News Publisher Center 登録\n"
        "   ① publishercenter.google.com にアクセス\n"
        "   ② 「出版物を追加」→ hellobtc.jp\n"
        "   ③ ニュースサイトマップURLを登録: https://hellobtc.jp/sitemap-news.xml\n"
        "   ※ ニュースサイトマップが既に稼働中（sitemap-news.xml）"
    )
    MANUAL_ACTIONS.append(
        "プラグインのアップデート（8件）\n"
        "   WordPress管理画面 → プラグイン → 「利用可能な更新」→ 全て更新"
    )
    MANUAL_ACTIONS.append(
        "Google Search Console にニュースサイトマップを追加\n"
        "   Search Console → サイトマップ → 「sitemap-news.xml」を追加送信"
    )
    MANUAL_ACTIONS.append(
        "SEO SIMPLE PACK の OGP/Twitter Card を確認\n"
        "   SEO PACK → SNS/OGP設定 → Twitter Card: summary_large_image\n"
        "   デフォルトOG画像にロゴを設定"
    )


# ─────────────────────────────────────────────────────────────────────────────
# メイン
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("\n" + "🚀 " * 10)
    logger.info("helloBTC WordPress SEO セットアップ開始")
    logger.info("🚀 " * 10 + "\n")

    list_plugins()
    step_delete_plugins()
    step_update_core_settings()
    news_ok = step_check_news_sitemap()
    step_check_regular_sitemap()
    step_check_permalink()
    step_output_org_schema()
    step_news_publisher_instructions()

    # ── 手動対応サマリー ──────────────────────────────────────────────────────
    logger.info("\n" + "=" * 60)
    logger.info("📋 手動対応が必要な項目（優先度順）")
    logger.info("=" * 60)
    for i, action in enumerate(MANUAL_ACTIONS, 1):
        logger.info(f"\n[{i}] {action}")

    logger.info("\n" + "✅ " * 10)
    logger.info("セットアップスクリプト完了")
    logger.info("✅ " * 10)
