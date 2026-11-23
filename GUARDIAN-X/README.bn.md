# OMEGA Shield (OGX): ইউনিভার্সাল প্লাগ-অ্যান্ড-প্লে শিল্ড ও ডেভ কোর

**একটি ফোল্ডার** যেকোনো প্রজেক্টের রুটে ফেলুন, **একটি এন্ট্রি-পয়েন্ট** (`init.py`) দিয়ে চালান। শুধু **Python 3.11+**, কোনো থার্ড-পার্টি প্যাকেজ ইনস্টল করতে হয় না।

```
python omega-shield/init.py status
```

| স্তম্ভ | ফোল্ডার | কী পাবেন |
|---|---|---|
| **সুরক্ষা** | `guard/` | কমিট/পুশ/CI গেট, read-only সিকিউরিটি স্ক্যানার, **রানটাইম রিকোয়েস্ট-গার্ড** (রিকোয়েস্ট ভ্যালিডেশন, অ্যাটাক-সিগনেচার, rate limit, IP পলিসি, API-key/role অ্যাক্সেস কন্ট্রোল): WSGI/ASGI middleware অথবা যেকোনো ভাষার অ্যাপের জন্য রিভার্স-প্রক্সি |
| **ডেভেলপমেন্ট** | `devkit/` | হেলথ চেক, **কেন্দ্রীয় স্ট্রাকচার্ড লগিং**, **error tracking**, ডিবাগ ইউটিলিটি, AI-এজেন্ট কিট, রানবুক |
| **সংযোগ** | `link/` | *ঐচ্ছিক* ফাইল (Copilot, এজেন্ট-গাইড, এডিটর-রুল, Dependabot/Renovate): আপনি না বাছলে কিছুই তৈরি হয় না |

## দ্রুত শুরু
```
python omega-shield/init.py scan --strict            # read-only স্ক্যান; high/critical হলে exit 1
python omega-shield/init.py audit                    # হেলথ + সিকিউরিটি + মেইনটেন্যান্স রিপোর্ট
python omega-shield/init.py hooks install --confirm  # git pre-commit গেট চালু
python omega-shield/init.py guard test "/s?q=<script>alert(1)</script>"      # রিকোয়েস্ট-গার্ড পরীক্ষা (exit 1 = ব্লক)
python omega-shield/init.py proxy --upstream http://127.0.0.1:3000           # যেকোনো অ্যাপ গার্ড করুন (পরীক্ষামূলক)
python omega-shield/init.py logs                     # কেন্দ্রীয় লগ-ইভেন্ট
python omega-shield/init.py link plan                # কী কী সংযুক্ত করা যায় (কিছুই বদলায় না)
```
Python অ্যাপে এমবেড করতে (স্থায়ীভাবে `sys.path` না বদলে):
```python
import runpy
shield = runpy.run_path("omega-shield/init.py")["load_runtime"]()
app = shield.ShieldMiddleware(app)       # WSGI; ASGI-র জন্য shield.ShieldASGI(app)
```
Windows-এ `python`-এর বদলে `py -3` ব্যবহার করুন (যেমন `py -3 omega-shield\init.py status`)।

## জিরো-কনফ্লিক্ট নিশ্চয়তা
1. নিজের অবস্থান `__file__` থেকে খোঁজে: কোন ডিরেক্টরি থেকে চালালেন, `PYTHONPATH` কী, হোস্টের লেআউট কেমন, কিছুই বিষয় নয়।
2. `--confirm` ছাড়া হোস্টের ভেতরে কিছু লেখা হয় না (git hook ও `link` ছাড়া)।
3. `.pyc` লেখা হয় না, স্থায়ী `sys.path` বদল নেই, শুধু `omega.*` লগার কনফিগার হয়।
4. রিপোর্ট, লগ ও এরর-ডেটা থাকে প্রজেক্টের **বাইরে**, প্রতি-হোস্টের আলাদা স্টেট-ফোল্ডারে (`init.py status` দেখায়)।
5. শিল্ড-ফোল্ডার নিজের স্ক্যান থেকে বাদ; নামে কোনো স্পেস নেই।

## ফোল্ডার-কাঠামো
```
omega-shield/
├─ init.py  shield.toml  VERSION  README(.bn).md  LICENSE  SECURITY.md  CHANGELOG.md  AGENTS.md ...
├─ core/ogx/   kernel · connectors · analysis · shield · devkit · governance · ecosystem · runtime · cli   (ভেন্ডর্ড, শুধু stdlib)
├─ guard/      gates/  policies/  runtime/
├─ devkit/     health/  observability/  agents/  runbooks/
├─ link/       templates/
├─ docs/  schemas/  scripts/
└─ _dev/  .github/     টেস্ট, টুল, রিলিজ-উপাদান, এই রিপোর নিজস্ব CI (ড্রপ-ইনে থাকে না)
```
বিস্তারিত: `docs/ARCHITECTURE.md` · ইন্টিগ্রেশন: `docs/INTEGRATION.md` · রানটাইম গার্ড: `docs/RUNTIME_GUARD.md` · 0.7 থেকে মাইগ্রেশন: `docs/MIGRATION.md`।

## সৎ সীমাবদ্ধতা
* স্ক্যানার heuristic (স্ট্যাটিক রুল; data-flow বিশ্লেষণ বা DAST নেই)। পরিষ্কার স্ক্যান মানে নিরাপদ, এমন নয়।
* রানটাইম গার্ড হলো defence-in-depth, **ম্যানেজড WAF-এর বিকল্প নয়**; multipart বডি পরীক্ষা হয় না; প্রক্সি পরীক্ষামূলক (HTTP/1.1; TLS/WebSocket/chunked রিকোয়েস্ট নেই)।
* governance RBAC-এর identity যাচাই হয় না (লোকাল প্রোটোটাইপ)।
* পরীক্ষা হয়েছে Linux + Python 3.12-এ। Windows ও Python 3.11-এ চলার কথা (OS-নির্ভর কোড যোগ করা হয়নি), তবে এখানে চালিয়ে দেখা হয়নি।

টেস্ট: `PYTHONPATH=core python -m unittest discover -s _dev/tests` · লিন ড্রপ-ইন জিপ বানাতে: `python _dev/tools/make_dist.py`।
