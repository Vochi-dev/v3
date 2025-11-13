# 🔘 Кнопки PlanFix

## Типы кнопок

### 1. Primary Button (Основная кнопка)
Зеленая кнопка для главных действий.

```css
.pf-btn-primary {
  background-color: #6fa92e;
  color: #ffffff;
  border: none;
  border-radius: 3px;
  padding: 8px 16px;
  font-size: 13px;
  font-weight: 400;
  cursor: pointer;
  transition: background-color 0.2s ease;
}

.pf-btn-primary:hover {
  background-color: #5b951a;
}

.pf-btn-primary:active {
  background-color: #478106;
}
```

**Использование:**
- "Сохранить"
- "Создать"
- "Добавить"
- "Отправить"

**Пример HTML:**
```html
<button class="pf-btn pf-btn-primary">Сохранить</button>
```

---

### 2. Secondary Button (Вторичная кнопка)
Светлая кнопка для второстепенных действий.

```css
.pf-btn-secondary {
  background-color: #ffffff;
  color: #343434;
  border: 1px solid #d0d0d0;
  border-radius: 3px;
  padding: 8px 16px;
  font-size: 13px;
  font-weight: 400;
  cursor: pointer;
  transition: all 0.2s ease;
}

.pf-btn-secondary:hover {
  background-color: #f5f5f5;
  border-color: #b3b3b3;
}

.pf-btn-secondary:active {
  background-color: #e8e8e8;
}
```

**Использование:**
- "Отмена"
- "Закрыть"
- "Назад"

**Пример HTML:**
```html
<button class="pf-btn pf-btn-secondary">Отмена</button>
```

---

### 3. Icon Button (Кнопка-иконка)
Кнопка только с иконкой, без текста.

```css
.pf-btn-icon {
  background-color: transparent;
  color: #666;
  border: none;
  border-radius: 3px;
  padding: 6px;
  width: 32px;
  height: 32px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: background-color 0.2s ease;
}

.pf-btn-icon:hover {
  background-color: rgba(0, 0, 0, 0.05);
}

.pf-btn-icon:active {
  background-color: rgba(0, 0, 0, 0.1);
}
```

**Использование:**
- Настройки (шестеренка)
- Поиск (лупа)
- Меню (три точки)
- Закрыть (крестик)

**Пример HTML:**
```html
<button class="pf-btn pf-btn-icon">
  <img src="icon-settings.svg" alt="Настройки">
</button>
```

---

### 4. Danger Button (Опасное действие)
Красная кнопка для удаления/опасных действий.

```css
.pf-btn-danger {
  background-color: #d9534f;
  color: #ffffff;
  border: none;
  border-radius: 3px;
  padding: 8px 16px;
  font-size: 13px;
  font-weight: 400;
  cursor: pointer;
  transition: background-color 0.2s ease;
}

.pf-btn-danger:hover {
  background-color: #c9302c;
}

.pf-btn-danger:active {
  background-color: #ac2925;
}
```

**Использование:**
- "Удалить"
- "Отменить подписку"
- "Сбросить"

**Пример HTML:**
```html
<button class="pf-btn pf-btn-danger">Удалить</button>
```

---

### 5. Link Button (Кнопка-ссылка)
Кнопка в стиле текстовой ссылки.

```css
.pf-btn-link {
  background-color: transparent;
  color: #3377C3;
  border: none;
  padding: 4px 8px;
  font-size: 13px;
  font-weight: 400;
  cursor: pointer;
  text-decoration: none;
  transition: color 0.2s ease;
}

.pf-btn-link:hover {
  color: #2566a8;
  text-decoration: underline;
}
```

**Использование:**
- "Подробнее"
- "Отменить"
- "Скрыть"

**Пример HTML:**
```html
<button class="pf-btn pf-btn-link">Подробнее</button>
```

---

### 6. Menu Button (Кнопка меню)
Кнопка для боковой панели/меню.

```css
.pf-btn-menu {
  background-color: transparent;
  color: #dde0e2;
  border: none;
  border-radius: 3px;
  padding: 10px 12px;
  font-size: 13px;
  font-weight: 400;
  cursor: pointer;
  text-align: left;
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  transition: background-color 0.2s ease;
}

.pf-btn-menu:hover {
  background-color: #43495d;
}

.pf-btn-menu.active {
  background-color: #6fa92e;
  color: #ffffff;
}
```

**Использование:**
- Пункты бокового меню
- Навигация

**Пример HTML:**
```html
<button class="pf-btn pf-btn-menu">
  <img src="icon-tasks.svg" alt="">
  <span>Задачи</span>
</button>
```

---

## Размеры кнопок

### Small (Маленькая)
```css
.pf-btn-sm {
  padding: 6px 12px;
  font-size: 12px;
  height: 28px;
}
```

### Medium (Средняя) - по умолчанию
```css
.pf-btn {
  padding: 8px 16px;
  font-size: 13px;
  height: 34px;
}
```

