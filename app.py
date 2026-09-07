import os
import sqlite3
from datetime import date, datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, jsonify, g

app = Flask(__name__)
app.config['DATABASE'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'habits.db')
app.config['SECRET_KEY'] = 'dev-secret-key-habit-tracker-2026'

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(
            app.config['DATABASE'],
            detect_types=sqlite3.PARSE_DECLTYPES
        )
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON;")
    return g.db

@app.teardown_appcontext
def close_db(error):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_db():
    db = get_db()
    db.executescript('''
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
            FOREIGN KEY (habit_id) REFERENCES habits (id) ON DELETE CASCADE,
            UNIQUE(habit_id, completed_date)
        );
    ''')
    db.commit()

def calculate_streaks(completed_dates_set, today_str, yesterday_str):
    """
    Calculate current streak and longest streak from a set of 'YYYY-MM-DD' strings.
    """
    if not completed_dates_set:
        return 0, 0

    sorted_dates = sorted([datetime.strptime(d, '%Y-%m-%d').date() for d in completed_dates_set])
    
    # Calculate current streak
    today_date = datetime.strptime(today_str, '%Y-%m-%d').date()
    yesterday_date = datetime.strptime(yesterday_str, '%Y-%m-%d').date()
    
    current_streak = 0
    check_date = None
    
    if today_str in completed_dates_set:
        check_date = today_date
    elif yesterday_str in completed_dates_set:
        check_date = yesterday_date
        
    if check_date:
        while check_date.strftime('%Y-%m-%d') in completed_dates_set:
            current_streak += 1
            check_date -= timedelta(days=1)

    # Calculate longest streak
    longest_streak = 0
    if sorted_dates:
        temp_streak = 1
        longest_streak = 1
        for i in range(1, len(sorted_dates)):
            diff = (sorted_dates[i] - sorted_dates[i - 1]).days
            if diff == 1:
                temp_streak += 1
                longest_streak = max(longest_streak, temp_streak)
            elif diff > 1:
                temp_streak = 1
    
    return current_streak, max(longest_streak, current_streak)

def get_recent_days(num_days=7):
    """Returns a list of dicts for the last N days with format info."""
    today = date.today()
    days = []
    for i in range(num_days - 1, -1, -1):
        day_date = today - timedelta(days=i)
        days.append({
            'date_str': day_date.strftime('%Y-%m-%d'),
            'day_name': day_date.strftime('%a'),      # Mon, Tue, etc.
            'day_num': day_date.strftime('%d'),       # 01, 02, etc.
            'is_today': day_date == today
        })
    return days

@app.route('/')
def index():
    init_db()
    db = get_db()
    today_str = date.today().strftime('%Y-%m-%d')
    yesterday_str = (date.today() - timedelta(days=1)).strftime('%Y-%m-%d')
    
    recent_days = get_recent_days(7)
    
    # Fetch all active habits
    habits_rows = db.execute(
        "SELECT * FROM habits WHERE archived = 0 ORDER BY id DESC"
    ).fetchall()
    
    habits = []
    completed_today_count = 0
    total_completions_all_time = 0
    
    for h in habits_rows:
        h_id = h['id']
        logs = db.execute(
            "SELECT completed_date FROM habit_logs WHERE habit_id = ?",
            (h_id,)
        ).fetchall()
        
        completed_dates_set = {row['completed_date'] for row in logs}
        is_completed_today = today_str in completed_dates_set
        if is_completed_today:
            completed_today_count += 1
            
        total_completions_all_time += len(completed_dates_set)
        current_streak, longest_streak = calculate_streaks(completed_dates_set, today_str, yesterday_str)
        
        # Build 7-day history matrix
        history_7d = []
        for day in recent_days:
            d_str = day['date_str']
            history_7d.append({
                'date_str': d_str,
                'day_name': day['day_name'],
                'day_num': day['day_num'],
                'is_today': day['is_today'],
                'completed': d_str in completed_dates_set
            })
            
        habits.append({
            'id': h['id'],
            'name': h['name'],
            'category': h['category'] or 'General',
            'frequency': h['frequency'] or 7,
            'created_at': h['created_at'],
            'is_completed_today': is_completed_today,
            'current_streak': current_streak,
            'longest_streak': longest_streak,
            'total_completions': len(completed_dates_set),
            'history_7d': history_7d
        })
    
    total_habits = len(habits)
    completion_rate_today = round((completed_today_count / total_habits * 100)) if total_habits > 0 else 0
    best_overall_streak = max([h['current_streak'] for h in habits], default=0)
    
    return render_template(
        'index.html',
        habits=habits,
        today_formatted=date.today().strftime('%A, %B %d, %Y'),
        today_str=today_str,
        recent_days=recent_days,
        stats={
            'total_habits': total_habits,
            'completed_today': completed_today_count,
            'completion_rate_today': completion_rate_today,
            'best_streak': best_overall_streak,
            'total_completions': total_completions_all_time
        }
    )

@app.route('/habits/add', methods=['POST'])
def add_habit():
    name = request.form.get('name', '').strip()
    category = request.form.get('category', 'General').strip() or 'General'
    frequency = request.form.get('frequency', 7, type=int)
    
    if name:
        db = get_db()
        db.execute(
            "INSERT INTO habits (name, category, frequency) VALUES (?, ?, ?)",
            (name, category, max(1, min(7, frequency)))
        )
        db.commit()
        
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
        return jsonify({'success': True, 'message': 'Habit created successfully'})
    return redirect(url_for('index'))

@app.route('/habits/<int:habit_id>/toggle', methods=['POST'])
def toggle_habit(habit_id):
    db = get_db()
    
    # Get date from JSON, form, or default to today
    data = request.get_json(silent=True) or request.form
    target_date = data.get('date') or date.today().strftime('%Y-%m-%d')
    
    # Check if already completed
    existing = db.execute(
        "SELECT id FROM habit_logs WHERE habit_id = ? AND completed_date = ?",
        (habit_id, target_date)
    ).fetchone()
    
    completed = False
    if existing:
        db.execute("DELETE FROM habit_logs WHERE id = ?", (existing['id'],))
        completed = False
    else:
        db.execute(
            "INSERT INTO habit_logs (habit_id, completed_date) VALUES (?, ?)",
            (habit_id, target_date)
        )
        completed = True
        
    db.commit()
    
    # Recalculate streak for this habit
    today_str = date.today().strftime('%Y-%m-%d')
    yesterday_str = (date.today() - timedelta(days=1)).strftime('%Y-%m-%d')
    logs = db.execute(
        "SELECT completed_date FROM habit_logs WHERE habit_id = ?",
        (habit_id,)
    ).fetchall()
    completed_dates_set = {row['completed_date'] for row in logs}
    current_streak, longest_streak = calculate_streaks(completed_dates_set, today_str, yesterday_str)
    
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
        return jsonify({
            'success': True,
            'completed': completed,
            'date': target_date,
            'current_streak': current_streak,
            'longest_streak': longest_streak,
            'total_completions': len(completed_dates_set)
        })
        
    return redirect(url_for('index'))

@app.route('/habits/<int:habit_id>/delete', methods=['POST'])
def delete_habit(habit_id):
    db = get_db()
    db.execute("DELETE FROM habit_logs WHERE habit_id = ?", (habit_id,))
    db.execute("DELETE FROM habits WHERE id = ?", (habit_id,))
    db.commit()
    
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
        return jsonify({'success': True, 'message': 'Habit deleted'})
    return redirect(url_for('index'))

# Ensure DB is created when module is loaded/run
with app.app_context():
    init_db()

if __name__ == '__main__':
    app.run(debug=True, port=5000)
