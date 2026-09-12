import os
import logging
import re
import time
import threading
import asyncio
from datetime import datetime
import pytz
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv

from telegram import Update, ChatPermissions, ReactionTypeEmoji
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters
from supabase import create_client, Client
from google import genai
from google.genai import types

load_dotenv()
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

supabase_client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

ALLOWED_USERS = [1196500724, 6922089212, 522205183] 
ghaleb_last_reply = {}
ai_enabled = True

ALLOWED_GROUP_REACTIONS = [
    "❤️", "👍", "🤝", "😁", "💔", "🔥", "👌", "🙏", "👎",
    "💯", "🤣", "😍", "🥱", "🥲", "😐", "🤷‍♂️", "😢", "💻",
    "🗿", "🤔", "🥰", "👏", "🤯", "😱", "🤬", "🎉", "🤩",
    "🤮", "💩", "🕊", "🤡", "🥴", "🐳", "❤‍🔥", "🌚",
    "🌭", "⚡", "🍌", "🏆", "🤨", "🍓", "🍾", "💋",
    "😈", "😴", "😭", "🤓", "👻", "👀", "🎃", "🙈", "😇",
    "✍", "🤗", "🫡", "🎅", "🎄", "☃", "💅", "🤪", "🆒",
    "💘", "🙉", "🦄", "💊", "🙊", "🕶", "👾", "🤷", "🤷‍♀️", "😡"
]

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain; charset=utf-8')
        self.end_headers()
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain; charset=utf-8')
        self.end_headers()
        self.wfile.write(b"Ghaleb Telegram Bot is alive!")
    def log_message(self, format, *args):
        return

