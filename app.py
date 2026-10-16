from flask import Flask, render_template, request, jsonify, send_file, session
from datetime import datetime, timedelta
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import os
import json
import secrets
import re
from io import BytesIO

from scheduler import create_schedule


# =========================================================
# FLASK APPLICATION / SECURITY
# =========================================================

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25 MB upload limit
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=30)

try:  # optional: load settings from a .env file next to app.py
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

instance_dir = os.environ.get("REEXAM_INSTANCE_DIR") or os.path.join(app.root_path, "instance")
os.makedirs(instance_dir, exist_ok=True)
secret_file = os.path.join(instance_dir, "secret_key.txt")
if os.environ.get("SECRET_KEY"):
    app.secret_key = os.environ["SECRET_KEY"]
elif os.path.exists(secret_file):
    with open(secret_file, "r", encoding="utf-8") as f:
        app.secret_key = f.read().strip()
else:
    app.secret_key = secrets.token_hex(32)
    with open(secret_file, "w", encoding="utf-8") as f:
        f.write(app.secret_key)
    try:
        os.chmod(secret_file, 0o600)
    except OSError:
        pass

# Behind a reverse proxy, set TRUSTED_PROXY_COUNT so client IPs (rate limiting) are correct.
if os.environ.get("TRUSTED_PROXY_COUNT", "").isdigit() and int(os.environ["TRUSTED_PROXY_COUNT"]) > 0:
    from werkzeug.middleware.proxy_fix import ProxyFix
    _n = int(os.environ["TRUSTED_PROXY_COUNT"])
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=_n, x_proto=_n, x_host=_n)

# Sign in, registration, password recovery, logout, sessions, CSRF: see auth.py
from auth import init_auth, login_required  # noqa: E402

init_auth(app, instance_dir)


# =========================================================
# HOME PAGE
# =========================================================

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/reset-password")
def reset_password_page():
    # Link target of the password-reset email; the page reads ?token=... and shows the reset form.
    return render_template("index.html")




def convert_document_to_excel(source_path, extension, upload_folder, original_name):
    """Convert a tabular PDF/image to an Excel workbook for the existing scheduler."""
    import pandas as pd

    frames = []
    if extension == ".pdf":
        try:
            import pdfplumber
            with pdfplumber.open(source_path) as pdf:
                for page in pdf.pages:
                    for table in (page.extract_tables() or []):
                        if table and len(table) >= 2:
                            frames.append(pd.DataFrame(table[1:], columns=table[0]))
        except ImportError:
            pass

        # Scanned PDFs have no selectable text/table; use OCR as a fallback.
        if not frames:
            try:
                import fitz
                from PIL import Image
                import pytesseract
                import io
                doc = fitz.open(source_path)
                for page in doc:
                    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                    image = Image.open(io.BytesIO(pix.tobytes("png")))
                    frames.extend(_ocr_table_frames(image, pytesseract))
            except ImportError as exc:
                raise ValueError(
                    "This PDF appears to be scanned. Install the PDF/OCR dependencies "
                    "listed in requirements.txt and install Tesseract OCR, or upload a "
                    "text-based PDF/Excel file."
                ) from exc
    else:
        try:
            from PIL import Image
            import pytesseract
            image = Image.open(source_path).convert("RGB")
            frames = _ocr_table_frames(image, pytesseract)
        except ImportError as exc:
            raise ValueError(
                "PNG/JPEG OCR needs the dependencies in requirements.txt and the "
                "Tesseract OCR application installed on Windows."
            ) from exc
        except Exception as exc:
            raise ValueError(f"Could not read this image. Please upload a clear table image. Details: {exc}")

    valid_frame = None
    for frame in frames:
        if frame is None or frame.empty:
            continue
        frame.columns = [str(c).strip() if c is not None else "" for c in frame.columns]
        lower = {str(c).strip().lower() for c in frame.columns}
        has_code = any(c in lower for c in ("code", "subjectcode", "subject code", "course code"))
        has_name = any(c in lower for c in ("course name", "subjectname", "subject name", "name", "subject"))
        has_rolls = any(("roll" in c or "examno" in c or "registration" in c or "enrollment" in c or "student" in c) for c in lower)
        if has_code and has_name and has_rolls:
            valid_frame = frame
            break

    if valid_frame is None:
        raise ValueError(
            "The uploaded PDF/image was accepted, but its table could not be recognized as "
            "scheduler data. Upload a clear table containing subject Code, Course/Subject Name, "
            "and student roll-number columns. Excel input remains the most reliable option."
        )

    converted_path = os.path.join(
        upload_folder, os.path.splitext(original_name)[0] + "_converted.xlsx"
    )
    valid_frame.to_excel(converted_path, index=False)
    return converted_path


