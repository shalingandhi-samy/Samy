"""Database setup and operations for the Action Tracker."""
import aiosqlite
import os
from datetime import date, timedelta
from typing import Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "tasks.db")


async def get_db() -> aiosqlite.Connection:
    """Get a database connection."""
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    return db


DEFAULT_CATEGORIES = [
    ("Safety", "#ea1100"),
    ("Inventory", "#0053e2"),
    ("Maintenance", "#995213"),
    ("Compliance", "#6b21a8"),
    ("Cleanliness", "#2a8703"),
    ("Customer Experience", "#ffc220"),
    ("Equipment", "#0891b2"),
    ("General", "#888888"),
]


async def init_db() -> None:
    """Initialize the database schema."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                priority TEXT NOT NULL DEFAULT 'medium',
                category_id INTEGER DEFAULT NULL,
                task_date TEXT NOT NULL,
                due_date TEXT DEFAULT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (category_id) REFERENCES categories(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                color TEXT NOT NULL DEFAULT '#888888'
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS assignees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                email TEXT DEFAULT ''
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasks_date ON tasks(task_date)
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS task_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS task_attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                original_name TEXT NOT NULL,
                content_type TEXT NOT NULL DEFAULT 'application/octet-stream',
                file_size INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER,
                task_title TEXT NOT NULL DEFAULT '',
                action TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE SET NULL
            )
        """)
        # Migrations: add columns to existing tables
        for migration in [
            "ALTER TABLE tasks ADD COLUMN priority TEXT NOT NULL DEFAULT 'medium'",
            "ALTER TABLE tasks ADD COLUMN due_date TEXT DEFAULT NULL",
            "ALTER TABLE tasks ADD COLUMN category_id INTEGER DEFAULT NULL REFERENCES categories(id)",
            "ALTER TABLE tasks ADD COLUMN assignee_id INTEGER DEFAULT NULL REFERENCES assignees(id)",
            "ALTER TABLE assignees ADD COLUMN email TEXT DEFAULT ''",
        ]:
            try:
                await db.execute(migration)
            except Exception:
                pass  # Column already exists
        # Seed default categories
        for name, color in DEFAULT_CATEGORIES:
            await db.execute(
                "INSERT OR IGNORE INTO categories (name, color) VALUES (?, ?)",
                (name, color),
            )
        await db.commit()


async def get_tasks_due_today() -> list[dict]:
    """Get all tasks with due_date = today, regardless of status."""
    today = date.today().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT t.*, c.name as category_name, c.color as category_color, "
            "a.name as assignee_name "
            "FROM tasks t LEFT JOIN categories c ON t.category_id = c.id "
            "LEFT JOIN assignees a ON t.assignee_id = a.id "
            "WHERE t.due_date = ? "
            "ORDER BY CASE t.status WHEN 'in-progress' THEN 1 WHEN 'pending' THEN 2 WHEN 'done' THEN 3 END, "
            "CASE t.priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 WHEN 'low' THEN 3 END",
            (today,),
        )
        return [dict(row) for row in await cursor.fetchall()]


async def get_tasks_for_week(week_start: date) -> dict[str, list[dict]]:
    """Get all tasks grouped by day for a given week."""
    week_end = week_start + timedelta(days=6)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT t.*, c.name as category_name, c.color as category_color, "
            "a.name as assignee_name "
            "FROM tasks t LEFT JOIN categories c ON t.category_id = c.id "
            "LEFT JOIN assignees a ON t.assignee_id = a.id "
            "WHERE t.task_date BETWEEN ? AND ? "
            "ORDER BY CASE t.priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 WHEN 'low' THEN 3 END, t.created_at",
            (week_start.isoformat(), week_end.isoformat()),
        )
        rows = await cursor.fetchall()

    tasks_by_day: dict[str, list[dict]] = {}
    for i in range(7):
        day = week_start + timedelta(days=i)
        tasks_by_day[day.isoformat()] = []

    for row in rows:
        task = dict(row)
        tasks_by_day.setdefault(task["task_date"], []).append(task)

    return tasks_by_day


