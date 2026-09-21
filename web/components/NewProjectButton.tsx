"use client";

import { useState } from "react";
import Button from "@/components/ui/Button";
import Modal from "@/components/ui/Modal";
import { Toast } from "@/components/ui/Feedback";
import CreateProjectForm from "@/components/CreateProjectForm";

/** 「＋ 新規プロジェクト」：その場のモーダルで作成フォームを出す。 */
export default function NewProjectButton() {
  const [open, setOpen] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  return (
    <>
      <Button variant="primary" onClick={() => setOpen(true)}>
        <span aria-hidden>＋</span>
        <span>
          <span className="hidden sm:inline">新規</span>プロジェクト
        </span>
      </Button>
      <Modal
        open={open}
        title="新規プロジェクト"
        onClose={() => setOpen(false)}
      >
        <CreateProjectForm
          onDone={(message) => {
            setOpen(false);
            setToast(message);
          }}
        />
      </Modal>
      <Toast message={toast} onDone={() => setToast(null)} />
    </>
  );
}
