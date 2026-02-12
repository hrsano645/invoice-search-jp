#!/usr/bin/env python3

"""
日本のインボイス登録事業者検索ツール
"""

import sys
import zipfile
import json
import csv
from pathlib import Path
from typing import Optional
from importlib.metadata import version, PackageNotFoundError
from datetime import datetime, date
import re
import httpx
import duckdb
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich import print as rprint

# データ保存先
DATA_DIR = Path.home() / ".local" / "share" / "invoice_search_jp"
PARQUET_FILE = DATA_DIR / "invoice_data.parquet"
METADATA_FILE = DATA_DIR / "metadata.json"

# 国税庁のダウンロードエンドポイント
DOWNLOAD_BASE_URL = "https://www.invoice-kohyo.nta.go.jp/download/zenken/dlfile"
DIFF_DOWNLOAD_URL = "https://www.invoice-kohyo.nta.go.jp/download/sabun/dlfile"

# ファイル管理番号（法人CSV 5分割）※定期的に更新される可能性あり
CORPORATE_FILE_IDS = [4054, 4063, 4055, 4064, 4057]

def get_download_url(file_id: int, entity_type: str = "2", file_type: str = "01") -> str:
    """
    ダウンロードURLを生成
    entity_type: 1=個人、2=法人、3=人格のない社団等
    file_type: 01=CSV、11=XML、21=JSON
    """
    return f"{DOWNLOAD_BASE_URL}?dlFilKanriNo={file_id}&dlFilJinkakuKbn={entity_type}&dlFilType={file_type}"

console = Console()


