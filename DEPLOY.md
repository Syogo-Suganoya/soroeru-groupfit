# デプロイ手順

ソロエルを Cloud Run にデプロイする手順。**CLI（gcloud）** と **コンソール画面** の2パターンを
それぞれ最初から最後まで書いてある。どちらか一方だけ読めばよい。
CI からの継続デプロイは「C. GitHub Actions」にあるが、**現在は無効にしてある**。

> **未検証**: この手順はまだ実際のGCPプロジェクトで通していない。
> コンソールの項目名は変わることがあるため、画面が異なる場合は同等の設定を探すこと。

## 前提

- GCP プロジェクトがあり、課金が有効になっていること
- 実行者に Cloud Run 管理者・Artifact Registry 管理者・Secret Manager 管理者・
  サービスアカウント管理者の権限があること
- ローカルにソースがあること（CLI パターンのみ）

以下、プロジェクトIDは `soroeru-groupfit`、リージョンは `asia-northeast1`（東京）を前提に書く。
別のIDを使う場合は読み替える。

## 構成するもの

| リソース | 用途 |
|---|---|
| Cloud Run サービス `soroeru-groupfit` | アプリ本体（api） |
| Artifact Registry | コンテナイメージの置き場 |
| Firestore（Native モード） | ルーム・同意状態 |
| Cloud Storage バケット | ルーム内一時画像 |
| Secret Manager | Gemini / YouCam のキー |
| サービスアカウント `soroeru-run` | Cloud Run の実行 ID |
| Cloud Scheduler ジョブ | TTL 到来ルームの掃引（設計書 §7-2） |

---

# A. CLI でデプロイする

## A-1. 変数を決める

```bash
export PROJECT_ID=soroeru-groupfit
export REGION=asia-northeast1
export SERVICE=soroeru-groupfit
export BUCKET=${PROJECT_ID}-images
export SA=soroeru-run
```

```bash
gcloud config set project ${PROJECT_ID}
```

## A-2. API を有効化する

```bash
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com firestore.googleapis.com storage.googleapis.com secretmanager.googleapis.com cloudscheduler.googleapis.com aiplatform.googleapis.com
```

## A-3. Firestore とバケットを作る

```bash
gcloud firestore databases create --location=${REGION}
```

```bash
gcloud storage buckets create gs://${BUCKET} --location=${REGION} --uniform-bucket-level-access
```

ルーム内画像はイベント後に削除される前提だが、消し漏れの保険としてライフサイクルを入れておくとよい:

```bash
echo '{"rule":[{"action":{"type":"Delete"},"condition":{"age":30}}]}' > /tmp/lifecycle.json && gcloud storage buckets update gs://${BUCKET} --lifecycle-file=/tmp/lifecycle.json
```

## A-4. シークレットを登録する

実キーをコマンド履歴に残さないよう、ファイル経由かプロンプト入力で渡す:

```bash
printf 'あなたのGeminiキー' | gcloud secrets create gemini-api-key --data-file=-
```

```bash
printf 'あなたのYouCamキー' | gcloud secrets create youcam-api-key --data-file=-
```

```bash
printf 'あなたのYouCamシークレット' | gcloud secrets create youcam-secret-key --data-file=-
```

mock のまま動かすなら、この手順と A-7 のシークレット指定は飛ばしてよい。

## A-5. サービスアカウントを作って権限を付ける

```bash
gcloud iam service-accounts create ${SA} --display-name="Soroeru Cloud Run"
```

```bash
export SA_EMAIL=${SA}@${PROJECT_ID}.iam.gserviceaccount.com
```

必要な権限だけを付ける（Firestore 読み書き・バケット内オブジェクト操作・監査ログ書き込み・シークレット参照）:

```bash
gcloud projects add-iam-policy-binding ${PROJECT_ID} --member="serviceAccount:${SA_EMAIL}" --role="roles/datastore.user"
```

```bash
gcloud storage buckets add-iam-policy-binding gs://${BUCKET} --member="serviceAccount:${SA_EMAIL}" --role="roles/storage.objectAdmin"
```

```bash
gcloud projects add-iam-policy-binding ${PROJECT_ID} --member="serviceAccount:${SA_EMAIL}" --role="roles/logging.logWriter"
```

