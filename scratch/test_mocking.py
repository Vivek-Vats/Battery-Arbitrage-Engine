import sys
sys.path.append('.')
from unittest.mock import patch, MagicMock
import pandas as pd
import sqlite3
import data_ingestion

real_to_sql = pd.DataFrame.to_sql
def mock_to_sql(self, *args, **kwargs):
    print("Before to_sql:")
    print(self)
    print("Columns:", self.columns)
    print("Index len:", len(self.index))
    return

@patch('data_ingestion.pd.DataFrame.to_sql', new=mock_to_sql)
@patch('data_ingestion.fetch_prices')
def test_mocking(mock_fetch):
    idx = pd.DatetimeIndex(['2024-01-01 00:00:00', '2024-01-01 01:00:00'], tz='Europe/Amsterdam')
    df_fake = pd.Series([10.0, 20.0], index=idx)
    
    mock_fetch.side_effect = [df_fake, pd.Series(dtype=float)] 

    spine = pd.date_range('2023-12-31 23:00:00', periods=8, freq='15min', tz='UTC')

    with patch('data_ingestion.pd.date_range') as mock_date_range:
        mock_date_range.return_value = spine
        
        data_ingestion.main()

test_mocking()
