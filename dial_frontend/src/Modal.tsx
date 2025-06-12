import React, { useState, useEffect, useCallback, useRef, MouseEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { Node, Edge } from 'reactflow';
import SchemaEditor from './SchemaEditor';
import './Modal.css';

// Определяем типы, которые будут использоваться во всем приложении
export interface SchemaData {
    nodes: Node[];
    edges: Edge[];
    viewport: { x: number; y: number; zoom: number };
}

export interface Schema {
    schema_id: string;
    enterprise_id: string;
    schema_name: string;
    schema_data: SchemaData;
    created_at: string;
}

const getEnterpriseIdFromUrl = (): string | null => {
    const queryParams = new URLSearchParams(window.location.search);
    return queryParams.get('enterprise');
};

const Modal: React.FC = () => {
    const [currentView, setCurrentView] = useState<'list' | 'editor'>('list');
    const [schemas, setSchemas] = useState<Schema[]>([]);
    const [selectedSchema, setSelectedSchema] = useState<Schema | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const navigate = useNavigate();
    const modalContentRef = useRef<HTMLDivElement>(null);

    const enterpriseId = getEnterpriseIdFromUrl();
    const backgroundUrl = document.referrer;

    useEffect(() => {
        if (enterpriseId) {
            fetch(`/dial/api/enterprises/${enterpriseId}/schemas`)
                .then(res => {
                    if (!res.ok) {
                        throw new Error('Failed to fetch schemas');
                    }
                    return res.json();
                })
                .then((data: Schema[]) => {
                    setSchemas(data);
                    setIsLoading(false);
                })
                .catch(err => {
                    console.error("Error fetching schemas:", err);
                    setError('Не удалось загрузить схемы.');
                    setIsLoading(false);
                });
        } else {
            setError("ID предприятия не найдено в URL.");
            setIsLoading(false);
        }
    }, [enterpriseId]);

    const handleEditSchema = (schema: Schema) => {
        setSelectedSchema(schema);
        setCurrentView('editor');
    };

    const handleAddNewSchema = () => {
        if (!enterpriseId) {
            alert("Ошибка: ID предприятия не найден.");
            return;
        }

        const existingSchemaNumbers = new Set(
            schemas.map(s => {
                const match = s.schema_name.match(/^Входящая схема (\d+)$/);
                return match ? parseInt(match[1], 10) : 0;
            })
        );

        let newSchemaNumber = 1;
        while (existingSchemaNumbers.has(newSchemaNumber)) {
            newSchemaNumber++;
        }
        
        const newSchemaName = `Входящая схема ${newSchemaNumber}`;
        
        // Дополнительная проверка на уникальность, на всякий случай
        if (schemas.some(s => s.schema_name === newSchemaName)) {
             alert(`Ошибка: Имя схемы "${newSchemaName}" уже существует. Пожалуйста, создайте схему с другим именем.`);
             return;
        }

        const defaultNode: Node = {
            id: '1',
            type: 'custom',
            position: { x: 600, y: 30 },
            data: { label: 'Поступил новый звонок' },
            draggable: false,
            deletable: false,
        };
        
        const newSchemaTemplate: Omit<Schema, 'schema_id' | 'created_at'> & { schema_id?: string } = {
            enterprise_id: enterpriseId,
            schema_name: newSchemaName,
            schema_data: {
                nodes: [defaultNode],
                edges: [],
                viewport: { x: 0, y: 0, zoom: 1 },
            }
        };

        setSelectedSchema(newSchemaTemplate as Schema);
        setCurrentView('editor');
    };

    const handleSaveSchema = async (schemaToSave: Partial<Schema>): Promise<Schema> => {
        if (!enterpriseId) {
            alert("ID предприятия не найдено. Невозможно сохранить.");
            throw new Error("Enterprise ID not found");
        }
        
        const isNewSchema = !('schema_id' in schemaToSave) || !schemaToSave.schema_id;
        const url = isNewSchema 
            ? `/dial/api/enterprises/${enterpriseId}/schemas`
            : `/dial/api/enterprises/${enterpriseId}/schemas/${schemaToSave.schema_id}`;
        const method = isNewSchema ? 'POST' : 'PUT';
        
        const response = await fetch(url, {
            method: method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(schemaToSave), // Отправляем объект как есть
        });

        if (!response.ok) {
            const errorData = await response.json().catch(() => ({ detail: 'Не удалось прочитать ошибку' }));
            throw new Error(errorData.detail || 'Ошибка при сохранении схемы');
        }

        const savedSchema = await response.json();
        
        // Обновляем состояние списка схем
        if (isNewSchema) {
            setSchemas(prev => [...prev, savedSchema]);
        } else {
            setSchemas(schemas.map(s => s.schema_id === savedSchema.schema_id ? savedSchema : s));
        }

        // Возвращаем сохраненную схему, чтобы SchemaEditor мог продолжить работу
        return savedSchema;
    };

    // Вызывается из SchemaEditor для возврата к списку
    const handleBackToList = useCallback(() => {
        setCurrentView('list');
        setSelectedSchema(null);
    }, []);

    const handleDeleteSchema = async (schemaId: string) => {
        if (!enterpriseId || !schemaId) return;

        if (window.confirm("Вы уверены, что хотите удалить эту схему?")) {
            try {
                const response = await fetch(`/dial/api/enterprises/${enterpriseId}/schemas/${schemaId}`, {
                    method: 'DELETE',
                });

                if (!response.ok) {
                    if (response.status === 409) { // Conflict
                        const errorData = await response.json();
                        alert(errorData.detail); // Показываем сообщение с бэкенда
                        return; // Остаемся в редакторе
                    }
                    throw new Error('Не удалось удалить схему');
                }
                
                // Успешное удаление
                setSchemas(schemas.filter(s => s.schema_id !== schemaId));
                setCurrentView('list');
                setSelectedSchema(null);

            } catch (error) {
                console.error("Ошибка при удалении:", error);
                alert(`Произошла ошибка при удалении схемы: ${error instanceof Error ? error.message : 'Неизвестная ошибка'}`);
            }
        }
    };
    
    const handleClickOutside = useCallback((event: MouseEvent<HTMLDivElement>) => {
        // Проверяем, что клик был именно по оверлею, а не по его дочерним элементам (самому модальному окну)
        if (event.target === event.currentTarget) {
             if (currentView === 'list') {
                navigate(-1);
            } else if (currentView === 'editor') {
                handleBackToList();
            }
        }
    }, [navigate, currentView, handleBackToList]);

    const renderListView = () => {
        const sortedSchemas = [...schemas].sort((a, b) => {
            const regex = /^(.*?)\s*(\d+)$/;
            const matchA = a.schema_name.match(regex);
            const matchB = b.schema_name.match(regex);

            if (matchA && matchB) {
                const nameA = matchA[1].trim();
                const numA = parseInt(matchA[2], 10);
                const nameB = matchB[1].trim();
                const numB = parseInt(matchB[2], 10);

                if (nameA === nameB) {
                    return numA - numB; // Сортировка по номеру, от меньшего к большему
                }
            }
            // Для всех остальных случаев - стандартная сортировка по имени
            return a.schema_name.localeCompare(b.schema_name);
        });

        return (
            <>
                <div className="modal-header">
                    <h2>Схемы для предприятия: {enterpriseId}</h2>
                    <button onClick={() => navigate(-1)} className="close-button">&times;</button>
                </div>
                <ul className="schema-list">
                    {isLoading && <p>Загрузка...</p>}
                    {error && <p className="error">{error}</p>}
                    {!isLoading && !error && sortedSchemas.map(schema => (
                        <li key={schema.schema_id} className="schema-item">
                            <span>{schema.schema_name}</span>
                            <button onClick={() => handleEditSchema(schema)}>Редактировать</button>
                        </li>
                    ))}
                </ul>
                <button className="add-schema-button" onClick={handleAddNewSchema}>
                    Добавить новую схему
                </button>
            </>
        );
    };

    const renderEditorView = () => {
      if (!selectedSchema || !enterpriseId) return null;
      return (
          <SchemaEditor
              enterpriseId={enterpriseId}
              schema={selectedSchema}
              onSave={handleSaveSchema}
              onCancel={handleBackToList}
              onDelete={handleDeleteSchema}
          />
      );
    };

    return (
        <>
            {backgroundUrl && <iframe src={backgroundUrl} className="background-iframe" title="background"></iframe>}
            <div className="modal-overlay" onClick={handleClickOutside}>
                <div
                    className={`modal-content ${currentView === 'editor' ? 'editor-view' : ''}`}
                    ref={modalContentRef}
                    onClick={(e) => e.stopPropagation()}
                >
                    {currentView === 'list' ? renderListView() : renderEditorView()}
                </div>
            </div>
        </>
    );
};

export default Modal;