"""
🧙 Сказочник — Telegram бот для малышей v2.1
+ Экран согласия с офертой и политикой конфиденциальности
"""

import logging
import json
import io
from pathlib import Path
from datetime import datetime

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode, ChatAction

import anthropic
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY

# ──────────────────────────────────────────────
# НАСТРОЙКИ
# ──────────────────────────────────────────────
BOT_TOKEN    = "ВАШ_TELEGRAM_BOT_TOKEN"
CLAUDE_KEY   = "ВАШ_ANTHROPIC_API_KEY"
CLAUDE_MODEL = "claude-haiku-4-5-20251001"
DB_FILE      = "users.json"
ADMIN_IDS    = []

# ⚠️ Замените на реальные ссылки после публикации документов на Telegra.ph
OFFER_URL   = "https://telegra.ph/Polzovatelskoe-soglashenie-Skazochnik"
PRIVACY_URL = "https://telegra.ph/Politika-konfidencialnosti-Skazochnik"

REF_BONUS_TALES  = 3
FREE_TALES_LIMIT = 5   # 0 = безлимит

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# СОСТОЯНИЯ
# ──────────────────────────────────────────────
(
    STATE_CONSENT,
    STATE_CHILD_NAME,
    STATE_HERO,
    STATE_LESSON,
    STATE_TOPIC,
    STATE_CUSTOM_TOPIC,
) = range(6)

# ──────────────────────────────────────────────
# КОНТЕНТ
# ──────────────────────────────────────────────
LESSONS = {
    "🤝 Дружба":      "дружба, взаимопомощь, ценность настоящих друзей",
    "💪 Смелость":    "смелость, преодоление страха, вера в себя",
    "❤️ Доброта":     "доброта, сострадание, помощь другим",
    "🧹 Трудолюбие":  "трудолюбие, усердие, радость от честного труда",
    "🤥 Честность":   "честность, правдивость, последствия лжи",
    "🌿 Природа":     "забота о природе, животных и окружающем мире",
    "👪 Семья":       "любовь к семье, уважение к родителям и старшим",
    "🎁 Щедрость":    "щедрость, умение делиться и радовать других",
}

QUICK_HEROES = [
    "🦁 Лев", "🐰 Зайчик", "🐲 Дракончик", "🧚 Фея",
    "🤖 Робот", "🧙 Волшебник", "🦊 Лисичка", "🐻 Медвежонок",
    "🦄 Единорог", "🐬 Дельфин", "🐸 Лягушонок", "⭐ Звёздочка",
]

QUICK_TOPICS = [
    "🌲 В волшебном лесу",
    "🚀 На далёкой планете",
    "🌊 В подводном царстве",
    "🏰 В сказочном замке",
    "☁️ На облаке",
    "🌸 В зачарованном саду",
]

# ──────────────────────────────────────────────
# БАЗА ДАННЫХ
# ──────────────────────────────────────────────
def load_db() -> dict:
    if Path(DB_FILE).exists():
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_db(db: dict):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

def get_user(user_id: int) -> dict:
    db = load_db()
    uid = str(user_id)
    if uid not in db:
        db[uid] = {
            "child_name":    None,
            "hero":          None,
            "tales_count":   0,
            "bonus_tales":   0,
            "referrals":     [],
            "referred_by":   None,
            "consent_given": False,
            "consent_date":  None,
            "created_at":    datetime.now().isoformat(),
            "last_tale":     None,
            "history":       [],
        }
        save_db(db)
    return db[uid]

def update_user(user_id: int, data: dict):
    db = load_db()
    uid = str(user_id)
    if uid not in db:
        db[uid] = {}
    db[uid].update(data)
    save_db(db)

def has_consent(user_id: int) -> bool:
    return get_user(user_id).get("consent_given", False)

def tales_available(user_id: int) -> int:
    if FREE_TALES_LIMIT == 0:
        return -1
    user = get_user(user_id)
    used  = user.get("tales_count", 0)
    bonus = user.get("bonus_tales", 0)
    return max(0, FREE_TALES_LIMIT + bonus - used)

