# 🔐 **SSL СЕРТИФИКАТЫ - ТРЕКЕР И МОНИТОРИНГ**

**Домен:** `bot.vochi.by`  
**Последнее обновление:** 09.08.2025
**Статус:** ✅ Активен  

---

## 📊 **ТЕКУЩИЙ СТАТУС СЕРТИФИКАТА**

### 🗓️ **Активный сертификат:**
- **Выдан:** 03 августа 2025
- **Действителен до:** 01 ноября 2025, 20:01:52 GMT
- **Остается:** ~84 дней
- **Статус:** ✅ АКТИВЕН
- **Тип:** Let's Encrypt (бесплатный)

### 📈 **История продлений:**
| Дата | Действителен до | Способ продления | Статус |
|------|----------------|------------------|---------|
| 03.08.2025 | 01.11.2025 | Manual Standalone | ✅ Успешно |
| 14.05.2025 | 12.08.2025 | Webroot (устарел) | ⚠️ Истек |

---

## 🛠️ **КОНФИГУРАЦИЯ NGINX**

### 📂 **Путь к сертификатам:**
```nginx
ssl_certificate     /etc/letsencrypt/live/bot.vochi.by/fullchain.pem;
ssl_certificate_key /etc/letsencrypt/live/bot.vochi.by/privkey.pem;
```

### 🔧 **HTTP Challenge конфигурация:**
```nginx
server {
    listen 80;
    server_name bot.vochi.by;

    # Для Let's Encrypt certificate challenge
    location /.well-known/acme-challenge/ {
        root /var/www/html;
        try_files $uri $uri/ =404;
    }

    # Редирект всего остального на HTTPS
    location / {
        return 301 https://$host$request_uri;
    }
}
```

---

## ⚡ **ПРОЦЕДУРЫ ПРОДЛЕНИЯ**

### 🎯 **МЕТОД 1: Автоматическое продление (РЕКОМЕНДУЕМЫЙ)**

```bash
# Проверить статус автопродления
systemctl status certbot.timer

# Включить автопродление
systemctl enable certbot.timer
systemctl start certbot.timer

# Тестовое продление
certbot renew --dry-run

# Принудительное продление (если нужно)
certbot renew --force-renewal
```

### 🎯 **МЕТОД 2: Ручное продление (ИСПОЛЬЗУЕТСЯ СЕЙЧАС)**

```bash
# Остановить nginx
systemctl stop nginx

# Продлить сертификат standalone методом  
certbot renew --force-renewal --standalone

# Запустить nginx
systemctl start nginx

# Проверить новый сертификат
openssl s_client -connect bot.vochi.by:443 -servername bot.vochi.by </dev/null 2>/dev/null | openssl x509 -noout -dates
```

### 🎯 **МЕТОД 3: Webroot продление (НЕ РАБОТАЕТ - ТРЕБУЕТ НАСТРОЙКИ)**

```bash
# Создать папку для challenge
mkdir -p /var/www/html/.well-known/acme-challenge
chmod 755 /var/www/html/.well-known/acme-challenge

# Продлить через webroot
certbot renew --webroot -w /var/www/html
```

---

## 🚨 **ДИАГНОСТИКА ПРОБЛЕМ**

### ❌ **Известные проблемы:**

#### **1. Webroot метод не работает**
**Симптомы:** `certbot renew --dry-run` fails  
**Причина:** Отсутствует папка `.well-known` или nginx блокирует доступ  
**Решение:** Использовать standalone метод

#### **2. Certbot не видит сертификат**
**Симптомы:** `certbot certificates` показывает пустой список  
**Причина:** Сертификат создан вручную или другим способом  
**Решение:** Конфиг в `/etc/letsencrypt/renewal/bot.vochi.by.conf` существует

#### **3. Permission denied**
**Симптомы:** Ошибки доступа при продлении  
**Решение:** Запускать certbot от root

### 🔍 **Команды диагностики:**

```bash
# Проверить текущий сертификат
openssl s_client -connect bot.vochi.by:443 -servername bot.vochi.by </dev/null 2>/dev/null | openssl x509 -noout -dates

# Проверить статус certbot timer
systemctl status certbot.timer

# Посмотреть логи certbot
tail -50 /var/log/letsencrypt/letsencrypt.log

# Проверить конфигурацию renewal
cat /etc/letsencrypt/renewal/bot.vochi.by.conf

# Тестовое продление
certbot renew --dry-run -v
```

---

## 📅 **КАЛЕНДАРЬ МОНИТОРИНГА**

### 🗓️ **Критические даты:**

| Событие | Дата | Действие |
|---------|------|----------|
| **Текущий сертификат истекает** | 01.11.2025 | Автопродление за 30 дней |
| **Критический срок продления** | 01.10.2025 | Ручная проверка |
| **Предупреждение** | 15.10.2025 | Email уведомление |

### ⏰ **Рекомендуемый график проверок:**

- **Еженедельно:** Проверка статуса `systemctl status certbot.timer`
- **Ежемесячно:** Тестовое продление `certbot renew --dry-run`
- **За 45 дней до истечения:** Принудительная проверка
- **За 30 дней до истечения:** Резервное ручное продление

---

## 🔔 **НАСТРОЙКА МОНИТОРИНГА**

### 📧 **Email уведомления:**

```bash
# Добавить в crontab проверку сертификата
# Отправка email за 30 дней до истечения
0 9 * * * /usr/local/bin/cert-monitor.sh
```