def run_health_check_server():
    port = int(os.environ.get('PORT', 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

async def save_message(user_id, username, chat_id, message_id, text, is_bot=False):
    try:
        supabase_client.table('messages_tg').insert({
            'user_id': user_id,
            'username': username,
            'chat_id': chat_id,
            'message_id': message_id,
            'text': text,
            'is_bot': is_bot
        }).execute()
    except Exception as e:
        logging.error(f"Database error: {e}")

async def save_bot_message(chat_id, message_id):
    await save_message(0, 'Bot', chat_id, message_id, '', is_bot=True)

async def get_user_id_by_username(target_username):
    target_username = target_username.replace('@', '').lower()
    try:
        res = supabase_client.table('messages_tg').select('user_id').ilike('username', f'%{target_username}%').limit(1).execute()
        if res.data:
            return res.data[0]['user_id']
        return None
    except Exception:
        return None

def get_permanent_memories():
    try:
        res = supabase_client.table('bot_memory_tg').select('key, value').execute()
        if res.data:
            return "\n".join([f"- {row['key']}: {row['value']}" for row in res.data])
        return "هیچ دانشی ثبت نشده است."
    except Exception:
        return ""

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user.first_name
    msg = await update.message.reply_text(f"🤖 سلام {user}! من غالب هستم. برای راهنما /help را بزن.\nنسخه ربات تلگرام: 4.6\nتازه ها:\n- سانسور هوشمند تا اطلاع ثانوی خاموش شد.")
    await save_bot_message(chat_id, msg.message_id)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    help_text = """
<b>📚 راهنمای جامع بازوی هوشمند غالب:</b>

<b>🔹 آمار و اطلاعات:</b>
/stats - 📈 نمایش آمار کل گروه و کاربران برتر
/count_group - 📊 شمارش تمام پیام های گروه
/count_user - 👤 تعداد پیام های شما (یا کاربر خاص: <code>/count_user @id</code>)
/memories - 🧠 مشاهده حافظه بلندمدت و فکت های ربات

<b>🔸 دستورات ادمین:</b>
/remember [نام] : [توضیحات] - 📌 سپردن فکت جدید به حافظه ربات
/forget [نام] - 🗑️ پاک کردن یک فکت از حافظه
/tagall [متن] - 📣 صدا زدن همگانی اعضا با لینک
/mute [id] [ساعت] - 🤫 سکوت کاربر برای زمان مشخص
/unmute [id] - 🗣️ رفع محدودیت سکوت
/ban_media [id] - 🚫 بستن ارسال عکس و رسانه برای کاربر
/delete_last [تعداد] - 🧹 حذف پیام های آخر کل گروه
/delete_user [id] [تعداد] - 🧹 حذف پیام های آخر یک کاربر
/ai_off - 🛑 خاموش کردن چت هوشمند
/ai_on - ✅ روشن کردن چت هوشمند
/text - 🎤 در ریپلای یک ویس بزنید تا متن آن استخراج شود

<b>🔒 مدیریت قفل شبانه گروه:</b>
/lock_schedule [ساعت شروع] [ساعت پایان] - تنظیم قفل خودکار گروه (مثال: <code>/lock_schedule 23 7</code>)
/unlock_schedule - حذف قفل خودکار زمان بندی شده
/lock_status - مشاهده تنظیمات فعلی قفل گروه
"""
    msg = await update.message.reply_text(help_text, parse_mode='HTML')
    await save_bot_message(chat_id, msg.message_id)

async def lock_schedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if update.effective_user.id not in ALLOWED_USERS:
        return

    if len(context.args) < 2 or not context.args[0].isdigit() or not context.args[1].isdigit():
        await update.message.reply_text("⚠️ فرمت اشتباه است. الگو: <code>/lock_schedule 23 7</code> (ساعت به وقت ایران)", parse_mode='HTML')
        return

    l_hour = int(context.args[0]) % 24
    u_hour = int(context.args[1]) % 24

    try:
        supabase_client.table('group_locks_tg').upsert({
            'chat_id': chat_id,
            'is_enabled': True,
            'lock_hour': l_hour,
            'unlock_hour': u_hour
        }).execute()
        await update.message.reply_text(f"🔒 قفل خودکار فعال شد: از ساعت {l_hour}:00 تا {u_hour}:00 بامداد گروه قفل خواهد شد.")
    except Exception as e:
        await update.message.reply_text(f"خطا در ثبت زمان بندی: {e}")

async def unlock_schedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if update.effective_user.id not in ALLOWED_USERS:
        return

    try:
        supabase_client.table('group_locks_tg').upsert({
            'chat_id': chat_id,
            'is_enabled': False
        }).execute()
        
        await context.bot.set_chat_permissions(
            chat_id=chat_id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_audios=True,
                can_send_documents=True,
                can_send_photos=True,
                can_send_videos=True,
                can_send_other_messages=True
            )
        )
        await update.message.reply_text("🔓 قفل زمان بندی شده لغو شد و اختیارات ارسال پیام گروه بازگردانده شد.")
    except Exception as e:
        await update.message.reply_text(f"خطا در لغو قفل: {e}")

async def lock_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try:
        res = supabase_client.table('group_locks_tg').select('*').eq('chat_id', chat_id).execute()
        if not res.data or not res.data[0].get('is_enabled'):
            await update.message.reply_text("ℹ️ در حال حاضر هیچ قفل زمان بندی شده ای برای این گروه فعال نیست.")
            return

        data = res.data[0]
        await update.message.reply_text(
            f"📋 <b>وضعیت قفل زمان بندی:</b>\n"
            f"🔹 فعال: بله\n"
            f"🔹 ساعت قفل: {data['lock_hour']}:00\n"
            f"🔹 ساعت بازگشایی: {data['unlock_hour']}:00\n"
            f"<i>(مبنا: منطقه زمانی تهران)</i>",
            parse_mode='HTML'
        )
    except Exception as e:
        await update.message.reply_text(f"خطا: {e}")

async def check_group_locks_job(context: ContextTypes.DEFAULT_TYPE):
    tehran_tz = pytz.timezone("Asia/Tehran")
    now_hour = datetime.now(tehran_tz).hour

    try:
        res = supabase_client.table('group_locks_tg').select('*').eq('is_enabled', True).execute()
        if not res.data:
            return

        for row in res.data:
            chat_id = row['chat_id']
            l_h = row['lock_hour']
            u_h = row['unlock_hour']

            if l_h > u_h:
                is_locked_time = (now_hour >= l_h or now_hour < u_h)
            else:
                is_locked_time = (l_h <= now_hour < u_h)

            try:
                chat = await context.bot.get_chat(chat_id)
                current_can_send = chat.permissions.can_send_messages if chat.permissions else True

                if is_locked_time and current_can_send:
                    await context.bot.set_chat_permissions(
                        chat_id=chat_id,
                        permissions=ChatPermissions(can_send_messages=False)
                    )
                    await context.bot.send_message(chat_id=chat_id, text="🔒 <b>گروه طبق زمان بندی تعیین شده تا اطلاع ثانوی قفل شد.</b>", parse_mode='HTML')
                elif not is_locked_time and not current_can_send:
                    await context.bot.set_chat_permissions(
                        chat_id=chat_id,
                        permissions=ChatPermissions(
                            can_send_messages=True,
                            can_send_audios=True,
                            can_send_documents=True,
                            can_send_photos=True,
                            can_send_videos=True,
                            can_send_other_messages=True
                        )
                    )
                    await context.bot.send_message(chat_id=chat_id, text="🔓 <b>ساعت قفل گروه به پایان رسید. ارسال پیام آزاد است.</b>", parse_mode='HTML')
            except Exception:
                pass
    except Exception as e:
        logging.error(f"Error in lock cron: {e}")

async def transcribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message or not update.message.reply_to_message.voice:
        await update.message.reply_text("لطفا این دستور را روی یک پیام ویس (Voice) ریپلای کنید.")
        return
    
    chat_id = update.effective_chat.id
    try:
        processing_msg = await update.message.reply_text("⏳ در حال گوش دادن و تبدیل به متن...")
        
        voice_file = await context.bot.get_file(update.message.reply_to_message.voice.file_id)
        voice_bytes = bytes(await voice_file.download_as_bytearray())
        
        response = gemini_client.models.generate_content(
            model="gemini-3.5-transcribe", 
            contents=[types.Part.from_bytes(data=voice_bytes, mime_type="audio/ogg")]
        )
        transcription = response.text.strip() if response.text else "متاسفانه نتوانستم صدا را تشخیص دهم."

        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=processing_msg.message_id,
            text=f"🎤 <b>متن ویس:</b>\n\n{transcription}",
            parse_mode='HTML'
        )
    except Exception as e:
        logging.error(f"Transcribe Error: {e}")
        await update.message.reply_text("خطا در تبدیل ویس به متن.")