def add_referral(referrer_id: int, new_user_id: int):
    db = load_db()
    ref_uid = str(referrer_id)
    new_uid = str(new_user_id)
    if ref_uid not in db or str(referrer_id) == str(new_user_id):
        return
    refs = db[ref_uid].get("referrals", [])
    if new_uid in refs:
        return
    refs.append(new_uid)
    db[ref_uid]["referrals"] = refs
    db[ref_uid]["bonus_tales"] = db[ref_uid].get("bonus_tales", 0) + REF_BONUS_TALES
    if new_uid in db:
        db[new_uid]["referred_by"] = ref_uid
    save_db(db)

# ──────────────────────────────────────────────
# ГЕНЕРАЦИЯ СКАЗКИ
# ──────────────────────────────────────────────
def generate_tale(child_name: str, hero: str, lesson_key: str, topic: str) -> dict:
    lesson_desc = LESSONS.get(lesson_key, "доброта и взаимопомощь")
    client = anthropic.Anthropic(api_key=CLAUDE_KEY)

    system = """Ты — добрый сказочник для малышей 2–7 лет. Пишешь на русском языке.

Формат ответа (строго):
НАЗВАНИЕ: [название сказки]
СКАЗКА:
[текст 180–240 слов, простой язык, 2-3 повторяющихся фразы, счастливый конец]
МОРАЛЬ: [одно предложение — главный урок]

Правила: имя ребёнка вплети органично, никаких страшных сцен, финал радостный."""

    msg = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=900,
        system=system,
        messages=[{"role": "user", "content":
            f"Имя ребёнка: {child_name}\nГерой: {hero}\nМесто: {topic}\nУрок: {lesson_desc}"
        }],
    )

    raw = msg.content[0].text.strip()
    result = {"title": "Сказка", "body": raw, "moral": ""}
    lines = raw.split("\n")
    body_lines = []
    section = None
    for line in lines:
        if line.startswith("НАЗВАНИЕ:"):
            result["title"] = line.replace("НАЗВАНИЕ:", "").strip()
        elif line.startswith("СКАЗКА:"):
            section = "body"
        elif line.startswith("МОРАЛЬ:"):
            result["moral"] = line.replace("МОРАЛЬ:", "").strip()
            section = None
        elif section == "body":
            body_lines.append(line)
    if body_lines:
        result["body"] = "\n".join(body_lines).strip()
    return result

# ──────────────────────────────────────────────
# PDF
# ──────────────────────────────────────────────
def make_pdf(tale: dict, child_name: str, hero: str, lesson: str) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
        rightMargin=2.5*cm, leftMargin=2.5*cm,
        topMargin=2.5*cm, bottomMargin=2.5*cm,
        title=tale["title"], author="Сказочник Bot")
    styles = getSampleStyleSheet()
    s_hdr  = ParagraphStyle("H", parent=styles["Normal"], fontSize=9,
               textColor=colors.HexColor("#888888"), alignment=TA_CENTER)
    s_ttl  = ParagraphStyle("T", parent=styles["Title"], fontSize=22,
               textColor=colors.HexColor("#2c3e50"), alignment=TA_CENTER, leading=28)
    s_sub  = ParagraphStyle("S", parent=styles["Normal"], fontSize=10,
               textColor=colors.HexColor("#7f8c8d"), alignment=TA_CENTER)
    s_bod  = ParagraphStyle("B", parent=styles["Normal"], fontSize=13,
               leading=22, textColor=colors.HexColor("#2c3e50"),
               alignment=TA_JUSTIFY, firstLineIndent=20)
    s_mor  = ParagraphStyle("M", parent=styles["Normal"], fontSize=11,
               leading=18, textColor=colors.HexColor("#6c3483"), alignment=TA_CENTER)
    s_ftr  = ParagraphStyle("F", parent=styles["Normal"], fontSize=8,
               textColor=colors.HexColor("#bdc3c7"), alignment=TA_CENTER)
    story = []
    today = datetime.now().strftime("%d.%m.%Y")
    story += [
        Paragraph(f"Создано: {today}  |  Сказочник Bot", s_hdr),
        Spacer(1, 0.3*cm),
        HRFlowable(width="100%", thickness=1, color=colors.HexColor("#e8e8e8")),
        Spacer(1, 0.5*cm),
        Paragraph(tale["title"], s_ttl),
        Spacer(1, 0.2*cm),
        Paragraph(f"dla {child_name}  |  geroj: {hero}  |  {lesson}", s_sub),
        Spacer(1, 0.3*cm),
        HRFlowable(width="60%", thickness=1, color=colors.HexColor("#f0e0ff")),
        Spacer(1, 0.6*cm),
    ]
    for para in [p.strip() for p in tale["body"].split("\n") if p.strip()]:
        story.append(Paragraph(para, s_bod))
    if tale.get("moral"):
        story += [
            Spacer(1, 0.6*cm),
            HRFlowable(width="80%", thickness=1, color=colors.HexColor("#e8d5f5")),
            Spacer(1, 0.3*cm),
            Paragraph(f"Mudrost: {tale['moral']}", s_mor),
            Spacer(1, 0.3*cm),
            HRFlowable(width="80%", thickness=1, color=colors.HexColor("#e8d5f5")),
        ]
    story += [
        Spacer(1, 1*cm),
        HRFlowable(width="100%", thickness=1, color=colors.HexColor("#e8e8e8")),
        Spacer(1, 0.2*cm),
        Paragraph("Skazochnik Bot — personalnye pouchitelnye skazki dlya malyshej", s_ftr),
    ]
    doc.build(story)
    return buf.getvalue()

