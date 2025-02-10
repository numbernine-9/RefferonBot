from quart import Quart, request, Response
from telegram.ext import Application, CommandHandler, CallbackContext, CallbackQueryHandler
from telegram import Update
from supabase import create_client, Client
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from threading import Lock
import os
import traceback
import asyncio
import logging
from datetime import datetime, timezone
import random
import string
import time

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

# Initialize Quart app
app = Quart(__name__)

# Global variable to store the application
application = None
app_lock = Lock()

# Function to generate a unique referral code
def generate_referral_code():
  return ''.join(random.choices(string.ascii_letters + string.digits, k=8))

# Function to check if the user has already used their daily sendlink opportunity
async def check_daily_sendlink_limit(telegram_id: int) -> bool:
  """
  Check if the user has already used their daily sendlink opportunity.
  Returns True if the user can send a link today, False otherwise.
  """
  try:
    # Get the last sendlink date for the user
    response = await asyncio.to_thread(
      lambda: supabase.table("referral_links")
      .select("created_at")
      .eq("user_id", telegram_id)
      .order("created_at", desc=True)
      .limit(1)
      .execute()
    )

    if not response.data:
      # No sendlink exists for the user, so they can send one today
      return True

    last_sendlink_date = response.data[0]["created_at"].date()
    today = datetime.now(timezone.utc).date()

    # If the last sendlink was not today, the user can send one
    return last_sendlink_date != today

  except Exception as e:
    logger.error(f"Error checking daily sendlink limit: {e}")
    logger.error(traceback.format_exc())
    return False  # Assume the user cannot send a link if there's an error

# Start Command
async def start(update: Update, context: CallbackContext):
  try:
    telegram_id = update.message.chat_id
    username = update.message.chat.username or "Unknown"
    logger.info(f"Received /start command from {username}")

    # Check if user exists
    response = await asyncio.to_thread(
      lambda: supabase.table("user_profiles").select("*").eq("telegram_id", telegram_id).execute()
    )

    if response.data:
      user = response.data[0]
      referral_code = user["referral_code"]
    else:
      referral_code = generate_referral_code()
      referred_by_code = context.args[0] if context.args else None

      if referred_by_code:
        # Get referrer
        referrer_response = await asyncio.to_thread(
          lambda: supabase.table("user_profiles").select("*").eq("referral_code", referred_by_code).execute()
        )
        if not referrer_response.data:
          await update.message.reply_text("Invalid referral code.")
          return

        referrer = referrer_response.data[0]

        # Update referrer’s referral count and points
        await asyncio.to_thread(
          lambda: supabase.table("user_profiles").update({
            "referrals": referrer["referrals"] + 1,
            "points": referrer["points"] + 10
          }).eq("telegram_id", referrer["telegram_id"]).execute()
        )

      # Create new user entry
      await asyncio.to_thread(
        lambda: supabase.table("user_profiles").insert({
          "telegram_id": telegram_id,
          "username": username,
          "referral_code": referral_code,
          "referred_by": referred_by_code
        }).execute()
      )

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

  try:
    user_response = await asyncio.to_thread(
      lambda: supabase.table("user_profiles").select("*").eq("telegram_id", telegram_id).execute()
    )
    if not user_response.data:
      await update.message.reply_text("You are not registered!")
      return

    user = user_response.data[0]
    user_id = user["id"]

    # Check if the user has already used their free daily opportunity
    has_free_opportunity = await check_daily_sendlink_limit(user_id)

    # Check if the user has any paid opportunities left
    has_paid_opportunities = user["sendlink_opportunities"] > 0

    # If the user has no free opportunity and no paid opportunities, deny the request
    if not has_free_opportunity and not has_paid_opportunities:
      await update.message.reply_text("You have no sendlink opportunities left today.")
      return

    # Deduct one opportunity (free or paid)
    if has_free_opportunity:
      # Use the free opportunity
      pass  # No need to deduct anything for free opportunities
    else:
      # Use a paid opportunity
      await asyncio.to_thread(
        lambda: supabase.table("user_profiles").update(
          {"sendlink_opportunities": user["sendlink_opportunities"] - 1}
        ).eq("telegram_id", telegram_id).execute()
      )

    # Insert the new referral link
    await asyncio.to_thread(
      lambda: supabase.table("referral_links").insert({
        "user_id": user_id,
        "referral_link": referral_link,
        "created_at": datetime.now(timezone.utc).isoformat()
      }).execute()
    )

    # Determine the number of users to share the link with
    num_users_to_share = 30 if has_paid_opportunities else 3

    # Get random users to distribute the link
    random_users = await asyncio.to_thread(
      lambda: supabase.table("user_profiles").select("telegram_id")
      .neq("telegram_id", telegram_id)
      .limit(num_users_to_share)
      .execute()
    )

    if not random_users.data:
      await update.message.reply_text("No users available to send your link.")
      return

    for user in random_users.data:
      try:
        await context.bot.send_message(user["telegram_id"], f"📢 New referral link shared: {referral_link}")
      except Exception as e:
        logger.error(f"Error sending message to {user['telegram_id']}: {e}")

    await update.message.reply_text("✅ Your referral link has been shared with random users!")

  except Exception as e:
    logger.error(f"Error in sendlink command: {e}")
    logger.error(traceback.format_exc())
    await update.message.reply_text("An error occurred. Please try again later.")


