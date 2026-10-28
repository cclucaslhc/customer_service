"""Bounded CI diagnostics only; never approves or overrides a scan exit code."""
import json
from pathlib import Path
import sys


def summary(path):
    if not path.is_absolute() or path.is_symlink() or not path.is_file() or path.stat().st_size>32*1024*1024:
        return {'scan_diagnostic':'report_unavailable'}
    report=json.loads(path.read_text(encoding='utf-8'))
    findings=[]
    for target in report.get('Results',[]):
        for value in target.get('Vulnerabilities') or []:
            if value.get('Severity') in {'HIGH','CRITICAL'}:
                findings.append({key:str(value.get(key,''))[:200] for key in (
                    'VulnerabilityID','PkgName','InstalledVersion','FixedVersion','Severity')})
    return {'high_critical_findings':len(findings),'findings':findings[:100],
        'truncated':len(findings)>100}


if __name__=='__main__':
    try: value=summary(Path(sys.argv[1]))
    except Exception: value={'scan_diagnostic':'report_unreadable'}
    print(json.dumps(value,ensure_ascii=True))
