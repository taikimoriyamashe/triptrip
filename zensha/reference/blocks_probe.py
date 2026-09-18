import re,sys
def blocks(name):
    c=open(name+'.md').read()
    return [t for t in re.split(r'\n\s*\n',c) if t.strip()]
def rows(block):
    out=[]
    for line in block.split('\n'):
        if not line.startswith('|'): continue
        cells=[x.strip() for x in line.strip().strip('|').split('|')]
        if all(re.fullmatch(r':?-+:?',x) or x=='' for x in cells): continue
        out.append(cells)
    return out
def show(name,bi,maxrows=40,maxcols=20,maxw=14):
    b=blocks(name)[bi]; r=rows(b)
    if not r: print('(no rows)'); return
    ncol=max(len(x) for x in r)
    # drop columns that are empty in all rows
    keep=[j for j in range(ncol) if any(j<len(x) and x[j] for x in r)]
    print(f'--- {name} block {bi}: rows={len(r)} cols={ncol} nonempty_cols={len(keep)}')
    for x in r[:maxrows]:
        cells=[(x[j] if j<len(x) else '')[:maxw] for j in keep[:maxcols]]
        print(' | '.join(cells))
if __name__=='__main__':
    name=sys.argv[1]
    if sys.argv[2]=='find':
        pat=sys.argv[3]
        for i,b in enumerate(blocks(name)):
            n=len(re.findall(pat,b))
            if n: print(i,n,len(rows(b)))
    else:
        show(name,int(sys.argv[2]),*(int(a) for a in sys.argv[3:]))
