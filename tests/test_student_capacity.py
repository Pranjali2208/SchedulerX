"""Tests for student-capacity-based scheduling.

Run from the project folder:  python -m unittest discover -s tests -v
"""
import io
import os
import random
import sys
import tempfile
import unittest
from datetime import date

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)

import _appenv  # noqa: E402,F401  (isolates account files; must precede "import app")
import scheduler  # noqa: E402
from scheduler import (  # noqa: E402
    generate_schedule, pack_subjects_into_slots, select_best_subset,
)


def make_subjects(sizes):
    """sizes: {code: n_students}; every student is unique per subject."""
    subject_students = {}
    n = 0
    for code, size in sizes.items():
        subject_students[code] = {f"R{n + i}" for i in range(size)}
        n += size
    return subject_students


def run(sizes, m_exams=5, a_exams=4, m_stu=None, a_stu=None, slots=None):
    report = {}
    schedule, _ = generate_schedule(
        {}, make_subjects(sizes), slots or {}, m_exams, a_exams,
        {c: c for c in sizes},
        morning_student_capacity=m_stu, afternoon_student_capacity=a_stu,
        report=report,
    )
    return schedule, report


class SelectionTests(unittest.TestCase):
    def test_spec_example_picks_A_to_E(self):
        counts = {"A": 80, "B": 65, "C": 55, "D": 50, "E": 40, "F": 35}
        chosen = select_best_subset(list(counts), counts, 5, 300)
        self.assertEqual(sorted(chosen), ["A", "B", "C", "D", "E"])
        self.assertEqual(sum(counts[c] for c in chosen), 290)

    def test_prefers_better_student_fit_over_largest_first(self):
        # Largest-first greedy would take A (70) and stop at 70 students.
        # The best fit is B + C = 100, exactly the capacity.
        counts = {"A": 70, "B": 50, "C": 50}
        chosen = select_best_subset(list(counts), counts, 3, 100)
        self.assertEqual(sorted(chosen), ["B", "C"])

    def test_student_capacity_beats_exam_count(self):
        # Two exams give 100 students; three small exams give only 30.
        counts = {"A": 60, "B": 40, "C": 10, "D": 10, "E": 10}
        chosen = select_best_subset(list(counts), counts, 3, 100)
        self.assertEqual(sum(counts[c] for c in chosen), 100)

    def test_exam_cap_is_hard_limit(self):
        counts = {f"S{i}": 10 for i in range(10)}
        chosen = select_best_subset(list(counts), counts, 3, 1000)
        self.assertEqual(len(chosen), 3)

    def test_deterministic(self):
        counts = {"A": 10, "B": 10, "C": 10, "D": 10}
        first = select_best_subset(list(counts), counts, 2, 20)
        for _ in range(5):
            self.assertEqual(select_best_subset(list(counts), counts, 2, 20), first)

    def test_random_property_never_exceeds_limits_and_places_each_once(self):
        rng = random.Random(7)
        for _ in range(200):
            counts = {f"S{i}": rng.randint(1, 120) for i in range(rng.randint(1, 14))}
            cap = rng.randint(50, 400)
            exams = rng.randint(1, 6)
            slots, oversized = pack_subjects_into_slots(list(counts), counts, exams, cap)
            placed = [c for s in slots for c in s]
            self.assertEqual(len(placed), len(set(placed)))
            self.assertEqual(sorted(placed + oversized), sorted(counts))
            for s in slots:
                self.assertLessEqual(len(s), exams)
                self.assertLessEqual(sum(counts[c] for c in s), cap)
            self.assertEqual(sorted(oversized),
                             sorted(c for c, n in counts.items() if n > cap))


