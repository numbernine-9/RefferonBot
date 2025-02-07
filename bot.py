import os
import asyncio
import random
import string
from datetime import datetime, timezone
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, CallbackContext
from supabase import create_client, Client

load_dotenv()

# Load Environment Variables
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Initialize Supabase
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Function to generate a unique referral code
def generate_referral_code():
  return ''.join(random.choices(string.ascii_letters + string.digits, k=8))

# Start Command
async def start(update: Update, context: CallbackContext):
  telegram_id = update.message.chat_id
  username = update.message.chat.username or "Unknown"

  # Check if user exists
  response = supabase.table("user_profiles").select("*").eq("telegram_id", telegram_id).execute()
  if response.data:
    user = response.data[0]
    referral_code = user["referral_code"]
  else:
    referral_code = generate_referral_code()
    supabase.table("user_profiles").insert({"telegram_id": telegram_id, "username": username, "referral_code": referral_code}).execute()

  # Send welcome message with referral link
  ref_link = f"https://t.me/{context.bot.username}?start={referral_code}"
  await update.message.reply_text(f"Welcome {username}! 🎉\nYour referral link: {ref_link}")

# Handle Referrals
async def handle_referral(update: Update, context: CallbackContext):
  args = context.args
  telegram_id = update.message.chat_id
  username = update.message.chat.username or "Unknown"

  if args:
    referred_by_code = args[0]

    # Check if user already exists
    response = supabase.table("user_profiles").select("*").eq("telegram_id", telegram_id).execute()
    if response.data:
      await update.message.reply_text("You're already registered!")
      return

    # Get referrer
    referrer_response = supabase.table("user_profiles").select("*").eq("referral_code", referred_by_code).execute()
    if not referrer_response.data:
      await update.message.reply_text("Invalid referral code.")
      return

    referrer = referrer_response.data[0]

    # Create new user entry
    referral_code = generate_referral_code()
    supabase.table("user_profiles").insert({
        "telegram_id": telegram_id,
        "username": username,
        "referral_code": referral_code,
        "referred_by": referred_by_code
    }).execute()

    # Update referrer’s referral count and points
    supabase.table("user_profiles").update({
        "referrals": referrer["referrals"] + 1,
        "points": referrer["points"] + 10  # 10 points per referral
    }).eq("telegram_id", referrer["telegram_id"]).execute()

    await update.message.reply_text("You have been registered successfully! ✅")
    await context.bot.send_message(referrer["telegram_id"], f"🎉 You got a referral! Your new referral count: {referrer['referrals']}, Points: {referrer['points']}")
  else:
    await update.message.reply_text("Welcome! Use your referral link to invite friends. 😊")

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
      print(f"Error sending message to {user['telegram_id']}: {e}")

  await update.message.reply_text("✅ Your referral link has been shared with random users!")

# Show Leaderboard
async def leaderboard(update: Update, context: CallbackContext):
  response = supabase.table("user_profiles").select("username, referrals, points").order("referrals", desc=True).limit(10).execute()

  leaderboard_text = "🏆 Referral Leaderboard:\n"
  for index, user in enumerate(response.data, start=1):
    leaderboard_text += f"{index}. {user['username']} - {user['referrals']} referrals, {user['points']} points\n"

  await update.message.reply_text(leaderboard_text)

# Redeem Rewards
async def redeem(update: Update, context: CallbackContext):
  telegram_id = update.message.chat_id
  response = supabase.table("user_profiles").select("*").eq("telegram_id", telegram_id).execute()

  if not response.data:
    await update.message.reply_text("You are not registered!")
    return

  user = response.data[0]
  if user["points"] < 50:
    await update.message.reply_text("You need at least 50 points to redeem a reward.")
    return

  # Deduct points and confirm redemption
  supabase.table("user_profiles").update({"points": user["points"] - 50}).eq("telegram_id", telegram_id).execute()
  await update.message.reply_text("🎁 You have successfully redeemed a reward! Your points are now updated.")

# Main Function to Run the Bot
def main():
  app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

  app.add_handler(CommandHandler("start", start))
  app.add_handler(CommandHandler("start", handle_referral))
  app.add_handler(CommandHandler("leaderboard", leaderboard))
  app.add_handler(CommandHandler("redeem", redeem))
  app.add_handler(CommandHandler("sendlink", send_link, block=False))

  print("Bot is running...")
  app.run_polling()

if __name__ == "__main__":
  main()
