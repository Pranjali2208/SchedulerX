# Re-Exam Scheduler

Flask app that builds a conflict-free re-exam timetable from an uploaded Excel
registration file (graph colouring), exporting a timetable workbook.

## Run
```
pip install -r requirements.txt
python app.py          # http://127.0.0.1:5000
```
First start creates `instance/` (account files and secret key; never commit or share it) and
the administrator account **admin / admin** (or `ADMIN_USERNAME` / `ADMIN_PASSWORD` from the
environment/`.env`). Change the default password after the first sign in
(sidebar -> *Change Password*). Set `FLASK_DEBUG=1` only for local development.

## Accounts, sign in and password recovery
Code: `auth.py` (routes under `/auth/...`), UI in `templates/index.html`. Storage is unchanged:
JSON files in `instance/` (`admin_credentials.json`, `registered_users.json`).

* **Register** – full name, email, password, confirm password. Public registration always creates a
  normal user, never an administrator. Passwords: 8-128 chars with upper-case, lower-case and a digit,
  enforced on the server and stored only as hashes.
* **Sign in** – registered email or the administrator username, plus password. Sessions are also tracked
  server-side, so logout, a password change or a password reset ends them for real.
  Every POST needs the CSRF token (the page handles this automatically).
* **No recovery answer** – the security question/answer was removed everywhere. Old `recovery_answer_hash`
  values are deleted automatically at start-up (or run `python manage.py migrate`); accounts and
  passwords are untouched.
* **Forgot password** – the user enters their email, receives a link
  (`/reset-password?token=...`), and sets a new password. Tokens are random, single-use, expire after
  `RESET_TOKEN_MINUTES` (default 30), only their SHA-256 hash is stored, and a newer request replaces the
  older token. The response is identical whether or not the address is registered. Signed-in users can also
  use *Change Password* (asks for the current password).
* **Rate limiting** – sign-in (per IP and per account), registration, forgot/reset endpoints. It is
  in-process: fine for the default single-process server; use a shared limiter if you run many workers.

### Configure password-reset email
Email is enabled only when `SMTP_HOST`, `MAIL_FROM` and `APP_BASE_URL` are all set (copy `.env.example` to
`.env`): `SMTP_PORT` (587), `SMTP_SECURITY` (`starttls` | `ssl` | `none`), `SMTP_USERNAME`, `SMTP_PASSWORD`,
`APP_BASE_URL` (public address used in the link, e.g. `https://scheduler.example.edu`).
The administrator account has no email address of its own: set `ADMIN_EMAIL` to receive its reset link.
Without email configured the app says so honestly and sends nothing. The server operator can then create a
one-time link instead:
```
python manage.py reset-link someone@example.com      # or: python manage.py reset-link admin
```
Behind HTTPS set `COOKIE_SECURE=1`; behind a reverse proxy set `TRUSTED_PROXY_COUNT` (number of proxies).

## Student Capacity-Based Scheduling (new)
On the generator form, below *Morning/Afternoon Papers Per Day*, set:

- **Morning Student Capacity** and **Afternoon Student Capacity** – the maximum
  number of registered students in that slot on one day.
  Must be a positive whole number (1–100000). **Blank = no student limit**, which
  keeps the original exam-count-only behaviour exactly.

Priority rules (both are hard limits):
1. **Student capacity (first)** – each slot is filled with the combination of exams
   whose unique registered-student total is as close as possible to the capacity
   without exceeding it.
2. **Exam capacity (second)** – a slot never holds more than *Papers Per Day* exams.

Details
- Student counts = unique roll numbers registered per subject in the uploaded Excel
  (a roll number repeated within a subject counts once).
- Exact dynamic-programming selection per slot; ties go to fewer exams (keeps small
  exams free for later slots), then alphabetical code, so results are deterministic.
- Conflict-free grouping (graph colouring) and year-based Morning/Afternoon placement
  are unchanged; packing is applied inside each conflict-free group.
- An exam whose own student count exceeds its slot's capacity is **not scheduled** and
  is reported in the result message (code, students, capacity). Nothing is silently dropped.
- The timetable shows each subject's student count and each slot's total.

## Tests
```
python -m unittest discover -s tests -v
```
`tests/test_auth.py` covers registration, sign in, logout, CSRF, forgot/reset password and rate limits.


## Supported input formats

The upload field accepts Excel (`.xlsx`, `.xls`), PDF (`.pdf`), PNG (`.png`), and JPEG (`.jpg`, `.jpeg`) files. Excel is recommended for best accuracy. PDF and image inputs must be clear tables with subject/course code, subject/course name, and student roll-number columns. Text-based PDF tables are extracted directly; scanned PDFs and images use OCR and may need manual checking.

Install Python dependencies from this project folder:

```powershell
pip install -r requirements.txt
```

For scanned PDFs and PNG/JPEG OCR, install the **Tesseract OCR application** on Windows as well, and make sure `tesseract.exe` is available on PATH. OCR quality depends on image clarity, resolution, and table alignment. If OCR cannot identify the required columns, use the Excel version of the database for reliable scheduling.


## Draft / Final timetable title
The administrator can choose **Draft Time Table** or **Final Time Table** in the generator dashboard. The selected title is saved with the generated schedule and is applied to both PDF and Word exports. Older schedules default to Draft. Non-administrator accounts cannot publish a timetable as Final.