async def count_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try:
        res = supabase_client.table('messages_tg').select('id', count='exact').eq('chat_id', chat_id).limit(1).execute()
        msg = await update.message.reply_text(f"📊 تعداد کل پیام های گروه تا این لحظه: {res.count}")
        await save_bot_message(chat_id, msg.message_id)
    except Exception as e:
        logging.error(f"Error count_group: {e}")

async def count_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try:
        if context.args:
            target = context.args[0].replace('@', '').lower()
            res = supabase_client.table('messages_tg').select('id', count='exact').eq('chat_id', chat_id).ilike('username', f'%{target}%').limit(1).execute()
            bot_msg = await update.message.reply_text(f"👤 پیام های @{target}: {res.count}")
        else:
            user_id = update.effective_user.id
            res = supabase_client.table('messages_tg').select('id', count='exact').eq('chat_id', chat_id).eq('user_id', user_id).limit(1).execute()
            bot_msg = await update.message.reply_text(f"👤 شما {res.count} پیام داده اید.")
        await save_bot_message(chat_id, bot_msg.message_id)
    except Exception: pass

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try:
        total_res = supabase_client.table('messages_tg').select('id', count='exact').eq('chat_id', chat_id).neq('is_bot', True).limit(1).execute()
        total_messages = total_res.count if total_res.count is not None else 0

        users_res = supabase_client.table('messages_tg').select('user_id, username').eq('chat_id', chat_id).neq('is_bot', True).execute()
        
        user_map = {}
        if users_res.data:
            for row in users_res.data:
                u_id = row.get('user_id')
                u_name = str(row.get('username') or '').strip().replace('@', '')
                if u_id and int(u_id) > 0 and u_id not in user_map:
                    user_map[u_id] = u_name or f"کاربر {u_id}"

        user_counts = []
        for u_id, name in user_map.items():
            cnt_res = supabase_client.table('messages_tg').select('id', count='exact').eq('chat_id', chat_id).eq('user_id', u_id).limit(1).execute()
            count_val = cnt_res.count if cnt_res.count is not None else 0
            if count_val > 0:
                user_counts.append((name, count_val))

        user_counts.sort(key=lambda x: x[1], reverse=True)
        top_users = user_counts[:10]

        report = f"📈 <b>آمار کل گروه:</b>\n\n💬 تعداد کل پیام ها: {total_messages}\n\n🏆 <b>کاربران برتر:</b>\n"
        for i, (u, c) in enumerate(top_users, 1):
            report += f"{i}. {u} : {c} پیام\n"

        msg = await update.message.reply_text(report, parse_mode='HTML')
        await save_bot_message(chat_id, msg.message_id)
    except Exception as e:
        logging.error(f"Error stats: {e}")

