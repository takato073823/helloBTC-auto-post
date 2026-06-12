#!/usr/bin/env python3
"""
記事生成テスト（WordPress投稿なし・HTMLプレビューのみ）
使い方: ANTHROPIC_API_KEY=xxx python3 test_article_gen.py [基礎知識|コラム|DeFi|取引所]
"""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

PLACEHOLDER_IMG  = '<div style="background:#e0e0e0;padding:40px;text-align:center;border-radius:6px;color:#999;margin:16px 0;font-size:0.9em;">📷 画像（本番ではImagenで生成）</div>'
PLACEHOLDER_CHART = '<div style="background:#e0e0e0;padding:40px;text-align:center;border-radius:6px;color:#999;margin:16px 0;font-size:0.9em;">📊 グラフ（本番ではmatplotlibで生成）</div>'

def main():
    category = sys.argv[1] if len(sys.argv) > 1 else "基礎知識"
    print(f"「{category}」カテゴリの記事を生成中... (Claude Haiku 4.5)")

    from generator import generate_seo_article
    result = generate_seo_article(category)

    print(f"\n✅ 生成完了")
    print(f"  タイトル : {result['title']}")
    print(f"  スラッグ : {result['slug']}")
    print(f"  タグ     : {', '.join(result.get('tags', []))}")
    print(f"  要約     : {result.get('excerpt', '')[:80]}...")
    print(f"\n--- ツイート要点 ---")
    for b in result.get("tweet_bullets", []):
        print(f"  ・{b}")
    if result.get("chart"):
        c = result["chart"]
        print(f"\n--- チャート ---")
        print(f"  タイトル: {c.get('title')}")
        print(f"  ラベル  : {c.get('labels')}")
        print(f"  数値    : {c.get('values')}")

    # HTMLプレビュー生成
    content = result["content"]
    content = content.replace("{IMAGE_1}", PLACEHOLDER_IMG)
    content = content.replace("{IMAGE_2}", PLACEHOLDER_IMG)
    content = content.replace("{CHART}",   PLACEHOLDER_CHART)

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{result['title']}</title>
<style>
  body {{
    max-width: 860px; margin: 40px auto;
    font-family: -apple-system, "Hiragino Kaku Gothic Pro", Meiryo, sans-serif;
    line-height: 1.85; color: #333; padding: 0 24px;
  }}
  h1 {{ font-size: 1.6em; border-bottom: 3px solid #f7931a; padding-bottom: 12px; }}
  h2 {{ font-size: 1.3em; border-left: 5px solid #f7931a; padding-left: 12px; margin-top: 36px; }}
  h3 {{ font-size: 1.1em; border-bottom: 1px solid #e0e0e0; padding-bottom: 6px; }}
  img {{ max-width: 100%; height: auto; }}
  table {{ width: 100%; border-collapse: collapse; margin: 16px 0; }}
  th {{ background: #f7931a; color: #fff; padding: 10px 12px; border: 1px solid #e6881a; text-align: center; }}
  td {{ padding: 9px 12px; border: 1px solid #ddd; text-align: center; }}
  tr:nth-child(even) {{ background: #fff8e1; }}
  hr.wp-block-separator {{ border: none; border-top: 2px solid #e0e0e0; margin: 28px 0 0; }}
  /* SWELL swl-marker */
  .swl-marker.mark_orange {{ background: linear-gradient(transparent 60%, #ffd54f 60%); }}
  .swl-marker.mark_yellow {{ background: linear-gradient(transparent 60%, #fff176 60%); }}
  .swl-marker.mark_blue   {{ background: linear-gradient(transparent 60%, #81d4fa 60%); }}
  /* SWELL cap-block */
  .swell-block-capbox {{ border: 2px solid #e0e0e0; border-radius: 8px; margin: 24px 0; overflow: hidden; }}
  .swell-block-capbox.is-style-onborder_ttl  {{ border-color: #4caf50; }}
  .swell-block-capbox.is-style-onborder_ttl2 {{ border-color: #ff9800; }}
  .cap_box_ttl {{ padding: 10px 18px; font-weight: 700; font-size: 0.95em; }}
  .is-style-onborder_ttl  .cap_box_ttl {{ background: #e8f5e9; color: #2e7d32; }}
  .is-style-onborder_ttl2 .cap_box_ttl {{ background: #fff3e0; color: #e65100; }}
  .cap_box_content {{ padding: 14px 18px; }}
  /* SWELL big_icon_point */
  p.is-style-big_icon_point {{ background: #fff8e1; border-left: 5px solid #f7931a; padding: 14px 18px; border-radius: 4px; }}
  /* SWELL inline color */
  .swl-inline-color.has-swl-deep-01-color {{ color: #f7931a; }}
  .swl-inline-color.has-swl-deep-02-color {{ color: #1976d2; }}
</style>
</head>
<body>
<p style="background:#fff3e0;padding:10px 16px;border-radius:4px;font-size:0.85em;color:#e65100;">
  ℹ️ プレビュー表示 — カテゴリ: {category} | SWELLブロック記法 | 本番ではImagenとmatplotlibの画像が入ります
</p>
<h1>{result['title']}</h1>
<p style="color:#888;font-size:0.88em;">タグ: {' / '.join(result.get('tags', []))}</p>

{content}
</body>
</html>"""

    out_path = f"/Users/takatookuda/Desktop/preview_{category}.html"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\n📄 HTMLプレビュー保存: {out_path}")
    print("   ブラウザで開いてデザインを確認してください")

if __name__ == "__main__":
    main()
