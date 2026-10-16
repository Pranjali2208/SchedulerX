import re
import os

from datetime import date, timedelta

from collections import defaultdict

import pandas as pd

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill, Border, Side


# =========================================================
# 1. ROLL NUMBER VALIDATION
# =========================================================

ROLL_NUMBER_PATTERN = re.compile(
    r"^\d{6,8}$"
)


# =========================================================
# 2. FIND ROLL NUMBER COLUMNS
# =========================================================

def find_roll_number_columns(df):

    roll_columns = []

    for col in df.columns:

        col_name = str(
            col
        ).strip().lower()

        if (

            "roll" in col_name

            or

            "student" in col_name

            or

            "enrollment" in col_name

            or

            "registration" in col_name

            or

            "examno" in col_name

        ):

            roll_columns.append(

                col

            )

    return roll_columns


# =========================================================
# 3. FIND SUBJECT NAME COLUMN
# =========================================================

def find_subject_name_column(df):

    possible_columns = [

        "Name",

        "SubjectName",

        "Subject Name",

        "Course Name",

        "Subject",

        "Course",

        "Title"

    ]


    # -----------------------------------------------------
    # EXACT MATCH
    # -----------------------------------------------------

    for col in possible_columns:

        if col in df.columns:

            return col


    # -----------------------------------------------------
    # CASE-INSENSITIVE MATCH
    # -----------------------------------------------------

    for actual_col in df.columns:

        actual_lower = str(

            actual_col

        ).strip().lower()


        for possible in possible_columns:

            if (

                actual_lower

                ==

                possible.lower()

            ):

                return actual_col


    return None


# =========================================================
# 4. CLEAN EXCEL DATA
# =========================================================

def remove_lab_courses(df):
    """Remove lab/practical course rows from an uploaded Excel dataframe."""
    lab_keywords = [
        "LAB", "LABORATORY", "PRACTICAL", "PRACTICALS",
        "WORKSHOP", "STUDIO"
    ]
    candidate_columns = []
    for col in df.columns:
        name = str(col).strip().lower()
        if any(word in name for word in (
            "code", "subject", "course", "name", "title", "type", "category"
        )):
            candidate_columns.append(col)

    if not candidate_columns:
        return df.copy(), 0

    def is_lab_row(row):
        for col in candidate_columns:
            value = str(row.get(col, "")).strip().upper()
            if not value or value == "NAN":
                continue
            for keyword in lab_keywords:
                if re.search(r"\b" + re.escape(keyword) + r"\b", value):
                    return True
        return False

    mask = df.apply(is_lab_row, axis=1)
    removed = int(mask.sum())
    return df.loc[~mask].copy(), removed


def clean_excel_data(

    df,

    code_col="Code"

):

    print(

        "\nCleaning Excel data..."

    )


    # -----------------------------------------------------
    # REMOVE EMPTY ROWS
    # -----------------------------------------------------

    df = df.dropna(

        how="all"

    ).copy()


    # -----------------------------------------------------
    # REMOVE EXACT DUPLICATES
    # -----------------------------------------------------

    before_duplicates = len(

        df

    )


    df = df.drop_duplicates()


    removed_duplicates = (

        before_duplicates

        -

        len(df)

    )


    print(

        f"Exact duplicate rows removed: "
        f"{removed_duplicates}"

    )


    # -----------------------------------------------------
    # CHECK CODE COLUMN
    # -----------------------------------------------------

    if code_col not in df.columns:

        raise ValueError(

            f"'{code_col}' column not found.\n"

            f"Available columns: "
            f"{list(df.columns)}"

        )


    # -----------------------------------------------------
    # CONVERT CODE TO STRING
    # -----------------------------------------------------

    df[code_col] = (

        df[code_col]

        .fillna("")

        .astype(str)

        .str.strip()

    )


    # -----------------------------------------------------
    # FIND ROLL NUMBER COLUMNS
    # -----------------------------------------------------

    roll_columns = find_roll_number_columns(

        df

    )


    print(

        "Roll number columns found:",

        roll_columns

    )


    if not roll_columns:

        raise ValueError(

            "No Roll Number columns were found.\n"

            "Please check your Excel file."

        )


    # -----------------------------------------------------
    # REMOVE DUPLICATE REGISTRATIONS
    # -----------------------------------------------------

    seen_registrations = set()

    rows_to_keep = []


    for index, row in df.iterrows():

        subject_code = str(

            row[code_col]

        ).strip()


        if not subject_code:

            continue


        student_rolls = []


        for col in roll_columns:

            value = row[col]


            if pd.isna(value):

                continue


            value = str(

                value

            ).strip()


            if ROLL_NUMBER_PATTERN.match(

                value

            ):

                student_rolls.append(

                    value

                )


        registration_keys = []


        for roll in student_rolls:

            key = (

                roll,

                subject_code

            )


            registration_keys.append(

                key

            )


        has_new_registration = False


        for key in registration_keys:

            if key not in seen_registrations:

                has_new_registration = True

                seen_registrations.add(

                    key

                )


        if (

            has_new_registration

            or

            not student_rolls

        ):

            rows_to_keep.append(

                index

            )


    cleaned_df = df.loc[

        rows_to_keep

    ].copy()


    print(

        "Rows after cleaning:",

        len(cleaned_df)

    )


    return cleaned_df


# =========================================================
# 5. LOAD STUDENT REGISTRATIONS
# =========================================================

