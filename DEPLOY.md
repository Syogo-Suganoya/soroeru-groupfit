# デプロイ手順

ソロエルを Cloud Run にデプロイする手順。**CLI（gcloud）** と **コンソール画面** の2パターンを
それぞれ最初から最後まで書いてある。どちらか一方だけ読めばよい。
CI からの継続デプロイは「C. GitHub Actions」にある（**有効**。main への push でデプロイされる）。

> **未検証**: この手順はまだ実際のGCPプロジェクトで通していない。
> コンソールの項目名は変わることがあるため、画面が異なる場合は同等の設定を探すこと。

## 前提

- GCP プロジェクトがあり、**請求先アカウントがリンクされている**こと。
  ここが未設定だと、API の有効化やバケット作成が
  `HTTPError 403: The billing account for the owning project is disabled in state absent`
  で落ちる（→ [課金が有効か確かめる](#a-0-課金が有効か確かめる)）
- `gcloud` がプロジェクトのオーナー権限を持つアカウントで認証されていること
  （`gcloud auth list` の `ACTIVE` 行を確認する）
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

## A-0. 課金が有効か確かめる

**ここを飛ばすと以降が全部 403 で落ちる。** まず現状を見る:

```bash
gcloud billing projects describe ${PROJECT_ID}
```

`billingEnabled: true` かつ `billingAccountName` が入っていれば次へ進んでよい。
`billingEnabled: false` や `billingAccountName: ''` のときは、使える請求先アカウントを探す:

```bash
gcloud billing accounts list
```

`OPEN: True` の行の `ACCOUNT_ID`（`XXXXXX-YYYYYY-ZZZZZZ` 形式）をプロジェクトに紐づける:

```bash
gcloud billing projects link ${PROJECT_ID} --billing-account=XXXXXX-YYYYYY-ZZZZZZ
```

一覧が空の場合は請求先アカウント自体が無い。コンソールの
[お支払い](https://console.cloud.google.com/billing) から作る（クレジットカードの登録が要る）。
アカウントはあるのに見えない場合は、`gcloud auth list` で認証中のアカウントが
そのアカウントの管理者かどうかを確かめ、違えば `gcloud config set account メールアドレス` で切り替える。

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

YouCam のキーは [API コンソール](https://yce.makeupar.com/api-console/en/api-keys/)で発行する
`sk-` で始まるもの。現行のAPIは Bearer 認証だけを使うため、
旧 S2S 方式の secret key は登録しなくてよい。

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

シークレットは**1つずつ**権限を付ける。付け忘れると A-6 のデプロイが
`Permission denied on secret: .../youcam-api-key/versions/latest` で失敗する。

```bash
gcloud secrets add-iam-policy-binding gemini-api-key --member="serviceAccount:${SA_EMAIL}" --role="roles/secretmanager.secretAccessor"
```

```bash
gcloud secrets add-iam-policy-binding youcam-api-key --member="serviceAccount:${SA_EMAIL}" --role="roles/secretmanager.secretAccessor"
```

付いているか確かめる（`soroeru-run@...` が2件とも出ること）:

```bash
for s in gemini-api-key youcam-api-key; do echo "== $s"; gcloud secrets get-iam-policy $s --format='value(bindings.members)'; done
```

## A-6. デプロイする

リポジトリのルート（`compose.yaml` がある階層）で実行する。`--source .` を使うと
Cloud Build がリポジトリの `Dockerfile` を読んでビルドし、Artifact Registry に push まで行う。

A-4 でシークレットを登録し、A-5 で `secretAccessor` を付けてあることが前提。
`--set-secrets` で実キーを環境変数に流し込む。

```bash
gcloud run deploy ${SERVICE} --source . --region ${REGION} --service-account ${SA_EMAIL} --allow-unauthenticated --set-env-vars "APP_ENV=prod,DB_DRIVER=firestore,STORAGE_DRIVER=gcs,GCS_BUCKET=${BUCKET},GOOGLE_CLOUD_PROJECT=${PROJECT_ID},NOTIFY_CHANNEL=in_app,GEMINI_MODE=live,YOUCAM_MODE=live,GEMINI_MODEL=gemini-3.7-flash" --set-secrets "GEMINI_API_KEY=gemini-api-key:latest,YOUCAM_API_KEY=youcam-api-key:latest"
```

`--allow-unauthenticated` は誰でもURLを開ける状態にする。招待URLを配る性質上デモではこれでよいが、
ルームIDを知っていれば誰でも見られる点は理解した上で使う。

**外部APIは障害時にローカル実装へ落ちる。** キーが失効していてもルームは進むが、
**黙って落ちる**ので、デプロイしたら実際に試着して結果を目で確認すること。

- **Gemini**: live で効くのは総評文とドレスコード解釈の自然さ。色かぶり・フォーマル度の
  判定はローカル計算のままで、Gemini が落ちても警告は出続ける（[app/ports/llm.py](app/ports/llm.py)）。
- **YouCam**: 本人が写真を登録した衣装だけ実APIで試着する。
  **既定のカタログには衣装の参考画像（`reference_image_url`）が入っていない**ため、
  live にしただけでは実試着は起きない。[app/domain/catalog.py](app/domain/catalog.py) に
  実物の衣装画像URLを入れること。

## A-7. URL を設定に反映する（2回目のデプロイ）

`PUBLIC_BASE_URL` は招待URLと集合プレビューのURL生成に使う。URL はデプロイ後に確定するため、
取得してから環境変数を更新する:

```bash
export URL=$(gcloud run services describe ${SERVICE} --region ${REGION} --format='value(status.url)') && echo ${URL}
```

```bash
gcloud run services update ${SERVICE} --region ${REGION} --update-env-vars "PUBLIC_BASE_URL=${URL}"
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

## B-0. 課金を有効にする

**ここを飛ばすと以降が全部エラーになる。**

1. コンソール左上の**プロジェクト選択**で `soroeru-groupfit` を選ぶ
2. 検索窓に「お支払い」→ 開いた画面に**このプロジェクトには請求先アカウントがありません**と
   出ていたら、**請求先アカウントをリンク**を押して既存のアカウントを選ぶ
3. 選べるアカウントが無ければ**請求先アカウントを管理** → **アカウントを作成**
   （クレジットカードの登録が要る）してから、改めてリンクする
4. 画面に請求先アカウント名が表示されれば完了

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

1. 検索窓に「Secret Manager」→ **シークレットを作成**
2. 名前 `gemini-api-key`、シークレットの値に実キーを貼る → **シークレットを作成**
3. 同様に `youcam-api-key` を作る（値は [API コンソール](https://yce.makeupar.com/api-console/en/api-keys/)
   で発行する `sk-` で始まるキー）

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

シークレットへの権限も個別に付ける。**`gemini-api-key` と `youcam-api-key` の両方**に
必要で、片方を忘れると B-6 のデプロイが「シークレットへのアクセスが拒否されました」で失敗する:

7. Secret Manager で `gemini-api-key` を開き、**権限**タブ → **アクセスを許可**
8. プリンシパルに `soroeru-run@soroeru-groupfit.iam.gserviceaccount.com`、ロールに
   **Secret Manager のシークレット アクセサー** → **保存**
9. `youcam-api-key` にも 7〜8 を繰り返す

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
     | `GEMINI_MODE` | `live` |
     | `YOUCAM_MODE` | `live` |
     | `GEMINI_MODEL` | `gemini-3.7-flash` |

   - 同じタブの**シークレットを参照**から `GEMINI_API_KEY` ← `gemini-api-key`、
     `YOUCAM_API_KEY` ← `youcam-api-key` を割り当てる（バージョンは `latest`）
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

# C. GitHub Actions で自動デプロイする（オン）

ワークフローは [`.github/workflows/soroeru-groupfit-cd.yml`](.github/workflows/soroeru-groupfit-cd.yml) にある。
**main への push で、テスト → デプロイまで自動で走る。**

- `*.md` / `docs/` / `_memo/` だけの変更ではデプロイしない
- テストが落ちればデプロイは走らない（`needs: test`）
- Actions タブから手動実行もできる（`deploy` を `false` にするとテストだけ）

## ワークフローの中身

1. **test**: `docker compose` で Firestore エミュレータを立て、コンテナ内で pytest。
   ローカルと同じ compose を使うので、CI 専用の環境定義を持たない
2. **deploy**: Workload Identity Federation で認証 → `gcloud run deploy --source .`
   → 確定したURLを `PUBLIC_BASE_URL` に反映 → `/healthz` で `db` / `storage` と
   `gemini` / `youcam` が live を向いているかまで検証

## 初回に必要な設定

A の手順（サービスアカウント・シークレット・バケット）が済んでいる前提。
以下は CI からデプロイするための追加設定で、一度やれば以降は不要。

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
gcloud iam workload-identity-pools providers create-oidc github-provider --location=global --workload-identity-pool=github --issuer-uri="https://token.actions.githubusercontent.com" --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" --attribute-condition="assertion.repository=='Syogo-Suganoya/soroeru-groupfit'"
```

`attribute-condition` で自分のリポジトリからの要求だけに絞る。これを省くと
他のリポジトリからも認証できてしまうため、必ず入れる。
**リポジトリ名は実際のリモートと一致させること。** 食い違っていると認証が
`Permission denied` で弾かれる（`git remote -v` で確認できる）。

```bash
export PROJECT_NUMBER=$(gcloud projects describe ${PROJECT_ID} --format='value(projectNumber)')
```

```bash
gcloud iam service-accounts add-iam-policy-binding ${DEPLOYER} --role="roles/iam.workloadIdentityUser" --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github/attribute.repository/Syogo-Suganoya/soroeru-groupfit"
```

プロバイダのリソース名を控える（次の手順で使う）:

```bash
echo "projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github/providers/github-provider"
```

#### 別のリポジトリ名で作ってしまった場合

作り直す必要はない。条件とバインドを貼り替えれば直る。

プロバイダの条件を更新する:

```bash
gcloud iam workload-identity-pools providers update-oidc github-provider --location=global --workload-identity-pool=github --attribute-condition="assertion.repository=='Syogo-Suganoya/soroeru-groupfit'"
```

古いリポジトリ向けのバインドを外す（`旧リポジトリ名` は実際に使った値に置き換える）:

```bash
gcloud iam service-accounts remove-iam-policy-binding ${DEPLOYER} --role="roles/iam.workloadIdentityUser" --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github/attribute.repository/Syogo-Suganoya/旧リポジトリ名"
```

そのうえで、上の `add-iam-policy-binding` を正しいリポジトリ名で実行する。
外し忘れると、消したはずのリポジトリからも認証できる状態が残るので必ず外す。

確認（`soroeru-groupfit` だけが出ること）:

```bash
gcloud iam workload-identity-pools providers describe github-provider --location=global --workload-identity-pool=github --format='value(attributeCondition)' && gcloud iam service-accounts get-iam-policy ${DEPLOYER} --format='value(bindings.members)'
```

### C-3. GitHub 側に登録する

リポジトリの **Settings → Secrets and variables → Actions → Variables** で登録する。
WIF はキーレスなので秘密情報ではなく、Variables でよい。

| 名前 | 値 |
|---|---|
| `WIF_PROVIDER` | C-2 最後に表示されたリソース名 |
| `DEPLOY_SERVICE_ACCOUNT` | `soroeru-deployer@soroeru-groupfit.iam.gserviceaccount.com` |

この2つが未登録だと、デプロイジョブが認証で失敗する。

### C-4. 動作を確かめる

main に push するか、Actions タブから **Run workflow** を実行する。
`/healthz` の検証まで通れば、ジョブのサマリーにデプロイ先URLが出る。

## 止めたくなったら

Actions タブ → **soroeru-groupfit CD** → 右上の **...** → **Disable workflow**。
ファイルを消さずに止められる。恒久的に止めるなら、ワークフローの `push:` ブロックを削除する。

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

main への push で自動的に新しいリビジョンが作られる（C）。
CI を通さず手元から出したいときは、A-6 と同じコマンドを再実行すればよい。

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
