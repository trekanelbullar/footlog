import "server-only";
import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";

/**
 * サーバー側の Supabase クライアント。Cookie は Secure・HttpOnly・SameSite=Lax に固定する（design.md §2.7）。
 * このモジュールは lib/auth.ts からだけ呼ぶ（spec S10：Supabase の処理は auth.ts に閉じ込める）。
 */
export async function createSupabaseServerClient() {
  const cookieStore = await cookies();

  return createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return cookieStore.getAll();
        },
        setAll(cookiesToSet) {
          try {
            for (const { name, value, options } of cookiesToSet) {
              cookieStore.set(name, value, options);
            }
          } catch {
            // Server Component からの呼び出し（Cookie を書けない）。ミドルウェアが無いこの構成では
            // ログイン・ログアウトは Route Handler / Server Action からのみ行うため実害はない。
          }
        },
      },
      cookieOptions: {
        secure: true,
        httpOnly: true,
        sameSite: "lax",
        path: "/",
      },
    }
  );
}
