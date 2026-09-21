"""アプリ内通知・メールの送信の組み立て（設計書 §5.3 (12)、§5.7、AD-1 U5）。

メール本文には、渡された文字列（全員向けの版の ``summary_for_mail``・質問文・
無進捗の日時）とアプリへのリンクだけを入れる。ソースの原文や管理者限定の内容が
紛れ込む余地は無い（呼び出し側がその制約を守った文字列だけを渡す）。

送信に失敗しても呼び出し元（``run.py``・``agent.py``・``noprogress.py``）の処理は
失敗にせず、``notifications_log`` に記録する（成功なら ``provider_message_id``
付き、失敗なら ``NULL`` のまま。AD-1 U5：質問の回答はアプリ内だけ、メールには
回答画面へのリンクだけを載せる）。
"""

from uuid import UUID

import httpx
import psycopg

from ai_hackathon_team_a import mail
from ai_hackathon_team_a.worker_settings import WorkerSettings


def _app_url(settings: WorkerSettings, path: str) -> str:
    return f"{str(settings.app_base_url).rstrip('/')}{path}"


def record_notification(
    conn: psycopg.Connection,
    *,
    user_id: UUID,
    project_id: UUID,
    kind: str,
    title: str,
    question_id: UUID | None = None,
) -> UUID:
    """アプリ内通知（``notifications``）を1件書く。"""

    row = conn.execute(
        "INSERT INTO notifications (user_id, project_id, kind, title, question_id) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (user_id, project_id, kind, title, question_id),
    ).fetchone()
    return row[0]


def _send_and_log(
    conn: psycopg.Connection,
    *,
    project_id: UUID,
    report_id: UUID | None,
    kind: str,
    to_email: str,
    subject: str,
    text_body: str,
    settings: WorkerSettings,
    transport: httpx.BaseTransport | None,
) -> None:
    """1通送り、成否にかかわらず ``notifications_log`` に残す。

    失敗すれば ``provider_message_id`` は ``NULL`` のまま。
    """

    provider_message_id: str | None
    try:
        provider_message_id = mail.send_mail(
            to=to_email,
            subject=subject,
            text_body=text_body,
            settings=settings,
            transport=transport,
        )
    except mail.MailError:
        provider_message_id = None

    conn.execute(
        "INSERT INTO notifications_log "
        "(project_id, report_id, kind, recipients, provider_message_id) "
        "VALUES (%s, %s, %s, %s, %s)",
        (project_id, report_id, kind, [to_email], provider_message_id),
    )


def send_progress_notifications(
    conn: psycopg.Connection,
    *,
    project_id: UUID,
    report_id: UUID,
    version_no: int,
    summary_for_mail: str,
    settings: WorkerSettings,
    transport: httpx.BaseTransport | None = None,
) -> None:
    """進捗のメール・アプリ内通知（設計書 §5.3 (12)）。

    宛先は ``notify_on_progress = true`` の所属者。本文は全員向けの版の
    ``summary_for_mail`` とアプリへのリンクだけ（spec G1、D13）。
    """

    recipients = conn.execute(
        "SELECT user_id, email FROM project_members "
        "WHERE project_id = %s AND notify_on_progress = true",
        (project_id,),
    ).fetchall()
    if not recipients:
        return

    link = _app_url(settings, f"/projects/{project_id}")
    subject = "進捗レポートが更新されました"
    body = f"{summary_for_mail}\n\n詳しくはアプリでご確認ください：\n{link}"

    for user_id, email in recipients:
        record_notification(
            conn,
            user_id=user_id,
            project_id=project_id,
            kind="report",
            title=f"進捗レポートが更新されました（v{version_no}）",
        )
        _send_and_log(
            conn,
            project_id=project_id,
            report_id=report_id,
            kind="report",
            to_email=email,
            subject=subject,
            text_body=body,
            settings=settings,
            transport=transport,
        )


def send_question_notification(
    conn: psycopg.Connection,
    *,
    project_id: UUID,
    user_id: UUID,
    email: str,
    question_id: UUID,
    question_text: str,
    settings: WorkerSettings,
    transport: httpx.BaseTransport | None = None,
) -> None:
    """質問のアプリ内通知・メール（設計書 §5.3 (7)(12)、【C-1】、AD-1 U5）。

    メールには質問文と回答画面（``/questions/{qid}``）へのリンクだけを載せる。
    回答はアプリ内の回答欄でだけ受け付ける。
    """

    record_notification(
        conn,
        user_id=user_id,
        project_id=project_id,
        kind="question",
        title="確認したいことがあります",
        question_id=question_id,
    )
    link = _app_url(settings, f"/questions/{question_id}")
    subject = "確認したいことがあります"
    body = f"{question_text}\n\nこちらから回答してください：\n{link}"
    _send_and_log(
        conn,
        project_id=project_id,
        report_id=None,
        kind="question",
        to_email=email,
        subject=subject,
        text_body=body,
        settings=settings,
        transport=transport,
    )


def send_no_progress_notification(
    conn: psycopg.Connection,
    *,
    project_id: UUID,
    user_id: UUID,
    email: str,
    last_progress_text: str,
    settings: WorkerSettings,
    transport: httpx.BaseTransport | None = None,
) -> None:
    """無進捗の通知（設計書 §5.7、【B-X4】）。本文にはその立場で見える最後の進捗の日時だけ。"""

    record_notification(
        conn,
        user_id=user_id,
        project_id=project_id,
        kind="no_progress",
        title="しばらく進捗が記録されていません",
    )
    link = _app_url(settings, f"/projects/{project_id}")
    subject = "しばらく進捗が記録されていません"
    body = f"{last_progress_text}\n\n詳しくはアプリでご確認ください：\n{link}"
    _send_and_log(
        conn,
        project_id=project_id,
        report_id=None,
        kind="no_progress",
        to_email=email,
        subject=subject,
        text_body=body,
        settings=settings,
        transport=transport,
    )