```bash
gcloud secrets add-iam-policy-binding gemini-api-key --member="serviceAccount:${SA_EMAIL}" --role="roles/secretmanager.secretAccessor"
```

YouCam のシークレットにも同じ `add-iam-policy-binding` を実行する。

## A-6. デプロイする

リポジトリのルート（`compose.yaml` がある階層）で実行する。`--source .` を使うと
Cloud Build がリポジトリの `Dockerfile` を読んでビルドし、Artifact Registry に push まで行う。

```bash
gcloud run deploy ${SERVICE} --source . --region ${REGION} --service-account ${SA_EMAIL} --allow-unauthenticated --set-env-vars "APP_ENV=prod,DB_DRIVER=firestore,STORAGE_DRIVER=gcs,GCS_BUCKET=${BUCKET},GOOGLE_CLOUD_PROJECT=${PROJECT_ID},NOTIFY_CHANNEL=in_app,GEMINI_MODE=mock,YOUCAM_MODE=mock,GEMINI_MODEL=gemini-3.7-flash"
```

`--allow-unauthenticated` は誰でもURLを開ける状態にする。招待URLを配る性質上デモではこれでよいが、
ルームIDを知っていれば誰でも見られる点は理解した上で使う。

## A-7. URL を設定に反映する（2回目のデプロイ）

`PUBLIC_BASE_URL` は招待URLと集合プレビューのURL生成に使う。URL はデプロイ後に確定するため、
取得してから環境変数を更新する:

```bash
export URL=$(gcloud run services describe ${SERVICE} --region ${REGION} --format='value(status.url)') && echo ${URL}
```

```bash
gcloud run services update ${SERVICE} --region ${REGION} --update-env-vars "PUBLIC_BASE_URL=${URL}"
```

実キーを使う場合は、あわせてシークレットを紐づけてモードを live にする:

```bash
gcloud run services update ${SERVICE} --region ${REGION} --update-env-vars "GEMINI_MODE=live,YOUCAM_MODE=live" --update-secrets "GEMINI_API_KEY=gemini-api-key:latest,YOUCAM_API_KEY=youcam-api-key:latest,YOUCAM_SECRET_KEY=youcam-secret-key:latest"
```

集合プレビューの合成と記念ムービーはコンテナ内で描画する（設計書 §12）。
処理に時間とメモリを使うため、タイムアウトとメモリを上げておく:

```bash
gcloud run services update ${SERVICE} --region ${REGION} --timeout 300 --memory 1Gi
```

## A-8. TTL 掃引をスケジュールする

イベント日+7日を過ぎたルームを毎日削除する（設計書 §7-2）。Cloud Scheduler から
OIDC 認証つきで `/api/maintenance/sweep` を叩く。

```bash
gcloud iam service-accounts create soroeru-scheduler --display-name="Soroeru Scheduler"
```

```bash
export SCHED_SA=soroeru-scheduler@${PROJECT_ID}.iam.gserviceaccount.com
```

```bash
gcloud run services add-iam-policy-binding ${SERVICE} --region ${REGION} --member="serviceAccount:${SCHED_SA}" --role="roles/run.invoker"
```

```bash
gcloud scheduler jobs create http soroeru-ttl-sweep --location ${REGION} --schedule "0 3 * * *" --time-zone "Asia/Tokyo" --uri "${URL}/api/maintenance/sweep" --http-method POST --oidc-service-account-email ${SCHED_SA} --oidc-token-audience "${URL}"
```

## A-9. 動作確認

```bash
curl -s ${URL}/healthz
```

`{"status":"ok","modes":{...}}` が返り、`db` が `firestore`、`storage` が `gcs` になっていればよい。
ブラウザで `${URL}` を開き、ルーム作成 → 参加 → 衣装確定 → 集合プレビュー表示まで通ることを確認する。

---

# B. コンソール画面でデプロイする

画面から行う場合、ソースからの直接ビルドよりも、先にイメージを作ってからデプロイする方が迷いにくい。
ここではリポジトリを Cloud Build に読ませる流れで書く。

## B-1. API を有効化する

