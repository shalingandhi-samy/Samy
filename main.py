"""FastAPI application for the PHL5 | SAFETY INCIDENT CORRECTIVE ACTION TRACKER | FY 2027."""
from contextlib import asynccontextmanager
from datetime import date, timedelta

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from database import (
    init_db, get_tasks_for_week, create_task, update_task, toggle_task_status,
    delete_task, get_week_stats, get_categories, create_category,
    update_category, delete_category,
    get_assignees, create_assignee, get_assignee_by_id, delete_assignee, get_task_by_id, search_tasks,
    get_tasks_for_export, get_dashboard_stats, get_tasks_due_today,
    get_overdue_tasks, add_task_note, get_task_notes, delete_task_note,
    update_task_date, add_task_attachment, get_task_attachments,
    get_attachment_by_id, delete_task_attachment,
    get_tasks_upcoming_7_days, get_overdue_tasks_by_assignee,
)
from email_notifier import send_overdue_notifications, send_task_assigned_email, send_task_deleted_email

import csv
import io
import os
import uuid
import asyncio

from fastapi import UploadFile, File
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

UPLOADS_DIR = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOADS_DIR, exist_ok=True)

# Max upload size: 10 MB
MAX_UPLOAD_SIZE = 10 * 1024 * 1024
ALLOWED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp",  # images
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv",  # documents
    ".txt", ".md", ".json",                              # text
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database on startup."""
    await init_db()
    yield


app = FastAPI(title="PHL5 | SAFETY INCIDENT CORRECTIVE ACTION TRACKER | FY 2027", lifespan=lifespan)
templates = Jinja2Templates(directory="templates")

# Walmart weeks start on Saturday (Sat-Sun-Mon-Tue-Wed-Thu-Fri)
DAY_NAMES = ["Saturday", "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]


def get_week_start(target_date: date) -> date:
    """Get the Saturday of the Walmart week containing target_date."""
    # Python weekday(): Mon=0 ... Sat=5, Sun=6
    # We want Saturday as day 0 of the week
    days_since_saturday = (target_date.weekday() - 5) % 7
    return target_date - timedelta(days=days_since_saturday)


def build_week_context(
    week_start: date, tasks_by_day: dict, stats: dict,
    categories: list[dict], assignees: list[dict],
) -> dict:
    """Build the template context for a week view."""
    days = []
    today = date.today()
    for i in range(7):
        day_date = week_start + timedelta(days=i)
        day_key = day_date.isoformat()
        days.append({
            "name": DAY_NAMES[i],
            "date": day_date,
            "date_str": day_key,
            "display_date": day_date.strftime("%b %d"),
            "is_today": day_date == today,
            "tasks": tasks_by_day.get(day_key, []),
        })

    week_end = week_start + timedelta(days=6)
    prev_week = (week_start - timedelta(days=7)).isoformat()
    next_week = (week_start + timedelta(days=7)).isoformat()

    return {
        "days": days,
        "week_start": week_start,
        "week_end": week_end,
        "week_label": f"{week_start.strftime('%b %d')} – {week_end.strftime('%b %d, %Y')}",
        "prev_week": prev_week,
        "next_week": next_week,
        "stats": stats,
        "categories": categories,
        "assignees": assignees,
    }


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, week: str | None = None):
    """Render the main page."""
    if week:
        week_start = date.fromisoformat(week)
    else:
        week_start = get_week_start(date.today())

    tasks_by_day = await get_tasks_for_week(week_start)
    stats = await get_week_stats(week_start)
    categories = await get_categories()
    assignees = await get_assignees()
    context = build_week_context(week_start, tasks_by_day, stats, categories, assignees)
    return templates.TemplateResponse("index.html", {"request": request, **context})


@app.get("/week", response_class=HTMLResponse)
async def get_week(request: Request, week: str):
    """HTMX endpoint: get week content partial."""
    week_start = date.fromisoformat(week)
    tasks_by_day = await get_tasks_for_week(week_start)
    stats = await get_week_stats(week_start)
    categories = await get_categories()
    assignees = await get_assignees()
    context = build_week_context(week_start, tasks_by_day, stats, categories, assignees)
    return templates.TemplateResponse("partials/week_content.html", {"request": request, **context})


@app.post("/tasks", response_class=HTMLResponse)
async def add_task(
    request: Request,
    title: str = Form(...),
    task_date: str = Form(...),
    description: str = Form(default=""),
    priority: str = Form(default="medium"),
    category_id: str = Form(default=""),
    due_date: str = Form(default=""),
    assignee_id: str = Form(default=""),
):
    """HTMX endpoint: create a task and return the updated day column."""
    cat_id = int(category_id) if category_id else None
    asgn_id = int(assignee_id) if assignee_id else None
    due = due_date if due_date else None
    task = await create_task(title, task_date, description, priority, due_date=due, category_id=cat_id, assignee_id=asgn_id)
    # Send assignment email (fire-and-forget, don't block response)
    if asgn_id:
        assignee = await get_assignee_by_id(asgn_id)
        if assignee and assignee.get("email"):
            task["task_date"] = task_date
            task["due_date"] = due
            task["priority"] = priority
            asyncio.create_task(send_task_assigned_email(
                assignee["name"], assignee["email"], task,
            ))
    # Return the updated week content
    week_start = get_week_start(date.fromisoformat(task_date))
    tasks_by_day = await get_tasks_for_week(week_start)
    stats = await get_week_stats(week_start)
    categories = await get_categories()
    assignees = await get_assignees()
    context = build_week_context(week_start, tasks_by_day, stats, categories, assignees)
    return templates.TemplateResponse("partials/week_content.html", {"request": request, **context})


@app.patch("/tasks/{task_id}/toggle", response_class=HTMLResponse)
async def toggle_task(request: Request, task_id: int, week: str = ""):
    """HTMX endpoint: toggle task status."""
    task = await toggle_task_status(task_id)
    if not task:
        return HTMLResponse("Task not found", status_code=404)
    week_start = get_week_start(date.fromisoformat(task["task_date"]))
    tasks_by_day = await get_tasks_for_week(week_start)
    stats = await get_week_stats(week_start)
    categories = await get_categories()
    assignees = await get_assignees()
    context = build_week_context(week_start, tasks_by_day, stats, categories, assignees)
    return templates.TemplateResponse("partials/week_content.html", {"request": request, **context})


@app.delete("/tasks/{task_id}", response_class=HTMLResponse)
async def remove_task(request: Request, task_id: int, week: str = ""):
    """HTMX endpoint: delete a task."""
    # Grab task + assignee info BEFORE deleting so we can email them
    task = await get_task_by_id(task_id)
    if task and task.get("assignee_id"):
        assignee = await get_assignee_by_id(task["assignee_id"])
        if assignee and assignee.get("email"):
            asyncio.create_task(send_task_deleted_email(
                assignee["name"], assignee["email"], task,
            ))
    # Now delete
    if week:
        week_start = date.fromisoformat(week)
    else:
        week_start = get_week_start(date.today())
    await delete_task(task_id)
    tasks_by_day = await get_tasks_for_week(week_start)
    stats = await get_week_stats(week_start)
    categories = await get_categories()
    assignees = await get_assignees()
    context = build_week_context(week_start, tasks_by_day, stats, categories, assignees)
    return templates.TemplateResponse("partials/week_content.html", {"request": request, **context})


@app.get("/assignees/options", response_class=HTMLResponse)
async def assignee_options(request: Request):
    """HTMX endpoint: return fresh <option> elements for the assignee dropdown."""
    assignees = await get_assignees()
    options_html = '<option value="">Unassigned</option>'
    for a in assignees:
        options_html += f'<option value="{a["id"]}">{a["name"]}</option>'
    return HTMLResponse(options_html)


@app.post("/assignees", response_class=HTMLResponse)
async def add_assignee(
    request: Request,
    name: str = Form(...),
    email: str = Form(default=""),
):
    """HTMX endpoint: create a new assignee and return updated option list."""
    await create_assignee(name.strip(), email.strip())
    assignees = await get_assignees()
    options_html = '<option value="">Unassigned</option>'
    for a in assignees:
        aid = a['id']
        aname = a['name']
        options_html += f'<option value="{aid}">{aname}</option>'
    return HTMLResponse(options_html)


@app.get("/assignees/manage", response_class=HTMLResponse)
async def manage_assignees(request: Request):
    """HTMX endpoint: return the assignee management list."""
    assignees = await get_assignees()
    return templates.TemplateResponse(
        "partials/assignee_list.html",
        {"request": request, "assignees": assignees},
    )


@app.post("/assignees/manage", response_class=HTMLResponse)
async def add_assignee_from_modal(
    request: Request,
    name: str = Form(...),
    email: str = Form(default=""),
):
    """HTMX endpoint: create a new assignee from modal and return updated list."""
    await create_assignee(name.strip(), email.strip())
    assignees = await get_assignees()
    return templates.TemplateResponse(
        "partials/assignee_list.html",
        {"request": request, "assignees": assignees},
    )


@app.delete("/assignees/{assignee_id}", response_class=HTMLResponse)
async def remove_assignee(request: Request, assignee_id: int):
    """HTMX endpoint: delete an assignee and return updated list."""
    await delete_assignee(assignee_id)
    assignees = await get_assignees()
    return templates.TemplateResponse(
        "partials/assignee_list.html",
        {"request": request, "assignees": assignees},
    )

# ── Category Management ─────────────────────────────────────────

@app.get("/categories", response_class=HTMLResponse)
async def categories_page(request: Request):
    """Render the category management page."""
    categories = await get_categories()
    return templates.TemplateResponse(
        "categories.html", {"request": request, "categories": categories}
    )


@app.get("/categories/list", response_class=HTMLResponse)
async def categories_list(request: Request):
    """HTMX partial: return the categories list."""
    categories = await get_categories()
    return templates.TemplateResponse(
        "partials/category_list.html", {"request": request, "categories": categories}
    )


@app.post("/categories", response_class=HTMLResponse)
async def add_category(
    request: Request,
    name: str = Form(...),
    color: str = Form(default="#0053e2"),
):
    """HTMX endpoint: create a category and return updated list."""
    await create_category(name.strip(), color)
    categories = await get_categories()
    return templates.TemplateResponse(
        "partials/category_list.html", {"request": request, "categories": categories}
    )


@app.put("/categories/{cat_id}", response_class=HTMLResponse)
async def edit_category(
    request: Request,
    cat_id: int,
    name: str = Form(...),
    color: str = Form(default="#0053e2"),
):
    """HTMX endpoint: update a category and return updated list."""
    await update_category(cat_id, name.strip(), color)
    categories = await get_categories()
    return templates.TemplateResponse(
        "partials/category_list.html", {"request": request, "categories": categories}
    )


@app.delete("/categories/{cat_id}", response_class=HTMLResponse)
async def remove_category(request: Request, cat_id: int):
    """HTMX endpoint: delete a category and return updated list."""
    await delete_category(cat_id)
    categories = await get_categories()
    return templates.TemplateResponse(
        "partials/category_list.html", {"request": request, "categories": categories}
    )


@app.get("/tasks/{task_id}/detail", response_class=HTMLResponse)
async def task_detail(request: Request, task_id: int):
    """HTMX endpoint: return task detail partial for the modal."""
    task = await get_task_by_id(task_id)
    if not task:
        return HTMLResponse("Task not found", status_code=404)
    return templates.TemplateResponse(
        "partials/task_detail.html",
        {"request": request, "task": task, "today": date.today().isoformat()},
    )


@app.get("/tasks/{task_id}/edit", response_class=HTMLResponse)
async def task_edit_form(request: Request, task_id: int):
    """HTMX endpoint: return the edit form partial for a task."""
    task = await get_task_by_id(task_id)
    if not task:
        return HTMLResponse("Task not found", status_code=404)
    categories = await get_categories()
    assignees = await get_assignees()
    return templates.TemplateResponse(
        "partials/task_edit.html",
        {"request": request, "task": task, "categories": categories, "assignees": assignees},
    )


@app.put("/tasks/{task_id}", response_class=HTMLResponse)
async def edit_task(
    request: Request,
    task_id: int,
    title: str = Form(...),
    task_date: str = Form(...),
    description: str = Form(default=""),
    priority: str = Form(default="medium"),
    category_id: str = Form(default=""),
    due_date: str = Form(default=""),
    assignee_id: str = Form(default=""),
):
    """HTMX endpoint: update a task and return the updated week content."""
    cat_id = int(category_id) if category_id else None
    asgn_id = int(assignee_id) if assignee_id else None
    due = due_date if due_date else None
    task = await update_task(
        task_id, title, description, priority, task_date,
        due_date=due, category_id=cat_id, assignee_id=asgn_id,
    )
    if not task:
        return HTMLResponse("Task not found", status_code=404)
    week_start = get_week_start(date.fromisoformat(task["task_date"]))
    tasks_by_day = await get_tasks_for_week(week_start)
    stats = await get_week_stats(week_start)
    categories = await get_categories()
    assignees = await get_assignees()
    context = build_week_context(week_start, tasks_by_day, stats, categories, assignees)
    return templates.TemplateResponse("partials/week_content.html", {"request": request, **context})


# ── Due Today Sidebar ───────────────────────────────────────────

@app.get("/due-today", response_class=HTMLResponse)
async def due_today(request: Request):
    """HTMX endpoint: return the due-today sidebar partial."""
    tasks = await get_tasks_due_today()
    today = date.today().isoformat()
    return templates.TemplateResponse(
        "partials/due_today.html",
        {"request": request, "tasks": tasks, "today": today},
    )


# ── Upcoming 7 Days Sidebar ─────────────────────────────────────

@app.get("/upcoming-7", response_class=HTMLResponse)
async def upcoming_7_days(request: Request):
    """HTMX endpoint: return tasks due in the next 7 days."""
    grouped = await get_tasks_upcoming_7_days()
    today = date.today().isoformat()
    return templates.TemplateResponse(
        "partials/upcoming_7.html",
        {"request": request, "grouped": grouped, "today": today},
    )


# ── Overdue Notifications ──────────────────────────────────────

@app.get("/overdue", response_class=HTMLResponse)
async def overdue_tasks(request: Request):
    """HTMX endpoint: return overdue tasks for toast notifications."""
    tasks = await get_overdue_tasks()
    return templates.TemplateResponse(
        "partials/overdue_toast.html",
        {"request": request, "tasks": tasks},
    )


@app.get("/overdue-list", response_class=HTMLResponse)
async def overdue_list(request: Request):
    """HTMX endpoint: return overdue tasks list for the notification panel."""
    tasks = await get_overdue_tasks()
    return templates.TemplateResponse(
        "partials/overdue_list.html",
        {"request": request, "tasks": tasks},
    )


@app.post("/notify-overdue", response_class=HTMLResponse)
async def notify_overdue(request: Request, force: str = Form(default="")):
    """Send email notifications to assignees with overdue tasks."""
    grouped = await get_overdue_tasks_by_assignee()
    if not grouped:
        return HTMLResponse(
            '<div class="p-3 text-sm text-walmart-gray-100 text-center fade-in">'
            '\u2705 No overdue tasks with emailable assignees.</div>'
        )
    result = await send_overdue_notifications(grouped, force=bool(force))
    # Build response HTML
    lines = "".join(
        f'<div class="text-xs py-1">{d}</div>' for d in result["details"]
    )
    summary_color = "text-walmart-green-100" if result["failed"] == 0 else "text-walmart-red-100"
    return HTMLResponse(
        f'<div class="p-3 fade-in">'
        f'<div class="text-sm font-semibold {summary_color} mb-2">'
        f'\u2709\ufe0f Sent: {result["sent"]} | Skipped: {result["skipped"]} | Failed: {result["failed"]}</div>'
        f'{lines}</div>'
    )


# ── Task Notes / Comments ─────────────────────────────────────

@app.get("/tasks/{task_id}/notes", response_class=HTMLResponse)
async def task_notes_list(request: Request, task_id: int):
    """HTMX endpoint: return notes list for a task."""
    notes = await get_task_notes(task_id)
    return templates.TemplateResponse(
        "partials/task_notes.html",
        {"request": request, "notes": notes, "task_id": task_id},
    )


@app.post("/tasks/{task_id}/notes", response_class=HTMLResponse)
async def add_note(
    request: Request,
    task_id: int,
    content: str = Form(...),
):
    """HTMX endpoint: add a note to a task and return updated list."""
    await add_task_note(task_id, content.strip())
    notes = await get_task_notes(task_id)
    return templates.TemplateResponse(
        "partials/task_notes.html",
        {"request": request, "notes": notes, "task_id": task_id},
    )


@app.delete("/notes/{note_id}", response_class=HTMLResponse)
async def remove_note(request: Request, note_id: int, task_id: int = Query(...)):
    """HTMX endpoint: delete a note and return updated list."""
    await delete_task_note(note_id)
    notes = await get_task_notes(task_id)
    return templates.TemplateResponse(
        "partials/task_notes.html",
        {"request": request, "notes": notes, "task_id": task_id},
    )


# ── Drag & Drop ────────────────────────────────────────────────


# ── Task Attachments ─────────────────────────────────────────

@app.get("/tasks/{task_id}/attachments", response_class=HTMLResponse)
async def task_attachments_list(request: Request, task_id: int):
    """HTMX endpoint: return attachments list for a task."""
    attachments = await get_task_attachments(task_id)
    return templates.TemplateResponse(
        "partials/task_attachments.html",
        {"request": request, "attachments": attachments, "task_id": task_id},
    )


@app.post("/tasks/{task_id}/attachments", response_class=HTMLResponse)
async def upload_attachment(
    request: Request,
    task_id: int,
    file: UploadFile = File(...),
):
    """HTMX endpoint: upload a file attachment to a task."""
    # Validate extension
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return HTMLResponse(
            f'<div class="text-sm text-walmart-red-100 p-2">❌ File type "{ext}" not allowed.</div>',
            status_code=400,
        )
    # Read and validate size
    contents = await file.read()
    if len(contents) > MAX_UPLOAD_SIZE:
        return HTMLResponse(
            '<div class="text-sm text-walmart-red-100 p-2">❌ File too large (max 10 MB).</div>',
            status_code=400,
        )
    # Save to disk with a unique name
    unique_name = f"{uuid.uuid4().hex}{ext}"
    file_path = os.path.join(UPLOADS_DIR, unique_name)
    with open(file_path, "wb") as f:
        f.write(contents)
    # Record in DB
    await add_task_attachment(
        task_id, unique_name, file.filename or "unnamed",
        file.content_type or "application/octet-stream", len(contents),
    )
    attachments = await get_task_attachments(task_id)
    return templates.TemplateResponse(
        "partials/task_attachments.html",
        {"request": request, "attachments": attachments, "task_id": task_id},
    )


@app.get("/attachments/{attachment_id}")
async def serve_attachment(attachment_id: int):
    """Serve an attachment file for download or inline viewing."""
    attachment = await get_attachment_by_id(attachment_id)
    if not attachment:
        return HTMLResponse("Attachment not found", status_code=404)
    file_path = os.path.join(UPLOADS_DIR, attachment["filename"])
    if not os.path.exists(file_path):
        return HTMLResponse("File not found on disk", status_code=404)
    return FileResponse(
        file_path,
        filename=attachment["original_name"],
        media_type=attachment["content_type"],
    )


@app.delete("/attachments/{attachment_id}", response_class=HTMLResponse)
async def remove_attachment(
    request: Request, attachment_id: int, task_id: int = Query(...),
):
    """HTMX endpoint: delete an attachment."""
    filename = await delete_task_attachment(attachment_id)
    if filename:
        file_path = os.path.join(UPLOADS_DIR, filename)
        if os.path.exists(file_path):
            os.remove(file_path)
    attachments = await get_task_attachments(task_id)
    return templates.TemplateResponse(
        "partials/task_attachments.html",
        {"request": request, "attachments": attachments, "task_id": task_id},
    )


@app.patch("/tasks/{task_id}/move", response_class=HTMLResponse)
async def move_task(
    request: Request,
    task_id: int,
    new_date: str = Form(...),
    week: str = Form(default=""),
):
    """HTMX endpoint: move task to a new date (drag & drop)."""
    await update_task_date(task_id, new_date)
    week_start = get_week_start(date.fromisoformat(new_date))
    tasks_by_day = await get_tasks_for_week(week_start)
    stats = await get_week_stats(week_start)
    categories = await get_categories()
    assignees = await get_assignees()
    context = build_week_context(week_start, tasks_by_day, stats, categories, assignees)
    return templates.TemplateResponse("partials/week_content.html", {"request": request, **context})


@app.get("/search", response_class=HTMLResponse)
async def search_view(
    request: Request,
    q: str = Query(default=""),
    status: str = Query(default=""),
    priority: str = Query(default=""),
    category_id: str = Query(default=""),
    assignee_id: str = Query(default=""),
):
    """HTMX endpoint: search and filter tasks."""
    cat_id = int(category_id) if category_id else None
    asgn_id = int(assignee_id) if assignee_id else None
    results = await search_tasks(q, status, priority, cat_id, asgn_id)
    categories = await get_categories()
    assignees = await get_assignees()
    today = date.today().isoformat()
    return templates.TemplateResponse(
        "partials/search_results.html",
        {"request": request, "results": results, "query": q,
         "categories": categories, "assignees": assignees, "today": today},
    )


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Render the dashboard page."""
    stats = await get_dashboard_stats()
    return templates.TemplateResponse(
        "dashboard.html", {"request": request, **stats}
    )


@app.get("/export")
async def export_csv(week: str = ""):
    """Export current week's tasks as CSV."""
    if week:
        week_start = date.fromisoformat(week)
    else:
        week_start = get_week_start(date.today())
    tasks = await get_tasks_for_export(week_start)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Title", "Description", "Status", "Priority",
        "Category", "Assignee", "Task Date", "Due Date",
        "Created At", "Updated At",
    ])
    for t in tasks:
        writer.writerow([
            t["title"], t["description"], t["status"], t["priority"],
            t.get("category_name", ""), t.get("assignee_name", ""),
            t["task_date"], t.get("due_date", ""),
            t["created_at"], t["updated_at"],
        ])

    output.seek(0)
    week_end = week_start + timedelta(days=6)
    filename = f"phl5-safety-tracker_{week_start.isoformat()}_to_{week_end.isoformat()}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8101, reload=True)
