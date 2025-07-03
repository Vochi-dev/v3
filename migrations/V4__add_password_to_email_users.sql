-- Добавление поля password в таблицу email_users для аутентификации партнеров предприятий
ALTER TABLE email_users ADD COLUMN IF NOT EXISTS password VARCHAR(255);

-- Добавляем комментарий к полю
COMMENT ON COLUMN email_users.password IS 'Хеш пароля для входа в админку предприятия'; 