def load_registrations_from_excel(

    path,

    code_col="Code",

    remove_labs=False

):

    print(

        "\n========================================"

    )

    print(

        "READING EXCEL FILE"

    )

    print(

        "========================================"

    )


    print(

        "\nFile:"

    )

    print(

        path

    )


    # -----------------------------------------------------
    # READ EXCEL
    # -----------------------------------------------------

    df = pd.read_excel(

        path,

        dtype=str

    )


    print(

        "\nExcel loaded successfully!"

    )

    if remove_labs:
        df, removed_lab_rows = remove_lab_courses(df)
        print(f"Automatic lab removal enabled. Rows removed: {removed_lab_rows}")
    else:
        print("Automatic lab removal disabled.")


    print(

        "\nAvailable columns:"

    )


    print(

        df.columns.tolist()

    )


    # -----------------------------------------------------
    # FIND SUBJECT NAME COLUMN
    # -----------------------------------------------------

    name_col = find_subject_name_column(

        df

    )


    if name_col:

        print(

            "Subject name column found:",

            name_col

        )

    else:

        print(

            "WARNING: Subject name column not found."

        )


    # -----------------------------------------------------
    # NORMALIZE COMMON DATABASE COLUMN ALIASES
    # -----------------------------------------------------

    code_aliases = ["Code", "SubjectCode", "Subject Code"]
    for alias in code_aliases:
        if alias in df.columns:
            code_col = alias
            break

    # -----------------------------------------------------
    # CLEAN DATA
    # -----------------------------------------------------

    df = clean_excel_data(

        df,

        code_col

    )


    # -----------------------------------------------------
    # FIND ROLL COLUMNS
    # -----------------------------------------------------

    roll_columns = find_roll_number_columns(

        df

    )


    # =====================================================
    # DSA DATA STRUCTURES
    # =====================================================

    # Student -> Set of Subjects
    student_subjects = defaultdict(

        set

    )


    # Subject -> Set of Students
    subject_students = defaultdict(

        set

    )


    # Subject Code -> Subject Name
    subject_names = {}

    # Subject Code -> fixed exam slot derived from ClassName/Year.
    subject_slots = {}
    subject_classes = defaultdict(set)


    # -----------------------------------------------------
    # PROCESS EACH EXCEL ROW
    # -----------------------------------------------------

    for _, row in df.iterrows():

        subject_code = str(

            row[code_col]

        ).strip()


        if (

            not subject_code

            or

            subject_code.lower() == "nan"

        ):

            continue


        # -------------------------------------------------
        # GET SUBJECT NAME
        # -------------------------------------------------

        subject_name = ""


        if name_col:

            value = row[name_col]


            if not pd.isna(value):

                subject_name = str(

                    value

                ).strip()


        # If subject name is missing,
        # use subject code

        if (

            not subject_name

            or

            subject_name.lower() == "nan"

        ):

            subject_name = subject_code


        subject_names[

            subject_code

        ] = subject_name

        # -------------------------------------------------
        # DETERMINE FIXED EXAM SLOT FROM ACADEMIC YEAR
        # -------------------------------------------------
        # Year information may be stored in Branch (for example
        # S.Y.B.Tech., T.Y.B.Tech., F.Y. M.B.A.) or in Class/Year.
        # Keep all available academic labels so the fixed morning/afternoon
        # rule can be applied correctly.
        class_values = []
        for class_col in ("Branch", "ClassName", "Class", "Year", "Course"):
            if class_col in df.columns and not pd.isna(row[class_col]):
                value = str(row[class_col]).strip()
                if value and value.lower() != "nan":
                    class_values.append(value)
        if class_values:
            subject_classes[subject_code].add(" | ".join(class_values))


        # -------------------------------------------------
        # FIND STUDENTS
        # -------------------------------------------------

        for col in roll_columns:

            cell = row[col]


            if pd.isna(cell):

                continue


            roll_number = str(

                cell

            ).strip()


            # -------------------------------------------------
            # VALIDATE ROLL NUMBER
            # -------------------------------------------------

            if ROLL_NUMBER_PATTERN.match(

                roll_number

            ):

                # Student -> Subject

                student_subjects[

                    roll_number

                ].add(

                    subject_code

                )


                # Subject -> Student

                subject_students[

                    subject_code

                ].add(

                    roll_number

                )


    # Resolve slot after reading all rows because a subject may appear
    # in more than one class/year.
    # Required rule:
    #   First Year + Third Year -> Morning (10:30am-1:30pm)
    #   Second Year + Final Year -> Afternoon (2:30pm-5:30pm)
    # The input Excel can use full names or F.Y./S.Y./T.Y. abbreviations.
    import re

    def academic_slot_from_class(class_text):
        text = str(class_text).lower().strip()
        # Full names first.
        if re.search(r'\bfirst\s*year\b', text):
            return "morning"
        if re.search(r'\bthird\s*year\b', text):
            return "morning"
        if re.search(r'\bsecond\s*year\b', text):
            return "afternoon"
        if re.search(r'\b(final|fourth)\s*year\b', text):
            return "afternoon"
        # Common abbreviations: F.Y., S.Y., T.Y.
        if re.search(r'\bf\s*\.\s*y\s*\.?\b', text) or re.search(r'\bfy\b', text):
            return "morning"
        if re.search(r'\bt\s*\.\s*y\s*\.?\b', text) or re.search(r'\bty\b', text):
            return "morning"
        if re.search(r'\bs\s*\.\s*y\s*\.?\b', text) or re.search(r'\bsy\b', text):
            return "afternoon"
        return None

    for subject_code, classes in subject_classes.items():
        detected_slots = [
            academic_slot_from_class(class_value)
            for class_value in sorted(classes)
        ]
        detected_slots = [slot for slot in detected_slots if slot]

        has_morning = "morning" in detected_slots
        has_afternoon = "afternoon" in detected_slots

        if has_morning and has_afternoon:
            print(
                f"WARNING: {subject_code} is shared by morning and afternoon "
                f"years ({sorted(classes)}). Scheduling it in the morning."
            )

        # Keep the working backend's safe fallback for unknown year labels.
        subject_slots[subject_code] = (
            "morning" if has_morning or not has_afternoon else "afternoon"
        )

    student_subjects = dict(

        student_subjects

    )


    subject_students = dict(

        subject_students

    )


    print(

        "\nTotal students:",

        len(student_subjects)

    )


    print(

        "Total subjects:",

        len(subject_students)

    )


    return (

        student_subjects,

        subject_students,

        subject_names,

        subject_slots,

        df

    )


# =========================================================
# 6. BUILD CONFLICT GRAPH
# =========================================================

def build_conflict_graph(

    subject_students

):

    print(

        "\nBuilding Subject Conflict Graph..."

    )


    subjects = sorted(

        subject_students.keys()

    )


    # -----------------------------------------------------
    # GRAPH REPRESENTATION
    # -----------------------------------------------------
    #
    # Graph:
    #
    # Subject = Vertex
    #
    # Conflict = Edge
    #
    # If two subjects have at least one common student,
    # they cannot be conducted on the same exam day.
    #
    # -----------------------------------------------------

    conflict_graph = {

        subject: set()

        for subject in subjects

    }


    # -----------------------------------------------------
    # COMPARE EVERY PAIR OF SUBJECTS
    # -----------------------------------------------------

    for i in range(

        len(subjects)

    ):

        subject_a = subjects[i]


        students_a = subject_students[

            subject_a

        ]


        for j in range(

            i + 1,

            len(subjects)

        ):

            subject_b = subjects[j]


            students_b = subject_students[

                subject_b

            ]


            # -------------------------------------------------
            # SET INTERSECTION
            # -------------------------------------------------

            common_students = (

                students_a

                &

                students_b

            )


            # -------------------------------------------------
            # ADD GRAPH EDGE
            # -------------------------------------------------

            if common_students:

                conflict_graph[

                    subject_a

                ].add(

                    subject_b

                )


                conflict_graph[

                    subject_b

                ].add(

                    subject_a

                )


    total_edges = sum(

        len(neighbours)

        for neighbours in conflict_graph.values()

    ) // 2


    print(

        "Total graph vertices:",

        len(conflict_graph)

    )


    print(

        "Total conflict edges:",

        total_edges

    )


    return conflict_graph


# =========================================================
# 7. GREEDY GRAPH COLORING
# =========================================================

def greedy_graph_coloring(

    conflict_graph

):

    print(

        "\nRunning Greedy Graph Coloring..."

    )


    # -----------------------------------------------------
    # SORT SUBJECTS BY DEGREE
    # -----------------------------------------------------
    #
    # Highest degree subjects are scheduled first.
    #
    # This is called Largest Degree First strategy.
    #
    # -----------------------------------------------------

    subjects = sorted(

        conflict_graph.keys(),

        key=lambda subject: (

            -len(

                conflict_graph[

                    subject

                ]

            ),

            subject

        )

    )


    colors = {}


    # -----------------------------------------------------
    # ASSIGN COLOR TO EACH SUBJECT
    # -----------------------------------------------------

    for subject in subjects:

        used_colors = {

            colors[neighbour]

            for neighbour

            in conflict_graph[subject]

            if neighbour in colors

        }


        color = 0


        while color in used_colors:

            color += 1


        colors[

            subject

        ] = color


    total_colors = (

        max(

            colors.values(),

            default=-1

        )

        + 1

    )


    print(

        "Greedy coloring used",

        total_colors,

        "exam days."

    )


    return colors


# =========================================================
# 8. BACKTRACKING GRAPH COLORING
# =========================================================

def backtracking_graph_coloring(

    conflict_graph,

    max_colors

):

    subjects = sorted(

        conflict_graph.keys(),

        key=lambda subject: (

            -len(

                conflict_graph[

                    subject

                ]

            ),

            subject

        )

    )


    colors = {}


    # -----------------------------------------------------
    # CHECK WHETHER A SUBJECT CAN USE A COLOR
    # -----------------------------------------------------

    def is_safe(

        subject,

        color

    ):

        for neighbour in conflict_graph[

            subject

        ]:

            if colors.get(

                neighbour

            ) == color:

                return False


        return True


    # -----------------------------------------------------
    # BACKTRACKING FUNCTION
    # -----------------------------------------------------

    def solve(

        index

    ):

        if index == len(subjects):

            return True


        subject = subjects[index]


        # Try each available color

        for color in range(

            max_colors

        ):

            if is_safe(

                subject,

                color

            ):

                colors[

                    subject

                ] = color


                if solve(

                    index + 1

                ):

                    return True


                # Backtrack

                del colors[

                    subject

                ]


        return False


    if solve(

        0

    ):

        print(

            "Backtracking coloring successful."

        )

        return colors


    return None


