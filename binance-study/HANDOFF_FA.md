# Handoff برای ادامهٔ Binance Quant Research Lab

این سند یک handoff عملیاتی برای ایجنت بعدی است؛ وضعیت‌ها را از فایل‌های همین شاخه می‌گیرد و ادعا نمی‌کند کاری که pending است اجرا شده. پیش از هر پردازش، `AGENTS.md` را کامل بخوانید و وضعیت محیط/worker را از نو بررسی کنید.

## محل فعلی و نسخهٔ پشتیبان

- ریموت: `https://github.com/itman224/top-100-crypto-points.git`
- شاخهٔ پشتیبان: `backup/binance-study-recovery`
- پروژه داخل مخزن: `binance-study/`
- آخرین commit پشتیبانِ پیش از افزودن این اسناد: `c11667258fc03b60b8819d54500854123ed98a19`؛ commit همین اسناد پس از push در تاریخچهٔ شاخه خواهد بود.
- در نوبت پشتیبان‌گیری، ۳٬۶۲۹ مسیر (فایل و symlink) با تطبیق SHA-256 و اندازه منتقل شد؛ mismatch صفر بود. محتوا ۲۱۵٬۷۳۴٬۸۶۹ بایت بود. هیچ فایل پروژه‌ای به‌خاطر `.gitignore` حذف نشد و هیچ فایل بزرگ‌تر از حد ۱۰۰ MB وجود نداشت؛ Git LFS لازم نبود. مخزن `.git` تو در توی مسیر مبدأ عمداً از کپی کنار گذاشته شد، چون مقصد Git repository مستقل خود را دارد. مبدأ قبل از آن commit نیز دست‌نخورده ماند.
- چند symlink در `lab/.venv` حفظ شده‌اند؛ یکی به مفسر بیرون از tree یعنی `/opt/codex/runtimes/codex-primary-runtime/dependencies/python/bin/python3` اشاره می‌کند. در checkout جدید venv را از نو بسازید.

## مأموریت و تاریخچهٔ تصمیم‌ها

مأموریت اولیه، تحلیل ۱۰۰ نماد Binance USDT-M perpetual بر پایهٔ حجم quote تاریخی مارس ۲۰۲۶، با رویدادهای candle-body اکیدتر از ±۱٪ در 5m/15m/1h/4h/1d بود. درخواست‌ها در ادامه به بازهٔ پایان ۲۷ اکتبر ۲۰۲۶ گسترش یافتند و الزام کردند از API مستقیم exchange استفاده نشود. S3 listing رسمی گهگاه HTTP 503 داد؛ برای کشف نمادها از snapshot عمومی Hugging Face استفاده شد، اما کندل‌های گزارش‌شده در provenance خروجی از آرشیو رسمی Binance هستند. HF نباید به‌عنوان ranking رسمی یا پوشش بدون survivorship bias معرفی شود.

بعد از مرحلهٔ استخراج، کاربر پروژه را به Quant Research Lab گسترش داد: نصب نسخهٔ ثابت pandas-ta-classic، benchmark causal، non-event controls، معاملات پس از هزینه، ارزیابی زمانی، ترکیب‌ها، پاسخ به پرسش‌های پژوهشی و dashboard. بعداً policy دائمی اجرای batch/checkpoint و عدم کاهش دامنه ثبت شد. درخواست فعلیِ پیش از handoff، فقط افزودن راهنمای فارسی، README راه‌اندازی و handoff و push همین اسناد بود؛ پژوهش/داده در این مرحله اجرا نشده است.

## وضعیت دادهٔ استخراج‌شده

فایل مرجع: `output/summary.json`؛ universe: `output/universe.json`؛ آزمون نهایی: `output/validation.json`؛ جزئیات: `EXECUTION_LEDGER.md`.