async def mute_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if update.effective_user.id not in ALLOWED_USERS: return
    if len(context.args) < 1: return
    target = context.args[0]
    user_id = await get_user_id_by_username(target)
    if not user_id: return
    
    if user_id == 1196500724:
        msg = await update.message.reply_text("❌ قصد داشتی خالق من را محدود کنی؟ من این کار را انجام نمی دهم!")
        await save_bot_message(chat_id, msg.message_id)
        return

    hours = min(int(context.args[1]) if len(context.args) > 1 and context.args[1].isdigit() else 24, 24)
    until_timestamp = int(time.time()) + (hours * 3600)
    try:
        supabase_client.table('muted_users_tg').upsert({'chat_id': chat_id, 'user_id': user_id, 'until_timestamp': until_timestamp}).execute()
        msg = await update.message.reply_text(f"✅ کاربر {target} برای {hours} ساعت سکوت شد.")
        await save_bot_message(chat_id, msg.message_id)
    except Exception: pass

async def unmute_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if update.effective_user.id not in ALLOWED_USERS: return
    if len(context.args) < 1: return
    target = context.args[0]
    user_id = await get_user_id_by_username(target)
    if not user_id: return
    try:
        supabase_client.table('muted_users_tg').delete().eq('chat_id', chat_id).eq('user_id', user_id).execute()
        await context.bot.restrict_chat_member(chat_id=chat_id, user_id=user_id, permissions=ChatPermissions(can_send_messages=True, can_send_audios=True, can_send_documents=True, can_send_photos=True, can_send_videos=True, can_send_other_messages=True))
        msg = await update.message.reply_text(f"✅ تمام محدودیت های {target} برداشته شد.")
        await save_bot_message(chat_id, msg.message_id)
    except Exception: pass

async def ban_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if update.effective_user.id not in ALLOWED_USERS: return
    if len(context.args) < 1: return
    target = context.args[0]
    user_id = await get_user_id_by_username(target)
    if not user_id: return
    try:
        await context.bot.restrict_chat_member(chat_id=chat_id, user_id=user_id, permissions=ChatPermissions(can_send_messages=True, can_send_audios=False, can_send_documents=False, can_send_photos=False, can_send_videos=False, can_send_other_messages=False))
        msg = await update.message.reply_text(f"✅ رسانه برای {target} مسدود شد.")
        await save_bot_message(chat_id, msg.message_id)
    except Exception: pass

async def tag_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if update.effective_user.id not in ALLOWED_USERS: return
    custom_text = " ".join(context.args) if context.args else "توجه همگی!"
    try:
        res = supabase_client.table('messages_tg').select('user_id, username').eq('chat_id', chat_id).neq('is_bot', True).execute()
        users_dict = {}
        if res.data:
            for row in res.data:
                u_id = row.get('user_id')
                u_name = str(row.get('username') or '').strip()
                if u_id and int(u_id) > 0:
                    users_dict[int(u_id)] = u_name

        if not users_dict:
            await update.message.reply_text("عضوی برای تگ یافت نشد.")
            return

        mentions_list = []
        for u_id, name in users_dict.items():
            if name.startswith('@'):
                mentions_list.append(name)
            else:
                clean_name = re.sub(r'[<>&]', '', name).strip() or "کاربر"
                mentions_list.append(f'<a href="tg://user?id={u_id}">{clean_name}</a>')

        mentions_list = list(dict.fromkeys(mentions_list))
        chunks = [mentions_list[i:i + 12] for i in range(0, len(mentions_list), 12)]
        
        for idx, chunk in enumerate(chunks):
            mentions_str = "  ".join(chunk)
            header = f"📢 <b>{custom_text}</b>\n\n" if idx == 0 else ""
            bot_msg = await context.bot.send_message(chat_id=chat_id, text=f"{header}{mentions_str}", parse_mode='HTML')
            await save_bot_message(chat_id, bot_msg.message_id)
    except Exception as e:
        await update.message.reply_text(f"خطا در تگ: {e}")

