// Инициализация Telegram WebApp
window.Telegram.WebApp.ready();

// Конфигурация  
const API_BASE_URL = 'https://bot.vochi.by/miniapp';
let currentUser = null;
let currentEnterprise = null;
let callsData = [];
let clientsData = [];

// Инициализация приложения
document.addEventListener('DOMContentLoaded', function() {
    initTelegramWebApp();
    initUI();
    loadInitialData();
});

/**
 * Инициализация Telegram WebApp
 */
function initTelegramWebApp() {
    const tg = window.Telegram.WebApp;
    
    // Получаем данные пользователя
    const initData = tg.initDataUnsafe;
    
    if (initData.user) {
        currentUser = {
            id: initData.user.id,
            first_name: initData.user.first_name,
            last_name: initData.user.last_name,
            username: initData.user.username
        };
        
        // Отображаем имя пользователя
        document.getElementById('user-name').textContent = 
            `${currentUser.first_name} ${currentUser.last_name || ''}`.trim();
    } else {
        console.warn('Telegram WebApp user data not available');
        // Fallback для тестирования
        currentUser = { id: 374573193, first_name: 'Test', last_name: 'User' };
        document.getElementById('user-name').textContent = 'Test User';
    }
    
    // Настраиваем тему
    tg.setHeaderColor('secondary_bg_color');
    tg.expand();
    
    console.log('Telegram WebApp initialized:', currentUser);
}

/**
 * Инициализация UI
 */
function initUI() {
    // Устанавливаем сегодняшнюю дату в фильтр
    const today = new Date().toISOString().split('T')[0];
    document.getElementById('filter-date').value = today;
    
    // Навигация по умолчанию
    showCalls();
}

/**
 * Загрузка начальных данных
 */
async function loadInitialData() {
    try {
        await Promise.all([
            loadEnterpriseInfo(),
            loadCalls(),
            loadStats()
        ]);
    } catch (error) {
        console.error('Error loading initial data:', error);
        showError('Ошибка загрузки данных');
    }
}

/**
 * Загрузка информации о предприятии
 */
async function loadEnterpriseInfo() {
    try {
        const response = await fetch(`${API_BASE_URL}/enterprise-info`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tg_id: currentUser.id })
        });
        
        if (response.ok) {
            const data = await response.json();
            currentEnterprise = data.enterprise;
            document.getElementById('enterprise-name').textContent = 
                data.enterprise.name || `Предприятие ${data.enterprise.number}`;
        } else {
            document.getElementById('enterprise-name').textContent = 'Неизвестное предприятие';
        }
    } catch (error) {
        console.error('Error loading enterprise info:', error);
        document.getElementById('enterprise-name').textContent = 'Ошибка загрузки';
    }
}

/**
 * Загрузка списка звонков
 */
async function loadCalls() {
    try {
        showLoading('calls-list', 'Загружаем звонки...');
        
        const filterType = document.getElementById('filter-type').value;
        const filterDate = document.getElementById('filter-date').value;
        
        const response = await fetch(`${API_BASE_URL}/calls`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                tg_id: currentUser.id,
                filter_type: filterType,
                filter_date: filterDate,
                limit: 50
            })
        });
        
        if (response.ok) {
            const data = await response.json();
            callsData = data.calls;
            renderCalls();
        } else {
            showError('Ошибка загрузки звонков');
        }
    } catch (error) {
        console.error('Error loading calls:', error);
        showError('Ошибка сети при загрузке звонков');
    }
}

/**
 * Отображение списка звонков
 */
function renderCalls() {
    const container = document.getElementById('calls-list');
    
    if (callsData.length === 0) {
        container.innerHTML = '<div class="info-message">📞 Звонков не найдено</div>';
        return;
    }
    
    const html = callsData.map(call => `
        <div class="call-item" onclick="showCallDetails('${call.id}')">
            <div class="call-icon">
                ${getCallIcon(call.call_type, call.call_status)}
            </div>
            <div class="call-details">
                <div class="call-phone">${formatPhone(call.phone)}</div>
                <div class="call-meta">
                    <span>📅 ${formatDate(call.start_time)}</span>
                    <span>⏱️ ${formatDuration(call.duration)}</span>
                    <span>📍 ${call.extension || 'Неизвестно'}</span>
                </div>
            </div>
            <div class="call-actions">
                ${call.recording_url ? 
                    `<button class="play-btn" onclick="playRecording(event, '${call.recording_url}', '${call.phone}', '${call.start_time}', '${call.duration}')">🎵</button>` : 
                    `<button class="play-btn" disabled>🚫</button>`
                }
            </div>
        </div>
    `).join('');
    
    container.innerHTML = html;
}

/**
 * Загрузка статистики
 */
async function loadStats() {
    try {
        const response = await fetch(`${API_BASE_URL}/stats`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tg_id: currentUser.id })
        });
        
        if (response.ok) {
            const data = await response.json();
            document.getElementById('total-calls').textContent = data.total_calls || 0;
            document.getElementById('successful-calls').textContent = data.successful_calls || 0;
            document.getElementById('avg-duration').textContent = data.avg_duration || '0:00';
        }
    } catch (error) {
        console.error('Error loading stats:', error);
    }
}

/**
 * Поиск клиентов
 */