- خروجی آخر ثبت‌شده: ۱۰۰/۱۰۰ نماد، ۸۰۰/۸۰۰ پارتیشن نماد-ماه و ۶٬۰۴۸٬۰۰۰ از ۶٬۰۴۸٬۰۰۰ کندل 5m در بازه. تعداد کندل‌های مشتق‌شدهٔ تایم‌فریم‌ها نیز در `validated_candle_counts_by_timeframe` است.
- تعداد رویدادهای ثبت‌شده: ۴۶۳٬۲۲۰؛ ۲۳۶٬۳۴۳ صعودی و ۲۲۶٬۸۷۷ نزولی؛ از جمله فایل‌های `output/events/*/*.jsonl.gz` و `output/source_manifest.json.gz`.
- universe از ۵۳۰ کاندیدای snapshot ساخته شد؛ ۵۰۹ کاندیدا رتبه‌بندی شدند و ۲۱ شکست خوردند. انتخاب ۱۰۰ نماد مشروط به این snapshot است؛ قراردادهای delisted ممکن است غایب باشند.
- `summary.json` همهٔ کندل‌های دوره را complete و صفر missing ثبت می‌کند؛ archive counters تعداد ۱۰۰ فایل ماهانهٔ ناموجود را نیز نشان می‌دهد که با آرشیوهای روزانه پوشش یافته‌اند. این دو نوع نبود فایل را با missing candle اشتباه نگیرید.
- Manifest تعداد ۳٬۲۵۰ منبع verified و ۸۹۹ منبع inferred دارد؛ URLهای inferred از convention مسیرند و دسترسی مستقل‌شان تأیید نشده است.
- `output/validation.json` وضعیت `passed_with_warnings`، بدون error و با هشدار `close_timestamp_validation_pending` برای ۱۳ symbol-month دارد. فایل‌های دیگر/گزارش‌های قبلی بعضی close timestampها را verified می‌خوانند. پیش از ادعای تأیید کامل، این ناسازگاری گزارش‌ها و checkpointهای همان ۱۳ پارتیشن را تعیین تکلیف و validation/report را دوباره بسازید.
- `output/resource_stats` در این خروجی runtime و peak memory را ثبت نکرده؛ این مقادیر را حدس نزنید.

## وضعیت benchmark و شاخه‌های پژوهشی

نسخه و catalog pandas-ta-classic در `lab/coverage_report.json`: v0.9.0، git revision `9c1e0ca16f1decf2cb39823f0e229e17d97bafb2`. گزارش شمارش ۲۲۴ قابلیت دسته‌ای و ۶۲ نام الگوی candle دارد؛ `cdl_pattern` و نام patternها واحدهای مستقل قابل جمع نیستند.

