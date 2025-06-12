import React, { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import './Modal.css';
import SchemaEditor from './SchemaEditor';

interface Schema {
  id: string;
  name: string;
}

const Modal: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();

  const [view, setView] = useState<'list' | 'editor'>('list');
  const [currentSchemaId, setCurrentSchemaId] = useState<string | null>(null);

  const [schemas, setSchemas] = useState<Schema[]>([]);
  const [enterpriseId, setEnterpriseId] = useState<string | null>(null);
  const [previousUrl, setPreviousUrl] = useState<string | null>(null);

  useEffect(() => {
    // Получаем ID предприятия из query-параметра ?enterprise=...
    const queryParams = new URLSearchParams(location.search);
    const id = queryParams.get('enterprise');
    setEnterpriseId(id);

    // Устанавливаем URL для фона
    const referrer = document.referrer;
    if (referrer && new URL(referrer).hostname === window.location.hostname) {
      setPreviousUrl(referrer);
    } else {
      setPreviousUrl('/');
    }

    // Загружаем схемы, если ID предприятия известен
    if (id) {
      fetch(`/dial/api/enterprises/${id}/schemas`)
        .then(res => {
          if (!res.ok) {
            throw new Error('Network response was not ok');
          }
          return res.json();
        })
        .then(data => setSchemas(data))
        .catch(error => console.error("Failed to fetch schemas:", error));
    }
  }, [location.search]);

  const handleEditSchema = (schemaId: string) => {
    setCurrentSchemaId(schemaId);
    setView('editor');
  };

  const handleBackToList = () => {
    setCurrentSchemaId(null);
    setView('list');
    // Refresh list after potential changes
    if (enterpriseId) {
        fetch(`/dial/api/enterprises/${enterpriseId}/schemas`)
            .then(res => res.json())
            .then(data => setSchemas(data));
    }
  };

  const handleAddSchema = () => {
    if (!enterpriseId) return;

    // 1. Определяем уникальное имя для новой схемы
    const existingNames = new Set(schemas.map(s => s.name));
    let newSchemaName = '';
    let counter = 1;
    while (true) {
        const candidateName = `Входящая схема ${counter}`;
        if (!existingNames.has(candidateName)) {
            newSchemaName = candidateName;
            break;
        }
        counter++;
    }

    // 2. Создаем узел по умолчанию
    const defaultNode = {
        id: '1',
        type: 'custom',
        position: { x: 450, y: 100 }, // Центрируем наверху
        data: { label: 'Поступил новый звонок' },
        draggable: false,
        deletable: false,
    };

    // 3. Формируем тело запроса
    const newSchemaPayload = {
        name: newSchemaName,
        nodes: [defaultNode],
        edges: [],
    };

    // 4. Отправляем на сервер для создания
    fetch(`/dial/api/enterprises/${enterpriseId}/schemas`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(newSchemaPayload),
    })
    .then(res => res.json())
    .then((createdSchema: Schema) => {
        // 5. Обновляем локальный список и переключаемся в редактор
        setSchemas(prevSchemas => [...prevSchemas, createdSchema]);
        handleEditSchema(createdSchema.id);
    })
    .catch(error => console.error("Failed to create new schema:", error));
  };

  if (!previousUrl) {
    return null;
  }
  
  const renderListView = () => (
    <div className="modal-content" onClick={(e) => e.stopPropagation()}>
      <div className="modal-header">
        <h2>Схемы для предприятия: {enterpriseId}</h2>
        <button onClick={() => navigate(-1)} className="modal-close-button">&times;</button>
      </div>
      <div className="modal-body">
        <ul className="schema-list">
          {schemas.length > 0 ? (
            schemas.map(schema => (
              <li key={schema.id} className="schema-item">
                <span>{schema.name}</span>
                <button className="edit-button" onClick={() => handleEditSchema(schema.id)}>
                  Редактировать
                </button>
              </li>
            ))
          ) : (
            <p>Схем для этого предприятия пока нет.</p>
          )}
        </ul>
        <button className="add-schema-button" onClick={handleAddSchema}>
          Добавить новую схему
        </button>
      </div>
    </div>
  );

  return (
    <div className="overlay-container">
      <iframe src={previousUrl} className="overlay-iframe" title="background" />
      <div 
        className="modal-overlay" 
        onClick={() => {
          if (view === 'editor') {
            handleBackToList();
          } else {
            navigate(-1);
          }
        }}
      >
        {view === 'list' && renderListView()}
        {view === 'editor' && enterpriseId && currentSchemaId && (
          <div onClick={(e) => e.stopPropagation()}>
            <SchemaEditor
              enterpriseId={enterpriseId}
              schemaId={currentSchemaId}
              onClose={handleBackToList}
            />
          </div>
        )}
      </div>
    </div>
  );
};

export default Modal; 