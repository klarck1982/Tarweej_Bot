#!/usr/bin/env bash
# تشغيل المحاكاة الثلاث على قواعد جديدة + فحص السجل المالي + ملخص المشاكل
cd "$(dirname "$0")"; mkdir -p results; R=results
P=$(ls -d /usr/lib/postgresql/*/bin|tail -1)
/tmp/freshdb.sh 2>/dev/null; timeout 900 /tmp/venv/bin/python scenarios.py > $R/scen_stdout.txt 2>&1; echo "scenarios exit=$?"
/tmp/freshdb.sh 2>/dev/null; timeout 1500 /tmp/venv/bin/python crawler.py ${1:-500} > $R/crawl_stdout.txt 2>&1; echo "crawler exit=$?"
$P/psql -h 127.0.0.1 -p 5433 -U postgres promobot -tAc "select count(*) from users u where balance_usd <> (select coalesce(sum(amount_usd),0) from ledger l where l.user_id=u.tg_id)" | sed 's/^/ledger_mismatch_users=/'
/tmp/freshdb.sh 2>/dev/null; timeout 900 /tmp/venv/bin/python extra.py > $R/extra_stdout.txt 2>&1; echo "extra exit=$?"
/tmp/venv/bin/python - <<'PY'
import json, collections
rows = []
for f in ['crawl', 'scenario', 'extra']:
    for i in json.load(open(f'results/{f}_issues.json')):
        # الضغطات المتزامنة في أداة المحاكاة تتشارك سجل الرسائل ← «إجابة مزدوجة» وهمية
        if i['kind'] == 'double-answer' and any(w in i['where'] for w in ('متزامن', ' 2', 'رفض2', 'إلغاء 2')):
            continue
        rows.append((f, i['sev'], i['kind'], i['where'][:55], i['detail'][:110].replace('\n', ' ')))
c = collections.Counter(r[1] for r in rows)
print("SUMMARY", dict(c))
for r in sorted(rows, key=lambda r: ['critical', 'high', 'medium', 'low'].index(r[1])):
    print("  ", *r, sep=" | ")
PY
