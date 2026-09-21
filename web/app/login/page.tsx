import Link from "next/link";
import LoginForm from "@/components/LoginForm";
import { isMockMode } from "@/lib/mock/mode";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ signedUp?: string }>;
}) {
  const { signedUp } = await searchParams;

  return (
    <div className="mx-auto max-w-sm">
      <h1 className="mb-6 text-xl font-semibold">ログイン</h1>

      {signedUp && (
        <p className="mb-4 rounded bg-green-50 p-2 text-sm text-green-700">
          サインアップしました。ログインしてください。
        </p>
      )}

      <LoginForm />

      <p className="mt-4 text-sm text-gray-600">
        アカウントが無い場合は<Link href="/signup" className="text-blue-600 hover:underline">サインアップ</Link>
      </p>
      {isMockMode() && (
        <p className="mt-6 text-xs text-gray-400">
          モックモードでは manager@example.com / member@example.com のいずれか（パスワードは任意）でログインできます。
        </p>
      )}
    </div>
  );
}