# =========================================================
# 9. FINAL GRAPH COLORING
# =========================================================

def color_conflict_graph(

    conflict_graph,

    enable_exact_backtracking=False,

    backtracking_vertex_limit=60

):

    if not conflict_graph:
        return {}

    greedy_colors = greedy_graph_coloring(conflict_graph)
    greedy_day_count = max(greedy_colors.values(), default=-1) + 1
    vertex_count = len(conflict_graph)

    # Exact graph coloring is exponential. Do not run it on the
    # large real-world dataset (273 subjects in the supplied file).
    if not enable_exact_backtracking or vertex_count > backtracking_vertex_limit:
        print(
            f"Skipping exact backtracking; using fast greedy coloring "
            f"for {vertex_count} subjects."
        )
        print(f"Greedy coloring used {greedy_day_count} exam days.")
        return greedy_colors

    for color_count in range(1, greedy_day_count):
        print(f"Trying backtracking with {color_count} days...")
        result = backtracking_graph_coloring(conflict_graph, color_count)
        if result is not None:
            print(f"Better coloring found with {color_count} days.")
            return result

    print("Using greedy coloring result.")
    print(f"Greedy coloring used {greedy_day_count} exam days.")
    return greedy_colors


# =========================================================
# 10A. STUDENT-CAPACITY-AWARE SLOT PACKING
# =========================================================
# Priority 1: total registered students in a slot must be as close
#             as possible to (never above) the student capacity.
# Priority 2: number of exams in a slot never exceeds the exam capacity.
# Both are hard limits; student fill is what is optimised.

def select_best_subset(codes, student_counts, max_exams, max_students):
    """Pick the subset of ``codes`` for ONE slot.

    Maximises the total number of students without exceeding
    ``max_students`` and without using more than ``max_exams`` exams.
    Ties are broken by (1) fewer exams (keeps small exams available for
    later slots), then (2) alphabetical subject codes, so the result is
    deterministic. Exact dynamic programming over (exam count, students).
    """
    codes = sorted(codes)
    # state (exam_count, total_students) -> tuple of chosen codes
    states = {(0, 0): ()}
    for code in codes:
        size = student_counts.get(code, 0)
        if size > max_students:
            continue
        additions = {}
        for (count, total), chosen in states.items():
            if count + 1 > max_exams or total + size > max_students:
                continue
            key = (count + 1, total + size)
            if key not in states and key not in additions:
                additions[key] = chosen + (code,)
        states.update(additions)

    best_key = max(states, key=lambda k: (k[1], -k[0]))
    return list(states[best_key])


def pack_subjects_into_slots(codes, student_counts, max_exams, max_students):
    """Split ``codes`` into slot-days using student capacity first.

    Returns (slots, oversized). ``oversized`` lists codes whose own student
    count exceeds the capacity (they cannot be scheduled without breaking
    the limit, so they are reported instead).
    """
    oversized = sorted(
        c for c in codes if student_counts.get(c, 0) > max_students
    )
    remaining = [c for c in codes if c not in oversized]
    slots = []
    while remaining:
        chosen = select_best_subset(
            remaining, student_counts, max_exams, max_students
        )
        if not chosen:
            break
        slots.append(chosen)
        remaining = [c for c in remaining if c not in chosen]
    return slots, oversized


# =========================================================
# 10. GENERATE SCHEDULE USING GRAPH COLORING
# =========================================================

def generate_schedule(

    student_subjects,

    subject_students,

    subject_slots=None,

    morning_capacity=5,

    afternoon_capacity=4,

    subject_names=None,

    morning_student_capacity=None,

    afternoon_student_capacity=None,

    report=None

):

    for label, value in (
        ("Morning", morning_student_capacity),
        ("Afternoon", afternoon_student_capacity)
    ):
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or value <= 0
        ):
            raise ValueError(
                f"{label} student capacity must be a positive whole number."
            )

    student_capacities = {
        "morning": morning_student_capacity,
        "afternoon": afternoon_student_capacity
    }
    student_counts = {
        code: len(students)
        for code, students in subject_students.items()
    }
    unscheduled = []

    def record_unscheduled(codes, slot):
        for code in codes:
            unscheduled.append({
                "code": code,
                "name": str((subject_names or {}).get(code, code)),
                "students": student_counts.get(code, 0),
                "slot": slot,
                "student_capacity": student_capacities[slot],
                "reason": (
                    f"{student_counts.get(code, 0)} registered students "
                    f"exceed the {slot} student capacity of "
                    f"{student_capacities[slot]}."
                )
            })

    def pack_slot(codes, slot, exam_capacity):
        """Chunk codes into slot-days (legacy slicing if no student cap)."""
        cap = student_capacities[slot]
        if cap is None:
            return [
                codes[i:i + exam_capacity]
                for i in range(0, len(codes), exam_capacity)
            ]
        slots, oversized = pack_subjects_into_slots(
            codes, student_counts, exam_capacity, cap
        )
        record_unscheduled(oversized, slot)
        return slots

    if morning_capacity <= 0:

        raise ValueError(

            "Morning papers must be greater than 0."

        )


    if afternoon_capacity <= 0:

        raise ValueError(

            "Afternoon papers must be greater than 0."

        )


    # -----------------------------------------------------
    # SPECIAL END-OF-SCHEDULE SUBJECTS
    # -----------------------------------------------------
    # Engineering Graphics and Scholastic Aptitude are always
    # placed after all regular examination days. Both use the
    # morning slot. Engineering Graphics uses a fixed time
    # of 10.30am to 02.30pm during export.
    # -----------------------------------------------------

    if subject_names is None:
        subject_names = {}

    def is_engineering_graphics(code):
        name = str(subject_names.get(code, code)).strip().lower()
        return (
            "engineering graphics" in name
            or str(code).upper() in {"SH1135", "SH1136"}
        )

    def is_scholastic_aptitude(code):
        name = str(subject_names.get(code, code)).strip().lower()
        return "scholastic aptitude" in name

    special_graphics = sorted(
        [code for code in subject_students if is_engineering_graphics(code)]
    )
    special_aptitude = sorted(
        [
            code for code in subject_students
            if is_scholastic_aptitude(code) and code not in special_graphics
        ]
    )
    special_subjects = set(special_graphics + special_aptitude)

    regular_subject_students = {
        code: students
        for code, students in subject_students.items()
        if code not in special_subjects
    }

    # Keep special subjects out of the normal graph-coloring pool.
    # They are appended as final morning exam day(s) below.
    # -----------------------------------------------------
    # BUILD CONFLICT GRAPH
    # -----------------------------------------------------

    conflict_graph = build_conflict_graph(

        regular_subject_students

    )


    # -----------------------------------------------------
    # COLOR GRAPH
    # -----------------------------------------------------

    subject_colors = color_conflict_graph(

        conflict_graph

    )


    # -----------------------------------------------------
    # GROUP SUBJECTS BY COLOR
    # -----------------------------------------------------
    #
    # Same color means:
    #
    # No common student conflict
    #
    # Therefore subjects can be scheduled
    # on the same exam day.
    #
    # -----------------------------------------------------

    color_groups = defaultdict(

        list

    )


    for subject, color in subject_colors.items():

        color_groups[

            color

        ].append(

            subject

        )


    # -----------------------------------------------------
    # CREATE DAYS
    # -----------------------------------------------------

    sorted_colors = sorted(

        color_groups.keys()

    )


    days = []


    # -----------------------------------------------------
    # DISTRIBUTE EACH COLOR GROUP INTO FIXED YEAR-BASED SLOTS
    # -----------------------------------------------------
    # First + Third Year  -> Morning (10.30am to 01.30pm)
    # Second + Final Year -> Afternoon (02.30pm to 05.30pm)
    # -----------------------------------------------------

    if subject_slots is None:
        subject_slots = {}

    days = []

    for color in sorted(color_groups.keys()):

        subjects = sorted(
            color_groups[color],
            key=lambda subject: (
                -len(conflict_graph[subject]),
                subject
            )
        )

        morning_subjects = []
        afternoon_subjects = []

        for subject in subjects:
            if subject_slots.get(subject, "morning") == "afternoon":
                afternoon_subjects.append(subject)
            else:
                morning_subjects.append(subject)

        morning_days = pack_slot(morning_subjects, "morning", morning_capacity)
        afternoon_days = pack_slot(
            afternoon_subjects, "afternoon", afternoon_capacity
        )

        required_days = max(len(morning_days), len(afternoon_days))
        if student_capacities["morning"] is None and \
                student_capacities["afternoon"] is None:
            required_days = max(1, required_days)

        for day_index in range(required_days):
            days.append({
                "morning": (
                    morning_days[day_index]
                    if day_index < len(morning_days) else []
                ),
                "afternoon": (
                    afternoon_days[day_index]
                    if day_index < len(afternoon_days) else []
                )
            })

    # -----------------------------------------------------
    # APPEND SPECIAL SUBJECTS AT THE END
    # -----------------------------------------------------
    # Engineering Graphics is scheduled first, followed by
    # Scholastic Aptitude. They are morning-only and capacity
    # is still respected.
    # -----------------------------------------------------

    # Engineering Graphics occupies 10.30am-02.30pm, so no other
    # examination is placed on the same calendar day as Graphics.
    # Scholastic Aptitude is therefore placed on the following
    # available morning day(s).
    for chunk in pack_slot(special_graphics, "morning", morning_capacity):
        days.append({
            "morning": chunk,
            "afternoon": []
        })

    for chunk in pack_slot(special_aptitude, "morning", morning_capacity):
        days.append({
            "morning": chunk,
            "afternoon": []
        })

    # -----------------------------------------------------
    # CREATE FINAL SCHEDULE
    # -----------------------------------------------------

    schedule = []


    for index, day_info in enumerate(

        days,

        start=1

    ):

        schedule.append({

            "day":

            index,

            "morning":

            day_info[

                "morning"

            ],

            "afternoon":

            day_info[

                "afternoon"

            ],

            "morning_students":

            sum(student_counts.get(c, 0) for c in day_info["morning"]),

            "afternoon_students":

            sum(student_counts.get(c, 0) for c in day_info["afternoon"])

        })


    total_days = len(

        schedule

    )

    if report is not None:
        report["unscheduled"] = unscheduled
        report["student_counts"] = student_counts
        report["student_capacities"] = student_capacities

    for item in unscheduled:
        print(
            f"WARNING: NOT SCHEDULED - {item['code']} "
            f"({item['students']} students): {item['reason']}"
        )

    print(

        "\nFinal exam days:",

        total_days

    )


    return (

        schedule,

        total_days

    )