def load_metadata() -> dict:
    """メタデータを読み込む"""
    if not METADATA_FILE.exists():
        return {}
    try:
        with open(METADATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        rprint(f"[yellow]警告: メタデータの読み込みに失敗しました: {e}[/yellow]")
        return {}


def save_metadata(data: dict):
    """メタデータを保存"""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(METADATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        rprint(f"[yellow]警告: メタデータの保存に失敗しました: {e}[/yellow]")


def reiwa_to_gregorian(reiwa_year: int, month: int, day: int) -> date:
    """令和→西暦変換（令和元年=2019年）"""
    gregorian_year = reiwa_year + 2018
    return date(gregorian_year, month, day)


def fetch_zenken_file_ids() -> Optional[list[str]]:
    """全件データページから法人CSVのファイルIDを取得"""
    try:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            response = client.get("https://www.invoice-kohyo.nta.go.jp/download/zenken")
            response.raise_for_status()
            html = response.text

            # 法人データ（entity_type='2', file_type='01'）のファイルIDを抽出
            # パターン例: <a href="#" onclick="return doDownload('4419','2','01');">分割1
            pattern = r"doDownload\('(\d+)','2','01'\)"
            matches = re.findall(pattern, html)

            if len(matches) == 5:
                return matches
            else:
                rprint(f"[yellow]警告: 全件データのファイルIDが5件見つかりませんでした（{len(matches)}件）[/yellow]")
                return None

    except Exception as e:
        rprint(f"[yellow]警告: 全件データのファイルID取得に失敗しました: {e}[/yellow]")
        return None


def fetch_sabun_file_list() -> list[tuple[date, str]]:
    """差分データページから日付とファイルIDのリストを取得"""
    try:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            response = client.get("https://www.invoice-kohyo.nta.go.jp/download/sabun")
            response.raise_for_status()
            html = response.text

            # 日付とファイルIDを抽出
            # パターン例: <th scope="row">令和8年2月10日</th>...doDownload('4469','01')
            # 1行のテーブル行内に日付とファイルIDが含まれる
            pattern = r'<th scope="row">令和(\d+)年(\d+)月(\d+)日</th>.*?doDownload\(\'(\d+)\',\'01\'\)'
            matches = re.findall(pattern, html, re.DOTALL)

            result = []
            for reiwa_year, month, day, file_id in matches:
                date_obj = reiwa_to_gregorian(int(reiwa_year), int(month), int(day))
                result.append((date_obj, file_id))

            # 日付降順でソート
            result.sort(key=lambda x: x[0], reverse=True)
            return result

    except Exception as e:
        rprint(f"[yellow]警告: 差分データのファイルリスト取得に失敗しました: {e}[/yellow]")
        return []


def download_and_extract_csv(url: str, extract_to: Path) -> Optional[Path]:
    """ZIPファイルをダウンロードしてCSVを展開"""
    try:
        with httpx.Client(timeout=120.0, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()

            zip_path = extract_to / "temp.zip"
            zip_path.write_bytes(response.content)

            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                # CSVファイルだけを展開
                csv_files = [f for f in zip_ref.namelist() if f.endswith('.csv')]
                if csv_files:
                    zip_ref.extract(csv_files[0], extract_to)
                    zip_path.unlink()
                    return extract_to / csv_files[0]

            zip_path.unlink()
            return None

    except Exception as e:
        rprint(f"[red]ダウンロードエラー ({url}):[/red] {e}")
        return None


def download_diff_file(file_id: str, extract_to: Path) -> Optional[Path]:
    """差分データZIPをダウンロードしてCSVを展開"""
    url = f"{DIFF_DOWNLOAD_URL}?dlFilKanriNo={file_id}&type=01"
    try:
        with httpx.Client(timeout=120.0, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()

            zip_path = extract_to / f"diff_{file_id}.zip"
            zip_path.write_bytes(response.content)

            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                csv_files = [f for f in zip_ref.namelist() if f.endswith('.csv')]
                if csv_files:
                    # 差分CSVを展開
                    csv_name = f"diff_{file_id}_{csv_files[0]}"
                    zip_ref.extract(csv_files[0], extract_to)
                    extracted_path = extract_to / csv_files[0]
                    renamed_path = extract_to / csv_name
                    extracted_path.rename(renamed_path)
                    zip_path.unlink()
                    return renamed_path

            zip_path.unlink()
            return None

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            rprint(f"[yellow]差分ファイル {file_id} が見つかりませんでした（スキップ）[/yellow]")
        else:
            rprint(f"[red]差分ダウンロードエラー ({file_id}):[/red] {e}")
        return None
    except Exception as e:
        rprint(f"[red]差分ダウンロードエラー ({file_id}):[/red] {e}")
        return None


def merge_diff_data(diff_csv_paths: list[Path]):
    """差分CSVを既存Parquetにマージ"""
    if not PARQUET_FILE.exists():
        rprint("[red]エラー: Parquetファイルが存在しません[/red]")
        return False

    try:
        con = duckdb.connect()

        # 既存データを一時テーブルにロード
        con.execute(f"CREATE TEMP TABLE current AS SELECT * FROM '{PARQUET_FILE}'")

        # 差分CSVを一時テーブルにロード
        csv_paths_str = ", ".join([f"'{str(f)}'" for f in diff_csv_paths])
        con.execute(f"""
            CREATE TEMP TABLE diff AS
            SELECT * FROM read_csv(
                [{csv_paths_str}],
                header=false,
                names=['sequenceNumber', 'registratedNumber', 'process', 'correct', 'kind',
                       'country', 'latest', 'registrationDate', 'updateDate', 'disposalDate',
                       'expireDate', 'address', 'addressPrefectureCode', 'addressCityCode',
                       'addressRequest', 'addressRequestPrefectureCode', 'addressRequestCityCode',
                       'kana', 'name', 'addressInside', 'addressInsidePrefectureCode',
                       'addressInsideCityCode', 'tradeName', 'popularName_previousName'],
                delim=',',
                quote='"',
                ignore_errors=true
            )
        """)

        # 削除対象を除外（process = '21'）
        con.execute("""
            DELETE FROM current
            WHERE registratedNumber IN (
                SELECT registratedNumber FROM diff WHERE process = '21'
            )
        """)

        # 更新対象を削除（後で INSERT するため）
        con.execute("""
            DELETE FROM current
            WHERE registratedNumber IN (
                SELECT registratedNumber FROM diff WHERE process IN ('01', '11')
            )
        """)

        # 新規・更新データを挿入
        con.execute("""
            INSERT INTO current
            SELECT * FROM diff WHERE process IN ('01', '11')
        """)

        # 新しいParquetファイルに書き出し
        temp_parquet = PARQUET_FILE.parent / f"{PARQUET_FILE.name}.tmp"
        con.execute(f"""
            COPY current TO '{temp_parquet}' (FORMAT 'parquet', COMPRESSION 'zstd')
        """)

        con.close()

        # 元のファイルを置き換え
        temp_parquet.rename(PARQUET_FILE)

        return True

    except Exception as e:
        rprint(f"[red]差分マージエラー:[/red] {e}")
        return False


def determine_update_strategy() -> tuple[str, Optional[list[date]]]:
    """
    更新戦略を決定

    Returns:
        ("full", None): 全件更新が必要
        ("diff", [date_list]): 差分更新、適用すべき日付リスト
        ("skip", None): 更新不要
    """
    # Parquetファイルが存在しない場合は全件更新
    if not PARQUET_FILE.exists():
        return ("full", None)

    # メタデータを読み込み
    metadata = load_metadata()
    if not metadata or "last_diff_date" not in metadata:
        # メタデータがないか、差分更新日がない場合は全件更新
        # ただし、full_update_dateがある場合は差分更新を試みる
        if "full_update_date" in metadata:
            # 全件更新日から差分更新を開始
            try:
                full_update_dt = datetime.fromisoformat(metadata["full_update_date"])
                last_date = full_update_dt.date()
            except Exception:
                return ("full", None)
        else:
            return ("full", None)
    else:
        # 最終差分更新日を取得
        try:
            last_date = date.fromisoformat(metadata["last_diff_date"])
        except Exception:
            return ("full", None)

    # 差分ファイルリストを取得
    diff_list = fetch_sabun_file_list()
    if not diff_list:
        rprint("[yellow]差分ファイルリストの取得に失敗しました[/yellow]")
        return ("skip", None)

    # 最終更新日の翌日から今日までの差分を抽出
    today = date.today()
    from datetime import timedelta
    next_date = last_date + timedelta(days=1)

    # 適用すべき差分を抽出
    dates_to_apply = []
    for diff_date, file_id in diff_list:
        if next_date <= diff_date <= today:
            dates_to_apply.append(diff_date)

    # 日付昇順でソート
    dates_to_apply.sort()

    # 差分がない場合はスキップ
    if not dates_to_apply:
        return ("skip", None)

    # 40営業日を超える場合は全件更新
    if len(dates_to_apply) > 40:
        rprint(f"[yellow]差分データが40営業日を超えています（{len(dates_to_apply)}日分）[/yellow]")
        return ("full", None)

    return ("diff", dates_to_apply)


def update_data(force_full: bool = False):
    """データを更新（差分 or 全件を自動判定）"""
    if force_full:
        # --full オプション: 強制的に全件更新
        rprint("[cyan]全件データを再ダウンロードします...[/cyan]")
        return init_data()

    strategy, date_list = determine_update_strategy()

    if strategy == "full":
        rprint("[cyan]差分更新できません。全件データを再ダウンロードします...[/cyan]")
        return init_data()

    elif strategy == "skip":
        rprint("[green]データは最新です[/green]")
        return True

    elif strategy == "diff":
        # 差分更新の実行
        rprint(f"[cyan]{len(date_list)}日分の差分データを適用します...[/cyan]")

        # 差分ファイルリストを取得
        diff_file_list = fetch_sabun_file_list()
        date_to_file_id = {d: fid for d, fid in diff_file_list}

        downloaded_files = []

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console
        ) as progress:

            # 各日付の差分ファイルをダウンロード
            task = progress.add_task("[cyan]差分データをダウンロード中...", total=len(date_list))
            for diff_date in date_list:
                file_id = date_to_file_id.get(diff_date)
                if file_id:
                    progress.update(task, description=f"[cyan]{diff_date} の差分をダウンロード中...")
                    csv_path = download_diff_file(file_id, DATA_DIR)
                    if csv_path:
                        downloaded_files.append(csv_path)
                else:
                    rprint(f"[yellow]{diff_date} の差分ファイルが見つかりませんでした（スキップ）[/yellow]")
                progress.advance(task)

            if not downloaded_files:
                rprint("[yellow]差分ファイルのダウンロードに失敗しました[/yellow]")
                return False

            # 差分データをマージ
            progress.add_task("[cyan]差分データをマージ中...", total=None)
            if not merge_diff_data(downloaded_files):
                return False

        # ダウンロードした一時ファイルを削除
        for csv_file in downloaded_files:
            csv_file.unlink()

        # メタデータの last_diff_date を更新
        metadata = load_metadata()
        metadata["last_diff_date"] = date_list[-1].isoformat()
        save_metadata(metadata)

        rprint(f"[green]✓ {len(date_list)}日分の差分データを適用しました[/green]")
        return True

    return False


def init_data():
    """データの初期化：CSVダウンロード → Parquet変換"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    csv_files = []

    # 動的にファイルIDを取得
    rprint("[cyan]最新のファイルIDを取得中...[/cyan]")
    file_ids = fetch_zenken_file_ids()

    if not file_ids:
        # 取得失敗時は旧IDにフォールバック
        rprint("[yellow]警告: 最新のファイルIDの取得に失敗しました。既知のIDを使用します[/yellow]")
        file_ids = [str(fid) for fid in CORPORATE_FILE_IDS]

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console
    ) as progress:

        # 法人データのダウンロード
        task = progress.add_task("[cyan]法人データをダウンロード中...", total=len(file_ids))
        for i, file_id in enumerate(file_ids, 1):
            url = get_download_url(int(file_id), entity_type="2", file_type="01")
            progress.update(task, description=f"[cyan]法人データ {i}/{len(file_ids)} をダウンロード中...")
            csv_path = download_and_extract_csv(url, DATA_DIR)
            if csv_path:
                csv_files.append(csv_path)
            progress.advance(task)

        if not csv_files:
            rprint("[red]CSVファイルのダウンロードに失敗しました[/red]")
            return False

        # Parquetに変換
        progress.add_task("[cyan]Parquetに変換中...", total=None)
        try:
            con = duckdb.connect()

            # CSVカラム名を明示的に指定（PDFドキュメント参照）
            # 法人データは30カラム、ヘッダーなし
            csv_paths_str = ", ".join([f"'{str(f)}'" for f in csv_files])

            con.execute(f"""
                COPY (
                    SELECT * FROM read_csv(
                        [{csv_paths_str}],
                        header=false,
                        names=['sequenceNumber', 'registratedNumber', 'process', 'correct', 'kind',
                               'country', 'latest', 'registrationDate', 'updateDate', 'disposalDate',
                               'expireDate', 'address', 'addressPrefectureCode', 'addressCityCode',
                               'addressRequest', 'addressRequestPrefectureCode', 'addressRequestCityCode',
                               'kana', 'name', 'addressInside', 'addressInsidePrefectureCode',
                               'addressInsideCityCode', 'tradeName', 'popularName_previousName'],
                        delim=',',
                        quote='"',
                        ignore_errors=true
                    )
                ) TO '{PARQUET_FILE}' (FORMAT 'parquet', COMPRESSION 'zstd')
            """)

            con.close()

            # 一時CSVファイルを削除
            for csv_file in csv_files:
                csv_file.unlink()

            # ファイルサイズを表示
            size_mb = PARQUET_FILE.stat().st_size / (1024 * 1024)
            rprint(f"[green]✓[/green] Parquetファイル作成完了: {PARQUET_FILE}")
            rprint(f"[green]✓[/green] サイズ: {size_mb:.1f} MB")

            # メタデータを保存（全件更新日時とデータ基準日）
            now = datetime.now().astimezone()
            # 前月末日を計算
            today = date.today()
            first_of_month = date(today.year, today.month, 1)
            from datetime import timedelta
            data_as_of = first_of_month - timedelta(days=1)

            metadata = {
                "full_update_date": now.isoformat(),
                "data_as_of": data_as_of.isoformat(),
                "version": "0.4.0"
            }
            save_metadata(metadata)

            return True

        except Exception as e:
            rprint(f"[red]Parquet変換エラー:[/red] {e}")
            return False


def show_status():
    """データの状態を表示"""
    if not PARQUET_FILE.exists():
        rprint("[yellow]データが初期化されていません[/yellow]")
        rprint("まず [cyan]invoice_search_jp init[/cyan] を実行してください")
        return

    # メタデータを読み込み
    metadata = load_metadata()

    # ファイルサイズを取得
    file_size_bytes = PARQUET_FILE.stat().st_size
    file_size_mb = file_size_bytes / (1024 * 1024)

    # レコード総数を取得
    try:
        con = duckdb.connect()
        total_count = con.execute(f"SELECT COUNT(*) FROM '{PARQUET_FILE}'").fetchone()[0]
        con.close()
    except Exception as e:
        total_count = "取得失敗"
        rprint(f"[yellow]警告: レコード数の取得に失敗しました: {e}[/yellow]")

    # テーブルを作成
    table = Table(title="インボイスデータの状態", show_header=True)
    table.add_column("項目", style="cyan", width=20)
    table.add_column("内容", style="white")

    # ファイルサイズ
    table.add_row("ファイルサイズ", f"{file_size_mb:.1f} MB")

    # 全件更新日時
    if "full_update_date" in metadata:
        try:
            full_update_dt = datetime.fromisoformat(metadata["full_update_date"])
            full_update_str = full_update_dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            full_update_str = metadata["full_update_date"]
        table.add_row("全件更新日時", full_update_str)
    else:
        table.add_row("全件更新日時", "不明")

    # 差分更新日
    if "last_diff_date" in metadata:
        table.add_row("差分更新日", metadata["last_diff_date"])
    else:
        table.add_row("差分更新日", "なし")

    # データ基準日
    if "data_as_of" in metadata:
        table.add_row("データ基準日", metadata["data_as_of"])
    else:
        table.add_row("データ基準日", "不明")

    # 登録事業者数
    if isinstance(total_count, int):
        table.add_row("登録事業者数", f"{total_count:,} 件")
    else:
        table.add_row("登録事業者数", total_count)

    console.print(table)


def search_by_name(query: str, limit: int = 20, page: int = 1, output_format: str = "table"):
    """事業者名で検索"""
    if not PARQUET_FILE.exists():
        rprint("[red]エラー:[/red] データが初期化されていません")
        rprint("まず [cyan]invoice_search_jp init[/cyan] を実行してください")
        return

    try:
        con = duckdb.connect()

        # 総件数を取得
        total_count = con.execute(f"""
            SELECT COUNT(*)
            FROM '{PARQUET_FILE}'
            WHERE
                "name" LIKE '%{query}%'
                OR "address" LIKE '%{query}%'
        """).fetchone()[0]

        if total_count == 0:
            if output_format == "table":
                rprint(f"[yellow]'{query}' に一致する事業者が見つかりませんでした[/yellow]")
            con.close()
            return

        # ページネーション用のオフセット計算
        offset = (page - 1) * limit
        total_pages = (total_count + limit - 1) // limit  # 切り上げ

        # ページ番号の検証
        if page < 1:
            if output_format == "table":
                rprint(f"[red]エラー:[/red] ページ番号は1以上を指定してください")
            con.close()
            return
        
        if offset >= total_count:
            if output_format == "table":
                rprint(f"[red]エラー:[/red] ページ番号が範囲外です（全{total_pages}ページ）")
            con.close()
            return

        # 事業者名と住所で検索（ページネーション対応）
        result = con.execute(f"""
            SELECT registratedNumber, name, address, addressPrefectureCode, registrationDate
            FROM '{PARQUET_FILE}'
            WHERE
                "name" LIKE '%{query}%'
                OR "address" LIKE '%{query}%'
            LIMIT {limit}
            OFFSET {offset}
        """).fetchall()

        columns = ["registratedNumber", "name", "address", "addressPrefectureCode", "registrationDate"]

        # 出力形式に応じて表示
        if output_format == "csv":
            writer = csv.writer(sys.stdout)
            writer.writerow(columns)
            for row in result:
                writer.writerow([str(v) if v else "" for v in row])
        
        elif output_format == "json":
            data = []
            for row in result:
                data.append(dict(zip(columns, [str(v) if v else "" for v in row])))
            print(json.dumps(data, ensure_ascii=False, indent=2))
        
        else:  # table
            # 結果を表示
            # expand=Trueでターミナル幅いっぱいに展開、ratioで列幅の比率を制御
            table = Table(
                title=f"検索結果: '{query}' ({len(result)}件 / 全{total_count}件) - ページ {page}/{total_pages}",
                expand=True
            )
            table.add_column("登録番号", style="cyan", ratio=1, overflow="fold")
            table.add_column("名称", style="white", ratio=2, overflow="fold")
            table.add_column("所在地", style="white", ratio=3, overflow="fold")
            table.add_column("都道府県", style="green", ratio=1, overflow="fold")
            table.add_column("登録日", style="yellow", ratio=1, overflow="fold")

            for row in result:
                table.add_row(*[str(v) if v else "" for v in row])

            console.print(table)

            # ページネーション情報の表示
            if page < total_pages:
                rprint(f"[yellow]次のページ:[/yellow] invoice_search_jp search '{query}' --page {page + 1}")
            if total_count > limit:
                rprint(f"[dim]表示件数を変更: --limit オプションを使用[/dim]")

        con.close()

    except Exception as e:
        rprint(f"[red]検索エラー:[/red] {e}")


def lookup_by_number(number: str, output_format: str = "table"):
    """登録番号で検索"""
    if not PARQUET_FILE.exists():
        rprint("[red]エラー:[/red] データが初期化されていません")
        rprint("まず [cyan]invoice_search_jp init[/cyan] を実行してください")
        return

    # T接頭辞の処理
    if not number.startswith("T"):
        number = "T" + number

    try:
        con = duckdb.connect()

        result = con.execute(f"""
            SELECT *
            FROM '{PARQUET_FILE}'
            WHERE "registratedNumber" = '{number}'
        """).fetchone()

        if not result:
            if output_format == "table":
                rprint(f"[red]登録番号 {number} は見つかりませんでした[/red]")
            con.close()
            return

        # 結果を表示
        columns = [desc[0] for desc in con.description]

        if output_format == "csv":
            writer = csv.writer(sys.stdout)
            writer.writerow(columns)
            writer.writerow([str(v) if v else "" for v in result])
        
        elif output_format == "json":
            data = dict(zip(columns, [str(v) if v else "" for v in result]))
            print(json.dumps(data, ensure_ascii=False, indent=2))
        
        else:  # table
            table = Table(title=f"登録事業者情報: {number}", show_header=False)
            table.add_column("項目", style="cyan", width=20)
            table.add_column("内容", style="white")

            for col, val in zip(columns, result):
                if val:
                    table.add_row(col, str(val))

            console.print(table)
        
        con.close()

    except Exception as e:
        rprint(f"[red]検索エラー:[/red] {e}")


def main():
    if len(sys.argv) < 2:
        rprint("[yellow]Usage:[/yellow]")
        rprint("  invoice_search_jp init                           # データ初期化")
        rprint("  invoice_search_jp update                         # データ更新（差分 or 全件を自動判定）")
        rprint("  invoice_search_jp update --full                  # 全件データを強制再ダウンロード")
        rprint("  invoice_search_jp status                         # データの状態を表示")
        rprint("  invoice_search_jp search <事業者名>               # 事業者名で検索")
        rprint("  invoice_search_jp search <事業者名> --page 2      # ページ指定")
        rprint("  invoice_search_jp search <事業者名> --limit 50    # 表示件数指定")
        rprint("  invoice_search_jp search <事業者名> --format csv  # CSV形式で出力")
        rprint("  invoice_search_jp search <事業者名> --format json # JSON形式で出力")
        rprint("  invoice_search_jp lookup <登録番号>               # 登録番号で検索")
        rprint("  invoice_search_jp lookup <登録番号> --format csv  # CSV形式で出力")
        rprint("  invoice_search_jp --version, -v                  # バージョン表示")
        sys.exit(1)

    command = sys.argv[1]

    if command in ("--version", "-v"):
        try:
            pkg_version = version("invoice-search-jp")
            rprint(f"invoice_search_jp version {pkg_version}")
        except PackageNotFoundError:
            rprint("[yellow]バージョン情報が取得できません（開発モードの可能性があります）[/yellow]")
        sys.exit(0)

    elif command == "init":
        rprint("[cyan]インボイスデータを初期化します...[/cyan]")
        if init_data():
            rprint("[green]✓ 初期化完了[/green]")
        else:
            rprint("[red]✗ 初期化失敗[/red]")
            sys.exit(1)

    elif command == "update":
        force_full = "--full" in sys.argv
        rprint("[cyan]データを更新します...[/cyan]")
        if update_data(force_full=force_full):
            rprint("[green]✓ 更新完了[/green]")
        else:
            rprint("[red]✗ 更新失敗[/red]")
            sys.exit(1)

    elif command == "status":
        show_status()

    elif command == "search":
        if len(sys.argv) < 3:
            rprint("[red]エラー:[/red] 検索キーワードを指定してください")
            rprint("例: invoice_search_jp search 株式会社")
            sys.exit(1)

        query = sys.argv[2]
        
        # オプション引数の解析
        limit = 20
        page = 1
        output_format = "table"
        
        i = 3
        while i < len(sys.argv):
            if sys.argv[i] == "--limit" and i + 1 < len(sys.argv):
                try:
                    limit = int(sys.argv[i + 1])
                    if limit < 1:
                        rprint("[red]エラー:[/red] --limit は1以上を指定してください")
                        sys.exit(1)
                    i += 2
                except ValueError:
                    rprint("[red]エラー:[/red] --limit には数値を指定してください")
                    sys.exit(1)
            elif sys.argv[i] == "--page" and i + 1 < len(sys.argv):
                try:
                    page = int(sys.argv[i + 1])
                    if page < 1:
                        rprint("[red]エラー:[/red] --page は1以上を指定してください")
                        sys.exit(1)
                    i += 2
                except ValueError:
                    rprint("[red]エラー:[/red] --page には数値を指定してください")
                    sys.exit(1)
            elif sys.argv[i] == "--format" and i + 1 < len(sys.argv):
                output_format = sys.argv[i + 1].lower()
                if output_format not in ("table", "csv", "json"):
                    rprint("[red]エラー:[/red] --format は table, csv, json のいずれかを指定してください")
                    sys.exit(1)
                i += 2
            else:
                rprint(f"[red]エラー:[/red] 不明なオプション '{sys.argv[i]}'")
                sys.exit(1)
        
        search_by_name(query, limit=limit, page=page, output_format=output_format)

    elif command == "lookup":
        if len(sys.argv) < 3:
            rprint("[red]エラー:[/red] 登録番号を指定してください")
            rprint("例: invoice_search_jp lookup T1234567890123")
            sys.exit(1)

        number = sys.argv[2]
        output_format = "table"
        
        # オプション引数の解析
        i = 3
        while i < len(sys.argv):
            if sys.argv[i] == "--format" and i + 1 < len(sys.argv):
                output_format = sys.argv[i + 1].lower()
                if output_format not in ("table", "csv", "json"):
                    rprint("[red]エラー:[/red] --format は table, csv, json のいずれかを指定してください")
                    sys.exit(1)
                i += 2
            else:
                rprint(f"[red]エラー:[/red] 不明なオプション '{sys.argv[i]}'")
                sys.exit(1)
        
        lookup_by_number(number, output_format=output_format)

    else:
        rprint(f"[red]エラー:[/red] 不明なコマンド '{command}'")
        sys.exit(1)


if __name__ == "__main__":
    main()
