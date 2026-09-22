/**
 * モックモードかどうかの判定はこの1関数だけに閉じ込める。
 * `WORKER_MOCK=1` かつ `NODE_ENV=development` のときだけ true。
 * 本番ビルド（NODE_ENV=production）では、WORKER_MOCK の値に関わらず必ず false になる。
 *
 * lib/worker.ts と lib/auth.ts の両方がこの関数を呼び、条件を重複して書かない。
 */
export function isMockMode(): boolean {
  return process.env.WORKER_MOCK === "1" && process.env.NODE_ENV === "development";
}
