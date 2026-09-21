-- decision-trace: 承認後の追加指示 AD-8〜AD-12（design/decision-trace/addenda.md）に
-- 伴うスキーマ変更。新しい表は追加しない（既存表への列追加・CHECK制約の更新のみ）。
-- RLS・ポリシーは 0001 で有効化済みの表をそのまま使う。

-- ============================================================
-- AD-10：抽出時に判定した「過去の却下案との類似」
-- ============================================================
-- 出来事ごとに任意（decision/finding/open_issue だけに付く）。events は既に
-- 追記専用のトリガー（forbid_update_delete）で保護されているため、この列も
-- 追記時に書いたきり以後は変わらない。
ALTER TABLE events ADD COLUMN similar_rejected_event_no integer;

-- ============================================================
-- AD-9：cost_limited のアプリ内通知の種類を追加する
-- ============================================================
ALTER TABLE notifications DROP CONSTRAINT notifications_kind_check;
ALTER TABLE notifications ADD CONSTRAINT notifications_kind_check
    CHECK (kind IN ('question', 'report', 'no_progress', 'cost_limited'));

-- ============================================================
-- AD-11：質問の上限を超えた分を保留する状態を追加する
-- ============================================================
ALTER TABLE agent_questions DROP CONSTRAINT agent_questions_status_check;
ALTER TABLE agent_questions ADD CONSTRAINT agent_questions_status_check
    CHECK (status IN ('open', 'answered', 'expired', 'deferred'));
