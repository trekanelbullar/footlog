/**
 * モックモードで固定の2ユーザー（manager / member）を切り替えられるようにするための定義。
 * 実在の会社名・個人名は使わない。
 */

export interface MockUser {
  id: string;
  email: string;
}

export const MOCK_MANAGER: MockUser = {
  id: "00000000-0000-4000-8000-000000000001",
  email: "manager@example.com",
};

export const MOCK_MEMBER: MockUser = {
  id: "00000000-0000-4000-8000-000000000002",
  email: "member@example.com",
};

export const MOCK_USERS: MockUser[] = [MOCK_MANAGER, MOCK_MEMBER];

export function findMockUserByEmail(email: string): MockUser | null {
  const normalized = email.trim().toLowerCase();
  return MOCK_USERS.find((u) => u.email.toLowerCase() === normalized) ?? null;
}

export function findMockUserById(id: string): MockUser | null {
  return MOCK_USERS.find((u) => u.id === id) ?? null;
}

/** モックのセッションCookieの名前（値はユーザーIDのみ）。 */
export const MOCK_SESSION_COOKIE = "mock_session_user_id";
