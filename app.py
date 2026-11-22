
import os
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from flask import (
    Flask,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)


# ============================================================
# APPLICATION CONFIGURATION
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

app = Flask(__name__)

app.config.update(
    DATABASE=str(BASE_DIR / "habits.db"),
    SECRET_KEY=os.environ.get("SECRET_KEY", os.urandom(32).hex()),
)


# ============================================================
# DATABASE MANAGEMENT
# ============================================================

def get_db():
    """Return a database connection for the current request."""
    if "db" not in g:
        connection = sqlite3.connect(app.config["DATABASE"])
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        g.db = connection

    return g.db


@app.teardown_appcontext
def close_db(error=None):
    """Close the database connection after each request."""
    connection = g.pop("db", None)

    if connection is not None:
        connection.close()


def init_db():
    """Create database tables if they do not exist."""
    db = get_db()

    db.executescript("""
        CREATE TABLE IF NOT EXISTS habits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT DEFAULT 'General',
            frequency INTEGER DEFAULT 7,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            archived INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS habit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            habit_id INTEGER NOT NULL,
            completed_date TEXT NOT NULL,
            FOREIGN KEY (habit_id)
                REFERENCES habits(id)
                ON DELETE CASCADE,
            UNIQUE(habit_id, completed_date)
        );

        CREATE INDEX IF NOT EXISTS idx_habit_logs_habit_id
        ON habit_logs(habit_id);
    """)

    db.commit()


# ============================================================
# DATE UTILITIES
# ============================================================

def format_date(value):
    """Convert a date object into YYYY-MM-DD format."""
    return value.isoformat()


def get_recent_days(num_days=7):
    """Generate information for the last N days."""
    today = date.today()

    return [
        {
            "date_str": format_date(day),
            "day_name": day.strftime("%a"),
            "day_num": day.strftime("%d"),
            "is_today": day == today,
        }
        for day in (
            today - timedelta(days=offset)
            for offset in range(num_days - 1, -1, -1)
        )
    ]


def validate_date(date_string):
    """Validate a completion date and reject future dates."""
    try:
        parsed_date = date.fromisoformat(date_string)

        if parsed_date.isoformat() != date_string:
            return False

        return parsed_date <= date.today()

    except (ValueError, TypeError):
        return False


# ============================================================
# STREAK CALCULATIONS
# ============================================================

def calculate_streaks(completed_dates, today_str=None, yesterday_str=None):
    """
    Calculate current and longest consecutive-day streaks.

    Current streak remains active when the latest completion
    is today or yesterday.
    """
    if not completed_dates:
        return 0, 0

    today = (
        date.fromisoformat(today_str)
        if today_str
        else date.today()
    )

    yesterday = (
        date.fromisoformat(yesterday_str)
        if yesterday_str
        else today - timedelta(days=1)
    )

    dates = {
        date.fromisoformat(value)
        for value in completed_dates
    }

    # Current streak
    current_streak = 0

    if today in dates:
        cursor = today
    elif yesterday in dates:
        cursor = yesterday
    else:
        cursor = None

    while cursor is not None and cursor in dates:
        current_streak += 1
        cursor -= timedelta(days=1)

    # Longest streak
    longest_streak = 0
    consecutive_days = 0
    previous_day = None

    for day in sorted(dates):
        if previous_day and (day - previous_day).days == 1:
            consecutive_days += 1
        else:
            consecutive_days = 1

        longest_streak = max(
            longest_streak,
            consecutive_days
        )

        previous_day = day

    return current_streak, longest_streak


# ============================================================
# DATABASE QUERY HELPERS
# ============================================================

def get_active_habits():
    """Fetch all non-archived habits."""
    return get_db().execute("""
        SELECT *
        FROM habits
        WHERE archived = 0
        ORDER BY id DESC
    """).fetchall()


def get_habit(habit_id):
    """Fetch one habit by ID."""
    return get_db().execute(
        "SELECT * FROM habits WHERE id = ?",
        (habit_id,)
    ).fetchone()


def get_all_habit_logs():
    """Fetch completion dates grouped by habit ID."""
    rows = get_db().execute("""
        SELECT habit_id, completed_date
        FROM habit_logs
    """).fetchall()

    logs = defaultdict(set)

    for row in rows:
        logs[row["habit_id"]].add(row["completed_date"])

    return logs


def get_habit_dates(habit_id):
    """Fetch completion dates for a specific habit."""
    rows = get_db().execute("""
        SELECT completed_date
        FROM habit_logs
        WHERE habit_id = ?
    """, (habit_id,)).fetchall()

    return {row["completed_date"] for row in rows}


# ============================================================
# HABIT DATA PROCESSING
# ============================================================

def build_habit_data(habit, completed_dates, recent_days):
    """Convert a database habit into dashboard-ready data."""
    today_str = format_date(date.today())

    current_streak, longest_streak = calculate_streaks(
        completed_dates
    )

    history = [
        {
            **day,
            "completed": day["date_str"] in completed_dates,
        }
        for day in recent_days
    ]

    return {
        "id": habit["id"],
        "name": habit["name"],
        "category": habit["category"] or "General",
        "frequency": habit["frequency"] or 7,
        "created_at": habit["created_at"],
        "is_completed_today": today_str in completed_dates,
        "current_streak": current_streak,
        "longest_streak": longest_streak,
        "total_completions": len(completed_dates),
        "history_7d": history,
    }


