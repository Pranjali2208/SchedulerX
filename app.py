
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
            exam_type=exam_type
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
            exam_type=exam_type
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
            host="127.0.0.1", port=5000)