async def get_task_by_id(task_id: int) -> Optional[dict]:
    """Get a single task with category and assignee info."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT t.*, c.name as category_name, c.color as category_color, "
            "a.name as assignee_name "
            "FROM tasks t LEFT JOIN categories c ON t.category_id = c.id "
            "LEFT JOIN assignees a ON t.assignee_id = a.id "
            "WHERE t.id = ?",
            (task_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def create_task(
    title: str, task_date: str, description: str = "",
    priority: str = "medium", due_date: str | None = None,
    category_id: int | None = None, assignee_id: int | None = None,
) -> dict:
    """Create a new task."""
    if priority not in ("high", "medium", "low"):
        priority = "medium"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "INSERT INTO tasks (title, description, task_date, priority, due_date, category_id, assignee_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (title, description, task_date, priority, due_date or None, category_id, assignee_id),
        )
        await db.commit()
        task_cursor = await db.execute(
            "SELECT * FROM tasks WHERE id = ?", (cursor.lastrowid,)
        )
        return dict(await task_cursor.fetchone())


async def update_task(
    task_id: int,
    title: str,
    description: str = "",
    priority: str = "medium",
    task_date: str = "",
    due_date: str | None = None,
    category_id: int | None = None,
    assignee_id: int | None = None,
) -> Optional[dict]:
    """Update an existing task's fields."""
    if priority not in ("high", "medium", "low"):
        priority = "medium"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        if not await cursor.fetchone():
            return None
        await db.execute(
            "UPDATE tasks SET title = ?, description = ?, priority = ?, "
            "task_date = ?, due_date = ?, category_id = ?, assignee_id = ?, "
            "updated_at = datetime('now') WHERE id = ?",
            (title, description, priority, task_date, due_date, category_id, assignee_id, task_id),
        )
        await db.commit()
        result = await db.execute(
            "SELECT t.*, c.name as category_name, c.color as category_color, "
            "a.name as assignee_name "
            "FROM tasks t LEFT JOIN categories c ON t.category_id = c.id "
            "LEFT JOIN assignees a ON t.assignee_id = a.id "
            "WHERE t.id = ?",
            (task_id,),
        )
        row = await result.fetchone()
        return dict(row) if row else None


async def toggle_task_status(task_id: int) -> Optional[dict]:
    """Toggle task between pending, in-progress, and done."""
    status_cycle = {"pending": "in-progress", "in-progress": "done", "done": "pending"}
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        task = await cursor.fetchone()
        if not task:
            return None
        new_status = status_cycle.get(task["status"], "pending")
        await db.execute(
            "UPDATE tasks SET status = ?, updated_at = datetime('now') WHERE id = ?",
            (new_status, task_id),
        )
        await db.commit()
        cursor = await db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        return dict(await cursor.fetchone())


async def delete_task(task_id: int) -> bool:
    """Delete a task by ID."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        await db.commit()
        return cursor.rowcount > 0


async def get_week_stats(week_start: date) -> dict:
    """Get statistics for the week."""
    week_end = week_start + timedelta(days=6)
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT status, COUNT(*) as count FROM tasks "
            "WHERE task_date BETWEEN ? AND ? GROUP BY status",
            (week_start.isoformat(), week_end.isoformat()),
        )
        rows = await cursor.fetchall()

    stats = {"pending": 0, "in-progress": 0, "done": 0, "total": 0}
    for row in rows:
        stats[row[0]] = row[1]
        stats["total"] += row[1]
    return stats


async def get_categories() -> list[dict]:
    """Get all categories."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM categories ORDER BY name")
        return [dict(row) for row in await cursor.fetchall()]


