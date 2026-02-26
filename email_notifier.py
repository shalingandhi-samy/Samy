"""Email notification sender using Outlook COM automation (Windows).

Sends emails through the locally installed Microsoft Outlook application
via PowerShell COM automation. No extra config or tokens needed.
"""
import asyncio
import logging
import subprocess
import tempfile
from datetime import date, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Track which assignees were notified today to avoid spam
_notified_today: dict[str, str] = {}  # email -> date_str


def _build_email_html(assignee_name: str, tasks: list[dict]) -> str:
    """Build a nicely formatted HTML email body for overdue tasks."""
    rows = ""
    for t in tasks:
        priority_color = {
            "high": "#ea1100", "medium": "#ffc220", "low": "#0053e2"
        }.get(t["priority"], "#888")
        rows += f"""
        <tr style="border-bottom:1px solid #e0e0e0;">
            <td style="padding:8px 12px;font-size:14px;">{t['title']}</td>
            <td style="padding:8px 12px;font-size:14px;color:#ea1100;font-weight:600;">
                {t.get('due_date', 'N/A')}
            </td>
            <td style="padding:8px 12px;">
                <span style="background:{priority_color};color:white;padding:2px 8px;
                    border-radius:12px;font-size:11px;text-transform:uppercase;">
                    {t['priority']}
                </span>
            </td>
        </tr>"""

    return f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;">
        <div style="background:#0053e2;color:white;padding:16px 24px;border-radius:8px 8px 0 0;">
            <h2 style="margin:0;font-size:18px;">
                \u26a0\ufe0f Overdue Task Reminder - PHL5 Safety Tracker
            </h2>
        </div>
        <div style="background:white;padding:20px 24px;border:1px solid #e0e0e0;
            border-top:none;border-radius:0 0 8px 8px;">
            <p style="font-size:14px;color:#2e2e2e;">
                Hi <strong>{assignee_name}</strong>,
            </p>
            <p style="font-size:14px;color:#2e2e2e;">
                You have <strong style="color:#ea1100;">{len(tasks)} overdue task(s)</strong>
                that need your attention:
            </p>
            <table style="width:100%;border-collapse:collapse;margin:16px 0;">
                <thead>
                    <tr style="background:#f8f8f8;">
                        <th style="padding:8px 12px;text-align:left;font-size:12px;
                            text-transform:uppercase;color:#888;">Task</th>
                        <th style="padding:8px 12px;text-align:left;font-size:12px;
                            text-transform:uppercase;color:#888;">Due Date</th>
                        <th style="padding:8px 12px;text-align:left;font-size:12px;
                            text-transform:uppercase;color:#888;">Priority</th>
                    </tr>
                </thead>
                <tbody>{rows}</tbody>
            </table>
            <p style="font-size:13px;color:#888;margin-top:16px;">
                Please update your tasks in the PHL5 Safety Tracker tracker.
            </p>
            <hr style="border:none;border-top:1px solid #e0e0e0;margin:16px 0;">
            <p style="font-size:11px;color:#aaa;">
                Sent automatically by PHL5 Safety Tracker Action Tracker \u2022 {date.today().isoformat()}
            </p>
        </div>
    </div>
    """


def _send_via_outlook(to_email: str, subject: str, html_body: str) -> bool:
    """Send an email using Outlook COM automation via PowerShell."""
    # Escape single quotes in the HTML for PowerShell
    safe_body = html_body.replace("'", "''")
    safe_subject = subject.replace("'", "''")

    ps_script = f"""
$outlook = New-Object -ComObject Outlook.Application
$mail = $outlook.CreateItem(0)
$mail.To = '{to_email}'
$mail.Subject = '{safe_subject}'
$mail.HTMLBody = '{safe_body}'
$mail.Send()
Write-Output 'OK'
"""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".ps1", delete=False, encoding="utf-8"
        ) as f:
            f.write(ps_script)
            script_path = f.name

        result = subprocess.run(
            ["powershell", "-ExecutionPolicy", "Bypass", "-File", script_path],
            capture_output=True, text=True, timeout=30,
        )
        Path(script_path).unlink(missing_ok=True)

        if "OK" in result.stdout:
            logger.info("Email sent to %s", to_email)
            return True
        else:
            logger.error("Outlook send failed: %s %s", result.stdout, result.stderr)
            return False
    except Exception as exc:
        logger.error("Email send error: %s", exc)
        return False


def _build_task_assigned_html(assignee_name: str, task: dict) -> str:
    """Build HTML email body for a newly assigned task."""
    priority_color = {
        "high": "#ea1100", "medium": "#ffc220", "low": "#0053e2"
    }.get(task.get("priority", "medium"), "#888")
    due_section = ""
    if task.get("due_date"):
        due_section = f"""
        <tr>
            <td style="padding:6px 12px;font-size:13px;color:#888;">Due Date</td>
            <td style="padding:6px 12px;font-size:14px;font-weight:600;color:#ea1100;">
                {task['due_date']}
            </td>
        </tr>"""
    desc_section = ""
    if task.get("description"):
        desc_section = f"""
        <tr>
            <td style="padding:6px 12px;font-size:13px;color:#888;">Description</td>
            <td style="padding:6px 12px;font-size:14px;">{task['description']}</td>
        </tr>"""

    return f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;">
        <div style="background:#0053e2;color:white;padding:16px 24px;border-radius:8px 8px 0 0;">
            <h2 style="margin:0;font-size:18px;">
                \U0001f4cb New Task Assigned - PHL5 Safety Tracker
            </h2>
        </div>
        <div style="background:white;padding:20px 24px;border:1px solid #e0e0e0;
            border-top:none;border-radius:0 0 8px 8px;">
            <p style="font-size:14px;color:#2e2e2e;">
                Hi <strong>{assignee_name}</strong>,
            </p>
            <p style="font-size:14px;color:#2e2e2e;">
                A new task has been assigned to you:
            </p>
            <table style="width:100%;border-collapse:collapse;margin:16px 0;
                background:#f8f8f8;border-radius:8px;">
                <tr>
                    <td style="padding:6px 12px;font-size:13px;color:#888;">Task</td>
                    <td style="padding:6px 12px;font-size:16px;font-weight:700;">
                        {task['title']}
                    </td>
                </tr>{desc_section}
                <tr>
                    <td style="padding:6px 12px;font-size:13px;color:#888;">Scheduled</td>
                    <td style="padding:6px 12px;font-size:14px;">{task['task_date']}</td>
                </tr>{due_section}
                <tr>
                    <td style="padding:6px 12px;font-size:13px;color:#888;">Priority</td>
                    <td style="padding:6px 12px;">
                        <span style="background:{priority_color};color:white;padding:2px 10px;
                            border-radius:12px;font-size:12px;text-transform:uppercase;">
                            {task.get('priority', 'medium')}
                        </span>
                    </td>
                </tr>
            </table>
            <p style="font-size:13px;color:#888;margin-top:16px;">
                Please check the PHL5 Safety Tracker tracker for full details.
            </p>
            <hr style="border:none;border-top:1px solid #e0e0e0;margin:16px 0;">
            <p style="font-size:11px;color:#aaa;">
                Sent by PHL5 Safety Tracker Action Tracker \u2022 {date.today().isoformat()}
            </p>
        </div>
    </div>
    """


