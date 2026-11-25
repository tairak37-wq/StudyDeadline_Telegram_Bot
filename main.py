import os
import re
import sqlite3
import logging
from datetime import datetime, date, time
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, ConversationHandler, filters
)

BOT_TOKEN = os.environ["BOT_TOKEN"]
TIMEZONE = os.getenv("TIMEZONE", "Europe/Berlin")
DB_PATH = os.getenv("DB_PATH", "studydeadline.sqlite3")
TZ = ZoneInfo(TIMEZONE)

TITLE, DEADLINE_DATE = range(2)
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO
)
log = logging.getLogger("studydeadline")


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS deadlines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                due_date TEXT NOT NULL,
                completed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS sent_reminders (
                deadline_id INTEGER NOT NULL,
                days_before INTEGER NOT NULL,
                sent_at TEXT NOT NULL,
                PRIMARY KEY (deadline_id, days_before)
            )
        """)


def home_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить дедлайн", callback_data="add")],
        [InlineKeyboardButton("📋 Все дедлайны", callback_data="list")],
        [InlineKeyboardButton("✅ Выполненные", callback_data="done_list")],
    ])


def main_text():
    return (
        "📚 *StudyDeadline*\n\n"
        "Твой учебный планировщик. Здесь можно добавлять задания, "
        "следить за сроками и отмечать выполненное."
    )


def get_deadlines(user_id, completed=0):
    with db() as con:
        return con.execute(
            "SELECT * FROM deadlines WHERE user_id=? AND completed=? ORDER BY due_date, id",
            (user_id, completed)
        ).fetchall()


def days_left(due_date):
    return (date.fromisoformat(due_date) - datetime.now(TZ).date()).days


def format_deadline(row):
    left = days_left(row["due_date"])
    d = date.fromisoformat(row["due_date"]).strftime("%d.%m.%Y")
    if left < 0:
        status = f"Просрочено на {abs(left)} дн."
    elif left == 0:
        status = "Сдать сегодня"
    elif left == 1:
        status = "Остался 1 день"
    else:
        status = f"Осталось {left} дн."
    return f"• *{row['title']}*\n  📅 {d} — _{status}_"


async def show_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            main_text(), parse_mode="Markdown", reply_markup=home_keyboard()
        )
    else:
        await update.effective_message.reply_text(
            main_text(), parse_mode="Markdown", reply_markup=home_keyboard()
        )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_home(update, context)


async def list_deadlines(update: Update, context: ContextTypes.DEFAULT_TYPE, completed=0):
    query = update.callback_query
    await query.answer()
    rows = get_deadlines(update.effective_user.id, completed)
    heading = "✅ *Выполненные дедлайны*" if completed else "📋 *Твои активные дедлайны*"
    if not rows:
        body = "Пока здесь пусто."
    else:
        body = "\n\n".join(format_deadline(r) for r in rows)

    buttons = []
    if not completed:
        for row in rows:
            buttons.append([InlineKeyboardButton(
                f"☑️ Выполнено: {row['title'][:35]}",
                callback_data=f"complete:{row['id']}"
            )])
    buttons.append([InlineKeyboardButton("➕ Добавить дедлайн", callback_data="add")])
    buttons.append([InlineKeyboardButton("🏠 На главную", callback_data="home")])
    await query.edit_message_text(
        heading + "\n\n" + body,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("✏️ Напиши название задания или дедлайна:")
    return TITLE


async def receive_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = update.message.text.strip()
    if not title or len(title) > 120:
        await update.message.reply_text("Название должно быть от 1 до 120 символов. Попробуй ещё раз:")
        return TITLE
    context.user_data["new_deadline_title"] = title
    await update.message.reply_text(
        "📅 Теперь введи дату сдачи в формате *ДД.ММ.ГГГГ* (например, 15.12.2026):",
        parse_mode="Markdown"
    )
    return DEADLINE_DATE


async def receive_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    try:
        due = datetime.strptime(raw, "%d.%m.%Y").date()
    except ValueError:
        await update.message.reply_text("Не распознала дату. Введи её как ДД.ММ.ГГГГ, например 15.12.2026:")
        return DEADLINE_DATE

    if due < datetime.now(TZ).date():
        await update.message.reply_text("Эта дата уже прошла. Введи сегодняшнюю или будущую дату:")
        return DEADLINE_DATE

    title = context.user_data.pop("new_deadline_title")
    with db() as con:
        con.execute(
            "INSERT INTO deadlines(user_id,title,due_date,created_at) VALUES(?,?,?,?)",
            (update.effective_user.id, title, due.isoformat(), datetime.now(TZ).isoformat())
        )

    await update.message.reply_text(
        f"🎉 Дедлайн добавлен!\n\n*{title}*\n📅 {due.strftime('%d.%m.%Y')}",
        parse_mode="Markdown",
        reply_markup=home_keyboard()
    )
    return ConversationHandler.END


async def cancel_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("new_deadline_title", None)
    await update.message.reply_text("Добавление отменено.", reply_markup=home_keyboard())
    return ConversationHandler.END


async def complete_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    try:
        deadline_id = int(q.data.split(":", 1)[1])
    except (ValueError, IndexError):
        return
    with db() as con:
        con.execute(
            "UPDATE deadlines SET completed=1 WHERE id=? AND user_id=?",
            (deadline_id, update.effective_user.id)
        )
    await q.edit_message_text("🎉 Отличная работа! Дедлайн отмечен как выполненный.")
    # Return to the main menu as requested.
    await q.message.reply_text(main_text(), parse_mode="Markdown", reply_markup=home_keyboard())


async def reminder_job(context: ContextTypes.DEFAULT_TYPE):
    today = datetime.now(TZ).date()
    with db() as con:
        rows = con.execute("""
            SELECT * FROM deadlines
            WHERE completed=0 AND due_date>=?
        """, (today.isoformat(),)).fetchall()

        for row in rows:
            remaining = (date.fromisoformat(row["due_date"]) - today).days
            if remaining >= 14:
                reminder_days = (14, 7, 1)
            elif remaining >= 1:
                reminder_days = (3, 1)
            else:
                reminder_days = ()

            if remaining not in reminder_days:
                continue

            already = con.execute(
                "SELECT 1 FROM sent_reminders WHERE deadline_id=? AND days_before=?",
                (row["id"], remaining)
            ).fetchone()
            if already:
                continue

            if remaining == 14:
                when = "через 2 недели"
            elif remaining == 7:
                when = "через неделю"
            elif remaining == 3:
                when = "через 3 дня"
            else:
                when = "завтра"

            due = date.fromisoformat(row["due_date"]).strftime("%d.%m.%Y")
            message = (
                f"⏰ *Напоминание о дедлайне!*\n\n"
                f"*{row['title']}*\n"
                f"Сдать: {due}\n"
                f"До сдачи: {when}."
            )
            try:
                await context.bot.send_message(
                    chat_id=row["user_id"], text=message, parse_mode="Markdown"
                )
                con.execute(
                    "INSERT OR IGNORE INTO sent_reminders(deadline_id,days_before,sent_at) VALUES(?,?,?)",
                    (row["id"], remaining, datetime.now(TZ).isoformat())
                )
            except Exception:
                log.exception("Не удалось отправить напоминание для дедлайна %s", row["id"])


async def unknown_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Используй кнопки меню или команду /start.", reply_markup=home_keyboard())


def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    conversation = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_start, pattern="^add$")],
        states={
            TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_title)],
            DEADLINE_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_date)],
        },
        fallbacks=[CommandHandler("cancel", cancel_add)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conversation)
    app.add_handler(CallbackQueryHandler(lambda u, c: show_home(u, c), pattern="^home$"))
    app.add_handler(CallbackQueryHandler(lambda u, c: list_deadlines(u, c, 0), pattern="^list$"))
    app.add_handler(CallbackQueryHandler(lambda u, c: list_deadlines(u, c, 1), pattern="^done_list$"))
    app.add_handler(CallbackQueryHandler(complete_deadline, pattern=r"^complete:\d+$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unknown_text))

    # Daily reminders at 09:00 in TIMEZONE. JobQueue requires python-telegram-bot[job-queue].
    app.job_queue.run_daily(reminder_job, time=time(hour=9, minute=0, tzinfo=TZ))
    log.info("StudyDeadline запущен; часовой пояс: %s", TIMEZONE)
    app.run_polling()


if __name__ == "__main__":
    main()
