import sys; sys.path.insert(0, '.')
import os, datetime, pandas as pd
from src.data.bar_store import bar_path

bars_dir = bar_path('X', 'X').parent
files = sorted(bars_dir.glob('*__daily.csv'), key=os.path.getmtime, reverse=True)[:8]
for f in files:
    mt = datetime.datetime.fromtimestamp(os.path.getmtime(f))
    df = pd.read_csv(f)
    last = df['date'].iloc[-1] if len(df) else 'empty'
    print(f"{f.name:35s}  written={mt.strftime('%H:%M:%S')}  last_bar={last}")
