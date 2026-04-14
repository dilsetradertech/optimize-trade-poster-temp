import duckdb
import pandas as pd
from datetime import datetime
CSV_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
DB_FILE = "options_trade_poster.db"
TABLE_NAME = "instruments"
def fetch_and_filter_csv(url: str) -> pd.DataFrame:
    try:
        df = pd.read_csv(url)
        # Convert expiry date column
        df["SEM_EXPIRY_DATE"] = pd.to_datetime(df["SEM_EXPIRY_DATE"], errors="coerce")
        today = pd.Timestamp.today()
        current_month = today.month
        current_year = today.year
        # Remove expired contracts (keep NaT for non-expiry instruments)
        df = df[df["SEM_EXPIRY_DATE"].isna() | (df["SEM_EXPIRY_DATE"] >= today)]
        # Add sort key: 0 = current month, 1 = future, 2 = no expiry
        def sort_key(row):
            expiry = row["SEM_EXPIRY_DATE"]
            if pd.isna(expiry):
                return 2
            elif expiry.month == current_month and expiry.year == current_year:
                return 0
            else:
                return 1
        df["sort_order"] = df.apply(sort_key, axis=1)
        # Sort by custom order, then by expiry date
        df = df.sort_values(by=["sort_order", "SEM_EXPIRY_DATE"], ascending=[True, True])
        # Select final columns
        filtered_df = df[[
            "SEM_SMST_SECURITY_ID",    #C
            "SEM_INSTRUMENT_NAME",     #D
            "SEM_CUSTOM_SYMBOL",       #H
            "SEM_EXM_EXCH_ID",         #A
            "SEM_EXPIRY_DATE",         #I
            "SEM_LOT_UNITS"            #G
        ]].reset_index(drop=True)
        return filtered_df
    except Exception as e:
        raise RuntimeError(f"Error processing CSV: {e}")
def create_table_from_df(df: pd.DataFrame, db_path: str, table_name: str) -> None:
    try:
        with duckdb.connect(db_path) as conn:
            conn.execute(f"DROP TABLE IF EXISTS {table_name}")
            conn.execute(f"CREATE TABLE {table_name} AS SELECT * FROM df")
            count = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]

    except Exception as e:
        raise RuntimeError(f"Error creating DuckDB table: {e}")
def main():
    try:
        df = fetch_and_filter_csv(CSV_URL)
        create_table_from_df(df, DB_FILE, TABLE_NAME)
    except Exception as e:
        print(f"[ERROR] {e}")
if __name__ == "__main__":
    main()











