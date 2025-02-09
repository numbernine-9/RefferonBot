from flask import Flask, request, Response
from telegram.ext import Application, CommandHandler, CallbackContext
from telegram import Update
from supabase import create_client, Client
import os
from dotenv import load_dotenv
import traceback
import asyncio
import logging
from datetime import datetime, timezone
import random
import string
import threading

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load Environment Variables
load_dotenv()

# Validate Environment Variables
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

if not all([SUPABASE_URL, SUPABASE_KEY, TELEGRAM_BOT_TOKEN]):
  raise ValueError("Missing required environment variables. Check your .env file.")

# Initialize Supabase
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Initialize Flask app
app = Flask(__name__)

# Global variable to store the application
application = None

# Function to generate a unique referral code
def generate_referral_code():
  return ''.join(random.choices(string.ascii_letters + string.digits, k=8))

# Start Command
async def start(update: Update, context: CallbackContext):
  try:
    telegram_id = update.message.chat_id
    username = update.message.chat.username or "Unknown"
    logger.info(f"Received /start command from {username}")

    # Check if user exists
    response = supabase.table("user_profiles").select("*").eq("telegram_id", telegram_id).execute()
    if response.data:
      user = response.data[0]
      referral_code = user["referral_code"]
    else:
      referral_code = generate_referral_code()
      referred_by_code = context.args[0] if context.args else None

      if referred_by_code:
        # Get referrer
        referrer_response = supabase.table("user_profiles").select("*").eq("referral_code", referred_by_code).execute()
        if not referrer_response.data:
          await update.message.reply_text("Invalid referral code.")
          return

        referrer = referrer_response.data[0]

        # Update referrer’s referral count and points
        supabase.table("user_profiles").update({
          "referrals": referrer["referrals"] + 1,
          "points": referrer["points"] + 10
        }).eq("telegram_id", referrer["telegram_id"]).execute()

      # Create new user entry
      supabase.table("user_profiles").insert({
        "telegram_id": telegram_id,
        "username": username,
        "referral_code": referral_code,
        "referred_by": referred_by_code
      }).execute()

    # Send welcome message
    ref_link = f"https://t.me/{context.bot.username}?start={referral_code}"
    await update.message.reply_text(f"Welcome {username}! 🎉\nYour referral link: {ref_link}")

  except Exception as e:
    logger.error(f"Error in start command: {str(e)}")
    logger.error(traceback.format_exc())
    await update.message.reply_text("An error occurred. Please try again later.")

# Handle Sending Referral Link Once Per Day
async def send_link(update: Update, context: CallbackContext):
  telegram_id = update.message.chat_id
  args = context.args

  if not args:
    await update.message.reply_text("Usage: /sendlink <your-referral-link>")
    return

  referral_link = args[0]
  user_response = supabase.table("user_profiles").select("*").eq("telegram_id", telegram_id).execute()
  if not user_response.data:
    await update.message.reply_text("You are not registered!")
    return

  user = user_response.data[0]
  user_id = user["id"]

  # Check if user already sent a link today
  today = datetime.now(timezone.utc).date()
  check_response = supabase.table("referral_links").select("*").eq("user_id", user_id).gte("created_at", str(today)).execute()
  if check_response.data:
    await update.message.reply_text("You can only send one referral link per day!")
    return

  # Insert the new referral link
  supabase.table("referral_links").insert({
      "user_id": user_id,
      "referral_link": referral_link,
      "created_at": datetime.now(timezone.utc).isoformat()
  }).execute()

  # Get random users to distribute the link
  random_users = supabase.table("user_profiles").select("telegram_id").neq("telegram_id", telegram_id).limit(5).execute()
  if not random_users.data:
    await update.message.reply_text("No users available to send your link.")
    return

  for user in random_users.data:
    try:
      await context.bot.send_message(user["telegram_id"], f"📢 New referral link shared: {referral_link}")
    except Exception as e:
      logger.error(f"Error sending message to {user['telegram_id']}: {e}")

  await update.message.reply_text("✅ Your referral link has been shared with random users!")

# Show Leaderboard
async def leaderboard(update: Update, context: CallbackContext):
  try:
    response = supabase.table("user_profiles").select("username, referrals, points").order("referrals", desc=True).limit(10).execute()

    leaderboard_text = "🏆 Referral Leaderboard:\n"
    for index, user in enumerate(response.data, start=1):
      leaderboard_text += f"{index}. {user['username']} - {user['referrals']} referrals, {user['points']} points\n"

    await update.message.reply_text(leaderboard_text)
  except Exception as e:
    logger.error(f"Error in leaderboard command: {e}")
    logger.error(traceback.format_exc())
    await update.message.reply_text("An error occurred while fetching the leaderboard. Please try again later.")