# =========================================================
# 11. CONVERT SUBJECT CODES TO OBJECTS
# =========================================================

def add_subject_details(

    schedule,

    subject_names,

    student_counts=None

):
    student_counts = student_counts or {}

    detailed_schedule = []


    for row in schedule:

        new_row = {

            "day":

            row["day"],

            "date":

            row.get(

                "date",

                ""

            ),

            "morning": [],

            "afternoon": [],

            "morning_students": row.get("morning_students"),

            "afternoon_students": row.get("afternoon_students")

        }


        # -------------------------------------------------
        # MORNING
        # -------------------------------------------------

        for subject_code in row["morning"]:

            new_row["morning"].append({

                "code":

                subject_code,

                "name":

                subject_names.get(

                    subject_code,

                    subject_code

                ),

                "students":

                student_counts.get(subject_code)

            })


        # -------------------------------------------------
        # AFTERNOON
        # -------------------------------------------------

        for subject_code in row["afternoon"]:

            new_row["afternoon"].append({

                "code":

                subject_code,

                "name":

                subject_names.get(

                    subject_code,

                    subject_code

                ),

                "students":

                student_counts.get(subject_code)

            })


        detailed_schedule.append(

            new_row

        )


    return detailed_schedule


# =========================================================
# 12. ASSIGN CALENDAR DATES
# =========================================================

def assign_calendar_dates(

    schedule,

    start_date,

    holidays=None,

    remove_labs=False

):

    if holidays is None:

        holidays = set()


    dated_schedule = []


    current_date = start_date


    for row in schedule:

        # -------------------------------------------------
        # SKIP SUNDAY AND HOLIDAYS
        # -------------------------------------------------

        while (

            current_date.weekday() == 6

            or

            current_date in holidays

        ):

            current_date += timedelta(

                days=1

            )


        new_row = dict(

            row

        )


        new_row[

            "date"

        ] = current_date.strftime(

            "%Y-%m-%d"

        )


        dated_schedule.append(

            new_row

        )


        current_date += timedelta(

            days=1

        )


    return dated_schedule


# =========================================================
# 13. EXPORT CLEANED DATA
# =========================================================

def export_cleaned_excel(

    cleaned_df,

    output_folder

):

    os.makedirs(

        output_folder,

        exist_ok=True

    )


    cleaned_file = os.path.join(

        output_folder,

        "cleaned_reexam_database.xlsx"

    )


    cleaned_df.to_excel(

        cleaned_file,

        index=False

    )


    print(

        "\nCleaned Excel created:"

    )


    print(

        cleaned_file

    )


    return cleaned_file


# =========================================================
# 14. EXPORT TIMETABLE EXCEL
# =========================================================

