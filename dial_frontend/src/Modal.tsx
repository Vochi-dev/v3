import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { Node, Edge } from 'reactflow';
import SchemaEditor from './SchemaEditor';
import './Modal.css';

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
    schema_type: 'incoming' | 'outgoing';
}

const getEnterpriseIdFromUrl = (): string | null => {
    const queryParams = new URLSearchParams(window.location.search);
    return queryParams.get('enterprise');
};

const getSchemaTypeFromUrl = (): 'incoming' | 'outgoing' => {
    const queryParams = new URLSearchParams(window.location.search);
    const type = queryParams.get('type');
    return type === 'outgoing' ? 'outgoing' : 'incoming';
};

const Modal: React.FC = () => {
    const [currentView, setCurrentView] = useState<'list' | 'editor'>('list');
    const [schemas, setSchemas] = useState<Schema[]>([]);
    const [allLines, setAllLines] = useState<any[]>([]);
    const [selectedSchema, setSelectedSchema] = useState<Partial<Schema> | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const navigate = useNavigate();
    const modalContentRef = useRef<HTMLDivElement>(null);

    const enterpriseId = getEnterpriseIdFromUrl();
    const schemaType = getSchemaTypeFromUrl();
    const backgroundUrl = document.referrer;

    const fetchData = useCallback(() => {
        if (enterpriseId) {
            setIsLoading(true);
            Promise.all([
                fetch(`/dial/api/enterprises/${enterpriseId}/schemas`).then(res => res.json()),
                fetch(`/dial/api/enterprises/${enterpriseId}/lines`).then(res => res.json())
            ]).then(([schemasData, linesData]) => {
                setSchemas(schemasData);
                setAllLines(linesData);
            }).catch(err => {
                console.error("Error fetching data:", err);
                setError('Не удалось загрузить данные.');
            }).finally(() => {
                setIsLoading(false);
            });
        } else {
            setError("ID предприятия не найдено в URL.");
            setIsLoading(false);
        }
    }, [enterpriseId]);

    useEffect(() => {
        fetchData();
    }, [fetchData]);

    const handleEditSchema = (schema: Schema) => {
        setSelectedSchema(schema);
        setCurrentView('editor');
    };

    const handleAddNewSchema = () => {
        if (!enterpriseId) {
            alert("Ошибка: ID предприятия не найден.");
            return;
        }

        const isOutgoing = schemaType === 'outgoing';
        const schemaPrefix = isOutgoing ? 'Исходящая схема' : 'Входящая схема';
        const regex = new RegExp(`^${schemaPrefix} (\\d+)$`);

        const existingSchemaNumbers = new Set(
            schemas
                .filter(s => s.schema_type === schemaType)
                .map(s => {
                    const match = s.schema_name.match(regex);
                    return match ? parseInt(match[1], 10) : 0;
                })
        );

        let newSchemaNumber = 1;
        while (existingSchemaNumbers.has(newSchemaNumber)) {
            newSchemaNumber++;
        }
        
        const newSchemaName = `${schemaPrefix} ${newSchemaNumber}`;

        const defaultNodes: Node[] = isOutgoing ? [{
            id: 'start-outgoing',
            type: 'outgoing-call',
            position: { x: 600, y: 30 },
            data: { label: 'Исходящий звонок' },
            draggable: false,
            deletable: false,
        }] : [{
            id: '1',
            type: 'custom', // ВОЗВРАЩАЮ ИСХОДНЫЙ ТИП
            position: { x: 600, y: 30 },
            data: { label: 'Поступил новый звонок' },
            draggable: false,
            deletable: false,
        }];
        
        const newSchemaTemplate: Partial<Schema> = {
            enterprise_id: enterpriseId,
            schema_name: newSchemaName,
            schema_type: schemaType,
            schema_data: {
                nodes: defaultNodes,
                edges: [],
                viewport: { x: 0, y: 0, zoom: 1 },
            }
        };

        setSelectedSchema(newSchemaTemplate);
        setCurrentView('editor');
    };

    const handleSaveSchema = async (schemaToSave: Partial<Schema>): Promise<Schema> => {
        if (!enterpriseId) {
            throw new Error("Enterprise ID not found");
        }
        
        const isNewSchema = !schemaToSave.schema_id;
        const url = isNewSchema 
            ? `/dial/api/enterprises/${enterpriseId}/schemas`
            : `/dial/api/enterprises/${enterpriseId}/schemas/${schemaToSave.schema_id}`;
        const method = isNewSchema ? 'POST' : 'PUT';
        
        const response = await fetch(url, {
            method: method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(schemaToSave),
        });

        if (!response.ok) {
            const errorData = await response.json().catch(() => ({ detail: 'Не удалось прочитать ошибку' }));
            throw new Error(errorData.detail || 'Ошибка при сохранении схемы');
        }

        const savedSchema = await response.json();
        
        const updatedSchemas = isNewSchema
            ? [...schemas, savedSchema]
            : schemas.map(s => s.schema_id === savedSchema.schema_id ? savedSchema : s);

        setSchemas(updatedSchemas);
        setSelectedSchema(savedSchema);
        return savedSchema;
    };

    const handleBackToList = useCallback(() => {
        fetchData();
        setCurrentView('list');
        setSelectedSchema(null);
    }, [fetchData]);

    const handleDeleteSchema = async (schemaId: string) => {
        if (!enterpriseId || !schemaId) return;

        if (window.confirm("Вы уверены, что хотите удалить эту схему?")) {
            try {
                const response = await fetch(`/dial/api/enterprises/${enterpriseId}/schemas/${schemaId}`, {
                    method: 'DELETE',
                });

                if (!response.ok) {
                    if (response.status === 409) {
                        const errorData = await response.json();
                        alert(errorData.detail);
                        return;
                    }
                    throw new Error('Не удалось удалить схему');
                }
                
                setSchemas(schemas.filter(s => s.schema_id !== schemaId));
                setCurrentView('list');
                setSelectedSchema(null);

            } catch (error) {
                console.error("Ошибка при удалении:", error);
                alert(`Произошла ошибка при удалении схемы: ${error instanceof Error ? error.message : 'Неизвестная ошибка'}`);
            }
        }
    };
    
    const handleClickOutside = useCallback((event: React.MouseEvent<HTMLDivElement>) => {
        if (event.target === event.currentTarget) {
             if (currentView === 'list') {
                navigate(-1);
            } else if (currentView === 'editor') {
                handleBackToList();
            }
        }
    }, [navigate, currentView, handleBackToList]);

    const renderListView = () => {
        const filteredSchemas = schemas.filter(s => s.schema_type === schemaType);

        const sortedSchemas = [...filteredSchemas].sort((a, b) => {
            const regex = /^(.*?)\s*(\d+)$/;
            const matchA = a.schema_name.match(regex);
            const matchB = b.schema_name.match(regex);

            if (matchA && matchB) {
                const nameA = matchA[1].trim();
                const numA = parseInt(matchA[2], 10);
                const nameB = matchB[1].trim();
                const numB = parseInt(matchB[2], 10);

                if (nameA === nameB) {
                    return numA - numB;
                }
            }
            return a.schema_name.localeCompare(b.schema_name);
        });

        return (
            <>
                {isLoading && <p>Загрузка...</p>}
                {error && <p className="error">{error}</p>}
                <ul className="schema-list">
                    {!isLoading && !error && sortedSchemas.map(schema => {
                        const assignedLines = allLines.filter(line => line.in_schema === schema.schema_name);

                        const outgoingNode = schema.schema_type === 'outgoing' 
                            ? schema.schema_data?.nodes.find(n => n.id === 'start-outgoing') 
                            : null;
                        const assignedPhonesDetails = outgoingNode?.data?.phones_details || [];

                        return (
                            <li key={schema.schema_id} className="schema-item">
                                <div className="schema-info" onClick={() => handleEditSchema(schema)}>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                                        <span style={{ fontWeight: 700 }}>{schema.schema_name}</span>
                                        {schema.schema_type === 'incoming' && (schema.schema_data as any)?.smartRedirect && (
                                            <span style={{ color: '#10b981', fontWeight: 700 }}>
                                                Умная переадресация
                                            </span>
                                        )}
                                    </div>
                                    {assignedLines.length > 0 && schema.schema_type === 'incoming' && (
                                        <div className="assigned-lines-list">
                                            {assignedLines.map(line => (
                                                <div key={line.id} className="assigned-line-item">
                                                    {line.display_name}
                                                </div>
                                            ))}
                                        </div>
                                    )}
                                    {assignedPhonesDetails.length > 0 && schema.schema_type === 'outgoing' && (
                                        <div className="assigned-lines-list">
                                            {assignedPhonesDetails.map((phoneDetail: { phone_number: string; full_name: string }) => (
                                                <div key={phoneDetail.phone_number} className="assigned-line-item">
                                                    {`${phoneDetail.phone_number} - ${phoneDetail.full_name || 'Не назначен'}`}
                                                </div>
                                            ))}
                                        </div>
                                    )}
                                </div>
                                <button onClick={(e) => { e.stopPropagation(); handleEditSchema(schema as Schema); }}>Редактировать</button>
                            </li>
                        );
                    })}
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
                schemaType={schemaType}
            />
        );
    };

    const viewTitle = schemaType === 'outgoing' 
        ? `Исходящие схемы`
        : `Входящие схемы`;

    return (
        <>
            {backgroundUrl && <iframe src={backgroundUrl} className="background-iframe" title="background"></iframe>}
            <div className="modal-overlay" onClick={handleClickOutside}>
                <div
                    className={`modal-content ${currentView === 'editor' ? 'editor-view' : ''}`}
                    ref={modalContentRef}
                    onClick={(e) => e.stopPropagation()}
                >
                    {currentView === 'list' ? (
                         <>
                            <div className="modal-header">
                                <h2>{viewTitle}</h2>
                                <button onClick={() => navigate(-1)} className="close-button">&times;</button>
                            </div>
                            {renderListView()}
                        </>
                    ) : (
                        renderEditorView()
                    )}
                </div>
            </div>
        </>
    );
};

export default Modal;