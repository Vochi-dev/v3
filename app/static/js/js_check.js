document.addEventListener('DOMContentLoaded', function() {
    var banner = document.getElementById('js-check-banner');
    if (banner) banner.style.display = 'none';
    var msg = document.createElement('div');
    msg.style = 'position:fixed;bottom:0;left:0;width:100vw;background:#090;color:#fff;padding:10px 0;text-align:center;z-index:9999;font-size:1.2em;';
    msg.textContent = 'JS успешно исполнился!';
    document.body.appendChild(msg);
    setTimeout(() => { msg.remove(); }, 5000);
    fetch('/admin/log-action', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'EXTERNAL_JS: JS успешно загружен и исполнился', ts: new Date().toISOString() })
    });
}); 