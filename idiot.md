# Сравнение кода паттернов 2-12 и 2-13

## ПАТТЕРН 2-12 (РАБОТАЮЩИЙ)

```javascript
case 23:
    // ======= ПАТТЕРН 2-12: ДИНАМИЧЕСКИЕ СОБЫТИЯ DIAL В ЗАВИСИМОСТИ ОТ ЗАПОЛНЕННЫХ ШАГОВ =======
    
    // Получаем базовую информацию
    const phone_2_12 = (formData.get('external_phone') || 'UNKNOWN_PHONE').replace(/^\+/, '');
    const trunk_2_12 = formData.get('line_id') || 'UNKNOWN_LINE';
    const answeredManager_2_12 = formData.get('answered_manager_final') || '150';
    
    // Собираем группы менеджеров для каждого шага
    const step1Extensions_2_12 = [
        formData.get('step1_manager_1'),
        formData.get('step1_manager_2'),
        formData.get('step1_manager_3'),
        formData.get('step1_manager_4')
    ].filter(value => value && value !== '');
    
    const step2Extensions_2_12 = [
        formData.get('step2_manager_1'),
        formData.get('step2_manager_2'),
        formData.get('step2_manager_3'),
        formData.get('step2_manager_4')
    ].filter(value => value && value !== '');
    
    const step3Extensions_2_12 = [
        formData.get('step3_manager_1'),
        formData.get('step3_manager_2'),
        formData.get('step3_manager_3'),
        formData.get('step3_manager_4')
    ].filter(value => value && value !== '');
    
    // Определяем активные шаги на основе заполненных данных
    const activeSteps_2_12 = [];
    if (step1Extensions_2_12.length > 0) activeSteps_2_12.push({step: 1, extensions: step1Extensions_2_12});
    if (step2Extensions_2_12.length > 0) activeSteps_2_12.push({step: 2, extensions: step2Extensions_2_12});
    if (step3Extensions_2_12.length > 0) activeSteps_2_12.push({step: 3, extensions: step3Extensions_2_12});
    
    // Проверяем что хотя бы один шаг заполнен
    if (activeSteps_2_12.length === 0) {
        console.warn('Паттерн 2-12: Не заполнен ни один шаг с менеджерами');
        // Добавляем дефолтный шаг
        activeSteps_2_12.push({step: 1, extensions: ['150']});
    }
    
    // Генерируем UniqueId для каждого события
    const uniqueIds_2_12 = {
        start: generateUniqueId("1757774467.253"),
        new_callerid: generateUniqueId("1757774467.253"),
        bridge_create: generateUniqueId(""),
        bridge_external: generateUniqueId("1757774467.253"),
        bridge_manager: generateUniqueId("1757774482.256"),
        bridge_leave_manager: generateUniqueId("1757774482.256"),
        bridge_leave_external: generateUniqueId("1757774467.253"),
        bridge_destroy: generateUniqueId(""),
        hangup: generateUniqueId("1757774467.253")
    };
    
    // Генерируем уникальные ID для каждого dial события
    const dialUniqueIds_2_12 = activeSteps_2_12.map((_, index) => 
        generateUniqueId("1757774467.253")
    );
    
    // UUID для моста
    const bridgeId_2_12 = generateBridgeUUID();
    
    let currentDelay = 0;
    
    // ШАГ 1: START
    events.push({
        step: 1,
        event: "start",
        delay: currentDelay,
        data: {
            UniqueId: uniqueIds_2_12.start,
            Token: "375293332255",
            Phone: phone_2_12,
            CallType: 0,
            Trunk: trunk_2_12
        }
    });
    currentDelay += 1000;
    
    // ШАГ 2: NEW_CALLERID (исходный канал)
    events.push({
        step: 2,
        event: "new_callerid",
        delay: currentDelay,
        data: {
            UniqueId: uniqueIds_2_12.new_callerid,
            Channel: `SIP/${trunk_2_12}-0000006c`,
            Exten: trunk_2_12,
            Context: "from-out-office",
            CallerIDNum: phone_2_12,
            ConnectedLineNum: "<unknown>",
            Token: "375293332255",
            ConnectedLineName: "<unknown>",
            CallerIDName: `-${phone_2_12}`
        }
    });
    currentDelay += 1000;
    
    // ДИНАМИЧЕСКИЕ DIAL СОБЫТИЯ - генерируем столько, сколько шагов заполнено
    activeSteps_2_12.forEach((stepData, index) => {
        events.push({
            step: 3 + index,
            event: "dial",
            delay: currentDelay,
            data: {
                UniqueId: dialUniqueIds_2_12[index],
                Extensions: stepData.extensions,
                Phone: phone_2_12,
                ExtTrunk: "",
                Trunk: trunk_2_12,
                Token: "375293332255",
                ExtPhone: phone_2_12,
                CallType: 0
            }
        });
        // Между dial событиями делаем паузу 14 секунд (как в DB файле)
        currentDelay += (index < activeSteps_2_12.length - 1) ? 14000 : 8000;
    });
    
    // ... дальше идут остальные события ...
    
    break;
```

