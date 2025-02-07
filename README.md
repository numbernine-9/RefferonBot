# ReferronBot

ReferronBot is a Telegram bot designed to manage referral programs. It allows users to generate unique referral codes, track referrals, earn points, and redeem rewards. The bot is built using Python and integrates with a PostgreSQL database hosted on Supabase.

---

## Features

- **Referral Code Generation**: Each user gets a unique referral code.
- **Referral Tracking**: Track who referred whom and reward points accordingly.
- **Leaderboard**: View the top users based on referrals and points.
- **Rewards System**: Users can redeem rewards using their earned points.
- **Row-Level Security (RLS)**: Secure database access with RLS policies.

---

## Technologies Used

- **Python**: Core programming language.
- **Supabase**: PostgreSQL database with RLS and authentication.
- **python-telegram-bot**: Library for interacting with the Telegram Bot API.
- **Render**: Hosting platform for the Python script.

---

## Setup Instructions

### Prerequisites

1. **Python 3.8+**: Install Python from [python.org](https://www.python.org/).
2. **Telegram Bot Token**: Create a bot using [BotFather](https://core.telegram.org/bots#botfather) and get the token.
3. **Supabase Account**: Sign up at [Supabase](https://supabase.com/) and create a project.
4. **Git**: Install Git from [git-scm.com](https://git-scm.com/).

### Installation

1. Clone the repository:

```bash
git clone https://github.com/your-username/ReferronBot.git
cd ReferronBot

python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

pip install -r requirements.txt

Create a .env file in the root directory:
SUPABASE_URL=your_supabase_url
SUPABASE_KEY=your_supabase_key
TELEGRAM_BOT_TOKEN=your_telegram_bot_token

python bot.py
```