- نسخهٔ اولیه روی ۱۰۰ نماد جمع شده: ۱۴ variant از ۱۱ خانوادهٔ قاعدهٔ جهت‌دار و دو filter. ۱۰ خانواده مستقیماً تابع pandas-ta-classic داشتند؛ Donchian-style و relative volume دستی بودند. این محدودیت انتخاب اولیهٔ مهندسی/زمان اجرا بود، نه نتیجهٔ رد علمی capabilityهای دیگر.
- نتیجهٔ اولیه در `lab/results/results.json` و تحلیل روش در `lab/VALIDATION.md` است. فایل validation می‌گوید ۱۰۰ نماد و ۶۹۰ رکورد تجمیعی reconcile شده‌اند. این نتایج جای benchmark همهٔ library را نمی‌گیرند.
- enrichment رویدادها در `lab/results/event_features/`: ۴۶۳٬۲۲۰ event ID با فایل مرجع تطبیق داده شده؛ ۴۶۳٬۱۳۰ کامل و ۹۰ دارای warm-up ناقص‌اند. Warm-up ناقص را neutral یا عدم رویداد حساب نکنید.
- TP/SL sensitivity موجود در `lab/results/risk_summary.json` برای ۱٪ stop / ۲٪ take، هزینهٔ رفت‌وبرگشت ۰٫۱٪، funding حذف‌شده و stop-first در برخورد مبهم را ثبت می‌کند. پنج پیکربندی منتخب mean net منفی در holdout اکتشافی دارند. MFE/MAE full-bar ممکن است بعد از خروج رخ دهد. هیچ استراتژی تأییدشدهٔ deployable/سودآور گزارش نشده است.
- Holdout سپتامبر تا اکتبر قبلاً مشاهده شده؛ دیگر holdout دست‌نخورده برای تأیید نیست. نتایج آن را exploratory بنویسید و روی آن tune نکنید.
- v2، فایل `lab/benchmark_v2.py`، ۵۲ تابع/فرضیهٔ جهت‌دار با تعریف صریح signal دارد و خروجی BTC موجود است (`lab/results/benchmark_v2/symbols/BTCUSDT.json.gz`: ۵۲ فرضیه، ۳٬۶۴۰ رکورد ماهانه، صفر خطای ثبت‌شده در همان فایل). با وجود آن، `lab/results/benchmark_v2/execution_ledger.json` اکنون ۰ completed / ۰ failed / ۱۰۰ pending نشان می‌دهد، مدت/ETA ثبت‌شده ندارد و `persistent_scheduler_active=false` است. فایل BTC را بدون اعتبارسنجی fingerprint و پنج timeframe به‌عنوان تکمیل ledger حساب نکنید.
- هنوز ۲۲۴ capability به‌طور کامل benchmark نشده‌اند؛ ترکیب‌ها و ۱۰–۲۰ استراتژی خارج‌ازنمونهٔ پذیرفته‌شده تولید نشده‌اند. ابتدا semantic/casuality rules را تعریف کنید؛ ورودی‌های صرفاً transform را بی‌دلیل به سیگنال تبدیل نکنید. الگوریتم‌ها و patterns را کوچک/قابل‌بازتولید batch کنید، نه brute-force.
- `lab/walkforward.py` و `lab/ui/` وجود دارند؛ نتایج walk-forward سراسری را به‌عنوان کامل فرض نکنید تا ledger و فایل‌های aggregate نتیجهٔ اجرا را نشان دهند. Dashboard محلی پیش‌فرض روی 127.0.0.1:8765 است؛ deployment عمومی انجام نشده است.

## کارهای ثبت‌شده در گفت‌وگو و این نوبت

1. پیاده‌سازی استخراج، universe شرطی بر snapshot HF، candle aggregation، event partitionها، manifest و گزارش‌ها در `binance-study/` نگهداری شده است.
2. benchmark اولیه، enrichment رویداد و sensitivity ریسک طبق فایل‌های `lab/VALIDATION.md` و `lab/results/` اجرا شده‌اند؛ caveatهای holdout و هزینه‌ها بالا ثبت شده است.
3. برای تکمیل catalog ساخته شدند: `lab/coverage_v2.py`, `lab/coverage_report.json`, `lab/benchmark_v2.py`, `lab/batch_runner_v2.py`, `lab/walkforward.py` و تست‌هایشان؛ اما اجرای ۱۰۰ نماد در v2 هنوز ledger-level pending است.
4. dashboard، API `/api/research-v2` و صفحهٔ تحقیق را از داده‌ها/ledger موجود می‌خواند؛ محلی است و خروجی v2 خالی/pending را نباید به‌عنوان رتبه‌بندی کامل نمایش داد.
5. پروژه به `itman224/top-100-crypto-points` در branch `backup/binance-study-recovery` پشتیبان‌گیری شد و remote branch تأیید شد.
6. همین مرحله `README.md` را به راهنمای فارسی نصب/اجرا تبدیل می‌کند و دو فایل `FILE_GUIDE_FA.md` و `HANDOFF_FA.md` را اضافه می‌کند؛ پس از این commit، همین branch محل handoff تازه است.