# ──────────────────────────────────────────────
# КЛАВИАТУРЫ
# ──────────────────────────────────────────────
def kb_consent() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📄 Соглашение",         url=OFFER_URL),
            InlineKeyboardButton("🔒 Конфиденциальность",  url=PRIVACY_URL),
        ],
        [InlineKeyboardButton("✅ Принимаю условия", callback_data="consent_accept")],
        [InlineKeyboardButton("❌ Не принимаю",      callback_data="consent_decline")],
    ])

def kb_main(uid: int) -> InlineKeyboardMarkup:
    avail = tales_available(uid)
    avail_text = "∞" if avail == -1 else str(avail)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"✨ Новая сказка (осталось: {avail_text})", callback_data="new_tale")],
        [
            InlineKeyboardButton("👶 Сменить профиль",  callback_data="change_profile"),
            InlineKeyboardButton("📚 Мои сказки",       callback_data="my_tales"),
        ],
        [
            InlineKeyboardButton("🎁 Пригласить друга", callback_data="referral"),
            InlineKeyboardButton("📊 Статистика",        callback_data="my_stats"),
        ],
    ])

def kb_lessons() -> InlineKeyboardMarkup:
    buttons, row = [], []
    for key in LESSONS:
        row.append(InlineKeyboardButton(key, callback_data=f"lesson:{key}"))
        if len(row) == 2:
            buttons.append(row); row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(buttons)

def kb_heroes() -> ReplyKeyboardMarkup:
    rows = [QUICK_HEROES[i:i+3] for i in range(0, len(QUICK_HEROES), 3)]
    rows.append(["✏️ Свой герой"])
    return ReplyKeyboardMarkup(
        [[KeyboardButton(h) for h in row] for row in rows],
        resize_keyboard=True, one_time_keyboard=True,
    )

def kb_topics() -> InlineKeyboardMarkup:
    btns = [[InlineKeyboardButton(t, callback_data=f"topic:{t}")] for t in QUICK_TOPICS]
    btns.append([InlineKeyboardButton("✏️ Своё место", callback_data="topic:__custom__")])
    return InlineKeyboardMarkup(btns)

def kb_after_tale() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✨ Ещё сказку!",  callback_data="new_tale"),
            InlineKeyboardButton("📄 Скачать PDF",  callback_data="get_pdf"),
        ],
        [
            InlineKeyboardButton("🔄 Другой урок",  callback_data="change_lesson"),
            InlineKeyboardButton("🦁 Другой герой", callback_data="change_hero"),
        ],
        [InlineKeyboardButton("🏠 Меню",            callback_data="main_menu")],
    ])

# ──────────────────────────────────────────────
# ЭКРАН СОГЛАСИЯ
# ──────────────────────────────────────────────
async def show_consent_screen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (
        "🧙‍♂️ *Добро пожаловать в Сказочник!*\n\n"
        "Перед началом ознакомьтесь с условиями:\n\n"
        "📄 *Пользовательское соглашение* — правила использования\n"
        "🔒 *Политика конфиденциальности* — как хранятся данные\n\n"
        "Нажимая «✅ Принимаю условия», вы подтверждаете:\n"
        "• вам исполнилось 18 лет\n"
        "• вы являетесь родителем/опекуном ребёнка\n"
        "• согласны с условиями использования"
    )
    if update.message:
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN,
                                        reply_markup=kb_consent())
    else:
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN,
                                                      reply_markup=kb_consent())
    return STATE_CONSENT

