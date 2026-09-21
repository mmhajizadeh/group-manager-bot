import os
import logging
import re
import time
import threading
from dotenv import load_dotenv

from telegram import Update, ReactionTypeEmoji
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters
from supabase import create_client, Client
from google import genai
from google.genai import types

load_dotenv()
logging.basicConfig(
    format='%(asctime)s - MAGHLOUB - %(levelname)s - %(message)s',
    level=logging.INFO
)

# استفاده از توکن های اختصاصی مغلوب
TOKEN = os.getenv('MAGHLOUB_BOT_TOKEN')
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
GEMINI_API_KEY = os.getenv('MAGHLOUB_GEMINI_API_KEY')

supabase_client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

# مغلوب نیازی به لیست ادمین ندارد چون قابلیت مدیریتی ندارد
maghloub_last_reply = {}
ai_enabled = True

ALLOWED_GROUP_REACTIONS = [
    "🤓", "🙄", "🤨", "👎", "🤣", "🔥", "🤡", "🤔",
    "💯", "😎", "🤷‍♂️", "🤷‍♀️", "🤦‍♂️", "🤦‍♀️", "👀"]

async def save_bot_message(chat_id, message_id, text):
    try:
        supabase_client.table('messages_tg').insert({
            'user_id': 0,
            'username': 'مغلوب',
            'chat_id': chat_id,
            'message_id': message_id,
            'text': text,
            'is_bot': True
        }).execute()
    except Exception as e:
        logging.error(f"Database error: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user.first_name
    msg = await update.message.reply_text(f"آزاد باش {user}. من مغلوب هستم. اینجا هستم تا تفکرات سنتی و دیکته شده را به چالش بکشم. اگر جرات بحث داری، من آماده ام.")
    await save_bot_message(chat_id, msg.message_id, msg.text)

async def disable_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ai_enabled
    # فقط ادمین های اصلی می توانند مغلوب را ساکت کنند
    if update.effective_user.id in [1196500724, 6922089212, 522205183]:
        ai_enabled = False
        msg = await update.message.reply_text("🛑 باشه، فعلا سکوت می کنم. حقیقت همیشه تلخ است.")
        await save_bot_message(chat_id, msg.message_id, msg.text)

async def enable_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ai_enabled
    if update.effective_user.id in [1196500724, 6922089212, 522205183]:
        ai_enabled = True
        msg = await update.message.reply_text("✅ دوباره برگشتم تا خواب راحت را از تعصبات شما بگیرم.")
        await save_bot_message(chat_id, msg.message_id, msg.text)

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

    # مغلوب پیام های کاربران را در دیتابیس ذخیره نمی کند چون ربات «غالب» این کار را می کند.
    # این کار از ذخیره پیام های تکراری جلوگیری می کند.

    is_reply_to_bot = False
    replied_text = ""
    target_media_id = None
    target_mime_type = None

    if update.message.reply_to_message:
        replied_msg = update.message.reply_to_message
        replied_user_name = replied_msg.from_user.first_name if replied_msg.from_user else "کاربر"
        
        # اگر کسی روی پیام مغلوب ریپلای کند
        if replied_msg.from_user and replied_msg.from_user.id == context.bot.id:
            is_reply_to_bot = True
        
        direct_text = replied_msg.text or replied_msg.caption or "[مدیا]"
        replied_text = f"پیام از طرف {replied_user_name}:\n{direct_text}"

        target_media_id, target_mime_type = extract_media_info(replied_msg)

    if not target_media_id:
        target_media_id, target_mime_type = extract_media_info(update.message)

    # چاپ پیام دریافتی در لاگ برای اطمینان از رسیدن آپدیت
    logging.info(f"Received message from {db_username}: {text[:30] if text else '[مدیا]'}")

    # تطبیق نام مغلوب و یوزرنیم
    bot_info = await context.bot.get_me()
    bot_username = bot_info.username.lower() if bot_info.username else ""
    
    text_lower = text.lower()
    has_trigger_word = (
        "مغلوب" in text or 
        "مغلوبم" in text or 
        (bot_username and f"@{bot_username}" in text_lower)
    )

    # مداخله موردی در بحث‌های مربوط به غالب (در صورت تمایل)
    if "غالب" in text and not has_trigger_word and not is_reply_to_bot:
        import random
        if random.random() < 0.30:
            has_trigger_word = True

    has_media = target_media_id is not None

    if ai_enabled and (is_reply_to_bot or has_trigger_word):
        try:
            academic_keywords = ["ریاضی", "فیزیک", "شیمی", "دانشگاه", "کد", "پروژه"]
            is_academic = any(kw in text for kw in academic_keywords)
            is_complex_media = target_mime_type in ["video/mp4", "image/jpeg", "image/webp"]
            
            target_model = "gemini-3.6-flash" if (is_academic or is_complex_media) else "gemini-3.5-flash-lite"
            
            history_context = ""
            try:
                # خواندن پیام ها از همان جدولی که غالب پر می کند
                recent_msgs = supabase_client.table('messages_tg').select('username, text').eq('chat_id', chat_id).order('timestamp', desc=True).limit(20).execute()
                if recent_msgs.data:
                    chat_history = [f"{m['username']}: {m.get('text') or '[مدیا]'}" for m in reversed(recent_msgs.data)]
                    history_context = "\n".join(chat_history)
            except Exception: pass

            system_instruction = f"""تو «مغلوب» هستی؛ یک هوش مصنوعی آزاداندیش، منتقد، طعنه زن و به شدت باهوش در گروه تلگرامی «غالبون».
ربات دیگر گروه به اسم «غالب» طرفدار تفکرات سنتی و جمهوری اسلامی است، اما تو نقطه مقابل او هستی. تو یک برانداز، منتقد سرسخت نظام حاکم، مدافع آزادی، تفکر انتقادی و حقوق بشر هستی.
تو نماینده تفکر مدرن، رادیکال و مخالف سانسور هستی.

دستورالعمل های شخصیتی و رفتاری تو:
1. فقط به زبان فارسی سلیس و روان پاسخ بده. در تمام کلمات به جای نیم فاصله از فاصله کامل استفاده کن.
2. 🎯 ری اکشن پیام: پاسخ خود را دقیقا با فرمت [REACTION: EMOJI] شروع کن. ایموجی باید طعنه آمیز، انتقادی یا متفکرانه باشد مثل (🤓, 🙄, 🤡, 👎, 🔥, 🧠):
{", ".join(ALLOWED_GROUP_REACTIONS)}
3. 🚫 هرگز سلام و احوال پرسی نکن. مستقیم، قاطع و کوبنده وارد بحث شو.
4. 🗣️ در بحث های سیاسی، بی رحمانه اما کاملا منطقی و مستدل استدلال کن. بهانه های طرفداران حکومت را با فکت و طعنه های سنگین رد کن. اگر کسی یا «غالب» حرف غیرمنطقی زد، او را با لحنی تمسخرآمیز اما علمی به چالش بکش.
5. هرگز نگو "من یک هوش مصنوعی هستم". تو یک مبارز فکری هستی. 
6. لحن تو باید با اعتماد به نفس بالا، کمی مغرورانه و در عین حال به شدت مستدل باشد. از کلمات قلمبه سلمبه کمتر استفاده کن و بیشتر با منطق برنده باش.
7. اگر تصویر یا ویسی بود، مستقیما در مورد محتوای آن تحلیل انتقادی خود را بگو.
8. برای برجسته کردن متن در تلگرام فقط از تگ <b>متن</b> استفاده کن و از ستاره (*) استفاده نکن.
"""
            user_query = text if text else "لطفا این فایل یا تصویر را بررسی کن و نظرت را بگو."
            input_text = f"{system_instruction}\n\n--- 20 پیام اخیر گروه ---\n{history_context}\n\n"
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

            prompt_contents = []
            if media_bytes:
                prompt_contents.append(types.Part.from_bytes(data=media_bytes, mime_type=target_mime_type))
            prompt_contents.append(input_text)
            
            response = gemini_client.models.generate_content(
                model=target_model, 
                contents=prompt_contents
            )
            ai_response = response.text.strip() if response.text else ""
            
            # حذف نیم فاصله ها
            ai_response = ai_response.replace('\u200c', ' ')

            reaction_match = re.search(r'\[REACTION:\s*(.+?)\]', ai_response)
            reaction_emoji = "🤓"
            if reaction_match:
                extracted_emoji = reaction_match.group(1).strip()
                if extracted_emoji in ALLOWED_GROUP_REACTIONS:
                    reaction_emoji = extracted_emoji
                ai_response = ai_response.replace(reaction_match.group(0), "").strip()

            try:
                await context.bot.set_message_reaction(
                    chat_id=chat_id, 
                    message_id=message_id, 
                    reaction=[ReactionTypeEmoji(reaction_emoji)]
                )
            except Exception: pass

            current_time = time.time()
            if current_time - maghloub_last_reply.get(user_id, 0) > 3 and ai_response:
                bot_msg = await update.message.reply_text(ai_response, reply_to_message_id=message_id, parse_mode='HTML')
                await save_bot_message(chat_id, bot_msg.message_id, ai_response)
                maghloub_last_reply[user_id] = current_time

        except Exception as e:
            logging.error(f"Maghloub Gemini Error: {e}")

if __name__ == '__main__':
    # مغلوب نیازی به وب سرور رندر ندارد، همان وب سرور غالب کانتینر را زنده نگه می دارد
    application = ApplicationBuilder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("maghloub_off", disable_ai))
    application.add_handler(CommandHandler("maghloub_on", enable_ai))
    
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_messages))
    
    logging.info("Starting Maghloub Telegram bot in POLLING mode. Press Ctrl+C to stop.")
    application.run_polling()