async def get_dashboard_stats() -> dict:
    """Get comprehensive dashboard statistics."""
    today = date.today()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # Overall counts
        cursor = await db.execute(
            "SELECT status, COUNT(*) as count FROM tasks GROUP BY status"
        )
        status_counts = {"pending": 0, "in-progress": 0, "done": 0}
        total = 0
        for row in await cursor.fetchall():
            status_counts[row["status"]] = row["count"]
            total += row["count"]

        # By priority
        cursor = await db.execute(
            "SELECT priority, COUNT(*) as count FROM tasks GROUP BY priority"
        )
        priority_counts = {"high": 0, "medium": 0, "low": 0}
        for row in await cursor.fetchall():
            priority_counts[row["priority"]] = row["count"]

        # By category
        cursor = await db.execute(
            "SELECT c.name, c.color, COUNT(t.id) as count "
            "FROM tasks t LEFT JOIN categories c ON t.category_id = c.id "
            "GROUP BY t.category_id ORDER BY count DESC"
        )
        category_data = [
            {"name": row["name"] or "Uncategorized", "color": row["color"] or "#888888", "count": row["count"]}
            for row in await cursor.fetchall()
        ]

        # By assignee
        cursor = await db.execute(
            "SELECT a.name, COUNT(t.id) as total, "
            "SUM(CASE WHEN t.status = 'done' THEN 1 ELSE 0 END) as done "
            "FROM tasks t LEFT JOIN assignees a ON t.assignee_id = a.id "
            "GROUP BY t.assignee_id ORDER BY total DESC"
        )
        assignee_data = [
            {"name": row["name"] or "Unassigned", "total": row["total"], "done": row["done"]}
            for row in await cursor.fetchall()
        ]

        # Overdue count
        cursor = await db.execute(
            "SELECT COUNT(*) as count FROM tasks "
            "WHERE due_date IS NOT NULL AND due_date < ? AND status != 'done'",
            (today.isoformat(),),
        )
        overdue = (await cursor.fetchone())["count"]

        # Weekly trend (last 8 weeks)
        weekly_trend = []
        for i in range(7, -1, -1):
            week_start = today - timedelta(days=today.weekday() + 2 + (7 * i))  # Saturday
            week_start = week_start - timedelta(days=(week_start.weekday() - 5) % 7)
            week_end = week_start + timedelta(days=6)
            cursor = await db.execute(
                "SELECT COUNT(*) as total, "
                "SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END) as done "
                "FROM tasks WHERE task_date BETWEEN ? AND ?",
                (week_start.isoformat(), week_end.isoformat()),
            )
            row = await cursor.fetchone()
            weekly_trend.append({
                "label": week_start.strftime("%b %d"),
                "total": row["total"] or 0,
                "done": row["done"] or 0,
            })

    return {
        "total": total,
        "status_counts": status_counts,
        "priority_counts": priority_counts,
        "category_data": category_data,
        "assignee_data": assignee_data,
        "overdue": overdue,
        "weekly_trend": weekly_trend,
    }


async def create_category(name: str, color: str = "#888888") -> dict:
    """Create a new category. Ignores duplicates."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            "INSERT OR IGNORE INTO categories (name, color) VALUES (?, ?)", (name, color)
        )
        await db.commit()
        cat_cursor = await db.execute(
            "SELECT * FROM categories WHERE name = ?", (name,)
        )
        return dict(await cat_cursor.fetchone())


async def update_category(cat_id: int, name: str, color: str) -> Optional[dict]:
    """Update a category's name and color."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            "UPDATE categories SET name = ?, color = ? WHERE id = ?",
            (name, color, cat_id),
        )
        await db.commit()
        cursor = await db.execute(
            "SELECT * FROM categories WHERE id = ?", (cat_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def delete_category(cat_id: int) -> bool:
    """Delete a category. Nullifies category_id on related tasks."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE tasks SET category_id = NULL WHERE category_id = ?", (cat_id,)
        )
        cursor = await db.execute(
            "DELETE FROM categories WHERE id = ?", (cat_id,)
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_assignees() -> list[dict]:
    """Get all assignees."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM assignees ORDER BY name")
        return [dict(row) for row in await cursor.fetchall()]


async def get_assignee_by_id(assignee_id: int) -> Optional[dict]:
    """Get a single assignee by ID."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM assignees WHERE id = ?", (assignee_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def create_assignee(name: str, email: str = "") -> dict:
    """Create a new assignee."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "INSERT OR IGNORE INTO assignees (name, email) VALUES (?, ?)", (name, email)
        )
        await db.commit()
        a_cursor = await db.execute(
            "SELECT * FROM assignees WHERE name = ?", (name,)
        )
        return dict(await a_cursor.fetchone())


