# 🚀 Быстрый старт - PlanFix UI Kit

## 📦 Что внутри?

```
ui-kit/
├── README.md              # Полное описание
├── QUICK_START.md         # Этот файл
├── colors.md             # Палитра цветов
├── typography.md         # Шрифты
├── components/
│   └── buttons.md        # Кнопки
├── styles/
│   ├── variables.css     # CSS переменные
│   └── ui-kit.css        # Готовые классы
└── examples/
    └── demo.html         # Демо страница
```

---

## 🎯 Основные цвета

| Цвет | HEX | Использование |
|------|-----|---------------|
| **Зеленый** | `#6fa92e` | Кнопки, акценты, активные элементы |
| **Синий** | `#3377C3` | Ссылки |
| **Серый темный** | `#53586b` | Боковое меню |
| **Красный** | `#d9534f` | Опасные действия |
| **Текст** | `#343434` | Основной текст |

---

## 🔤 Шрифт

```css
font-family: -apple-system, "system-ui", "Segoe UI", roboto, "Helvetica Neue", helvetica, arial, sans-serif;
font-size: 13px;
```

---

## 🔘 Кнопки

### HTML
```html
<button class="pf-btn pf-btn-primary">Сохранить</button>
<button class="pf-btn pf-btn-secondary">Отмена</button>
<button class="pf-btn pf-btn-danger">Удалить</button>
```

### Размеры
```html
<button class="pf-btn pf-btn-primary pf-btn-sm">Маленькая</button>
<button class="pf-btn pf-btn-primary">Средняя</button>
<button class="pf-btn pf-btn-primary pf-btn-lg">Большая</button>
```

---

## 📝 Формы

```html
<div class="pf-form-group">
  <label class="pf-label">Название</label>
  <input type="text" class="pf-input" placeholder="Введите текст">
</div>

<div class="pf-btn-group">
  <button class="pf-btn pf-btn-primary">OK</button>
  <button class="pf-btn pf-btn-secondary">Отмена</button>
</div>
```

---

## 📊 Таблица

```html
<table class="pf-table">
  <thead>
    <tr>
      <th>№</th>
      <th>Название</th>
      <th>Статус</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>1</td>
      <td>Задача 1</td>
      <td>Новая</td>
    </tr>
  </tbody>
</table>
```

---

## 🎴 Карточка

```html
<div class="pf-card">
  <div class="pf-card-header">Заголовок</div>
  <div class="pf-card-body">
    <p>Контент</p>
  </div>
  <div class="pf-card-footer">
    <button class="pf-btn pf-btn-primary">Действие</button>
  </div>
</div>
```

---

## 🎨 Утилиты

### Отступы
```html
<div class="pf-mt-md">Верхний отступ</div>
<div class="pf-mb-lg">Нижний отступ</div>
<div class="pf-p-xl">Внутренний отступ</div>
```

### Flex
```html
<div class="pf-flex pf-gap-sm">
  <button>1</button>
  <button>2</button>
</div>

<div class="pf-flex pf-flex-between">
  <span>Слева</span>
  <span>Справа</span>
</div>
```

### Тени
```html
<div class="pf-card pf-shadow-md">Карточка с тенью</div>
```

---

## 📱 Подключение

### 1. Подключите CSS файлы
```html
<link rel="stylesheet" href="ui-kit/styles/variables.css">
<link rel="stylesheet" href="ui-kit/styles/ui-kit.css">
```

### 2. Используйте классы
```html
<button class="pf-btn pf-btn-primary">Кнопка</button>
```

### 3. Готово! 🎉

---

## 🎯 Примеры

### Форма входа
```html
<div class="pf-card" style="max-width: 400px; margin: 0 auto;">
  <div class="pf-card-header">Вход в систему</div>
  <div class="pf-card-body">
    <div class="pf-form-group">
      <label class="pf-label">Email</label>
      <input type="email" class="pf-input" placeholder="your@email.com">
    </div>
    <div class="pf-form-group">
      <label class="pf-label">Пароль</label>
      <input type="password" class="pf-input" placeholder="••••••••">
    </div>
  </div>
  <div class="pf-card-footer">
    <button class="pf-btn pf-btn-primary pf-w-full">Войти</button>
  </div>
</div>
```

### Список задач
```html
<div class="pf-card">
  <div class="pf-card-header">
    <div class="pf-flex pf-flex-between">
      <h2 class="pf-h2">Задачи</h2>
      <button class="pf-btn pf-btn-primary pf-btn-sm">+ Добавить</button>
    </div>
  </div>
  <div class="pf-card-body">
    <table class="pf-table">
      <thead>
        <tr>
          <th>Название</th>
          <th>Статус</th>
          <th>Действия</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td>Задача 1</td>
          <td>В работе</td>
          <td>
            <button class="pf-btn pf-btn-link pf-btn-sm">Редактировать</button>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</div>
```

---

## 📚 Дальше

- Откройте `examples/demo.html` для полной демонстрации
- Изучите `colors.md` для палитры цветов
- Изучите `typography.md` для типографики
- Изучите `components/buttons.md` для кнопок
- Читайте `README.md` для полного описания

---

## ⚠️ Важно

Этот UI Kit создан **только для изучения** дизайна PlanFix.  
Используйте эти принципы для создания **собственного** уникального дизайна.

---

**Удачи! 🚀**

