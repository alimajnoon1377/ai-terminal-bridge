# سناریوهای عملی

هر سناریو یک تست خودکار متناظر دارد که با SSH و پلِ واقعی (نه mock) اجرا می‌شود. مثال‌ها با `atb call` نوشته شده‌اند؛ دستیارها همان ابزارها را از راه MCP صدا می‌زنند.

```bash
export ATB_URL=http://127.0.0.1:8750 ATB_AGENT_TOKEN=TOKEN
```

(در نصب Docker: `docker compose exec -e ATB_AGENT_TOKEN=TOKEN bridge atb call ...`)

## ۱. رفع خطا: نوشتن، تست، اصلاح، commit و بازگردانی

دسترسی: `developer` روی `/srv/projects/demo`.

```bash
atb call write_file '{"server":"web1","path":"calc.py","content":"def add(a, b):\n    return a - b\n"}'
atb call run_tests '{"server":"web1","framework":"make"}'          # شکست می‌خورد
atb call search_files '{"server":"web1","pattern":"return a"}'
atb call edit_file '{"server":"web1","path":"calc.py","old_text":"a - b","new_text":"a + b"}'
atb call run_tests '{"server":"web1","framework":"make"}'          # موفق
atb call git_operations '{"server":"web1","operation":"init"}'
atb call git_operations '{"server":"web1","operation":"add","files":["calc.py"]}'
atb call git_operations '{"server":"web1","operation":"commit","message":"fix add"}'
atb call rollback_changes '{"server":"web1","change_id":"CHANGE_ID"}'
```

این مثال فرض می‌کند `Makefile` و فایل تست در پروژه هستند.
تست: `test_scenario_fix_a_bug_and_commit`

## ۲. نصب وابستگی: شبکه پیش‌فرض بسته است

در `developer` دستور شبکه ندارد؛ پس از روشن کردن `sandbox_network` دارد. تست فقط دسترسی شبکه را (به یک سرویس محلی) می‌سنجد؛ نصب بسته از اینترنت آزمایش نشده است.

```bash
atb call execute_command '{"server":"web1","command":"python3 -m venv .venv && .venv/bin/pip install -r requirements.txt","timeout_seconds":600}'
```

تست: `test_scenario_network_is_off_until_granted`

## ۳. عملیات مخرب با تأیید انسان

دسترسی: `operator`.

```bash
atb call execute_command '{"server":"web1","command":"rm old.log"}'
# {"status":"approval_required","approval_id":"..."}  ← انسان در داشبورد تأیید می‌کند
atb call get_approval '{"approval_id":"ID","wait_seconds":50}'
atb call execute_command '{"server":"web1","command":"rm old.log"}'   # همان فراخوانی
```

تست: `test_scenario_a_human_approves_a_destructive_command`

## ۴. کار طولانی و قطع اتصال

```bash
atb call execute_command '{"server":"web1","command":"make build","wait_seconds":2,"timeout_seconds":600}'
atb call disconnect_server '{"server":"web1"}'
atb call get_command_output '{"command_id":"ID","offset":0,"wait_seconds":50}'
```

تست‌ها: `test_scenario_long_command_survives_a_disconnect`، `test_job_survives_bridge_restart`

## ۵. چند سرور هم‌زمان

یک دستیار برای هر سرور دسترسی جدا می‌گیرد و فراخوانی‌ها را موازی می‌فرستد (از راه WebSocket یا درخواست‌های هم‌زمان). سروری که دسترسی ندارد برای دستیار «پیدا نمی‌شود».

تست: `test_scenario_two_servers_at_once` (دو sshd مستقل روی یک ماشین)

## ۶. توقف اضطراری

مدیر دکمه‌ی توقف را می‌زند: دستورهای در حال اجرا خاتمه می‌یابند، تأییدهای معلق لغو و فراخوانی‌های بعدی با `policy_denied` رد می‌شوند.

تست‌ها: `test_scenario_emergency_stop`، `test_kill_switch_stops_everything`

## ۷. بازگردانی کل پوشه

```bash
atb call create_snapshot '{"server":"web1","path":"/srv/projects/demo","label":"before refactor"}'
atb call rollback_changes '{"server":"web1","snapshot_id":"ID"}'   # تأیید می‌خواهد
```

محتوای جایگزین‌شده حذف نمی‌شود و کنار پوشه با پسوند `.atb-before-...` می‌ماند.
تست: `test_snapshot_restore_needs_approval_and_keeps_old_tree`

## اجرای تست‌ها

نیازمند Linux، `sshd`، `bubblewrap`، `git`، `make`، `tmux` و یک کاربر عادی:

```bash
pip install -e "./backend[dev]"
cd backend
ATB_TEST_USER=someuser python -m pytest      # وقتی تست‌ها با root اجرا می‌شوند
```

تست‌های Docker فقط با `ATB_TEST_DOCKER_IMAGE=<image>` اجرا می‌شوند و کاربر آزمایشی باید عضو گروه docker باشد.
