critic: codex/gpt-5.6-sol xhigh（パートA：API一覧と認証（§1・§2）。抜粋のみ渡し、制限15分、完走）

## Round 2 — 2026-09-21 — 工程: 批評（クロスベンダー、design.md v2）
指摘IDは台帳では A-X1〜A-X5 と表記する

1. [X-1] 種別: 設計 / 深刻度: high  
   指摘: W1〜W24にプロジェクトメンバーを削除してアクセス権を失効させるAPIがない。  
   具体的な破綻シナリオ: 退職者または侵害された利用者をプロジェクトから外したくても、managerはW7でmemberへ降格することしかできず、その利用者は有効なJWTでW10・W13・W18・W19等を引き続き利用できる。  
   直し方の提案: `DELETE /internal/projects/{pid}/members/{uid}` を追加し、最後のmanagerは削除不可、対象行削除後は全APIの所属確認が直ちに404となることを試験する。

2. [X-2] 種別: 設計 / 深刻度: high  
   指摘: JWTについて署名と`sub`の確認しか明記されず、許可する`alg`・`iss`・`aud`・`exp`・`nbf`・利用者トークン種別の検証条件が定義されていない。  
   具体的な破綻シナリオ: 期限切れトークン、別audience向けトークン、またはHS256分岐をトークン側の`alg`で選ぶ実装に細工したトークンを送り、署名と`sub`だけを通して本人としてW1〜W23を操作する。  
   直し方の提案: 受理するアルゴリズムをサーバー設定で一意に固定し、Supabaseプロジェクト固有の`iss`・期待する`aud`・`exp`・`nbf`・認証済み利用者の種別を必須検証する。HS256/JWKSの選択をJWTヘッダーに委ねない。

3. [X-3] 種別: 実装 / 深刻度: high  
   指摘: `pid`を含むW7・W19について、子リソースがその`pid`に属することを同一クエリで確認する要件がない。  
   具体的な破綻シナリオ: 攻撃者が所属するプロジェクトAの`pid`と、推測したプロジェクトBの`version_no`を組み合わせてW19を呼び、実装がAへの所属確認後に`version_no`だけでレポートを検索するとBのレポートが返る。W7でも`uid`だけでmembershipを更新すると同様の越境が起きる。  
   直し方の提案: W7は`WHERE project_id=:pid AND user_id=:uid`、W19は`WHERE project_id=:pid AND version_no=:version_no`のように親子を不可分に検索・更新し、不一致は権限不足と同じ404にする。

4. [X-4] 種別: 前提 / 深刻度: high  
   指摘: `WORKER_SHARED_SECRET`と`CRON_SECRET`について、未設定・空文字・同一値を起動時に拒否するfail-closed条件がない。  
   具体的な破綻シナリオ: デプロイ時に`CRON_SECRET`が渡らず、実装が`os.getenv(..., "")`を使うと、空または欠落した`X-Cron-Secret`との比較が成立して、外部からW17を繰り返し実行できる。両secretが同値ならweb側secretの漏えいがcron権限にも拡大する。  
   直し方の提案: 起動時に両secretの存在、十分なランダム長、相互非一致を検証し、不備があればworkerを起動させない。ヘッダー欠落は比較前に必ず401とする。

5. [X-5] 種別: 設計 / 深刻度: mid  
   指摘: ブラウザ→webの変更系Route HandlerにCSRF対策が定義されておらず、Cookieのセッション確認だけに依存している。  
   具体的な破綻シナリオ: セッションCookieが`SameSite=None`、または同一site内の侵害された別originから送信可能な状態で、ログイン中の利用者にW14相当のPOSTを踏ませると、本人のJWTで意図しないrunが作成される。  
   直し方の提案: Cookieを`Secure`・`HttpOnly`・適切な`SameSite`に固定したうえで、変更系Route Handlerでは`Origin`/`Host`照合またはCSRFトークンを必須にし、JSON系は許可する`Content-Type`も限定する。