-- ================================
-- ReferronBot Database Schema v2.0
-- ================================

-- Required Extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ================================
-- Core Functions
-- ================================

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION generate_referral_code()
RETURNS TEXT AS $$
DECLARE
  code TEXT;
BEGIN
  LOOP
    code := SUBSTRING(MD5(random()::text), 1, 8);
    EXIT WHEN NOT EXISTS (SELECT 1 FROM user_profiles WHERE referral_code = code);
  END LOOP;
  RETURN code;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION check_daily_sendlink_limit(user_id INTEGER)
RETURNS BOOLEAN AS $$
DECLARE
  last_sendlink_date DATE;
BEGIN
  SELECT created_at::DATE INTO last_sendlink_date
  FROM referral_links
  WHERE user_id = check_daily_sendlink_limit.user_id
  ORDER BY created_at DESC
  LIMIT 1;

  RETURN (last_sendlink_date IS NULL OR last_sendlink_date != CURRENT_DATE);
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION handle_new_referral()
RETURNS TRIGGER AS $$
BEGIN
  -- Insert into referrals table
  INSERT INTO referrals (referrer_id, referred_id, created_at)
  VALUES ((SELECT id FROM user_profiles WHERE referral_code = NEW.referred_by), NEW.id, NOW());

  -- Update referrer's stats
  UPDATE user_profiles
  SET
    referrals = referrals + 1,
    points = points + 10,  -- Reward referrer with 10 points per invite
    updated_at = NOW()
  WHERE referral_code = NEW.referred_by;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION mark_referral_as_sent()
RETURNS TRIGGER AS $$
BEGIN
  UPDATE referral_links
  SET
    sent = TRUE,
    updated_at = NOW()
  WHERE id = NEW.id;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION redeem_reward(user_id INT, reward_id INT)
RETURNS TEXT AS $$
DECLARE
  user_points INT;
  cost INT;
BEGIN
  -- Get user points
  SELECT points INTO user_points FROM user_profiles WHERE id = user_id;

  -- Get reward cost
  SELECT points_required INTO cost FROM rewards WHERE id = reward_id;

  -- Check if user has enough points
  IF user_points < cost THEN
    RETURN 'Not enough points to redeem this reward!';
  END IF;

  -- Deduct points and insert redemption record
  UPDATE user_profiles
  SET
    points = points - cost,
    updated_at = NOW()
  WHERE id = user_id;

  INSERT INTO user_rewards (user_id, reward_id)
  VALUES (user_id, reward_id);

RETURN 'Reward redeemed successfully!';
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION check_daily_referral_limit()
RETURNS TRIGGER AS $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM referral_links
    WHERE user_id = NEW.user_id
    AND created_at::date = NEW.created_at::date
  ) THEN
    RAISE EXCEPTION 'User can only create one referral link per day';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ================================
-- Core Tables
-- ================================

CREATE TABLE user_profiles (
  id SERIAL PRIMARY KEY,
  user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
  telegram_id BIGINT UNIQUE NOT NULL,
  username TEXT,
  first_name TEXT,
  last_name TEXT,
  email TEXT,
  referral_code TEXT UNIQUE NOT NULL DEFAULT generate_referral_code(),
  referred_by TEXT,
  referrals INTEGER DEFAULT 0,
  points INTEGER DEFAULT 0,
  sendlink_opportunities INTEGER DEFAULT 1,
  status TEXT DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'banned')),
  last_login TIMESTAMP WITH TIME ZONE,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE referrals (
  id SERIAL PRIMARY KEY,
  referrer_id INTEGER REFERENCES user_profiles(id) ON DELETE CASCADE,
  referred_id INTEGER REFERENCES user_profiles(id) ON DELETE CASCADE,
  status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'confirmed', 'rejected')),
  points_awarded INTEGER DEFAULT 0,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE rewards (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT,
  points_required INTEGER NOT NULL,
  reward_type TEXT NOT NULL CHECK (reward_type IN ('digital', 'physical', 'service')),
  quantity_available INTEGER DEFAULT -1,  -- -1 means unlimited
  status TEXT DEFAULT 'active' CHECK (status IN ('active', 'inactive', 'discontinued')),
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE user_rewards (
  id SERIAL PRIMARY KEY,
  user_id INTEGER REFERENCES user_profiles(id) ON DELETE CASCADE,
  reward_id INTEGER REFERENCES rewards(id) ON DELETE CASCADE,
  status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'processed', 'delivered', 'cancelled')),
  redeemed_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  delivered_at TIMESTAMP WITH TIME ZONE,
  notes TEXT
);

