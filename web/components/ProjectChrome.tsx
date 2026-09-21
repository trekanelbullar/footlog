"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import AddSourceButton from "@/components/AddSourceButton";
import RunPanel from "@/components/RunPanel";

// プロジェクト内の画面の骨格（design/ui-guidelines.md a・b）：見出し行の右に操作、下にタブ。
// primary は画面ごとに1つだけ：レポート＝今すぐ確認、資料＝資料を追加、メンバー＝メンバーを追加
// （メンバーの primary はメンバー画面の中に置く）。

type Tab = "report" | "sources" | "members" | "questions";

function currentTab(pathname: string, pid: string): Tab {
  const rest = pathname.slice(`/projects/${pid}`.length);
  if (rest.startsWith("/sources")) return "sources";
  if (rest.startsWith("/members")) return "members";
  if (rest.startsWith("/questions")) return "questions";
  return "report";
}

export function ProjectActions({
  pid,
  hasSources,
}: {
  pid: string;
  hasSources: boolean;
}) {
  const tab = currentTab(usePathname(), pid);
  if (tab === "report") {
    // 資料がまだ無いときは、本文の空の状態に primary（資料を追加）を出すので、ここには出さない。
    if (!hasSources) return null;
    return (
      <>
        <AddSourceButton pid={pid} variant="secondary" />
        <RunPanel pid={pid} variant="primary" />
      </>
    );
  }
  if (tab === "sources") {
    return (
      <>
        {hasSources && <RunPanel pid={pid} variant="secondary" />}
        <AddSourceButton pid={pid} variant="primary" />
      </>
    );
  }
  return null;
}

export function ProjectTabs({ pid }: { pid: string }) {
  const tab = currentTab(usePathname(), pid);
  const tabs: [Tab, string, string][] = [
    ["report", "レポート", `/projects/${pid}`],
    ["sources", "資料", `/projects/${pid}/sources`],
    ["members", "メンバー", `/projects/${pid}/members`],
    ["questions", "質問", `/projects/${pid}/questions`],
  ];
  return (
    <nav
      className="-mb-px flex gap-1 overflow-x-auto"
      aria-label="プロジェクト内の画面"
    >
      {tabs.map(([key, label, href]) => (
        <Link
          key={key}
          href={href}
          aria-current={tab === key ? "page" : undefined}
          className={`border-b-2 px-3 py-2.5 text-sm whitespace-nowrap focus-visible:outline-2 focus-visible:outline-gray-900 ${
            tab === key
              ? "border-gray-900 font-semibold text-gray-900"
              : "border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-900"
          }`}
        >
          {label}
        </Link>
      ))}
    </nav>
  );
}
