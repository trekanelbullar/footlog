"use client";

import { useEffect } from "react";
import type { ReactNode } from "react";

// 状態の見せ方（design/ui-guidelines.md e・f）。成功は右下のトースト、失敗はその場に残す。

export function Toast({
  message,
  onDone,
}: {
  message: string | null;
  onDone: () => void;
}) {
  useEffect(() => {
    if (!message) return;
    const t = setTimeout(onDone, 4000);
    return () => clearTimeout(t);
  }, [message, onDone]);
  if (!message) return null;
  return (
    <div
      role="status"
      className="fixed right-4 bottom-4 z-50 max-w-sm rounded-md border border-gray-200 bg-gray-900 px-4 py-3 text-sm text-white"
    >
      {message}
    </div>
  );
}

export function InlineError({ children }: { children: ReactNode }) {
  if (!children) return null;
  return (
    <p
      role="alert"
      className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700"
    >
      {children}
    </p>
  );
}

export function InlineNotice({ children }: { children: ReactNode }) {
  if (!children) return null;
  return (
    <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
      {children}
    </p>
  );
}

export function EmptyState({
  title,
  body,
  action,
}: {
  title: string;
  body?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-md border border-dashed border-gray-300 px-6 py-14 text-center">
      <p className="text-base font-semibold text-gray-900">{title}</p>
      {body && <p className="max-w-md text-sm text-gray-500">{body}</p>}
      {action}
    </div>
  );
}

export function Loading({ label = "読み込んでいます…" }: { label?: string }) {
  return <p className="py-8 text-sm text-gray-500">{label}</p>;
}