def export_timetable_excel(
    schedule,
    output_folder,
    cleaned_df,
    template_path=None
):
    """Export a capacity-correct timetable plus a detailed student roster.

    The important distinction is:
      * Timetable sheet = one row per scheduled paper.
      * Student Roster sheet = multiple rows may exist for one paper because
        student roll numbers are split into groups of five.

    Therefore a paper with 200 students is NOT counted as 200 papers.
    """

    os.makedirs(output_folder, exist_ok=True)

    def pick(*names):
        for name in names:
            if name in cleaned_df.columns:
                return name
        return None

    roll_col = pick("RollNo", "ExamNo", "Roll No", "Roll Number")
    code_col = pick("Code", "SubjectCode", "Subject Code")

    # Some timetable/database files store five roll-number columns
    # (Roll No 1 ... Roll No 5) instead of one ExamNo/RollNo column.
    roll_columns = (
        [roll_col] if roll_col else find_roll_number_columns(cleaned_df)
    )
    name_col = pick(
        "SubjectName", "Course Name", "Name", "Subject Name", "Subject"
    )
    sem_col = pick("Semister", "Semester", "Sem")
    class_col = pick("ClassName", "Class")
    branch_col = pick("BranchName", "Branch")

    if not roll_columns or not code_col:
        raise ValueError(
            "Cannot create roster: a roll-number column (RollNo/ExamNo or Roll No 1-5) "
            "and Code/SubjectCode are required."
        )

    data = cleaned_df.copy()
    for col in [roll_col, code_col, name_col, sem_col, class_col, branch_col]:
        if col:
            data[col] = data[col].fillna("").astype(str).str.strip()

    # Build student groups once. These are used only by the detailed roster.
    groups = {}
    for _, r in data.iterrows():
        code = r[code_col]
        if not code:
            continue

        key = (
            code,
            r[branch_col] if branch_col else "",
            r[class_col] if class_col else "",
            r[sem_col] if sem_col else "",
        )

        g = groups.setdefault(
            key,
            {
                "code": code,
                "name": r[name_col] if name_col else code,
                "branch": r[branch_col] if branch_col else "",
                "class": r[class_col] if class_col else "",
                "sem": r[sem_col] if sem_col else "",
                "rolls": [],
            },
        )

        for roll_col_name in roll_columns:
            roll = str(r[roll_col_name]).strip() if pd.notna(r[roll_col_name]) else ""
            if roll and ROLL_NUMBER_PATTERN.match(roll):
                if roll not in g["rolls"]:
                    g["rolls"].append(roll)

    subject_groups = defaultdict(list)
    for g in groups.values():
        subject_groups[g["code"]].append(g)

    # Quick lookup for total students and metadata per subject.
    subject_student_count = defaultdict(int)
    subject_names_export = {}

    for g in groups.values():
        subject_student_count[g["code"]] += len(g["rolls"])
        subject_names_export.setdefault(g["code"], g["name"] or g["code"])

    # -----------------------------------------------------
    # MDM COURSE -> OWNING BRANCH (display only)
    # -----------------------------------------------------
    # The Branch values in the database are the *students'* home branches.
    # An MDM (multidisciplinary minor) course is taught by one specific
    # branch, so listing every student's branch is misleading. The owning
    # branch is encoded in the course-code prefix (e.g. ECMD202 -> "EC",
    # the same prefix used by non-MDM codes such as EC2014 of Electronics
    # and Tele-Communication Engineering). The prefix -> branch relation is
    # learnt from the non-MDM rows of the same database; a prefix that maps
    # to more than one branch (e.g. SH, OE) is treated as unknown.
    mdm_name_pattern = re.compile(r"\bMDM\b", re.IGNORECASE)
    mdm_code_pattern = re.compile(r"^([A-Za-z]+)MD\d", re.IGNORECASE)
    plain_code_pattern = re.compile(r"^([A-Za-z]+)")

    prefix_branches = defaultdict(set)
    if branch_col and name_col:
        for _, r in data.iterrows():
            code = r[code_col]
            branch = r[branch_col]
            if not code or not branch:
                continue
            if mdm_name_pattern.search(r[name_col]) or mdm_code_pattern.match(code):
                continue
            m = plain_code_pattern.match(code)
            if m:
                prefix_branches[m.group(1).upper()].add(branch)

    def mdm_owner_branch(code, name):
        """Return the branch owning an MDM course, or None if not reliable."""
        if not (mdm_name_pattern.search(str(name or "")) or mdm_code_pattern.match(code)):
            return None
        m = mdm_code_pattern.match(code)
        if not m:
            return None
        candidates = prefix_branches.get(m.group(1).upper(), set())
        if len(candidates) == 1:
            return next(iter(candidates))
        return None

    # -----------------------------------------------------
    # EXPORT-ONLY DISPLAY RULES: year-only Class, short Branch
    # -----------------------------------------------------
    # These only change what is *written* to Excel. Scheduling, grouping
    # and the cleaned database are untouched.
    branch_short_names = {
        "computer science and information technology": "CSIT",
        "civil engineering": "Civil",
        "mechanical engineering": "Mechanical",
        "computer engineering": "CSE",
        "electronics and tele-communication engineering": "E&TC",
        "computer science and engineering (artificial intelligence and machine learning)": "CSE (AIML)",
        "robotics and automation": "R&A",
        "mechatronics engineering": "Mechatronics",
        "bachelor of business administration": "BBA",
        # Branches present in the data but not listed in the request.
        "electrical engineering": "Electrical",
        "bachelor of computer application": "BCA",
        "master of business administration": "MBA",
        "computer science and engineering": "CSE",
        "structural engineering": "Structural",
        "mechanical engineering (thermal engineering)": "Mechanical (Thermal)",
        "automotive technology": "Automotive",
        "construction management": "Construction Mgmt",
        "(m.b.a. innovation, entrepreneurship and venture development)": "MBA (IEVD)",
    }
    year_labels = {
        "first": ("First Year", 1), "second": ("Second Year", 2),
        "third": ("Third Year", 3), "fourth": ("Final Year", 4),
        "final": ("Final Year", 4),
    }
    # First-year subjects are common to all B.Tech branches, so their
    # branch is exported as "all". Other first-year programmes (M.Tech,
    # M.B.A., B.B.A., B.C.A.) are separate programmes and keep their branch.
    first_year_all_programmes = {"b.tech"}

    def class_year(class_label):
        """'Third Year B.Tech.' -> ('Third Year', 3, 'b.tech')."""
        text = re.sub(r"\s+", " ", str(class_label or "")).strip()
        m = re.match(r"^(\w+)\s+Year\s*(.*)$", text, re.IGNORECASE)
        if not m or m.group(1).lower() not in year_labels:
            return "", 99, text.rstrip(".").strip().lower()
        label, rank = year_labels[m.group(1).lower()]
        return label, rank, m.group(2).rstrip(".").strip().lower()

    def short_branch(branch):
        key = re.sub(r"\s+", " ", str(branch or "")).strip()
        return branch_short_names.get(key.lower(), key)

    def display_class_branch(code, name, class_label=None):
        """Return (Class text, Branch text) for one paper as exported.

        Order of rules: year -> first-year "all" -> MDM owning branch ->
        short branch name. class_label limits the result to one class
        (used by the per-class tabs).
        """
        owner = mdm_owner_branch(code, name)
        years, branches = [], []
        for g in subject_groups.get(code, []):
            if class_label is not None and g.get("class") != class_label:
                continue
            year, rank, programme = class_year(g.get("class"))
            if year and (rank, year) not in years:
                years.append((rank, year))
            if year == "First Year" and programme in first_year_all_programmes:
                b = "all"
            elif owner:
                b = short_branch(owner)
            else:
                b = short_branch(g.get("branch"))
            if b and b not in branches:
                branches.append(b)
        # Open Elective courses (code starts with "OE") are open to all
        # branches, so the exported Branch is always "All".
        if str(code or "").strip().upper().startswith("OE") and (years or branches):
            branches = ["All"]
        return (
            ", ".join(y for _, y in sorted(years)),
            ", ".join(branches),
        )

    morning_time = "10.30am to 01.30pm"
    afternoon_time = "02.30pm to 05.30pm"
    graphics_time = "10.30am to 02.30pm"

    # -----------------------------------------------------
    # BUILD ONE-ROW-PER-PAPER TIMETABLE
    # -----------------------------------------------------
    timetable_rows = []

    # -----------------------------------------------------
    # BUILD DETAILED STUDENT ROSTER
    # -----------------------------------------------------
    roster_rows = []

    from datetime import datetime

    def get_date_obj(day_row):
        raw_date = day_row.get("date", "")
        try:
            return datetime.strptime(raw_date, "%Y-%m-%d").date()
        except Exception:
            try:
                return pd.to_datetime(raw_date).date()
            except Exception:
                return date.today()

    def add_scheduled_subject(day_row, subject, time_text, slot_name):
        code = subject["code"] if isinstance(subject, dict) else str(subject)
        name = (
            subject.get("name", code)
            if isinstance(subject, dict)
            else subject_names_export.get(code, code)
        )

        date_obj = day_row["date_obj"]

        # ---- Exactly ONE row per scheduled paper ----
        # Count is the total number of unique students attempting this paper.
        # Only the first five roll numbers are displayed in the timetable;
        # the full list remains available in the Student Roster sheet.
        matching_groups = subject_groups.get(code, [])
        all_rolls = []
        branches, classes, sems = [], [], []
        for g in matching_groups:
            if g.get("branch") and g["branch"] not in branches:
                branches.append(g["branch"])
            if g.get("class") and g["class"] not in classes:
                classes.append(g["class"])
            if g.get("sem") and g["sem"] not in sems:
                sems.append(g["sem"])
            for roll in g.get("rolls", []):
                if roll not in all_rolls:
                    all_rolls.append(roll)

        # MDM courses: show the branch that owns the course instead of the
        # combined list of students' branches. If it cannot be established
        # reliably, keep the previous behaviour (never guess).
        owner = mdm_owner_branch(code, name)
        if owner:
            branches = [owner]

        first_five = all_rolls[:5]
        timetable_row = {
            "Day & Date": date_obj.strftime("%A, %d.%m.%Y"),
            "Time": time_text,
            "Branch": ", ".join(branches),
            "Class": ", ".join(classes),
            "Sem": ", ".join(sems),
            "Course Name": name or code,
            "Code": code,
            "Count": len(all_rolls),
        }
        for i in range(5):
            timetable_row[f"Roll No {i + 1}"] = (
                first_five[i] if i < len(first_five) else None
            )
        timetable_rows.append(timetable_row)

        # ---- Detailed student roster ----
        for g in subject_groups.get(code, []):
            rolls = g["rolls"]
            chunks = [
                rolls[i:i + 5] for i in range(0, len(rolls), 5)
            ] or [[]]

            for chunk in chunks:
                row = {
                    "Day & Date": date_obj.strftime("%A, %d.%m.%Y"),
                    "Time": time_text,
                    "Branch": g["branch"],
                    "Class": g["class"],
                    "Sem": g["sem"],
                    "Course Name": g["name"] or code,
                    "Code": code,
                }
                for i in range(5):
                    row[f"Roll No {i + 1}"] = (
                        chunk[i] if i < len(chunk) else None
                    )
                roster_rows.append(row)

    for day_row in schedule:
        row = dict(day_row)
        row["date_obj"] = get_date_obj(row)

        morning = row.get("morning", [])
        afternoon = row.get("afternoon", [])

        # Hard validation: this is the value selected by the user.
        # If this fails, the scheduler/exporter must never silently create
        # an oversized slot.
        for subject in morning:
            subject_code = subject.get("code", "") if isinstance(subject, dict) else str(subject)
            subject_name = (
                subject.get("name", subject_code)
                if isinstance(subject, dict)
                else subject_names_export.get(subject_code, subject_code)
            )
            is_graphics = (
                "engineering graphics" in str(subject_name).lower()
                or subject_code.upper() in {"SH1135", "SH1136"}
            )
            add_scheduled_subject(
                row,
                subject,
                graphics_time if is_graphics else morning_time,
                "Morning"
            )

        for subject in afternoon:
            add_scheduled_subject(
                row, subject, afternoon_time, "Afternoon"
            )

    timetable_columns = [
        "Day & Date",
        "Time",
        "Branch",
        "Class",
        "Sem",
        "Course Name",
        "Code",
        "Count",
        "Roll No 1",
        "Roll No 2",
        "Roll No 3",
        "Roll No 4",
        "Roll No 5",
    ]

    roster_columns = [
        "Day & Date",
        "Time",
        "Branch",
        "Class",
        "Sem",
        "Course Name",
        "Code",
        "Roll No 1",
        "Roll No 2",
        "Roll No 3",
        "Roll No 4",
        "Roll No 5",
    ]

    timetable_df = pd.DataFrame(
        timetable_rows, columns=timetable_columns
    )
    roster_df = pd.DataFrame(
        roster_rows, columns=roster_columns
    )

    # Stable sorting by exam date, slot and paper code.
    time_order = {
        morning_time: 0,
        afternoon_time: 1,
    }

    if not timetable_df.empty:
        timetable_df["_date_sort"] = pd.to_datetime(
            timetable_df["Day & Date"],
            format="%A, %d.%m.%Y",
            errors="coerce",
        )
        timetable_df["_time_sort"] = (
            timetable_df["Time"].map(time_order).fillna(99)
        )
        timetable_df = (
            timetable_df.sort_values(
                ["_date_sort", "_time_sort", "Code"],
                kind="stable",
            )
            .drop(columns=["_date_sort", "_time_sort"])
        )

    if not roster_df.empty:
        roster_df["_date_sort"] = pd.to_datetime(
            roster_df["Day & Date"],
            format="%A, %d.%m.%Y",
            errors="coerce",
        )
        roster_df["_time_sort"] = (
            roster_df["Time"].map(time_order).fillna(99)
        )
        roster_df = (
            roster_df.sort_values(
                [
                    "_date_sort",
                    "_time_sort",
                    "Branch",
                    "Class",
                    "Sem",
                    "Code",
                ],
                kind="stable",
            )
            .drop(columns=["_date_sort", "_time_sort"])
        )

    # -----------------------------------------------------
    # CAPACITY VALIDATION
    # -----------------------------------------------------
    # The timetable sheet is the authoritative paper count.
    # Duplicate student-roster rows are deliberately ignored here.
    for (day_date, time_text), group in timetable_df.groupby(
        ["Day & Date", "Time"], sort=False
    ):
        paper_count = group["Code"].nunique()

        # Infer the selected capacity from the actual schedule shape.
        # The exporter does not change scheduling; it only verifies that
        # each scheduled slot contains unique paper codes.
        duplicate_count = len(group) - paper_count
        if duplicate_count:
            raise ValueError(
                f"Duplicate paper codes found in {day_date} / {time_text}. "
                "The timetable export was stopped to prevent duplicate papers."
            )

        print(
            f"EXPORT CHECK | {day_date} | {time_text} | "
            f"Unique papers: {paper_count}"
        )

    timetable_file = os.path.join(
        output_folder, "generated_reexam_timetable.xlsx"
    )

    # -----------------------------------------------------
    # WRITE EXCEL
    # -----------------------------------------------------
    # Excel readability: keep worksheet gridlines visible and also add
    # thin borders so rows/columns remain easy to follow when printed
    # or when Excel gridlines are disabled by a user's settings.
    table_side = Side(style="thin")
    table_border = Border(
        left=table_side, right=table_side,
        top=table_side, bottom=table_side
    )
    # Keep the first sheet as the actual timetable so it cannot be confused
    # with the detailed roster. The roster is retained on a separate sheet.
    wb = load_workbook(template_path) if (
        template_path and os.path.exists(template_path)
    ) else None

    if wb is None:
        from openpyxl import Workbook
        wb = Workbook()

    # Use a clean workbook layout. This avoids old template rows being
    # interpreted as timetable papers.
    first_ws = wb.worksheets[0]
    first_ws.title = "Timetable"

    # Remove all existing sheets except the first one.
    while len(wb.worksheets) > 1:
        wb.remove(wb.worksheets[-1])

    # Clear first sheet completely.
    first_ws.delete_rows(1, first_ws.max_row)

    # Write timetable header and data.
    for c_idx, header in enumerate(timetable_columns, 1):
        first_ws.cell(1, c_idx).value = header

    # Display-only transformation: year-only Class + short Branch.
    export_timetable_df = timetable_df[timetable_columns].copy()
    for idx, trow in export_timetable_df.iterrows():
        cls_txt, br_txt = display_class_branch(trow["Code"], trow["Course Name"])
        if cls_txt or br_txt:
            export_timetable_df.at[idx, "Class"] = cls_txt
            export_timetable_df.at[idx, "Branch"] = br_txt

    for r_idx, values in enumerate(
        export_timetable_df.itertuples(
            index=False, name=None
        ),
        2,
    ):
        for c_idx, value in enumerate(values, 1):
            first_ws.cell(r_idx, c_idx).value = value

    first_ws.freeze_panes = "A2"
    first_ws.auto_filter.ref = (
        f"A1:M{max(first_ws.max_row, 1)}"
    )

    # Header formatting.
    for cell in first_ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(
            horizontal="center",
            vertical="center",
            wrap_text=True,
        )
        cell.border = table_border

    for row_cells in first_ws.iter_rows(
        min_row=2,
        max_row=first_ws.max_row,
        min_col=1,
        max_col=len(timetable_columns),
    ):
        for cell in row_cells:
            cell.alignment = Alignment(
                vertical="top",
                wrap_text=True,
            )
            cell.border = table_border

    widths = [24, 24, 30, 24, 10, 42, 16, 10, 14, 14, 14, 14, 14]
    from openpyxl.utils import get_column_letter
    for i, width in enumerate(widths, 1):
        first_ws.column_dimensions[get_column_letter(i)].width = width

    first_ws.row_dimensions[1].height = 30

    # Create detailed roster sheet.
    roster_ws = wb.create_sheet("Student Roster")

    for c_idx, header in enumerate(roster_columns, 1):
        roster_ws.cell(1, c_idx).value = header

    export_roster_df = roster_df[roster_columns].copy()
    for idx, rrow in export_roster_df.iterrows():
        cls_txt, br_txt = display_class_branch(
            rrow["Code"], rrow["Course Name"], class_label=rrow["Class"]
        )
        if cls_txt or br_txt:
            export_roster_df.at[idx, "Class"] = cls_txt
            export_roster_df.at[idx, "Branch"] = br_txt

    for r_idx, values in enumerate(
        export_roster_df.itertuples(
            index=False, name=None
        ),
        2,
    ):
        for c_idx, value in enumerate(values, 1):
            roster_ws.cell(r_idx, c_idx).value = value

    roster_ws.freeze_panes = "A2"
    roster_ws.auto_filter.ref = (
        f"A1:L{max(roster_ws.max_row, 1)}"
    )

    for cell in roster_ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(
            horizontal="center",
            vertical="center",
            wrap_text=True,
        )
        cell.border = table_border

    for row_cells in roster_ws.iter_rows(
        min_row=2,
        max_row=roster_ws.max_row,
        min_col=1,
        max_col=len(roster_columns),
    ):
        for cell in row_cells:
            cell.alignment = Alignment(
                vertical="top",
                wrap_text=True,
            )
            cell.border = table_border

    roster_widths = [
        24, 24, 32, 24, 10, 42, 16,
        14, 14, 14, 14, 14,
    ]
    for i, width in enumerate(roster_widths, 1):
        roster_ws.column_dimensions[chr(64 + i)].width = width

    roster_ws.row_dimensions[1].height = 30

    # -----------------------------------------------------
    # COURSE + ACADEMIC YEAR TABS (SAME EXCEL WORKBOOK)
    # -----------------------------------------------------
    # One worksheet per unique course/year combination found in the
    # already-generated timetable (the "Class" column, e.g.
    # "Third Year B.Tech." -> sheet "B.Tech - Third Year"). This only
    # reads timetable_df; it does not touch scheduling.
    from openpyxl.utils import get_column_letter

    year_rank = {
        "first": 1, "second": 2, "third": 3, "fourth": 4,
        "fifth": 5, "final": 6,
    }

    def split_class(label):
        # "Third Year B.Tech." -> ("B.Tech", "Third Year", rank)
        text = re.sub(r"\s+", " ", str(label)).strip()
        m = re.match(r"^(\w+)\s+Year\s+(.+)$", text, re.IGNORECASE)
        if m:
            course = m.group(2).strip().rstrip(".").strip()
            year = f"{m.group(1).capitalize()} Year"
            return course, year, year_rank.get(m.group(1).lower(), 99)
        return text.rstrip(".").strip(), "", 99

    def safe_sheet_name(name, used_names):
        # Excel sheet names cannot contain: \ / * ? : [ ] , cannot start or
        # end with an apostrophe, and are limited to 31 characters.
        # Uniqueness is case-insensitive in Excel.
        clean = re.sub(r"[\\/:*?\[\]]", "-", str(name)).strip().strip("'")
        clean = clean or "Class"
        clean = clean[:31]
        base = clean
        counter = 2
        used_lower = {n.lower() for n in used_names}
        while clean.lower() in used_lower:
            suffix = f" ({counter})"
            clean = base[:31 - len(suffix)] + suffix
            counter += 1
        return clean

    # Collect class labels. A paper shared by several classes lists them
    # comma-separated, so it appears on every matching class sheet and no
    # scheduled paper is dropped. Rows with no class go to "Unspecified".
    def row_classes(text):
        parts = [p.strip() for p in str(text or "").split(",")]
        parts = [p for p in parts if p and p.lower() != "nan"]
        return parts or ["Unspecified"]

    class_to_idx = {}
    for idx, text in zip(timetable_df.index, timetable_df["Class"]):
        for label in row_classes(text):
            class_to_idx.setdefault(label, []).append(idx)

    class_keys = sorted(
        class_to_idx,
        key=lambda lbl: (
            split_class(lbl)[0].lower(), split_class(lbl)[2], lbl.lower()
        ),
    )

    used_sheet_names = {ws.title for ws in wb.worksheets}
    title_font = Font(bold=True, size=14)
    sub_font = Font(bold=True, size=12)
    header_fill = PatternFill("solid", start_color="D9D9D9", end_color="D9D9D9")
    last_col = get_column_letter(len(timetable_columns))

    for label in class_keys:
        course, year, _ = split_class(label)
        display = f"{course} - {year}" if year else course
        sheet_name = safe_sheet_name(display, used_sheet_names)
        used_sheet_names.add(sheet_name)
        class_ws = wb.create_sheet(sheet_name)

        class_df = timetable_df.loc[class_to_idx[label], timetable_columns].copy()
        for idx, crow in class_df.iterrows():
            cls_txt, br_txt = display_class_branch(
                crow["Code"], crow["Course Name"], class_label=label
            )
            if cls_txt or br_txt:
                class_df.at[idx, "Class"] = cls_txt
                class_df.at[idx, "Branch"] = br_txt

        # Title block (layout follows the ESE reference timetable).
        class_ws.cell(1, 1).value = "Re-Examination Time Table"
        class_ws.cell(2, 1).value = display
        for r, font in ((1, title_font), (2, sub_font)):
            class_ws.merge_cells(f"A{r}:{last_col}{r}")
            class_ws.cell(r, 1).font = font
            class_ws.cell(r, 1).alignment = Alignment(
                horizontal="center", vertical="center"
            )
            class_ws.row_dimensions[r].height = 24

        hdr = 4
        for c_idx, header in enumerate(timetable_columns, 1):
            cell = class_ws.cell(hdr, c_idx)
            cell.value = header
            cell.font = Font(bold=True)
            cell.fill = header_fill
            cell.alignment = Alignment(
                horizontal="center", vertical="center", wrap_text=True
            )
            cell.border = table_border
        class_ws.row_dimensions[hdr].height = 30

        first_data = hdr + 1
        for r_idx, values in enumerate(
            class_df.itertuples(index=False, name=None), first_data
        ):
            for c_idx, value in enumerate(values, 1):
                cell = class_ws.cell(r_idx, c_idx)
                cell.value = None if pd.isna(value) else value
                cell.alignment = Alignment(vertical="center", wrap_text=True)
                cell.border = table_border
            # Size the row so long Branch / Course Name text stays visible.
            lines = 1
            for c_idx, value in enumerate(values, 1):
                if isinstance(value, str):
                    usable = max(int(widths[c_idx - 1]) - 2, 1)
                    lines = max(lines, -(-len(value) // usable))
            class_ws.row_dimensions[r_idx].height = max(30, 15 * lines)
        last_data = first_data + len(class_df) - 1

        # Merge consecutive identical Day & Date / Time cells, as in the
        # reference timetable (values and row order are unchanged).
        for col in (1, 2):
            run_start = first_data
            for r in range(first_data, last_data + 2):
                same = (
                    r <= last_data
                    and class_ws.cell(r, col).value
                    == class_ws.cell(run_start, col).value
                    and (
                        col == 1
                        or class_ws.cell(r, 1).value
                        == class_ws.cell(run_start, 1).value
                    )
                )
                if not same:
                    if r - 1 > run_start:
                        class_ws.merge_cells(
                            start_row=run_start, start_column=col,
                            end_row=r - 1, end_column=col,
                        )
                    class_ws.cell(run_start, col).alignment = Alignment(
                        horizontal="center", vertical="center",
                        wrap_text=True,
                    )
                    run_start = r

        class_ws.freeze_panes = f"A{first_data}"
        for i, width in enumerate(widths, 1):
            class_ws.column_dimensions[get_column_letter(i)].width = width
        class_ws.page_setup.orientation = "landscape"
        class_ws.page_setup.fitToWidth = 1
        class_ws.page_setup.fitToHeight = 0
        class_ws.sheet_properties.pageSetUpPr.fitToPage = True
        class_ws.print_title_rows = f"{hdr}:{hdr}"

    # Keep the main timetable selected when the user first opens the file.
    # Gridlines are intentionally visible so row/column structure is clear.
    for ws in wb.worksheets:
        ws.sheet_view.showGridLines = True
    wb.active = 0

    wb.save(timetable_file)

    print("\nGenerated timetable Excel:")
    print(timetable_file)
    print("Timetable paper rows:", len(timetable_df))
    print("Detailed roster rows:", len(roster_df))

    return timetable_file


# =========================================================
# 15. MAIN SCHEDULER FUNCTION
# =========================================================

def create_schedule(

    excel_path,

    start_date,

    morning_capacity=5,

    afternoon_capacity=4,

    output_folder=None,

    holidays=None,

    remove_labs=False,

    morning_student_capacity=None,

    afternoon_student_capacity=None

):

    print(

        "\n========================================"

    )


    print(

        "STARTING RE-EXAM SCHEDULER"

    )


    print(

        "========================================"

    )


    print(

        "\nDSA ALGORITHMS USED:"

    )


    print(

        "1. Graph Data Structure"

    )


    print(

        "2. Graph Coloring"

    )


    print(

        "3. Greedy Algorithm"

    )


    print(

        "4. Backtracking"

    )


    print(

        "5. Set Intersection"

    )


    print(

        "6. Dictionary / Hash Map"

    )


    print(

        "7. Sorting"

    )


    print(

        "\nMorning Papers Per Day:",

        morning_capacity

    )


    print(

        "Afternoon Papers Per Day:",

        afternoon_capacity

    )


    # -----------------------------------------------------
    # OUTPUT FOLDER
    # -----------------------------------------------------

    if output_folder is None:

        output_folder = os.path.join(

            os.path.dirname(

                os.path.abspath(

                    __file__

                )

            ),

            "generated_schedules"

        )


    os.makedirs(

        output_folder,

        exist_ok=True

    )


    # -----------------------------------------------------
    # LOAD EXCEL
    # -----------------------------------------------------

    (

        student_subjects,

        subject_students,

        subject_names,

        subject_slots,

        cleaned_df

    ) = load_registrations_from_excel(

        excel_path,

        remove_labs=remove_labs

    )


    # -----------------------------------------------------
    # EXPORT CLEANED EXCEL
    # -----------------------------------------------------

    cleaned_file = export_cleaned_excel(

        cleaned_df,

        output_folder

    )


    # -----------------------------------------------------
    # GENERATE GRAPH COLORING SCHEDULE
    # -----------------------------------------------------

    capacity_report = {}

    (

        schedule,

        total_days

    ) = generate_schedule(

        student_subjects,

        subject_students,

        subject_slots,

        morning_capacity,

        afternoon_capacity,

        subject_names,

        morning_student_capacity=morning_student_capacity,

        afternoon_student_capacity=afternoon_student_capacity,

        report=capacity_report

    )


    # -----------------------------------------------------
    # ASSIGN CALENDAR DATES
    # -----------------------------------------------------

    # Convert holiday values to date objects so the calendar
    # assignment can skip every selected holiday.
    normalized_holidays = set()

    if holidays:
        for holiday in holidays:
            if isinstance(holiday, date):
                normalized_holidays.add(holiday)
            else:
                try:
                    normalized_holidays.add(
                        pd.to_datetime(str(holiday)).date()
                    )
                except (ValueError, TypeError):
                    raise ValueError(
                        f"Invalid holiday date: {holiday}. Use YYYY-MM-DD."
                    )

    dated_schedule = assign_calendar_dates(
        schedule,
        start_date,
        holidays=normalized_holidays
    )


    # -----------------------------------------------------
    # ADD SUBJECT CODE + NAME
    # -----------------------------------------------------

    detailed_schedule = add_subject_details(

        dated_schedule,

        subject_names,

        capacity_report.get("student_counts")

    )


    # -----------------------------------------------------
    # EXPORT TIMETABLE EXCEL
    # -----------------------------------------------------

    template_path = os.path.join(
        os.path.dirname(os.path.abspath(excel_path)),
        "timetable_unsorted_placeholder.xlsx"
    )
    if not os.path.exists(template_path):
        project_template = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "uploads",
            "timetable_unsorted_placeholder.xlsx"
        )
        template_path = project_template

    timetable_file = export_timetable_excel(
        detailed_schedule,
        output_folder,
        cleaned_df,
        template_path=template_path
    )


    # -----------------------------------------------------
    # COUNT STUDENTS
    # -----------------------------------------------------

    total_students = len(

        student_subjects

    )


    # -----------------------------------------------------
    # COUNT SUBJECTS
    # -----------------------------------------------------

    total_subjects = len(

        subject_students

    )


    # -----------------------------------------------------
    # FINAL RESULT
    # -----------------------------------------------------

    result = {

        "students":

        total_students,

        "subjects":

        total_subjects,

        "exam_days":

        total_days,

        "schedule":

        detailed_schedule,

        "cleaned_file":

        cleaned_file,

        "timetable_file":

        timetable_file,

        "unscheduled":

        capacity_report.get("unscheduled", []),

        "morning_student_capacity":

        morning_student_capacity,

        "afternoon_student_capacity":

        afternoon_student_capacity

    }


    print(

        "\n========================================"

    )


    print(

        "SCHEDULING COMPLETED SUCCESSFULLY"

    )


    print(

        "========================================"

    )


    print(

        "Total Students:",

        total_students

    )


    print(

        "Total Subjects:",

        total_subjects

    )


    print(

        "Total Exam Days:",

        total_days

    )


    print(

        "Timetable:",

        timetable_file

    )


    return result


# =========================================================
# 16. DIRECT TEST
# =========================================================

if __name__ == "__main__":

    current_folder = os.path.dirname(

        os.path.abspath(

            __file__

        )

    )


    excel_file = os.path.join(

        current_folder,

        "database.xlsx"

    )


    if not os.path.exists(

        excel_file

    ):

        print(

            "\nERROR:"

        )


        print(

            "database.xlsx was not found!"

        )


        print(

            "\nPython searched here:"

        )


        print(

            excel_file

        )


        exit()


    exam_start_date = date(

        2026,

        10,

        26

    )


    morning_papers = 5


    afternoon_papers = 4


    result = create_schedule(

        excel_file,

        exam_start_date,

        morning_capacity=

        morning_papers,

        afternoon_capacity=

        afternoon_papers

    )


    print(

        "\n========================================"

    )


    print(

        "RE-EXAM SCHEDULER RESULT"

    )


    print(

        "========================================"

    )


    print(

        f"\nTotal Students: "
        f"{result['students']}"

    )


    print(

        f"Total Subjects: "
        f"{result['subjects']}"

    )


    print(

        f"Total Exam Days: "
        f"{result['exam_days']}"

    )


    print(

        "\nGenerated Timetable:"

    )


    print(

        "----------------------------------------"

    )


    for row in result["schedule"]:

        print(

            f"\nDay {row['day']} | "
            f"Date: {row['date']}"

        )


        print(

            "\nSlot 1 - Morning "
            "(10:30 AM - 01:30 PM):"

        )


        for subject in row["morning"]:

            print(

                f"  {subject['code']} - "
                f"{subject['name']}"

            )


        print(

            "\nSlot 2 - Afternoon "
            "(02:30 PM - 05:30 PM):"

        )


        for subject in row["afternoon"]:

            print(

                f"  {subject['code']} - "
                f"{subject['name']}"

            )


    print(

        "\n----------------------------------------"

    )


    print(

        "\nCleaned database:"

    )


    print(

        result["cleaned_file"]

    )


    print(

        "\nGenerated timetable:"

    )


    print(

        result["timetable_file"]

    )


    print(

        "\nScheduling completed successfully!"

    )
