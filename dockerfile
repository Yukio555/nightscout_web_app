# ベースイメージ
FROM python:3.11-slim

# 作業ディレクトリの設定
WORKDIR /app

# 依存関係ファイルのコピーとインストール
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# アプリケーションコードのコピー
# . は現在のディレクトリの全てのファイルを指します
COPY . .

# 環境変数の設定 (ログ出力用)
ENV PYTHONUNBUFFERED=1

# ポートの公開 (fry.ioは通常自動検出しますが明示しておくと良いです)
EXPOSE 8080

# アプリケーションの起動コマンド (Gunicornを使用)
# app:app は ファイル名:Flaskインスタンス名 を指します
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "app:app"]
