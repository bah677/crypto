-- Создать БД (от суперпользователя postgres):
--   psql -U postgres -h localhost -f scripts/create_db.sql

CREATE DATABASE gold_adviser OWNER traiding_bot_ema_sub;
GRANT ALL PRIVILEGES ON DATABASE gold_adviser TO traiding_bot_ema_sub;
