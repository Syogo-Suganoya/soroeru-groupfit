# 開発ガイド

ソロエルの開発手順と実装上の約束ごとをまとめています。

## 起動

```bash
docker compose up
```

api と Firestore エミュレータが立ち上がります。http://localhost:8080 が紹介ページ、
`/app` がルーム操作の画面、`/docs` が OpenAPI です。

**データ管理は Firestore が既定です。** 開発時もエミュレータを使い、本番と同じ経路で動かします。
接続先は `FIRESTORE_EMULATOR_HOST` の有無だけで決まり、アプリのコードは変わりません。

他のプロジェクトとポートがぶつかるときは、ホスト側だけ差し替えます:

```bash
HOST_PORT=8083 FIRESTORE_HOST_PORT=8220 PUBLIC_BASE_URL=http://localhost:8083 docker compose up
```

`PUBLIC_BASE_URL` は招待URLと集合プレビューのURL生成に使うため、ポートを変えたら合わせて指定してください。

エミュレータのデータはコンテナを消すと失われます。作り直すには次のようにします:

```bash
docker compose down && docker compose up
```

## テスト

```bash
docker compose run --rm api python -m pytest -q
```

`tests/test_firestore_repository.py` はエミュレータを相手にする統合テストで、`--no-deps` を付けると
接続できずにスキップされます。ドメインと API のテストは memory ドライバで完結するため、
エミュレータがなくても走ります。

## 外部APIの切り替え

外部APIのキーがなくても、すべての機能が動きます。外部APIは Gemini だけで、既定は mock です:

| 環境変数 | 既定 | 説明 |
|---|---|---|
| `GEMINI_MODE` | `mock` | `live` にすると Gemini の実APIを使います（ドレスコードの読み取りと総評） |
| `GEMINI_API_KEY` | （空） | `live` のときに必要です |
| `GEMINI_MODEL` | `gemini-3.7-flash` | 使うモデル |

実キーを使うときは `.env.example` を `.env` にコピーして値を入れ、`GEMINI_MODE=live` にします。

次の3つは本番とテストのための切り替えです。ローカル開発では既定のまま使ってください:

| 環境変数 | 既定 | 既定以外の値 |
|---|---|---|
| `DB_DRIVER` | `firestore` | `memory` はテスト専用です（`tests/conftest.py` が固定します） |
| `STORAGE_DRIVER` | `local` | `gcs` は本番用です。`GCS_BUCKET` と GCP の認証が要ります |
| `NOTIFY_CHANNEL` | `in_app` | `line` は未完成です。宛先にソロエル内部の uid を渡しているため、LINE には届きません |

判定のしきい値と自動削除の日数も環境変数で変えられます（`COLOR_CLASH_DELTA_E`＝12、
`FORMALITY_GAP_THRESHOLD`＝2、`TTL_DAYS_AFTER_EVENT`＝7）。一覧と既定値は `app/config.py` にあります。

**テストは `.env` の影響を受けません。** `tests/conftest.py` の `build_settings()` がモードを mock に固定しています。
ここを迂回して `Settings()` を直接作らないでください。手元で `GEMINI_MODE=live` にしている人だけ
テストが実キーを要求する、という状態になってしまいます。

## 個人試着を触るとき

`TryOnPort` の裏にあるのは `MockTryOnPort` だけで、衣装の色・柄からイラストを描いています。
**アプリは顔写真を受け取りません。** 実写の試着エンジンを足すときは、次の2つを守ってください:

- 外部に送ってよいのは本人の分だけです。集合プレビューは他人が写るため送りません
- 外部が落ちてもルームの進行は止めません。mock の描画に落として、どちらで作ったかを
  `TryOnResult.engine` に残します

パーソナルカラーは `MockTryOnPort._tone_of` が uid から決定的に割り当て、
`figure.tone_match_score` で衣装色との適合度を出しています。あくまで提示順の並び替えに使うもので、
「似合う / 似合わない」を断定するものではありません。

## 画面の写しとアイコンの作り直し

紹介ページと README の挿絵（`web/shots/*.png`）は、[`docs/shots.js`](docs/shots.js) が
実際の画面を操作して撮っています。画面を変えたら撮り直してください:

```bash
GEMINI_MODE=mock docker compose --profile shots run --rm shots
```

「友人4人でお呼ばれ」の場面を API で組み立ててから、各メンバーの画面を開いて撮ります。
毎回新しいルームを作るので、エミュレータを空にしなくても同じ絵になります。
`GEMINI_MODE=mock` を付けるのは、総評文を毎回同じにするためです（live だと撮るたびに変わります）。

アイコンの形は `web/favicon.svg` だけに置き、PNG（32px・180px・192px・512px）はそこから書き出します。
SVG を変えたら書き出し直してください:

```bash
docker compose --profile shots run --rm --no-deps shots docs/icons.js
```