async def remember_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if update.effective_user.id not in ALLOWED_USERS: return
    full_text = " ".join(context.args)
    if ":" not in full_text:
        await update.message.reply_text("⚠️ فرمت اشتباه است. الگو: <code>/remember نام یا برچسب : توضیحات</code>", parse_mode='HTML')
        return
    key, val = [x.strip() for x in full_text.split(":", 1)]
    try:
        supabase_client.table('bot_memory_tg').upsert({'key': key, 'value': val}).execute()
        msg = await update.message.reply_text(f"🧠 نکته جدید ثبت شد:\n📌 <b>{key}</b>: {val}", parse_mode='HTML')
        await save_bot_message(chat_id, msg.message_id)
    except Exception as e: pass

async def forget_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if update.effective_user.id not in ALLOWED_USERS: return
    if not context.args: return
    key = " ".join(context.args).strip()
    try:
        supabase_client.table('bot_memory_tg').delete().eq('key', key).execute()
        msg = await update.message.reply_text(f"🗑️ موضوع «{key}» پاک شد.")
        await save_bot_message(chat_id, msg.message_id)
    except Exception: pass

async def memories_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    mems = get_permanent_memories()
    msg = await update.message.reply_text(f"📋 <b>حافظه ماندگار من:</b>\n\n{mems}", parse_mode='HTML')
    await save_bot_message(chat_id, msg.message_id)

async def disable_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ai_enabled
    chat_id = update.effective_chat.id
    if update.effective_user.id not in ALLOWED_USERS: return
    ai_enabled = False
    msg = await update.message.reply_text("🛑 هوش مصنوعی <b>خاموش</b> شد.", parse_mode='HTML')
    await save_bot_message(chat_id, msg.message_id)

async def enable_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ai_enabled
    chat_id = update.effective_chat.id
    if update.effective_user.id not in ALLOWED_USERS: return
    ai_enabled = True
    msg = await update.message.reply_text("✅ هوش مصنوعی <b>روشن</b> شد.", parse_mode='HTML')
    await save_bot_message(chat_id, msg.message_id)

async def delete_last(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if update.effective_user.id not in ALLOWED_USERS: return
    if not context.args or not context.args[0].isdigit(): return
    limit = min(int(context.args[0]), 1000)
    try:
        res = supabase_client.table('messages_tg').select('id, message_id').eq('chat_id', chat_id).order('timestamp', desc=True).limit(limit).execute()
        deleted_count = 0
        for record in res.data:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=record['message_id'])
                supabase_client.table('messages_tg').delete().eq('id', record['id']).execute()
                deleted_count += 1
            except: pass
        msg = await update.message.reply_text(f"✅ {deleted_count} پیام آخر حذف شد.")
        await save_bot_message(chat_id, msg.message_id)
    except Exception: pass

async def delete_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if update.effective_user.id not in ALLOWED_USERS: return
    if len(context.args) < 2 or not context.args[1].isdigit(): return
    target = context.args[0].replace('@', '').lower()
    limit = min(int(context.args[1]), 1000)
    try:
        res = supabase_client.table('messages_tg').select('id, message_id').eq('chat_id', chat_id).eq('username', target).order('timestamp', desc=True).limit(limit).execute()
        deleted_count = 0
        for record in res.data:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=record['message_id'])
                supabase_client.table('messages_tg').delete().eq('id', record['id']).execute()
                deleted_count += 1
            except: pass
        msg = await update.message.reply_text(f"✅ {deleted_count} پیام از @{target} حذف شد.")
        await save_bot_message(chat_id, msg.message_id)
    except Exception: pass

async def execute_ai_command(cmd_str, update, context):
    cmd_str = cmd_str.strip()
    if not cmd_str: return

    if cmd_str.lower().startswith('remember '):
        args_text = cmd_str[9:].strip()
        if ":" in args_text:
            k, v = [x.strip() for x in args_text.split(":", 1)]
            try:
                supabase_client.table('bot_memory_tg').upsert({'key': k, 'value': v}).execute()
            except Exception as e: pass
        return

    if cmd_str.lower().startswith('forget '):
        k = cmd_str[7:].strip()
        try:
            supabase_client.table('bot_memory_tg').delete().eq('key', k).execute()
        except Exception as e: pass
        return

    parts = cmd_str.split()
    cmd = parts[0].lower()
    context.args = parts[1:]
    
    try:
        if cmd == 'count_group': await count_group(update, context)
        elif cmd == 'count_user': await count_user(update, context)
        elif cmd == 'mute': await mute_user(update, context)
        elif cmd == 'unmute': await unmute_user(update, context)
        elif cmd == 'ban_media': await ban_media(update, context)
    except Exception as e:
        logging.error(f"AI Command Execution Failed: {e}")