1. コンソール左上の**プロジェクト選択**で `soroeru-groupfit` を選ぶ
2. 検索窓に「API とサービス」→ **API とサービスの有効化**
3. 次を1つずつ検索して**有効にする**を押す
   - Cloud Run Admin API
   - Cloud Build API
   - Artifact Registry API
   - Cloud Firestore API
   - Cloud Storage API
   - Secret Manager API
   - Cloud Scheduler API

## B-2. Firestore を作る

1. 検索窓に「Firestore」→ **データベースを作成**
2. **Native モード**を選ぶ
3. ロケーションに `asia-northeast1` を選ぶ
4. **作成**

## B-3. バケットを作る

1. 検索窓に「Cloud Storage」→ **バケットを作成**
2. 名前 `soroeru-groupfit-images`（バケット名は全世界で一意である必要があるため、
   取られていたら末尾に数字を足す）
3. ロケーションタイプ **Region** → `asia-northeast1`
4. アクセス制御で**均一**を選ぶ
5. **作成**
6. 作成後、**ライフサイクル**タブで「30日経過したオブジェクトを削除」のルールを足しておくと消し漏れの保険になる

## B-4. シークレットを登録する

mock のまま動かすなら飛ばしてよい。

1. 検索窓に「Secret Manager」→ **シークレットを作成**
2. 名前 `gemini-api-key`、シークレットの値に実キーを貼る → **シークレットを作成**
3. 同様に `youcam-api-key`、`youcam-secret-key` を作る

## B-5. サービスアカウントを作る

1. 検索窓に「サービス アカウント」→ **サービス アカウントを作成**
2. 名前 `soroeru-run` → **作成して続行**
3. ロールに次を追加する
   - Cloud Datastore ユーザー
   - ログ書き込み
4. **完了**

バケットへの権限は個別に付ける:

5. Cloud Storage で作成したバケットを開き、**権限**タブ → **アクセスを許可**
6. プリンシパルに `soroeru-run@soroeru-groupfit.iam.gserviceaccount.com`、ロールに
   **Storage オブジェクト管理者** → **保存**

シークレットを使う場合:

7. Secret Manager で各シークレットを開き、**権限**タブ → **アクセスを許可**
8. 同じサービスアカウントに **Secret Manager のシークレット アクセサー** を付与

## B-6. デプロイする

1. 検索窓に「Cloud Run」→ **サービスを作成**
2. **リポジトリから継続的にデプロイする**を選び、**Cloud Build を設定**
   - GitHub 等のリポジトリを接続し、ブランチを選ぶ
   - ビルドタイプに **Dockerfile** を選び、パスを `/Dockerfile` にする
   - ソースが GitHub にない場合は、代わりにローカルから
     `gcloud run deploy --source .`（A-6）を使うのが早い
3. サービス名 `soroeru-groupfit`、リージョン `asia-northeast1`
4. **認証**で「未認証の呼び出しを許可」を選ぶ（招待URLを配るため）
5. **コンテナ、ボリューム、ネットワーキング、セキュリティ**を開く
   - **セキュリティ**タブ → サービス アカウントに `soroeru-run` を選ぶ
   - **変数とシークレット**タブ → 環境変数に次を追加

     | 名前 | 値 |
     |---|---|
     | `APP_ENV` | `prod` |
     | `DB_DRIVER` | `firestore` |
     | `STORAGE_DRIVER` | `gcs` |
     | `GCS_BUCKET` | `soroeru-groupfit-images`（作成したバケット名） |
     | `GOOGLE_CLOUD_PROJECT` | `soroeru-groupfit` |
     | `NOTIFY_CHANNEL` | `in_app` |
     | `GEMINI_MODE` | `mock`（実キーを使うなら `live`） |
     | `YOUCAM_MODE` | `mock`（同上） |
     | `GEMINI_MODEL` | `gemini-3.7-flash` |

   - 実キーを使う場合は同じタブの**シークレットを参照**から
     `GEMINI_API_KEY` ← `gemini-api-key`、`YOUCAM_API_KEY` ← `youcam-api-key`、
     `YOUCAM_SECRET_KEY` ← `youcam-secret-key` を割り当てる（バージョンは `latest`）
   - **コンテナ**タブ → 集合プレビューと記念ムービーの生成に時間がかかるため、
     リクエストのタイムアウトを `300` 秒、メモリを `1 GiB` にする
6. **作成**

## B-7. URL を設定に反映する