class GenerateScheduleTests(unittest.TestCase):
    SIZES = {"A": 80, "B": 65, "C": 55, "D": 50, "E": 40, "F": 35}

    def test_first_morning_slot_matches_example(self):
        schedule, report = run(self.SIZES, m_exams=5, m_stu=300, a_stu=250)
        first = schedule[0]["morning"]
        self.assertEqual(sorted(first), ["A", "B", "C", "D", "E"])
        self.assertEqual(schedule[0]["morning_students"], 290)
        self.assertEqual(report["unscheduled"], [])

    def test_all_scheduled_once_and_limits_respected(self):
        schedule, report = run(self.SIZES, m_exams=2, m_stu=130, a_stu=130)
        seen = [c for d in schedule for c in d["morning"] + d["afternoon"]]
        self.assertEqual(sorted(seen), sorted(self.SIZES))
        for d in schedule:
            self.assertLessEqual(len(d["morning"]), 2)
            self.assertLessEqual(d["morning_students"], 130)

    def test_morning_and_afternoon_use_own_configuration(self):
        slots = {"A": "morning", "B": "morning", "C": "afternoon", "D": "afternoon"}
        sizes = {"A": 60, "B": 40, "C": 30, "D": 20}
        schedule, report = run(sizes, m_exams=5, a_exams=1, m_stu=100, a_stu=50,
                               slots=slots)
        for d in schedule:
            self.assertLessEqual(d["morning_students"], 100)
            self.assertLessEqual(d["afternoon_students"], 50)
            self.assertLessEqual(len(d["afternoon"]), 1)
        self.assertEqual(schedule[0]["morning_students"], 100)  # A + B together
        self.assertEqual(report["unscheduled"], [])

    def test_oversized_exam_reported_not_scheduled(self):
        sizes = {"BIG": 500, "A": 20, "B": 30}
        schedule, report = run(sizes, m_stu=100, a_stu=100)
        seen = [c for d in schedule for c in d["morning"] + d["afternoon"]]
        self.assertNotIn("BIG", seen)
        self.assertEqual([u["code"] for u in report["unscheduled"]], ["BIG"])
        self.assertIn("exceed", report["unscheduled"][0]["reason"])
        self.assertEqual(report["unscheduled"][0]["students"], 500)

    def test_without_student_capacity_matches_legacy_exam_chunking(self):
        sizes = {f"S{i:02d}": 10 + i for i in range(12)}
        schedule, report = run(sizes, m_exams=5)
        seen = [c for d in schedule for c in d["morning"] + d["afternoon"]]
        self.assertEqual(sorted(seen), sorted(sizes))
        for d in schedule:
            self.assertLessEqual(len(d["morning"]), 5)
        self.assertEqual(report["unscheduled"], [])

    def test_invalid_student_capacity_rejected(self):
        for bad in (0, -5, 2.5, "10", True):
            with self.assertRaises(ValueError):
                run(self.SIZES, m_stu=bad)

    def test_duplicate_students_counted_once(self):
        # subject_students holds sets, so a repeated roll number counts once.
        subject_students = {"A": {"1", "2", "2", "3"}}
        report = {}
        generate_schedule({}, subject_students, {}, 5, 4, {"A": "A"},
                          morning_student_capacity=3, report=report)
        self.assertEqual(report["student_counts"]["A"], 3)
        self.assertEqual(report["unscheduled"], [])


class EndToEndTests(unittest.TestCase):
    """Real Excel file -> create_schedule, and the Flask /generate route."""

    DB = os.path.join(PROJECT, "database.xlsx")

    def test_create_schedule_with_real_excel(self):
        with tempfile.TemporaryDirectory() as out:
            result = scheduler.create_schedule(
                self.DB, date(2026, 10, 26), 5, 4, output_folder=out,
                morning_student_capacity=12, afternoon_student_capacity=9)
            self.assertTrue(os.path.exists(result["timetable_file"]))
            scheduled = [s["code"] for d in result["schedule"]
                         for s in d["morning"] + d["afternoon"]]
            self.assertEqual(len(scheduled), len(set(scheduled)))
            self.assertEqual(len(scheduled) + len(result["unscheduled"]),
                             result["subjects"])
            for d in result["schedule"]:
                self.assertLessEqual(d["morning_students"], 12)
                self.assertLessEqual(d["afternoon_students"], 9)
                self.assertLessEqual(len(d["morning"]), 5)
                self.assertLessEqual(len(d["afternoon"]), 4)

    def _client(self, tmp):
        import _appenv
        import app as flask_app
        flask_app.app.root_path = tmp
        os.makedirs(os.path.join(tmp, "uploads"), exist_ok=True)
        # Sign in through the real endpoint (server-side session + CSRF token).
        client, token = _appenv.csrf_client(flask_app)
        self.assertEqual(_appenv.login(client, token).status_code, 200)
        client.environ_base["HTTP_X_CSRF_TOKEN"] = token
        return client

    def _post(self, client, **extra):
        data = {
            "file": (open(self.DB, "rb"), "database.xlsx"),
            "course": "B.Tech", "year": "Second Year",
            "start_date": "2026-10-26",
            "course_year_start_dates": '{"B.Tech|Second Year": "2026-10-26"}',
            "morning_papers": "5", "afternoon_papers": "4",
            "remove_labs": "false", "holiday_dates": "[]",
        }
        data.update(extra)
        return client.post("/generate", data=data,
                           content_type="multipart/form-data")

    def test_route_rejects_invalid_values_and_accepts_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = self._client(tmp)
            for bad in ("0", "-3", "abc", "2.5", "1000000"):
                r = self._post(client, morning_students=bad)
                self.assertEqual(r.status_code, 400, bad)
                self.assertIn("student capacity", r.get_json()["error"])
            r = self._post(client, afternoon_students="-1")
            self.assertEqual(r.status_code, 400)
            r = self._post(client, morning_students="12", afternoon_students="9")
            self.assertEqual(r.status_code, 200, r.get_json())
            body = r.get_json()
            self.assertEqual(body["morning_student_capacity"], 12)
            self.assertEqual(body["afternoon_student_capacity"], 9)
            self.assertIn("unscheduled", body)
            r = self._post(client)  # blank = legacy behaviour
            self.assertEqual(r.status_code, 200)
            self.assertIsNone(r.get_json()["morning_student_capacity"])


if __name__ == "__main__":
    unittest.main()
