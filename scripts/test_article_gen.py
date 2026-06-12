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
  /* SWELL マーカー */
  .swl-marker.mark_orange {{ background: linear-gradient(transparent 55%, #ffd54f 55%); font-weight: 600; }}
  .swl-marker.mark_yellow {{ background: linear-gradient(transparent 55%, #fff176 55%); }}
  .swl-marker.mark_pink   {{ background: linear-gradient(transparent 55%, #f8bbd0 55%); }}
  .swl-marker.mark_blue   {{ background: linear-gradient(transparent 55%, #bbdefb 55%); }}
  .swl-marker.mark_green  {{ background: linear-gradient(transparent 55%, #c8e6c9 55%); }}
  /* SWELL cap-block */
  .swell-block-capbox {{ border: 2px solid #e0e0e0; border-radius: 8px; margin: 24px 0; overflow: hidden; }}
  .swell-block-capbox.is-style-onborder_ttl  {{ border-color: #4caf50; }}
  .swell-block-capbox.is-style-onborder_ttl2 {{ border-color: #ff9800; }}
  .cap_box_ttl {{ padding: 10px 18px; font-weight: 700; font-size: 0.95em; }}
  .is-style-onborder_ttl  .cap_box_ttl {{ background: #e8f5e9; color: #2e7d32; }}
  .is-style-onborder_ttl2 .cap_box_ttl {{ background: #fff3e0; color: #e65100; }}
  .cap_box_content {{ padding: 14px 18px; }}
  /* SWELL big_icon 段落スタイル */
  p.is-style-big_icon_point   {{ background: #fff8e1; border-left: 5px solid #f7931a; padding: 14px 18px; border-radius: 4px; margin: 16px 0; }}
  p.is-style-big_icon_point::before  {{ content: "💡 "; }}
  p.is-style-big_icon_check   {{ background: #e8f5e9; border-left: 5px solid #4caf50; padding: 14px 18px; border-radius: 4px; margin: 12px 0; }}
  p.is-style-big_icon_check::before  {{ content: "✅ "; }}
  p.is-style-big_icon_caution {{ background: #fff3e0; border-left: 5px solid #ff9800; padding: 14px 18px; border-radius: 4px; margin: 12px 0; }}
  p.is-style-big_icon_caution::before {{ content: "⚠️ "; }}
  p.is-style-big_icon_memo    {{ background: #f3e5f5; border-left: 5px solid #9c27b0; padding: 14px 18px; border-radius: 4px; margin: 12px 0; }}
  p.is-style-big_icon_memo::before    {{ content: "📝 "; }}
  /* SWELL ステップブロック */
  .swell-block-step {{ margin: 24px 0; }}
  .swell-block-step__item {{ display: flex; gap: 16px; margin-bottom: 20px; align-items: flex-start; }}
  .swell-block-step__number {{ background: #f7931a; color: #fff; border-radius: 50%; width: 56px; height: 56px; display: flex; flex-direction: column; align-items: center; justify-content: center; flex-shrink: 0; }}
  .swell-block-step__number .__label {{ font-size: 0.6em; font-weight: 700; line-height: 1; }}
  .swell-block-step__number .__num {{ font-size: 1.4em; font-weight: 900; line-height: 1; }}
  .swell-block-step__title {{ font-weight: 700; font-size: 1.05em; margin-bottom: 6px; color: #222; }}
  .swell-block-step__body {{ flex: 1; }}
  /* SWELL FAQブロック */
  .swell-block-faq {{ margin: 24px 0; }}
  .swell-block-faq.is-style-faq-box .swell-block-faq__item {{ border: 1px solid #e0e0e0; border-radius: 8px; margin-bottom: 16px; overflow: hidden; }}
  .faq_q {{ background: #1a1a2e; color: #fff; padding: 14px 18px; font-weight: 700; }}
  .faq_q::before {{ content: "Q. "; color: #f7931a; }}
  .faq_a {{ padding: 14px 18px; background: #fafafa; }}
  .faq_a::before {{ content: "A. "; font-weight: 700; color: #f7931a; }}
  /* SWELL 用語定義リスト */
  .swell-block-dl {{ margin: 20px 0; }}
  .swell-block-dl.is-style-border_left .swell-block-dl__item {{ border-left: 4px solid #f7931a; padding: 8px 0 8px 16px; margin-bottom: 12px; }}
  .swell-block-dl__dt {{ font-weight: 700; color: #222; margin-bottom: 4px; }}
  .swell-block-dl__dd {{ color: #555; font-size: 0.95em; }}
  /* SWELL inline color */
  .swl-inline-color.has-swl-deep-01-color {{ color: #f7931a; font-weight: 600; }}
  .swl-inline-color.has-swl-deep-02-color {{ color: #1976d2; font-weight: 600; }}
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
