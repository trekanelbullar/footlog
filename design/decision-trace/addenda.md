# 承認後の追加指示（decision-trace）

承認済みの design.md（v3:abce42014906）は書き換えず、承認後にユーザーから受けた指示をここに記録する。design.md と食い違う場合は、この文書が優先する。

## AD-1 未決事項 U5〜U12 の確定（2026-09-21）

すべて design.md の既定値で確定。
- U5：質問への回答はアプリ内の回答欄のみ。メールには回答画面へのリンクを載せる
- U6：取り込みは W8・W9 の中で同期的に行う
- U7：web が W15 を最大300秒待ち、画面は W16 を2秒ごとに問い合わせる。worker は240秒で打ち切る
- U8：図の文法は作りとテストで保証し、描けなければブラウザ側で箇条書きに切り替える
- U9：design.md §1.3 の権限表のとおり
- U10：プロジェクトごとに member と manager の2つ。admin は作らない
- U11：定期実行は1時間ごと（デモ中は5分ごと）、1回の呼び出しで全プロジェクトを順に
- U12：段階ごとの出力上限は環境変数で設定。既定は抽出・組み立てが4000、それ以外が500

## AD-2 思考モードと出力上限（2026-09-21）

- 思考モードの既定は、全段階 `off`。
- ある段階で思考モードが `off` 以外のときは、思考に使われる分で出力の枠が食われる（PoC で `orcarouter/auto` が出力上限を使い切り、本文が空になったのと同じ事故）。そこで：
  - その段階の出力上限が `ORCAROUTER_REASONING_MIN_MAX_TOKENS`（既定 16000）より小さければ、実際に使う上限をこの値まで引き上げる。
  - 引き上げたときは、起動時に警告を出す（段階名、設定値、実際に使う値）。
  - コストの予約（design.md §6.1）は、引き上げた後の上限で見積もる。

## AD-3 テスト用の DB（2026-09-21）

- DB を使うテストは、手元では Homebrew の PostgreSQL 16 に作った `decision_trace_test` で流す（`TEST_DATABASE_URL=postgresql://localhost/decision_trace_test`）。
- `.env` の `DATABASE_URL`（Supabase）は、テストでは**絶対に使わない**。テストの接続先は `TEST_DATABASE_URL` だけから取り、ホスト名が `localhost`・`127.0.0.1`・`postgres`（CI のサービスコンテナ）のどれかでなければ、テストを始めずに失敗させる。
- CI のサービスコンテナも、手元と同じ PostgreSQL 16 にする。

## AD-4 app_worker の RLS の扱い：ポリシー方式（2026-09-21、ユーザー決定。当初の BYPASSRLS 案は取り消し）

- 全表で RLS を有効にする（design.md §3）。そのうえで、各表に `app_worker` にだけ全行を許すポリシーを作る：`CREATE POLICY app_worker_all ON <表> TO app_worker USING (true) WITH CHECK (true)`。`anon`・`authenticated` 向けのポリシーは作らないので、Supabase の匿名キーとログインユーザーのキーからは引き続き何も読めない。
- `app_worker` に BYPASSRLS は**付けない**。理由：PostgreSQL 16 以降は BYPASSRLS つきのロールを作る側も BYPASSRLS を持っている必要があり、スーパーユーザーではない Supabase の `postgres` ユーザーで作れるかが不確か。ポリシー方式なら特別な権限が要らず、手元と Supabase で同じように動く。
- ポリシーは、どの操作に効くかを1つのポリシー（`FOR ALL`）で決める。追記専用の表の UPDATE・DELETE・TRUNCATE は、ポリシーではなく、トリガーと REVOKE で止める（design.md §3.2 のまま）。
- 表を足すマイグレーションでは、その表にも同じポリシーを必ず作る。
- 起動時の確認（AD-5）では、`app_worker` が BYPASSRLS を持っていないことも確かめる。

## AD-5 起動時の接続ユーザーの確認と、マイグレーション用の接続の分離（2026-09-21）

- worker は起動時に、今つないでいる DB のユーザーを確かめる。`current_user` が `app_worker` でない、またはスーパーユーザーである場合は、起動しない（管理者の接続文字列で誤って起動したことに気づくため）。
- テスト環境だけは除く。`APP_ENV=test` のときだけ確認を飛ばし、それ以外（未設定を含む）は必ず確認する（設定を忘れたときに、確認が働く側に倒す）。
- マイグレーションは `MIGRATION_DATABASE_URL`（Supabase の `postgres` ユーザー）で流し、アプリの `DATABASE_URL`（`app_worker`）とは分ける。マイグレーションのコマンドは `DATABASE_URL` を読まない。
