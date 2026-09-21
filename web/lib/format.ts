/**
 * 表示用の書式。時刻は日本時間に固定する（spec S6）。
 */

const JST_FORMATTER = new Intl.DateTimeFormat("ja-JP", {
  timeZone: "Asia/Tokyo",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
});

/** ISO-8601 の時刻を、日本時間の「2026/09/20 14:00」の形にする。読めなければそのまま返す。 */
export function formatJst(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : JST_FORMATTER.format(date);
}

/** ISO-8601 の時刻の比較（オフセットが違っても正しく並ぶよう、文字列ではなく時刻で比べる）。 */
export function compareIso(a: string, b: string): number {
  return new Date(a).getTime() - new Date(b).getTime();
}

const FOOTNOTE_REF = /\[\^(\d+)\]/g;

/**
 * 本文の脚注の印 `[^n]` を、脚注へのページ内リンク `[［n］](#fn-n)` に置き換える。
 * worker の本文は脚注の定義行を含まない（脚注の原文は別の欄で届く）ため、印だけを変換する。
 */
export function linkFootnoteRefs(markdown: string): string {
  return markdown.replace(FOOTNOTE_REF, (_match, n: string) => `[［${n}］](#fn-${n})`);
}
