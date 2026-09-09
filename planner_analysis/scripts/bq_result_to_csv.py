#!/usr/bin/env python3
"""Convert one or more BigQuery MCP result JSON files (schema+rows in f/v format) into a single CSV.
Usage: bq_result_to_csv.py out.csv in1.json in2.json ...
"""
import sys, json, csv
out=sys.argv[1]; files=sys.argv[2:]
w=None; n=0; cols=None
with open(out,'w',newline='',encoding='utf-8') as fo:
    for fn in files:
        d=json.load(open(fn,encoding='utf-8'))
        c=[f['name'] for f in d['schema']['fields']]
        if cols is None:
            cols=c; w=csv.writer(fo); w.writerow(cols)
        elif c!=cols:
            raise SystemExit(f'schema mismatch in {fn}')
        for r in d.get('rows',[]):
            w.writerow([('' if cell['v'] is None else cell['v']) for cell in r['f']]); n+=1
print(f'wrote {n} rows x {len(cols)} cols to {out}')
