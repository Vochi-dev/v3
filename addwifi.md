# Управление WiFi сетями на Mikrotik

## Добавление новой WiFi сети

Замените `НАЗВАНИЕ_СЕТИ`, `ПАРОЛЬ` и `имя-профиля`/`имя-интерфейса` на свои значения.

### 1. Создать профиль безопасности
```
/interface wireless security-profiles add name=имя-профиля mode=dynamic-keys authentication-types=wpa2-psk wpa2-pre-shared-key=ПАРОЛЬ
```

### 2. Создать виртуальный WiFi интерфейс
```
/interface wireless add name=wlan-имя mode=ap-bridge master-interface=wlan1 ssid=НАЗВАНИЕ_СЕТИ security-profile=имя-профиля disabled=no
```

### 3. Добавить в bridge (для работы в той же подсети)
```
/interface bridge port add bridge=bridge interface=wlan-имя
```

---

## Примеры

### Пример: сеть "Cdek-Logist" с паролем "1234qwer"
```
/interface wireless security-profiles add name=cdek-profile mode=dynamic-keys authentication-types=wpa2-psk wpa2-pre-shared-key=1234qwer
/interface wireless add name=wlan-cdek mode=ap-bridge master-interface=wlan1 ssid=Cdek-Logist security-profile=cdek-profile disabled=no
/interface bridge port add bridge=bridge interface=wlan-cdek
```

### Пример: сеть "audiobro.by" с паролем "f7fk332_djd9ndjH"
```
/interface wireless security-profiles add name=audiobro-profile mode=dynamic-keys authentication-types=wpa2-psk wpa2-pre-shared-key=f7fk332_djd9ndjH
/interface wireless add name=wlan-audiobro mode=ap-bridge master-interface=wlan1 ssid=audiobro.by security-profile=audiobro-profile disabled=no
/interface bridge port add bridge=bridge interface=wlan-audiobro
```

---

## Удаление WiFi сети

### 1. Удалить из bridge
```
/interface bridge port remove [find interface=wlan-имя]
```

### 2. Удалить WiFi интерфейс
```
/interface wireless remove [find name=wlan-имя]
```

### 3. Удалить профиль безопасности
```
/interface wireless security-profiles remove [find name=имя-профиля]
```

### Пример: удаление сети "Cdek-Logist"
```
/interface bridge port remove [find interface=wlan-cdek]
/interface wireless remove [find name=wlan-cdek]
/interface wireless security-profiles remove [find name=cdek-profile]
```

---

## Полезные команды

### Посмотреть все WiFi интерфейсы
```
/interface wireless print
```

### Посмотреть все профили безопасности
```
/interface wireless security-profiles print
```

### Включить/выключить WiFi интерфейс
```
/interface wireless enable wlan-имя
/interface wireless disable wlan-имя
```

### Изменить пароль существующей сети
```
/interface wireless security-profiles set имя-профиля wpa2-pre-shared-key=НОВЫЙ_ПАРОЛЬ
```

### Изменить SSID существующей сети
```
/interface wireless set wlan-имя ssid=НОВОЕ_НАЗВАНИЕ
```

---

## Ограничения

- Максимум **8 виртуальных WiFi сетей** на hAP mini (RB931-2nD)
- Рекомендуется не более **4-5 сетей** для стабильной работы
- Все виртуальные сети работают на том же канале что и master-interface (wlan1)


