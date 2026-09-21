"use client";

import { useState } from "react";
import Button, { type ButtonVariant } from "@/components/ui/Button";
import Modal from "@/components/ui/Modal";
import { Toast } from "@/components/ui/Feedback";
import AddConversationForm from "@/components/AddConversationForm";
import AddFileForm from "@/components/AddFileForm";

/** 「＋ 資料を追加」：画面を移動せず、その場のモーダルで貼り付けとファイル選択を出す。 */
export default function AddSourceButton({
  pid,
  variant,
}: {
  pid: string;
  variant: ButtonVariant;
}) {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<"conversation" | "file">("conversation");
  const [toast, setToast] = useState<string | null>(null);

  function done(message: string) {
    setOpen(false);
    setToast(message);
  }

  return (
    <>
      <Button variant={variant} onClick={() => setOpen(true)}>
        <span aria-hidden>＋</span>
        <span>
          <span className="hidden sm:inline">資料を</span>追加
        </span>
      </Button>
      <Modal open={open} title="資料を追加" onClose={() => setOpen(false)}>
        <div
          role="tablist"
          className="mb-5 inline-flex rounded-md border border-gray-300 p-0.5"
        >
          {(
            [
              ["conversation", "会話を貼り付け"],
              ["file", "ファイルを選択"],
            ] as const
          ).map(([key, label]) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={mode === key}
              onClick={() => setMode(key)}
              className={`h-8 rounded px-3 text-sm focus-visible:outline-2 focus-visible:outline-gray-900 ${
                mode === key
                  ? "bg-gray-900 text-white"
                  : "text-gray-700 hover:bg-gray-100"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        {mode === "conversation" ? (
          <AddConversationForm pid={pid} onDone={done} />
        ) : (
          <AddFileForm pid={pid} onDone={done} />
        )}
      </Modal>
      <Toast message={toast} onDone={() => setToast(null)} />
    </>
  );
}
