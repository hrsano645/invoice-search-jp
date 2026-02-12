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

データの保存先は、`~/.local/share/invoice_search_jp` です。

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

# 都道府県を指定して検索
invoice_search_jp search "株式会社" -p 東京
invoice_search_jp search "株式会社" --prefecture 大阪

# 半角数字でも検索可能（自動で全角に変換）
invoice_search_jp search "4丁目"  # 「４丁目」を含む住所がヒット

# ページネーション - 2ページ目を表示
invoice_search_jp search "株式会社" --page 2

# 表示件数を変更
invoice_search_jp search "株式会社" --limit 50

# 組み合わせも可能
invoice_search_jp search "株式会社" --page 3 --limit 50

# CSV形式で出力（他のツールと連携）
invoice_search_jp search "苫小牧市" --format csv

# JSON形式で出力（他のツールと連携）
invoice_search_jp search "苫小牧市" --format json

# 出力形式の組み合わせも可能
invoice_search_jp search "苫小牧市" --limit 100 --format csv > result.csv
```

**出力例**:
```
検索結果: '苫小牧市' (20件 / 全156件) - ページ 1/8
┌──────────┬────────────────────┬──────────────────────────┬────┬────────┐
│ 登録番号 │ 名称               │ 所在地                   │都…│ 登録日 │
├──────────┼────────────────────┼──────────────────────────┼────┼────────┤
│ T100...  │ 苫小牧市           │ 北海道苫小牧市旭町４丁目 │ 01 │ 2023…  │
│ T143...  │ 株式会社○○         │ 北海道苫小牧市...        │ 01 │ 2023…  │
└──────────┴────────────────────┴──────────────────────────┴────┴────────┘
次のページ: invoice_search_jp search '苫小牧市' --page 2
表示件数を変更: --limit オプションを使用
```

**CSV出力例**:
```csv
registratedNumber,name,address,addressPrefectureCode,registrationDate
T1000020012131,苫小牧市,北海道苫小牧市旭町４丁目５番６号,01,2023-10-01
T1430001015131,株式会社○○,北海道苫小牧市...,01,2023-10-01
```

**JSON出力例**:
```json
[
  {
    "registratedNumber": "T1000020012131",
    "name": "苫小牧市",
    "address": "北海道苫小牧市旭町４丁目５番６号",
    "addressPrefectureCode": "01",
    "registrationDate": "2023-10-01"
  }
]
```

### 3. 登録番号で検索

登録番号（T+13桁）で事業者情報を検索します。

```bash
invoice_search_jp lookup T1000020012131

# CSV形式で出力
invoice_search_jp lookup T1000020012131 --format csv

# JSON形式で出力
invoice_search_jp lookup T1000020012131 --format json
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

**CSV出力例**:
```csv
sequenceNumber,registratedNumber,process,correct,kind,country,latest,registrationDate,updateDate,disposalDate,expireDate,address,addressPrefectureCode,addressCityCode,addressRequest,addressRequestPrefectureCode,addressRequestCityCode,kana,name,addressInside,addressInsidePrefectureCode,addressInsideCityCode,tradeName,popularName_previousName
1,T1000020012131,01,0,1,,1,2023-10-01,,,,北海道苫小牧市旭町４丁目５番６号,01,01234,,,,トマコマイシ,苫小牧市,,,,
```

## データ更新

国税庁は以下のスケジュールでデータを公開します：
- **全件データ（月次）**: 毎月1日（営業日）6:00 AM、前月末時点のデータ
- **差分データ（日次）**: 毎営業日 6:00 AM、過去40営業日分を保持

### データの状態を確認

```bash
invoice_search_jp status
```

**出力例**:
```
          インボイスデータの状態
┌──────────────────┬──────────────────┐
│ 項目             │ 内容             │
├──────────────────┼──────────────────┤
│ ファイルサイズ   │ 70.8 MB          │
│ 全件更新日時     │ 2026-02-08 10:00 │
│ 差分更新日       │ 2026-02-10       │
│ データ基準日     │ 2026-01-31       │
│ 登録事業者数     │ 2,550,629 件     │
└──────────────────┴──────────────────┘
```

### データを更新

```bash
# 自動判定で更新（差分 or 全件）
invoice_search_jp update

# 全件データを強制再ダウンロード
invoice_search_jp update --full
```

`update` コマンドは、最終更新日から現在までの差分データを自動的に適用します。
差分データが40営業日を超える場合や、メタデータが存在しない場合は自動的に全件更新を行います。

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
- [x] 差分データでの増分更新（v0.4.0で実装済み）
- [x] 標準出力へのCSV/JSON形式での出力対応（v0.3.0で実装済み）
- [x] 動的ファイルID取得（v0.4.0で実装済み）
- [ ] 検索速度の最適化
- [x] 半角/全角の正規化対応（v0.5.0で実装済み）
- [x] 都道府県フィルター機能（v0.5.0で実装済み）

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

v0.4.0以降、ファイル管理番号は自動的に取得されます。

ダウンロードエラーが発生する場合：
1. ネットワーク接続を確認してください
2. 国税庁のサイトがメンテナンス中でないか確認してください
3. しばらく時間をおいて再試行してください

それでも解決しない場合は、[Issue](https://github.com/hrsano645/invoice-search-jp/issues)で報告してください。
