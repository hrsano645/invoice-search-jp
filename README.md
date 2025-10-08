# Invoice Search JP

日本のインボイス登録事業者検索ツール

国税庁の適格請求書発行事業者公表システムのデータを利用して、**事業者名からの逆引き検索**を可能にするコマンドラインツールです。

## 特徴

- **逆引き検索**: 事業者名や住所から登録番号を検索（国税庁Web-APIでは不可能）
- **高速検索**: ローカルで素早く検索可能
- **簡単セットアップ**: uvで依存関係を自動管理

## 注意事項

**本ツールは個人が開発した実験的なプロトタイプです。利用は自己責任でお願いします。**

- 国税庁が公開するオープンデータを使用していますが、非公式ツールです
- 個人プロジェクトのため、問題への対応が遅れる場合があります
- データの正確性や最新性については、必ず[国税庁公式サイト](https://www.invoice-kohyo.nta.go.jp/)で確認してください

## 使い方

### 必要なもの

```bash
# uv がインストールされていない場合
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### インストール方法

#### インストールして使う（推奨）

```bash
uv tool install git+https://github.com/hrsano645/invoice-search-jp.git
```

#### 直接実行（インストール不要）

インストールせずに、毎回GitHubから直接実行することもできます。

```bash
uvx --from git+https://github.com/hrsano645/invoice-search-jp.git invoice_search_jp init
```

以下のサンプルコマンドは、インストール済みを前提としています。

---

### 1. データ初期化（初回のみ）

国税庁から法人データをダウンロードします（初回のみ、約1-2分）。

```bash
invoice_search_jp init
```

### 2. 事業者名で検索（逆引き）

事業者名または住所の部分一致で検索します。

```bash
# 市町村名で検索
invoice_search_jp search "苫小牧市"

# 企業名で検索
invoice_search_jp search "株式会社"

# 住所で検索
invoice_search_jp search "北海道"
```

**出力例**:
```
検索結果: '苫小牧市' (20件)
┌──────────┬────────────────────┬──────────────────────────┬────┬────────┐
│ 登録番号 │ 名称               │ 所在地                   │都…│ 登録日 │
├──────────┼────────────────────┼──────────────────────────┼────┼────────┤
│ T100...  │ 苫小牧市           │ 北海道苫小牧市旭町４丁目 │ 01 │ 2023…  │
│ T143...  │ 株式会社○○         │ 北海道苫小牧市...        │ 01 │ 2023…  │
└──────────┴────────────────────┴──────────────────────────┴────┴────────┘
```

### 3. 登録番号で検索

登録番号（T+13桁）で事業者情報を検索します。

```bash
invoice_search_jp lookup T1000020012131
```

**出力例**:
```
登録事業者情報: T1000020012131
┌────────────────────┬────────────────────────────┐
│ registratedNumber  │ T1000020012131             │
│ name               │ 苫小牧市                   │
│ address            │ 北海道苫小牧市旭町４丁目…  │
│ registrationDate   │ 2023-10-01                 │
└────────────────────┴────────────────────────────┘
```

## データ更新

国税庁は毎月1日（休日除く）に前月末時点のデータを公開します。

```bash
# 最新データに更新
rm -rf ~/.local/share/invoice_search_jp
invoice_search_jp init
```

## ローカル開発・カスタマイズ

コードを編集したい場合は、リポジトリをクローンしてください。

```bash
git clone https://github.com/hrsano645/invoice-search-jp.git
cd invoice-search-jp
uv pip install -e .

# コマンド実行
invoice_search_jp init
```

## 制限事項

- 現在は**法人データのみ**対応（個人事業主・人格なし社団等は未実装）

## データソース

- [国税庁 適格請求書発行事業者公表サイト](https://www.invoice-kohyo.nta.go.jp/)
- [全件データダウンロード](https://www.invoice-kohyo.nta.go.jp/download/zenken)
- [リソース定義書（PDF）](https://www.invoice-kohyo.nta.go.jp/files/k-resource-dl.pdf)

## ライセンス

データは国税庁が公開する適格請求書発行事業者公表データを使用しています。

## 今後の課題・拡張

- [ ] 個人事業主・人格なし社団等のデータ対応
- [ ] 差分データでの増分更新
- [ ] Web UI / REST API
- [ ] 検索速度の最適化

## トラブルシューティング

### `uv: command not found`

```bash
# uv をインストール
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### `データが初期化されていません`

```bash
# 初回または更新時に実行
invoice_search_jp init
```

### `ダウンロードエラー`

ファイル管理番号が変更されている可能性があります。
[ダウンロードページ](https://www.invoice-kohyo.nta.go.jp/download/zenken)のHTMLを確認して、`CORPORATE_FILE_IDS`を更新してください。

```python
# invoice_search_jp/cli.py の該当箇所
CORPORATE_FILE_IDS = [4054, 4063, 4055, 4064, 4057]  # 更新が必要
```