デプロイ完了後、サービス詳細の上部に `https://soroeru-groupfit-xxxxx-an.a.run.app` の形式で URL が出る。

1. **新しいリビジョンの編集とデプロイ**を押す
2. **変数とシークレット**タブで環境変数 `PUBLIC_BASE_URL` に上記URLを追加
3. **デプロイ**

この設定は招待URLと集合プレビューのURL生成に使うため、入れ忘れるとリンクが `localhost` のままになる。

## B-8. TTL 掃引をスケジュールする

1. 検索窓に「サービス アカウント」→ `soroeru-scheduler` を作成（ロールなしでよい）
2. Cloud Run のサービス一覧で `soroeru-groupfit` のチェックボックスを選び、右の**権限**パネル →
   **プリンシパルを追加**
3. `soroeru-scheduler@...` に **Cloud Run 起動元** を付与
4. 検索窓に「Cloud Scheduler」→ **ジョブを作成**
   - 名前 `soroeru-ttl-sweep`、リージョン `asia-northeast1`
   - 頻度 `0 3 * * *`、タイムゾーン `日本標準時`
   - ターゲットタイプ **HTTP**、URL に `<サービスURL>/api/maintenance/sweep`
   - HTTP メソッド **POST**
   - **認証ヘッダー**で「OIDC トークンを追加」を選び、サービス アカウントに
     `soroeru-scheduler`、対象（audience）にサービスURLを入れる
5. **作成**

## B-9. 動作確認

ブラウザで `<サービスURL>/healthz` を開き、`db` が `firestore`、`storage` が `gcs` に
なっていることを確認する。続けてトップページでルーム作成 → 参加 → 衣装確定 →
集合プレビュー表示まで通ることを見る。

---

# C. GitHub Actions で自動デプロイする（現在オフ）

ワークフローは [`.github/workflows/soroeru-groupfit-cd.yml`](../../.github/workflows/soroeru-groupfit-cd.yml)
（リポジトリルート基準）にある。**今は無効にしてある。**

- トリガーは `workflow_dispatch`（手動）のみ。`push` はコメントアウト済み
- 手動実行時も、リポジトリ変数 `CD_ENABLED` が `"true"` で、かつ実行時に
  `deploy` を `true` にしないとデプロイまで進まない（既定はテストのみ）

二重にしてあるのは、コメントを戻し忘れても実デプロイが走らないようにするため。
テストは常に走るので、CD をオフのままCIとしても使える。

## ワークフローの中身

1. **test**: `docker compose` で Firestore エミュレータを立て、コンテナ内で pytest。
   ローカルと同じ compose を使うので、CI 専用の環境定義を持たない
2. **deploy**: Workload Identity Federation で認証 → `gcloud run deploy --source .`
   → 確定したURLを `PUBLIC_BASE_URL` に反映 → `/healthz` で `db`/`storage` を検証

テストが落ちればデプロイは走らない（`needs: test`）。

## 有効化する手順

### C-1. デプロイ用サービスアカウントを作る

Cloud Run の実行IDとは別に、CI からデプロイするためのIDを用意する:

```bash
gcloud iam service-accounts create soroeru-deployer --display-name="Soroeru GitHub Actions"
```

```bash
export DEPLOYER=soroeru-deployer@${PROJECT_ID}.iam.gserviceaccount.com
```

```bash
for ROLE in roles/run.admin roles/cloudbuild.builds.editor roles/artifactregistry.writer roles/storage.admin roles/iam.serviceAccountUser; do gcloud projects add-iam-policy-binding ${PROJECT_ID} --member="serviceAccount:${DEPLOYER}" --role="${ROLE}"; done
```

`iam.serviceAccountUser` は、デプロイ時に Cloud Run へ `soroeru-run` を割り当てるために要る。

### C-2. Workload Identity Federation を設定する

サービスアカウントキーJSONを GitHub に置かずに認証するための設定。

```bash
gcloud iam workload-identity-pools create github --location=global --display-name="GitHub Actions"
```

```bash
gcloud iam workload-identity-pools providers create-oidc github-provider --location=global --workload-identity-pool=github --issuer-uri="https://token.actions.githubusercontent.com" --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" --attribute-condition="assertion.repository=='Syogo-Suganoya/gemini-ops-orchestrator'"
```

