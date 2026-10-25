import json,sys,collections
def agg(path):
    T=collections.Counter(); groups={k:collections.defaultdict(collections.Counter) for k in ["by_source","by_license","by_variant","by_domain","by_src_lic"]}
    for l in open(path,encoding='utf-8'):
        r=json.loads(l)
        T.update(r["total"])
        for g in groups:
            for k,v in r[g].items(): groups[g][k].update(v)
    return T,groups
if __name__=="__main__":
    T,g=agg(sys.argv[1]); print(dict(T))
    for name in ["by_license","by_variant","by_domain"]:
        print("==",name)
        for k,v in sorted(g[name].items(),key=lambda x:-x[1]["tok"]): print(f"  {k!r:30} docs={v['docs']:>10,} ge10={v['docs_ge10']:>10,} tok={v['tok']/1e9:.4f}B tok_ge10={v['tok_ge10']/1e9:.4f}B")
    print("== top sources")
    for k,v in sorted(g["by_source"].items(),key=lambda x:-x[1]["tok"])[:40]: print(f"  {k:35} docs={v['docs']:>10,} tok={v['tok']/1e6:9.1f}M")