ساب‌ایجنت‌های قبلی که در orchestration ثبت شده بودند شامل coverage، benchmark_v2، statistics/walk-forward، batch orchestration، dashboard و audit مستقل بودند. وضعیت فعال بودن آن‌ها بعد از پایان/تعویض محیط تأیید نشده است؛ فرض نکنید worker یا scheduler هنوز در حال کار است. مدل‌های آن‌ها در artifactهای حاضر ثبت نشده‌اند و در این handoff حدس زده نمی‌شوند.

## راه شروع ایمن برای ایجنت بعدی

1. `AGENTS.md`, `README.md`, همین فایل، `EXECUTION_LEDGER.md`, `lab/VALIDATION.md`, `lab/coverage_report.json` و هر دو ledger را بخوانید.
2. از branch `backup/binance-study-recovery` شروع کنید؛ `git status`, شاخه، commit، فضای دیسک، RAM/cgroup و `ps` را بررسی کنید. PID فایل قدیمی دلیل زنده بودن worker نیست.
3. محیط را با فرمان README بازسازی کنید؛ venv منتقل‌شده ممکن است به Python مطلق قدیمی اشاره کند.
4. تست‌های معتبر موجود را اجرا کنید؛ تغییرهای مهندسی را commit و پیش از push، `git diff --check` و وضعیت خروجی‌ها را بازبینی کنید.
5. ابتدا اختلاف `validation.json`/`summary.json` در close-time status را حل کنید، بدون حذف checkpointهای معتبر.
6. `batch_runner_v2.py --dry-run` را اجرا کنید. سپس با یک worker آغاز کنید و پس از یک batch، ledger، کیفیت loader/CRC، missing indicator-timeframe-direction، RAM، disk و resume را مستقل بررسی کنید. تنها پس از آن همهٔ ۱۰۰ نماد را خودکار به ترتیب ادامه دهید.
7. دامنه را به همهٔ capabilityهای قابل‌استفاده گسترش دهید؛ status برای هر capability باید یکی از اجراشده، نیازمند معنای سیگنال، غیرجهت‌دار، نیازمند بازبینی causal، وابسته‌به‌داده/پارامتر یا خطاداده باشد. عدم آزمون را با نتیجهٔ صفر یا عدم فایده یکی نکنید.
8. بعد از اجرای پایدار، بسازید: aggregate LONG/SHORT به تفکیک timeframe؛ false positives از کندل‌های پیوستهٔ بدون event؛ آزمون paired و confidence intervals/multiple-testing controls؛ walk-forward زمانی و پایداری بر نماد/ماه/regime؛ هزینه، slippage، funding یا صریحاً نبود آن؛ و backtest ترکیب‌ها با کنترل overlap/capital. Holdout قدیمی برای انتخاب مجدد مجاز نیست.
9. Dashboard را به aggregateهای واقعاً ایجادشده متصل کنید و تعداد pending/failed، provenance و محدودیت‌ها را قابل‌مشاهده نگه دارید.
10. Ledger باید تعداد کل/تکمیل/در حال اجرا/pending/failed batch، زمان و resource واقعی، ETA مبتنی بر سرعت مشاهده‌شده، resume command و وضعیت scheduler واقعی را ذخیره کند. اکنون scheduler دائمی فعال ثبت نشده؛ ادعای background continuation نکنید.

## دستورهای دقیق ادامه

در checkout همین شاخه:

```sh
cd /workspace/top-100-crypto-points/binance-study
lab/.venv/bin/python lab/batch_runner_v2.py --dry-run
lab/.venv/bin/python lab/batch_runner_v2.py
```

این فرمان فقط v2 ثبت‌شده را اجرا می‌کند و تکمیل کل مأموریت را تضمین نمی‌کند. برای پردازش داده‌های پایه از README و checkpointهای `output/checkpoints/` پیروی کنید؛ universe را بی‌دلیل دوباره نسازید. پس از هر تغییر، ledger و summary/validation را با اعداد واقعی بازتولید و push کنید.
