document.addEventListener('click', function(e) {
    const btn = e.target.closest('button');
    if (btn) {
        fetch('/admin/log-action', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                action: 'Кнопка: ' + (btn.textContent || btn.value || btn.id || 'без текста'),
                ts: new Date().toISOString()
            })
        });
    }
}); 