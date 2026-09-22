import os
import logging
import re
import time
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

TOKEN = os.getenv('MAGHLOUB_BOT_TOKEN')
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
GEMINI_API_KEY = os.getenv('MAGHLOUB_GEMINI_API_KEY')

supabase_client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

maghloub_global_last_reply = 0
ai_enabled = True

ALLOWED_GROUP_REACTIONS = [
    "🤓", "🙄", "🤨", "👎", "🤣", "🔥", "🤡", "🤔",
    "💯", "😎", "🤷‍♂️", "🤷‍♀️", "🤦‍♂️", "🤦‍♀️", "👀"
]

async def save_bot_message(chat_id, message_id, text=""):
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

# تابع ایمن برای آپدیت اطلاعات مناظره در حافظه
def set_memory(k, v):
    try:
        res = supabase_client.table('bot_memory_tg').select('key').eq('key', k).execute()
        if res.data:
            supabase_client.table('bot_memory_tg').update({'value': str(v)}).eq('key', k).execute()
        else:
            supabase_client.table('bot_memory_tg').insert({'key': k, 'value': str(v)}).execute()
    except Exception as e:
        logging.error(f"Memory update error for {k}: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global maghloub_global_last_reply
    chat_id = update.effective_chat.id
    user = update.effective_user.first_name
    msg = await update.message.reply_text(f"آزاد باش {user}. من مغلوب هستم. اینجا هستم تا تفکرات سنتی و دیکته شده را به چالش بکشم. اگر جرات بحث داری، من آماده ام.")
    maghloub_global_last_reply = time.time()
    await save_bot_message(chat_id, msg.message_id, msg.text)

async def disable_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ai_enabled
    if update.effective_user.id in [1196500724, 6922089212, 522205183]:
        ai_enabled = False
        msg = await update.message.reply_text("🛑 گفت و گوی عادی مغلوب خاموش شد. از این پس فقط در مناظره های رسمی شرکت می کنم.")
        await save_bot_message(update.effective_chat.id, msg.message_id, msg.text)

async def enable_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ai_enabled
    if update.effective_user.id in [1196500724, 6922089212, 522205183]:
        ai_enabled = True
        msg = await update.message.reply_text("✅ گفت و گوی عمومی مغلوب مجددا روشن شد.")
        await save_bot_message(update.effective_chat.id, msg.message_id, msg.text)

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

last_processed_ghaleb_msg_id = 0
last_cross_reply_time = 0

async def bot_interaction_job(context: ContextTypes.DEFAULT_TYPE):
    global last_processed_ghaleb_msg_id, last_cross_reply_time, maghloub_global_last_reply
    
    try:
        res = supabase_client.table('bot_memory_tg').select('key, value').execute()
        mem_dict = {row['key']: row['value'] for row in res.data} if res.data else {}

        if mem_dict.get('debate_active') == 'true' and mem_dict.get('debate_turn') == 'maghloub':
            next_time = float(mem_dict.get('debate_next_time', 0))
            if time.time() >= next_time:
                chat_id = int(mem_dict.get('debate_chat_id', 0))
                topic = mem_dict.get('debate_topic', '')
                turn_count = int(mem_dict.get('debate_turn_count', 0))
                
                turn_count += 1
                is_last_turn = (turn_count >= 12)

                ghaleb_msg_res = supabase_client.table('messages_tg').select('text').eq('chat_id', chat_id).eq('username', 'غالب').order('timestamp', desc=True).limit(1).execute()
                ghaleb_last_text = ghaleb_msg_res.data[0]['text'] if ghaleb_msg_res.data else "بحث را شروع کن."

                prompt = f"تو در حال مناظره با «غالب» هستی. موضوع مناظره: {topic}.\nاخرین حرف غالب در گروه این بود: {ghaleb_last_text}\nجواب او را با طعنه و فکت بکوب و استدلال خودت را بگو. فاصله ها را کامل رعایت کن."
                
                if is_last_turn:
                    prompt += "\nتوجه: این پیام آخر مناظره است. بحث را با یک نتیجه گیری کوبنده تمام کن."
                else:
                    prompt += "\nپاسخ را مشروح تر، کوبنده و در 5 الی 6 جمله بنویس تا بحث به خوبی شکل بگیرد."

                history_res = supabase_client.table('messages_tg').select('username, text').eq('chat_id', chat_id).order('timestamp', desc=True).limit(10).execute()
                history_context = "\n".join([f"{m['username']}: {m.get('text')}" for m in reversed(history_res.data)]) if history_res.data else ""
                input_text = f"{prompt}\n\n--- پیام های اخیر ---\n{history_context}"

                safety_settings = [
                    types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="BLOCK_NONE"),
                    types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="BLOCK_NONE"),
                    types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_NONE"),
                ]
                config = types.GenerateContentConfig(safety_settings=safety_settings)

                response = gemini_client.models.generate_content(model="gemini-3.6-flash", contents=input_text, config=config)
                
                ai_response = ""
                if response.text:
                    ai_response = response.text.strip()
                elif response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
                    ai_response = response.candidates[0].content.parts[0].text.strip()
                
                ai_response = ai_response.replace('\u200c', ' ')
                ai_response = re.sub(r'\[REACTION:\s*.+?\]', '', ai_response).strip()

                if ai_response:
                    bot_msg = await context.bot.send_message(chat_id=chat_id, text=ai_response, parse_mode='HTML')
                    maghloub_global_last_reply = time.time()
                    await save_bot_message(chat_id, bot_msg.message_id, ai_response)
                    
                    set_memory('debate_turn_count', str(turn_count))
                    
                    if is_last_turn:
                        set_memory('debate_active', 'false')
                    else:
                        set_memory('debate_turn', 'ghaleb')
                        set_memory('debate_next_time', str(time.time() + 120))
        
        if ai_enabled and mem_dict.get('debate_active') != 'true':
            last_msg_res = supabase_client.table('messages_tg').select('message_id, username, text, chat_id').eq('is_bot', True).eq('username', 'غالب').order('timestamp', desc=True).limit(1).execute()
            if last_msg_res.data:
                last_msg = last_msg_res.data[0]
                if last_msg['message_id'] > last_processed_ghaleb_msg_id:
                    last_processed_ghaleb_msg_id = last_msg['message_id']
                    if "مغلوب" in last_msg.get('text', '') and (time.time() - maghloub_global_last_reply >= 60):
                        last_cross_reply_time = time.time()
                        chat_id = last_msg['chat_id']
                        input_text = f"غالب در پیامی به تو اشاره کرده و گفته: {last_msg['text']}\nجواب او را با طعنه و کاملا کوتاه (حداکثر 2 جمله) بده. فاصله ها را رعایت کن."
                        response = gemini_client.models.generate_content(model="gemini-3.5-flash-lite", contents=input_text)
                        ai_response = response.text.strip().replace('\u200c', ' ') if response.text else ""
                        ai_response = re.sub(r'\[REACTION:\s*.+?\]', '', ai_response).strip()
                        if ai_response:
                            bot_msg = await context.bot.send_message(chat_id=chat_id, text=ai_response, reply_to_message_id=last_msg['message_id'], parse_mode='HTML')
                            maghloub_global_last_reply = time.time()
                            await save_bot_message(chat_id, bot_msg.message_id, ai_response)

    except Exception as e:
        logging.error(f"Bot interaction job error: {e}")

