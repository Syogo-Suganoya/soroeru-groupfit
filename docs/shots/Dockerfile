# 紹介ページと README に載せる画面の写しを撮るためだけのコンテナ。
# 実行中の api にブラウザで繋ぎ、利用者と同じ順に操作して撮る。
# api イメージには入れない（撮影のときしか要らないため）。
FROM zenika/alpine-chrome:with-puppeteer

USER root
# 日本語のフォントが無いと全部豆腐になる。
# chromium-lang が無いと --lang=ja-JP が効かず、日付の入力欄が 10/22/2026 の米国式で写る。
RUN apk add --no-cache font-noto-cjk chromium-lang
