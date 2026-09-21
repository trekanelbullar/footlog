import "server-only";
import { NextResponse } from "next/server";
import { WorkerApiError } from "@/lib/worker-types";

/**
 * Route Handler 共通のエラー処理。worker.ts の各関数は、セッションが無ければ
 * WorkerApiError(401) を投げる（w* 関数の requireSession）。ここでその例外を
 * worker のエラー形（{"error", "message"}）のまま HTTP レスポンスに変換する。
 */
export async function handle<T>(fn: () => Promise<T>): Promise<NextResponse> {
  try {
    const data = await fn();
    return NextResponse.json(data);
  } catch (e) {
    if (e instanceof WorkerApiError) {
      return NextResponse.json({ error: e.code, message: e.message }, { status: e.status });
    }
    console.error(e);
    return NextResponse.json(
      { error: "internal_error", message: "サーバー内部でエラーが発生しました。" },
      { status: 500 }
    );
  }
}