async def cb_consent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id

    if query.data == "consent_accept":
        update_user(uid, {
            "consent_given": True,
            "consent_date":  datetime.now().isoformat(),
        })
        await query.edit_message_text(
            "✅ *Условия приняты. Добро пожаловать!*\n\n"
            "Как зовут вашего малыша? 👶\n_(напишите имя ребёнка)_",
            parse_mode=ParseMode.MARKDOWN,
        )
        return STATE_CHILD_NAME

    elif query.data == "consent_decline":
        await query.edit_message_text(
            "😔 Без принятия условий использование бота невозможно.\n\n"
            "Нажмите /start чтобы попробовать снова."
        )
        return ConversationHandler.END

    return STATE_CONSENT

# ──────────────────────────────────────────────
# /start
# ──────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid = update.effective_user.id

    # Реферал
    args = context.args
    if args and args[0].startswith("ref_"):
        try:
            referrer_id = int(args[0][4:])
            if referrer_id != uid:
                add_referral(referrer_id, uid)
                try:
                    await context.bot.send_message(referrer_id,
                        f"🎉 По вашей ссылке пришёл новый пользователь!\n"
                        f"+{REF_BONUS_TALES} бонусных сказки 🌟")
                except Exception:
                    pass
        except Exception:
            pass

    db_user = get_user(uid)

    # Нет согласия → экран согласия
    if not db_user.get("consent_given"):
        return await show_consent_screen(update, context)

    # Профиль заполнен → главное меню
    if db_user.get("child_name") and db_user.get("hero"):
        avail = tales_available(uid)
        avail_text = "безлимит" if avail == -1 else f"осталось {avail}"
        await update.message.reply_text(
            f"🌙 *С возвращением!*\n\n"
            f"👶 Ребёнок: *{db_user['child_name']}*\n"
            f"🦁 Герой: *{db_user['hero']}*\n"
            f"📚 Сказок: *{db_user.get('tales_count',0)}*\n"
            f"🎁 Доступно: *{avail_text}*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_main(uid),
        )
        return ConversationHandler.END

    # Согласие есть, профиль не заполнен
    await update.message.reply_text(
        "Как зовут вашего малыша? 👶\n_(напишите имя)_",
        parse_mode=ParseMode.MARKDOWN,
    )
    return STATE_CHILD_NAME

# ──────────────────────────────────────────────
# ПРОФИЛЬ
# ──────────────────────────────────────────────
async def state_child_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    if not (2 <= len(name) <= 30):
        await update.message.reply_text("Имя от 2 до 30 символов. Попробуйте ещё раз:")
        return STATE_CHILD_NAME
    context.user_data["child_name"] = name
    await update.message.reply_text(
        f"Прекрасно! *{name}* — красивое имя! 💫\n\nВыберите любимого героя малыша 👇",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_heroes(),
    )
    return STATE_HERO