### Large (Большая)
```css
.pf-btn-lg {
  padding: 10px 20px;
  font-size: 14px;
  height: 40px;
}
```

---

## Состояния кнопок

### Disabled (Отключена)
```css
.pf-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  pointer-events: none;
}
```

### Loading (Загрузка)
```css
.pf-btn.loading {
  position: relative;
  color: transparent;
}

.pf-btn.loading::after {
  content: "";
  position: absolute;
  width: 16px;
  height: 16px;
  top: 50%;
  left: 50%;
  margin-left: -8px;
  margin-top: -8px;
  border: 2px solid #ffffff;
  border-radius: 50%;
  border-top-color: transparent;
  animation: spin 0.6s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}
```

---

## Группы кнопок

### Горизонтальная группа
```css
.pf-btn-group {
  display: inline-flex;
  gap: 8px;
}
```

**Пример:**
```html
<div class="pf-btn-group">
  <button class="pf-btn pf-btn-primary">Сохранить</button>
  <button class="pf-btn pf-btn-secondary">Отмена</button>
</div>
```

### Вертикальная группа
```css
.pf-btn-group-vertical {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
```

---

## Кнопки с иконками

### Иконка слева
```html
<button class="pf-btn pf-btn-primary">
  <img src="icon-plus.svg" alt="" class="pf-btn-icon-left">
  <span>Добавить</span>
</button>
```

```css
.pf-btn-icon-left {
  width: 16px;
  height: 16px;
  margin-right: 6px;
}
```

### Иконка справа
```html
<button class="pf-btn pf-btn-secondary">
  <span>Подробнее</span>
  <img src="icon-arrow.svg" alt="" class="pf-btn-icon-right">
</button>
```

```css
.pf-btn-icon-right {
  width: 16px;
  height: 16px;
  margin-left: 6px;
}
```

---

## Особенности дизайна

### ✅ DO (Правильно)
- Используйте зеленый для главных действий
- Используйте белый/серый для второстепенных
- Используйте красный только для опасных действий
- Добавляйте hover эффекты
- Делайте кнопки достаточно большими (минимум 28px высотой)
- Используйте иконки для визуального восприятия
- Группируйте связанные кнопки

### ❌ DON'T (Неправильно)
- Не используйте слишком много primary кнопок
- Не делайте кнопки слишком маленькими
- Не используйте низкий контраст
- Не смешивайте разные стили
- Не забывайте про состояние :disabled

---

## Адаптивность

```css
@media (max-width: 768px) {
  .pf-btn {
    width: 100%;
    display: block;
  }
  
  .pf-btn-group {
    flex-direction: column;
  }
}
```

---

## Полный CSS

```css
/* Базовая кнопка */
.pf-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 8px 16px;
  font-size: 13px;
  font-weight: 400;
  font-family: -apple-system, "system-ui", "Segoe UI", roboto, "Helvetica Neue", helvetica, arial, sans-serif;
  line-height: 1;
  text-align: center;
  text-decoration: none;
  border: none;
  border-radius: 3px;
  cursor: pointer;
  transition: all 0.2s ease;
  user-select: none;
  white-space: nowrap;
}

.pf-btn:focus {
  outline: 2px solid #6fa92e;
  outline-offset: 2px;
}

/* Primary */
.pf-btn-primary {
  background-color: #6fa92e;
  color: #ffffff;
}

.pf-btn-primary:hover {
  background-color: #5b951a;
}

/* Secondary */
.pf-btn-secondary {
  background-color: #ffffff;
  color: #343434;
  border: 1px solid #d0d0d0;
}

.pf-btn-secondary:hover {
  background-color: #f5f5f5;
}

/* Danger */
.pf-btn-danger {
  background-color: #d9534f;
  color: #ffffff;
}

.pf-btn-danger:hover {
  background-color: #c9302c;
}

/* Link */
.pf-btn-link {
  background-color: transparent;
  color: #3377C3;
  padding: 4px 8px;
}

.pf-btn-link:hover {
  text-decoration: underline;
}

/* Disabled */
.pf-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
```

---

## Примеры использования

### Форма с кнопками
```html
<form class="pf-form">
  <input type="text" class="pf-input" placeholder="Название задачи">
  <div class="pf-btn-group">
    <button type="submit" class="pf-btn pf-btn-primary">Создать</button>
    <button type="button" class="pf-btn pf-btn-secondary">Отмена</button>
  </div>
</form>
```

### Панель действий
```html
<div class="pf-actions">
  <button class="pf-btn pf-btn-primary">
    <img src="icon-plus.svg" alt="">
    Добавить
  </button>
  <button class="pf-btn pf-btn-icon">
    <img src="icon-filter.svg" alt="Фильтр">
  </button>
  <button class="pf-btn pf-btn-icon">
    <img src="icon-sort.svg" alt="Сортировка">
  </button>
</div>
```

