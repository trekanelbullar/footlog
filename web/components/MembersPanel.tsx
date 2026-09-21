"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import type { Member, Role } from "@/lib/worker-types";

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

  async function patchMember(uid: string, body: Partial<{ role: Role; notify_on_progress: boolean; notify_on_no_progress: boolean }>) {
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
      const res = await fetch(`/api/projects/${pid}/members/${uid}`, { method: "DELETE" });
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
    setError(null);
    setBusy("new");
    try {
      const res = await fetch(`/api/projects/${pid}/members`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: newEmail, role: newRole }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.message ?? "追加に失敗しました。");
        return;
      }
      setNewEmail("");
      router.refresh();
    } catch {
      setError("通信に失敗しました。");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      {error && <p className="rounded bg-red-50 p-2 text-sm text-red-700">{error}</p>}

      <table className="w-full max-w-3xl text-left text-sm">
        <thead className="text-gray-500">
          <tr>
            <th className="py-1 pr-2">メールアドレス</th>
            <th className="py-1 pr-2">ロール</th>
            <th className="py-1 pr-2">進捗通知</th>
            <th className="py-1 pr-2">無進捗通知</th>
            <th className="py-1 pr-2">操作</th>
          </tr>
        </thead>
        <tbody>
          {initialMembers.map((m) => {
            const canEditNotify = myRole === "manager" || m.user_id === myUserId;
            return (
              <tr key={m.user_id} className="border-t">
                <td className="py-2 pr-2">
                  {m.email}
                  {m.user_id === myUserId && <span className="ml-1 text-xs text-gray-400">（自分）</span>}
                </td>
                <td className="py-2 pr-2">
                  {myRole === "manager" ? (
                    <select
                      className="rounded border px-1 py-0.5"
                      value={m.role}
                      disabled={busy === m.user_id}
                      onChange={(e) => patchMember(m.user_id, { role: e.target.value as Role })}
                    >
                      <option value="member">メンバー</option>
                      <option value="manager">マネージャー</option>
                    </select>
                  ) : m.role === "manager" ? (
                    "マネージャー"
                  ) : (
                    "メンバー"
                  )}
                </td>
                <td className="py-2 pr-2">
                  <input
                    type="checkbox"
                    checked={m.notify_on_progress}
                    disabled={!canEditNotify || busy === m.user_id}
                    onChange={(e) => patchMember(m.user_id, { notify_on_progress: e.target.checked })}
                  />
                </td>
                <td className="py-2 pr-2">
                  <input
                    type="checkbox"
                    checked={m.notify_on_no_progress}
                    disabled={!canEditNotify || busy === m.user_id}
                    onChange={(e) => patchMember(m.user_id, { notify_on_no_progress: e.target.checked })}
                  />
                </td>
                <td className="py-2 pr-2">
                  {myRole === "manager" && (
                    <button
                      type="button"
                      disabled={busy === m.user_id}
                      onClick={() => removeMember(m.user_id)}
                      className="rounded border px-2 py-1 text-xs text-red-600 hover:bg-red-50 disabled:opacity-50"
                    >
                      削除
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {myRole === "manager" && (
        <form onSubmit={addMember} className="flex max-w-md flex-col gap-3 rounded border bg-white p-4">
          <h2 className="text-sm font-semibold">メンバーを追加</h2>
          <label className="flex flex-col gap-1 text-sm">
            登録済みユーザーのメールアドレス
            <input
              type="email"
              required
              className="rounded border px-3 py-2"
              value={newEmail}
              onChange={(e) => setNewEmail(e.target.value)}
            />
          </label>
          <label className="flex items-center gap-2 text-sm">
            ロール
            <select
              className="rounded border px-2 py-1"
              value={newRole}
              onChange={(e) => setNewRole(e.target.value as Role)}
            >
              <option value="member">メンバー</option>
              <option value="manager">マネージャー</option>
            </select>
          </label>
          <button
            type="submit"
            disabled={busy === "new"}
            className="w-fit rounded bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
          >
            追加する
          </button>
        </form>
      )}
    </div>
  );
}