def _ocr_table_frames(image, pytesseract):
    """OCR a clear table image and split columns using the spacing in recognized lines."""
    import pandas as pd
    import re
    text = pytesseract.image_to_string(image, config="--psm 6")
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # Tables with aligned columns usually retain multiple spaces in OCR output.
        cells = [cell.strip() for cell in re.split(r"\t+|\s{2,}", line) if cell.strip()]
        if len(cells) >= 3:
            rows.append(cells)
    if len(rows) < 2:
        return []
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    header_index = next(
        (i for i, row in enumerate(rows[:8])
         if any("code" in c.lower() for c in row)
         and any(("name" in c.lower() or "subject" in c.lower() or "course" in c.lower()) for c in row)
         and any(("roll" in c.lower() or "examno" in c.lower() or "student" in c.lower()) for c in row)),
        None
    )
    if header_index is None:
        return []
    return [pd.DataFrame(rows[header_index + 1:], columns=rows[header_index])]


# =========================================================
# GENERATE RE-EXAM TIMETABLE
# =========================================================

@app.route("/generate", methods=["POST"])
@login_required
def generate():
    try:
        file = request.files.get("file")
        if not file:
            return jsonify({"success": False, "error": "Please upload an Excel, PDF, PNG, or JPEG database file."}), 400
        if file.filename == "":
            return jsonify({"success": False, "error": "Please select a valid input file."}), 400

        original_name = secure_filename(file.filename)
        extension = os.path.splitext(original_name)[1].lower()
        allowed_extensions = (".xlsx", ".xls", ".pdf", ".png", ".jpg", ".jpeg")
        if extension not in allowed_extensions:
            return jsonify({"success": False, "error": "Supported formats: Excel (.xlsx, .xls), PDF (.pdf), PNG (.png), and JPEG (.jpg, .jpeg)."}), 400

        upload_folder = os.path.join(app.root_path, "uploads")
        os.makedirs(upload_folder, exist_ok=True)
        uploaded_path = os.path.join(upload_folder, original_name)
        file.save(uploaded_path)
        file_path = uploaded_path
        if extension in (".pdf", ".png", ".jpg", ".jpeg"):
            try:
                file_path = convert_document_to_excel(uploaded_path, extension, upload_folder, original_name)
            except ValueError as exc:
                return jsonify({"success": False, "error": str(exc)}), 400

        start_date_text = request.form.get("start_date", "2026-10-26")
        if not start_date_text:
            return jsonify({"success": False, "error": "Please select an exam start date."}), 400
        try:
            start_date = datetime.strptime(start_date_text, "%Y-%m-%d").date()
        except ValueError:
            return jsonify({"success": False, "error": "Invalid date format. Please use YYYY-MM-DD."}), 400

        selected_course = request.form.get("course", "").strip()
        selected_year = request.form.get("year", "").strip()
        if not selected_course:
            return jsonify({"success": False, "error": "Please select a course."}), 400
        if not selected_year:
            return jsonify({"success": False, "error": "Please select an academic year."}), 400

        academic_session = request.form.get("academic_year", "2026-27").strip() or "2026-27"
        year_match = re.fullmatch(r"(\d{4})-(\d{2})", academic_session)
        if not year_match or (int(year_match.group(1)) + 1) % 100 != int(year_match.group(2)):
            return jsonify({"success": False, "error": "Academic year must look like 2026-27."}), 400
        selected_semester = request.form.get("semester", "Sem - 1").strip() or "Sem - 1"
        if selected_semester not in ("Sem - 1", "Sem - 2"):
            return jsonify({"success": False, "error": "Please select Sem - 1 or Sem - 2."}), 400

        mapping_text = request.form.get("course_year_start_dates", "{}")
        try:
            course_year_start_dates = json.loads(mapping_text)
            if not isinstance(course_year_start_dates, dict):
                raise ValueError
        except (ValueError, TypeError, json.JSONDecodeError):
            return jsonify({"success": False, "error": "Invalid course/year start-date configuration."}), 400

        selected_key = f"{selected_course}|{selected_year}"
        mapped_start_date = course_year_start_dates.get(selected_key)
        if not mapped_start_date:
            return jsonify({"success": False, "error": f"Please add a starting date for {selected_course} - {selected_year} before generating."}), 400
        try:
            start_date = datetime.strptime(str(mapped_start_date).strip(), "%Y-%m-%d").date()
        except ValueError:
            return jsonify({"success": False, "error": f"Invalid start date for {selected_course} - {selected_year}."}), 400

        try:
            morning_papers = int(request.form.get("morning_papers", "5"))
            afternoon_papers = int(request.form.get("afternoon_papers", "4"))
        except (ValueError, TypeError):
            return jsonify({"success": False, "error": "Morning and Afternoon paper counts must be numbers."}), 400
        if morning_papers <= 0 or afternoon_papers <= 0:
            return jsonify({"success": False, "error": "Morning and Afternoon papers must be greater than 0."}), 400

        # Optional student capacity per slot (blank = no student limit, which
        # keeps the original exam-count-only behaviour).
        student_capacities = {}
        for field, label in (("morning_students", "Morning"), ("afternoon_students", "Afternoon")):
            raw_value = str(request.form.get(field, "")).strip()
            if raw_value == "":
                student_capacities[field] = None
                continue
            if not raw_value.isdigit() or int(raw_value) <= 0:
                return jsonify({"success": False, "error": f"{label} student capacity must be a positive whole number."}), 400
            if int(raw_value) > 100000:
                return jsonify({"success": False, "error": f"{label} student capacity is too large (maximum 100000)."}), 400
            student_capacities[field] = int(raw_value)

        remove_labs = str(request.form.get("remove_labs", "true")).strip().lower() in ("true", "1", "yes", "on")

        output_folder = os.path.join(app.root_path, "generated_schedules")
        os.makedirs(output_folder, exist_ok=True)

        holiday_dates_text = request.form.get("holiday_dates", "[]")
        try:
            holiday_dates = json.loads(holiday_dates_text)
            if not isinstance(holiday_dates, list):
                raise ValueError
        except (ValueError, TypeError, json.JSONDecodeError):
            return jsonify({"success": False, "error": "Invalid holiday dates."}), 400

        validated_holidays = []
        for holiday_text in holiday_dates:
            try:
                holiday_date = datetime.strptime(str(holiday_text), "%Y-%m-%d").date()
            except ValueError:
                return jsonify({"success": False, "error": f"Invalid holiday date: {holiday_text}"}), 400
            if holiday_date not in validated_holidays:
                validated_holidays.append(holiday_date)

        result = create_schedule(
            excel_path=file_path,
            start_date=start_date,
            morning_capacity=morning_papers,
            afternoon_capacity=afternoon_papers,
            output_folder=output_folder,
            holidays=validated_holidays,
            remove_labs=remove_labs,
            morning_student_capacity=student_capacities["morning_students"],
            afternoon_student_capacity=student_capacities["afternoon_students"]
        )

        # Only the administrator may publish a timetable as Final. Other accounts always
        # generate Draft output, even if a crafted request submits another value.
        requested_status = str(request.form.get("timetable_status", "draft")).strip().lower()
        if requested_status not in ("draft", "final"):
            return jsonify({"success": False, "error": "Choose Draft Time Table or Final Time Table."}), 400
        timetable_status = requested_status if session.get("role") == "administrator" else "draft"
        _save_timetable_meta(output_folder, academic_session, selected_semester, timetable_status)

        return jsonify({
            "success": True,
            "academic_session": academic_session,
            "semester": selected_semester,
            "timetable_status": timetable_status,
            "students": result["students"],
            "subjects": result["subjects"],
            "exam_days": result["exam_days"],
            "schedule": result["schedule"],
            "cleaned_file": result["cleaned_file"],
            "timetable_file": result["timetable_file"],
            "morning_capacity": morning_papers,
            "afternoon_capacity": afternoon_papers,
            "morning_student_capacity": student_capacities["morning_students"],
            "afternoon_student_capacity": student_capacities["afternoon_students"],
            "unscheduled": result.get("unscheduled", []),
            "remove_labs": remove_labs,
            "course": selected_course,
            "year": selected_year,
            "start_date": start_date.strftime("%Y-%m-%d"),
            "course_year_start_dates": course_year_start_dates,
            "holidays": [d.strftime("%Y-%m-%d") for d in validated_holidays]
        })
    except Exception as e:
        print("\nERROR:", str(e))
        return jsonify({"success": False, "error": str(e)}), 500


