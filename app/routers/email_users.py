<!-- app/templates/email_users.html -->
<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <title>Пользователи по e-mail</title>
  <style>
    body { font-family: sans-serif; padding: 1rem; }
    table { border-collapse: collapse; width: 100%; margin-top: 1rem; }
    th, td { border: 1px solid #ccc; padding: 0.5rem; text-align: left; }
    th { background: #f0f0f0; }
    .danger { color: #dc3545; text-decoration: none; }
  </style>
</head>
<body>
  <h1>Пользователи по e-mail</h1>

  <form action="/admin/email-users/upload" method="post" enctype="multipart/form-data">
    <input type="file" name="file" accept=".csv" required>
    <button type="submit">Загрузить CSV</button>
  </form>

  <table>
    <thead>
      <tr>
        <th>ID Telegram</th>
        <th>e-mail</th>
        <th>Имя</th>
        <th>Все права</th>
        <th>Право 1</th>
        <th>Право 2</th>
        <th>Unit</th>
        <th>Удалить</th>
      </tr>
    </thead>
    <tbody>
      {% for u in email_users %}
        <tr>
          <td>{{ u.tg_id or "" }}</td>
          <td>{{ u.email }}</td>
          <td>{{ u.name or "" }}</td>
          <td>{{ u.right_all }}</td>
          <td>{{ u.right_1 }}</td>
          <td>{{ u.right_2 }}</td>
          <td>{{ u.enterprise_name or "" }}</td>
          <td>
            {% if u.tg_id %}
              <form action="/admin/email-users/delete/{{ u.tg_id }}" method="post" style="display:inline">
                <button type="submit" class="danger">Delete</button>
              </form>
            {% endif %}
          </td>
        </tr>
      {% endfor %}
    </tbody>
  </table>

  <p><a href="/admin/dashboard">← Назад на дашборд</a></p>
</body>
</html>