def calculate_dashboard_stats(habits):
    """Calculate summary statistics for active habits."""
    total_habits = len(habits)

    completed_today = sum(
        habit["is_completed_today"]
        for habit in habits
    )

    completion_rate = (
        round(completed_today / total_habits * 100)
        if total_habits
        else 0
    )

    return {
        "total_habits": total_habits,
        "completed_today": completed_today,
        "completion_rate_today": completion_rate,
        "best_streak": max(
            (habit["current_streak"] for habit in habits),
            default=0
        ),
        "total_completions": sum(
            habit["total_completions"]
            for habit in habits
        ),
    }


# ============================================================
# RESPONSE HELPERS
# ============================================================

def is_ajax_request():
    """Check whether the client expects a JSON response."""
    return (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.is_json
    )


def success_response(message, **extra):
    """Return a success response or redirect."""
    if is_ajax_request():
        return jsonify({
            "success": True,
            "message": message,
            **extra,
        })

    return redirect(url_for("index"))


def error_response(message, status_code=400):
    """Return an error response appropriate to the request."""
    if is_ajax_request():
        return jsonify({
            "success": False,
            "message": message,
        }), status_code

    return message, status_code


# ============================================================
# DASHBOARD ROUTE
# ============================================================

@app.route("/")
def index():
    """Display the habit tracker dashboard."""
    today = date.today()
    recent_days = get_recent_days()

    habit_rows = get_active_habits()
    all_logs = get_all_habit_logs()

    habits = [
        build_habit_data(
            habit,
            all_logs.get(habit["id"], set()),
            recent_days
        )
        for habit in habit_rows
    ]

    return render_template(
        "index.html",
        habits=habits,
        today_formatted=today.strftime("%A, %B %d, %Y"),
        today_str=format_date(today),
        recent_days=recent_days,
        stats=calculate_dashboard_stats(habits),
    )


# ============================================================
# ADD HABIT
# ============================================================

@app.route("/habits/add", methods=["POST"])
def add_habit():
    """Create a new habit."""
    data = request.get_json(silent=True) if request.is_json else request.form

    if not isinstance(data, dict) and request.is_json:
        return error_response("Invalid JSON body")

    name = str(data.get("name") or "").strip()
    category = str(data.get("category") or "General").strip()

    if not name:
        return error_response("Habit name is required")

    if len(name) > 100:
        return error_response("Habit name must be 100 characters or fewer")

    if len(category) > 50:
        return error_response("Category must be 50 characters or fewer")

    try:
        frequency = int(data.get("frequency", 7))
    except (TypeError, ValueError, OverflowError):
        return error_response("Frequency must be a valid integer")

    frequency = max(1, min(7, frequency))

    db = get_db()

    with db:
        db.execute("""
            INSERT INTO habits (name, category, frequency)
            VALUES (?, ?, ?)
        """, (name, category or "General", frequency))

    return success_response("Habit created successfully")


# ============================================================
# TOGGLE HABIT COMPLETION
# ============================================================

@app.route("/habits/<int:habit_id>/toggle", methods=["POST"])
def toggle_habit(habit_id):
    """Mark a habit complete or incomplete for a given date."""
    habit = get_habit(habit_id)

    if habit is None:
        return error_response("Habit not found", 404)

    data = request.get_json(silent=True) if request.is_json else request.form

    if not isinstance(data, dict) and request.is_json:
        return error_response("Invalid JSON body")

    target_date = data.get("date") or format_date(date.today())

    if not validate_date(target_date):
        return error_response("Invalid or future completion date")

    db = get_db()

    existing = db.execute("""
        SELECT id
        FROM habit_logs
        WHERE habit_id = ? AND completed_date = ?
    """, (habit_id, target_date)).fetchone()

    with db:
        if existing:
            db.execute(
                "DELETE FROM habit_logs WHERE id = ?",
                (existing["id"],)
            )
            completed = False
        else:
            db.execute("""
                INSERT INTO habit_logs (habit_id, completed_date)
                VALUES (?, ?)
            """, (habit_id, target_date))
            completed = True

    completed_dates = get_habit_dates(habit_id)

    current_streak, longest_streak = calculate_streaks(
        completed_dates
    )

    if is_ajax_request():
        return jsonify({
            "success": True,
            "completed": completed,
            "date": target_date,
            "current_streak": current_streak,
            "longest_streak": longest_streak,
            "total_completions": len(completed_dates),
        })

    return redirect(url_for("index"))


# ============================================================
# DELETE HABIT
# ============================================================

@app.route("/habits/<int:habit_id>/delete", methods=["POST"])
def delete_habit(habit_id):
    """Delete a habit and its completion history."""
    habit = get_habit(habit_id)

    if habit is None:
        return error_response("Habit not found", 404)

    db = get_db()

    # Related habit logs are deleted automatically by SQLite.
    with db:
        db.execute(
            "DELETE FROM habits WHERE id = ?",
            (habit_id,)
        )

    return success_response("Habit deleted")


# ============================================================
# APPLICATION STARTUP
# ============================================================

with app.app_context():
    init_db()


if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_DEBUG") == "1"

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=debug_mode,
    )
