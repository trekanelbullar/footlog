# Footlog — AIとの壁打ちで消えていく思考の足跡を残す

## 概要

複数人のプロジェクトに散らばったAIとの対話ログや作業ファイルを集約し、
「何を決めたか」「その理由」「検討したが採用しなかった案」「次にやること」を
根拠つきで自動整理する進捗管理エージェントです。

普段どおりAIとやり取りしたログや作業メモをプロジェクトに置いておくだけで、AIが
内容を読み解き、決定・却下案・未解決事項を仕分けた資料を定期的に作り直します。
進捗があれば関係者にメールで通知し、一定期間進捗がないときはアラートを出します。

<img width="956" height="756" alt="スクリーンショット 2026-09-22 14 12 52" src="https://github.com/user-attachments/assets/bc3f7570-02a4-4aca-a2b9-8abcf09b848d" />


詳細な設計思想は[こちらの記事](https://qiita.com/aym__/items/63904650641ec10e46f9)、
仕様は`design/`の設計書を参照してください（現段階ではどちらも作成中の部分があります）。

## まず確認するもの

セットアップには次の3つが必要です。

- GitHubリポジトリへの招待
- Python 3.13.15（完全固定）
- Orca Routerで自分用に発行したAPIキー

APIキーはパスワードと同じ秘密情報です。他のメンバーのキーを使い回さず、チャットやメールで
送らないでください。

## 1. 必要なツールを準備する

次のツールをインストールします。

- [Git](https://git-scm.com/downloads)
- [Python 3.13.15](https://www.python.org/downloads/release/python-31315/)
- [uv](https://docs.astral.sh/uv/getting-started/installation/)

ターミナルでバージョンを確認します。

```bash
git --version
python3 --version
uv --version
```

Pythonは必ず次の表示になるようにしてください。

```text
Python 3.13.15
```

## 2. リポジトリを取得する

GitHubの招待を承認してから、作業用フォルダで以下を実行します。

```bash
git clone https://github.com/trekanelbullar/ai-hackathon-team-a.git
cd ai-hackathon-team-a
```

## 3. Python環境を作る

以下の1コマンドで、必要なライブラリと`.venv`を準備します。

```bash
uv sync --all-groups --locked
```

## 4. 自分専用のAPIキーを設定する

macOSまたはLinuxでは次を実行します。

```bash
cp .env.example .env
```

Windows PowerShellでは次を実行します。

```powershell
Copy-Item .env.example .env
```

作成された`.env`をVS Codeなどで開き、次の1行だけを自分のAPIキーに置き換えます。

```dotenv
ORCAROUTER_API_KEY=sk-orca-your-key
```

接続確認済みの既定モデルは`qwen/qwen3.7-flash`です。このモデルは無料モデルではなく、
利用量に応じてOrca Routerの残高を消費します。最新料金はOrca RouterのModels画面で確認して
ください。

`.env`はGit管理の対象外です。APIキーをREADME、ソースコード、Issue、Pull Request、
スクリーンショット、チャットなどへ貼り付けないでください。

## 5. 接続を確認する

次のコマンドを実行します。

```bash
uv run ai-hackathon
```

`Prompt:`と表示されたら、次の文章を入力してEnterを押します。

```text
接続できている場合は「接続完了」とだけ返してください。
```

`接続完了`と表示されればセットアップ完了です。この操作は実際にOrca Routerへリクエストを
送り、少量のAPIクレジットを消費します。

プロンプトをコマンド引数に書くとシェル履歴に残る可能性があるため、対話入力を推奨します。

## 困ったとき

### `AuthenticationError`

- `.env`のAPIキーが`sk-orca-`から始まっているか確認する
- キーの前後に空白や引用符が入っていないか確認する
- Orca RouterのAPI Keys画面でキーがActiveか確認する

### `RateLimitError`

- 短時間に連続実行せず、少し待ってから再実行する
- Orca RouterのRequests画面でエラー詳細を確認する
- APIキーの利用上限とワークスペース残高を確認する

### `Orca Router returned an empty response`

接続自体は成功していますが、上流モデルが一時的に空の応答を返しています。少し待ってから
再実行し、続く場合はOrca RouterのRequests画面で該当リクエストを確認してください。

## 開発用チェック

以下のテストは外部APIへ接続せず、APIクレジットも消費しません。

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

自動修正と整形は以下で実行できます。

```bash
uv run ruff check --fix .
uv run ruff format .
```

## 環境変数

| 変数 | 必須 | 既定値 | 説明 |
| --- | --- | --- | --- |
| `ORCAROUTER_API_KEY` | はい | なし | Orca RouterのAPIキー |
| `ORCAROUTER_BASE_URL` | いいえ | `https://api.orcarouter.ai/v1` | APIのベースURL |
| `ORCAROUTER_MODEL` | いいえ | `qwen/qwen3.7-flash` | 利用するモデルID |
| `ORCAROUTER_TIMEOUT_SECONDS` | いいえ | `30` | リクエストのタイムアウト秒数 |
| `ORCAROUTER_MAX_RETRIES` | いいえ | `2` | 一時的な失敗に対する再試行回数 |
| `ORCAROUTER_MAX_TOKENS` | いいえ | `500` | 1リクエストの最大出力トークン数 |
| `ORCAROUTER_MAX_PROMPT_CHARS` | いいえ | `20000` | 誤送信・過剰利用を防ぐ入力文字数上限 |

## セキュリティ方針

- APIキーや認証ファイルはGitへコミットしない
- APIキー、入力プロンプト、モデルの応答をログへ出力しない
- 個人情報、社外秘、パスワードなどを外部APIへ送らない
- ブラウザ向けUIを作る場合も、APIキーをフロントエンドへ渡さない
- エラー表示は詳細な内部情報を利用者へ公開しない
- API側で利用上限を設定し、不要になったキーは失効させる

Orca Routerを含む外部APIへ送信したデータは、外部サービス側で処理されます。投入してよい
データかどうかを事前に確認してください。

## 既知の制限

- ソースを非公開・除外にする前に、その内容を材料にして作られた別の出来事（要約に写り
  込んだもの）は追いません。作られた時点では正当な情報だったため、AIの要約の中身を後
  から機械的に判定する手段が無いことによる制限です。
- 送信済みのメールは取り消せません。後からソースを非公開・除外にしても、すでに送った
  メールの内容は取り消せません。
- 出来事が数百件に積み上がったときの対策（決着済み・置き換え済みの出来事を一定期間後に
  まとめて圧縮し、図と次の判断にはそれだけを渡す）は未実装です。
- 表（スプレッドシート）の細かい修正が、ノイズとして出来事に溜まる可能性があります。

## 開発メンバー向けのブランチ運用

`main`へ直接pushせず、最新の`main`から機能ブランチを作り、Pull Requestで統合します。

```bash
git switch main
git pull
git switch -c feature/short-description
```