# =========================================================
# DOWNLOADS
# =========================================================

@app.route("/download/timetable")
@login_required
def download_timetable():
    try:
        file_path = os.path.join(app.root_path, "generated_schedules", "generated_reexam_timetable.xlsx")
        if not os.path.exists(file_path):
            return "Timetable file not found. Please generate the timetable first.", 404
        return send_file(file_path, as_attachment=True, download_name="generated_reexam_timetable.xlsx")
    except Exception as e:
        return str(e), 500



def _save_timetable_meta(folder, academic_year, semester, timetable_status="draft"):
    """Remember title and academic details for the PDF and Word exports."""
    with open(os.path.join(folder, "timetable_meta.json"), "w", encoding="utf-8") as handle:
        json.dump({"academic_year": academic_year, "semester": semester,
                   "timetable_status": timetable_status}, handle)


def _load_timetable_meta():
    """Selected title and academic details (defaults to Draft for older schedules)."""
    try:
        with open(os.path.join(app.root_path, "generated_schedules", "timetable_meta.json"), encoding="utf-8") as handle:
            data = json.load(handle)
        status = str(data.get("timetable_status", "draft")).strip().lower()
        if status not in ("draft", "final"):
            status = "draft"
        return data.get("academic_year"), data.get("semester"), status
    except (OSError, ValueError, AttributeError):
        return None, None, "draft"


