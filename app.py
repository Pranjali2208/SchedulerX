"""Flask web application for the Re-Exam Scheduler."""
import json
import os
import tempfile
import threading
from datetime import datetime

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, send_file
from openpyxl import load_workbook
from werkzeug.utils import secure_filename

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INSTANCE_DIR = os.environ.get("REEXAM_INSTANCE_DIR") or os.path.join(BASE_DIR, "instance")
GENERATED_DIR = os.path.join(BASE_DIR, "generated_schedules")
ALLOWED_EXTENSIONS = {".xlsx", ".xls"}

os.makedirs(INSTANCE_DIR, exist_ok=True)
os.makedirs(GENERATED_DIR, exist_ok=True)


def _generated_dir():
    return os.path.join(app.root_path, "generated_schedules")


def _meta_file():
    return os.path.join(_generated_dir(), "timetable_meta.json")

app = Flask(__name__, instance_path=INSTANCE_DIR)
app.config.update(
    MAX_CONTENT_LENGTH=25 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=30 * 24 * 3600,
)


def _load_secret_key():
    key = os.environ.get("SECRET_KEY", "").strip()
    if key:
        return key
    path = os.path.join(INSTANCE_DIR, "secret_key.txt")
    try:
        with open(path, encoding="utf-8") as fh:
            key = fh.read().strip()
        if key:
            return key
    except OSError:
        pass
    import secrets
    key = secrets.token_hex(32)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(key)
    except OSError:
        pass
    return key


app.secret_key = _load_secret_key()

import auth  # noqa: E402
from auth import login_required, current_session  # noqa: E402

auth.init_auth(app, INSTANCE_DIR)

# Render/other reverse proxies: trust forwarded headers (HTTPS, client IP)
_proxies = int(os.environ.get("TRUSTED_PROXY_COUNT", "0") or 0)
if _proxies > 0:
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=_proxies, x_proto=_proxies, x_host=_proxies)

_generate_lock = threading.Lock()


@app.route("/")
@app.route("/reset-password")
def index():
    return render_template("index.html")


@app.route("/healthz")
def healthz():
    return "ok", 200


def _save_timetable_meta(meta):
    with open(_meta_file(), "w", encoding="utf-8") as fh:
        json.dump(meta, fh)


def _load_timetable_meta():
    """Return (academic_year, semester, exam_type, status) of the last generated timetable."""
    try:
        with open(_meta_file(), encoding="utf-8") as fh:
            meta = json.load(fh)
    except (OSError, ValueError):
        meta = {}
    return (meta.get("academic_year", ""), meta.get("semester", ""),
            meta.get("exam_type", ""), meta.get("timetable_status", "draft"))


def _parse_clock(value):
    """'HH:MM' (24h, from <input type=time>) -> 'HH:MM' or None."""
    try:
        return datetime.strptime((value or "").strip()[:5], "%H:%M").strftime("%H:%M")
    except ValueError:
        return None


def _load_slot_times():
    """Administrator-entered examination timings of the last generated timetable, or None."""
    try:
        with open(_meta_file(), encoding="utf-8") as fh:
            return json.load(fh).get("slot_times")
    except (OSError, ValueError):
        return None


@app.route("/generate", methods=["POST"])
@login_required
def generate():
    import pandas as pd
    from scheduler import create_schedule

    upload = request.files.get("file")
    if not upload or not upload.filename:
        return jsonify({"success": False, "error": "Please upload a database file."}), 400
    ext = os.path.splitext(upload.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({"success": False,
                        "error": "Unsupported file type. Please upload an Excel (.xlsx or .xls) file."}), 400

    form = request.form
    try:
        morning = int(form.get("morning_papers", "5"))
        afternoon = int(form.get("afternoon_papers", "4"))
        if morning <= 0 or afternoon <= 0:
            raise ValueError
    except ValueError:
        return jsonify({"success": False, "error": "Paper capacity must be a positive whole number."}), 400

    def _student_cap(name):
        raw = (form.get(name) or "").strip()
        if not raw:
            return None
        if not raw.isdigit() or not 1 <= int(raw) <= 100000:
            raise ValueError(name)
        return int(raw)

    try:
        morning_students = _student_cap("morning_students")
        afternoon_students = _student_cap("afternoon_students")
    except ValueError:
        return jsonify({"success": False,
                        "error": "Each student capacity must be a whole number between 1 and 100000."}), 400

    try:
        start_date = pd.to_datetime(form.get("start_date", "")).date()
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "Please provide a valid start date."}), 400

    try:
        holidays = json.loads(form.get("holiday_dates") or "[]")
        if not isinstance(holidays, list):
            holidays = []
    except ValueError:
        holidays = []

    slot_times = {}
    for slot in ("morning", "afternoon"):
        start, end = _parse_clock(form.get(slot + "_start")), _parse_clock(form.get(slot + "_end"))
        if not start or not end:
            return jsonify({"success": False, "error": "Please enter both the %s examination start time and end time." % slot.capitalize()}), 400
        if end <= start:
            return jsonify({"success": False, "error": "%s examination end time must be later than the start time." % slot.capitalize()}), 400
        slot_times[slot] = [start, end]

    remove_labs = (form.get("remove_labs", "").lower() in ("1", "true", "yes", "on"))
    status = (form.get("timetable_status") or "draft").lower()
    sess = current_session() or {}
    if status != "final" or sess.get("role") != "administrator":
        status = "draft"

    with _generate_lock, tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, secure_filename(upload.filename) or ("upload" + ext))
        upload.save(path)
        try:
            result = create_schedule(
                path, start_date, morning_capacity=morning, afternoon_capacity=afternoon,
                output_folder=_generated_dir(), holidays=holidays, remove_labs=remove_labs,
                morning_student_capacity=morning_students,
                afternoon_student_capacity=afternoon_students,
            )
        except ValueError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        except Exception as exc:
            app.logger.exception("Timetable generation failed")
            return jsonify({"success": False, "error": "Could not generate timetable: " + str(exc)}), 500

    academic_year = (form.get("academic_year") or "").strip()
    semester = (form.get("semester") or "").strip()
    exam_type = (form.get("exam_type") or "").strip()
    _save_timetable_meta({"academic_year": academic_year, "semester": semester,
                          "exam_type": exam_type, "timetable_status": status, "slot_times": slot_times,
                          "generated_at": datetime.now().isoformat(timespec="seconds")})

    return jsonify({
        "success": True,
        "students": result["students"],
        "subjects": result["subjects"],
        "exam_days": result["exam_days"],
        "schedule": result["schedule"],
        "unscheduled": result.get("unscheduled", []),
        "morning_capacity": morning,
        "afternoon_capacity": afternoon,
        "morning_student_capacity": morning_students,
        "afternoon_student_capacity": afternoon_students,
        "course": form.get("course", ""),
        "year": form.get("year", ""),
        "academic_session": academic_year,
        "semester": semester,
        "start_date": str(start_date),
        "holidays": holidays,
        "remove_labs": remove_labs,
        "timetable_status": status,
    })