## ПАТТЕРН 2-13 (ТЕКУЩЕЕ СОСТОЯНИЕ)

```javascript
case 24:
    // ======= ПАТТЕРН 2-13: РЕГЛАМЕНТИРОВАННЫЕ 3 DIAL + МОБИЛЬНЫЕ СОБЫТИЯ (19 событий) =======
    
    // БЕРЕМ ВСЕ ДАННЫЕ ИЗ ФОРМЫ (как в 2-12)
    const phone_2_13 = (formData.get('external_phone') || '375447034448').replace(/^\+/, '');
    const trunk_2_13 = formData.get('line_id') || '0001363';
    const answeredManager_2_13 = formData.get('answered_manager_final') || '151';
    const mobileManager_2_13 = formData.get('step3_mobile_manager') || '375296254070';
    const mobileLine_2_13 = formData.get('step3_mobile_line') || '0001366';
    
    // Собираем группы менеджеров для каждого шага ИЗ ФОРМЫ (как в 2-12)
    const step1Extensions_2_13 = [
        formData.get('step1_manager_1'),
        formData.get('step1_manager_2'),
        formData.get('step1_manager_3'),
        formData.get('step1_manager_4')
    ].filter(value => value && value !== '');
    
    const step2Extensions_2_13 = [
        formData.get('step2_manager_1'),
        formData.get('step2_manager_2'),
        formData.get('step2_manager_3'),
        formData.get('step2_manager_4')
    ].filter(value => value && value !== '');
    
    // ФИКСИРОВАННЫЕ 3 DIAL события согласно DB файлу:
    // DIAL #1: только первый менеджер из step1 - ОБЯЗАТЕЛЬНО должен быть заполнен
    const dial1Extensions_2_13 = [step1Extensions_2_13[0]];
    
    // DIAL #2: первый из step1 + первый из step2 - ОБА ОБЯЗАТЕЛЬНЫ  
    const dial2Extensions_2_13 = [step1Extensions_2_13[0], step2Extensions_2_13[0]];
    
    // DIAL #3: первый из step1 + мобильный менеджер + первый из step2 - ВСЕ ОБЯЗАТЕЛЬНЫ
    const dial3Extensions_2_13 = [step1Extensions_2_13[0], mobileManager_2_13, step2Extensions_2_13[0]];
    
    // ВРЕМЕННАЯ ЗАГЛУШКА - показываем сформированные Extensions
    events.push({
        step: 1,
        event: 'start',
        description: `Паттерн 2-13: Extensions сформированы`,
        data: { 
            phone: phone_2_13,
            trunk: trunk_2_13,
            answered_manager: answeredManager_2_13,
            dial1_extensions: dial1Extensions_2_13,
            dial2_extensions: dial2Extensions_2_13,
            dial3_extensions: dial3Extensions_2_13,
            note: "Extensions для 3 DIAL событий готовы"
        }
    });
    break;
```

## ПЛАН ДОРАБОТКИ 2-13

**ЧТО НУЖНО ДОБАВИТЬ В 2-13:**