def _read_timetable_sheets(file_path):
    """Read the main timetable sheet for PDF/Word without rerunning scheduling."""
    from openpyxl import load_workbook
    workbook = load_workbook(file_path, read_only=True, data_only=True)
    try:
        worksheet = workbook["Timetable"] if "Timetable" in workbook.sheetnames else workbook.worksheets[0]
        rows = []
        for row in worksheet.iter_rows(values_only=True):
            values = ["" if value is None else str(value) for value in row]
            while values and not values[-1].strip():
                values.pop()
            if any(value.strip() for value in values):
                rows.append(values)
        if not rows:
            raise ValueError("The generated timetable workbook is empty.")
        headers = [value.strip() for value in rows[0]]
        required = ["Day & Date", "Time", "Branch", "Class", "Sem", "Course Name", "Code", "Count"]
        normalized = {name.casefold(): idx for idx, name in enumerate(headers)}
        if all(name.casefold() in normalized for name in required):
            indices = [normalized[name.casefold()] for name in required]
            result = [required]
            for row in rows[1:]:
                result.append([row[i] if i < len(row) else "" for i in indices])
        else:
            # Keep the first eight source columns as a safe fallback for compatible workbooks.
            result = [row[:8] + [""] * max(0, 8 - len(row[:8])) for row in rows]
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
        output = build_pdf(build_model(rows, *_load_timetable_meta()))
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
        output = build_docx(build_model(rows, *_load_timetable_meta()))
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
            host="127.0.0.1", port=5000)