async def state_hero(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text == "✏️ Свой герой":
        await update.message.reply_text(
            "Напишите имя героя:\n_(принцесса, пират, котёнок Мурзик...)_",
            parse_mode=ParseMode.MARKDOWN,
        )
        return STATE_HERO
    context.user_data["hero"] = text
    child = context.user_data.get("child_name", "малыш")
    update_user(update.effective_user.id, {"child_name": child, "hero": text})
    await update.message.reply_text(
        f"✨ *{child}* и *{text}* — отличная команда!\n\nВыберите урок сказки 👇",
        parse_mode=ParseMode.MARKDOWN, reply_markup=ReplyKeyboardRemove(),
    )
    await update.message.reply_text("Выберите урок:", reply_markup=kb_lessons())
    return STATE_LESSON

# ──────────────────────────────────────────────
# УРОК И МЕСТО
# ──────────────────────────────────────────────
async def cb_lesson(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    lesson = query.data.split(":", 1)[1]
    context.user_data["lesson"] = lesson
    await query.edit_message_text(
        f"Урок: *{lesson}* ✓\n\nВыберите место действия 🗺️",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_topics(),
    )
    return STATE_TOPIC

async def cb_topic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    topic_val = query.data.split(":", 1)[1]
    if topic_val == "__custom__":
        await query.edit_message_text(
            "Напишите место или тему:\n_(в космосе, на ферме, в пекарне...)_",
            parse_mode=ParseMode.MARKDOWN,
        )
        return STATE_CUSTOM_TOPIC
    context.user_data["topic"] = topic_val
    await _do_generate(update, context)
    return ConversationHandler.END

async def state_custom_topic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["topic"] = update.message.text.strip()
    await _do_generate(update, context)
    return ConversationHandler.END

# ──────────────────────────────────────────────
# ГЕНЕРАЦИЯ
# ──────────────────────────────────────────────
async def _do_generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid    = update.effective_user.id
    db_u   = get_user(uid)
    child  = context.user_data.get("child_name") or db_u.get("child_name", "малыш")
    hero   = context.user_data.get("hero")        or db_u.get("hero", "волшебник")
    lesson = context.user_data.get("lesson", "❤️ Доброта")
    topic  = context.user_data.get("topic",  "В волшебном лесу")

    avail = tales_available(uid)
    if avail == 0:
        await context.bot.send_message(
            update.effective_chat.id,
            f"😔 *Сказки закончились!*\n\nПригласи друга → +{REF_BONUS_TALES} сказки бесплатно!",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🎁 Пригласить друга", callback_data="referral")
            ]]),
        )
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    wait_msg = await context.bot.send_message(
        update.effective_chat.id,
        "🪄 *Сказочник думает...*\n✨ Готовлю сказку специально для вас!",
        parse_mode=ParseMode.MARKDOWN,
    )
    try:
        tale = generate_tale(child, hero, lesson, topic)
        context.user_data["last_tale"] = tale
        context.user_data["last_meta"] = {"child": child, "hero": hero, "lesson": lesson}

        moral_line = f"\n\n🌟 *Мораль:* _{tale['moral']}_" if tale.get("moral") else ""
        await wait_msg.delete()
        await context.bot.send_message(
            update.effective_chat.id,
            f"📖 *{tale['title']}*\n\n👶 {child}  •  {hero}  •  {lesson}\n\n{tale['body']}{moral_line}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_after_tale(),
        )

        db = load_db()
        u  = db.setdefault(str(uid), {})
        u["tales_count"] = u.get("tales_count", 0) + 1
        u["last_tale"]   = datetime.now().isoformat()
        history = u.get("history", [])
        history.append({
            "title": tale["title"], "date": datetime.now().strftime("%d.%m.%Y"),
            "lesson": lesson, "hero": hero, "child": child,
        })
        u["history"] = history[-15:]
        save_db(db)

    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await wait_msg.edit_text(
            "😔 Что-то пошло не так. Попробуйте ещё раз.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Попробовать снова", callback_data="new_tale")
            ]]),
        )