@app.route("/download/timetable")
@login_required
def download_timetable():
    file_path = os.path.join(_generated_dir(), "generated_reexam_timetable.xlsx")
    if not os.path.exists(file_path):
        return "Timetable file not found. Please generate the timetable first.", 404
    return send_file(file_path, as_attachment=True, download_name="generated_reexam_timetable.xlsx")


def _read_timetable_sheets(file_path):
    """Return [(sheet_name, rows)] with the 8 export columns of the main Timetable sheet."""
    workbook = load_workbook(file_path, read_only=True, data_only=True)
    try:
        sheet = workbook["Timetable"] if "Timetable" in workbook.sheetnames else workbook.worksheets[0]
        rows = [["" if c is None else str(c) for c in row] for row in sheet.iter_rows(values_only=True)]
        required = ["Day & Date", "Time", "Branch", "Class", "Sem", "Course Name", "Code", "Count"]
        header = rows[0] if rows else []
        if all(name in header for name in required):
            indices = [header.index(name) for name in required]

            result = [required]
            sem_index = required.index("Sem")

            semester_mapping = {
                "1": "I",
                "2": "II",
                "Sem - 1": "Sem - I",
                "Sem - 2": "Sem - II",
                "Sem - I": "Sem - I",
                "Sem - II": "Sem - II",
            }

            for row in rows[1:]:
                output_row = [
                    row[i] if i < len(row) else ""
                    for i in indices
                ]

                semester_value = output_row[sem_index].strip()

                output_row[sem_index] = semester_mapping.get(
                    semester_value,
                    semester_value,
                )

                result.append(output_row)

        else:
            # Keep the first eight source columns as a safe fallback.
            result = [
                row[:8] + [""] * max(0, 8 - len(row[:8]))
                for row in rows
            ]

        return [("Timetable", result)]

    finally:
        workbook.close()



@app.route("/download/timetable.pdf")
@login_required
def download_timetable_pdf():
    file_path = os.path.join(app.root_path, "generated_schedules", "generated_reexam_timetable.xlsx")
    if not os.path.exists(file_path):
        return "Timetable file not found. Please generate the timetable first.", 404
    try:
        from timetable_export import build_model, build_pdf
        rows = _read_timetable_sheets(file_path)[0][1]
        academic_year, semester, exam_type, status = _load_timetable_meta()
        model = build_model(
            rows,
            academic_year=academic_year,
            semester=semester,
            timetable_status=status,
            exam_type=exam_type,
            slot_times=_load_slot_times()
        )
        output = build_pdf(model)
        return send_file(output, mimetype="application/pdf", as_attachment=True,
                         download_name="generated_reexam_timetable.pdf")
    except Exception as exc:
        app.logger.exception("PDF timetable export failed")
        return jsonify({"success": False, "error": "Could not generate PDF timetable. " + str(exc)}), 500


@app.route("/download/timetable.docx")
@login_required
def download_timetable_docx():
    file_path = os.path.join(app.root_path, "generated_schedules", "generated_reexam_timetable.xlsx")
    if not os.path.exists(file_path):
        return "Timetable file not found. Please generate the timetable first.", 404
    try:
        from timetable_export import build_model, build_docx
        rows = _read_timetable_sheets(file_path)[0][1]
        academic_year, semester, exam_type, status = _load_timetable_meta()
        model = build_model(
            rows,
            academic_year=academic_year,
            semester=semester,
            timetable_status=status,
            exam_type=exam_type,
            slot_times=_load_slot_times()
        )
        output = build_docx(model)
        return send_file(output, mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                         as_attachment=True, download_name="generated_reexam_timetable.docx")
    except Exception as exc:
        app.logger.exception("Word timetable export failed")
        return jsonify({"success": False, "error": "Could not generate Word timetable. " + str(exc)}), 500


@app.route("/download/cleaned")
@login_required
def download_cleaned():
    try:
        file_path = os.path.join(app.root_path, "generated_schedules", "cleaned_reexam_database.xlsx")
        if not os.path.exists(file_path):
            return "Cleaned database file not found. Please generate the timetable first.", 404
        return send_file(file_path, as_attachment=True, download_name="cleaned_reexam_database.xlsx")
    except Exception as e:
        return str(e), 500


if __name__ == "__main__":
    # Set FLASK_DEBUG=1 for development (auto-reload + debugger). Keep it off in production.
    app.run(debug=os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "yes"),
            host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "5000")))
