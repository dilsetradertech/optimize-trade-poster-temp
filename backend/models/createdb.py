import os
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# ────────────────────────── PostgreSQL Configuration
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_DB = os.getenv("POSTGRES_DB")
POSTGRES_USER = os.getenv("POSTGRES_USER")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")

# Validate required environment variables
required_vars = {
    "POSTGRES_DB": POSTGRES_DB,
    "POSTGRES_USER": POSTGRES_USER,
    "POSTGRES_PASSWORD": POSTGRES_PASSWORD,
}

missing_vars = [key for key, value in required_vars.items() if not value]
if missing_vars:
    raise ValueError(f"❌ Missing required environment variables: {', '.join(missing_vars)}")

# ────────────────────────── Database Connection
def get_db_connection():
    """Create and return a PostgreSQL database connection."""
    try:
        conn = psycopg2.connect(
            dbname=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
            host=POSTGRES_HOST,
            port=int(POSTGRES_PORT),
        )
        print("✅ Database connected successfully")
        return conn
    except psycopg2.OperationalError as e:
        print(f"❌ PostgreSQL Operational Error: {e}")
        raise
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        raise

def create_tables():
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # ────────────────────────── Extensions & Types
        cur.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
        cur.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'targetby_enum') THEN
                CREATE TYPE targetby_enum AS ENUM ('Percentage', 'Points');
            END IF;
        END$$;
        """)
        cur.execute("CREATE SEQUENCE IF NOT EXISTS trade_targets_id_seq;")

        # ────────────────────────── TABLES CREATION (in dependency order)

        # ROLES
        cur.execute("""
        CREATE TABLE IF NOT EXISTS roles (
            id   TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE
        );
        """)

        # USERS
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id       TEXT PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL,
            role_id  TEXT NOT NULL,
            is_active BOOLEAN DEFAULT true,
            mobile VARCHAR(15),
            otp VARCHAR(6),
            session_token TEXT,
            session_expiry TIMESTAMPTZ,
            is_login BOOLEAN DEFAULT false,
            created_at TIMESTAMP DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Kolkata'),
            role_updated_at TIMESTAMP,                                   
            FOREIGN KEY (role_id) REFERENCES roles(id)
        );
        """)

        # PROFILES
        cur.execute("""
        CREATE TABLE IF NOT EXISTS profiles (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL UNIQUE,
            firstname TEXT NOT NULL,
            lastname TEXT NOT NULL,
            mobileno TEXT NOT NULL,
            profileimage TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        """)

        # PERMISSIONS
        cur.execute("""
        CREATE TABLE IF NOT EXISTS permissions (
            id TEXT PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id TEXT NOT NULL,
            permission_name TEXT NOT NULL,
            is_enabled BOOLEAN NOT NULL DEFAULT FALSE,
            can_comment BOOLEAN DEFAULT TRUE,
            can_stop BOOLEAN,                
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        """)

        # SETTINGS
        cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            sl DOUBLE PRECISION NOT NULL DEFAULT 0.8,
            t1 DOUBLE PRECISION NOT NULL DEFAULT 10.0,
            t2 DOUBLE PRECISION NOT NULL DEFAULT 20.0,
            t3 DOUBLE PRECISION NOT NULL DEFAULT 30.0,
            targetby targetby_enum NOT NULL DEFAULT 'Percentage',
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        """)

        # TRADE_HISTORY
        cur.execute("""
        CREATE TABLE IF NOT EXISTS trade_history (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            scrip TEXT NOT NULL,
            tradetype TEXT NOT NULL,
            entryprice NUMERIC(10,2) NOT NULL,
            stoploss NUMERIC(10,2) NOT NULL,
            target1 NUMERIC(10,2) NOT NULL,
            target2 NUMERIC(10,2) NOT NULL,
            target3 NUMERIC(10,2) NOT NULL,
            exchangeid TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            user_id TEXT,
            user_name TEXT,
            security_id INTEGER,
            telegram_message_id BIGINT,
            partial_profit NUMERIC(10,2),
            partial_loss NUMERIC(10,2),
            completed_at TIMESTAMPTZ,
            segment VARCHAR,
            lot_size DOUBLE PRECISION,
            source TEXT,
            reason TEXT,
            chart_url TEXT,
            telegram_message_map JSONB,
            exchange_std TEXT,
            comment TEXT,
            position_type VARCHAR(10),
            exchange_segment VARCHAR(20),
            instrument VARCHAR(20)                       
        );
        """)

        # TRADE_TARGETS
        cur.execute("""
        CREATE TABLE IF NOT EXISTS trade_targets (
            id INTEGER PRIMARY KEY DEFAULT nextval('trade_targets_id_seq'),
            t1 NUMERIC(10,3),
            t1_hit BOOLEAN DEFAULT FALSE,
            t2 NUMERIC(10,3),
            t2_hit BOOLEAN DEFAULT FALSE,
            t3 NUMERIC(10,3),
            t3_hit BOOLEAN DEFAULT FALSE,
            trade_id UUID,
            stoploss_hit BOOLEAN DEFAULT FALSE,
            is_monitoring_complete BOOLEAN DEFAULT FALSE,
            partial_profit NUMERIC(10,2) DEFAULT 0,
            partial_loss NUMERIC(10,2) DEFAULT 0,
            completed_at TIMESTAMPTZ,
            source TEXT,
            t1_hit_at TIMESTAMPTZ,
            t2_hit_at TIMESTAMPTZ,
            t3_hit_at TIMESTAMPTZ,
            stoploss_hit_at TIMESTAMPTZ,                             
            FOREIGN KEY (trade_id) REFERENCES trade_history(id) ON DELETE CASCADE
        );
        """)

        # DRAFT
        cur.execute("""
        CREATE TABLE IF NOT EXISTS draft (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            scrip TEXT,
            tradetype TEXT,
            entryprice NUMERIC(10,2),
            stoploss NUMERIC(10,2),
            target1 NUMERIC(10,2),
            target2 NUMERIC(10,2),
            target3 NUMERIC(10,2),
            exchangeid TEXT,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            user_id TEXT,
            user_name TEXT,
            security_id INTEGER,
            completed_at TIMESTAMPTZ,
            source TEXT,
            reason TEXT,
            lot_size INT,
            instrument_name TEXT,
            chart_url TEXT,
            position_type VARCHAR(10),
            exchange_segment VARCHAR(20),
            draft BOOLEAN DEFAULT FALSE
        );    
        """)

        # TRADE_REASONS
        cur.execute("""
        CREATE TABLE IF NOT EXISTS trade_reasons (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            reason TEXT NOT NULL,
            created_by VARCHAR(50),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)     

        # API_TOKENS
        cur.execute("""
        CREATE TABLE IF NOT EXISTS api_tokens (
            id SERIAL PRIMARY KEY,
            client_id TEXT NOT NULL,
            access_token TEXT NOT NULL,
            backend TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMPTZ DEFAULT (CURRENT_TIMESTAMP + INTERVAL '24 HOURS'),
            status TEXT DEFAULT 'active',
            deleted_at TIMESTAMP
        );
        """)
        
        # TELEGRAM_CHANNELS
        cur.execute("""
        CREATE TABLE IF NOT EXISTS telegram_channels (
            id SERIAL PRIMARY KEY,
            channel_id TEXT UNIQUE NOT NULL,
            channel_key TEXT NOT NULL,
            channel_name TEXT DEFAULT '',
            allow_mcx BOOLEAN DEFAULT FALSE,
            allow_index BOOLEAN DEFAULT FALSE,
            allow_stock BOOLEAN DEFAULT FALSE,
            allow_btst BOOLEAN DEFAULT FALSE,
            allow_button BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT NOW(),
            allow_equity BOOLEAN DEFAULT TRUE,
            allow_selling BOOLEAN DEFAULT FALSE,
            allow_algo BOOLEAN DEFAULT FALSE,
            allow_future BOOLEAN DEFAULT FALSE
        );
        """)

        # PROFILE_BACKUP
        cur.execute("""
        CREATE TABLE IF NOT EXISTS profile_backup (
            id TEXT,
            profileimage TEXT
        );
        """)

        # ────────────────────────── DEFAULT ROLE + ADMIN USER
        cur.execute("""
        INSERT INTO roles (id, name) VALUES
        ('admin', 'admin'),
        ('analyst', 'analyst')
        ON CONFLICT (id) DO NOTHING;
        """)
             
        cur.execute("SELECT id FROM users WHERE username='admin';")
        if not cur.fetchone():
            admin_id = str(uuid.uuid4())
            hashed_pw = bcrypt.hashpw("admin".encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
            cur.execute("""
                INSERT INTO users (id, username, password, role_id)
                VALUES (%s, %s, %s, %s)
            """, (admin_id, "admin", hashed_pw, "admin"))

        # ────────────────────────── COMMIT
        conn.commit()
        cur.close()
        conn.close()
        print("✅ Database tables created successfully.")

    except Exception as e:
        print("❌ Error creating tables:", e)
