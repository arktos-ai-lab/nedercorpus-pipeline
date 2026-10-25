"""Crude LaTeX -> prose markdown for check_style.py; also reports dash usage in prose."""
import re, sys
src = open(sys.argv[1], encoding="utf-8").read()
src = src.split("\\begin{document}", 1)[1]
src = re.sub(r"(?<!\\)%.*", "", src)
# report dashes before stripping (outside tabular)
body_nontab = re.sub(r"\\begin\{tabular\}.*?\\end\{tabular\}", "", src, flags=re.S)
for m in re.finditer(r"---|(?<![0-9{])--(?![0-9])|\u2013|\u2014", body_nontab):
    print("DASH:", body_nontab[max(0, m.start()-60):m.end()+30].replace("\n", " "), file=sys.stderr)
src = re.sub(r"\\begin\{tabular\}.*?\\end\{tabular\}", "", src, flags=re.S)
src = src.replace("\\bibliography{references}", "")
captions = re.findall(r"\\caption\{((?:[^{}]|\{[^{}]*\})*)\}", src)
src = re.sub(r"\\begin\{table\}.*?\\end\{table\}", "", src, flags=re.S)
src += "\n\n" + "\n\n".join(captions)
src = re.sub(r"\\cite[pt]?(\[[^\]]*\])?\{[^}]*\}", "", src)
src = re.sub(r"\\(ref|label|footnote)\{[^}]*\}", "X", src)
src = re.sub(r"\\(section|subsection|paragraph)\*?\{([^}]*)\}", r"\n\n# \2\n\n", src)
for _ in range(3):
    src = re.sub(r"\\(textbf|emph|texttt|textsuperscript|url)\{([^{}]*)\}", r"\2", src)
src = re.sub(r"\\[a-zA-Z]+\*?", " ", src)
src = re.sub(r"[{}$~]", " ", src)
paras = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n", src)]
print("\n\n".join(p for p in paras if p))
