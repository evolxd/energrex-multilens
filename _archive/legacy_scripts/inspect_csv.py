import pandas as pd, pathlib, sys

df = pd.read_csv('results_validated.csv', encoding='utf-8-sig', nrows=1)
raw_cols = [c for c in df.columns if c.startswith('raw_')]
out = pathlib.Path('inspect_out.txt')
lines = ['RAW columns:']
for c in raw_cols:
    lines.append('  ' + c)
lines.append('')
df2 = pd.read_csv('results_validated.csv', encoding='utf-8-sig')
lines.append(f'Total tickers: {len(df2)}')
lines.append('Tickers: ' + ', '.join(df2['ticker'].tolist()))
out.write_text('\n'.join(lines), encoding='utf-8')
print('written to inspect_out.txt')
