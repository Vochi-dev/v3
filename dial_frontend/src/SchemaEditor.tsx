import React, { useCallback, useEffect, useMemo, useState } from 'react';
import ReactFlow, {
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  Background,
  Node,
  Edge,
  OnNodesChange,
  OnEdgesChange,
  OnConnect,
  NodeMouseHandler,
} from 'reactflow';
import 'reactflow/dist/style.css';
import './SchemaEditor.css';
import IncomingCallModal from './IncomingCallModal';
import CustomNode from './CustomNode';

interface SchemaEditorProps {
    enterpriseId: string;
    schemaId: string;
    onClose: () => void;
}

const SchemaEditor: React.FC<SchemaEditorProps> = ({ enterpriseId, schemaId, onClose }) => {
    const [nodes, setNodes] = useState<Node[]>([]);
    const [edges, setEdges] = useState<Edge[]>([]);
    const [schemaName, setSchemaName] = useState('');
    const [isLinesModalOpen, setLinesModalOpen] = useState(false);

    const nodeTypes = useMemo(() => ({ custom: CustomNode }), []);

    useEffect(() => {
        if (schemaId && enterpriseId) {
            fetch(`/dial/api/enterprises/${enterpriseId}/schemas/${schemaId}`)
                .then((res) => res.json())
                .then((data) => {
                    setSchemaName(data.name || '');
                    setNodes(data.nodes || []);
                    setEdges(data.edges || []);
                })
                .catch(err => console.error("Failed to fetch schema", err));
        }
    }, [schemaId, enterpriseId]);

    const onNodesChange: OnNodesChange = useCallback((changes) => setNodes((nds) => applyNodeChanges(changes, nds)), []);
    const onEdgesChange: OnEdgesChange = useCallback((changes) => setEdges((eds) => applyEdgeChanges(changes, eds)), []);
    const onConnect: OnConnect = useCallback((connection) => setEdges((eds) => addEdge(connection, eds)), []);

    const onNodeClick: NodeMouseHandler = useCallback((event, node) => {
        event.stopPropagation();
        if (node.type === 'custom') {
            setLinesModalOpen(true);
        }
    }, []);

    const handleSave = () => {
        fetch(`/dial/api/enterprises/${enterpriseId}/schemas/${schemaId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: schemaName, nodes, edges }),
        })
        .then(res => {
            if (res.ok) {
                alert('Схема сохранена!');
                onClose();
            } else {
                alert('Ошибка сохранения схемы');
            }
        });
    };

    const handleDelete = () => {
        if (window.confirm(`Вы уверены, что хотите удалить схему "${schemaName}"?`)) {
            fetch(`/dial/api/enterprises/${enterpriseId}/schemas/${schemaId}`, {
                method: 'DELETE',
            })
            .then(res => {
                if (res.ok) {
                    alert('Схема удалена!');
                    onClose();
                } else {
                    alert('Ошибка удаления схемы');
                }
            });
        }
    };

    return (
        <div className="editor-container">
            <header className="editor-header">
                <h2>Редактирование схемы:</h2>
                <input
                    type="text"
                    value={schemaName}
                    onChange={(e) => setSchemaName(e.target.value)}
                    placeholder="Имя схемы"
                />
            </header>
            <main className="editor-content">
                <ReactFlow
                    nodes={nodes}
                    edges={edges}
                    onNodesChange={onNodesChange}
                    onEdgesChange={onEdgesChange}
                    onConnect={onConnect}
                    onNodeClick={onNodeClick}
                    onPaneClick={onClose}
                    nodeTypes={nodeTypes}
                    fitView
                    proOptions={{ hideAttribution: true }}
                >
                    <Background />
                </ReactFlow>
            </main>
            <footer className="editor-footer">
                <div className="footer-left">
                    <button className="delete-button" onClick={handleDelete}>Удалить</button>
                </div>
                <div className="footer-right">
                    <button className="cancel-button" onClick={onClose}>Отмена</button>
                    <button className="save-button" onClick={handleSave}>Сохранить</button>
                </div>
            </footer>

            {isLinesModalOpen && (
                <div className="internal-modal-overlay">
                    <IncomingCallModal
                        enterpriseId={enterpriseId}
                        schemaId={schemaId}
                        schemaName={schemaName}
                        onClose={() => setLinesModalOpen(false)}
                    />
                </div>
            )}
        </div>
    );
};

export default SchemaEditor; 