# Redeem Rewards
async def redeem(update: Update, context: CallbackContext):
  telegram_id = update.message.chat_id

  try:
    # Fetch user data asynchronously
    response = await asyncio.to_thread(
      lambda: supabase.table("user_profiles").select("*").eq("telegram_id", telegram_id).execute()
    )

    if not response.data:
      await update.message.reply_text("You are not registered!")
      return

    user = response.data[0]

    if user["points"] < 50:
      await update.message.reply_text("You need at least 50 points to redeem a reward.")
      return

    # Deduct points asynchronously
    await asyncio.to_thread(
      lambda: supabase.table("user_profiles").update(
          {"points": user["points"] - 50}
      ).eq("telegram_id", telegram_id).execute()
    )

    # Send confirmation message
    await update.message.reply_text("🎁 You have successfully redeemed a reward! Your points are now updated.")

  except Exception as e:
    logger.error(f"Error in redeem command: {e}")
    logger.error(traceback.format_exc())
    await update.message.reply_text("An error occurred while redeeming. Please try again later.")

# Help Command
async def help_command(update: Update, context: CallbackContext):
    help_text = """
    🤖 **ReferronBot Commands**

    Here are the available commands and how to use them:

    1. **/start** - Start using the bot and get your referral link.
      - Usage: `/start` or `/start <referral_code>` (if you were referred by someone).

    2. **/sendlink <your-referral-link>** - Share your referral link with others.
      - Usage: `/sendlink https://t.me/your_bot?start=your_referral_code`

    3. **/leaderboard** - View the top 10 users with the most referrals and points.
      - Usage: `/leaderboard`

    4. **/redeem** - Redeem rewards using your points.
      - Usage: `/redeem`

    5. **/help** - Get this help message.
      - Usage: `/help`

    📝 **Note**: You can only send one referral link per day.
      """
    await update.message.reply_text(help_text, parse_mode="Markdown")

# Error Handler
async def error_handler(update: Update, context: CallbackContext):
  print(f"Error: {context.error}")
  try:
    logger.error(f"An error occurred: {context.error}")

    # Log additional context if available
    if update and update.message:
      logger.error(f"Error in message: {update.message.text}")

    # Optionally send an error message
    if update and update.message:
      try:
        await update.message.reply_text("Sorry, an error occurred while processing your request.")
      except Exception as reply_error:
        logger.error(f"Could not send error reply: {reply_error}")

  except Exception as e:
    logger.error(f"Error in error handler: {e}")

# Background task to keep the event loop alive
def background_task():
  loop = asyncio.new_event_loop()
  asyncio.set_event_loop(loop)
  loop.run_forever()

# Webhook Route
@app.route("/webhook", methods=["POST"])
def webhook():
  global application
  if application is None:
    logger.error("Telegram bot application is not initialized")
    return Response("Telegram application not initialized", status=500)

  try:
    update_data = request.get_json(force=True)
    update = Update.de_json(update_data, application.bot)

    # ✅ Ensure application is initialized before processing updates
    if not application._initialized:
      logger.warning("Application is not initialized! Initializing now...")
      asyncio.run(initialize_bot())

    # Check if there's an active event loop
    try:
      loop = asyncio.get_running_loop()
      loop.create_task(application.process_update(update))  # Run in existing loop
    except RuntimeError:
      asyncio.run(application.process_update(update))  # Run in a new loop

    return Response("OK", status=200)
  except Exception as e:
    logger.error(f"Webhook processing error: {e}")
    logger.error(traceback.format_exc())
    return Response("Error processing webhook", status=500)

# Health Check
@app.route("/health", methods=["GET"])
def health_check():
  return Response("Bot is running", status=200)

# Initialize the bot
async def initialize_bot():
  global application

  if application is not None:
    logger.info("Telegram bot is already initialized.")
    return application

  try:
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # ✅ Initialize the application
    await application.initialize()   # REQUIRED!
    await application.start()

    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("leaderboard", leaderboard))
    application.add_handler(CommandHandler("redeem", redeem))
    application.add_handler(CommandHandler("sendlink", send_link))
    application.add_handler(CommandHandler("help", help_command))

    application.add_error_handler(error_handler)

    # Set webhook
    webhook_url = "https://refferonbot.onrender.com/webhook"
    logger.info(f"Setting webhook to: {webhook_url}")
    await application.bot.set_webhook(webhook_url)

    logger.info("Bot initialized successfully")

  except Exception as e:
    logger.error(f"Error initializing bot: {e}")
    logger.error(traceback.format_exc())
    raise

# Initialize the bot application
def create_app():
  global application

  # Start the background task to keep the event loop alive
  threading.Thread(target=background_task, daemon=True).start()

  # Initialize the bot
  loop = asyncio.get_event_loop()
  loop.run_until_complete(initialize_bot())

  logger.info("Flask app created and bot is initializing")
  return app

# Entry point for Gunicorn
app = create_app()

if __name__ == "__main__":
  app.run(debug=True)