`attribute-condition` で自分のリポジトリからの要求だけに絞る。これを省くと
他のリポジトリからも認証できてしまうため、必ず入れる。

```bash
export PROJECT_NUMBER=$(gcloud projects describe ${PROJECT_ID} --format='value(projectNumber)')
```

```bash
gcloud iam service-accounts add-iam-policy-binding ${DEPLOYER} --role="roles/iam.workloadIdentityUser" --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github/attribute.repository/Syogo-Suganoya/gemini-ops-orchestrator"
```

プロバイダのリソース名を控える（次の手順で使う）:

```bash
echo "projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github/providers/github-provider"
```

### C-3. GitHub 側に登録する

リポジトリの **Settings → Secrets and variables → Actions → Variables** で登録する。
WIF はキーレスなので秘密情報ではなく、Variables でよい（同リポジトリの他プロジェクトと共通）。

| 名前 | 値 |
|---|---|
| `WIF_PROVIDER` | C-2 最後に表示されたリソース名 |
| `DEPLOY_SERVICE_ACCOUNT` | `soroeru-deployer@soroeru-groupfit.iam.gserviceaccount.com` |
| `CD_ENABLED` | `true` |

`WIF_PROVIDER` と `DEPLOY_SERVICE_ACCOUNT` はリポジトリ全体で共有される。
プロジェクトごとに別のGCPプロジェクトへ出し分けたい場合は、
GitHub の Environments を切って環境変数として持たせる。

### C-4. 自動デプロイを解禁する

`CD_ENABLED=true` を入れた時点で、手動実行（Actions タブの **Run workflow** で
`deploy` を `true` にする）でデプロイできる。push でも動かしたい場合のみ、
`.github/workflows/soroeru-groupfit-cd.yml` の `push:` ブロックのコメントを外す:

```yaml
on:
  workflow_dispatch:
    inputs:
      deploy:
        description: "デプロイまで実行する（false ならテストのみ）"
        type: boolean
        default: false

  push:
    branches: [main]
    paths:
      - "gcp_hack/soroeru-groupfit/**"
      - ".github/workflows/soroeru-groupfit-cd.yml"
```

`paths` を絞ってあるので、同じリポジトリの他プロジェクトを触ってもソロエルはデプロイされない。

## また止めたくなったら

`CD_ENABLED` を `false` にする（またはVariablesから削除する）。
ワークフローのファイルを消さなくても、それだけで止まる。

---

# 運用メモ

## ログを見る

```bash
gcloud run services logs read soroeru-groupfit --region ${REGION} --limit 50
```

画面の場合は Cloud Run のサービス詳細 → **ログ**タブ。
同意の取得・撤回・削除の証跡は監査ログとしてアプリ側にも残り、`/api/rooms/{id}/audit` で読める
（設計書 §7-4）。

## 更新デプロイ

CLI なら A-6 と同じコマンドを再実行するだけ。GitHub Actions を有効にしている場合は、
`gcp_hack/soroeru-groupfit/**` への push で自動的に新しいリビジョンが作られる。

## 片付ける

デモ後に課金を止めるには、リソースを消すのが確実:

```bash
gcloud run services delete ${SERVICE} --region ${REGION}
```

```bash
gcloud scheduler jobs delete soroeru-ttl-sweep --location ${REGION}
```

```bash
gcloud storage rm -r gs://${BUCKET}
```

Firestore のデータはコンソールの Firestore → **データ**から削除する。
プロジェクトごと消してよいなら `gcloud projects delete ${PROJECT_ID}` が最も確実。

## よくあるつまずき

| 症状 | 原因と対処 |
|---|---|
| 招待URLが `localhost` になる | `PUBLIC_BASE_URL` が未設定。A-7 / B-7 を実施する |
| プレビュー画像が 404 | バケットへの権限不足。サービスアカウントに Storage オブジェクト管理者があるか確認 |
| 起動直後に落ちる | `GOOGLE_CLOUD_PROJECT` の未設定、または Firestore 未作成 |
| Scheduler が 403 | Cloud Run 起動元ロールの付与漏れ、または OIDC の audience がサービスURLと不一致 |
| 日本語が豆腐になる | 図の生成イメージ側の問題。アプリの Dockerfile には日本語フォントが入っている |
