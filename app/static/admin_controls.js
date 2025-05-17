// app/static/admin_controls.js

document.addEventListener("DOMContentLoaded", function () {
  // Кнопки управления сервисом и ботами
  const serviceStartBtn = document.getElementById("service-start");
  const serviceStopBtn = document.getElementById("service-stop");
  const botsStartBtn = document.getElementById("bots-start");
  const botsStopBtn = document.getElementById("bots-stop");

  function sendControlRequest(url, action) {
    fetch(url, { method: "POST" })
      .then(response => {
        if (!response.ok) throw new Error(`Failed to ${action}`);
        alert(`${action} успешно`);
      })
      .catch(() => alert(`Ошибка при попытке ${action}`));
  }

  if (serviceStartBtn) {
    serviceStartBtn.addEventListener("click", () => {
      sendControlRequest("/admin/service/start", "запустить сервис");
    });
  }
  if (serviceStopBtn) {
    serviceStopBtn.addEventListener("click", () => {
      sendControlRequest("/admin/service/stop", "остановить сервис");
    });
  }
  if (botsStartBtn) {
    botsStartBtn.addEventListener("click", () => {
      sendControlRequest("/admin/bots/start", "запустить всех ботов");
    });
  }
  if (botsStopBtn) {
    botsStopBtn.addEventListener("click", () => {
      sendControlRequest("/admin/bots/stop", "остановить всех ботов");
    });
  }
});
