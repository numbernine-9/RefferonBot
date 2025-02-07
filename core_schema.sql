-- =============================
-- ReferronBot Database Schema
-- =============================

-- Enable extensions if needed
-- Uncomment the following line if using UUIDs (optional)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- =============================
-- 1. Function: Generate Unique Referral Code
-- =============================
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

-- =============================
-- 1. User Profiles Table
-- =============================
CREATE TABLE user_profiles (
    id SERIAL PRIMARY KEY,
    user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    telegram_id BIGINT UNIQUE NOT NULL,
    username TEXT,
    referral_code TEXT UNIQUE NOT NULL DEFAULT generate_referral_code(),
    referred_by TEXT,  -- Stores the referral code of the referrer
    referrals INT DEFAULT 0,
    points INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Index for faster lookups
CREATE INDEX idx_user_profiles_telegram_id ON user_profiles(telegram_id);
CREATE INDEX idx_user_profiles_referral_code ON user_profiles(referral_code);

-- =============================
-- 2. Referrals Table (Tracks each referral)
-- =============================
CREATE TABLE referrals (
    id SERIAL PRIMARY KEY,
    referrer_id INT REFERENCES user_profiles(id) ON DELETE CASCADE,
    referred_id INT REFERENCES user_profiles(id) ON DELETE CASCADE,
    created_at TIMESTAMP DEFAULT NOW()
);

-- =============================
-- 3. Rewards Table
-- =============================
CREATE TABLE rewards (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    points_required INT NOT NULL
);

-- =============================
-- 4. User Rewards Table (Tracks redeemed rewards)
-- =============================
CREATE TABLE user_rewards (
    id SERIAL PRIMARY KEY,
    user_id INT REFERENCES user_profiles(id) ON DELETE CASCADE,
    reward_id INT REFERENCES rewards(id) ON DELETE CASCADE,
    redeemed_at TIMESTAMP DEFAULT NOW()
);

-- =============================
-- 1. Create Referral Links Table
-- =============================
CREATE TABLE referral_links (
    id SERIAL PRIMARY KEY,
    user_id INT REFERENCES user_profiles(id) ON DELETE CASCADE,
    referral_link TEXT NOT NULL,
    sent BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Ensure a user can only submit one referral link per day
CREATE UNIQUE INDEX unique_daily_referral ON referral_links (user_id, DATE(created_at));

-- =============================
-- 5. Enable Row-Level Security (RLS)
-- =============================
ALTER TABLE user_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE referrals ENABLE ROW LEVEL SECURITY;
ALTER TABLE rewards ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_rewards ENABLE ROW LEVEL SECURITY;
ALTER TABLE referral_links ENABLE ROW LEVEL SECURITY;

-- =============================
-- 6. RLS Policies
-- =============================
-- Users can only see their own data
CREATE POLICY "Users can view their own data"
ON user_profiles FOR SELECT
TO authenticated
USING (telegram_id = (SELECT telegram_id FROM auth.users WHERE auth.users.id = auth.uid()));

-- Users can insert themselves
CREATE POLICY "Users can insert themselves"
ON user_profiles FOR INSERT
WITH CHECK (true);

-- Users can insert their own referral links
CREATE POLICY "Users can insert their own links"
ON referral_links FOR INSERT
WITH CHECK (user_id = (SELECT id FROM user_profiles WHERE user_id = auth.uid()));

-- Users can only see their own referral links
CREATE POLICY "Users can view their own links"
ON referral_links FOR SELECT
USING (user_id = (SELECT id FROM user_profiles WHERE user_id = auth.uid()));

-- Admins can view all referral links (Modify role if needed)
CREATE POLICY "Admins can view all links"
ON referral_links FOR SELECT
USING (auth.role() = 'admin');

-- =============================
-- 7. Function: Handle New Referrals
-- =============================
CREATE OR REPLACE FUNCTION handle_new_referral()
RETURNS TRIGGER AS $$
BEGIN
    -- Insert into referrals table
    INSERT INTO referrals (referrer_id, referred_id, created_at)
    VALUES ((SELECT id FROM user_profiles WHERE referral_code = NEW.referred_by), NEW.id, NOW());

    -- Update referrer's stats
    UPDATE user_profiles
    SET referrals = referrals + 1,
        points = points + 10  -- Reward referrer with 10 points per invite
    WHERE referral_code = NEW.referred_by;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- =============================
-- 8. Trigger: Auto-Update Referrals Count
-- =============================
CREATE TRIGGER update_referrer_stats
AFTER INSERT ON user_profiles
FOR EACH ROW
WHEN (NEW.referred_by IS NOT NULL)
EXECUTE FUNCTION handle_new_referral();

-- =============================
-- 9. View: Leaderboard
-- =============================
CREATE VIEW referral_leaderboard AS
SELECT username, referrals, points
FROM user_profiles
ORDER BY referrals DESC, points DESC
LIMIT 10;

-- =============================
-- 10. Function: Redeem Rewards
-- =============================
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
    UPDATE user_profiles SET points = points - cost WHERE id = user_id;
    INSERT INTO user_rewards (user_id, reward_id, redeemed_at) VALUES (user_id, reward_id, NOW());

    RETURN 'Reward redeemed successfully!';
END;
$$ LANGUAGE plpgsql;

-- =============================
-- 4. Function: Auto-Mark Referral Link as Sent
-- =============================
CREATE OR REPLACE FUNCTION mark_referral_as_sent()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE referral_links
    SET sent = TRUE
    WHERE id = NEW.id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- =============================
-- 5. Trigger: Mark Referral Link as Sent After Distribution
-- =============================
CREATE TRIGGER trigger_mark_sent
AFTER UPDATE ON referral_links
FOR EACH ROW
WHEN (NEW.sent = TRUE)
EXECUTE FUNCTION mark_referral_as_sent();