ブラウザと日本語フォントは撮影専用のイメージ（`docs/shots/Dockerfile`）に閉じてあり、api のイメージには入れていません。

## アーキテクチャ図の再生成

図は [`docs/architecture.py`](docs/architecture.py)（mingrammer/diagrams）から生成しています。
構成を変えたら再生成してください:

```bash
docker compose --profile diagram run --rm diagram
```

graphviz と日本語フォントは生成専用のイメージ（`docs/Dockerfile`）に閉じてあり、api のイメージには入れていません。

## ディレクトリ構成

| パス | 役割 |
|---|---|
| `app/domain/` | 判定ロジック（色差 CIEDE2000・ドレスコード・調和）。LLM に依存しない純関数です |
| `app/agents/` | ADK 相当のエージェント群。`orchestrator.py` がルームの状態から進行を決めます |
| `app/ports/` | 外部API・永続化の抽象。mock / live はここだけで差し替えます |
| `app/rendering/` | 試着カード・集合プレビューの描画（Pillow） |
| `app/main.py` | API Gateway。薄く保ち、進行の判断は Orchestrator に委ねます |
| `web/` | 紹介ページ（`index.html`）とアプリ本体（`app.html`）、アイコン、画面の写し（`shots/`） |
| `docs/` | アーキテクチャ図・画面の写し・アイコンの生成環境 |

## 実装の約束ごと

**永続化は Firestore を前提に書きます。** ルームは1ドキュメントに丸ごと入れます。
画像は Cloud Storage に置いて参照だけを持たせ、ドキュメントの 1MiB の上限に触れないようにしています。
`memory` ドライバはテスト用で、本番と同じ経路を通す責任は
`tests/test_firestore_repository.py` が負っています。ルームのモデルを変えたら、
このテストの往復（保存 → 読み戻し）で型が落ちないことを確認してください。

**外部サービスは必ず `app/ports/` を経由して呼びます。** エージェントやドメインから SDK を直接呼ばないでください。
mock / live の分岐は `app/deps.py` の1箇所だけに置きます。呼び出し側は実装の違いを知りません。

**判定の数値はドメインの純関数で決めます。** LLM（Gemini）に任せるのは次の2つだけです。
Gemini が落ちても警告が出続けるようにするため、また判定根拠を再現できるようにするためです。

- **ドレスコードの読み取り**: ルーム作成時に1回だけ呼び、結果を `EventInfo.dress_rules` に保存します。
  判定はこの保存値を使うので、同じルームで日によって NG が変わることはありません。読み取り結果は
  シーンの基本の NG に**足すだけ**で（`effective_preset`）、基本の NG を外すことはできません
- **総評**: 判定の結果を全員向けの短い文章にします。`advance()` は同意や試着でも走るため、
  総評の入力（確定衣装と指摘）が変わったときだけ作り直します（`HarmonyReport.explanation_key`）。
  Gemini が落ちて自動の文に替わった回は、次の機会に作り直します

警告を追加するときは、`evidence` に必ず根拠の数値を入れてください。根拠のない警告は作りません。

**ルームの進行は `Orchestrator.advance()` に集約します。** API 層はイベントを渡すだけにします。
`advance()` は状態が動くたびに検知をやり直すので、通知など副作用のある処理は**再実行に耐える**ように書いてください
（通知は `dedupe_key` で未読の重複を弾いています）。

**同意・削除は監査ログに残します。** アクションを増やすときは `AuditAction` に追加してください。
監査ログはルームを削除したあとも残します。削除の証跡そのものが必要なためです。

**合成してよいかの判定は `Member.composable` だけを見ます。** 各所で条件を書き直さないでください。

**コメントには「何を守っているか」を書きます。** 同意・削除・根拠の提示に関わる箇所は、
その制約がなぜあるのかをコメントだけで読み取れるようにしてください。

## 集合プレビューの合成を触るとき

`CompositorPort` の裏にあり、実装は Pillow で完結しています。

**外部の生成モデルに差し替えるときも、誰をシルエットにするかの判断はローカル側に残します。**
渡してよいのは「ローカルで組み立てたベース画像」までで、未同意の人の顔を生成させる余地を作りません。
合成結果がどのエンジンで作られたかは `PreviewRevision.engine` に残します。

**外部を呼ぶ実装を足すなら、失敗時はローカルの合成にフォールバックしてください。**
品質は落ちても体験は続く、という優先順位で書きます。ローカル実装の振る舞いは
`tests/test_preview_features.py` が守っています。

## シーンプリセットを増やす

`app/domain/dresscode.py` の `PRESETS` に追加します。成人式・コスプレは枠だけあり、中身は MVP 外です。
色かぶり・柄かぶり・浮きの判定はシーンに依存しないため、プリセットを足すだけで動きます。
