import "server-only";
import { NextResponse } from "next/server";

/**
 * CSRF対策（design.md §2.7・A-X5）。変更系（POST・PATCH・DELETE）の Route Handler の先頭で呼ぶ。
 * - Origin ヘッダーが自分のオリジン（APP_BASE_URL）と一致しなければ 403。
 * - Content-Type は application/json と multipart/form-data だけを受け付ける。
 * 問題なければ null を返す。
 */
export function checkCsrf(req: Request): NextResponse | null {
  const appBaseUrl = process.env.APP_BASE_URL;
  if (!appBaseUrl) {
    return NextResponse.json(
      { error: "server_misconfigured", message: "APP_BASE_URL が設定されていません。" },
      { status: 500 }
    );
  }

  let expectedOrigin: string;
  try {
    expectedOrigin = new URL(appBaseUrl).origin;
  } catch {
    return NextResponse.json(
      { error: "server_misconfigured", message: "APP_BASE_URL の形式が不正です。" },
      { status: 500 }
    );
  }

  const origin = req.headers.get("origin");
  if (origin !== expectedOrigin) {
    return NextResponse.json(
      { error: "csrf_origin_mismatch", message: "不正なリクエスト元のため処理を拒否しました。" },
      { status: 403 }
    );
  }

  // 本文の無い DELETE などは Content-Type が付かないことがあるため、ヘッダーが無ければ許す。
  // ヘッダーがある場合は application/json・multipart/form-data だけを受け付ける
  // （HTML フォームの素の送信は application/x-www-form-urlencoded・multipart/form-data・text/plain の
  //   いずれかになり、application/json は付けられないため、この判定自体もCSRF対策として働く）。
  const contentType = req.headers.get("content-type");
  if (contentType) {
    const isJson = contentType.startsWith("application/json");
    const isMultipart = contentType.startsWith("multipart/form-data");
    if (!isJson && !isMultipart) {
      return NextResponse.json(
        { error: "unsupported_content_type", message: "対応していない Content-Type です。" },
        { status: 403 }
      );
    }
  }

  return null;
}
