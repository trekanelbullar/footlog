"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import type { Member, Role } from "@/lib/worker-types";
import Button from "@/components/ui/Button";
import { SelectField, TextField } from "@/components/ui/Field";
import { InlineError, Toast } from "@/components/ui/Feedback";
import Modal from "@/components/ui/Modal";

export default function MembersPanel({
  pid,
  initialMembers,
  myUserId,
  myRole,
}: {
  pid: string;
  initialMembers: Member[];
  myUserId: string;
  myRole: Role;
}) {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [newEmail, setNewEmail] = useState("");
  const [newRole, setNewRole] = useState<Role>("member");
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  async function patchMember(
    uid: string,
    body: Partial<{
      role: Role;
      notify_on_progress: boolean;
      notify_on_no_progress: boolean;
    }>,
  ) {
    setError(null);
    setBusy(uid);
    try {
      const res = await fetch(`/api/projects/${pid}/members/${uid}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.message ?? "変更に失敗しました。");
        return;
      }
      router.refresh();
    } catch {
      setError("通信に失敗しました。");
    } finally {
      setBusy(null);
    }
  }

  async function removeMember(uid: string) {
    setError(null);
    setBusy(uid);
    try {
      const res = await fetch(`/api/projects/${pid}/members/${uid}`, {
        method: "DELETE",
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.message ?? "削除に失敗しました。");
        return;
      }
      router.refresh();
    } catch {
      setError("通信に失敗しました。");
    } finally {
      setBusy(null);
    }
  }

  async function addMember(e: React.FormEvent) {
    e.preventDefault();
    setAddError(null);
    setBusy("new");
    try {
      const res = await fetch(`/api/projects/${pid}/members`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: newEmail, role: newRole }),
      });
      const data = await res.json();
      if (!res.ok) {
        setAddError(data.message ?? "追加に失敗しました。");
        return;
      }
      setToast(`${newEmail} を追加しました。`);
      setNewEmail("");
      setAdding(false);
      router.refresh();
    } catch {
      setAddError("通信に失敗しました。");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-base font-semibold text-gray-900">
          メンバー（{initialMembers.length}人）
        </h2>
        {myRole === "manager" && (
          <Button variant="primary" onClick={() => setAdding(true)}>
            <span aria-hidden>＋</span>
            <span>
              <span className="hidden sm:inline">メンバーを</span>追加
            </span>
          </Button>
        )}
      </div>
      <InlineError>{error}</InlineError>

      <ul className="divide-y divide-gray-200 rounded-md border border-gray-200">
        {initialMembers.map((m) => {
          const canEditNotify = myRole === "manager" || m.user_id === myUserId;
          return (
            <li
              key={m.user_id}
              className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:justify-between"
            >
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-gray-900">
                  {m.email}
                  {m.user_id === myUserId && (
                    <span className="ml-1 text-xs font-normal text-gray-400">
                      （自分）
                    </span>
                  )}
                </p>
                <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-600">
                  <label className="inline-flex items-center gap-1.5">
                    <input
                      type="checkbox"
                      className="size-4 accent-gray-900"
                      checked={m.notify_on_progress}
                      disabled={!canEditNotify || busy === m.user_id}
                      onChange={(e) =>
                        patchMember(m.user_id, {
                          notify_on_progress: e.target.checked,
                        })
                      }
                    />
                    進捗を通知
                  </label>
                  <label className="inline-flex items-center gap-1.5">
                    <input
                      type="checkbox"
                      className="size-4 accent-gray-900"
                      checked={m.notify_on_no_progress}
                      disabled={!canEditNotify || busy === m.user_id}
                      onChange={(e) =>
                        patchMember(m.user_id, {
                          notify_on_no_progress: e.target.checked,
                        })
                      }
                    />
                    無進捗を通知
                  </label>
                </div>
              </div>
              <div className="flex items-center gap-1 sm:justify-end">
                {myRole === "manager" ? (
                  <SelectField
                    label={`${m.email} のロール`}
                    hideLabel
                    className="w-40"
                    value={m.role}
                    disabled={busy === m.user_id}
                    onChange={(e) =>
                      patchMember(m.user_id, { role: e.target.value as Role })
                    }
                  >
                    <option value="member">メンバー</option>
                    <option value="manager">マネージャー</option>
                  </SelectField>
                ) : (
                  <span className="rounded-full border border-gray-300 px-2 py-0.5 text-xs text-gray-700">
                    {m.role === "manager" ? "マネージャー" : "メンバー"}
                  </span>
                )}
                {myRole === "manager" && (
                  <Button
                    variant="ghost"
                    disabled={busy === m.user_id}
                    onClick={() => removeMember(m.user_id)}
                  >
                    削除
                  </Button>
                )}
              </div>
            </li>
          );
        })}
      </ul>

      <Modal
        open={adding}
        title="メンバーを追加"
        onClose={() => setAdding(false)}
      >
        <form onSubmit={addMember} className="flex flex-col gap-4">
          <InlineError>{addError}</InlineError>
          <TextField
            label="メールアドレス"
            help="登録済みのユーザーだけ追加できます。"
            type="email"
            required
            value={newEmail}
            onChange={(e) => setNewEmail(e.target.value)}
          />
          <SelectField
            label="ロール"
            className="max-w-xs"
            value={newRole}
            onChange={(e) => setNewRole(e.target.value as Role)}
          >
            <option value="member">メンバー</option>
            <option value="manager">マネージャー</option>
          </SelectField>
          <div>
            <Button type="submit" variant="primary" disabled={busy === "new"}>
              追加する
            </Button>
          </div>
        </form>
      </Modal>
      <Toast message={toast} onDone={() => setToast(null)} />
    </div>
  );
}
