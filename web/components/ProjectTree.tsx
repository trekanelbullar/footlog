"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import Button from "@/components/ui/Button";
import { formatJst } from "@/lib/format";
import type { MyProjectSummary, ReportSummary } from "@/lib/worker-types";

// 左のツリー（design/ui-spec-3col.md 1、addenda AD-14）：プロジェクト → 固定見出し「決定ログ」→ 版。
// 今のデータの階層はプロジェクト → 版の2つだけなので、ワークスペースは出さない。
// 1024px 未満ではハンバーガーで開くドロワーにする。

function DocIcon() {
  return (
    <svg
      aria-hidden
      viewBox="0 0 16 16"
      className="size-4 shrink-0 fill-none stroke-current"
      strokeWidth="1.3"
    >
      <path d="M4 1.5h5l3 3v10H4z" />
      <path d="M9 1.5v3h3" />
    </svg>
  );
}

function FolderIcon({ muted = false }: { muted?: boolean }) {
  return (
    <svg
      aria-hidden
      viewBox="0 0 16 16"
      className={`size-4 shrink-0 ${muted ? "fill-[#c9c1b5]" : "fill-[#c8962e]"}`}
    >
      <path d="M1.5 3.5h4.2l1.4 1.5h7.4v8.5h-13z" />
    </svg>
  );
}

function Chevron({ open }: { open: boolean }) {
  return (
    <svg
      aria-hidden
      viewBox="0 0 16 16"
      className={`size-3.5 shrink-0 fill-none stroke-current transition-transform ${open ? "rotate-90" : ""}`}
      strokeWidth="1.8"
    >
      <path d="M6 3.5l4.5 4.5L6 12.5" />
    </svg>
  );
}

function TreeBody({
  pid,
  projects,
  reports,
  onNavigate,
}: {
  pid: string;
  projects: MyProjectSummary[];
  reports: ReportSummary[];
  onNavigate?: () => void;
}) {
  const pathname = usePathname();
  const [folderOpen, setFolderOpen] = useState(true);
  const sorted = [...reports].sort((a, b) => b.version_no - a.version_no);
  const latestVisible = sorted.find((r) => !r.withheld)?.version_no ?? null;
  const match = pathname.match(/\/reports\/(\d+)/);
  const onReportTab = pathname === `/projects/${pid}` || Boolean(match);
  const activeVersion = match
    ? Number(match[1])
    : pathname === `/projects/${pid}`
      ? latestVisible
      : null;

  const shortDate = (iso: string) => {
    const d = formatJst(iso);
    return `${Number(d.slice(5, 7))}/${Number(d.slice(8, 10))}`;
  };
  const rowBase =
    "flex items-center gap-1.5 rounded-md px-2 py-1.5 hover:bg-[#e6e1d8] focus-visible:outline-2 focus-visible:outline-[#b4541a]";

  return (
    <nav aria-label="プロジェクトと版" className="flex h-full flex-col text-sm">
      <ul className="flex-1 overflow-y-auto px-3 py-2">
        {projects.map((p) => {
          const current = p.project_id === pid;
          return (
            <li key={p.project_id} className="mb-1">
              <Link
                href={`/projects/${p.project_id}`}
                onClick={onNavigate}
                className={`${rowBase} ${current ? "font-semibold text-[#2b2823]" : "text-[#5b554c]"}`}
              >
                <Chevron open={current} />
                <span className="truncate">{p.name}</span>
              </Link>
              {current && (
                <div className="ml-3">
                  <button
                    type="button"
                    onClick={() => setFolderOpen((o) => !o)}
                    aria-expanded={folderOpen}
                    className={`${rowBase} w-full text-left font-medium text-[#2b2823]`}
                  >
                    <Chevron open={folderOpen} />
                    <FolderIcon />
                    決定ログ
                  </button>
                  {folderOpen && (
                    <ul className="ml-4 flex flex-col gap-0.5 py-1">
                      {sorted.length === 0 && (
                        <li className="px-2 py-1.5 text-xs text-[#8a8175]">
                          まだ版はありません
                        </li>
                      )}
                      {sorted.map((r) => {
                        const active =
                          onReportTab && r.version_no === activeVersion;
                        return (
                          <li key={r.version_no}>
                            <Link
                              href={`/projects/${pid}/reports/${r.version_no}`}
                              onClick={onNavigate}
                              aria-current={active ? "page" : undefined}
                              className={`flex items-start gap-2 rounded-lg border px-2.5 py-2 focus-visible:outline-2 focus-visible:outline-[#b4541a] ${
                                active
                                  ? "border-[#e2ddd3] bg-white font-medium text-[#2b2823]"
                                  : "border-transparent text-[#5b554c] hover:bg-[#e6e1d8]"
                              }`}
                            >
                              <span
                                className={`mt-0.5 ${active ? "text-[#b4541a]" : "text-[#8a8175]"}`}
                              >
                                <DocIcon />
                              </span>
                              <span className="min-w-0">
                                <span className="block truncate">
                                  第{r.version_no}版の決定ログ
                                  {r.withheld ? "（表示不可）" : ""}
                                </span>
                                <span className="block text-xs font-normal text-[#8a8175]">
                                  第{r.version_no}版
                                  {r.audience === "managers"
                                    ? "・管理者向け"
                                    : ""}
                                  ・{shortDate(r.generated_at)}更新
                                </span>
                              </span>
                            </Link>
                          </li>
                        );
                      })}
                    </ul>
                  )}
                  {(
                    [
                      ["資料", `/projects/${pid}/sources`],
                      ["メンバー", `/projects/${pid}/members`],
                    ] as const
                  ).map(([label, href]) => {
                    const here = pathname.startsWith(href);
                    return (
                      <Link
                        key={href}
                        href={href}
                        onClick={onNavigate}
                        aria-current={here ? "page" : undefined}
                        className={`${rowBase} ${here ? "font-medium text-[#2b2823]" : "text-[#5b554c]"}`}
                      >
                        <Chevron open={false} />
                        <FolderIcon muted />
                        {label}
                      </Link>
                    );
                  })}
                </div>
              )}
            </li>
          );
        })}
      </ul>
      <p className="border-t border-[#e2ddd3] px-5 py-3 text-xs text-[#8a8175]">
        決定ログを自動生成・全{reports.length}件
      </p>
    </nav>
  );
}