1. ✅ Данные из формы - ГОТОВО
2. ✅ Формирование Extensions - ГОТОВО  
3. ❌ Генерация UniqueId для всех событий
4. ❌ Генерация bridgeId
5. ❌ currentDelay и все 19 событий
6. ❌ Заменить заглушку на реальные события

**ПРЕДПОЛАГАЕМЫЙ КОД 2-13 (ПОЛНЫЙ):**

```javascript
case 24:
    // ======= ПАТТЕРН 2-13: РЕГЛАМЕНТИРОВАННЫЕ 3 DIAL + МОБИЛЬНЫЕ СОБЫТИЯ (19 событий) =======
    
    // БЕРЕМ ВСЕ ДАННЫЕ ИЗ ФОРМЫ (как в 2-12)
    const phone_2_13 = (formData.get('external_phone') || '375447034448').replace(/^\+/, '');
    const trunk_2_13 = formData.get('line_id') || '0001363';
    const answeredManager_2_13 = formData.get('answered_manager_final') || '151';
    const mobileManager_2_13 = formData.get('step3_mobile_manager') || '375296254070';
    const mobileLine_2_13 = formData.get('step3_mobile_line') || '0001366';
    
    // Собираем группы менеджеров для каждого шага ИЗ ФОРМЫ (как в 2-12)
    const step1Extensions_2_13 = [
        formData.get('step1_manager_1'),
        formData.get('step1_manager_2'),
        formData.get('step1_manager_3'),
        formData.get('step1_manager_4')
    ].filter(value => value && value !== '');
    
    const step2Extensions_2_13 = [
        formData.get('step2_manager_1'),
        formData.get('step2_manager_2'),
        formData.get('step2_manager_3'),
        formData.get('step2_manager_4')
    ].filter(value => value && value !== '');
    
    // ФИКСИРОВАННЫЕ 3 DIAL события согласно DB файлу:
    const dial1Extensions_2_13 = [step1Extensions_2_13[0]];
    const dial2Extensions_2_13 = [step1Extensions_2_13[0], step2Extensions_2_13[0]];
    const dial3Extensions_2_13 = [step1Extensions_2_13[0], mobileManager_2_13, step2Extensions_2_13[0]];
    
    // Генерируем UniqueId для каждого события на основе DB образца
    const uniqueIds_2_13 = {
        start: generateUniqueId("1757774605.260"),
        new_callerid_main: generateUniqueId("1757774605.260"),
        dial1: generateUniqueId("1757774605.260"),
        dial2: generateUniqueId("1757774605.260"),
        dial3: generateUniqueId("1757774605.260"),
        new_callerid_local1: generateUniqueId("1757774635.265"),
        new_callerid_local2: generateUniqueId("1757774635.266"),
        dial_mobile1: generateUniqueId("1757774635.266"),
        new_callerid_mobile1: generateUniqueId("1757774635.268"),
        dial_mobile2: generateUniqueId("1757774635.266"),
        new_callerid_mobile2: generateUniqueId("1757774635.269"),
        bridge_create: generateUniqueId(""),
        bridge_external: generateUniqueId("1757774605.260"),
        hangup_mobile: generateUniqueId("1757774635.266"),
        bridge_manager: generateUniqueId("1757774635.267"),
        bridge_leave_manager: generateUniqueId("1757774635.267"),
        bridge_leave_external: generateUniqueId("1757774605.260"),
        bridge_destroy: generateUniqueId(""),
        hangup_main: generateUniqueId("1757774605.260")
    };
    
    const bridgeId_2_13 = generateBridgeUUID();
    let currentDelay = 0;
    
    // ВСЕ 19 СОБЫТИЙ...
    
    break;
```

**ПРОБЛЕМА:** Когда я добавляю даже простую строку `let currentDelay = 0;`, форма ломается! 

**ВОЗМОЖНЫЕ ПРИЧИНЫ:**
1. Синтаксическая ошибка в JavaScript
2. Неправильное место вставки
3. Конфликт имён переменных
4. Проблема с областью видимости

**НУЖНО НАЙТИ:** Где именно происходит ошибка!