async function searchClients() {
    const query = document.getElementById('client-search').value.trim();
    const container = document.getElementById('clients-list');
    
    if (query.length < 3) {
        container.innerHTML = '<div class="info-message">🔍 Введите минимум 3 символа для поиска</div>';
        return;
    }
    
    try {
        showLoading('clients-list', 'Поиск клиентов...');
        
        const response = await fetch(`${API_BASE_URL}/clients/search`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                tg_id: currentUser.id,
                query: query
            })
        });
        
        if (response.ok) {
            const data = await response.json();
            renderClients(data.clients);
        } else {
            showError('Ошибка поиска клиентов');
        }
    } catch (error) {
        console.error('Error searching clients:', error);
        showError('Ошибка сети при поиске');
    }
}

/**
 * Отображение списка клиентов
 */
function renderClients(clients) {
    const container = document.getElementById('clients-list');
    
    if (clients.length === 0) {
        container.innerHTML = '<div class="info-message">👥 Клиенты не найдены</div>';
        return;
    }
    
    const html = clients.map(client => `
        <div class="client-item">
            <div class="client-name">${client.name || 'Без имени'}</div>
            <div class="client-phone">${formatPhone(client.phone)}</div>
            <div class="client-stats">
                <span>📞 ${client.calls_count || 0} звонков</span>
                <span>📅 Последний: ${client.last_call ? formatDate(client.last_call) : 'Никогда'}</span>
                <span>⏱️ Общее время: ${client.total_duration || '0:00'}</span>
            </div>
        </div>
    `).join('');
    
    container.innerHTML = html;
}

/**
 * Воспроизведение записи
 */
function playRecording(event, recordingUrl, phone, date, duration) {
    event.stopPropagation();
    
    // Заполняем модальное окно
    document.getElementById('audio-phone').textContent = formatPhone(phone);
    document.getElementById('audio-date').textContent = formatDate(date);
    document.getElementById('audio-duration').textContent = formatDuration(duration);
    
    // Настраиваем аудиоплеер
    const audioPlayer = document.getElementById('audio-player');
    audioPlayer.src = recordingUrl;
    
    // Показываем модальное окно
    document.getElementById('audio-modal').classList.add('active');
    
    // Автоматически начинаем воспроизведение
    audioPlayer.play().catch(error => {
        console.error('Error playing audio:', error);
        alert('Не удалось воспроизвести запись');
    });
}

/**
 * Закрытие модального окна
 */
function closeAudioModal() {
    const modal = document.getElementById('audio-modal');
    const audioPlayer = document.getElementById('audio-player');
    
    audioPlayer.pause();
    audioPlayer.src = '';
    modal.classList.remove('active');
}

/**
 * Навигация
 */
function showCalls() {
    setActiveSection('calls-section', 0);
    loadCalls();
}

function showClients() {
    setActiveSection('clients-section', 1);
}

function showStats() {
    setActiveSection('stats-section', 2);
    loadStats();
}

function setActiveSection(sectionId, navIndex) {
    // Скрываем все секции
    document.querySelectorAll('.section').forEach(section => {
        section.classList.remove('active');
    });
    
    // Показываем нужную секцию
    document.getElementById(sectionId).classList.add('active');
    
    // Обновляем навигацию
    document.querySelectorAll('.nav-btn').forEach((btn, index) => {
        btn.classList.toggle('active', index === navIndex);
    });
}

/**
 * Обновление данных
 */
function refreshCalls() {
    loadCalls();
}

function filterCalls() {
    loadCalls();
}

/**
 * Утилиты форматирования
 */
function formatPhone(phone) {
    if (!phone) return 'Неизвестный номер';
    const cleaned = phone.replace(/\D/g, '');
    if (cleaned.length === 12 && cleaned.startsWith('375')) {
        return `+375 ${cleaned.slice(3, 5)} ${cleaned.slice(5, 8)}-${cleaned.slice(8, 10)}-${cleaned.slice(10)}`;
    }
    return phone;
}

function formatDate(dateString) {
    if (!dateString) return 'Неизвестно';
    const date = new Date(dateString);
    return date.toLocaleString('ru-RU', {
        day: '2-digit',
        month: '2-digit',
        year: '2-digit',
        hour: '2-digit',
        minute: '2-digit'
    });
}

function formatDuration(seconds) {
    if (!seconds || seconds < 0) return '0:00';
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}:${secs.toString().padStart(2, '0')}`;
}

function getCallIcon(callType, callStatus) {
    if (callType === 1) { // Исходящий
        return callStatus === 2 ? '✅' : '❌'; // Успешный : Неуспешный
    } else { // Входящий
        return callStatus === 2 ? '📞' : '📵'; // Отвеченный : Пропущенный
    }
}

/**
 * Утилиты UI
 */
function showLoading(containerId, message = 'Загрузка...') {
    document.getElementById(containerId).innerHTML = 
        `<div class="loading">🔄 ${message}</div>`;
}

function showError(message) {
    console.error(message);
    // Можно добавить toast-уведомления
}

function showCallDetails(callId) {
    // TODO: Открыть детальную информацию о звонке
    console.log('Show call details for:', callId);
}

// Обработка закрытия модального окна по клику вне его
document.getElementById('audio-modal').addEventListener('click', function(e) {
    if (e.target === this) {
        closeAudioModal();
    }
});

// Обработка кнопки "Назад" Telegram
window.Telegram.WebApp.onEvent('backButtonClicked', function() {
    const modal = document.getElementById('audio-modal');
    if (modal.classList.contains('active')) {
        closeAudioModal();
    } else {
        window.Telegram.WebApp.close();
    }
});

console.log('Mini App script loaded successfully');