CREATE TABLE referral_links (
  id SERIAL PRIMARY KEY,
  user_id INTEGER REFERENCES user_profiles(id) ON DELETE CASCADE,
  referral_link TEXT NOT NULL,
  sent BOOLEAN DEFAULT FALSE,
  clicks INTEGER DEFAULT 0,
  conversions INTEGER DEFAULT 0,
  expires_at TIMESTAMP WITH TIME ZONE,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE user_payments (
  id SERIAL PRIMARY KEY,
  user_id INTEGER REFERENCES user_profiles(id) ON DELETE CASCADE,
  payment_amount NUMERIC NOT NULL,
  payment_wallet TEXT NOT NULL,
  transaction_id TEXT,
  payment_status TEXT DEFAULT 'pending' CHECK (payment_status IN ('pending', 'processing', 'completed', 'failed', 'refunded')),
  payment_type TEXT NOT NULL CHECK (payment_type IN ('withdrawal', 'deposit', 'reward')),
  currency TEXT DEFAULT 'USD',
  notes TEXT,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ================================
-- Indexes
-- ================================

CREATE INDEX idx_user_profiles_telegram_id ON user_profiles(telegram_id);
CREATE INDEX idx_user_profiles_referral_code ON user_profiles(referral_code);
CREATE INDEX idx_user_profiles_referred_by ON user_profiles(referred_by);
CREATE INDEX idx_referrals_referrer_id ON referrals(referrer_id);
CREATE INDEX idx_referrals_referred_id ON referrals(referred_id);
CREATE INDEX idx_user_rewards_user_id ON user_rewards(user_id);
CREATE INDEX idx_referral_links_user_id ON referral_links(user_id);
CREATE INDEX idx_user_payments_user_id ON user_payments(user_id);

-- ================================
-- Views
-- ================================

CREATE VIEW referral_leaderboard AS
SELECT
  username,
  referrals,
  points,
  RANK() OVER (ORDER BY referrals DESC, points DESC) as rank
FROM user_profiles
WHERE status = 'active'
LIMIT 10;

CREATE VIEW user_statistics AS
SELECT
  up.id,
  up.username,
  up.referrals,
  up.points,
  COUNT(DISTINCT r.id) as total_rewards_redeemed,
  COUNT(DISTINCT rl.id) as total_links_created,
  SUM(rl.clicks) as total_clicks,
  SUM(rl.conversions) as total_conversions
FROM user_profiles up
LEFT JOIN user_rewards r ON up.id = r.user_id
LEFT JOIN referral_links rl ON up.id = rl.user_id
GROUP BY up.id, up.username, up.referrals, up.points;

-- ================================
-- Triggers
-- ================================

CREATE TRIGGER update_referrer_stats
  AFTER INSERT ON user_profiles
  FOR EACH ROW
  WHEN (NEW.referred_by IS NOT NULL)
  EXECUTE FUNCTION handle_new_referral();

CREATE TRIGGER trigger_mark_sent
  AFTER UPDATE ON referral_links
  FOR EACH ROW
  WHEN (NEW.sent = TRUE)
  EXECUTE FUNCTION mark_referral_as_sent();

CREATE TRIGGER update_timestamp
  BEFORE UPDATE ON user_profiles
  FOR EACH ROW
  EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER enforce_daily_referral_limit
  BEFORE INSERT ON referral_links
  FOR EACH ROW
  EXECUTE FUNCTION check_daily_referral_limit();

-- ================================
-- Row Level Security (RLS)
-- ================================

ALTER TABLE user_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE referrals ENABLE ROW LEVEL SECURITY;
ALTER TABLE rewards ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_rewards ENABLE ROW LEVEL SECURITY;
ALTER TABLE referral_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_payments ENABLE ROW LEVEL SECURITY;

-- User Profiles Policies
CREATE POLICY "Users can view their own data"
  ON user_profiles FOR SELECT
  USING (user_id = auth.uid());

CREATE POLICY "Users can insert themselves"
  ON user_profiles FOR INSERT
  WITH CHECK (true);

CREATE POLICY "Users can update their own data"
  ON user_profiles FOR UPDATE
  USING (user_id = auth.uid());

-- Referral Links Policies
CREATE POLICY "Users can insert their own links"
  ON referral_links FOR INSERT
  WITH CHECK (user_id = (SELECT id FROM user_profiles WHERE user_id = auth.uid()));

CREATE POLICY "Users can view their own links"
  ON referral_links FOR SELECT
  USING (user_id = (SELECT id FROM user_profiles WHERE user_id = auth.uid()));

CREATE POLICY "Admins can view all links"
  ON referral_links FOR SELECT
  USING (auth.role() = 'admin');

-- User Payments Policies
CREATE POLICY "Users can view their own payments"
  ON user_payments FOR SELECT
  USING (user_id = (SELECT id FROM user_profiles WHERE user_id = auth.uid()));

CREATE POLICY "Users can insert their own payments"
  ON user_payments FOR INSERT
  WITH CHECK (user_id = (SELECT id FROM user_profiles WHERE user_id = auth.uid()));