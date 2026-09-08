# 開発ガイド

ソロエルの開発手順と実装上の約束ごと。プロダクトの概要は [README.md](README.md)、
仕様の背景は [設計書.md](設計書.md)、Cloud Run へのデプロイは [DEPLOY.md](DEPLOY.md) を参照。

## 前提

**Python はコンテナで実行する。** ホストに venv を作らない。
ローカル・CI・本番が同一の Dockerfile を参照することで、環境差異による不具合を構造的に排除する
（設計書 §8）。

## 起動

```bash
docker compose up
```

api と Firestore エミュレータが立ち上がる。http://localhost:8080 が紹介ページ、
`/app` がルーム操作の画面、`/docs` で OpenAPI。

**データ管理は Firestore が既定。** 開発時もエミュレータを使い、本番と同じ経路で動かす。
接続先は `FIRESTORE_EMULATOR_HOST` の有無だけで決まり、アプリのコードは同じ。

他プロジェクトとポートがぶつかるときはホスト側だけ差し替える:

```bash
HOST_PORT=8083 FIRESTORE_HOST_PORT=8220 PUBLIC_BASE_URL=http://localhost:8083 docker compose up
```

`PUBLIC_BASE_URL` は招待URLと集合プレビューのURL生成に使うため、ポートを変えたら合わせて指定する。

エミュレータのデータはコンテナを消すと失われる。作り直すには:

```bash
docker compose down && docker compose up
```

## テスト

```bash
docker compose run --rm api python -m pytest -q
```

`tests/test_firestore_repository.py` はエミュレータ相手の統合テストで、`--no-deps` を付けると
接続できずスキップされる。ドメインとAPIのテストは memory ドライバで完結するため、
エミュレータなしでも走る。

## 外部APIの切り替え

外部APIキーが無くても全機能が動く。既定はすべて mock:

| 環境変数 | 既定 | 取りうる値 |
|---|---|---|
| `GEMINI_MODE` | `mock` | `mock` / `live` |
| `YOUCAM_MODE` | `mock` | `mock` / `live` |
| `NOTIFY_CHANNEL` | `in_app` | `in_app` / `line` |
| `DB_DRIVER` | `firestore` | `firestore` / `memory`（memory はテスト用） |
| `STORAGE_DRIVER` | `local` | `local` / `gcs` |

実キーを使うときは `.env.example` を `.env` にコピーして値を入れ、対応するモードを切り替える。
`.env` はコミットしない。本番の秘密情報は Secret Manager から Cloud Run に注入し、イメージには焼き込まない。

## アーキテクチャ図の再生成

図は [`docs/architecture.py`](docs/architecture.py)（mingrammer/diagrams）から生成する。
構成を変えたら再生成する:

```bash
docker compose --profile diagram run --rm diagram
```

graphviz と日本語フォントは生成専用イメージ（`docs/Dockerfile`）に閉じてあり、api イメージには入れない。

## ディレクトリ構成

| パス | 役割 |
|---|---|
| `app/domain/` | 判定ロジック（色差 CIEDE2000・ドレスコード・調和）。LLM 非依存の純関数 |
| `app/agents/` | ADK 相当のエージェント群。`orchestrator.py` がルーム状態駆動で進行を決める |
| `app/ports/` | 外部API・永続化の抽象。mock / live をここだけで差し替える |
| `app/rendering/` | 試着カード・集合プレビューの描画（Pillow） |
| `app/main.py` | API Gateway。薄く保ち、進行の判断は Orchestrator に委ねる |
| `web/` | 紹介ページ（`index.html`）とアプリ本体（`app.html`）。どちらも単一 HTML |
| `docs/` | アーキテクチャ図とその生成環境 |

## 実装の約束ごと

**永続化は Firestore を前提に書く。** ルームは1ドキュメントに丸ごと入れる（設計書 §6）。
画像は Cloud Storage に置いて参照だけを持たせ、ドキュメントの 1MiB 上限に触れないようにする。
`memory` ドライバはテスト用で、本番と同じ経路を通す責任は
`tests/test_firestore_repository.py` が負う。ルームのモデルを変えたら、
このテストの往復（保存 → 読み戻し）で型が落ちないことを確認する。

**外部サービスは必ず `app/ports/` 経由で呼ぶ。** エージェントやドメインから SDK を直接叩かない。
mock / live の分岐は `app/deps.py` の1箇所だけに置く。呼び出し側は実装の違いを知らない。

**判定の数値はドメインの純関数で決める。** LLM には言語化と自由記述の解釈だけをさせる。
これは Gemini が落ちても警告が出続けるようにするため、かつ判定根拠（設計書 §7-3）を再現可能にするため。
警告を追加するときは `evidence` に必ず根拠の数値を入れる。根拠のない警告は作らない。

**ルーム進行は `Orchestrator.advance()` に集約する。** API 層はイベントを渡すだけにする。
`advance()` は状態が動くたびに検知をやり直すので、通知など副作用のある処理は**再実行に耐える**ように書く
（通知は `dedupe_key` で未読の重複を弾いている）。

**同意・削除は監査ログを残す。** アクションを増やすときは `AuditAction` に追加する。
監査ログはルーム削除後も残す。削除の証跡そのものが必要なため。

**合成してよいかの判定は `Member.composable` だけを見る。** 各所で条件を再実装しない。

**コメントには設計書の該当節を書く。** なぜその制約があるかは設計書側にあるため、
コードには「何を守っているか」への参照を残す。

## 集合プレビューの合成・記念ムービーを触るとき

どちらも `CompositorPort` / `VideoPort` の裏にあり、実装は Pillow で完結している（設計書 §12）。
会場ライティングは `figure.apply_lighting` の色調調整による近似で、物理的な再照明ではない。

**外部の生成モデルに差し替えるときも、誰をシルエットにするかの判断はローカル側に残す。**
渡してよいのは「ローカルで組み立てたベース画像」までで、未同意の人の顔を生成させる余地を作らない
（設計書 §7-1）。合成結果がどのエンジン由来かは `PreviewRevision.engine` / `Movie.engine` に残す。

**外部を呼ぶ実装を足すなら、失敗時はローカル合成にフォールバックする。**
品質は落ちるが体験は続く、という優先順位で書くこと。ローカル実装の振る舞いは
`tests/test_preview_features.py` が守っている。

## シーンプリセットを増やす

`app/domain/dresscode.py` の `PRESETS` に追加する。成人式・コスプレは枠だけあり、中身は MVP 外
（設計書 §9）。色かぶり・柄かぶり・浮きの判定はシーンに依存しないため、プリセットを足すだけで動く。
