import React, { useEffect, useState } from 'react';
import GenericModal from './GenericModal'; // Используем наш новый универсальный компонент
import './SchemaEditor.css'; // Используем стили от главного редактора для единообразия

interface OutgoingSchemasModalProps {
    enterpriseId: string;
    enterpriseName: string;
    onClose: () => void;
}

const OutgoingSchemasModal: React.FC<OutgoingSchemasModalProps> = ({ enterpriseId, enterpriseName, onClose }) => {
    const [schemas, setSchemas] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        const fetchSchemas = async () => {
            try {
                setLoading(true);
                // !!! ВАЖНО: Этот эндпоинт пока не существует. Его нужно будет создать.
                const response = await fetch(`/api/enterprise/${enterpriseId}/outgoing-schemas`);
                if (!response.ok) {
                    throw new Error(`Ошибка при загрузке схем: ${response.statusText}`);
                }
                const data = await response.json();
                setSchemas(data);
            } catch (e) {
                if (e instanceof Error) {
                    setError(e.message);
                } else {
                    setError("Произошла неизвестная ошибка");
                }
                // Временный MOCK, чтобы можно было видеть окно
                console.warn("Используются временные данные, т.к. эндпоинт не готов.");
                setSchemas([
                    { id: 1, name: 'Схема "Холодный обзвон"' },
                    { id: 2, name: 'Схема "Опрос клиентов"' },
                ]);
            } finally {
                setLoading(false);
            }
        };

        fetchSchemas();
    }, [enterpriseId]);

    const handleEditSchema = (schemaId: number) => {
        // Переход на страницу редактирования конкретной схемы
        window.location.href = `/dial_outgoing/?enterprise=${enterpriseId}&schema=${schemaId}`;
    };

    const handleCreateSchema = () => {
        // Переход на страницу создания новой схемы
        window.location.href = `/dial_outgoing/?enterprise=${enterpriseId}`;
    };

    return (
        <GenericModal title={`Исходящие схемы для "${enterpriseName}"`} onClose={onClose}>
            <div className="schema-list-container">
                {loading && <p>Загрузка...</p>}
                {error && <p style={{ color: 'red' }}>{error}</p>}
                {!loading && (
                    <>
                        <ul className="schema-list">
                            {schemas.map((schema: any) => (
                                <li key={schema.id} className="schema-list-item" onClick={() => handleEditSchema(schema.id)}>
                                    {schema.name}
                                </li>
                            ))}
                        </ul>
                        <button onClick={handleCreateSchema} className="btn-add-schema">
                            Создать новую схему
                        </button>
                    </>
                )}
            </div>
        </GenericModal>
    );
};

export default OutgoingSchemasModal; 