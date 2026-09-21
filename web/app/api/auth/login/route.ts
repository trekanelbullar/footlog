import { NextResponse } from "next/server";
import { checkCsrf } from "@/lib/csrf";
import { signInWithPassword } from "@/lib/auth";

// ログインは W1〜W23 の契約には無いが、Cookie を書き換える変更系のリクエストのため
// 他の Route Handler と同じく CSRF 対策（Origin・Content-Type の確認）をかける。
export async function POST(req: Request) {
  const csrfError = checkCsrf(req);
  if (csrfError) return csrfError;

  const { email, password } = (await req.json()) as { email?: string; password?: string };
  if (!email || !password) {
    return NextResponse.json(
      { error: "invalid_input", message: "メールアドレスとパスワードを入力してください。" },
      { status: 400 }
    );
  }

  const { error } = await signInWithPassword(email, password);
  if (error) {
    return NextResponse.json({ error: "login_failed", message: error }, { status: 401 });
  }
  return NextResponse.json({ ok: true });
}
