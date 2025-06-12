import React, { useState, useCallback, useEffect, useRef } from 'react';
import ReactFlow, {
    applyNodeChanges,
    applyEdgeChanges,
    addEdge,
    Node,
    Edge,
    OnNodesChange,
    OnEdgesChange,
    OnConnect,
    Connection,
    ReactFlowInstance,
    ReactFlowProvider
} from 'reactflow';
import 'reactflow/dist/style.css';
import './SchemaEditor.css';
import IncomingCallNode from './nodes/IncomingCallNode';
import { Schema, Line } from './types';
import IncomingCallModal from './IncomingCallModal';
import NodeActionModal from './NodeActionModal';
import DialModal from './DialModal';
import AddManagerModal from './AddManagerModal';

interface ManagerInfo {
    userId: number;
    name: string;
    phone: string;
}

interface SchemaEditorProps {
    enterpriseId: string;
    schema: Partial<Schema>;
    onSave: (schema: Partial<Schema>) => Promise<Schema>;
    onCancel: () => void;
    onDelete: (schemaId: string) => void;
}

const nodeTypes = {
    custom: IncomingCallNode,
};

const SchemaEditor: React.FC<SchemaEditorProps> = ({ enterpriseId, schema, onSave, onCancel, onDelete }) => {
    const [nodes, setNodes] = useState<Node[]>(schema.schema_data?.nodes || []);
    const [edges, setEdges] = useState<Edge[]>(schema.schema_data?.edges || []);
    const [schemaName, setSchemaName] = useState(schema.schema_name || 'Новая схема');
    const [isLinesModalOpen, setIsLinesModalOpen] = useState(false);
    const [isNodeActionModalOpen, setIsNodeActionModalOpen] = useState(false);
    const [isDialModalOpen, setIsDialModalOpen] = useState(false);
    const [isAddManagerModalOpen, setIsAddManagerModalOpen] = useState(false);
    const [dialManagers, setDialManagers] = useState<ManagerInfo[]>([]);
    
    const [selectedLines, setSelectedLines] = useState<Set<string>>(new Set());
    const [allLines, setAllLines] = useState<Line[]>([]);
    const [isLoading, setIsLoading] = useState(false);

    const reactFlowWrapper = useRef<HTMLDivElement>(null);
    const [reactFlowInstance, setReactFlowInstance] = useState<ReactFlowInstance | null>(null);

    useEffect(() => {
        if (schema.schema_id) {
            fetch(`/dial/api/enterprises/${enterpriseId}/lines`)
                .then(res => res.ok ? res.json() : Promise.reject(res))
                .then((data: Line[]) => {
                    setAllLines(data);
                    const normalizedSchemaName = schemaName.trim().toLowerCase();
                    const initiallySelected = new Set(
                        data.filter(line => line.in_schema && line.in_schema.trim().toLowerCase() === normalizedSchemaName).map(line => line.id)
                    );
                    setSelectedLines(initiallySelected);
                })
                .catch(err => console.error("Не удалось загрузить линии для схемы", err));
        }
    }, [schema.schema_id, schema.schema_name, enterpriseId, schemaName]);


    useEffect(() => {
        setNodes(schema.schema_data?.nodes || []);
        setEdges(schema.schema_data?.edges || []);
        setSchemaName(schema.schema_name || 'Новая схема');
        
        if (reactFlowInstance && schema.schema_data?.viewport) {
            const { x, y, zoom } = schema.schema_data.viewport;
            reactFlowInstance.setViewport({ x, y, zoom });
        }
    }, [schema, reactFlowInstance]);


    const onNodesChange: OnNodesChange = useCallback((changes) => setNodes((nds) => applyNodeChanges(changes, nds)), []);
    const onEdgesChange: OnEdgesChange = useCallback((changes) => setEdges((eds) => applyEdgeChanges(changes, eds)), []);
    const onConnect: OnConnect = useCallback((params: Edge | Connection) => setEdges((eds) => addEdge(params, eds)), []);

    const handleSave = async () => {
        if (!schemaName.trim()) {
            alert('Название схемы не может быть пустым.');
            return;
        }
        setIsLoading(true);
        const viewport = reactFlowInstance?.getViewport() || { x: 0, y: 0, zoom: 1 };
        const schemaToSave: Partial<Schema> = {
            ...schema,
            enterprise_id: enterpriseId,
            schema_name: schemaName,
            schema_data: { nodes, edges, viewport }
        };

        try {
            const savedSchema = await onSave(schemaToSave);
            
            if (savedSchema && savedSchema.schema_id) {
                const res = await fetch(`/dial/api/enterprises/${enterpriseId}/schemas/${savedSchema.schema_id}/assign_lines`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(Array.from(selectedLines)),
                });

                if (!res.ok) {
                   throw new Error('Ошибка привязки линий');
                }
            }
            onCancel();
        } catch (error) {
            console.error("Failed to save schema or assign lines:", error);
            alert("Не удалось сохранить схему или привязать линии.");
        } finally {
            setIsLoading(false);
        }
    };

    const handleDelete = () => {
        if (schema.schema_id) {
            onDelete(schema.schema_id);
        } else {
            onCancel();
        }
    };
    
    const handleNodeClick = (event: React.MouseEvent, node: Node) => {
        event.stopPropagation();
        if (node.type === 'custom' && node.id === '1') {
             setIsLinesModalOpen(true);
        }
    };

    const handleAddNode = () => {
        setIsNodeActionModalOpen(true);
    };

    const handleOpenDialModal = () => {
        setIsNodeActionModalOpen(false);
        setIsDialModalOpen(true);
    };

    const handleOpenAddManagerModal = () => {
        setIsAddManagerModalOpen(true);
    };

    const handleAddManagers = (selected: ManagerInfo[]) => {
        setDialManagers(prev => [...prev, ...selected]);
    };

    const handleLinesUpdate = async (newSelectedLines: Set<string>) => {
        setSelectedLines(newSelectedLines);
        setIsLinesModalOpen(false);

        if (schema.schema_id) {
            setIsLoading(true);
            try {
                const res = await fetch(`/dial/api/enterprises/${enterpriseId}/schemas/${schema.schema_id}/assign_lines`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(Array.from(newSelectedLines)),
                });

                if (!res.ok) {
                    const errorData = await res.json().catch(() => ({ detail: 'Неизвестная ошибка сервера' }));
                    throw new Error(errorData.detail || 'Не удалось обновить привязки линий');
                }
            } catch (error) {
                console.error("Failed to update line assignments:", error);
                alert(error instanceof Error ? error.message : 'Произошла ошибка');
            } finally {
                setIsLoading(false);
            }
        }
    };

    const assignedLines = allLines.filter(line => selectedLines.has(line.id));

    const nodesWithCallbacks = React.useMemo(() => {
        return nodes.map(node => {
            if (node.type === 'custom' && node.id === '1') {
                return {
                    ...node,
                    data: {
                        ...node.data,
                        onAddClick: handleAddNode,
                    },
                };
            }
            return node;
        });
    }, [nodes]);

    return (
        <div className="schema-editor-container">
            <div className="schema-editor-header">
                <input
                    type="text"
                    value={schemaName}
                    onChange={(e) => setSchemaName(e.target.value)}
                    className="schema-name-input"
                    placeholder="Название схемы"
                    maxLength={35}
                />
                <div style={{ marginTop: '-40px', paddingLeft: '700px' }}>
                    <h4 style={{ margin: '0', fontSize: '1em', color: '#555' }}>Используются линии:</h4>
                    {assignedLines.map(line => (
                        <div key={line.id} style={{ fontSize: '0.9em', color: '#666' }}>{line.display_name}</div>
                    ))}
                </div>
            </div>
            <div className="react-flow-wrapper" ref={reactFlowWrapper}>
                <ReactFlow
                    nodes={nodesWithCallbacks}
                    edges={edges}
                    onNodesChange={onNodesChange}
                    onEdgesChange={onEdgesChange}
                    onConnect={onConnect}
                    onNodeClick={handleNodeClick}
                    nodeTypes={nodeTypes}
                    onInit={setReactFlowInstance}
                    fitView
                    className="react-flow-canvas"
                >
                </ReactFlow>
            </div>
            <div className="schema-editor-footer">
                <div className="footer-buttons-left">
                    <button onClick={handleDelete} className="delete-button" disabled={isLoading}>Удалить</button>
                </div>
                <div className="footer-buttons-right">
                    <button onClick={onCancel} className="cancel-button" disabled={isLoading}>Отмена</button>
                    <button onClick={handleSave} className="save-button" disabled={isLoading}>
                        {isLoading ? 'Сохранение...' : 'Сохранить'}
                    </button>
                </div>
            </div>
            {isLinesModalOpen && (
                <IncomingCallModal
                    enterpriseId={enterpriseId}
                    schemaName={schemaName}
                    initialSelectedLines={selectedLines}
                    onClose={() => setIsLinesModalOpen(false)}
                    onConfirm={handleLinesUpdate}
                />
            )}
            {isNodeActionModalOpen && (
                <NodeActionModal
                    onClose={() => setIsNodeActionModalOpen(false)}
                    onDialClick={handleOpenDialModal}
                />
            )}
            {isDialModalOpen && (
                <DialModal
                    onClose={() => setIsDialModalOpen(false)}
                    onAddManagerClick={handleOpenAddManagerModal}
                    addedManagers={dialManagers}
                />
            )}
            {isAddManagerModalOpen && (
                <AddManagerModal
                    enterpriseId={enterpriseId}
                    onClose={() => setIsAddManagerModalOpen(false)}
                    onAdd={handleAddManagers}
                />
            )}
        </div>
    );
};

// Обертка для доступа к хуку useReactFlow
const SchemaEditorWrapper: React.FC<SchemaEditorProps> = (props) => {
    return (
        <ReactFlowProvider>
            <SchemaEditor {...props} />
        </ReactFlowProvider>
    );
};

export default SchemaEditorWrapper; 