async def delete_assignee(assignee_id: int) -> bool:
    """Delete an assignee and unassign their tasks."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE tasks SET assignee_id = NULL WHERE assignee_id = ?",
            (assignee_id,),
        )
        cursor = await db.execute(
            "DELETE FROM assignees WHERE id = ?", (assignee_id,)
        )
        await db.commit()
        return cursor.rowcount > 0


async def search_tasks(
    query: str = "",
    status: str = "",
    priority: str = "",
    category_id: int | None = None,
    assignee_id: int | None = None,
) -> list[dict]:
    """Search and filter tasks."""
    sql = (
        "SELECT t.*, c.name as category_name, c.color as category_color, "
        "a.name as assignee_name "
        "FROM tasks t LEFT JOIN categories c ON t.category_id = c.id "
        "LEFT JOIN assignees a ON t.assignee_id = a.id WHERE 1=1"
    )
    params: list = []
    if query:
        sql += " AND (t.title LIKE ? OR t.description LIKE ?)"
        params.extend([f"%{query}%", f"%{query}%"])
    if status:
        sql += " AND t.status = ?"
        params.append(status)
    if priority:
        sql += " AND t.priority = ?"
        params.append(priority)
    if category_id:
        sql += " AND t.category_id = ?"
        params.append(category_id)
    if assignee_id:
        sql += " AND t.assignee_id = ?"
        params.append(assignee_id)
    sql += " ORDER BY t.task_date DESC, t.created_at DESC LIMIT 100"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(sql, params)
        return [dict(row) for row in await cursor.fetchall()]


async def get_tasks_for_export(week_start: date) -> list[dict]:
    """Get all tasks for a week for CSV export."""
    week_end = week_start + timedelta(days=6)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT t.*, c.name as category_name, a.name as assignee_name "
            "FROM tasks t LEFT JOIN categories c ON t.category_id = c.id "
            "LEFT JOIN assignees a ON t.assignee_id = a.id "
            "WHERE t.task_date BETWEEN ? AND ? "
            "ORDER BY t.task_date, t.priority",
            (week_start.isoformat(), week_end.isoformat()),
        )
        return [dict(row) for row in await cursor.fetchall()]


async def get_overdue_tasks() -> list[dict]:
    """Get tasks with a due_date before today that are not done."""
    today = date.today().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT t.*, c.name as category_name, c.color as category_color, "
            "a.name as assignee_name "
            "FROM tasks t LEFT JOIN categories c ON t.category_id = c.id "
            "LEFT JOIN assignees a ON t.assignee_id = a.id "
            "WHERE t.due_date IS NOT NULL AND t.due_date < ? AND t.status != 'done' "
            "ORDER BY t.due_date ASC",
            (today,),
        )
        return [dict(row) for row in await cursor.fetchall()]


async def get_overdue_tasks_by_assignee() -> dict[str, list[dict]]:
    """Get overdue tasks grouped by assignee email. Skips assignees without email."""
    today = date.today().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT t.*, a.name as assignee_name, a.email as assignee_email "
            "FROM tasks t JOIN assignees a ON t.assignee_id = a.id "
            "WHERE t.due_date IS NOT NULL AND t.due_date < ? AND t.status != 'done' "
            "AND a.email IS NOT NULL AND a.email != '' "
            "ORDER BY a.email, t.due_date ASC",
            (today,),
        )
        rows = await cursor.fetchall()

    grouped: dict[str, list[dict]] = {}
    for row in rows:
        task = dict(row)
        email = task["assignee_email"]
        grouped.setdefault(email, []).append(task)
    return grouped


async def get_tasks_upcoming_7_days() -> dict[str, list[dict]]:
    """Get tasks with due_date in the next 7 days, grouped by due date."""
    today = date.today()
    end = today + timedelta(days=6)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT t.*, c.name as category_name, c.color as category_color, "
            "a.name as assignee_name "
            "FROM tasks t LEFT JOIN categories c ON t.category_id = c.id "
            "LEFT JOIN assignees a ON t.assignee_id = a.id "
            "WHERE t.due_date BETWEEN ? AND ? "
            "ORDER BY t.due_date, "
            "CASE t.priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 WHEN 'low' THEN 3 END",
            (today.isoformat(), end.isoformat()),
        )
        rows = await cursor.fetchall()

    grouped: dict[str, list[dict]] = {}
    for i in range(7):
        day = (today + timedelta(days=i)).isoformat()
        grouped[day] = []

    for row in rows:
        task = dict(row)
        if task["due_date"] in grouped:
            grouped[task["due_date"]].append(task)

    return grouped


# ── Task Notes ───────────────────────────────────────────────────


async def add_task_note(task_id: int, content: str) -> dict:
    """Add a timestamped note to a task."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "INSERT INTO task_notes (task_id, content) VALUES (?, ?)",
            (task_id, content),
        )
        await db.commit()
        note_cursor = await db.execute(
            "SELECT * FROM task_notes WHERE id = ?", (cursor.lastrowid,)
        )
        return dict(await note_cursor.fetchone())