/** PC では左に固定、1024px 未満ではドロワー（開くボタンは MobileTreeButton）。 */
export default function ProjectTree(props: {
  pid: string;
  projects: MyProjectSummary[];
  reports: ReportSummary[];
}) {
  return (
    <aside className="hidden w-[264px] shrink-0 border-r border-[#e2ddd3] bg-[#efebe4] lg:block">
      <div className="sticky top-0 h-[calc(100vh-3.5rem)] pt-3">
        <TreeBody {...props} />
      </div>
    </aside>
  );
}

export function MobileTreeButton(props: {
  pid: string;
  projects: MyProjectSummary[];
  reports: ReportSummary[];
}) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);
  return (
    <>
      <Button
        variant="ghost"
        className="lg:hidden"
        aria-label="プロジェクトと版の一覧を開く"
        onClick={() => setOpen(true)}
      >
        <svg
          aria-hidden
          viewBox="0 0 16 16"
          className="size-5 fill-none stroke-current"
          strokeWidth="1.6"
        >
          <path d="M2 4h12M2 8h12M2 12h12" />
        </svg>
      </Button>
      {open && (
        <div
          className="fixed inset-0 z-40 lg:hidden"
          role="dialog"
          aria-modal="true"
          aria-label="プロジェクトと版"
        >
          <div
            className="absolute inset-0 bg-black/30"
            onClick={() => setOpen(false)}
          />
          <div className="absolute inset-y-0 left-0 flex w-[280px] max-w-[85vw] flex-col border-r border-[#e2ddd3] bg-[#efebe4]">
            <div className="flex items-center justify-between border-b border-gray-200 px-3 py-2">
              <span className="text-sm font-semibold text-gray-900">
                プロジェクトと版
              </span>
              <Button
                variant="ghost"
                onClick={() => setOpen(false)}
                aria-label="閉じる"
              >
                ✕
              </Button>
            </div>
            <div className="min-h-0 flex-1">
              <TreeBody {...props} onNavigate={() => setOpen(false)} />
            </div>
          </div>
        </div>
      )}
    </>
  );
}