# Show Leaderboard
async def leaderboard(update: Update, context: CallbackContext):
  try:
    response = await asyncio.to_thread(
      lambda: supabase.table("user_profiles").select("username, referrals, points").order("referrals", desc=True).limit(10).execute()
    )

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

#
async def buy_sendlink(update: Update, context: CallbackContext):
  telegram_id = update.message.chat_id

  # try:
  #   # Check if the user has already used their free daily opportunity
  #   if not await check_daily_sendlink_limit(telegram_id):
  #     await update.message.reply_text("You have already used your free daily sendlink opportunity.")
  #     return

  #   # Check if the user has already bought an additional opportunity today
  #   response = await asyncio.to_thread(
  #     lambda: supabase.table("user_payments").select("*")
  #     .eq("user_id", telegram_id)
  #     .eq("payment_status", "completed")
  #     .gte("created_at", str(datetime.now(timezone.utc).date()))
  #     .execute()
  #   )

  #   if response.data:
  #     await update.message.reply_text("You have already bought an additional sendlink opportunity today.")
  #     return

  #   # Provide payment instructions
  #   payment_wallet = "UQC7ULX1aBGwJBI5BRtYID0V5a0FxsBLOjb3Rwrkvl-r8l3k"  # Replace with your TON wallet address
  #   await update.message.reply_text(
  #     f"To buy an additional sendlink opportunity, send 1 TON to the following wallet address:\n\n"
  #     f"`{payment_wallet}`\n\n"
  #     f"Once the payment is confirmed, you will be able to send one more link today."
  #   )

  # except Exception as e:
  #   logger.error(f"Error in buy_sendlink command: {e}")
  #   logger.error(traceback.format_exc())
  #   await update.message.reply_text("An error occurred. Please try again later.")
  try:
    # Fetch user balance and sendlink opportunities
    user_response = await asyncio.to_thread(
      lambda: supabase.table("user_profiles").select("balance, sendlink_opportunities").eq("telegram_id", telegram_id).execute()
    )
    user = user_response.data[0] if user_response.data else None

    if not user:
      await update.message.reply_text("You are not registered!")
      return

    user_balance = user["balance"]
    sendlink_opportunities = user["sendlink_opportunities"]

    # Create inline keyboard
    keyboard = [
      [InlineKeyboardButton("Buy Ads", callback_data="buy_ads")],
      [InlineKeyboardButton("History", callback_data="view_history")],
      [InlineKeyboardButton("Wallet", callback_data="view_wallet")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Send message with inline keyboard
    await update.message.reply_text(
      f"Your balance: {user_balance} TON\n"
      f"Sendlink opportunities left today: {sendlink_opportunities}\n\n"
      "Choose an option:",
      reply_markup=reply_markup
  )

  except Exception as e:
    logger.error(f"Error in buy_sendlink command: {e}")
    logger.error(traceback.format_exc())
    await update.message.reply_text("An error occurred. Please try again later.")


async def handle_callback_query(update: Update, context: CallbackContext):
  query = update.callback_query
  await query.answer()

  if query.data == "buy_ads":
    await show_buy_ads_interface(query)
  elif query.data == "view_history":
    await show_transaction_history(query)
  elif query.data == "view_wallet":
    await show_wallet_balance(query)
  elif query.data == "back_to_main":
    await show_main_menu(query)
  elif query.data.startswith("buy_"):
    await handle_buy_impressions(query)
  elif query.data == "top_up_wallet":
    await top_up_wallet(query)

async def handle_buy_impressions(query):
  telegram_id = query.from_user.id
  impressions = int(query.data.split("_")[1])  # Extract impressions from callback_data
  price = impressions * 0.01  # Calculate price (0.01 TON per impression)

  try:
    # Fetch user balance
    user_response = await asyncio.to_thread(
      lambda: supabase.table("user_profiles").select("balance").eq("telegram_id", telegram_id).execute()
    )
    user_balance = user_response.data[0]["balance"] if user_response.data else 0

    # Check if the user has enough balance
    if user_balance < price:
      await query.edit_message_text("Insufficient balance. Please top up your wallet.")
      return

    # Deduct the price from the user's balance
    new_balance = user_balance - price
    await asyncio.to_thread(
      lambda: supabase.table("user_profiles").update({"balance": new_balance})
      .eq("telegram_id", telegram_id)
      .execute()
    )

    # Add the impressions to the user's sendlink opportunities
    await asyncio.to_thread(
      lambda: supabase.table("user_profiles").update({"sendlink_opportunities": impressions})
      .eq("telegram_id", telegram_id)
      .execute()
    )

    # Log the transaction
    await asyncio.to_thread(
      lambda: supabase.table("transactions").insert({
        "user_id": telegram_id,
        "amount": price,
        "transaction_type": "purchase",
        "status": "completed"
      }).execute()
    )

    # Notify the user
    await query.edit_message_text(f"✅ You have successfully purchased {impressions} impressions for {price} TON.")

  except Exception as e:
    logger.error(f"Error handling buy impressions: {e}")
    logger.error(traceback.format_exc())
    await query.edit_message_text("An error occurred. Please try again later.")

async def show_main_menu(query):
  # Fetch user balance
  telegram_id = query.from_user.id
  user_response = await asyncio.to_thread(
    lambda: supabase.table("user_profiles").select("balance").eq("telegram_id", telegram_id).execute()
  )
  user_balance = user_response.data[0]["balance"] if user_response.data else 0

  # Create inline keyboard
  keyboard = [
    [InlineKeyboardButton("Buy Ads", callback_data="buy_ads")],
    [InlineKeyboardButton("History", callback_data="view_history")],
    [InlineKeyboardButton("Wallet", callback_data="view_wallet")]
  ]
  reply_markup = InlineKeyboardMarkup(keyboard)

  # Send message with inline keyboard
  await query.edit_message_text(
    f"Your balance: {user_balance} TON\n\n"
    "Choose an option:",
    reply_markup=reply_markup
  )

async def show_buy_ads_interface(query):
  # Display the buy ads interface
  keyboard = [
    [InlineKeyboardButton("5 Impressions - 0.05 TON", callback_data="buy_5_impressions")],
    [InlineKeyboardButton("10 Impressions - 0.10 TON", callback_data="buy_10_impressions")],
    [InlineKeyboardButton("Back", callback_data="back_to_main")]
  ]
  reply_markup = InlineKeyboardMarkup(keyboard)

  await query.edit_message_text(
    "Buy Ads:\n\n"
    "1. 5 Impressions - 0.05 TON\n"
    "2. 10 Impressions - 0.10 TON\n\n"
    "Impressions are not equivalent to referrals. Not everybody who sees your link will click it and become your referrals.",
    reply_markup=reply_markup
  )

async def show_transaction_history(query):
  # Fetch and display transaction history
  telegram_id = query.from_user.id
  transactions = await asyncio.to_thread(
    lambda: supabase.table("transactions").select("*").eq("user_id", telegram_id).execute()
  )

  history_text = "Transaction History:\n\n"
  for transaction in transactions.data:
    history_text += f"{transaction['created_at']}: {transaction['amount']} TON ({transaction['status']})\n"

  keyboard = [[InlineKeyboardButton("Back", callback_data="back_to_main")]]
  reply_markup = InlineKeyboardMarkup(keyboard)

  await query.edit_message_text(history_text, reply_markup=reply_markup)

async def show_wallet_balance(query):
  # Fetch and display wallet balance
  telegram_id = query.from_user.id
  user_response = await asyncio.to_thread(
    lambda: supabase.table("user_profiles").select("balance").eq("telegram_id", telegram_id).execute()
  )
  user_balance = user_response.data[0]["balance"] if user_response.data else 0

  keyboard = [
    [InlineKeyboardButton("Top Up", callback_data="top_up_wallet")],
    [InlineKeyboardButton("Back", callback_data="back_to_main")]
  ]
  reply_markup = InlineKeyboardMarkup(keyboard)

  await query.edit_message_text(
    f"Your balance: {user_balance} TON\n\n"
    "Choose an option:",
    reply_markup=reply_markup
  )

async def top_up_wallet(query):
  telegram_id = query.from_user.id
  payment_wallet = "UQC7ULX1aBGwJBI5BRtYID0V5a0FxsBLOjb3Rwrkvl-r8l3k"  # Replace with your TON wallet address

  await query.edit_message_text(
    f"To top up your wallet, send TON to the following address:\n\n"
    f"`{payment_wallet}`\n\n"
    "Once the payment is confirmed, your balance will be updated."
  )

# Help Command
async def help_command(update: Update, context: CallbackContext):
    help_text = """
    🤖 **ReferronBot Commands**

    Here are the available commands and how to use them:

    1. **/start** - Start using the bot and get your referral link.
      - Usage: `/start` or `/start <referral_code>` (if you were referred by someone).

    2. **/sendlink <your-referral-link>** - Share your referral link with others.
      - Usage: `/sendlink https://t.me/your_bot?start=your_referral_code`

    3. **/buysendlink** - Buy daily sendlink opportunities.
      - Usage: `/buysendlink`

    4. **/leaderboard** - View the top 10 users with the most referrals and points.
      - Usage: `/leaderboard`

    5. **/redeem** - Redeem rewards using your points.
      - Usage: `/redeem`

    6. **/help** - Get this help message.
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

@app.route("/payment-confirmation", methods=["POST"])
async def payment_confirmation():
  data = await request.get_json()
  telegram_id = data.get("telegram_id")
  payment_wallet = data.get("payment_wallet")
  amount = data.get("amount")

  try:
    # Update user balance
    await asyncio.to_thread(
      lambda: supabase.table("user_profiles").update({"balance": amount})
      .eq("telegram_id", telegram_id)
      .execute()
    )

    # Log the transaction
    await asyncio.to_thread(
      lambda: supabase.table("transactions").insert({
        "user_id": telegram_id,
        "amount": amount,
        "transaction_type": "topup",
        "status": "completed"
      }).execute()
    )

    # Notify the user
    await application.bot.send_message(telegram_id, f"Your wallet has been topped up with {amount} TON.")

    return Response("Payment confirmed", status=200)
  except Exception as e:
    logger.error(f"Error confirming payment: {e}")
    logger.error(traceback.format_exc())
    return Response("Error confirming payment", status=500)


# Webhook Route
@app.route("/webhook", methods=["POST"])
async def webhook():
  global application
  if application is None:
    logger.error("Telegram bot application is not initialized")
    return Response("Telegram application not initialized", status=500)

  try:
    update_data = await request.get_json(force=True) # Await the JSON data
    update = Update.de_json(update_data, application.bot)

    # ✅ Ensure application is initialized before processing updates
    if not application._initialized:
      logger.warning("Application is not initialized! Initializing now...")
      # asyncio.run(initialize_bot())
      await initialize_bot()

    await application.process_update(update)
    return Response("OK", status=200)
  except Exception as e:
    logger.error(f"Webhook processing error: {e}")
    logger.error(traceback.format_exc())
    return Response("Error processing webhook", status=500)

# Health Check
@app.route("/health", methods=["GET"])
async def health_check():
  if application is None or not application._initialized:
    return Response("Bot is not initialized", status=503)
  return Response("Bot is running", status=200)

# Initialize the bot
async def initialize_bot():
  global application

  with app_lock:
    if application is not None:
      logger.info("Telegram bot is already initialized.")
      return application

    retries = 3
    for attempt in range(retries):
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
        application.add_handler(CommandHandler("buysendlink", buy_sendlink))
        application.add_handler(CommandHandler("help", help_command))

        application.add_handler(CallbackQueryHandler(handle_callback_query))


        application.add_error_handler(error_handler)

        # Set webhook
        webhook_url = "https://refferonbot.onrender.com/webhook"
        logger.info(f"Setting webhook to: {webhook_url}")
        await set_webhook_with_retry(application, webhook_url)

        logger.info("Bot initialized successfully")
        return application
      except Exception as e:
        logger.error(f"Error initializing bot: {e}")
        logger.error(traceback.format_exc())
        raise

async def set_webhook_with_retry(application, webhook_url):
  retries = 3
  for attempt in range(retries):
    try:
      await application.bot.set_webhook(webhook_url)
      logger.info(f"Webhook set successfully: {webhook_url}")
      return
    except Exception as e:
      logger.error(f"Error setting webhook (attempt {attempt + 1}/{retries}): {e}")
      if attempt == retries - 1:
          raise
      time.sleep(1)  # Wait before retrying


# Initialize the bot application
def create_app():
  global application

  # Initialize the bot
  asyncio.run(initialize_bot())

  logger.info("Quart app created and bot is initializing")
  return app


# Entry point for Gunicorn
app = create_app()

# Entry point for Gunicorn
if __name__ == "__main__":
  app.run(debug=True)