async def get_task_notes(task_id: int) -> list[dict]:
    """Get all notes for a task, newest first."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM task_notes WHERE task_id = ? ORDER BY created_at DESC",
            (task_id,),
        )
        return [dict(row) for row in await cursor.fetchall()]


async def delete_task_note(note_id: int) -> bool:
    """Delete a note by ID."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("DELETE FROM task_notes WHERE id = ?", (note_id,))
        await db.commit()
        return cursor.rowcount > 0


# ── Drag & Drop ──────────────────────────────────────────────────


async def update_task_date(task_id: int, new_date: str) -> Optional[dict]:
    """Update just the task_date for a task (used by drag & drop)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            "UPDATE tasks SET task_date = ?, updated_at = datetime('now') WHERE id = ?",
            (new_date, task_id),
        )
        await db.commit()
        cursor = await db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None


# ── Task Attachments ─────────────────────────────────────────────


async def add_task_attachment(
    task_id: int, filename: str, original_name: str,
    content_type: str, file_size: int,
) -> dict:
    """Record a file attachment for a task."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "INSERT INTO task_attachments "
            "(task_id, filename, original_name, content_type, file_size) "
            "VALUES (?, ?, ?, ?, ?)",
            (task_id, filename, original_name, content_type, file_size),
        )
        await db.commit()
        row = await db.execute(
            "SELECT * FROM task_attachments WHERE id = ?", (cursor.lastrowid,)
        )
        return dict(await row.fetchone())


async def get_task_attachments(task_id: int) -> list[dict]:
    """Get all attachments for a task, newest first."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM task_attachments WHERE task_id = ? "
            "ORDER BY created_at DESC",
            (task_id,),
        )
        return [dict(row) for row in await cursor.fetchall()]


async def get_attachment_by_id(attachment_id: int) -> Optional[dict]:
    """Get a single attachment record."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM task_attachments WHERE id = ?", (attachment_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def delete_task_attachment(attachment_id: int) -> Optional[str]:
    """Delete an attachment record. Returns the filename for disk cleanup."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT filename FROM task_attachments WHERE id = ?", (attachment_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        filename = row["filename"]
        await db.execute(
            "DELETE FROM task_attachments WHERE id = ?", (attachment_id,)
        )
        await db.commit()
        return filename