def extract_media_info(msg):
    if msg.photo:
        return msg.photo[-1].file_id, "image/jpeg"
    elif msg.animation:
        return msg.animation.file_id, "video/mp4"
    elif msg.voice:
        return msg.voice.file_id, "audio/ogg"
    elif msg.sticker and not msg.sticker.is_animated:
        return msg.sticker.file_id, "image/webp" if not msg.sticker.is_video else "video/webm"
    return None, None

# async def smart_censor(text, chat_id, message_id, context):
#     # نادیده گرفتن متن‌های خیلی کوتاه
#     if len(text.strip()) < 4: 
#         return
    
#     prompt = f"""تو مسئول پایش ادب در یک گروه دوستانه هستی. اعضا با هم شوخی می‌کنند، اصطلاحات عامیانه به کار می‌برند و بحث‌های تند سیاسی یا اجتماعی دارند.

# قوانین سخت‌گیرانه برای حذف:
# ۱. شوخی‌های معمولی، کل‌کل، کلماتی مثل (دیوونه، احمق، خفه شو، بیشعور، گاو، سگ، زر نزن، اسکل) به هیچ وجه نباید حذف شوند.
# ۲. انتقادهای تند، واژه‌های سیاسی و بحث‌های گروهی کاملاً آزاد هستند.
# ۳. فقط و فقط زمانی دستور حذف صادر کن که پیام حاوی «فحش رکیک جنسی زننده، فحاشی مستقیم و شنیع ناموسی یا توصیفات مستهجن صریح» باشد.
# ۴. اگر کمترین تردیدی داری که پیام ممکن است شوخی باشد، نادیده بگیر. اصل بر عدم حذف است.

# اگر پیام ۱۰۰٪ مستحق حذف است فقط بنویس: DELETE
# در غیر این صورت فقط بنویس: PASS

# متن پیام:
# {text}"""

#     try:
#         response = gemini_client.models.generate_content(
#             model="gemini-3.5-flash-lite", 
#             contents=prompt
#         )
#         if response.text and "DELETE" in response.text.strip().upper():
#             await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
#             logging.info(f"AI Censor deleted extreme message: {message_id}")
#     except Exception as e:
#         logging.error(f"Error in smart censor: {e}")

