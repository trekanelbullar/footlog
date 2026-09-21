# チーム共有用メッセージ

以下をコピーして、SlackやLINEなどでチームメンバーへ送れます。

---

GitHubにハッカソン用リポジトリを用意しました。

リポジトリ：
https://github.com/trekanelbullar/ai-hackathon-team-a

最初にGitHubから届いている招待を承認してください。その後、リポジトリのREADMEにある
「1. 必要なツールを準備する」から順番に進めると、Orca Routerの接続確認までできます。

大まかな流れは次のとおりです。

1. Git、Python 3.13.15、uvを準備
2. リポジトリをclone
3. `uv sync --all-groups --locked`を実行
4. `.env.example`を`.env`へコピー
5. 自分で発行したOrca Router APIキーを`.env`へ入力
6. `uv run ai-hackathon`で接続確認

APIキーはパスワードと同じ秘密情報です。私のキーを共有するのではなく、各自でキーを発行して
ください。APIキーをSlack、LINE、GitHub、スクリーンショットなどへ貼らないようお願いします。

接続確認には`qwen/qwen3.7-flash`を使用します。このモデルは無料ではなく、少量ですがAPI残高を
消費します。

途中でエラーになった場合は、APIキーを隠した状態でエラーメッセージだけ共有してください。

---
