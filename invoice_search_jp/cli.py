#!/usr/bin/env python3

"""
日本のインボイス登録事業者検索ツール
"""

import sys
import zipfile
from pathlib import Path
from typing import Optional
from importlib.metadata import version, PackageNotFoundError
import httpx
import duckdb
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich import print as rprint

# データ保存先
DATA_DIR = Path.home() / ".local" / "share" / "invoice_search_jp"
PARQUET_FILE = DATA_DIR / "invoice_data.parquet"

# 国税庁のダウンロードエンドポイント
DOWNLOAD_BASE_URL = "https://www.invoice-kohyo.nta.go.jp/download/zenken/dlfile"

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


def init_data():
    """データの初期化：CSVダウンロード → Parquet変換"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    csv_files = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console
    ) as progress:

        # 法人データのダウンロード
        task = progress.add_task("[cyan]法人データをダウンロード中...", total=len(CORPORATE_FILE_IDS))
        for i, file_id in enumerate(CORPORATE_FILE_IDS, 1):
            url = get_download_url(file_id, entity_type="2", file_type="01")
            progress.update(task, description=f"[cyan]法人データ {i}/{len(CORPORATE_FILE_IDS)} をダウンロード中...")
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
            return True

        except Exception as e:
            rprint(f"[red]Parquet変換エラー:[/red] {e}")
            return False


def search_by_name(query: str, limit: int = 20, page: int = 1):
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
            rprint(f"[yellow]'{query}' に一致する事業者が見つかりませんでした[/yellow]")
            con.close()
            return

        # ページネーション用のオフセット計算
        offset = (page - 1) * limit
        total_pages = (total_count + limit - 1) // limit  # 切り上げ

        # ページ番号の検証
        if page < 1:
            rprint(f"[red]エラー:[/red] ページ番号は1以上を指定してください")
            con.close()
            return
        
        if offset >= total_count:
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

        # 結果を表示
        table = Table(title=f"検索結果: '{query}' ({len(result)}件 / 全{total_count}件) - ページ {page}/{total_pages}")
        # width指定なし＋overflow='fold'で自動調整＆折り返し
        table.add_column("登録番号", style="cyan", overflow="fold")
        table.add_column("名称", style="white", overflow="fold")
        table.add_column("所在地", style="white", overflow="fold")
        table.add_column("都道府県", style="green", overflow="fold")
        table.add_column("登録日", style="yellow", overflow="fold")

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


def lookup_by_number(number: str):
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
            rprint(f"[red]登録番号 {number} は見つかりませんでした[/red]")
            return

        # 結果を表示
        columns = [desc[0] for desc in con.description]

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
        rprint("  invoice_search_jp search <事業者名>               # 事業者名で検索")
        rprint("  invoice_search_jp search <事業者名> --page 2      # ページ指定")
        rprint("  invoice_search_jp search <事業者名> --limit 50    # 表示件数指定")
        rprint("  invoice_search_jp lookup <登録番号>               # 登録番号で検索")
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

    elif command == "search":
        if len(sys.argv) < 3:
            rprint("[red]エラー:[/red] 検索キーワードを指定してください")
            rprint("例: invoice_search_jp search 株式会社")
            sys.exit(1)

        query = sys.argv[2]
        
        # オプション引数の解析
        limit = 20
        page = 1
        
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
            else:
                rprint(f"[red]エラー:[/red] 不明なオプション '{sys.argv[i]}'")
                sys.exit(1)
        
        search_by_name(query, limit=limit, page=page)

    elif command == "lookup":
        if len(sys.argv) < 3:
            rprint("[red]エラー:[/red] 登録番号を指定してください")
            rprint("例: invoice_search_jp lookup T1234567890123")
            sys.exit(1)

        number = sys.argv[2]
        lookup_by_number(number)

    else:
        rprint(f"[red]エラー:[/red] 不明なコマンド '{command}'")
        sys.exit(1)


if __name__ == "__main__":
    main()
