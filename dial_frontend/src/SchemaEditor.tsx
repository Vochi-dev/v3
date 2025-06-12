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
import { Schema } from './Modal'; // Импортируем тип Schema из Modal.tsx
import IncomingCallModal from './IncomingCallModal';


interface SchemaEditorProps {
    enterpriseId: string;
    schema: Partial<Schema>; // Схема может быть неполной (без id)
    onSave: (schema: Partial<Schema>) => void;
    onCancel: () => void;
    onDelete: (schemaId: string) => void;
}

const nodeTypes = {
    custom: IncomingCallNode,
};

const SchemaEditor: React.FC<SchemaEditorProps> = ({ enterpriseId, schema, onSave, onCancel, onDelete }) => {
    // const { setViewport } = useReactFlow(); // Переменная не используется
    const [nodes, setNodes] = useState<Node[]>(schema.schema_data?.nodes || []);
    const [edges, setEdges] = useState<Edge[]>(schema.schema_data?.edges || []);
    const [schemaName, setSchemaName] = useState(schema.schema_name || 'Новая схема');
    const [isModalOpen, setIsModalOpen] = useState(false);
    const reactFlowWrapper = useRef<HTMLDivElement>(null);
    const [reactFlowInstance, setReactFlowInstance] = useState<ReactFlowInstance | null>(null);

    useEffect(() => {
        setNodes(schema.schema_data?.nodes || []);
        setEdges(schema.schema_data?.edges || []);
        setSchemaName(schema.schema_name || 'Новая схема');
        
        if (reactFlowInstance && schema.schema_data?.viewport) {
            const { x, y, zoom } = schema.schema_data.viewport;
            reactFlowInstance.setViewport({ x, y, zoom });
        }
    }, [schema, reactFlowInstance]);


    const onNodesChange: OnNodesChange = useCallback(
        (changes) => setNodes((nds) => applyNodeChanges(changes, nds)),
        []
    );
    const onEdgesChange: OnEdgesChange = useCallback(
        (changes) => setEdges((eds) => applyEdgeChanges(changes, eds)),
        []
    );
    const onConnect: OnConnect = useCallback(
        (params: Edge | Connection) => setEdges((eds) => addEdge(params, eds)),
        []
    );

    const handleSave = () => {
        if (!schemaName.trim()) {
            alert('Название схемы не может быть пустым.');
            return;
        }
        const viewport = reactFlowInstance?.getViewport() || { x: 0, y: 0, zoom: 1 };
        const schemaToSave: Partial<Schema> = {
            ...schema,
            schema_name: schemaName,
            schema_data: { nodes, edges, viewport }
        };
        onSave(schemaToSave);
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
             if (schema.schema_id) {
                setIsModalOpen(true);
             } else {
                alert("Сначала сохраните схему, чтобы привязать к ней линию.");
             }
        }
    };

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
            </div>
             <div className="react-flow-wrapper" ref={reactFlowWrapper}>
                <ReactFlow
                    nodes={nodes}
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
                    <button onClick={handleDelete} className="delete-button">Удалить</button>
                </div>
                <div className="footer-buttons-right">
                    <button onClick={onCancel} className="cancel-button">Отмена</button>
                    <button onClick={handleSave} className="save-button">Сохранить</button>
                </div>
            </div>
            {isModalOpen && schema.schema_id && (
                <IncomingCallModal
                    enterpriseId={enterpriseId}
                    schemaId={schema.schema_id}
                    schemaName={schemaName}
                    onClose={() => setIsModalOpen(false)}
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