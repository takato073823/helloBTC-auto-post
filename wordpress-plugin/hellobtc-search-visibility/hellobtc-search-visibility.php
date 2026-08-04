<?php
/**
 * Plugin Name: helloBTC Search Visibility
 * Description: helloBTC記事の検索結果説明、日時・著者表示、旧スキーマの重複を整えます。
 * Version: 1.0.0
 * Author: helloBTC
 * License: GPL-2.0-or-later
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

/**
 * SEO SIMPLE PACKのdescriptionに、自動投稿時の抜粋を使う。
 */
function hellobtc_search_description( $description ) {
	if ( ! is_singular( 'post' ) ) {
		return $description;
	}

	$post_id = get_queried_object_id();
	$excerpt = get_post_field( 'post_excerpt', $post_id );
	$excerpt = trim( preg_replace( '/\s+/u', ' ', wp_strip_all_tags( strip_shortcodes( $excerpt ) ) ) );
	if ( '' === $excerpt ) {
		return $description;
	}

	return wp_html_excerpt( $excerpt, 160, '…' );
}
add_filter( 'ssp_output_description', 'hellobtc_search_description', 20 );

/**
 * 旧自動投稿が本文先頭に保存したNewsArticle JSON-LDだけを除去する。
 * SWELL/テーマが生成するArticleスキーマはそのまま残す。
 */
function hellobtc_remove_legacy_news_schema( $content ) {
	$pattern = '~\A\s*<!--\s*wp:html\s*-->\s*<script\s+type=["\']application/ld\+json["\']\s*>(.*?)</script>\s*<!--\s*/wp:html\s*-->\s*~is';
	if ( ! preg_match( $pattern, $content, $matches ) ) {
		return $content;
	}

	$schema = json_decode( html_entity_decode( $matches[1], ENT_QUOTES | ENT_HTML5, 'UTF-8' ), true );
	if ( ! is_array( $schema ) || 'NewsArticle' !== ( $schema['@type'] ?? '' ) ) {
		return $content;
	}

	return preg_replace( $pattern, '', $content, 1 );
}
add_filter( 'the_content', 'hellobtc_remove_legacy_news_schema', 8 );

/**
 * Google Newsと読者向けに、公開日時・更新日時・著者を明示する。
 */
function hellobtc_add_article_transparency( $content ) {
	if ( ! is_singular( 'post' ) || ! in_the_loop() || ! is_main_query() ) {
		return $content;
	}

	$post_id       = get_the_ID();
	$published     = get_post_timestamp( $post_id, 'date' );
	$modified      = get_post_timestamp( $post_id, 'modified' );
	$published_iso = get_the_date( DATE_W3C, $post_id );
	$modified_iso  = get_the_modified_date( DATE_W3C, $post_id );
	$timezone      = wp_timezone();
	$author_id     = (int) get_post_field( 'post_author', $post_id );
	$author_name   = get_the_author_meta( 'display_name', $author_id );
	$author_url    = get_author_posts_url( $author_id );

	$parts   = array();
	$parts[] = sprintf(
		'<time datetime="%1$s">公開：%2$s JST</time>',
		esc_attr( $published_iso ),
		esc_html( wp_date( 'Y年n月j日 H:i', $published, $timezone ) )
	);
	if ( $modified > ( $published + MINUTE_IN_SECONDS ) ) {
		$parts[] = sprintf(
			'<time datetime="%1$s">更新：%2$s JST</time>',
			esc_attr( $modified_iso ),
			esc_html( wp_date( 'Y年n月j日 H:i', $modified, $timezone ) )
		);
	}
	if ( '' !== $author_name ) {
		$parts[] = sprintf(
			'著者：<a rel="author" href="%1$s">%2$s</a>',
			esc_url( $author_url ),
			esc_html( $author_name )
		);
	}

	$meta = '<div class="hellobtc-article-meta" aria-label="記事情報">' . implode( '<span aria-hidden="true">／</span>', $parts ) . '</div>';
	return $meta . $content;
}
add_filter( 'the_content', 'hellobtc_add_article_transparency', 11 );

/**
 * テーマに依存しない最小限の表示。
 */
function hellobtc_search_visibility_styles() {
	if ( ! is_singular( 'post' ) ) {
		return;
	}
	?>
	<style id="hellobtc-search-visibility-css">
	.hellobtc-article-meta{display:flex;flex-wrap:wrap;gap:.3em .65em;margin:0 0 1.5em;color:#666;font-size:.86em;line-height:1.7}
	.hellobtc-article-meta a{color:inherit;text-decoration:underline;text-underline-offset:.15em}
	.hellobtc-source{margin-top:2em;padding-top:1em;border-top:1px solid #e5e7eb;color:#555;font-size:.9em}
	</style>
	<?php
}
add_action( 'wp_head', 'hellobtc_search_visibility_styles', 30 );
