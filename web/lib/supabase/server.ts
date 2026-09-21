import "server-only";
import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";

/**
 * サーバー側の Supabase クライアント。Cookie は Secure・HttpOnly・SameSite=Lax に固定する（design.md §2.7）。
 * このモジュールは lib/auth.ts からだけ呼ぶ（spec S10：Supabase の処理は auth.ts に閉じ込める）。
 */
export async function createSupabaseServerClient() {
  const cookieStore = await cookies();

  // Cloud Run では実行時に渡す SUPABASE_URL / SUPABASE_PUBLISHABLE_KEY を優先する。
  // NEXT_PUBLIC_ で始まる変数は next build のときにコードへ埋め込まれ、実行時の設定が
  // 効かないため（手元の開発では web/.env.local の NEXT_PUBLIC_ の値を使う）。
  const supabaseUrl = process.env.SUPABASE_URL ?? process.env.NEXT_PUBLIC_SUPABASE_URL;
  const supabaseKey =
    process.env.SUPABASE_PUBLISHABLE_KEY ?? process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

  return createServerClient(
    supabaseUrl!,
    supabaseKey!,
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
