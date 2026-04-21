import os
import asyncio
import tempfile # تم تصحيح الكلمة هنا لضمان عمل الكود
import subprocess
import shutil
from pathlib import Path

# --- مكتبات إضافية لإبقاء البوت مستيقظاً ---
from flask import Flask
import threading

from telegram import Update, Message
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ParseMode

# ─── Flask Server for Keep-Alive ──────────────────────────────────────────────
app_flask = Flask('')

@app_flask.route('/')
def home():
    return "Bot is running 24/7!"

def run_flask():
    # منفذ 7860 هو المنفذ الافتراضي الذي تراقبه Hugging Face
    app_flask.run(host='0.0.0.0', port=7860)

def keep_alive():
    t = threading.Thread(target=run_flask)
    t.daemon = True
    t.start()

# ─── Configuration ────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

SUPPORTED_AUDIO_EXTENSIONS = {
    ".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac",
    ".wma", ".opus", ".aiff", ".aif", ".mp4", ".mkv",
    ".webm", ".mov", ".avi", ".flv", ".ts", ".mts",
}

MAX_FILE_SIZE_MB = 200  

# ─── Helpers ──────────────────────────────────────────────────────────────────

def human_size(num_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num_bytes) < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} TB"


async def edit_or_reply(msg: Message, text: str, **kwargs) -> Message:
    try:
        return await msg.edit_text(text, **kwargs)
    except Exception:
        return await msg.reply_text(text, **kwargs)


def run_demucs(input_path: str, output_dir: str) -> tuple[str, str]:
    cmd = [
        "python", "-m", "demucs",
        "--two-stems", "vocals",   
        "-n", "htdemucs",          
        "--mp3",                   
        "-o", output_dir,
        input_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-2000:] or "Demucs failed with no output")

    stem_dir = Path(output_dir) / "htdemucs" / Path(input_path).stem
    vocals = stem_dir / "vocals.mp3"
    no_vocals = stem_dir / "no_vocals.mp3"

    if not vocals.exists() or not no_vocals.exists():
        raise RuntimeError(
            f"Expected output files not found in {stem_dir}.\n"
            f"Directory contents: {list(stem_dir.iterdir()) if stem_dir.exists() else 'dir missing'}"
        )
    return str(vocals), str(no_vocals)


def extract_audio_from_video(video_path: str, out_wav: str) -> None:
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vn", "-ar", "44100", "-ac", "2",
        "-c:a", "pcm_s16le", out_wav,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg audio extraction failed:\n{result.stderr[-1500:]}")


VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".flv", ".ts", ".mts"}


# ─── Command Handlers ─────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "🎵 *مرحباً بك في بوت فصل الصوت!*\n\n"
        "أرسل لي أي ملف صوتي أو فيديو وسأفصل:\n"
        "  🎤 *صوت الغناء / الكلام* → `vocals.mp3`\n"
        "  🎸 *الموسيقى / المصاحبة* → `no_vocals.mp3`\n\n"
        "📁 *الامتدادات المدعومة:*\n"
        "`mp3, wav, flac, ogg, m4a, aac, wma, opus, aiff,\n"
        "mp4, mkv, webm, mov, avi, flv, ts, mts`\n\n"
        f"⚠️ الحد الأقصى لحجم الملف: *{MAX_FILE_SIZE_MB} MB*\n\n"
        "استخدم /help لمزيد من المعلومات."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "📖 *كيف أستخدم البوت؟*\n\n"
        "1️⃣ أرسل ملفاً صوتياً أو مقطع فيديو.\n"
        "2️⃣ انتظر قليلاً (الفصل يأخذ 1–5 دقائق حسب طول المقطع).\n"
        "3️⃣ ستصلك ملفان:\n"
        "   • `vocals.mp3` — الغناء / الكلام فقط\n"
        "   • `no_vocals.mp3` — الموسيقى / المصاحبة فقط\n\n"
        "🔧 *النموذج المستخدم:* htdemucs (Meta AI)\n"
        "📌 *ملاحظة:* جودة الفصل تعتمد على وضوح الصوت الأصلي."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ─── Core Processing ──────────────────────────────────────────────────────────