# ──────────────────────────────────────────────
# PDF
# ──────────────────────────────────────────────
async def send_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Готовлю PDF...")
    tale = context.user_data.get("last_tale")
    meta = context.user_data.get("last_meta", {})
    if not tale:
        await query.message.reply_text("Создайте новую сказку и сразу нажмите «Скачать PDF».")
        return
    wait = await query.message.reply_text("📄 Создаю PDF...")
    try:
        pdf_bytes = make_pdf(tale,
            child_name=meta.get("child", "малыш"),
            hero=meta.get("hero", "герой"),
            lesson=meta.get("lesson", ""),
        )
        safe = tale["title"][:40].replace(" ", "_").replace("/", "-")
        await wait.delete()
        await context.bot.send_document(
            chat_id=update.effective_chat.id,
            document=io.BytesIO(pdf_bytes),
            filename=f"skazka_{safe}.pdf",
            caption=f"📖 *{tale['title']}*\n\nВаша сказка в PDF ✨",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.error(f"PDF error: {e}")
        await wait.edit_text("😔 Не удалось создать PDF.")

# ──────────────────────────────────────────────
# ОБЩИЙ CALLBACK
# ──────────────────────────────────────────────
async def cb_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    uid  = update.effective_user.id
    data = query.data

    if not has_consent(uid) and data not in ("consent_accept", "consent_decline"):
        await query.edit_message_text("Нажмите /start для начала работы.")
        return ConversationHandler.END

    if data == "get_pdf":
        await send_pdf(update, context)
        return ConversationHandler.END

    if data == "new_tale":
        avail = tales_available(uid)
        if avail == 0:
            await query.edit_message_text(
                f"😔 *Сказки закончились!*\n\nПригласи друга → +{REF_BONUS_TALES} сказки.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🎁 Пригласить", callback_data="referral")],
                    [InlineKeyboardButton("🏠 Меню",       callback_data="main_menu")],
                ]),
            )
            return ConversationHandler.END
        db_user = get_user(uid)
        if db_user.get("child_name") and db_user.get("hero"):
            await query.edit_message_text("Выберите *чему учит* сказка 👇",
                parse_mode=ParseMode.MARKDOWN, reply_markup=kb_lessons())
            return STATE_LESSON
        await query.edit_message_text("Как зовут малыша? 👶")
        return STATE_CHILD_NAME

    if data == "change_profile":
        await query.edit_message_text("Как зовут малыша? 👶\n_(введите имя)_",
                                      parse_mode=ParseMode.MARKDOWN)
        return STATE_CHILD_NAME

    if data == "change_lesson":
        await query.edit_message_text("Выберите другой урок 👇",
            parse_mode=ParseMode.MARKDOWN, reply_markup=kb_lessons())
        return STATE_LESSON

    if data == "change_hero":
        await query.edit_message_text("Выберите нового героя 👇")
        await context.bot.send_message(update.effective_chat.id,
                                       "Выберите героя:", reply_markup=kb_heroes())
        return STATE_HERO

    if data == "main_menu":
        db_user = get_user(uid)
        avail = tales_available(uid)
        avail_text = "безлимит" if avail == -1 else str(avail)
        await query.edit_message_text(
            f"🏠 *Главное меню*\n\n"
            f"👶 Ребёнок: *{db_user.get('child_name','—')}*\n"
            f"🦁 Герой: *{db_user.get('hero','—')}*\n"
            f"📚 Прочитано: *{db_user.get('tales_count',0)}*\n"
            f"🎁 Осталось: *{avail_text}*",
            parse_mode=ParseMode.MARKDOWN, reply_markup=kb_main(uid),
        )
        return ConversationHandler.END

    if data == "my_tales":
        db_user = get_user(uid)
        history = db_user.get("history", [])
        if not history:
            text = "📚 Сказок пока нет. Создайте первую! ✨"
        else:
            lines = ["📚 *Последние сказки:*\n"]
            for item in reversed(history[-7:]):
                lines.append(f"• _{item['date']}_ — *{item['title']}*\n  {item.get('lesson','')} | {item.get('hero','')}")
            text = "\n".join(lines)
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✨ Новая сказка", callback_data="new_tale")],
                [InlineKeyboardButton("🏠 Меню",         callback_data="main_menu")],
            ]))
        return ConversationHandler.END

    if data == "referral":
        db_user  = get_user(uid)
        bot_info = await context.bot.get_me()
        ref_link = f"https://t.me/{bot_info.username}?start=ref_{uid}"
        await query.edit_message_text(
            f"🎁 *Реферальная программа*\n\n"
            f"За каждого приглашённого — *+{REF_BONUS_TALES} сказки бесплатно!*\n\n"
            f"Ваша ссылка:\n`{ref_link}`\n\n"
            f"👥 Приглашено: *{len(db_user.get('referrals',[]))}*\n"
            f"🌟 Бонусов получено: *{db_user.get('bonus_tales',0)}*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🏠 Меню", callback_data="main_menu")
            ]]),
        )
        return ConversationHandler.END

    if data == "my_stats":
        db_user = get_user(uid)
        avail   = tales_available(uid)
        avail_text = "безлимит" if avail == -1 else str(avail)
        since   = (db_user.get("consent_date") or db_user.get("created_at","—"))[:10]
        await query.edit_message_text(
            f"📊 *Ваша статистика*\n\n"
            f"📚 Сказок: *{db_user.get('tales_count',0)}*\n"
            f"🎁 Осталось: *{avail_text}*\n"
            f"🌟 Бонусных: *{db_user.get('bonus_tales',0)}*\n"
            f"👥 Друзей приглашено: *{len(db_user.get('referrals',[]))}*\n"
            f"📅 В боте с: *{since}*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🎁 Пригласить друга", callback_data="referral")],
                [InlineKeyboardButton("🏠 Меню",             callback_data="main_menu")],
            ]),
        )
        return ConversationHandler.END

    return ConversationHandler.END