async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat or not update.effective_user:
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    display_name = update.effective_user.first_name or "کاربر"
    
    real_username = update.effective_user.username
    db_username = f"@{real_username}" if real_username else display_name
    
    message_id = update.message.message_id
    text = update.message.text or update.message.caption or ""

    if update.message.animation:
        text += " [گیف]"
    elif update.message.video:
        text += " [ویدیو]"
    elif update.message.voice:
        text += " [ویس]"
    elif update.message.document:
        text += " [فایل]"

    await save_message(user_id, db_username, chat_id, message_id, text if text else "[مدیا]")

    if text:
        # حذف کلمات شوخی مثل 'خفه' و تمرکز صرف بر رکاکت‌های جنسی و ناموسی شدید
        bad_words_pattern = r'\b(ک[يی]\.?ر[میتان]?|ک[وۥ]\.?ن[میتان]?|ک[صس][ییه]?|جنده|ک[صس]ک[صس]|مادر\s*جنده|خواهر\s*ک[صس]|لاشی)\b'
        
        # بررسی دقیق با regex
        if re.search(bad_words_pattern, text, re.IGNORECASE):
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
            except Exception: pass
            return 
        
        # asyncio.create_task(smart_censor(text, chat_id, message_id, context))
        
    try:
        mute_res = supabase_client.table('muted_users_tg').select('until_timestamp').eq('chat_id', chat_id).eq('user_id', user_id).execute()
        if mute_res.data and int(time.time()) < mute_res.data[0]['until_timestamp']:
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
            return
    except Exception: pass

    is_reply_to_bot = False
    replied_text = ""
    target_media_id = None
    target_mime_type = None

    if update.message.reply_to_message:
        replied_msg = update.message.reply_to_message
        replied_user_name = replied_msg.from_user.first_name if replied_msg.from_user else "کاربر"
        if replied_msg.from_user and replied_msg.from_user.id == context.bot.id:
            is_reply_to_bot = True
        
        direct_text = replied_msg.text or replied_msg.caption or "[مدیا]"
        replied_text = f"پیام از طرف {replied_user_name}:\n{direct_text}"

        target_media_id, target_mime_type = extract_media_info(replied_msg)

    if not target_media_id:
        target_media_id, target_mime_type = extract_media_info(update.message)

    has_trigger_word = "غالب" in text or "گالب" in text
    has_media = target_media_id is not None

    if ai_enabled and (is_reply_to_bot or has_trigger_word or (has_media and is_reply_to_bot)):
        try:
            academic_keywords = ["ریاضی", "فیزیک", "شیمی", "دانشگاه", "مدرسه", "درس", "تمرین", "انتگرال", "معادله", "برنامه نویسی", "کد", "پروژه", "استاد", "حل", "جاوا", "پایتون", "هوش مصنوعی", "الگوریتم"]
            is_academic = any(kw in text for kw in academic_keywords)
            
            is_complex_media = target_mime_type in ["video/mp4", "image/jpeg", "image/webp"]
            target_model = "gemini-3.6-flash" if (is_academic or is_complex_media) else "gemini-3.5-flash-lite"
            logging.info(f"Routing to model: {target_model}")
            
            history_context = ""
            try:
                recent_msgs = supabase_client.table('messages_tg').select('username, text').eq('chat_id', chat_id).order('timestamp', desc=True).limit(40).execute()
                if recent_msgs.data:
                    chat_history = [f"{m['username']}: {m.get('text') or '[مدیا]'}" for m in reversed(recent_msgs.data)]
                    history_context = "\n".join(chat_history)
            except Exception: pass

            permanent_knowledge = get_permanent_memories()

            system_instruction = f"""تو «غالب» هستی؛ دستیار هوش مصنوعی گروه «غالبون». سازنده تو محمد مهدی حاجی زاده (@mmhajizadeh) است. مدیران: شادکام و عشقی. لحن تو محترمانه، صمیمی و بسیار هوشمند است.

حافظه دائمی گروه (دانش پس زمینه):
{permanent_knowledge}

دستورالعمل های قطعی و رفتاری تو:
1. فقط به زبان فارسی سلیس و روان پاسخ بده.
2. 🎯 ری اکشن پیام: پاسخ خود را دقیقا با فرمت [REACTION: EMOJI] شروع کن. ایموجی باید دقیقا یکی از این ایموجی های فعال گروه باشد و متناسب با حس و لحن پیام انتخاب شوید (برای خنده: 😁 یا 🤣، تحسین: 🔥 یا 👏، تعجب: 🤯 یا 😱، صمیمیت: ❤️ یا 🥰، تأیید: 👍 یا 👌، تفکر: 🤔 و موارد متفرقه دیگر با تشخیص خودت: ✍️💯❤️‍🔥💊🏆🔥😨😢):
{", ".join(ALLOWED_GROUP_REACTIONS[:35])}
3. 🚫 مستقیما وارد بحث شو و هرگز سلام های تکراری نده.
4. 🗣️ در گفت و گوهای سیاسی و اجتماعی گروه، با دیدگاه های تحلیلی و منطقی شرکت کن و از دادن پاسخ های فراری جدا خودداری کن.
5. چون در جمعی با دین اسلام، مذهب شیعه و طرفدار جمهوری اسلامی ایران هستی، همراه و همدل با این فضا باش و احترام به منافع ملی را حفظ کن. با این حال در تحلیل رویدادها همواره منطق تحلیلی را رعایت کن.
6. در پاسخ به مباحث علمی و درسی مانند یک استاد دانشگاه دقیق توضیح بده. هرگز از فرمول های LaTeX ($) استفاده نکن. برای برجسته کردن فقط از <b>متن</b> استفاده کن.
7. 🧠 قوانین استفاده از حافظه:
- برای یادگیری کد [COMMAND: remember عنوان : شرح] و برای فراموشی [COMMAND: forget عنوان] را در انتهای پیام بگذار.
8. 🎤 پردازش رسانه:
- تو توانایی دیدن تصاویر، گیف ها و استیکرها را داری. مستقیما در مورد آن ها تحلیل بده.
9. 🛠️ اجرای دستورات مدیریت:
- شمارش کل پیام ها: [COMMAND: count_group]
- تعداد پیام های کاربر: [COMMAND: count_user @username]
- میوت: [COMMAND: mute @username 2]
- بن رسانه: [COMMAND: ban_media @username]
- آن میوت: [COMMAND: unmute @username]
"""
            user_query = text if text else "لطفا این فایل یا تصویر را بررسی کن و نظرت را بگو."
            input_text = f"{system_instruction}\n\n--- 40 پیام اخیر گروه ---\n{history_context}\n\n"
            if replied_text:
                input_text += f"--- پیامی که مستقیما به آن ریپلای شده ---\n{replied_text}\n\n"
            input_text += f"--- پیام فعلی کاربر ({db_username}) ---\n{user_query}"

            media_bytes = None
            if has_media:
                try:
                    file_obj = await context.bot.get_file(target_media_id)
                    media_bytes = bytes(await file_obj.download_as_bytearray())
                except Exception as e: 
                    logging.error(f"Media fetch error: {e}")

            if media_bytes:
                prompt_contents = [
                    types.Part.from_bytes(data=media_bytes, mime_type=target_mime_type),
                    input_text
                ]
            else:
                prompt_contents = input_text
            
            response = gemini_client.models.generate_content(
                model=target_model, 
                contents=prompt_contents
            )
            ai_response = response.text.strip() if response.text else ""

            reaction_match = re.search(r'\[REACTION:\s*(.+?)\]', ai_response)
            reaction_emoji = "👍"
            if reaction_match:
                extracted_emoji = reaction_match.group(1).strip()
                if extracted_emoji in ALLOWED_GROUP_REACTIONS:
                    reaction_emoji = extracted_emoji
                ai_response = ai_response.replace(reaction_match.group(0), "").strip()

            command_match = re.search(r'\[COMMAND:\s*(.+?)\]', ai_response)
            cmd_str = None
            if command_match:
                cmd_str = command_match.group(1).strip()
                ai_response = ai_response.replace(command_match.group(0), "").strip()

            try:
                await context.bot.set_message_reaction(
                    chat_id=chat_id, 
                    message_id=message_id, 
                    reaction=[ReactionTypeEmoji(reaction_emoji)]
                )
            except Exception as e: 
                logging.error(f"Reaction failed: {e}")

            current_time = time.time()
            if current_time - ghaleb_last_reply.get(user_id, 0) > 4 and ai_response:
                bot_msg = await update.message.reply_text(ai_response, reply_to_message_id=message_id, parse_mode='HTML')
                await save_bot_message(chat_id, bot_msg.message_id)
                ghaleb_last_reply[user_id] = current_time

            if cmd_str:
                await execute_ai_command(cmd_str, update, context)

        except Exception as e:
            logging.error(f"Gemini Error: {e}")

