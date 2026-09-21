import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import "./globals.css";
import { getSession } from "@/lib/auth";
import LogoutButton from "@/components/LogoutButton";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "決定ログ",
  description: "プロジェクトの意思決定と根拠を自動で追跡するツール",
};

export default async function RootLayout({ children }: LayoutProps<"/">) {
  const session = await getSession();

  return (
    <html
      lang="ja"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col bg-gray-50 text-gray-900">
        <header className="border-b bg-white">
          <div className="mx-auto flex max-w-5xl items-center justify-between px-4 py-3">
            <Link href="/projects" className="font-semibold">
              決定ログ
            </Link>
            {session ? (
              <nav className="flex items-center gap-4 text-sm">
                <Link href="/projects" className="hover:underline">
                  プロジェクト
                </Link>
                <Link href="/notifications" className="hover:underline">
                  通知
                </Link>
                <span className="text-gray-500">{session.email}</span>
                <LogoutButton />
              </nav>
            ) : (
              <nav className="text-sm">
                <Link href="/login" className="hover:underline">
                  ログイン
                </Link>
              </nav>
            )}
          </div>
        </header>
        <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-6">{children}</main>
      </body>
    </html>
  );
}