# ──────────────────────────────────────────────
# КОМАНДЫ ПО 152-ФЗ
# ──────────────────────────────────────────────
async def cmd_mydata(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    db_user = get_user(uid)
    consent_date = (db_user.get("consent_date") or "—")[:19].replace("T", " ")
    await update.message.reply_text(
        f"📋 *Ваши данные в боте:*\n\n"
        f"🆔 Telegram ID: `{uid}`\n"
        f"👶 Имя ребёнка: *{db_user.get('child_name') or '—'}*\n"
        f"🦁 Герой: *{db_user.get('hero') or '—'}*\n"
        f"📚 Сказок создано: *{db_user.get('tales_count',0)}*\n"
        f"✅ Согласие: *{consent_date}*\n\n"
        f"Для удаления: /deletedata",
        parse_mode=ParseMode.MARKDOWN,
    )

async def cmd_deletedata(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚠️ *Удаление данных*\n\nВсе данные будут удалены безвозвратно. Уверены?",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🗑 Да, удалить", callback_data="confirm_delete"),
            InlineKeyboardButton("❌ Отмена",       callback_data="main_menu"),
        ]]),
    )

async def cb_confirm_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db = load_db()
    uid = str(update.effective_user.id)
    if uid in db:
        del db[uid]
        save_db(db)
    await query.edit_message_text("✅ Все ваши данные удалены.\nНажмите /start чтобы начать заново.")

# ──────────────────────────────────────────────
# ПРОЧИЕ КОМАНДЫ
# ──────────────────────────────────────────────
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🧙‍♂️ *Команды:*\n\n"
        "/start — главное меню\n"
        "/mydata — мои данные\n"
        "/deletedata — удалить мои данные\n"
        "/help — справка\n/cancel — отменить действие",
        parse_mode=ParseMode.MARKDOWN,
    )

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if ADMIN_IDS and update.effective_user.id not in ADMIN_IDS:
        return
    db = load_db()
    today = datetime.now().strftime("%Y-%m-%d")
    await update.message.reply_text(
        f"📊 *Статистика*\n\n"
        f"👤 Пользователей: *{len(db)}*\n"
        f"✅ Дали согласие: *{sum(1 for v in db.values() if v.get('consent_given'))}*\n"
        f"📚 Сказок всего: *{sum(v.get('tales_count',0) for v in db.values())}*\n"
        f"🔥 Активных сегодня: *{sum(1 for v in db.values() if (v.get('last_tale') or '').startswith(today))}*\n"
        f"👥 Рефералов: *{sum(len(v.get('referrals',[])) for v in db.values())}*",
        parse_mode=ParseMode.MARKDOWN,
    )

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Отменено. /start — начать снова.",
                                    reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# ──────────────────────────────────────────────
# ЗАПУСК
# ──────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", cmd_start),
            CallbackQueryHandler(cb_menu, pattern="^(new_tale|change_profile|change_hero)$"),
        ],
        states={
            STATE_CONSENT:      [CallbackQueryHandler(cb_consent, pattern="^consent_")],
            STATE_CHILD_NAME:   [MessageHandler(filters.TEXT & ~filters.COMMAND, state_child_name)],
            STATE_HERO:         [MessageHandler(filters.TEXT & ~filters.COMMAND, state_hero)],
            STATE_LESSON:       [CallbackQueryHandler(cb_lesson,  pattern="^lesson:")],
            STATE_TOPIC:        [CallbackQueryHandler(cb_topic,   pattern="^topic:")],
            STATE_CUSTOM_TOPIC: [MessageHandler(filters.TEXT & ~filters.COMMAND, state_custom_topic)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        per_message=False,
        allow_reentry=True,
    )

    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(cb_confirm_delete, pattern="^confirm_delete$"))
    app.add_handler(CallbackQueryHandler(cb_menu))
    app.add_handler(CommandHandler("help",       cmd_help))
    app.add_handler(CommandHandler("admin",      cmd_admin))
    app.add_handler(CommandHandler("mydata",     cmd_mydata))
    app.add_handler(CommandHandler("deletedata", cmd_deletedata))

    logger.info("🧙 Сказочник v2.1 запущен!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