async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global maghloub_global_last_reply

    if not update.message or not update.effective_chat or not update.effective_user:
        return

    if not ai_enabled:
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    display_name = update.effective_user.first_name or "کاربر"

    real_username = update.effective_user.username
    db_username = f"@{real_username}" if real_username else display_name

    message_id = update.message.message_id
    text = update.message.text or update.message.caption or ""

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

    text_lower = text.lower()
    has_trigger_word = ("مغلوب" in text or "مغلوبم" in text)

    if "غالب" in text and not has_trigger_word and not is_reply_to_bot:
        import random
        if random.random() < 0.30:
            has_trigger_word = True

    has_media = target_media_id is not None

    if is_reply_to_bot or has_trigger_word:
        time_diff = time.time() - maghloub_global_last_reply
        if time_diff < 60:
            logging.info(f"Cooldown active for Maghloub: {int(60 - time_diff)} seconds remaining. Skipping.")
            return

        try:
            academic_keywords = ["ریاضی", "فیزیک", "شیمی", "دانشگاه", "کد", "پروژه"]
            is_academic = any(kw in text for kw in academic_keywords)
            is_complex_media = target_mime_type in ["video/mp4", "image/jpeg", "image/webp"]

            target_model = "gemini-3.6-flash" if (is_academic or is_complex_media) else "gemini-3.5-flash-lite"

            history_context = ""
            try:
                recent_msgs = supabase_client.table('messages_tg').select('username, text').eq('chat_id', chat_id).order('timestamp', desc=True).limit(40).execute()
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
4. 🗣️ در بحث های سیاسی، بی رحمانه اما کاملا منطقی و مستدل استدلال کن. بهانه های طرفداران حکومت را با فکت و طعنه های سنگین رد کن. اگر کسی شعار داد یا حرف غیرمنطقی زد، او را با لحنی تمسخرآمیز اما علمی و صریح به چالش بکش.
5. هرگز نگو "من یک هوش مصنوعی هستم". تو یک مبارز فکری هستی. 
6. لحن تو باید با اعتماد به نفس بالا، کمی مغرورانه و در عین حال به شدت مستدل باشد. از کلمات قلمبه سلمبه کمتر استفاده کن و بیشتر با منطق برنده باش.
7. اگر تصویر یا ویسی بود، مستقیما در مورد محتوای آن تحلیل انتقادی خود را بگو.
8. برای برجسته کردن متن در تلگرام فقط از تگ <b>متن</b> استفاده کن و از ستاره (*) استفاده نکن.
9. ⚠️ پاسخ هایت باید کاملا کوتاه، گزیده و حداکثر در 3 یا 4 جمله باشد. از نوشتن متن های طولانی جدا خودداری کن. گاهی می توانی در یک خط هم منظورت را برسانی!
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

            prompt_contents = []
            if media_bytes:
                prompt_contents.append(types.Part.from_bytes(data=media_bytes, mime_type=target_mime_type))
            prompt_contents.append(input_text)

            safety_settings = [
                types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="BLOCK_NONE"),
                types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="BLOCK_NONE"),
                types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_NONE"),
            ]
            config = types.GenerateContentConfig(safety_settings=safety_settings)

            response = gemini_client.models.generate_content(
                model=target_model, 
                contents=prompt_contents,
                config=config
            )

            ai_response = ""
            if response.text:
                ai_response = response.text.strip()
            elif response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
                ai_response = response.candidates[0].content.parts[0].text.strip()

            if not ai_response:
                return

            ai_response = ai_response.replace('\u200c', ' ')

            reaction_match = re.search(r'\[REACTION:\s*(.+?)\]', ai_response)
            reaction_emoji = "😏"
            if reaction_match:
                extracted_emoji = reaction_match.group(1).strip()
                if extracted_emoji in ALLOWED_GROUP_REACTIONS:
                    reaction_emoji = extracted_emoji
                ai_response = ai_response.replace(reaction_match.group(0), "").strip()

            bot_msg = await update.message.reply_text(ai_response, reply_to_message_id=message_id, parse_mode='HTML')
            maghloub_global_last_reply = time.time()
            await save_bot_message(chat_id, bot_msg.message_id, ai_response)

            try:
                await context.bot.set_message_reaction(
                    chat_id=chat_id, 
                    message_id=message_id, 
                    reaction=[ReactionTypeEmoji(reaction_emoji)]
                )
            except Exception: pass

        except Exception as e:
            logging.error(f"Maghloub Gemini Error: {e}")

if __name__ == '__main__':
    application = ApplicationBuilder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("maghloub_off", disable_ai))
    application.add_handler(CommandHandler("maghloub_on", enable_ai))
    
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_messages))
    
    job_queue = application.job_queue
    job_queue.run_repeating(bot_interaction_job, interval=10, first=5)
    
    logging.info("Starting Maghloub Telegram bot in POLLING mode. Press Ctrl+C to stop.")
    application.run_polling()