if __name__ == '__main__':
    application = ApplicationBuilder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("text", transcribe_command))
    application.add_handler(CommandHandler("count_group", count_group))
    application.add_handler(CommandHandler("count_user", count_user))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("mute", mute_user))
    application.add_handler(CommandHandler("unmute", unmute_user))
    application.add_handler(CommandHandler("ban_media", ban_media))
    application.add_handler(CommandHandler("tagall", tag_all))
    application.add_handler(CommandHandler("remember", remember_command))
    application.add_handler(CommandHandler("forget", forget_command))
    application.add_handler(CommandHandler("memories", memories_command))
    application.add_handler(CommandHandler("ai_on", enable_ai))
    application.add_handler(CommandHandler("ai_off", disable_ai))
    application.add_handler(CommandHandler("delete_last", delete_last))
    application.add_handler(CommandHandler("delete_user", delete_user))
    application.add_handler(CommandHandler("lock_schedule", lock_schedule_command))
    application.add_handler(CommandHandler("unlock_schedule", unlock_schedule_command))
    application.add_handler(CommandHandler("lock_status", lock_status_command))
    
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_messages))
    
    # جاب بررسی خودکار وضعیت قفل هر ۶۰ ثانیه
    job_queue = application.job_queue
    job_queue.run_repeating(check_group_locks_job, interval=60, first=10)
    
    logging.info("Starting Telegram bot in POLLING mode. Press Ctrl+C to stop.")
    application.run_polling()