### 📱 **Скрипт мониторинга (cert-monitor.sh):**

```bash
#!/bin/bash
DOMAIN="bot.vochi.by"
DAYS_WARNING=30

# Получить дату истечения сертификата
EXPIRY_DATE=$(openssl s_client -connect $DOMAIN:443 -servername $DOMAIN </dev/null 2>/dev/null | openssl x509 -noout -enddate | cut -d= -f2)
EXPIRY_EPOCH=$(date -d "$EXPIRY_DATE" +%s)
CURRENT_EPOCH=$(date +%s)
DAYS_LEFT=$(( ($EXPIRY_EPOCH - $CURRENT_EPOCH) / 86400 ))

if [ $DAYS_LEFT -le $DAYS_WARNING ]; then
    echo "⚠️ SSL certificate for $DOMAIN expires in $DAYS_LEFT days!" | mail -s "SSL Certificate Warning" admin@company.com
fi
```

### 🤖 **Автоматическое продление в cron:**

```bash
# Добавить в /etc/crontab
0 3 * * * root /usr/bin/certbot renew --quiet --renew-hook "systemctl reload nginx"
```

---

## 📋 **ЧЕКЛИСТ ЭКСТРЕННОГО ПРОДЛЕНИЯ**

### ⚡ **При срочном продлении (за 1-2 дня до истечения):**

- [ ] Создать backup текущего сертификата
- [ ] Остановить nginx: `systemctl stop nginx`
- [ ] Принудительное продление: `certbot renew --force-renewal --standalone`
- [ ] Запустить nginx: `systemctl start nginx`
- [ ] Проверить новые даты сертификата
- [ ] Протестировать сайт в браузере
- [ ] Обновить этот файл cert.md
- [ ] Уведомить команду об успешном продлении

### 🆘 **Если продление не работает:**

- [ ] Проверить DNS записи домена
- [ ] Убедиться что порты 80/443 открыты
- [ ] Временно отключить firewall
- [ ] Попробовать создать новый сертификат: `certbot certonly --standalone -d bot.vochi.by`
- [ ] В крайнем случае - обратиться к провайдеру хостинга

---

## 📚 **ПОЛЕЗНЫЕ ССЫЛКИ**

- [Let's Encrypt Documentation](https://letsencrypt.org/docs/)
- [Certbot User Guide](https://certbot.eff.org/docs/using.html)
- [SSL Labs SSL Test](https://www.ssllabs.com/ssltest/analyze.html?d=bot.vochi.by)
- [Certificate Transparency Log](https://crt.sh/?q=bot.vochi.by)

---

## 📱 **ИНТЕГРАЦИЯ В АДМИН ПАНЕЛЬ**

### 🎯 **Отображение в Services Modal**
Информация о SSL сертификате интегрирована в модалку "Сервисы системы" в админ панели:

**Endpoint:** `/admin/ssl-cert-info`  
**Расположение:** После баланса WebSMS, перед таблицей сервисов  

**Отображаемая информация:**
- 🔐 **Статус сертификата** (В норме/Внимание/Критично)
- 📅 **Дата истечения** в формате ДД.ММ.ГГГГ
- ⏰ **Количество дней до истечения**
- 🚨 **Цветовая индикация статуса**

**Статусы:**
- ✅ **В норме** (>30 дней) - зеленый
- ⚠️ **Внимание** (7-30 дней) - желтый  
- 🚨 **Критично** (<7 дней) - красный

**Автообновление:** При открытии модалки и нажатии кнопки "🔄 Обновить"

---

## 📝 **ЛОГИ ПОСЛЕДНИХ ДЕЙСТВИЙ**

### 03.08.2025 - Критическое продление + Интеграция в админку
```
⚠️ ПРОБЛЕМА: Сертификат истекал 12.08.2025 (через 9 дней)
🔧 ПРИЧИНА: Автопродление не работало (webroot проблема)
✅ РЕШЕНИЕ: Принудительное продление standalone методом
🎉 РЕЗУЛЬТАТ: Новый сертификат до 01.11.2025
🔗 БОНУС: Интеграция мониторинга в админ панель
```

**Выполненные действия:**
1. Диагностика: certbot timer active, но логи показывали failures
2. Проблема: отсутствует папка .well-known для HTTP challenge
3. Исправление: добавлена nginx конфигурация для .well-known
4. Создана папка /var/www/html/.well-known/acme-challenge
5. Принудительное продление с остановкой nginx
6. Сертификат продлен на 90 дней (до 01.11.2025)
7. **NEW:** Создан API endpoint `/admin/ssl-cert-info`
8. **NEW:** Интегрирован в модалку Services админ панели
9. **NEW:** Добавлен визуальный мониторинг в реальном времени

**Следующие шаги:**
- [ ] Настроить автоматическое продление на standalone метод
- [x] Создать скрипт мониторинга
- [x] Добавить cron задачу для проверки  
- [x] Добавить визуальный мониторинг в админку
- [ ] Протестировать автопродление за 60 дней до истечения

---

## 🎯 **NEXT ACTIONS**

1. **Сентябрь 2025:** Протестировать автопродление
2. **Октябрь 2025:** Резервная проверка перед истечением  
3. **Ноябрь 2025:** Мониторинг нового продления

*Этот документ автоматически обновляется при каждом продлении сертификата.*