async def send_task_assigned_email(
    assignee_name: str, assignee_email: str, task: dict,
) -> bool:
    """Send a 'new task assigned' email to the assignee."""
    if not assignee_email:
        return False
    subject = f"\U0001f4cb New Task: {task['title']} - PHL5 Safety Tracker"
    html_body = _build_task_assigned_html(assignee_name, task)
    return await asyncio.to_thread(
        _send_via_outlook, assignee_email, subject, html_body
    )


def _build_task_deleted_html(assignee_name: str, task: dict) -> str:
    """Build HTML email body for a deleted task notification."""
    return f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;">
        <div style="background:#ea1100;color:white;padding:16px 24px;border-radius:8px 8px 0 0;">
            <h2 style="margin:0;font-size:18px;">
                \U0001f5d1\ufe0f Task Deleted - PHL5 Safety Tracker
            </h2>
        </div>
        <div style="background:white;padding:20px 24px;border:1px solid #e0e0e0;
            border-top:none;border-radius:0 0 8px 8px;">
            <p style="font-size:14px;color:#2e2e2e;">
                Hi <strong>{assignee_name}</strong>,
            </p>
            <p style="font-size:14px;color:#2e2e2e;">
                The following task that was assigned to you has been <strong style="color:#ea1100;">deleted</strong>:
            </p>
            <table style="width:100%;border-collapse:collapse;margin:16px 0;
                background:#f8f8f8;border-radius:8px;">
                <tr>
                    <td style="padding:8px 12px;font-size:13px;color:#888;">Task</td>
                    <td style="padding:8px 12px;font-size:16px;font-weight:700;
                        text-decoration:line-through;color:#888;">
                        {task.get('title', 'Unknown')}
                    </td>
                </tr>
                <tr>
                    <td style="padding:8px 12px;font-size:13px;color:#888;">Was Due</td>
                    <td style="padding:8px 12px;font-size:14px;color:#888;">
                        {task.get('due_date') or 'No due date'}
                    </td>
                </tr>
            </table>
            <p style="font-size:13px;color:#888;margin-top:16px;">
                No further action is needed on this task.
            </p>
            <hr style="border:none;border-top:1px solid #e0e0e0;margin:16px 0;">
            <p style="font-size:11px;color:#aaa;">
                Sent by PHL5 Safety Tracker Action Tracker \u2022 {date.today().isoformat()}
            </p>
        </div>
    </div>
    """


async def send_task_deleted_email(
    assignee_name: str, assignee_email: str, task: dict,
) -> bool:
    """Send a 'task deleted' email to the assignee."""
    if not assignee_email:
        return False
    subject = f"\U0001f5d1\ufe0f Task Deleted: {task.get('title', '')} - PHL5 Safety Tracker"
    html_body = _build_task_deleted_html(assignee_name, task)
    return await asyncio.to_thread(
        _send_via_outlook, assignee_email, subject, html_body
    )


async def send_overdue_notifications(
    grouped_tasks: dict[str, list[dict]], force: bool = False,
) -> dict:
    """Send overdue email notifications to each assignee.

    Returns a summary dict with sent/skipped/failed counts.
    """
    today_str = date.today().isoformat()
    sent = 0
    skipped = 0
    failed = 0
    details: list[str] = []

    for email, tasks in grouped_tasks.items():
        # Dedup: only notify once per assignee per day unless forced
        if not force and _notified_today.get(email) == today_str:
            skipped += 1
            details.append(f"\u23ed Skipped {email} (already notified today)")
            continue

        assignee_name = tasks[0].get("assignee_name", "Team Member")
        subject = f"\u26a0\ufe0f {len(tasks)} Overdue Task(s) - PHL5 Safety Tracker"
        html_body = _build_email_html(assignee_name, tasks)

        # Run the blocking Outlook call in a thread
        success = await asyncio.to_thread(
            _send_via_outlook, email, subject, html_body
        )

        if success:
            _notified_today[email] = today_str
            sent += 1
            details.append(f"\u2705 Sent to {email} ({len(tasks)} tasks)")
        else:
            failed += 1
            details.append(f"\u274c Failed to send to {email}")

    return {"sent": sent, "skipped": skipped, "failed": failed, "details": details}
