import type { Metadata } from "next";
import {
  Geist,
  Geist_Mono,
  Noto_Sans_JP,
  Noto_Serif_JP,
} from "next/font/google";
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

// 記事の体裁（レポート・トップ）で使う和文書体。見出しは明朝の太字、本文はゴシック。
const notoSerifJp = Noto_Serif_JP({
  variable: "--font-noto-serif-jp",
  weight: ["700", "900"],
  subsets: ["latin"],
  preload: false,
});

const notoSansJp = Noto_Sans_JP({
  variable: "--font-noto-sans-jp",
  weight: ["400", "500", "700"],
  subsets: ["latin"],
  preload: false,
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
      className={`${geistSans.variable} ${geistMono.variable} ${notoSerifJp.variable} ${notoSansJp.variable} h-full antialiased`}
    >
      <body className="flex min-h-full flex-col bg-white font-sans-jp text-gray-900">
        <header className="border-b border-gray-200 bg-white">
          <div className="mx-auto flex h-14 max-w-6xl items-center justify-between gap-3 px-4">
            <Link href="/projects" className="whitespace-nowrap font-semibold">
              決定ログ
            </Link>
            {session ? (
              <nav className="flex items-center gap-4 whitespace-nowrap text-sm">
                <Link href="/projects" className="hover:underline">
                  プロジェクト
                </Link>
                <Link href="/notifications" className="hover:underline">
                  通知
                </Link>
                <span className="hidden text-gray-500 sm:inline">
                  {session.email}
                </span>
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
        <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6">
          {children}
        </main>
      </body>
    </html>
  );
}
