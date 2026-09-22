"use client";

import { useEffect } from "react";
import type { ReactNode } from "react";
import Button from "@/components/ui/Button";

/** 画面を移動せずに開く入力のための枠（資料の追加、新規プロジェクト、メンバーの追加）。 */
export default function Modal({
  open,
  title,
  onClose,
  children,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={title}
      className="fixed inset-0 z-40 flex items-start justify-center overflow-y-auto bg-black/40 p-4 sm:p-10"
      onClick={onClose}
    >
      <div
        className="w-full max-w-2xl rounded-lg border border-gray-200 bg-white"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-gray-200 px-5 py-3">
          <h2 className="text-base font-semibold text-gray-900">{title}</h2>
          <Button variant="ghost" onClick={onClose} aria-label="閉じる">
            ✕
          </Button>
        </div>
        <div className="px-5 py-5">{children}</div>
      </div>
    </div>
  );
}