async def process_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message

    file_obj = None
    original_filename = "audio"
    is_video = False

    if message.audio:
        file_obj = message.audio
        original_filename = message.audio.file_name or "audio.mp3"
    elif message.voice:
        file_obj = message.voice
        original_filename = "voice.ogg"
    elif message.video:
        file_obj = message.video
        original_filename = message.video.file_name or "video.mp4"
        is_video = True
    elif message.video_note:
        file_obj = message.video_note
        original_filename = "video_note.mp4"
        is_video = True
    elif message.document:
        file_obj = message.document
        original_filename = message.document.file_name or "file"
        ext = Path(original_filename).suffix.lower()
        if ext not in SUPPORTED_AUDIO_EXTENSIONS:
            await message.reply_text(
                f"❌ الامتداد *{ext}* غير مدعوم.\n\n"
                "الامتدادات المدعومة:\n"
                "`mp3, wav, flac, ogg, m4a, aac, wma, opus, aiff,\n"
                "mp4, mkv, webm, mov, avi, flv, ts, mts`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        if ext in VIDEO_EXTENSIONS:
            is_video = True
    else:
        await message.reply_text(
            "⚠️ أرسل ملفاً صوتياً أو فيديو من فضلك.\n"
            "استخدم /help لمعرفة الامتدادات المدعومة."
        )
        return

    if file_obj.file_size and file_obj.file_size > MAX_FILE_SIZE_MB * 1024 * 1024:
        await message.reply_text(
            f"❌ حجم الملف ({human_size(file_obj.file_size)}) يتجاوز الحد المسموح "
            f"({MAX_FILE_SIZE_MB} MB)."
        )
        return

    status_msg = await message.reply_text("⬇️ جارٍ تحميل الملف…")

    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            tg_file = await context.bot.get_file(file_obj.file_id)
            suffix = Path(original_filename).suffix or ".mp3"
            input_path = os.path.join(tmpdir, f"input{suffix}")
            await tg_file.download_to_drive(input_path)

            await edit_or_reply(
                status_msg,
                "✅ تم التحميل.\n"
                "🎬 جارٍ استخراج الصوت…" if is_video else
                "✅ تم التحميل.\n🔄 جارٍ فصل الصوت (قد يستغرق بضع دقائق)…",
            )

            if is_video:
                wav_path = os.path.join(tmpdir, "extracted.wav")
                await asyncio.get_event_loop().run_in_executor(
                    None, extract_audio_from_video, input_path, wav_path
                )
                input_path = wav_path
                await edit_or_reply(
                    status_msg,
                    "✅ تم استخراج الصوت.\n🔄 جارٍ فصل الصوت (قد يستغرق بضع دقائق)…",
                )

            output_dir = os.path.join(tmpdir, "out")
            os.makedirs(output_dir, exist_ok=True)

            vocals_path, no_vocals_path = await asyncio.get_event_loop().run_in_executor(
                None, run_demucs, input_path, output_dir
            )

            await edit_or_reply(status_msg, "✅ تم الفصل!\n📤 جارٍ إرسال الملفات…")

            base = Path(original_filename).stem

            with open(vocals_path, "rb") as f:
                await message.reply_audio(
                    audio=f,
                    filename=f"{base}_vocals.mp3",
                    title=f"{base} — صوت الغناء 🎤",
                    caption="🎤 *صوت الغناء / الكلام*",
                    parse_mode=ParseMode.MARKDOWN,
                )

            with open(no_vocals_path, "rb") as f:
                await message.reply_audio(
                    audio=f,
                    filename=f"{base}_instrumental.mp3",
                    title=f"{base} — الموسيقى 🎸",
                    caption="🎸 *الموسيقى / المصاحبة*",
                    parse_mode=ParseMode.MARKDOWN,
                )

            await edit_or_reply(status_msg, "✅ تم الإرسال بنجاح! أرسل ملفاً آخر متى شئت.")

        except RuntimeError as exc:
            await edit_or_reply(
                status_msg,
                f"❌ *حدث خطأ أثناء المعالجة:*\n`{str(exc)[:800]}`",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as exc:
            await edit_or_reply(
                status_msg,
                f"❌ *خطأ غير متوقع:*\n`{str(exc)[:800]}`",
                parse_mode=ParseMode.MARKDOWN,
            )


# ─── Entry Point ──────────────────────────────────────────────────────────────

def main() -> None:
    # بدء تشغيل خادم الويب في الخلفية قبل تشغيل البوت
    keep_alive()

    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        raise SystemExit(
            "❌ ضع توكن البوت في متغير البيئة BOT_TOKEN أو عدّل السطر الأول من الكود."
        )

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))

    media_filter = (
        filters.AUDIO
        | filters.VOICE
        | filters.VIDEO
        | filters.VIDEO_NOTE
        | filters.Document.ALL
    )
    app.add_handler(MessageHandler(media_filter, process_media))

    print("🤖 البوت يعمل الآن مع خادم Keep-alive… اضغط Ctrl+C للإيقاف.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
