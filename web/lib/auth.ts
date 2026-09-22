import "server-only";
import { cookies } from "next/headers";
import { createSupabaseServerClient } from "@/lib/supabase/server";
import { isMockMode } from "@/lib/mock/mode";
import { MOCK_SESSION_COOKIE, findMockUserByEmail, findMockUserById } from "@/lib/mock/users";

/**
 * ログイン・サインアップ・ログアウトと、サーバー側でのセッション取得はこのモジュールだけで行う（spec S10）。
 * Supabase（`@supabase/ssr`）を直接呼ぶコードは、このファイルと lib/supabase/server.ts の外に書かない。
 */

export interface Session {
  userId: string;
  email: string;
  /** worker へ転送する Supabase のアクセストークン。web はこの中身を解釈しない。 */
  accessToken: string;
}

export interface AuthResult {
  error: string | null;
}

const MOCK_LOGIN_HINT = "モックモードでは manager@example.com / member@example.com でログインしてください（パスワードは任意）。";

export async function signInWithPassword(email: string, password: string): Promise<AuthResult> {
  if (isMockMode()) {
    const user = findMockUserByEmail(email);
    if (!user) return { error: MOCK_LOGIN_HINT };
    const cookieStore = await cookies();
    cookieStore.set(MOCK_SESSION_COOKIE, user.id, {
      httpOnly: true,
      sameSite: "lax",
      secure: process.env.NODE_ENV === "production",
      path: "/",
    });
    return { error: null };
  }

  const supabase = await createSupabaseServerClient();
  const { error } = await supabase.auth.signInWithPassword({ email, password });
  return { error: error ? error.message : null };
}

export async function signUpWithPassword(email: string, password: string): Promise<AuthResult> {
  if (isMockMode()) {
    return { error: "モックモードではサインアップできません。" + MOCK_LOGIN_HINT };
  }

  const supabase = await createSupabaseServerClient();
  const { error } = await supabase.auth.signUp({ email, password });
  return { error: error ? error.message : null };
}

export async function signOut(): Promise<void> {
  if (isMockMode()) {
    const cookieStore = await cookies();
    cookieStore.delete(MOCK_SESSION_COOKIE);
    return;
  }

  const supabase = await createSupabaseServerClient();
  await supabase.auth.signOut();
}

export async function getSession(): Promise<Session | null> {
  if (isMockMode()) {
    const cookieStore = await cookies();
    const userId = cookieStore.get(MOCK_SESSION_COOKIE)?.value;
    if (!userId) return null;
    const user = findMockUserById(userId);
    if (!user) return null;
    return { userId: user.id, email: user.email, accessToken: `mock:${user.id}` };
  }

  const supabase = await createSupabaseServerClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();
  if (!session) return null;
  return {
    userId: session.user.id,
    email: session.user.email ?? "",
    accessToken: session.access_token,
  };
}
