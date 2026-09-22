import { NextResponse } from "next/server";
import { checkCsrf } from "@/lib/csrf";
import { signUpWithPassword } from "@/lib/auth";

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

  const { error } = await signUpWithPassword(email, password);
  if (error) {
    return NextResponse.json({ error: "signup_failed", message: error }, { status: 400 });
  }
  return NextResponse.json({ ok: true });
}
